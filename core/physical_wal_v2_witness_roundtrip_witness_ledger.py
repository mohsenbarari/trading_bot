"""Root-owned durable Witness ledger for the portable V2 ACK roundtrip.

The V2 ACK boundary deliberately has two durable Witness stages.  First an
IR-local signed recovery export is accepted and frozen before a Witness
context certificate can be released to FI.  Only a source envelope bound to
that certificate and an IR durable assertion bound to the same envelope may
then receive a FI-facing Witness attestation.  The actual carriage of those
opaque signed byte strings is out of scope: this module has no socket, URL,
peer, Object Storage, SSH, database, subprocess, or callback surface.

All accepted material is persisted in create-only, hash-linked records below a
root-owned fd-anchored directory.  The final accepted timestamp is also the
persisted trusted-clock floor, so a restarted Witness fails closed if its wall
clock moves backwards.  The ledger never accepts a Python capability from FI
or IR and never serializes one; it accepts and returns canonical signed wire
bytes only.

The wire grammar and signatures live in
``physical_wal_v2_witness_roundtrip_contract``.  Keeping the durable state
machine here makes the contract independently testable and prevents a future
transport implementation from quietly becoming part of this authority
boundary.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core.append_only_sync_delta_batch import SHA256_RE, canonical_json_bytes
from core import physical_wal_v2_witness_roundtrip_contract as _contract


__all__ = (
    "DEFAULT_PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_MAXIMUM_RECORDS",
    "PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_DEFAULT_ENABLED",
    "PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_SCHEMA",
    "PhysicalWalV2WitnessRoundtripWitnessLedgerConfig",
    "PhysicalWalV2WitnessRoundtripWitnessLedgerError",
    "PhysicalWalV2WitnessRoundtripWitnessLedgerRuntime",
    "PhysicalWalV2WitnessRoundtripWitnessLedgerResult",
    "attest_physical_wal_v2_witness_roundtrip",
    "certify_physical_wal_v2_witness_roundtrip_context",
    "open_physical_wal_v2_witness_roundtrip_witness_ledger",
)


PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_SCHEMA = (
    "gold-trade-physical-wal-v2-witness-roundtrip-witness-ledger-v1"
)
PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_VERSION = 1
PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_DEFAULT_ENABLED = False
DEFAULT_PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_MAXIMUM_RECORDS = 256

MAX_PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_RECORDS = 4_096
MAX_PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_RECORD_BYTES = 2 * 1024 * 1024

_DIRECTORY = "physical-wal-v2-witness-roundtrip-witness-ledger-v1"
_RECORDS_DIRECTORY = "records"
_BINDING_FILENAME = "binding.json"
_LOCK_FILENAME = "ledger.lock"
_BINDING_SCHEMA = "gold-trade-physical-wal-v2-witness-roundtrip-witness-ledger-binding-v1"
_RECORD_SCHEMA = "gold-trade-physical-wal-v2-witness-roundtrip-witness-ledger-record-v1"
_CONTEXT_STAGE = "context"
_ROUNDTRIP_STAGE = "roundtrip"
_STAGES = frozenset({_CONTEXT_STAGE, _ROUNDTRIP_STAGE})
_RECORD_RE = re.compile(r"^([0-9]{20})-([0-9a-f]{64})\.json$", re.ASCII)
_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", re.ASCII)
_SAFE_COMPONENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$", re.ASCII)
_ZERO_SHA256 = "0" * 64
_CAPABILITY = object()
_UNSET = object()

_RECORD_FIELDS = frozenset(
    {
        "schema",
        "version",
        "sequence",
        "previous_head_sha256",
        "stage",
        "accepted_at",
        "clock_floor",
        "witness_ledger_binding_sha256",
        "ledger_entry_sha256",
        "context_sha256",
        "recovery_export_base64",
        "recovery_export_sha256",
        "witness_context_certificate_base64",
        "witness_context_certificate_sha256",
        "fi_envelope_base64",
        "fi_envelope_sha256",
        "ir_durable_assertion_base64",
        "ir_durable_assertion_sha256",
        "witness_roundtrip_attestation_base64",
        "witness_roundtrip_attestation_sha256",
        "record_sha256",
    }
)


class PhysicalWalV2WitnessRoundtripWitnessLedgerError(RuntimeError):
    """A durable V2 Witness roundtrip state transition is unsafe or invalid."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class PhysicalWalV2WitnessRoundtripWitnessLedgerConfig:
    """Default-off, root-owned state for one fixed V2 wire-policy binding.

    ``roundtrip_config`` is deliberately typed as ``object`` until the wire
    contract is imported below.  The public entry points require its exact
    contract type and do not accept duck-typed policy objects.
    """

    state_root: Path | None = None
    roundtrip_config: object | None = field(default=None, repr=False)
    enabled: bool = PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_DEFAULT_ENABLED
    maximum_records: int = DEFAULT_PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_MAXIMUM_RECORDS


class PhysicalWalV2WitnessRoundtripWitnessLedgerRuntime:
    """Nonserializable root-owned handle with a stale-head fence."""

    __slots__ = ("_config", "_expected_head_sha256", "_capability")

    def __init__(self, config: "_Config", head_sha256: str, capability: object) -> None:
        if capability is not _CAPABILITY:
            raise TypeError("V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_CONSTRUCTION_FORBIDDEN")
        self._config = config
        self._expected_head_sha256 = head_sha256
        self._capability = capability

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_SERIALIZATION_FORBIDDEN")


@dataclass(frozen=True)
class PhysicalWalV2WitnessRoundtripWitnessLedgerResult:
    """Redacted durable Witness output; carrier selection remains external."""

    schema: str
    status: str
    sequence: int
    ledger_head_sha256: str
    ledger_entry_sha256: str
    context_sha256: str
    witness_context_certificate: bytes | None = field(default=None, repr=False)
    witness_roundtrip_attestation: bytes | None = field(default=None, repr=False)
    idempotent: bool = False


@dataclass(frozen=True)
class _Config:
    root: Path
    roundtrip_config: object
    binding_metadata: dict[str, object]
    binding_sha256: str
    maximum_records: int


@dataclass(frozen=True)
class _Record:
    sequence: int
    previous_head_sha256: str
    stage: str
    accepted_at: datetime
    clock_floor: datetime
    witness_ledger_binding_sha256: str
    ledger_entry_sha256: str
    context_sha256: str
    recovery_export: bytes | None
    recovery_export_sha256: str | None
    witness_context_certificate: bytes | None
    witness_context_certificate_sha256: str | None
    fi_envelope: bytes | None
    fi_envelope_sha256: str | None
    ir_durable_assertion: bytes | None
    ir_durable_assertion_sha256: str | None
    witness_roundtrip_attestation: bytes | None
    witness_roundtrip_attestation_sha256: str | None
    record_sha256: str


@dataclass(frozen=True)
class _State:
    records: tuple[_Record, ...]
    head_sha256: str
    clock_floor: datetime | None


def _fail(code: str) -> None:
    raise PhysicalWalV2WitnessRoundtripWitnessLedgerError(code)


def _canonical(value: object, *, code: str) -> bytes:
    try:
        return canonical_json_bytes(value)
    except (TypeError, ValueError) as exc:
        raise PhysicalWalV2WitnessRoundtripWitnessLedgerError(code) from exc


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256(value: object, *, code: str) -> str:
    if type(value) is not str or SHA256_RE.fullmatch(value) is None or value == _ZERO_SHA256:
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
        raise PhysicalWalV2WitnessRoundtripWitnessLedgerError(code) from exc


