"""Root-owned, role-local carriage boundary for fixed V2 Witness mailboxes.

This module deliberately sits *after* the pure delivery grammar.  It has no
network endpoint, URL, credential, provider SDK, remote client, direct
FI-to-IR route, or generic send operation.  Instead, each role-local process
receives one hard-fenced policy and one of eight explicitly named adapters:

* FI source outbox publishes only ``fi-to-witness``;
* Witness FI ingress consumes only ``fi-to-witness``;
* Witness IR egress publishes only ``witness-to-ir``;
* IR standby ingress consumes only ``witness-to-ir``;
* IR durable-ack outbox publishes only ``ir-to-witness``;
* Witness IR ingress consumes only ``ir-to-witness``;
* Witness FI egress publishes only ``witness-to-fi``; and
* FI writer-ack inbox consumes only ``witness-to-fi``.

The injected adapter surface is intentionally small and role-named.  An
outbound adapter can create one immutable object and return its immutable
receipt.  An inbound adapter can list its already-local fixed prefix and read
one exact key/version pair.  This runtime verifies canonical signed delivery
bytes before reserving durable state, writes a reservation before any outbound
adapter action, then keeps an exact receipt/consume record with a persisted
trusted-clock floor.  A crash after reservation fails closed rather than
attempting a second publication or accepting a fork.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Callable, Iterator
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
from typing import Any, Protocol

from core.append_only_sync_delta_batch import SHA256_RE, canonical_json_bytes
from core import physical_wal_v2_witness_roundtrip_delivery_contract as _delivery


__all__ = (
    "DEFAULT_PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_MAXIMUM_RECORDS",
    "PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_DEFAULT_ENABLED",
    "PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_SCHEMA",
    "PhysicalWalV2WitnessRoundtripDeliveryContent",
    "PhysicalWalV2WitnessRoundtripDeliveryCreateOnlyReceipt",
    "PhysicalWalV2WitnessRoundtripDeliveryImmutableLocator",
    "PhysicalWalV2WitnessRoundtripDeliveryRuntime",
    "PhysicalWalV2WitnessRoundtripDeliveryRuntimeConfig",
    "PhysicalWalV2WitnessRoundtripDeliveryRuntimeError",
    "PhysicalWalV2WitnessRoundtripDeliveryRuntimeResult",
    "PhysicalWalV2WitnessRoundtripFiToWitnessInboundScanner",
    "PhysicalWalV2WitnessRoundtripFiToWitnessPublisher",
    "PhysicalWalV2WitnessRoundtripIrToWitnessInboundScanner",
    "PhysicalWalV2WitnessRoundtripIrToWitnessPublisher",
    "PhysicalWalV2WitnessRoundtripWitnessToFiInboundScanner",
    "PhysicalWalV2WitnessRoundtripWitnessToFiPublisher",
    "PhysicalWalV2WitnessRoundtripWitnessToIrInboundScanner",
    "PhysicalWalV2WitnessRoundtripWitnessToIrPublisher",
    "consume_physical_wal_v2_witness_fi_to_witness_delivery",
    "consume_physical_wal_v2_witness_ir_to_witness_delivery",
    "consume_physical_wal_v2_witness_witness_to_fi_delivery",
    "consume_physical_wal_v2_witness_witness_to_ir_delivery",
    "open_physical_wal_v2_witness_roundtrip_delivery_runtime",
    "publish_physical_wal_v2_witness_fi_to_witness_delivery",
    "publish_physical_wal_v2_witness_ir_to_witness_delivery",
    "publish_physical_wal_v2_witness_witness_to_fi_delivery",
    "publish_physical_wal_v2_witness_witness_to_ir_delivery",
)


PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_SCHEMA = (
    "gold-trade-physical-wal-v2-witness-roundtrip-delivery-runtime-v1"
)
PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_DEFAULT_ENABLED = False
DEFAULT_PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_MAXIMUM_RECORDS = 64

MAX_PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_RECORDS = 256
MAX_PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_STATE_BYTES = 8 * 1024 * 1024
MAX_PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_DELIVERY_BYTES = 2 * 1024 * 1024

_DIRECTORY = "physical-wal-v2-witness-roundtrip-delivery-runtime-v1"
_STATE_FILENAME = "mailbox-state.json"
_LOCK_FILENAME = "mailbox-state.lock"
_STATE_SCHEMA = "gold-trade-physical-wal-v2-witness-roundtrip-delivery-runtime-state-v1"
_STATE_VERSION = 1
_OBJECT_ROOT = "physical-wal-v2-witness-roundtrip-delivery-v1"
_ZERO_SHA256 = "0" * 64
_CAPABILITY = object()

_FI_TO_WITNESS = "fi-to-witness"
_WITNESS_TO_IR = "witness-to-ir"
_IR_TO_WITNESS = "ir-to-witness"
_WITNESS_TO_FI = "witness-to-fi"

_PUBLISH = "publish"
_CONSUME = "consume"
_PUBLISH_RESERVED = "publish-reserved"
_PUBLISHED = "published"
_CONSUME_RESERVED = "consume-reserved"
_CONSUMED = "consumed"

_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", re.ASCII)
_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,255}$", re.ASCII)


@dataclass(frozen=True)
class _RolePolicy:
    mailbox: str
    direction: str


_ROLE_POLICIES = {
    "fi-writer-source-outbox": _RolePolicy(_FI_TO_WITNESS, _PUBLISH),
    "witness-fi-ingress": _RolePolicy(_FI_TO_WITNESS, _CONSUME),
    "witness-ir-egress": _RolePolicy(_WITNESS_TO_IR, _PUBLISH),
    "ir-standby-ack-inbox": _RolePolicy(_WITNESS_TO_IR, _CONSUME),
    "ir-durable-ack-outbox": _RolePolicy(_IR_TO_WITNESS, _PUBLISH),
    "witness-ir-ingress": _RolePolicy(_IR_TO_WITNESS, _CONSUME),
    "witness-fi-egress": _RolePolicy(_WITNESS_TO_FI, _PUBLISH),
    "fi-writer-ack-inbox": _RolePolicy(_WITNESS_TO_FI, _CONSUME),
}

_STATE_FIELDS = frozenset(
    {"schema", "version", "configuration_sha256", "clock_floor", "entries"}
)
_ENTRY_FIELDS = frozenset(
    {
        "sequence",
        "status",
        "local_role",
        "mailbox",
        "delivery_base64",
        "delivery_sha256",
        "object_key",
        "object_version_id",
        "object_content_sha256",
        "object_content_bytes",
        "retained_until",
        "reserved_at",
        "completed_at",
        "clock_floor",
    }
)


class PhysicalWalV2WitnessRoundtripDeliveryRuntimeError(RuntimeError):
    """A root-owned fixed-mailbox delivery transition is unsafe or foreign."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class PhysicalWalV2WitnessRoundtripDeliveryRuntimeConfig:
    """Default-off policy for exactly one fixed sender or recipient role."""

    state_root: Path | None = None
    delivery_config: _delivery.PhysicalWalV2WitnessRoundtripDeliveryConfig | None = field(
        default=None,
        repr=False,
    )
    local_role: str = ""
    enabled: bool = PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_DEFAULT_ENABLED
    maximum_records: int = (
        DEFAULT_PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_MAXIMUM_RECORDS
    )


@dataclass(frozen=True)
class PhysicalWalV2WitnessRoundtripDeliveryCreateOnlyReceipt:
    """Adapter return value for one immutable, create-only publication."""

    object_key: str
    object_version_id: str
    content_sha256: str
    content_bytes: int
    retained_until: datetime
    create_only: bool
    immutable: bool