def _host_now() -> datetime:
    try:
        return datetime.now(timezone.utc).replace(microsecond=0)
    except Exception as exc:
        raise PhysicalWalV2WitnessRoundtripWitnessLedgerError(
            "V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_CLOCK_INVALID"
        ) from exc


def _require_root() -> None:
    try:
        if os.geteuid() != 0:
            _fail("V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_ROOT_RUNTIME_REQUIRED")
    except OSError as exc:
        raise PhysicalWalV2WitnessRoundtripWitnessLedgerError(
            "V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_ROOT_RUNTIME_REQUIRED"
        ) from exc


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_JSON_INVALID")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> None:
    _fail("V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_JSON_INVALID")


def _parse_canonical(raw: object, *, code: str) -> dict[str, Any]:
    if type(raw) is not bytes or not raw or len(raw) > MAX_PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_RECORD_BYTES:
        _fail(code)
    try:
        parsed = json.loads(
            raw.decode("ascii", "strict"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
        raise PhysicalWalV2WitnessRoundtripWitnessLedgerError(code) from exc
    if type(parsed) is not dict or _canonical(parsed, code=code) != raw:
        _fail(code)
    return parsed


def _b64(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _unb64(value: object, *, permit_none: bool, code: str) -> bytes | None:
    if value is None and permit_none:
        return None
    if type(value) is not str:
        _fail(code)
    try:
        raw = base64.b64decode(value.encode("ascii"), validate=True)
    except (UnicodeEncodeError, binascii.Error) as exc:
        raise PhysicalWalV2WitnessRoundtripWitnessLedgerError(code) from exc
    if not raw or len(raw) > MAX_PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_RECORD_BYTES:
        _fail(code)
    return raw


def _safe_root_path(value: object) -> Path:
    if (
        not isinstance(value, Path)
        or not value.is_absolute()
        or len(value.parts) < 2
        or any(part in {"", ".", ".."} for part in value.parts[1:])
        or len(str(value)) > 4096
    ):
        _fail("V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_STATE_ROOT_UNSAFE")
    return value


def _require_fd_platform() -> None:
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        _fail("V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_PLATFORM_UNSAFE")


def _metadata_tuple(value: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_uid,
        value.st_nlink,
        value.st_size,
    )


def _fsync_fd(descriptor: int, *, code: str) -> None:
    try:
        os.fsync(descriptor)
    except OSError as exc:
        raise PhysicalWalV2WitnessRoundtripWitnessLedgerError(code) from exc


def _open_secure_root(value: Path) -> int:
    """Return an fd anchored at the root-owned 0700 state directory."""

    _require_fd_platform()
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    descriptor = -1
    try:
        descriptor = os.open("/", flags)
        components = value.parts[1:]
        for index, component in enumerate(components):
            next_descriptor = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
            metadata = os.fstat(descriptor)
            final = index == len(components) - 1
            if (
                not stat.S_ISDIR(metadata.st_mode)
                or metadata.st_uid != 0
                or (
                    not final
                    and (stat.S_IMODE(metadata.st_mode) & 0o022)
                    and not (metadata.st_mode & stat.S_ISVTX)
                )
                or (final and stat.S_IMODE(metadata.st_mode) != 0o700)
            ):
                _fail("V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_STATE_ROOT_UNSAFE")
        return descriptor
    except PhysicalWalV2WitnessRoundtripWitnessLedgerError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError as exc:
        if descriptor >= 0:
            os.close(descriptor)
        raise PhysicalWalV2WitnessRoundtripWitnessLedgerError(
            "V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_STATE_ROOT_UNSAFE"
        ) from exc


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
        raise PhysicalWalV2WitnessRoundtripWitnessLedgerError(code) from exc
    if (
        stat.S_ISLNK(metadata.st_mode)
        or (directory and not stat.S_ISDIR(metadata.st_mode))
        or (not directory and not stat.S_ISREG(metadata.st_mode))
        or metadata.st_uid != 0
        or (not directory and metadata.st_nlink != 1)
        or stat.S_IMODE(metadata.st_mode) != (0o700 if directory else 0o600)
    ):
        _fail(code)
    return metadata


def _ensure_child_directory(parent_fd: int, name: str) -> int:
    if _SAFE_COMPONENT_RE.fullmatch(name) is None:
        _fail("V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_PATH_COMPONENT_INVALID")
    created = False
    try:
        os.mkdir(name, 0o700, dir_fd=parent_fd)
        created = True
    except FileExistsError:
        pass
    except OSError as exc:
        raise PhysicalWalV2WitnessRoundtripWitnessLedgerError(
            "V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_DIRECTORY_CREATE_FAILED"
        ) from exc
    descriptor = -1
    try:
        if created:
            _fsync_fd(parent_fd, code="V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_DIRECTORY_FSYNC_FAILED")
        before = _safe_child_metadata(
            parent_fd,
            name,
            directory=True,
            code="V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_DIRECTORY_UNSAFE",
        )
        descriptor = os.open(
            name,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
            dir_fd=parent_fd,
        )
        opened = os.fstat(descriptor)
        after = _safe_child_metadata(
            parent_fd,
            name,
            directory=True,
            code="V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_DIRECTORY_UNSAFE",
        )
        if _metadata_tuple(before) != _metadata_tuple(opened) or _metadata_tuple(after) != _metadata_tuple(before):
            _fail("V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_DIRECTORY_UNSAFE")
        return descriptor
    except PhysicalWalV2WitnessRoundtripWitnessLedgerError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError as exc:
        if descriptor >= 0:
            os.close(descriptor)
        raise PhysicalWalV2WitnessRoundtripWitnessLedgerError(
            "V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_DIRECTORY_UNSAFE"
        ) from exc


def _open_lock(directory_fd: int) -> int:
    _require_fd_platform()
    descriptor = -1
    created = False
    try:
        try:
            descriptor = os.open(
                _LOCK_FILENAME,
                os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
                0o600,
                dir_fd=directory_fd,
            )
            created = True
        except FileExistsError:
            descriptor = os.open(
                _LOCK_FILENAME,
                os.O_RDWR | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
                dir_fd=directory_fd,
            )
        if created:
            os.fchmod(descriptor, 0o600)
            _fsync_fd(descriptor, code="V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_LOCK_FSYNC_FAILED")
            _fsync_fd(directory_fd, code="V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_DIRECTORY_FSYNC_FAILED")
        before = _safe_child_metadata(
            directory_fd,
            _LOCK_FILENAME,
            directory=False,
            code="V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_LOCK_UNSAFE",
        )
        opened = os.fstat(descriptor)
        after = _safe_child_metadata(
            directory_fd,
            _LOCK_FILENAME,
            directory=False,
            code="V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_LOCK_UNSAFE",
        )
        if _metadata_tuple(before) != _metadata_tuple(opened) or _metadata_tuple(after) != _metadata_tuple(before):
            _fail("V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_LOCK_UNSAFE")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        return descriptor
    except PhysicalWalV2WitnessRoundtripWitnessLedgerError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError as exc:
        if descriptor >= 0:
            os.close(descriptor)
        raise PhysicalWalV2WitnessRoundtripWitnessLedgerError(
            "V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_LOCK_OPEN_FAILED"
        ) from exc


@dataclass(frozen=True)
class _OpenStorage:
    directory_fd: int
    records_fd: int


@contextmanager
def _locked(config: _Config) -> Iterator[_OpenStorage]:
    root_fd = _open_secure_root(config.root)
    directory_fd = -1
    records_fd = -1
    lock_fd = -1
    try:
        directory_fd = _ensure_child_directory(root_fd, _DIRECTORY)
        records_fd = _ensure_child_directory(directory_fd, _RECORDS_DIRECTORY)
        lock_fd = _open_lock(directory_fd)
        yield _OpenStorage(directory_fd=directory_fd, records_fd=records_fd)
    finally:
        try:
            if lock_fd >= 0:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
        finally:
            for descriptor in (lock_fd, records_fd, directory_fd, root_fd):
                if descriptor >= 0:
                    try:
                        os.close(descriptor)
                    except OSError:
                        pass


def _read_file_at(parent_fd: int, name: str, *, permit_empty: bool, code: str) -> bytes:
    _require_fd_platform()
    descriptor = -1
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
            dir_fd=parent_fd,
        )
        before = _safe_child_metadata(parent_fd, name, directory=False, code=code)
        metadata = os.fstat(descriptor)
        after = _safe_child_metadata(parent_fd, name, directory=False, code=code)
        if (
            _metadata_tuple(before) != _metadata_tuple(metadata)
            or _metadata_tuple(after) != _metadata_tuple(before)
            or metadata.st_size > MAX_PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_RECORD_BYTES
            or (not permit_empty and metadata.st_size < 1)
        ):
            _fail(code)
        remaining = metadata.st_size
        result = bytearray()
        while remaining:
            chunk = os.read(descriptor, remaining)
            if not chunk:
                _fail(code)
            result.extend(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            _fail(code)
        return bytes(result)
    except PhysicalWalV2WitnessRoundtripWitnessLedgerError:
        raise
    except OSError as exc:
        raise PhysicalWalV2WitnessRoundtripWitnessLedgerError(code) from exc
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass


def _write_all(descriptor: int, payload: bytes, *, code: str) -> None:
    view = memoryview(payload)
    while view:
        try:
            written = os.write(descriptor, view)
        except OSError as exc:
            raise PhysicalWalV2WitnessRoundtripWitnessLedgerError(code) from exc
        if type(written) is not int or written <= 0:
            _fail(code)
        view = view[written:]


def _write_create_only_at(parent_fd: int, name: str, payload: bytes, *, code: str) -> None:
    _require_fd_platform()
    if (
        _SAFE_COMPONENT_RE.fullmatch(name) is None
        or type(payload) is not bytes
        or not 1 <= len(payload) <= MAX_PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_RECORD_BYTES
    ):
        _fail(code)
    descriptor = -1
    try:
        descriptor = os.open(
            name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
            0o600,
            dir_fd=parent_fd,
        )
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != 0
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_size != 0
        ):
            _fail("V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_STATE_UNSAFE")
        _write_all(descriptor, payload, code=code)
        _fsync_fd(descriptor, code="V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_RECORD_FSYNC_FAILED")
    except PhysicalWalV2WitnessRoundtripWitnessLedgerError:
        raise
    except FileExistsError:
        raise
    except OSError as exc:
        raise PhysicalWalV2WitnessRoundtripWitnessLedgerError(code) from exc
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass
    _fsync_fd(parent_fd, code="V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_DIRECTORY_FSYNC_FAILED")


def _binding_payload(config: _Config) -> bytes:
    return _canonical(config.binding_metadata, code="V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_CONFIG_INVALID")


def _init_storage(storage: _OpenStorage, *, config: _Config) -> None:
    """Create immutable binding state exactly once under the lock."""

    expected = _binding_payload(config)
    try:
        actual = _read_file_at(
            storage.directory_fd,
            _BINDING_FILENAME,
            permit_empty=False,
            code="V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_BINDING_MISSING_OR_UNSAFE",
        )
    except PhysicalWalV2WitnessRoundtripWitnessLedgerError as exc:
        if exc.code != "V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_BINDING_MISSING_OR_UNSAFE":
            raise
        try:
            _write_create_only_at(
                storage.directory_fd,
                _BINDING_FILENAME,
                expected,
                code="V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_BINDING_WRITE_FAILED",
            )
            actual = expected
        except FileExistsError:
            actual = _read_file_at(
                storage.directory_fd,
                _BINDING_FILENAME,
                permit_empty=False,
                code="V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_BINDING_MISSING_OR_UNSAFE",
            )
    if actual != expected:
        _fail("V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_FOREIGN_BINDING")


def _exact_mapping(value: object, *, fields: frozenset[str], code: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != fields:
        _fail(code)
    return dict(value)


def _head_sha256(value: object, *, permit_zero: bool, code: str) -> str:
    if permit_zero and value == _ZERO_SHA256:
        return _ZERO_SHA256
    return _sha256(value, code=code)


def _optional_wire(
    raw_value: object,
    sha_value: object,
    *,
    code: str,
) -> tuple[bytes | None, str | None]:
    raw = _unb64(raw_value, permit_none=True, code=code)
    if raw is None:
        if sha_value is not None:
            _fail(code)
        return None, None
    digest = _sha256(sha_value, code=code)
    if _sha256_bytes(raw) != digest:
        _fail(code)
    return raw, digest


def _record_without_digest(value: _Record) -> dict[str, object]:
    return {
        "schema": _RECORD_SCHEMA,
        "version": PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_VERSION,
        "sequence": value.sequence,
        "previous_head_sha256": value.previous_head_sha256,
        "stage": value.stage,
        "accepted_at": _timestamp(value.accepted_at, code="V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_RECORD_TIME_INVALID"),
        "clock_floor": _timestamp(value.clock_floor, code="V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_RECORD_TIME_INVALID"),
        "witness_ledger_binding_sha256": value.witness_ledger_binding_sha256,
        "ledger_entry_sha256": value.ledger_entry_sha256,
        "context_sha256": value.context_sha256,
        "recovery_export_base64": None if value.recovery_export is None else _b64(value.recovery_export),
        "recovery_export_sha256": value.recovery_export_sha256,
        "witness_context_certificate_base64": (
            None
            if value.witness_context_certificate is None
            else _b64(value.witness_context_certificate)
        ),
        "witness_context_certificate_sha256": value.witness_context_certificate_sha256,
        "fi_envelope_base64": None if value.fi_envelope is None else _b64(value.fi_envelope),
        "fi_envelope_sha256": value.fi_envelope_sha256,
        "ir_durable_assertion_base64": (
            None if value.ir_durable_assertion is None else _b64(value.ir_durable_assertion)
        ),
        "ir_durable_assertion_sha256": value.ir_durable_assertion_sha256,
        "witness_roundtrip_attestation_base64": (
            None
            if value.witness_roundtrip_attestation is None
            else _b64(value.witness_roundtrip_attestation)
        ),
        "witness_roundtrip_attestation_sha256": value.witness_roundtrip_attestation_sha256,
    }


def _record_digest(value: _Record) -> str:
    return _sha256_bytes(
        _canonical(
            _record_without_digest(value),
            code="V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_RECORD_INVALID",
        )
    )


def _ledger_entry_digest(value: _Record) -> str:
    """Stable pre-signing entry hash with no certificate/attestation cycle.

    The Witness must provide this value to the signed FI-facing attestation
    before the enclosing record can be created.  Therefore it commits only to
    the sequence, prior durable head, trusted time and the hashes of the
    accepted signed inputs; ``record_sha256`` subsequently commits to this
    digest *and* the immutable signed output bytes.
    """

    return _sha256_bytes(
        _canonical(
            {
                "schema": _RECORD_SCHEMA,
                "version": PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_VERSION,
                "sequence": value.sequence,
                "previous_head_sha256": value.previous_head_sha256,
                "stage": value.stage,
                "accepted_at": _timestamp(
                    value.accepted_at,
                    code="V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_RECORD_TIME_INVALID",
                ),
                "clock_floor": _timestamp(
                    value.clock_floor,
                    code="V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_RECORD_TIME_INVALID",
                ),
                "witness_ledger_binding_sha256": value.witness_ledger_binding_sha256,
                "context_sha256": value.context_sha256,
                "recovery_export_sha256": value.recovery_export_sha256,
                # Stage 2 accepts the prior Witness certificate as an
                # independently supplied wire input. It must be committed
                # directly rather than trusted only through a nested FI
                # envelope. Stage 1 deliberately omits its *output* from
                # this pre-signing digest to avoid a signature cycle.
                "witness_context_certificate_sha256": (
                    value.witness_context_certificate_sha256
                    if value.stage == _ROUNDTRIP_STAGE
                    else None
                ),
                "fi_envelope_sha256": value.fi_envelope_sha256,
                "ir_durable_assertion_sha256": value.ir_durable_assertion_sha256,
            },
            code="V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_RECORD_INVALID",
        )
    )


def _record_mapping(value: _Record) -> dict[str, object]:
    result = _record_without_digest(value)
    result["record_sha256"] = value.record_sha256
    return result


def _record_from_mapping(value: object, *, config: _Config) -> _Record:
    item = _exact_mapping(
        value,
        fields=_RECORD_FIELDS,
        code="V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_RECORD_FIELDS_INVALID",
    )
    if (
        item["schema"] != _RECORD_SCHEMA
        or item["version"] != PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_VERSION
        or type(item["sequence"]) is not int
        or item["sequence"] < 1
        or type(item["stage"]) is not str
        or item["stage"] not in _STAGES
    ):
        _fail("V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_RECORD_INVALID")
    stage = item["stage"]
    recovery_export, recovery_export_sha = _optional_wire(
        item["recovery_export_base64"],
        item["recovery_export_sha256"],
        code="V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_RECORD_WIRE_INVALID",
    )
    certificate, certificate_sha = _optional_wire(
        item["witness_context_certificate_base64"],
        item["witness_context_certificate_sha256"],
        code="V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_RECORD_WIRE_INVALID",
    )
    fi_envelope, fi_envelope_sha = _optional_wire(
        item["fi_envelope_base64"],
        item["fi_envelope_sha256"],
        code="V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_RECORD_WIRE_INVALID",
    )
    assertion, assertion_sha = _optional_wire(
        item["ir_durable_assertion_base64"],
        item["ir_durable_assertion_sha256"],
        code="V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_RECORD_WIRE_INVALID",
    )
    attestation, attestation_sha = _optional_wire(
        item["witness_roundtrip_attestation_base64"],
        item["witness_roundtrip_attestation_sha256"],
        code="V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_RECORD_WIRE_INVALID",
    )
    if stage == _CONTEXT_STAGE:
        if (
            recovery_export is None
            or certificate is None
            or fi_envelope is not None
            or assertion is not None
            or attestation is not None
        ):
            _fail("V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_RECORD_STAGE_INVALID")
    else:
        if (
            recovery_export is not None
            or certificate is None
            or fi_envelope is None
            or assertion is None
            or attestation is None
        ):
            _fail("V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_RECORD_STAGE_INVALID")
    result = _Record(
        sequence=item["sequence"],
        previous_head_sha256=_head_sha256(
            item["previous_head_sha256"],
            permit_zero=True,
            code="V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_RECORD_CHAIN_INVALID",
        ),
        stage=stage,
        accepted_at=_parse_timestamp(
            item["accepted_at"],
            code="V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_RECORD_TIME_INVALID",
        ),
        clock_floor=_parse_timestamp(
            item["clock_floor"],
            code="V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_RECORD_TIME_INVALID",
        ),
        witness_ledger_binding_sha256=_sha256(
            item["witness_ledger_binding_sha256"],
            code="V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_RECORD_BINDING_INVALID",
        ),
        ledger_entry_sha256=_sha256(
            item["ledger_entry_sha256"],
            code="V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_RECORD_DIGEST_INVALID",
        ),
        context_sha256=_sha256(
            item["context_sha256"],
            code="V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_RECORD_CONTEXT_INVALID",
        ),
        recovery_export=recovery_export,
        recovery_export_sha256=recovery_export_sha,
        witness_context_certificate=certificate,
        witness_context_certificate_sha256=certificate_sha,
        fi_envelope=fi_envelope,
        fi_envelope_sha256=fi_envelope_sha,
        ir_durable_assertion=assertion,
        ir_durable_assertion_sha256=assertion_sha,
        witness_roundtrip_attestation=attestation,
        witness_roundtrip_attestation_sha256=attestation_sha,
        record_sha256=_sha256(
            item["record_sha256"],
            code="V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_RECORD_DIGEST_INVALID",
        ),
    )
    if (
        result.accepted_at != result.clock_floor
        or result.witness_ledger_binding_sha256 != config.binding_sha256
        or _ledger_entry_digest(result) != result.ledger_entry_sha256
        or _record_digest(result) != result.record_sha256
    ):
        _fail("V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_RECORD_DIGEST_INVALID")
    return result


def _record_filename(value: _Record) -> str:
    return f"{value.sequence:020d}-{value.record_sha256}.json"


def _read_records(storage: _OpenStorage, *, config: _Config, trusted_now: datetime) -> _State:
    try:
        names = os.listdir(storage.records_fd)
    except OSError as exc:
        raise PhysicalWalV2WitnessRoundtripWitnessLedgerError(
            "V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_RECORD_DIRECTORY_READ_FAILED"
        ) from exc
    parsed_names: list[tuple[int, str, str]] = []
    for name in names:
        if type(name) is not str:
            _fail("V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_RECORD_NAME_INVALID")
        match = _RECORD_RE.fullmatch(name)
        if match is None:
            _fail("V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_RECORD_NAME_INVALID")
        parsed_names.append((int(match.group(1)), match.group(2), name))
    parsed_names.sort()
    if len(parsed_names) > config.maximum_records:
        _fail("V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_RECORD_LIMIT_EXCEEDED")
    records: list[_Record] = []
    # The fixed binding digest is the nonzero genesis head.  It prevents a
    # first record from being replayed into another Witness policy namespace,
    # and is safe to place in signed portable artifacts (it contains no key).
    expected_head = config.binding_sha256
    expected_sequence = 1
    previous_floor: datetime | None = None
    for sequence, digest, name in parsed_names:
        raw = _read_file_at(
            storage.records_fd,
            name,
            permit_empty=False,
            code="V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_RECORD_UNSAFE",
        )
        record = _record_from_mapping(
            _parse_canonical(raw, code="V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_RECORD_INVALID"),
            config=config,
        )
        if (
            sequence != expected_sequence
            or record.sequence != sequence
            or record.record_sha256 != digest
            or record.previous_head_sha256 != expected_head
            or (previous_floor is not None and record.clock_floor < previous_floor)
        ):
            _fail("V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_RECORD_CHAIN_INVALID")
        records.append(record)
        expected_head = record.record_sha256
        expected_sequence += 1
        previous_floor = record.clock_floor
    if previous_floor is not None and trusted_now < previous_floor:
        _fail("V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_CLOCK_ROLLBACK_DETECTED")
    # Confirm the visibility of a freshly fsync'd create-only record before
    # returning an idempotent durable answer after a process restart.
    _fsync_fd(
        storage.records_fd,
        code="V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_DIRECTORY_FSYNC_FAILED",
    )
    return _State(records=tuple(records), head_sha256=expected_head, clock_floor=previous_floor)


def _append_record(storage: _OpenStorage, *, record: _Record) -> None:
    if _record_digest(record) != record.record_sha256:
        _fail("V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_RECORD_DIGEST_INVALID")
    payload = _canonical(
        _record_mapping(record),
        code="V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_RECORD_INVALID",
    )
    try:
        _write_create_only_at(
            storage.records_fd,
            _record_filename(record),
            payload,
            code="V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_RECORD_WRITE_FAILED",
        )
    except FileExistsError:
        _fail("V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_RECORD_COLLISION")


def _config(value: object) -> _Config:
    if type(value) is not PhysicalWalV2WitnessRoundtripWitnessLedgerConfig:
        _fail("V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_CONFIG_INVALID")
    if (
        value.enabled is not True
        or type(value.roundtrip_config) is not _contract.PhysicalWalV2WitnessRoundtripConfig
        or type(value.maximum_records) is not int
        or not 1 <= value.maximum_records <= MAX_PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_RECORDS
    ):
        _fail("V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_CONFIG_INVALID")
    _require_root()
    try:
        contract_facts = _contract._config(value.roundtrip_config)
    except (AttributeError, TypeError, ValueError, _contract.PhysicalWalV2WitnessRoundtripError) as exc:
        raise PhysicalWalV2WitnessRoundtripWitnessLedgerError(
            "V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_CONFIG_INVALID"
        ) from exc
    root = _safe_root_path(value.state_root)
    metadata: dict[str, object] = {
        "schema": _BINDING_SCHEMA,
        "ledger_schema": PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_SCHEMA,
        "ledger_version": PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_VERSION,
        "roundtrip_configuration_sha256": contract_facts.configuration_sha256,
        "maximum_records": value.maximum_records,
        "clock_policy": "root-owned-wall-clock-with-persisted-anti-rollback-floor-v1",
        "record_policy": "create-only-hash-linked-witness-sequence-v1",
    }
    binding_sha256 = _sha256_bytes(
        _canonical(metadata, code="V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_CONFIG_INVALID")
    )
    return _Config(
        root=root,
        roundtrip_config=value.roundtrip_config,
        binding_metadata=metadata,
        binding_sha256=binding_sha256,
        maximum_records=value.maximum_records,
    )


def _runtime(value: object) -> PhysicalWalV2WitnessRoundtripWitnessLedgerRuntime:
    if (
        type(value) is not PhysicalWalV2WitnessRoundtripWitnessLedgerRuntime
        or value._capability is not _CAPABILITY
        or type(value._config) is not _Config
        or _head_sha256(
            value._expected_head_sha256,
            permit_zero=False,
            code="V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_RUNTIME_INVALID",
        )
        != value._expected_head_sha256
    ):
        _fail("V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_RUNTIME_INVALID")
    return value


def _require_runtime_head(
    runtime: PhysicalWalV2WitnessRoundtripWitnessLedgerRuntime,
    state: _State,
) -> None:
    if state.head_sha256 != runtime._expected_head_sha256:
        _fail("V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_STALE_RUNTIME")


def _new_record(
    *,
    config: _Config,
    state: _State,
    stage: str,
    now: datetime,
    context_sha256: str,
    recovery_export: bytes | None = None,
    witness_context_certificate: bytes | None = None,
    fi_envelope: bytes | None = None,
    ir_durable_assertion: bytes | None = None,
    witness_roundtrip_attestation: bytes | None = None,
) -> _Record:
    """Construct a record whose entry digest exists before Witness signing."""

    if stage not in _STAGES:
        _fail("V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_RECORD_STAGE_INVALID")
    current = _utc(now, code="V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_CLOCK_INVALID")
    if state.clock_floor is not None and current < state.clock_floor:
        _fail("V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_CLOCK_ROLLBACK_DETECTED")
    draft = _Record(
        sequence=len(state.records) + 1,
        previous_head_sha256=state.head_sha256,
        stage=stage,
        accepted_at=current,
        clock_floor=current,
        witness_ledger_binding_sha256=config.binding_sha256,
        ledger_entry_sha256="a" * 64,
        context_sha256=_sha256(
            context_sha256,
            code="V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_RECORD_CONTEXT_INVALID",
        ),
        recovery_export=recovery_export,
        recovery_export_sha256=(
            None if recovery_export is None else _sha256_bytes(recovery_export)
        ),
        witness_context_certificate=witness_context_certificate,
        witness_context_certificate_sha256=(
            None
            if witness_context_certificate is None
            else _sha256_bytes(witness_context_certificate)
        ),
        fi_envelope=fi_envelope,
        fi_envelope_sha256=None if fi_envelope is None else _sha256_bytes(fi_envelope),
        ir_durable_assertion=ir_durable_assertion,
        ir_durable_assertion_sha256=(
            None if ir_durable_assertion is None else _sha256_bytes(ir_durable_assertion)
        ),
        witness_roundtrip_attestation=witness_roundtrip_attestation,
        witness_roundtrip_attestation_sha256=(
            None
            if witness_roundtrip_attestation is None
            else _sha256_bytes(witness_roundtrip_attestation)
        ),
        record_sha256="b" * 64,
    )
    with_entry = replace(draft, ledger_entry_sha256=_ledger_entry_digest(draft))
    return replace(with_entry, record_sha256=_record_digest(with_entry))


def _finalize_record(
    record: _Record,
    *,
    witness_context_certificate: bytes | None | object = _UNSET,
    witness_roundtrip_attestation: bytes | None | object = _UNSET,
) -> _Record:
    """Attach signed output bytes without changing the pre-signing digest."""

    certificate = (
        record.witness_context_certificate
        if witness_context_certificate is _UNSET
        else witness_context_certificate
    )
    attestation = (
        record.witness_roundtrip_attestation
        if witness_roundtrip_attestation is _UNSET
        else witness_roundtrip_attestation
    )
    if (certificate is not None and type(certificate) is not bytes) or (
        attestation is not None and type(attestation) is not bytes
    ):
        _fail("V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_RECORD_WIRE_INVALID")
    candidate = replace(
        record,
        witness_context_certificate=certificate,
        witness_context_certificate_sha256=(
            None
            if certificate is None
            else _sha256_bytes(certificate)
        ),
        witness_roundtrip_attestation=attestation,
        witness_roundtrip_attestation_sha256=(
            None
            if attestation is None
            else _sha256_bytes(attestation)
        ),
    )
    if _ledger_entry_digest(candidate) != record.ledger_entry_sha256:
        _fail("V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_ENTRY_DIGEST_CHANGED")
    return replace(candidate, record_sha256=_record_digest(candidate))


def _fresh_contract_now() -> datetime:
    return _host_now()


def _wire_bytes(value: object, *, code: str) -> bytes:
    if type(value) is not bytes or not 1 <= len(value) <= _contract.MAX_PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_WIRE_BYTES:
        _fail(code)
    return value


def _random_identifier(prefix: str) -> str:
    value = prefix + secrets.token_hex(24)
    if len(value) > 127:
        _fail("V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_RANDOM_INVALID")
    return value


def _random_nonce() -> str:
    value = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii").rstrip("=")
    if not re.fullmatch(r"[A-Za-z0-9_-]{22,128}", value):
        _fail("V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_RANDOM_INVALID")
    return value


def _post_commit_live_revalidation(
    *,
    config: _Config,
    canonical_context: bytes,
    witnessed_term: object,
    activation: object,
    now: datetime,
) -> object:
    """Recheck the live term after fsync and before exposing a signed output."""

    try:
        facts = _contract._config(config.roundtrip_config)
        mapping, _raw, _context_facts = _contract._context(
            canonical_context,
            config=facts,
            code="V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_CONTEXT_INVALID",
        )
        return _contract._check_live_activation(
            context_mapping=mapping,
            witnessed_term=witnessed_term,
            activation=activation,
            now=now,
        )
    except (AttributeError, TypeError, ValueError, _contract.PhysicalWalV2WitnessRoundtripError) as exc:
        raise PhysicalWalV2WitnessRoundtripWitnessLedgerError(
            "V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_POST_COMMIT_LIVE_REVALIDATION_FAILED"
        ) from exc


def _require_post_commit_live_match(
    *,
    config: _Config,
    record: _Record,
    canonical_context: bytes,
    witnessed_term: object,
    activation: object,
    expected_witness_transition_id: str,
    expected_activation_mode: str,
    expected_activation_stream_generation_id: str,
    expected_activation_route_artifact_sha256: str,
    expected_activation_source_cutover_attestation_sha256: str,
    expected_activation_receiver_permit_sha256: str,
) -> None:
    """Fail closed unless a fresh local term check equals signed output pins."""

    observed = _fresh_contract_now()
    if observed < record.clock_floor:
        _fail("V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_CLOCK_ROLLBACK_DETECTED")
    live = _post_commit_live_revalidation(
        config=config,
        canonical_context=canonical_context,
        witnessed_term=witnessed_term,
        activation=activation,
        now=observed,
    )
    try:
        actual = (
            live.witness_transition_id,
            live.activation_mode,
            live.activation_stream_generation_id,
            live.activation_route_artifact_sha256,
            live.activation_source_cutover_attestation_sha256,
            live.activation_receiver_permit_sha256,
        )
    except AttributeError as exc:
        raise PhysicalWalV2WitnessRoundtripWitnessLedgerError(
            "V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_POST_COMMIT_LIVE_REVALIDATION_FAILED"
        ) from exc
    expected = (
        expected_witness_transition_id,
        expected_activation_mode,
        expected_activation_stream_generation_id,
        expected_activation_route_artifact_sha256,
        expected_activation_source_cutover_attestation_sha256,
        expected_activation_receiver_permit_sha256,
    )
    if actual != expected:
        _fail("V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_POST_COMMIT_LIVE_CHANGED")


def open_physical_wal_v2_witness_roundtrip_witness_ledger(
    config: PhysicalWalV2WitnessRoundtripWitnessLedgerConfig,
) -> PhysicalWalV2WitnessRoundtripWitnessLedgerRuntime:
    """Open one root-owned Witness ledger without accepting any peer bytes."""

    normalized = _config(config)
    now = _fresh_contract_now()
    with _locked(normalized) as storage:
        _init_storage(storage, config=normalized)
        state = _read_records(storage, config=normalized, trusted_now=now)
    return PhysicalWalV2WitnessRoundtripWitnessLedgerRuntime(
        normalized,
        state.head_sha256,
        _CAPABILITY,
    )


def _verified_context_record(
    record: _Record,
    *,
    config: _Config,
    now: datetime,
) -> _contract.VerifiedPhysicalWalV2WitnessContextCertificate:
    if (
        record.stage != _CONTEXT_STAGE
        or record.recovery_export is None
        or record.witness_context_certificate is None
    ):
        _fail("V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_CONTEXT_RECORD_INVALID")
    try:
        export = _contract.verify_physical_wal_v2_witness_recovery_export(
            record.recovery_export,
            config=config.roundtrip_config,
            now=now,
        )
        certificate = _contract.verify_physical_wal_v2_witness_context_certificate(
            record.witness_context_certificate,
            config=config.roundtrip_config,
            now=now,
        )
    except _contract.PhysicalWalV2WitnessRoundtripError as exc:
        raise PhysicalWalV2WitnessRoundtripWitnessLedgerError(
            "V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_CONTEXT_RECORD_INVALID"
        ) from exc
    if (
        export.context_sha256 != record.context_sha256
        or certificate.context_sha256 != record.context_sha256
        or certificate.canonical_recovery_export != record.recovery_export
        or certificate.certificate_sha256 != record.witness_context_certificate_sha256
        or certificate.witness_sequence != record.sequence
        or certificate.witness_ledger_entry_sha256 != record.ledger_entry_sha256
        or certificate.witness_ledger_previous_head_sha256 != record.previous_head_sha256
        or certificate.witness_ledger_binding_sha256 != record.witness_ledger_binding_sha256
    ):
        _fail("V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_CONTEXT_RECORD_CROSS_PIN_MISMATCH")
    return certificate


def _verified_roundtrip_record(
    record: _Record,
    *,
    config: _Config,
    now: datetime,
) -> _contract.VerifiedPhysicalWalV2WitnessRoundtripAttestation:
    if (
        record.stage != _ROUNDTRIP_STAGE
        or record.witness_context_certificate is None
        or record.fi_envelope is None
        or record.ir_durable_assertion is None
        or record.witness_roundtrip_attestation is None
    ):
        _fail("V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_ROUNDTRIP_RECORD_INVALID")
    try:
        certificate = _contract.verify_physical_wal_v2_witness_context_certificate(
            record.witness_context_certificate,
            config=config.roundtrip_config,
            now=now,
        )
        envelope = _contract.verify_physical_wal_v2_witness_source_envelope(
            record.fi_envelope,
            config=config.roundtrip_config,
            now=now,
        )
        assertion = _contract.verify_physical_wal_v2_witness_ir_durable_assertion(
            record.ir_durable_assertion,
            config=config.roundtrip_config,
            now=now,
        )
        attestation = _contract.verify_physical_wal_v2_witness_roundtrip_attestation(
            record.witness_roundtrip_attestation,
            config=config.roundtrip_config,
            now=now,
        )
    except _contract.PhysicalWalV2WitnessRoundtripError as exc:
        raise PhysicalWalV2WitnessRoundtripWitnessLedgerError(
            "V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_ROUNDTRIP_RECORD_INVALID"
        ) from exc
    if (
        certificate.context_sha256 != record.context_sha256
        or envelope.canonical_context_certificate != record.witness_context_certificate
        or assertion.canonical_source_envelope != record.fi_envelope
        or attestation.canonical_ir_durable_assertion != record.ir_durable_assertion
        or attestation.context_certificate_sha256 != certificate.certificate_sha256
        or attestation.context_sha256 != record.context_sha256
        or attestation.source_envelope_sha256 != record.fi_envelope_sha256
        or attestation.ir_durable_assertion_sha256 != record.ir_durable_assertion_sha256
        or attestation.witness_sequence != record.sequence
        or attestation.witness_ledger_entry_sha256 != record.ledger_entry_sha256
        or attestation.witness_ledger_previous_head_sha256 != record.previous_head_sha256
        or attestation.witness_ledger_binding_sha256 != record.witness_ledger_binding_sha256
        or attestation.attestation_sha256 != record.witness_roundtrip_attestation_sha256
    ):
        _fail("V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_ROUNDTRIP_RECORD_CROSS_PIN_MISMATCH")
    return attestation


def _result_for_context_record(
    record: _Record,
    *,
    head_sha256: str,
    idempotent: bool,
) -> PhysicalWalV2WitnessRoundtripWitnessLedgerResult:
    if record.witness_context_certificate is None:
        _fail("V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_CONTEXT_RECORD_INVALID")
    return PhysicalWalV2WitnessRoundtripWitnessLedgerResult(
        schema=PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_SCHEMA,
        status="witness-context-certified",
        sequence=record.sequence,
        ledger_head_sha256=head_sha256,
        ledger_entry_sha256=record.ledger_entry_sha256,
        context_sha256=record.context_sha256,
        witness_context_certificate=record.witness_context_certificate,
        idempotent=idempotent,
    )


def _result_for_roundtrip_record(
    record: _Record,
    *,
    head_sha256: str,
    idempotent: bool,
) -> PhysicalWalV2WitnessRoundtripWitnessLedgerResult:
    if record.witness_roundtrip_attestation is None:
        _fail("V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_ROUNDTRIP_RECORD_INVALID")
    return PhysicalWalV2WitnessRoundtripWitnessLedgerResult(
        schema=PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_SCHEMA,
        status="witness-roundtrip-attested",
        sequence=record.sequence,
        ledger_head_sha256=head_sha256,
        ledger_entry_sha256=record.ledger_entry_sha256,
        context_sha256=record.context_sha256,
        witness_roundtrip_attestation=record.witness_roundtrip_attestation,
        idempotent=idempotent,
    )


def certify_physical_wal_v2_witness_roundtrip_context(
    *,
    runtime: PhysicalWalV2WitnessRoundtripWitnessLedgerRuntime,
    recovery_export: bytes,
    witnessed_term: object,
    activation: object,
    witness_signer: object,
) -> PhysicalWalV2WitnessRoundtripWitnessLedgerResult:
    """Durably certify one IR recovery export before FI may build a request.

    The recovery export is raw canonical signed bytes.  No process-local IR
    recovery capability, FI endpoint, or transport callback is accepted here.
    Certificate identifiers, nonces and expiry are owned by the Witness; they
    are never caller-selected replay controls.
    """

    handle = _runtime(runtime)
    raw_export = _wire_bytes(
        recovery_export,
        code="V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_RECOVERY_EXPORT_INVALID",
    )
    now = _fresh_contract_now()
    try:
        export = _contract.verify_physical_wal_v2_witness_recovery_export(
            raw_export,
            config=handle._config.roundtrip_config,
            now=now,
        )
    except _contract.PhysicalWalV2WitnessRoundtripError as exc:
        raise PhysicalWalV2WitnessRoundtripWitnessLedgerError(
            "V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_RECOVERY_EXPORT_INVALID"
        ) from exc
    if export.canonical_export != raw_export:
        _fail("V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_RECOVERY_EXPORT_INVALID")
    with _locked(handle._config) as storage:
        _init_storage(storage, config=handle._config)
        state = _read_records(storage, config=handle._config, trusted_now=now)
        _require_runtime_head(handle, state)
        for record in state.records:
            if record.stage != _CONTEXT_STAGE:
                continue
            if record.recovery_export == raw_export:
                certificate = _verified_context_record(record, config=handle._config, now=now)
                if certificate.context_sha256 != export.context_sha256:
                    _fail("V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_RECOVERY_EXPORT_COLLISION")
                _require_post_commit_live_match(
                    config=handle._config,
                    record=record,
                    canonical_context=certificate.canonical_context,
                    witnessed_term=witnessed_term,
                    activation=activation,
                    expected_witness_transition_id=certificate.witness_transition_id,
                    expected_activation_mode=certificate.activation_mode,
                    expected_activation_stream_generation_id=certificate.activation_stream_generation_id,
                    expected_activation_route_artifact_sha256=(
                        certificate.activation_route_artifact_sha256
                    ),
                    expected_activation_source_cutover_attestation_sha256=(
                        certificate.activation_source_cutover_attestation_sha256
                    ),
                    expected_activation_receiver_permit_sha256=(
                        certificate.activation_receiver_permit_sha256
                    ),
                )
                return _result_for_context_record(
                    record,
                    head_sha256=state.head_sha256,
                    idempotent=True,
                )
            if record.context_sha256 == export.context_sha256:
                _fail("V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_RECOVERY_EXPORT_COLLISION")
        draft = _new_record(
            config=handle._config,
            state=state,
            stage=_CONTEXT_STAGE,
            now=now,
            context_sha256=export.context_sha256,
            recovery_export=raw_export,
        )
        try:
            raw_certificate = _contract.build_physical_wal_v2_witness_context_certificate(
                config=handle._config.roundtrip_config,
                recovery_export=export,
                witness_sequence=draft.sequence,
                witness_ledger_entry_sha256=draft.ledger_entry_sha256,
                witness_ledger_previous_head_sha256=draft.previous_head_sha256,
                witness_ledger_binding_sha256=draft.witness_ledger_binding_sha256,
                certificate_id=_random_identifier("v2-witness-context-"),
                certificate_nonce=_random_nonce(),
                expires_at=export.expires_at,
                witnessed_term=witnessed_term,
                activation=activation,
                witness_signer=witness_signer,
                now=now,
            )
            certificate = _contract.verify_physical_wal_v2_witness_context_certificate(
                raw_certificate,
                config=handle._config.roundtrip_config,
                now=now,
            )
        except _contract.PhysicalWalV2WitnessRoundtripError as exc:
            raise PhysicalWalV2WitnessRoundtripWitnessLedgerError(
                "V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_CONTEXT_CERTIFICATE_INVALID"
            ) from exc
        record = _finalize_record(
            draft,
            witness_context_certificate=certificate.canonical_certificate,
        )
        if (
            certificate.context_sha256 != record.context_sha256
            or certificate.canonical_recovery_export != record.recovery_export
            or certificate.witness_sequence != record.sequence
            or certificate.witness_ledger_entry_sha256 != record.ledger_entry_sha256
            or certificate.witness_ledger_previous_head_sha256 != record.previous_head_sha256
            or certificate.witness_ledger_binding_sha256 != record.witness_ledger_binding_sha256
        ):
            _fail("V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_CONTEXT_CERTIFICATE_CROSS_PIN_MISMATCH")
        _append_record(storage, record=record)
        handle._expected_head_sha256 = record.record_sha256
        _require_post_commit_live_match(
            config=handle._config,
            record=record,
            canonical_context=certificate.canonical_context,
            witnessed_term=witnessed_term,
            activation=activation,
            expected_witness_transition_id=certificate.witness_transition_id,
            expected_activation_mode=certificate.activation_mode,
            expected_activation_stream_generation_id=certificate.activation_stream_generation_id,
            expected_activation_route_artifact_sha256=(
                certificate.activation_route_artifact_sha256
            ),
            expected_activation_source_cutover_attestation_sha256=(
                certificate.activation_source_cutover_attestation_sha256
            ),
            expected_activation_receiver_permit_sha256=(
                certificate.activation_receiver_permit_sha256
            ),
        )
        return _result_for_context_record(
            record,
            head_sha256=record.record_sha256,
            idempotent=False,
        )


def attest_physical_wal_v2_witness_roundtrip(
    *,
    runtime: PhysicalWalV2WitnessRoundtripWitnessLedgerRuntime,
    context_certificate: bytes,
    fi_source_envelope: bytes,
    ir_durable_assertion: bytes,
    witnessed_term: object,
    activation: object,
    witness_signer: object,
) -> PhysicalWalV2WitnessRoundtripWitnessLedgerResult:
    """Durably mediate the exact certificate/envelope/assertion byte triple.

    The three signed wire artifacts are all explicit inputs.  Their nested
    copies must match byte-for-byte, and a one-time durable result cannot be
    repurposed for a different member of the triple.
    """

    handle = _runtime(runtime)
    raw_certificate = _wire_bytes(
        context_certificate,
        code="V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_CONTEXT_CERTIFICATE_INVALID",
    )
    raw_envelope = _wire_bytes(
        fi_source_envelope,
        code="V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_SOURCE_ENVELOPE_INVALID",
    )
    raw_assertion = _wire_bytes(
        ir_durable_assertion,
        code="V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_IR_ASSERTION_INVALID",
    )
    now = _fresh_contract_now()
    try:
        certificate = _contract.verify_physical_wal_v2_witness_context_certificate(
            raw_certificate,
            config=handle._config.roundtrip_config,
            now=now,
        )
        envelope = _contract.verify_physical_wal_v2_witness_source_envelope(
            raw_envelope,
            config=handle._config.roundtrip_config,
            now=now,
        )
        assertion = _contract.verify_physical_wal_v2_witness_ir_durable_assertion(
            raw_assertion,
            config=handle._config.roundtrip_config,
            now=now,
        )
    except _contract.PhysicalWalV2WitnessRoundtripError as exc:
        raise PhysicalWalV2WitnessRoundtripWitnessLedgerError(
            "V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_ROUNDTRIP_INPUT_INVALID"
        ) from exc
    if (
        certificate.canonical_certificate != raw_certificate
        or envelope.canonical_envelope != raw_envelope
        or assertion.canonical_assertion != raw_assertion
        or envelope.canonical_context_certificate != raw_certificate
        or assertion.canonical_source_envelope != raw_envelope
        or certificate.context_sha256 != envelope.context_sha256
        or certificate.context_sha256 != assertion.context_sha256
    ):
        _fail("V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_ROUNDTRIP_INPUT_CROSS_PIN_MISMATCH")
    with _locked(handle._config) as storage:
        _init_storage(storage, config=handle._config)
        state = _read_records(storage, config=handle._config, trusted_now=now)
        _require_runtime_head(handle, state)
        matching_context = next(
            (
                record
                for record in state.records
                if record.stage == _CONTEXT_STAGE
                and record.context_sha256 == certificate.context_sha256
                and record.witness_context_certificate == raw_certificate
            ),
            None,
        )
        if matching_context is None:
            _fail("V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_CONTEXT_NOT_DURABLE")
        verified_context = _verified_context_record(
            matching_context,
            config=handle._config,
            now=now,
        )
        if verified_context.canonical_certificate != raw_certificate:
            _fail("V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_CONTEXT_NOT_DURABLE")
        for record in state.records:
            if record.stage != _ROUNDTRIP_STAGE:
                continue
            exact = (
                record.witness_context_certificate == raw_certificate
                and record.fi_envelope == raw_envelope
                and record.ir_durable_assertion == raw_assertion
            )
            if exact:
                attestation = _verified_roundtrip_record(record, config=handle._config, now=now)
                if attestation.context_sha256 != certificate.context_sha256:
                    _fail("V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_ROUNDTRIP_COLLISION")
                _require_post_commit_live_match(
                    config=handle._config,
                    record=record,
                    canonical_context=verified_context.canonical_context,
                    witnessed_term=witnessed_term,
                    activation=activation,
                    expected_witness_transition_id=attestation.witness_transition_id,
                    expected_activation_mode=attestation.activation_mode,
                    expected_activation_stream_generation_id=(
                        attestation.activation_stream_generation_id
                    ),
                    expected_activation_route_artifact_sha256=(
                        attestation.activation_route_artifact_sha256
                    ),
                    expected_activation_source_cutover_attestation_sha256=(
                        attestation.activation_source_cutover_attestation_sha256
                    ),
                    expected_activation_receiver_permit_sha256=(
                        attestation.activation_receiver_permit_sha256
                    ),
                )
                return _result_for_roundtrip_record(
                    record,
                    head_sha256=state.head_sha256,
                    idempotent=True,
                )
            if (
                record.witness_context_certificate == raw_certificate
                or record.fi_envelope == raw_envelope
                or record.ir_durable_assertion == raw_assertion
            ):
                _fail("V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_ROUNDTRIP_COLLISION")
        draft = _new_record(
            config=handle._config,
            state=state,
            stage=_ROUNDTRIP_STAGE,
            now=now,
            context_sha256=certificate.context_sha256,
            witness_context_certificate=raw_certificate,
            fi_envelope=raw_envelope,
            ir_durable_assertion=raw_assertion,
        )
        try:
            raw_attestation = _contract.build_physical_wal_v2_witness_roundtrip_attestation(
                config=handle._config.roundtrip_config,
                ir_durable_assertion=assertion,
                mediation_id=_random_identifier("v2-witness-mediation-"),
                witness_sequence=draft.sequence,
                witness_ledger_entry_sha256=draft.ledger_entry_sha256,
                witness_ledger_previous_head_sha256=draft.previous_head_sha256,
                witness_ledger_binding_sha256=draft.witness_ledger_binding_sha256,
                attestation_id=_random_identifier("v2-witness-attestation-"),
                attestation_nonce=_random_nonce(),
                expires_at=assertion.expires_at,
                witnessed_term=witnessed_term,
                activation=activation,
                witness_signer=witness_signer,
                now=now,
            )
            attestation = _contract.verify_physical_wal_v2_witness_roundtrip_attestation(
                raw_attestation,
                config=handle._config.roundtrip_config,
                now=now,
            )
        except _contract.PhysicalWalV2WitnessRoundtripError as exc:
            raise PhysicalWalV2WitnessRoundtripWitnessLedgerError(
                "V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_ATTESTATION_INVALID"
            ) from exc
        record = _finalize_record(
            draft,
            witness_roundtrip_attestation=attestation.canonical_attestation,
        )
        if (
            attestation.context_certificate_sha256 != certificate.certificate_sha256
            or attestation.context_sha256 != record.context_sha256
            or attestation.source_envelope_sha256 != record.fi_envelope_sha256
            or attestation.ir_durable_assertion_sha256 != record.ir_durable_assertion_sha256
            or attestation.witness_sequence != record.sequence
            or attestation.witness_ledger_entry_sha256 != record.ledger_entry_sha256
            or attestation.witness_ledger_previous_head_sha256 != record.previous_head_sha256
            or attestation.witness_ledger_binding_sha256 != record.witness_ledger_binding_sha256
        ):
            _fail("V2_WITNESS_ROUNDTRIP_WITNESS_LEDGER_ATTESTATION_CROSS_PIN_MISMATCH")
        _append_record(storage, record=record)
        handle._expected_head_sha256 = record.record_sha256
        _require_post_commit_live_match(
            config=handle._config,
            record=record,
            canonical_context=certificate.canonical_context,
            witnessed_term=witnessed_term,
            activation=activation,
            expected_witness_transition_id=attestation.witness_transition_id,
            expected_activation_mode=attestation.activation_mode,
            expected_activation_stream_generation_id=attestation.activation_stream_generation_id,
            expected_activation_route_artifact_sha256=(
                attestation.activation_route_artifact_sha256
            ),
            expected_activation_source_cutover_attestation_sha256=(
                attestation.activation_source_cutover_attestation_sha256
            ),
            expected_activation_receiver_permit_sha256=(
                attestation.activation_receiver_permit_sha256
            ),
        )
        return _result_for_roundtrip_record(
            record,
            head_sha256=record.record_sha256,
            idempotent=False,
        )