@dataclass(frozen=True)
class PhysicalWalV2WitnessRoundtripDeliveryImmutableLocator:
    """A scanner-supplied immutable key/version locator in its fixed prefix."""

    object_key: str
    object_version_id: str
    content_sha256: str
    content_bytes: int
    retained_until: datetime
    immutable: bool


@dataclass(frozen=True)
class PhysicalWalV2WitnessRoundtripDeliveryContent:
    """One exact immutable object read; its bytes are still untrusted input."""

    object_key: str
    object_version_id: str
    content_sha256: str
    content_bytes: int
    retained_until: datetime
    immutable: bool
    canonical_delivery: bytes = field(repr=False)


class PhysicalWalV2WitnessRoundtripFiToWitnessPublisher(Protocol):
    def create_fi_to_witness_delivery(
        self,
        *,
        object_key: str,
        canonical_delivery: bytes,
        content_sha256: str,
        content_bytes: int,
        retained_until: datetime,
    ) -> PhysicalWalV2WitnessRoundtripDeliveryCreateOnlyReceipt: ...


class PhysicalWalV2WitnessRoundtripWitnessToIrPublisher(Protocol):
    def create_witness_to_ir_delivery(
        self,
        *,
        object_key: str,
        canonical_delivery: bytes,
        content_sha256: str,
        content_bytes: int,
        retained_until: datetime,
    ) -> PhysicalWalV2WitnessRoundtripDeliveryCreateOnlyReceipt: ...


class PhysicalWalV2WitnessRoundtripIrToWitnessPublisher(Protocol):
    def create_ir_to_witness_delivery(
        self,
        *,
        object_key: str,
        canonical_delivery: bytes,
        content_sha256: str,
        content_bytes: int,
        retained_until: datetime,
    ) -> PhysicalWalV2WitnessRoundtripDeliveryCreateOnlyReceipt: ...


class PhysicalWalV2WitnessRoundtripWitnessToFiPublisher(Protocol):
    def create_witness_to_fi_delivery(
        self,
        *,
        object_key: str,
        canonical_delivery: bytes,
        content_sha256: str,
        content_bytes: int,
        retained_until: datetime,
    ) -> PhysicalWalV2WitnessRoundtripDeliveryCreateOnlyReceipt: ...


class PhysicalWalV2WitnessRoundtripFiToWitnessInboundScanner(Protocol):
    def list_fi_to_witness_delivery_locators(
        self,
    ) -> tuple[PhysicalWalV2WitnessRoundtripDeliveryImmutableLocator, ...]: ...

    def read_fi_to_witness_delivery_exact(
        self, *, object_key: str, object_version_id: str
    ) -> PhysicalWalV2WitnessRoundtripDeliveryContent: ...


class PhysicalWalV2WitnessRoundtripWitnessToIrInboundScanner(Protocol):
    def list_witness_to_ir_delivery_locators(
        self,
    ) -> tuple[PhysicalWalV2WitnessRoundtripDeliveryImmutableLocator, ...]: ...

    def read_witness_to_ir_delivery_exact(
        self, *, object_key: str, object_version_id: str
    ) -> PhysicalWalV2WitnessRoundtripDeliveryContent: ...


class PhysicalWalV2WitnessRoundtripIrToWitnessInboundScanner(Protocol):
    def list_ir_to_witness_delivery_locators(
        self,
    ) -> tuple[PhysicalWalV2WitnessRoundtripDeliveryImmutableLocator, ...]: ...

    def read_ir_to_witness_delivery_exact(
        self, *, object_key: str, object_version_id: str
    ) -> PhysicalWalV2WitnessRoundtripDeliveryContent: ...


class PhysicalWalV2WitnessRoundtripWitnessToFiInboundScanner(Protocol):
    def list_witness_to_fi_delivery_locators(
        self,
    ) -> tuple[PhysicalWalV2WitnessRoundtripDeliveryImmutableLocator, ...]: ...

    def read_witness_to_fi_delivery_exact(
        self, *, object_key: str, object_version_id: str
    ) -> PhysicalWalV2WitnessRoundtripDeliveryContent: ...


@dataclass(frozen=True)
class PhysicalWalV2WitnessRoundtripDeliveryRuntimeResult:
    """A redacted durable receipt or consume observation for one fixed hop."""

    schema: str
    local_role: str
    mailbox: str
    status: str
    delivery_sha256: str
    object_key: str
    object_version_id: str
    object_content_sha256: str
    object_content_bytes: int
    retained_until: datetime
    committed_at: datetime
    idempotent: bool = False
    canonical_delivery: bytes = field(repr=False, default=b"")

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_SERIALIZATION_FORBIDDEN")


@dataclass(frozen=True)
class _Config:
    root: Path
    delivery_config: _delivery.PhysicalWalV2WitnessRoundtripDeliveryConfig
    local_role: str
    policy: _RolePolicy
    configuration_sha256: str
    maximum_records: int


@dataclass(frozen=True)
class _Entry:
    sequence: int
    status: str
    local_role: str
    mailbox: str
    delivery: bytes
    delivery_sha256: str
    object_key: str
    object_version_id: str | None
    object_content_sha256: str | None
    object_content_bytes: int | None
    retained_until: datetime | None
    reserved_at: datetime
    completed_at: datetime | None
    clock_floor: datetime


@dataclass(frozen=True)
class _State:
    entries: tuple[_Entry, ...]
    clock_floor: datetime | None
    head_sha256: str


@dataclass(frozen=True)
class _Storage:
    directory_fd: int


class PhysicalWalV2WitnessRoundtripDeliveryRuntime:
    """Nonserializable root-owned handle with an exact stale-state fence."""

    __slots__ = ("_config", "_expected_head_sha256", "_capability")

    def __init__(self, config: _Config, head_sha256: str, capability: object) -> None:
        if capability is not _CAPABILITY:
            raise TypeError("V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_CONSTRUCTION_FORBIDDEN")
        self._config = config
        self._expected_head_sha256: str | None = head_sha256
        self._capability = capability

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_SERIALIZATION_FORBIDDEN")


def _fail(code: str) -> None:
    raise PhysicalWalV2WitnessRoundtripDeliveryRuntimeError(code)


def _canonical(value: object, *, code: str) -> bytes:
    try:
        return canonical_json_bytes(value)
    except (TypeError, ValueError) as exc:
        raise PhysicalWalV2WitnessRoundtripDeliveryRuntimeError(code) from exc


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256(value: object, *, code: str, permit_zero: bool = False) -> str:
    if (
        type(value) is not str
        or SHA256_RE.fullmatch(value) is None
        or (not permit_zero and value == _ZERO_SHA256)
    ):
        _fail(code)
    return value


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_JSON_DUPLICATE_FIELD")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> None:
    _fail("V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_JSON_INVALID")


def _parse_canonical(raw: object, *, code: str) -> dict[str, Any]:
    if type(raw) is not bytes or not 1 <= len(raw) <= MAX_PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_STATE_BYTES:
        _fail(code)
    try:
        parsed = json.loads(
            raw.decode("ascii", "strict"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
        raise PhysicalWalV2WitnessRoundtripDeliveryRuntimeError(code) from exc
    if type(parsed) is not dict or _canonical(parsed, code=code) != raw:
        _fail(code)
    return dict(parsed)


def _exact_mapping(value: object, *, fields: frozenset[str], code: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != fields:
        _fail(code)
    return dict(value)


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
        raise PhysicalWalV2WitnessRoundtripDeliveryRuntimeError(code) from exc


def _host_now() -> datetime:
    try:
        return datetime.now(timezone.utc).replace(microsecond=0)
    except Exception as exc:
        raise PhysicalWalV2WitnessRoundtripDeliveryRuntimeError(
            "V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_CLOCK_INVALID"
        ) from exc


def _require_root() -> None:
    try:
        if os.geteuid() != 0:
            _fail("V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_ROOT_REQUIRED")
    except OSError as exc:
        raise PhysicalWalV2WitnessRoundtripDeliveryRuntimeError(
            "V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_ROOT_REQUIRED"
        ) from exc


def _require_fd_platform() -> None:
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        _fail("V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_PLATFORM_UNSAFE")


def _safe_root_path(value: object) -> Path:
    if not isinstance(value, Path) or not value.is_absolute() or any(
        part in {"", ".", ".."} for part in value.parts[1:]
    ):
        _fail("V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_STATE_ROOT_UNSAFE")
    return value


def _check_directory(info: os.stat_result, *, final: bool) -> None:
    mode = stat.S_IMODE(info.st_mode)
    sticky_root_parent = info.st_uid == 0 and bool(info.st_mode & stat.S_ISVTX)
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != 0
        or (final and mode != 0o700)
        or (not final and mode & 0o022 and not sticky_root_parent)
    ):
        _fail("V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_STATE_ROOT_UNSAFE")


def _open_secure_root(value: Path) -> int:
    _require_fd_platform()
    path = _safe_root_path(value)
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    descriptor = -1
    try:
        descriptor = os.open("/", flags)
        parts = path.parts[1:]
        if not parts:
            _fail("V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_STATE_ROOT_UNSAFE")
        for index, component in enumerate(parts):
            next_descriptor = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
            _check_directory(os.fstat(descriptor), final=index == len(parts) - 1)
        return descriptor
    except PhysicalWalV2WitnessRoundtripDeliveryRuntimeError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError as exc:
        if descriptor >= 0:
            os.close(descriptor)
        raise PhysicalWalV2WitnessRoundtripDeliveryRuntimeError(
            "V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_STATE_ROOT_UNSAFE"
        ) from exc


def _fsync_fd(descriptor: int, *, code: str) -> None:
    try:
        os.fsync(descriptor)
    except OSError as exc:
        raise PhysicalWalV2WitnessRoundtripDeliveryRuntimeError(code) from exc


def _safe_child_metadata(parent_fd: int, name: str, *, directory: bool, code: str) -> os.stat_result:
    try:
        info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except OSError as exc:
        raise PhysicalWalV2WitnessRoundtripDeliveryRuntimeError(code) from exc
    if (
        stat.S_ISLNK(info.st_mode)
        or (directory and not stat.S_ISDIR(info.st_mode))
        or (not directory and not stat.S_ISREG(info.st_mode))
        or info.st_uid != 0
        or stat.S_IMODE(info.st_mode) != (0o700 if directory else 0o600)
        or (not directory and info.st_nlink != 1)
    ):
        _fail(code)
    return info


def _ensure_child_directory(parent_fd: int, name: str) -> int:
    _require_fd_platform()
    descriptor = -1
    try:
        try:
            os.mkdir(name, mode=0o700, dir_fd=parent_fd)
            _fsync_fd(parent_fd, code="V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_DIRECTORY_FSYNC_FAILED")
        except FileExistsError:
            pass
        descriptor = os.open(
            name,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
            dir_fd=parent_fd,
        )
        _safe_child_metadata(
            parent_fd,
            name,
            directory=True,
            code="V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_DIRECTORY_UNSAFE",
        )
        _check_directory(os.fstat(descriptor), final=True)
        return descriptor
    except PhysicalWalV2WitnessRoundtripDeliveryRuntimeError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError as exc:
        if descriptor >= 0:
            os.close(descriptor)
        raise PhysicalWalV2WitnessRoundtripDeliveryRuntimeError(
            "V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_DIRECTORY_UNSAFE"
        ) from exc


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
                _fsync_fd(directory_fd, code="V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_DIRECTORY_FSYNC_FAILED")
            except FileExistsError:
                descriptor = os.open(_LOCK_FILENAME, flags, dir_fd=directory_fd)
        _safe_child_metadata(
            directory_fd,
            _LOCK_FILENAME,
            directory=False,
            code="V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_LOCK_UNSAFE",
        )
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        return descriptor
    except PhysicalWalV2WitnessRoundtripDeliveryRuntimeError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError as exc:
        if descriptor >= 0:
            os.close(descriptor)
        raise PhysicalWalV2WitnessRoundtripDeliveryRuntimeError(
            "V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_LOCK_OPEN_FAILED"
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
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_uid != 0
            or stat.S_IMODE(info.st_mode) != 0o600
            or not 1 <= info.st_size <= MAX_PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_STATE_BYTES
        ):
            _fail(code)
        chunks = bytearray()
        while len(chunks) < info.st_size:
            chunk = os.read(descriptor, info.st_size - len(chunks))
            if not chunk:
                _fail(code)
            chunks.extend(chunk)
        if os.read(descriptor, 1):
            _fail(code)
        return bytes(chunks)
    except PhysicalWalV2WitnessRoundtripDeliveryRuntimeError:
        raise
    except OSError as exc:
        raise PhysicalWalV2WitnessRoundtripDeliveryRuntimeError(code) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _b64(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _unb64(value: object, *, code: str) -> bytes:
    if type(value) is not str:
        _fail(code)
    try:
        raw = base64.b64decode(value.encode("ascii", "strict"), validate=True)
    except (UnicodeEncodeError, binascii.Error) as exc:
        raise PhysicalWalV2WitnessRoundtripDeliveryRuntimeError(code) from exc
    if not 1 <= len(raw) <= MAX_PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_DELIVERY_BYTES:
        _fail(code)
    return raw


def _object_prefix(mailbox: str) -> str:
    if mailbox not in {_FI_TO_WITNESS, _WITNESS_TO_IR, _IR_TO_WITNESS, _WITNESS_TO_FI}:
        _fail("V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_MAILBOX_INVALID")
    return _OBJECT_ROOT + "/" + mailbox + "/"


def _object_key(mailbox: str, delivery_sha256: str) -> str:
    return _object_prefix(mailbox) + _sha256(
        delivery_sha256,
        code="V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_OBJECT_KEY_INVALID",
    ) + ".json"


def _key_delivery_sha256(value: object, *, policy: _RolePolicy, code: str) -> str:
    if type(value) is not str or not value.isascii() or not value.startswith(_object_prefix(policy.mailbox)):
        _fail(code)
    suffix = value[len(_object_prefix(policy.mailbox)) :]
    if not suffix.endswith(".json"):
        _fail(code)
    return _sha256(suffix[:-5], code=code)


def _version(value: object, *, code: str) -> str:
    if type(value) is not str or _VERSION_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _verify_delivery(
    raw: object, *, config: _Config, now: datetime
) -> _delivery.VerifiedPhysicalWalV2WitnessRoundtripDelivery:
    if type(raw) is not bytes or not 1 <= len(raw) <= MAX_PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_DELIVERY_BYTES:
        _fail("V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_DELIVERY_INVALID")
    verifier: Callable[..., _delivery.VerifiedPhysicalWalV2WitnessRoundtripDelivery]
    if config.policy.mailbox == _FI_TO_WITNESS:
        verifier = _delivery.verify_physical_wal_v2_witness_fi_to_witness_delivery
    elif config.policy.mailbox == _WITNESS_TO_IR:
        verifier = _delivery.verify_physical_wal_v2_witness_witness_to_ir_delivery
    elif config.policy.mailbox == _IR_TO_WITNESS:
        verifier = _delivery.verify_physical_wal_v2_witness_ir_to_witness_delivery
    else:
        verifier = _delivery.verify_physical_wal_v2_witness_witness_to_fi_delivery
    try:
        verified = verifier(raw, config=config.delivery_config, now=now)
    except _delivery.PhysicalWalV2WitnessRoundtripDeliveryError as exc:
        raise PhysicalWalV2WitnessRoundtripDeliveryRuntimeError(
            "V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_DELIVERY_INVALID"
        ) from exc
    if verified.canonical_delivery != raw or verified.mailbox != config.policy.mailbox:
        _fail("V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_DELIVERY_INVALID")
    return verified


def _configuration_sha256(
    *,
    local_role: str,
    policy: _RolePolicy,
    delivery_config: _delivery.PhysicalWalV2WitnessRoundtripDeliveryConfig,
    maximum_records: int,
) -> str:
    try:
        facts = _delivery._config(delivery_config, mailbox=policy.mailbox)
    except (AttributeError, TypeError, ValueError, _delivery.PhysicalWalV2WitnessRoundtripDeliveryError) as exc:
        raise PhysicalWalV2WitnessRoundtripDeliveryRuntimeError(
            "V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_POLICY_INVALID"
        ) from exc
    return _sha256_bytes(
        _canonical(
            {
                "schema": PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_SCHEMA,
                "local_role": local_role,
                "mailbox": policy.mailbox,
                "direction": policy.direction,
                "delivery_binding_sha256": facts.binding_sha256,
                "roundtrip_configuration_sha256": facts.binding.roundtrip_configuration_sha256,
                "maximum_records": maximum_records,
            },
            code="V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_POLICY_INVALID",
        )
    )


def _config(value: object) -> _Config:
    if type(value) is not PhysicalWalV2WitnessRoundtripDeliveryRuntimeConfig:
        _fail("V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_CONFIG_INVALID")
    if (
        value.enabled is not True
        or type(value.maximum_records) is not int
        or not 1 <= value.maximum_records <= MAX_PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_RECORDS
    ):
        _fail("V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_CONFIG_INVALID")
    try:
        policy = _ROLE_POLICIES[value.local_role]
    except (KeyError, TypeError) as exc:
        raise PhysicalWalV2WitnessRoundtripDeliveryRuntimeError(
            "V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_ROLE_INVALID"
        ) from exc
    if type(value.delivery_config) is not _delivery.PhysicalWalV2WitnessRoundtripDeliveryConfig:
        _fail("V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_POLICY_INVALID")
    _require_root()
    root = _safe_root_path(value.state_root)
    digest = _configuration_sha256(
        local_role=value.local_role,
        policy=policy,
        delivery_config=value.delivery_config,
        maximum_records=value.maximum_records,
    )
    return _Config(
        root=root,
        delivery_config=value.delivery_config,
        local_role=value.local_role,
        policy=policy,
        configuration_sha256=digest,
        maximum_records=value.maximum_records,
    )


def _entry_statuses(policy: _RolePolicy) -> frozenset[str]:
    return (
        frozenset({_PUBLISH_RESERVED, _PUBLISHED})
        if policy.direction == _PUBLISH
        else frozenset({_CONSUME_RESERVED, _CONSUMED})
    )


def _entry_from_mapping(value: object, *, config: _Config) -> _Entry:
    item = _exact_mapping(
        value,
        fields=_ENTRY_FIELDS,
        code="V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_ENTRY_INVALID",
    )
    if (
        type(item["sequence"]) is not int
        or item["sequence"] < 1
        or type(item["status"]) is not str
        or item["status"] not in _entry_statuses(config.policy)
        or item["local_role"] != config.local_role
        or item["mailbox"] != config.policy.mailbox
    ):
        _fail("V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_ENTRY_INVALID")
    reserved_at = _parse_timestamp(
        item["reserved_at"], code="V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_ENTRY_TIME_INVALID"
    )
    clock_floor = _parse_timestamp(
        item["clock_floor"], code="V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_ENTRY_TIME_INVALID"
    )
    if reserved_at > clock_floor:
        _fail("V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_ENTRY_TIME_INVALID")
    delivery = _unb64(
        item["delivery_base64"], code="V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_ENTRY_DELIVERY_INVALID"
    )
    verified = _verify_delivery(delivery, config=config, now=reserved_at)
    delivery_sha = _sha256(
        item["delivery_sha256"], code="V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_ENTRY_DELIVERY_INVALID"
    )
    if verified.delivery_sha256 != delivery_sha:
        _fail("V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_ENTRY_DELIVERY_INVALID")
    object_key = item["object_key"]
    if object_key != _object_key(config.policy.mailbox, delivery_sha):
        _fail("V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_ENTRY_OBJECT_INVALID")
    complete = item["status"] in {_PUBLISHED, _CONSUMED}
    if not complete:
        if any(
            item[name] is not None
            for name in (
                "object_version_id",
                "object_content_sha256",
                "object_content_bytes",
                "retained_until",
                "completed_at",
            )
        ):
            _fail("V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_ENTRY_STATUS_INVALID")
        return _Entry(
            sequence=item["sequence"],
            status=item["status"],
            local_role=config.local_role,
            mailbox=config.policy.mailbox,
            delivery=delivery,
            delivery_sha256=delivery_sha,
            object_key=object_key,
            object_version_id=None,
            object_content_sha256=None,
            object_content_bytes=None,
            retained_until=None,
            reserved_at=reserved_at,
            completed_at=None,
            clock_floor=clock_floor,
        )
    version = _version(
        item["object_version_id"], code="V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_ENTRY_OBJECT_INVALID"
    )
    content_sha = _sha256(
        item["object_content_sha256"], code="V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_ENTRY_OBJECT_INVALID"
    )
    if content_sha != delivery_sha or type(item["object_content_bytes"]) is not int or item["object_content_bytes"] != len(delivery):
        _fail("V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_ENTRY_OBJECT_INVALID")
    retained_until = _parse_timestamp(
        item["retained_until"], code="V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_ENTRY_TIME_INVALID"
    )
    completed_at = _parse_timestamp(
        item["completed_at"], code="V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_ENTRY_TIME_INVALID"
    )
    if retained_until < verified.expires_at or completed_at != clock_floor:
        _fail("V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_ENTRY_TIME_INVALID")
    return _Entry(
        sequence=item["sequence"],
        status=item["status"],
        local_role=config.local_role,
        mailbox=config.policy.mailbox,
        delivery=delivery,
        delivery_sha256=delivery_sha,
        object_key=object_key,
        object_version_id=version,
        object_content_sha256=content_sha,
        object_content_bytes=len(delivery),
        retained_until=retained_until,
        reserved_at=reserved_at,
        completed_at=completed_at,
        clock_floor=clock_floor,
    )


def _entry_mapping(value: _Entry) -> dict[str, object]:
    return {
        "sequence": value.sequence,
        "status": value.status,
        "local_role": value.local_role,
        "mailbox": value.mailbox,
        "delivery_base64": _b64(value.delivery),
        "delivery_sha256": value.delivery_sha256,
        "object_key": value.object_key,
        "object_version_id": value.object_version_id,
        "object_content_sha256": value.object_content_sha256,
        "object_content_bytes": value.object_content_bytes,
        "retained_until": (
            None
            if value.retained_until is None
            else _timestamp(value.retained_until, code="V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_ENTRY_TIME_INVALID")
        ),
        "reserved_at": _timestamp(
            value.reserved_at, code="V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_ENTRY_TIME_INVALID"
        ),
        "completed_at": (
            None
            if value.completed_at is None
            else _timestamp(value.completed_at, code="V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_ENTRY_TIME_INVALID")
        ),
        "clock_floor": _timestamp(
            value.clock_floor, code="V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_ENTRY_TIME_INVALID"
        ),
    }


def _state_mapping(*, config: _Config, state: _State) -> dict[str, object]:
    if state.clock_floor is None:
        _fail("V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_STATE_CLOCK_INVALID")
    return {
        "schema": _STATE_SCHEMA,
        "version": _STATE_VERSION,
        "configuration_sha256": config.configuration_sha256,
        "clock_floor": _timestamp(
            state.clock_floor, code="V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_STATE_CLOCK_INVALID"
        ),
        "entries": [_entry_mapping(entry) for entry in state.entries],
    }


def _validate_entries(entries: tuple[_Entry, ...], *, state_floor: datetime) -> None:
    if tuple(entry.sequence for entry in entries) != tuple(range(1, len(entries) + 1)):
        _fail("V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_ENTRY_ORDER_INVALID")
    if any(entry.clock_floor > state_floor for entry in entries):
        _fail("V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_STATE_CLOCK_INVALID")
    if any(
        earlier.clock_floor > later.clock_floor
        for earlier, later in zip(entries, entries[1:])
    ):
        _fail("V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_ENTRY_CLOCK_ORDER_INVALID")
    if len({entry.delivery_sha256 for entry in entries}) != len(entries):
        _fail("V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_DELIVERY_REPLAY_OR_FORK")
    completed = [entry for entry in entries if entry.status in {_PUBLISHED, _CONSUMED}]
    if len({(entry.object_key, entry.object_version_id) for entry in completed}) != len(completed):
        _fail("V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_OBJECT_VERSION_FORK")


def _load_state(storage: _Storage, *, config: _Config, trusted_now: datetime) -> _State:
    raw = _read_file_at(
        storage.directory_fd,
        _STATE_FILENAME,
        code="V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_STATE_UNSAFE",
    )
    if raw is None:
        return _State(entries=(), clock_floor=None, head_sha256=_ZERO_SHA256)
    item = _exact_mapping(
        _parse_canonical(raw, code="V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_STATE_INVALID"),
        fields=_STATE_FIELDS,
        code="V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_STATE_INVALID",
    )
    if (
        item["schema"] != _STATE_SCHEMA
        or item["version"] != _STATE_VERSION
        or _sha256(
            item["configuration_sha256"],
            code="V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_CONFIGURATION_CONFLICT",
        )
        != config.configuration_sha256
        or type(item["entries"]) is not list
        or len(item["entries"]) > config.maximum_records
    ):
        _fail("V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_CONFIGURATION_CONFLICT")
    floor = _parse_timestamp(
        item["clock_floor"], code="V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_STATE_CLOCK_INVALID"
    )
    if trusted_now < floor:
        _fail("V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_CLOCK_ROLLBACK_DETECTED")
    entries = tuple(_entry_from_mapping(entry, config=config) for entry in item["entries"])
    _validate_entries(entries, state_floor=floor)
    return _State(entries=entries, clock_floor=floor, head_sha256=_sha256_bytes(raw))


def _write_all(descriptor: int, payload: bytes, *, code: str) -> None:
    view = memoryview(payload)
    try:
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                _fail(code)
            view = view[written:]
    except PhysicalWalV2WitnessRoundtripDeliveryRuntimeError:
        raise
    except OSError as exc:
        raise PhysicalWalV2WitnessRoundtripDeliveryRuntimeError(code) from exc


def _write_state(storage: _Storage, *, config: _Config, state: _State) -> str:
    _require_fd_platform()
    payload = _canonical(
        _state_mapping(config=config, state=state),
        code="V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_STATE_INVALID",
    )
    if not 1 <= len(payload) <= MAX_PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_STATE_BYTES:
        _fail("V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_STATE_SIZE_INVALID")
    temporary = ".mailbox-state." + secrets.token_hex(16) + ".tmp"
    descriptor = -1
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
            code="V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_TEMPORARY_UNSAFE",
        )
        _write_all(
            descriptor,
            payload,
            code="V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_TEMPORARY_WRITE_FAILED",
        )
        _fsync_fd(descriptor, code="V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_TEMPORARY_FSYNC_FAILED")
        os.close(descriptor)
        descriptor = -1
        os.replace(
            temporary,
            _STATE_FILENAME,
            src_dir_fd=storage.directory_fd,
            dst_dir_fd=storage.directory_fd,
        )
        _fsync_fd(storage.directory_fd, code="V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_DIRECTORY_FSYNC_FAILED")
        _safe_child_metadata(
            storage.directory_fd,
            _STATE_FILENAME,
            directory=False,
            code="V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_STATE_UNSAFE",
        )
        return _sha256_bytes(payload)
    except PhysicalWalV2WitnessRoundtripDeliveryRuntimeError:
        raise
    except OSError as exc:
        raise PhysicalWalV2WitnessRoundtripDeliveryRuntimeError(
            "V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_STATE_WRITE_FAILED"
        ) from exc
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass


def _runtime(value: object, *, local_role: str, direction: str) -> PhysicalWalV2WitnessRoundtripDeliveryRuntime:
    if (
        type(value) is not PhysicalWalV2WitnessRoundtripDeliveryRuntime
        or value._capability is not _CAPABILITY
        or value._expected_head_sha256 is None
        or value._config.local_role != local_role
        or value._config.policy.direction != direction
    ):
        _fail("V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_HANDLE_INVALID")
    return value


def _load_for_runtime(
    runtime: PhysicalWalV2WitnessRoundtripDeliveryRuntime,
    *,
    storage: _Storage,
    now: datetime,
) -> _State:
    state = _load_state(storage, config=runtime._config, trusted_now=now)
    if state.head_sha256 != runtime._expected_head_sha256:
        _fail("V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_STALE_HANDLE")
    return state


def _result(entry: _Entry, *, idempotent: bool) -> PhysicalWalV2WitnessRoundtripDeliveryRuntimeResult:
    if (
        entry.status not in {_PUBLISHED, _CONSUMED}
        or entry.object_version_id is None
        or entry.object_content_sha256 is None
        or entry.object_content_bytes is None
        or entry.retained_until is None
        or entry.completed_at is None
    ):
        _fail("V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_RESULT_UNAVAILABLE")
    return PhysicalWalV2WitnessRoundtripDeliveryRuntimeResult(
        schema=PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_SCHEMA,
        local_role=entry.local_role,
        mailbox=entry.mailbox,
        status=entry.status,
        delivery_sha256=entry.delivery_sha256,
        object_key=entry.object_key,
        object_version_id=entry.object_version_id,
        object_content_sha256=entry.object_content_sha256,
        object_content_bytes=entry.object_content_bytes,
        retained_until=entry.retained_until,
        committed_at=entry.completed_at,
        idempotent=idempotent,
        canonical_delivery=entry.delivery,
    )


def _completed_entry(
    reserved: _Entry,
    *,
    status: str,
    object_version_id: str,
    retained_until: datetime,
    completed_at: datetime,
) -> _Entry:
    if status not in {_PUBLISHED, _CONSUMED}:
        _fail("V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_ENTRY_STATUS_INVALID")
    return replace(
        reserved,
        status=status,
        object_version_id=object_version_id,
        object_content_sha256=reserved.delivery_sha256,
        object_content_bytes=len(reserved.delivery),
        retained_until=retained_until,
        completed_at=completed_at,
        clock_floor=completed_at,
    )


def _reservation(
    *, state: _State, config: _Config, delivery: bytes, delivery_sha256: str, now: datetime
) -> _Entry:
    status = _PUBLISH_RESERVED if config.policy.direction == _PUBLISH else _CONSUME_RESERVED
    return _Entry(
        sequence=len(state.entries) + 1,
        status=status,
        local_role=config.local_role,
        mailbox=config.policy.mailbox,
        delivery=delivery,
        delivery_sha256=delivery_sha256,
        object_key=_object_key(config.policy.mailbox, delivery_sha256),
        object_version_id=None,
        object_content_sha256=None,
        object_content_bytes=None,
        retained_until=None,
        reserved_at=now,
        completed_at=None,
        clock_floor=now,
    )


def _append_state(state: _State, entry: _Entry, *, floor: datetime) -> _State:
    return _State(entries=state.entries + (entry,), clock_floor=floor, head_sha256="")


def _replace_last(state: _State, entry: _Entry, *, floor: datetime) -> _State:
    if not state.entries or state.entries[-1].sequence != entry.sequence:
        _fail("V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_RESERVATION_LOST")
    return _State(entries=state.entries[:-1] + (entry,), clock_floor=floor, head_sha256="")


def _find_delivery(state: _State, delivery_sha256: str) -> _Entry | None:
    for entry in state.entries:
        if entry.delivery_sha256 == delivery_sha256:
            return entry
    return None


def _require_exact_existing(
    entry: _Entry,
    *,
    delivery: bytes,
    object_key: str,
    object_version_id: str | None = None,
    retained_until: datetime | None = None,
) -> None:
    if entry.delivery != delivery or entry.object_key != object_key:
        _fail("V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_DELIVERY_REPLAY_OR_FORK")
    if object_version_id is not None and (
        entry.object_version_id != object_version_id or entry.retained_until != retained_until
    ):
        _fail("V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_OBJECT_VERSION_FORK")


def _validate_create_receipt(
    value: object,
    *,
    object_key: str,
    delivery_sha256: str,
    delivery_bytes: int,
    required_retention: datetime,
) -> PhysicalWalV2WitnessRoundtripDeliveryCreateOnlyReceipt:
    if type(value) is not PhysicalWalV2WitnessRoundtripDeliveryCreateOnlyReceipt:
        _fail("V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_PUBLISH_RECEIPT_INVALID")
    receipt = value
    if (
        receipt.object_key != object_key
        or _version(
            receipt.object_version_id,
            code="V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_PUBLISH_RECEIPT_INVALID",
        )
        != receipt.object_version_id
        or _sha256(
            receipt.content_sha256,
            code="V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_PUBLISH_RECEIPT_INVALID",
        )
        != delivery_sha256
        or type(receipt.content_bytes) is not int
        or receipt.content_bytes != delivery_bytes
        or receipt.create_only is not True
        or receipt.immutable is not True
    ):
        _fail("V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_PUBLISH_RECEIPT_INVALID")
    retained = _utc(
        receipt.retained_until,
        code="V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_PUBLISH_RECEIPT_INVALID",
    )
    if retained < required_retention:
        _fail("V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_PUBLISH_RECEIPT_INVALID")
    return receipt


def _validate_locator(
    value: object, *, config: _Config, now: datetime
) -> PhysicalWalV2WitnessRoundtripDeliveryImmutableLocator:
    if type(value) is not PhysicalWalV2WitnessRoundtripDeliveryImmutableLocator:
        _fail("V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_LOCATOR_INVALID")
    locator = value
    key_sha = _key_delivery_sha256(
        locator.object_key,
        policy=config.policy,
        code="V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_LOCATOR_PREFIX_INVALID",
    )
    if (
        _version(locator.object_version_id, code="V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_LOCATOR_INVALID")
        != locator.object_version_id
        or _sha256(locator.content_sha256, code="V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_LOCATOR_INVALID")
        != key_sha
        or type(locator.content_bytes) is not int
        or not 1 <= locator.content_bytes <= MAX_PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_DELIVERY_BYTES
        or locator.immutable is not True
    ):
        _fail("V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_LOCATOR_INVALID")
    if _utc(locator.retained_until, code="V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_LOCATOR_INVALID") < now:
        _fail("V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_LOCATOR_RETENTION_INVALID")
    return locator


def _validate_content(
    value: object,
    *,
    locator: PhysicalWalV2WitnessRoundtripDeliveryImmutableLocator,
) -> PhysicalWalV2WitnessRoundtripDeliveryContent:
    if type(value) is not PhysicalWalV2WitnessRoundtripDeliveryContent:
        _fail("V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_CONTENT_INVALID")
    content = value
    if (
        content.object_key != locator.object_key
        or content.object_version_id != locator.object_version_id
        or content.content_sha256 != locator.content_sha256
        or content.content_bytes != locator.content_bytes
        or content.retained_until != locator.retained_until
        or content.immutable is not True
        or type(content.canonical_delivery) is not bytes
        or len(content.canonical_delivery) != locator.content_bytes
        or _sha256_bytes(content.canonical_delivery) != locator.content_sha256
    ):
        _fail("V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_CONTENT_INVALID")
    return content


def open_physical_wal_v2_witness_roundtrip_delivery_runtime(
    *, config: PhysicalWalV2WitnessRoundtripDeliveryRuntimeConfig
) -> PhysicalWalV2WitnessRoundtripDeliveryRuntime:
    resolved = _config(config)
    now = _host_now()
    with _locked(resolved) as storage:
        state = _load_state(storage, config=resolved, trusted_now=now)
    return PhysicalWalV2WitnessRoundtripDeliveryRuntime(
        resolved,
        state.head_sha256,
        _CAPABILITY,
    )


def _publish(
    *,
    runtime_value: object,
    local_role: str,
    delivery: bytes,
    create: Callable[..., PhysicalWalV2WitnessRoundtripDeliveryCreateOnlyReceipt],
) -> PhysicalWalV2WitnessRoundtripDeliveryRuntimeResult:
    runtime = _runtime(runtime_value, local_role=local_role, direction=_PUBLISH)
    now = _host_now()
    verified = _verify_delivery(delivery, config=runtime._config, now=now)
    raw = verified.canonical_delivery
    object_key = _object_key(runtime._config.policy.mailbox, verified.delivery_sha256)
    with _locked(runtime._config) as storage:
        state = _load_for_runtime(runtime, storage=storage, now=now)
        existing = _find_delivery(state, verified.delivery_sha256)
        if existing is not None:
            # A persisted receipt is historical evidence, not a lease to
            # replay an expired carrier byte string.  Re-read the trusted
            # clock and reverify before exposing any cached result.
            current_now = _host_now()
            if current_now < now:
                _fail("V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_CLOCK_ROLLBACK_DETECTED")
            state = _load_for_runtime(runtime, storage=storage, now=current_now)
            existing = _find_delivery(state, verified.delivery_sha256)
            if existing is None:
                _fail("V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_RESERVATION_LOST")
            current = _verify_delivery(existing.delivery, config=runtime._config, now=current_now)
            if current.delivery_sha256 != verified.delivery_sha256:
                _fail("V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_DELIVERY_REPLAY_OR_FORK")
            _require_exact_existing(existing, delivery=raw, object_key=object_key)
            if existing.status == _PUBLISH_RESERVED:
                _fail("V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_PUBLISH_INDETERMINATE")
            return _result(existing, idempotent=True)
        if len(state.entries) >= runtime._config.maximum_records:
            _fail("V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_RECORD_LIMIT")
        reserved = _reservation(
            state=state,
            config=runtime._config,
            delivery=raw,
            delivery_sha256=verified.delivery_sha256,
            now=now,
        )
        reserved_state = _append_state(state, reserved, floor=now)
        runtime._expected_head_sha256 = _write_state(
            storage,
            config=runtime._config,
            state=reserved_state,
        )
        # A reservation intentionally survives a crash, but it must not turn
        # into an authorization to invoke an adapter after the carrier has
        # expired.  Fence the freshly persisted head under a fresh clock just
        # before the sole external side effect.
        publish_now = _host_now()
        pre_publish_state = _load_for_runtime(runtime, storage=storage, now=publish_now)
        pre_publish_reserved = _find_delivery(pre_publish_state, verified.delivery_sha256)
        if pre_publish_reserved is None or pre_publish_reserved.status != _PUBLISH_RESERVED:
            _fail("V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_RESERVATION_LOST")
        _require_exact_existing(pre_publish_reserved, delivery=raw, object_key=object_key)
        pre_publish = _verify_delivery(raw, config=runtime._config, now=publish_now)
        if pre_publish.delivery_sha256 != verified.delivery_sha256:
            _fail("V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_DELIVERY_REPLAY_OR_FORK")
        try:
            receipt = create(
                object_key=object_key,
                canonical_delivery=raw,
                content_sha256=verified.delivery_sha256,
                content_bytes=len(raw),
                retained_until=verified.expires_at,
            )
        except PhysicalWalV2WitnessRoundtripDeliveryRuntimeError:
            raise
        except Exception as exc:
            raise PhysicalWalV2WitnessRoundtripDeliveryRuntimeError(
                "V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_PUBLISHER_FAILED"
            ) from exc
        checked = _validate_create_receipt(
            receipt,
            object_key=object_key,
            delivery_sha256=verified.delivery_sha256,
            delivery_bytes=len(raw),
            required_retention=verified.expires_at,
        )
        # The adapter can take arbitrarily long.  A success receipt must not
        # turn an expired/rolled-back observation into a completed record.
        # The durable reservation remains the only safe outcome in that case.
        completed_now = _host_now()
        current_state = _load_for_runtime(runtime, storage=storage, now=completed_now)
        current_reserved = _find_delivery(current_state, verified.delivery_sha256)
        if current_reserved is None or current_reserved.status != _PUBLISH_RESERVED:
            _fail("V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_RESERVATION_LOST")
        _require_exact_existing(current_reserved, delivery=raw, object_key=object_key)
        current = _verify_delivery(raw, config=runtime._config, now=completed_now)
        if current.delivery_sha256 != verified.delivery_sha256:
            _fail("V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_DELIVERY_REPLAY_OR_FORK")
        completed = _completed_entry(
            current_reserved,
            status=_PUBLISHED,
            object_version_id=checked.object_version_id,
            retained_until=_utc(
                checked.retained_until,
                code="V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_PUBLISH_RECEIPT_INVALID",
            ),
            completed_at=completed_now,
        )
        try:
            runtime._expected_head_sha256 = _write_state(
                storage,
                config=runtime._config,
                state=_replace_last(current_state, completed, floor=completed_now),
            )
        except Exception:
            runtime._expected_head_sha256 = None
            raise
        return _result(completed, idempotent=False)


def publish_physical_wal_v2_witness_fi_to_witness_delivery(
    runtime: PhysicalWalV2WitnessRoundtripDeliveryRuntime,
    delivery: bytes,
    *,
    publisher: PhysicalWalV2WitnessRoundtripFiToWitnessPublisher,
) -> PhysicalWalV2WitnessRoundtripDeliveryRuntimeResult:
    return _publish(
        runtime_value=runtime,
        local_role="fi-writer-source-outbox",
        delivery=delivery,
        create=publisher.create_fi_to_witness_delivery,
    )


def publish_physical_wal_v2_witness_witness_to_ir_delivery(
    runtime: PhysicalWalV2WitnessRoundtripDeliveryRuntime,
    delivery: bytes,
    *,
    publisher: PhysicalWalV2WitnessRoundtripWitnessToIrPublisher,
) -> PhysicalWalV2WitnessRoundtripDeliveryRuntimeResult:
    return _publish(
        runtime_value=runtime,
        local_role="witness-ir-egress",
        delivery=delivery,
        create=publisher.create_witness_to_ir_delivery,
    )


def publish_physical_wal_v2_witness_ir_to_witness_delivery(
    runtime: PhysicalWalV2WitnessRoundtripDeliveryRuntime,
    delivery: bytes,
    *,
    publisher: PhysicalWalV2WitnessRoundtripIrToWitnessPublisher,
) -> PhysicalWalV2WitnessRoundtripDeliveryRuntimeResult:
    return _publish(
        runtime_value=runtime,
        local_role="ir-durable-ack-outbox",
        delivery=delivery,
        create=publisher.create_ir_to_witness_delivery,
    )


def publish_physical_wal_v2_witness_witness_to_fi_delivery(
    runtime: PhysicalWalV2WitnessRoundtripDeliveryRuntime,
    delivery: bytes,
    *,
    publisher: PhysicalWalV2WitnessRoundtripWitnessToFiPublisher,
) -> PhysicalWalV2WitnessRoundtripDeliveryRuntimeResult:
    return _publish(
        runtime_value=runtime,
        local_role="witness-fi-egress",
        delivery=delivery,
        create=publisher.create_witness_to_fi_delivery,
    )


def _check_consume_state(
    runtime: PhysicalWalV2WitnessRoundtripDeliveryRuntime, *, now: datetime
) -> None:
    with _locked(runtime._config) as storage:
        _load_for_runtime(runtime, storage=storage, now=now)


def _consume_verified(
    *,
    runtime: PhysicalWalV2WitnessRoundtripDeliveryRuntime,
    verified: _delivery.VerifiedPhysicalWalV2WitnessRoundtripDelivery,
    locator: PhysicalWalV2WitnessRoundtripDeliveryImmutableLocator,
    now: datetime,
) -> PhysicalWalV2WitnessRoundtripDeliveryRuntimeResult:
    raw = verified.canonical_delivery
    object_key = _object_key(runtime._config.policy.mailbox, verified.delivery_sha256)
    if locator.object_key != object_key or locator.content_sha256 != verified.delivery_sha256:
        _fail("V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_LOCATOR_CONTENT_SUBSTITUTION")
    if locator.retained_until < verified.expires_at:
        _fail("V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_LOCATOR_RETENTION_INVALID")
    with _locked(runtime._config) as storage:
        state = _load_for_runtime(runtime, storage=storage, now=now)
        existing = _find_delivery(state, verified.delivery_sha256)
        if existing is not None:
            current_now = _host_now()
            if current_now < now:
                _fail("V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_CLOCK_ROLLBACK_DETECTED")
            state = _load_for_runtime(runtime, storage=storage, now=current_now)
            existing = _find_delivery(state, verified.delivery_sha256)
            if existing is None:
                _fail("V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_RESERVATION_LOST")
            current = _verify_delivery(existing.delivery, config=runtime._config, now=current_now)
            if current.delivery_sha256 != verified.delivery_sha256:
                _fail("V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_DELIVERY_REPLAY_OR_FORK")
            _require_exact_existing(existing, delivery=raw, object_key=object_key)
            if existing.status == _CONSUME_RESERVED:
                _fail("V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_CONSUME_INDETERMINATE")
            _require_exact_existing(
                existing,
                delivery=raw,
                object_key=object_key,
                object_version_id=locator.object_version_id,
                retained_until=locator.retained_until,
            )
            return _result(existing, idempotent=True)
        if len(state.entries) >= runtime._config.maximum_records:
            _fail("V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_RECORD_LIMIT")
        reserved = _reservation(
            state=state,
            config=runtime._config,
            delivery=raw,
            delivery_sha256=verified.delivery_sha256,
            now=now,
        )
        reserved_state = _append_state(state, reserved, floor=now)
        runtime._expected_head_sha256 = _write_state(
            storage,
            config=runtime._config,
            state=reserved_state,
        )
        completed_now = _host_now()
        current_state = _load_for_runtime(runtime, storage=storage, now=completed_now)
        current_reserved = _find_delivery(current_state, verified.delivery_sha256)
        if current_reserved is None or current_reserved.status != _CONSUME_RESERVED:
            _fail("V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_RESERVATION_LOST")
        _require_exact_existing(current_reserved, delivery=raw, object_key=object_key)
        current = _verify_delivery(raw, config=runtime._config, now=completed_now)
        if current.delivery_sha256 != verified.delivery_sha256:
            _fail("V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_DELIVERY_REPLAY_OR_FORK")
        completed = _completed_entry(
            current_reserved,
            status=_CONSUMED,
            object_version_id=locator.object_version_id,
            retained_until=locator.retained_until,
            completed_at=completed_now,
        )
        try:
            runtime._expected_head_sha256 = _write_state(
                storage,
                config=runtime._config,
                state=_replace_last(current_state, completed, floor=completed_now),
            )
        except Exception:
            runtime._expected_head_sha256 = None
            raise
        return _result(completed, idempotent=False)


def _consume(
    *,
    runtime_value: object,
    local_role: str,
    list_locators: Callable[[], tuple[PhysicalWalV2WitnessRoundtripDeliveryImmutableLocator, ...]],
    read_exact: Callable[..., PhysicalWalV2WitnessRoundtripDeliveryContent],
) -> tuple[PhysicalWalV2WitnessRoundtripDeliveryRuntimeResult, ...]:
    runtime = _runtime(runtime_value, local_role=local_role, direction=_CONSUME)
    now = _host_now()
    _check_consume_state(runtime, now=now)
    try:
        locators = list_locators()
    except PhysicalWalV2WitnessRoundtripDeliveryRuntimeError:
        raise
    except Exception as exc:
        raise PhysicalWalV2WitnessRoundtripDeliveryRuntimeError(
            "V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_INBOUND_SCAN_FAILED"
        ) from exc
    if type(locators) is not tuple or len(locators) > runtime._config.maximum_records:
        _fail("V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_INBOUND_SCAN_INVALID")
    normalized: list[PhysicalWalV2WitnessRoundtripDeliveryImmutableLocator] = []
    seen: dict[str, PhysicalWalV2WitnessRoundtripDeliveryImmutableLocator] = {}
    for value in locators:
        locator = _validate_locator(value, config=runtime._config, now=now)
        previous = seen.get(locator.object_key)
        if previous is not None:
            if previous != locator:
                _fail("V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_LOCATOR_FORK")
            continue
        seen[locator.object_key] = locator
        normalized.append(locator)
    results: list[PhysicalWalV2WitnessRoundtripDeliveryRuntimeResult] = []
    for locator in sorted(normalized, key=lambda item: (item.object_key, item.object_version_id)):
        try:
            content = read_exact(
                object_key=locator.object_key,
                object_version_id=locator.object_version_id,
            )
        except PhysicalWalV2WitnessRoundtripDeliveryRuntimeError:
            raise
        except Exception as exc:
            raise PhysicalWalV2WitnessRoundtripDeliveryRuntimeError(
                "V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_INBOUND_READ_FAILED"
            ) from exc
        # A scanner/read callback may have outlived the scan timestamp.  Do
        # not reserve durable consumption until its locator and signed bytes
        # pass under a fresh trusted clock.
        current_now = _host_now()
        if current_now < now:
            _fail("V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_CLOCK_ROLLBACK_DETECTED")
        current_locator = _validate_locator(locator, config=runtime._config, now=current_now)
        checked = _validate_content(content, locator=current_locator)
        verified = _verify_delivery(
            checked.canonical_delivery,
            config=runtime._config,
            now=current_now,
        )
        if verified.delivery_sha256 != locator.content_sha256:
            _fail("V2_WITNESS_ROUNDTRIP_DELIVERY_RUNTIME_LOCATOR_CONTENT_SUBSTITUTION")
        results.append(
            _consume_verified(
                runtime=runtime,
                verified=verified,
                locator=current_locator,
                now=current_now,
            )
        )
    return tuple(results)


def consume_physical_wal_v2_witness_fi_to_witness_delivery(
    runtime: PhysicalWalV2WitnessRoundtripDeliveryRuntime,
    *,
    scanner: PhysicalWalV2WitnessRoundtripFiToWitnessInboundScanner,
) -> tuple[PhysicalWalV2WitnessRoundtripDeliveryRuntimeResult, ...]:
    return _consume(
        runtime_value=runtime,
        local_role="witness-fi-ingress",
        list_locators=scanner.list_fi_to_witness_delivery_locators,
        read_exact=scanner.read_fi_to_witness_delivery_exact,
    )


def consume_physical_wal_v2_witness_witness_to_ir_delivery(
    runtime: PhysicalWalV2WitnessRoundtripDeliveryRuntime,
    *,
    scanner: PhysicalWalV2WitnessRoundtripWitnessToIrInboundScanner,
) -> tuple[PhysicalWalV2WitnessRoundtripDeliveryRuntimeResult, ...]:
    return _consume(
        runtime_value=runtime,
        local_role="ir-standby-ack-inbox",
        list_locators=scanner.list_witness_to_ir_delivery_locators,
        read_exact=scanner.read_witness_to_ir_delivery_exact,
    )


def consume_physical_wal_v2_witness_ir_to_witness_delivery(
    runtime: PhysicalWalV2WitnessRoundtripDeliveryRuntime,
    *,
    scanner: PhysicalWalV2WitnessRoundtripIrToWitnessInboundScanner,
) -> tuple[PhysicalWalV2WitnessRoundtripDeliveryRuntimeResult, ...]:
    return _consume(
        runtime_value=runtime,
        local_role="witness-ir-ingress",
        list_locators=scanner.list_ir_to_witness_delivery_locators,
        read_exact=scanner.read_ir_to_witness_delivery_exact,
    )


def consume_physical_wal_v2_witness_witness_to_fi_delivery(
    runtime: PhysicalWalV2WitnessRoundtripDeliveryRuntime,
    *,
    scanner: PhysicalWalV2WitnessRoundtripWitnessToFiInboundScanner,
) -> tuple[PhysicalWalV2WitnessRoundtripDeliveryRuntimeResult, ...]:
    return _consume(
        runtime_value=runtime,
        local_role="fi-writer-ack-inbox",
        list_locators=scanner.list_witness_to_fi_delivery_locators,
        read_exact=scanner.read_witness_to_fi_delivery_exact,
    )
