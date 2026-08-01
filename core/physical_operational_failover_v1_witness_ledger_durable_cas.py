"""Root-local durable append/CAS store for the V1 Witness term ledger.

This module is intentionally a persistence foundation, not an operational
failover runtime.  It implements the narrow ``read_current`` /
``append_compare_and_swap`` protocol consumed by
``physical_operational_failover_v1_witness_ledger``.  It has no provider,
network, database, SSH, traffic, writer-start, or promotion code.

The state root and namespace are fixed by this module; callers cannot choose a
path.  Every immutable ledger record is create-only and fsync'd, while the
current pointer is atomically replaced only after its record is durable.  A
separate injected, root-owned monotonic checkpoint is required to detect an
otherwise undetectable privileged whole-tree rollback.  Missing checkpoints,
symlinks, incomplete chains, temporary files, and unknown residue all fail
closed.

The returned snapshot is only durable evidence for the separate Witness ledger
state machine.  It neither authorizes a writer, a promotion, a database change,
or traffic.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import base64
import binascii
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
from typing import Iterator, Protocol

from core import physical_operational_failover_v1 as wire
from core import physical_operational_failover_v1_witness_ledger as ledger


__all__ = (
    "FIXED_PHYSICAL_OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_DURABLE_CAS_STATE_ROOT",
    "PHYSICAL_OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_DURABLE_CAS_DEFAULT_ENABLED",
    "PHYSICAL_OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_DURABLE_CAS_SCHEMA",
    "PhysicalOperationalFailoverV1WitnessLedgerDurableCasCheckpoint",
    "PhysicalOperationalFailoverV1WitnessLedgerDurableCasStore",
    "PhysicalOperationalFailoverV1WitnessLedgerDurableCasStoreError",
    "RootOwnedPhysicalOperationalFailoverV1WitnessLedgerDurableCasStoreConfig",
)


PHYSICAL_OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_DURABLE_CAS_SCHEMA = (
    "gold-trade-physical-operational-failover-v1-witness-term-ledger-durable-cas-v1"
)
PHYSICAL_OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_DURABLE_CAS_DEFAULT_ENABLED = False

# Deployment provisions this exact directory as root:root mode 0700.  No
# configuration field may override it; reassignment in tests is the only local
# seam and is not an application-facing API.
FIXED_PHYSICAL_OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_DURABLE_CAS_STATE_ROOT = Path(
    "/var/lib/trading-bot/physical-operational-failover-v1-witness-term-ledger"
)

_VERSION = 1
_MODE = "root-owned-v1-witness-term-ledger-durable-cas-v1"
_LOCK_FILENAME = "ledger.lock"
_BINDING_FILENAME = "binding.json"
_CURRENT_FILENAME = "current.json"
_ENTRIES_DIRECTORY = "entries"
_MAX_RECORD_BYTES = 2 * 1024 * 1024
_MAX_RECORDS = 8192
_ZERO_SHA256 = "0" * 64
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_ENTRY_NAME_RE = re.compile(r"^([0-9]{20})-([0-9a-f]{64})\.json$", re.ASCII)
_TEMP_NAME_RE = re.compile(r"^\.[A-Za-z0-9._-]+\.tmp$", re.ASCII)
_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", re.ASCII)


class PhysicalOperationalFailoverV1WitnessLedgerDurableCasStoreError(RuntimeError):
    """The local root-owned durable CAS boundary rejected unsafe state."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _fail(code: str) -> None:
    raise PhysicalOperationalFailoverV1WitnessLedgerDurableCasStoreError(code)


class PhysicalOperationalFailoverV1WitnessLedgerDurableCasCheckpoint(Protocol):
    """External root-owned monotonic checkpoint for the complete ledger head.

    The implementation lives outside this mutable state directory.  It must
    durably accept an exact replay of its current tuple or the direct next
    tuple, and reject a lower sequence or a same-sequence divergent tuple.
    This module deliberately provides no fallback checkpoint and never treats
    Object Storage, a peer, or a caller-supplied value as such an authority.
    """

    def attest_v1_witness_ledger_state(
        self,
        *,
        binding_sha256: str,
        ledger_schema: str,
        initial_fi_term_sha256: str,
        sequence: int,
        previous_head_sha256: str,
        head_sha256: str,
        record_sha256: str,
    ) -> None: ...


@dataclass(frozen=True)
class RootOwnedPhysicalOperationalFailoverV1WitnessLedgerDurableCasStoreConfig:
    """Pinned, default-off configuration with no caller-selected path."""

    schema: str = PHYSICAL_OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_DURABLE_CAS_SCHEMA
    enabled: bool = PHYSICAL_OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_DURABLE_CAS_DEFAULT_ENABLED
    ledger_config: ledger.RootOwnedPhysicalOperationalFailoverV1WitnessLedgerConfig | None = None
    require_durable_rollback_checkpoint: bool = True


@dataclass(frozen=True)
class _Facts:
    ledger_schema: str
    initial_fi_term_sha256: str
    ledger_config_identity_payload: dict[str, object]
    ledger_config_identity_sha256: str
    binding_payload: bytes
    binding_sha256: str


@dataclass(frozen=True)
class _Record:
    snapshot: ledger.PhysicalOperationalFailoverV1WitnessLedgerSnapshot
    record_sha256: str


@dataclass(frozen=True)
class _Storage:
    root_fd: int
    entries_fd: int


def _canonical(value: object, *, code: str) -> bytes:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii") + b"\n"
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise PhysicalOperationalFailoverV1WitnessLedgerDurableCasStoreError(code) from exc


def _strict_object(items: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in items:
        if key in result:
            _fail("OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_DURABLE_CAS_DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def _decode_canonical(value: object, *, code: str) -> dict[str, object]:
    if type(value) is not bytes or not value or len(value) > _MAX_RECORD_BYTES:
        _fail(code)
    try:
        decoded = json.loads(
            value.decode("ascii"),
            object_pairs_hook=_strict_object,
            parse_constant=lambda _value: _fail(code),
        )
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        PhysicalOperationalFailoverV1WitnessLedgerDurableCasStoreError,
    ):
        _fail(code)
    if type(decoded) is not dict or _canonical(decoded, code=code) != value:
        _fail(code)
    return decoded


def _sha256(value: object, *, code: str, permit_zero: bool = False) -> str:
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None:
        _fail(code)
    if not permit_zero and value == _ZERO_SHA256:
        _fail(code)
    return value


def _positive(value: object, *, code: str, permit_zero: bool = False) -> int:
    if (
        type(value) is not int
        or isinstance(value, bool)
        or value < (0 if permit_zero else 1)
        or value > 2**63 - 1
    ):
        _fail(code)
    return value


def _exact_mapping(value: object, *, fields: frozenset[str], code: str) -> dict[str, object]:
    if type(value) is not dict or set(value) != fields:
        _fail(code)
    return value


def _render_time(value: object, *, code: str) -> datetime:
    if type(value) is not str or _TIMESTAMP_RE.fullmatch(value) is None:
        _fail(code)
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PhysicalOperationalFailoverV1WitnessLedgerDurableCasStoreError(code) from exc
    if result.tzinfo is None or result.utcoffset() is None:
        _fail(code)
    result = result.astimezone(timezone.utc)
    if result.microsecond or result.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        _fail(code)
    return result


def _call_ledger(function: object, *args: object, code: str, **kwargs: object) -> object:
    if not callable(function):
        _fail(code)
    try:
        return function(*args, **kwargs)
    except (ledger.PhysicalOperationalFailoverV1WitnessLedgerError, wire.PhysicalOperationalFailoverV1Error) as exc:
        raise PhysicalOperationalFailoverV1WitnessLedgerDurableCasStoreError(code) from exc
    except (TypeError, ValueError, UnicodeError) as exc:
        raise PhysicalOperationalFailoverV1WitnessLedgerDurableCasStoreError(code) from exc


def _ledger_config_identity(config: object) -> tuple[dict[str, object], str]:
    code = "OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_DURABLE_CAS_LEDGER_CONFIG_INVALID"
    if type(config) is not ledger.RootOwnedPhysicalOperationalFailoverV1WitnessLedgerConfig:
        _fail(code)
    assert isinstance(config, ledger.RootOwnedPhysicalOperationalFailoverV1WitnessLedgerConfig)
    _call_ledger(ledger._facts, config, code=code)
    verification = config.verification_config
    if type(verification) is not wire.PhysicalOperationalFailoverV1VerificationConfig:
        _fail(code)
    try:
        _pins, pins_mapping = wire._pins_mapping(verification.pins, code=code)
        initial_term, initial_term_mapping = wire._term_mapping(config.initial_fi_term, code=code)
        initial_sha = ledger._term_sha256(initial_term, code=code)
    except (ledger.PhysicalOperationalFailoverV1WitnessLedgerError, wire.PhysicalOperationalFailoverV1Error) as exc:
        raise PhysicalOperationalFailoverV1WitnessLedgerDurableCasStoreError(code) from exc
    if initial_term.holder_site != "webapp_fi":
        _fail(code)
    identity = {
        "ledger_schema": config.schema,
        "verification": {
            "schema": wire.PHYSICAL_OPERATIONAL_FAILOVER_V1_SCHEMA,
            "enabled": True,
            "maximum_evidence_age_seconds": verification.maximum_evidence_age_seconds,
            "pins": pins_mapping,
            "fi_self_fence_signer_public_key_base64": base64.b64encode(
                verification.fi_self_fence_signer_public_key
            ).decode("ascii"),
            "ir_promotion_request_signer_public_key_base64": base64.b64encode(
                verification.ir_promotion_request_signer_public_key
            ).decode("ascii"),
            "witness_term_signer_public_key_base64": base64.b64encode(
                verification.witness_term_signer_public_key
            ).decode("ascii"),
            "ir_promotion_completion_signer_public_key_base64": base64.b64encode(
                verification.ir_promotion_completion_signer_public_key
            ).decode("ascii"),
        },
        "initial_fi_term": initial_term_mapping,
        "initial_fi_term_sha256": initial_sha,
    }
    return identity, initial_sha


def _facts(config: object) -> _Facts:
    code = "OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_DURABLE_CAS_CONFIG_INVALID"
    if type(config) is not RootOwnedPhysicalOperationalFailoverV1WitnessLedgerDurableCasStoreConfig:
        _fail(code)
    assert isinstance(config, RootOwnedPhysicalOperationalFailoverV1WitnessLedgerDurableCasStoreConfig)
    if config.schema != PHYSICAL_OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_DURABLE_CAS_SCHEMA:
        _fail(code)
    if config.enabled is not True:
        _fail("OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_DURABLE_CAS_DISABLED")
    if config.require_durable_rollback_checkpoint is not True:
        _fail("OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_DURABLE_CAS_CHECKPOINT_REQUIRED")
    identity, initial_sha = _ledger_config_identity(config.ledger_config)
    identity_sha = hashlib.sha256(_canonical(identity, code=code)).hexdigest()
    body = {
        "schema": PHYSICAL_OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_DURABLE_CAS_SCHEMA,
        "version": _VERSION,
        "mode": _MODE,
        "ledger_schema": ledger.PHYSICAL_OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_SCHEMA,
        "ledger_config_identity": identity,
        "ledger_config_identity_sha256": identity_sha,
        "writer_authorized": False,
        "promotion_authorized": False,
        "traffic_authorized": False,
    }
    binding_payload = _canonical(body, code=code)
    return _Facts(
        ledger_schema=ledger.PHYSICAL_OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_SCHEMA,
        initial_fi_term_sha256=initial_sha,
        ledger_config_identity_payload=identity,
        ledger_config_identity_sha256=identity_sha,
        binding_payload=binding_payload,
        binding_sha256=hashlib.sha256(binding_payload).hexdigest(),
    )


def _require_root() -> None:
    try:
        if os.geteuid() != 0:
            _fail("OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_DURABLE_CAS_ROOT_RUNTIME_REQUIRED")
    except OSError as exc:
        raise PhysicalOperationalFailoverV1WitnessLedgerDurableCasStoreError(
            "OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_DURABLE_CAS_ROOT_RUNTIME_REQUIRED"
        ) from exc


def _require_fd_platform() -> None:
    if not all(hasattr(os, name) for name in ("O_NOFOLLOW", "O_DIRECTORY", "fdatasync")):
        _fail("OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_DURABLE_CAS_PLATFORM_UNSUPPORTED")


def _metadata_tuple(metadata: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_uid,
        metadata.st_nlink,
        metadata.st_size,
    )


def _validate_ancestors(path: Path) -> None:
    code = "OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_DURABLE_CAS_STATE_ROOT_UNSAFE"
    if not isinstance(path, Path) or not path.is_absolute() or ".." in path.parts:
        _fail(code)
    _require_fd_platform()
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    descriptor = -1
    try:
        descriptor = os.open("/", flags)
        for component in path.parts[1:-1]:
            next_descriptor = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISDIR(metadata.st_mode)
                or metadata.st_uid != 0
                or stat.S_IMODE(metadata.st_mode) & 0o022
            ):
                _fail(code)
    except PhysicalOperationalFailoverV1WitnessLedgerDurableCasStoreError:
        raise
    except OSError as exc:
        raise PhysicalOperationalFailoverV1WitnessLedgerDurableCasStoreError(code) from exc
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass


def _open_secure_root() -> int:
    root = FIXED_PHYSICAL_OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_DURABLE_CAS_STATE_ROOT
    code = "OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_DURABLE_CAS_STATE_ROOT_UNSAFE"
    _validate_ancestors(root)
    descriptor = -1
    try:
        before = os.lstat(root)
        if (
            stat.S_ISLNK(before.st_mode)
            or not stat.S_ISDIR(before.st_mode)
            or before.st_uid != 0
            or stat.S_IMODE(before.st_mode) != 0o700
        ):
            _fail(code)
        descriptor = os.open(
            root,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
        )
        opened = os.fstat(descriptor)
        after = os.lstat(root)
        if (
            _metadata_tuple(before) != _metadata_tuple(opened)
            or _metadata_tuple(after) != _metadata_tuple(before)
        ):
            _fail(code)
        return descriptor
    except PhysicalOperationalFailoverV1WitnessLedgerDurableCasStoreError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError as exc:
        if descriptor >= 0:
            os.close(descriptor)
        raise PhysicalOperationalFailoverV1WitnessLedgerDurableCasStoreError(code) from exc


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
        raise PhysicalOperationalFailoverV1WitnessLedgerDurableCasStoreError(code) from exc
    if (
        stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != 0
        or (not stat.S_ISDIR(metadata.st_mode) if directory else not stat.S_ISREG(metadata.st_mode))
        or (directory and metadata.st_nlink < 2)
        or (not directory and metadata.st_nlink != 1)
        or stat.S_IMODE(metadata.st_mode) != (0o700 if directory else 0o600)
    ):
        _fail(code)
    return metadata


def _listdir(parent_fd: int, *, code: str) -> list[str]:
    try:
        names = os.listdir(parent_fd)
    except OSError as exc:
        raise PhysicalOperationalFailoverV1WitnessLedgerDurableCasStoreError(code) from exc
    if any(type(name) is not str or not name or "/" in name or "\\" in name for name in names):
        _fail(code)
    return names


def _ensure_entries_directory(root_fd: int) -> int:
    code = "OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_DURABLE_CAS_ENTRIES_UNSAFE"
    descriptor = -1
    try:
        created = False
        try:
            os.mkdir(_ENTRIES_DIRECTORY, 0o700, dir_fd=root_fd)
            created = True
        except FileExistsError:
            pass
        descriptor = os.open(
            _ENTRIES_DIRECTORY,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
            dir_fd=root_fd,
        )
        if created:
            os.fchmod(descriptor, 0o700)
            os.fsync(descriptor)
            os.fsync(root_fd)
        before = _safe_child_metadata(root_fd, _ENTRIES_DIRECTORY, directory=True, code=code)
        opened = os.fstat(descriptor)
        after = _safe_child_metadata(root_fd, _ENTRIES_DIRECTORY, directory=True, code=code)
        if _metadata_tuple(before) != _metadata_tuple(opened) or _metadata_tuple(after) != _metadata_tuple(before):
            _fail(code)
        return descriptor
    except PhysicalOperationalFailoverV1WitnessLedgerDurableCasStoreError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError as exc:
        if descriptor >= 0:
            os.close(descriptor)
        raise PhysicalOperationalFailoverV1WitnessLedgerDurableCasStoreError(code) from exc


def _open_lock(root_fd: int) -> int:
    code = "OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_DURABLE_CAS_LOCK_UNSAFE"
    descriptor = -1
    try:
        created = False
        try:
            descriptor = os.open(
                _LOCK_FILENAME,
                os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
                0o600,
                dir_fd=root_fd,
            )
            created = True
        except FileExistsError:
            descriptor = os.open(
                _LOCK_FILENAME,
                os.O_RDWR | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
                dir_fd=root_fd,
            )
        if created:
            os.fchmod(descriptor, 0o600)
            os.fdatasync(descriptor)
            os.fsync(root_fd)
        before = _safe_child_metadata(root_fd, _LOCK_FILENAME, directory=False, code=code)
        opened = os.fstat(descriptor)
        after = _safe_child_metadata(root_fd, _LOCK_FILENAME, directory=False, code=code)
        if _metadata_tuple(before) != _metadata_tuple(opened) or _metadata_tuple(after) != _metadata_tuple(before):
            _fail(code)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        return descriptor
    except PhysicalOperationalFailoverV1WitnessLedgerDurableCasStoreError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError as exc:
        if descriptor >= 0:
            os.close(descriptor)
        raise PhysicalOperationalFailoverV1WitnessLedgerDurableCasStoreError(
            "OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_DURABLE_CAS_LOCK_OPEN_FAILED"
        ) from exc


def _validate_root_entries(root_fd: int) -> None:
    known = {_LOCK_FILENAME, _BINDING_FILENAME, _CURRENT_FILENAME, _ENTRIES_DIRECTORY}
    for name in _listdir(root_fd, code="OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_DURABLE_CAS_ROOT_RESIDUE"):
        if name not in known:
            _fail(
                "OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_DURABLE_CAS_TEMP_RESIDUE"
                if _TEMP_NAME_RE.fullmatch(name)
                else "OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_DURABLE_CAS_ROOT_RESIDUE"
            )
        _safe_child_metadata(
            root_fd,
            name,
            directory=name == _ENTRIES_DIRECTORY,
            code="OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_DURABLE_CAS_ROOT_CHILD_UNSAFE",
        )


@contextmanager
def _locked_storage() -> Iterator[_Storage]:
    root_fd = -1
    entries_fd = -1
    lock_fd = -1
    try:
        root_fd = _open_secure_root()
        entries_fd = _ensure_entries_directory(root_fd)
        lock_fd = _open_lock(root_fd)
        _validate_root_entries(root_fd)
        yield _Storage(root_fd=root_fd, entries_fd=entries_fd)
    finally:
        try:
            if lock_fd >= 0:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
        finally:
            for descriptor in (lock_fd, entries_fd, root_fd):
                if descriptor >= 0:
                    try:
                        os.close(descriptor)
                    except OSError:
                        pass


def _read_file_at(parent_fd: int, name: str, *, code: str) -> bytes:
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
            or not 1 <= metadata.st_size <= _MAX_RECORD_BYTES
        ):
            _fail(code)
        remaining = metadata.st_size
        chunks = bytearray()
        while remaining:
            chunk = os.read(descriptor, remaining)
            if not chunk:
                _fail(code)
            chunks.extend(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            _fail(code)
        return bytes(chunks)
    except PhysicalOperationalFailoverV1WitnessLedgerDurableCasStoreError:
        raise
    except OSError as exc:
        raise PhysicalOperationalFailoverV1WitnessLedgerDurableCasStoreError(code) from exc
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
            raise PhysicalOperationalFailoverV1WitnessLedgerDurableCasStoreError(code) from exc
        if type(written) is not int or written <= 0:
            _fail(code)
        view = view[written:]


def _write_create_only_at(
    parent_fd: int,
    name: str,
    payload: bytes,
    *,
    code: str,
    allow_exact_existing: bool,
) -> None:
    if (
        type(name) is not str
        or not name
        or "/" in name
        or "\\" in name
        or name in {".", ".."}
        or type(payload) is not bytes
        or not 1 <= len(payload) <= _MAX_RECORD_BYTES
    ):
        _fail(code)
    descriptor = -1
    try:
        try:
            descriptor = os.open(
                name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
                0o600,
                dir_fd=parent_fd,
            )
        except FileExistsError:
            if allow_exact_existing and _read_file_at(parent_fd, name, code=code) == payload:
                return
            _fail(code)
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != 0
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_size != 0
        ):
            _fail("OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_DURABLE_CAS_STATE_UNSAFE")
        _write_all(descriptor, payload, code=code)
        os.fdatasync(descriptor)
    except PhysicalOperationalFailoverV1WitnessLedgerDurableCasStoreError:
        raise
    except OSError as exc:
        raise PhysicalOperationalFailoverV1WitnessLedgerDurableCasStoreError(code) from exc
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass
    try:
        os.fsync(parent_fd)
    except OSError as exc:
        raise PhysicalOperationalFailoverV1WitnessLedgerDurableCasStoreError(
            "OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_DURABLE_CAS_DIRECTORY_FSYNC_FAILED"
        ) from exc


def _ensure_absent_or_safe_current(root_fd: int) -> None:
    try:
        os.stat(_CURRENT_FILENAME, dir_fd=root_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    except OSError as exc:
        raise PhysicalOperationalFailoverV1WitnessLedgerDurableCasStoreError(
            "OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_DURABLE_CAS_CURRENT_UNSAFE"
        ) from exc
    _safe_child_metadata(
        root_fd,
        _CURRENT_FILENAME,
        directory=False,
        code="OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_DURABLE_CAS_CURRENT_UNSAFE",
    )


def _write_current_atomic(root_fd: int, payload: bytes) -> None:
    code = "OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_DURABLE_CAS_CURRENT_WRITE_FAILED"
    if type(payload) is not bytes or not 1 <= len(payload) <= _MAX_RECORD_BYTES:
        _fail(code)
    temporary = ".current-" + secrets.token_bytes(32).hex() + ".tmp"
    descriptor = -1
    try:
        _ensure_absent_or_safe_current(root_fd)
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
            0o600,
            dir_fd=root_fd,
        )
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != 0
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_size != 0
        ):
            _fail("OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_DURABLE_CAS_CURRENT_UNSAFE")
        _write_all(descriptor, payload, code=code)
        os.fdatasync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.rename(temporary, _CURRENT_FILENAME, src_dir_fd=root_fd, dst_dir_fd=root_fd)
        _safe_child_metadata(
            root_fd,
            _CURRENT_FILENAME,
            directory=False,
            code="OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_DURABLE_CAS_CURRENT_UNSAFE",
        )
        os.fsync(root_fd)
        if _read_file_at(root_fd, _CURRENT_FILENAME, code=code) != payload:
            _fail(code)
    except PhysicalOperationalFailoverV1WitnessLedgerDurableCasStoreError:
        raise
    except OSError as exc:
        raise PhysicalOperationalFailoverV1WitnessLedgerDurableCasStoreError(code) from exc
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass


def _state_mapping(value: object) -> dict[str, object]:
    code = "OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_DURABLE_CAS_STATE_INVALID"
    try:
        result = ledger._state_mapping(value, code=code)
    except ledger.PhysicalOperationalFailoverV1WitnessLedgerError as exc:
        raise PhysicalOperationalFailoverV1WitnessLedgerDurableCasStoreError(code) from exc
    if type(result) is not dict:
        _fail(code)
    return result


def _entry_mapping(value: object) -> dict[str, object]:
    code = "OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_DURABLE_CAS_ENTRY_INVALID"
    try:
        result = ledger._entry_mapping(value, code=code)
    except ledger.PhysicalOperationalFailoverV1WitnessLedgerError as exc:
        raise PhysicalOperationalFailoverV1WitnessLedgerDurableCasStoreError(code) from exc
    if type(result) is not dict:
        _fail(code)
    return result


_TERM_FIELDS = frozenset(
    {
        "holder_site",
        "writer_epoch",
        "writer_lease_id",
        "witness_transition_id",
        "witnessed_term_proof_sha256",
        "issued_at",
        "expires_at",
    }
)
_RESERVATION_FIELDS = frozenset(
    {
        "grant_id",
        "grant_nonce",
        "grant_replay_key_sha256",
        "issued_at",
        "expires_at",
        "successor_term",
        "activation_route_artifact_sha256",
        "activation_receiver_permit_sha256",
    }
)
_STATE_FIELDS = frozenset(
    {
        "sequence",
        "phase",
        "clock_floor",
        "active_term",
        "active_term_sha256",
        "predecessor_term",
        "predecessor_term_sha256",
        "predecessor_termination_reason",
        "fi_self_fence_receipt_sha256",
        "request_sha256",
        "request_id",
        "request_nonce",
        "canonical_request_base64",
        "reservation",
        "issued_grant_sha256",
        "issued_grant_id",
        "issued_grant_nonce",
        "completion_sha256",
        "consumed_replay_keys",
        "consumed_nonces",
    }
)
_ENTRY_FIELDS = frozenset(
    {
        "sequence",
        "previous_head_sha256",
        "observed_at",
        "event",
        "state_sha256",
        "entry_sha256",
    }
)


def _term_from_mapping(value: object, *, code: str) -> wire.PhysicalOperationalFailoverV1Term:
    fields = _exact_mapping(value, fields=_TERM_FIELDS, code=code)
    term = wire.PhysicalOperationalFailoverV1Term(
        holder_site=fields["holder_site"],
        writer_epoch=fields["writer_epoch"],
        writer_lease_id=fields["writer_lease_id"],
        witness_transition_id=fields["witness_transition_id"],
        witnessed_term_proof_sha256=fields["witnessed_term_proof_sha256"],
        issued_at=_render_time(fields["issued_at"], code=code),
        expires_at=_render_time(fields["expires_at"], code=code),
    )
    try:
        normalized, mapping = ledger._term_mapping(term, code=code)
    except ledger.PhysicalOperationalFailoverV1WitnessLedgerError as exc:
        raise PhysicalOperationalFailoverV1WitnessLedgerDurableCasStoreError(code) from exc
    if mapping != fields:
        _fail(code)
    return normalized


def _reservation_from_mapping(
    value: object,
    *,
    code: str,
) -> ledger.PhysicalOperationalFailoverV1WitnessGrantReservation:
    fields = _exact_mapping(value, fields=_RESERVATION_FIELDS, code=code)
    reservation = ledger.PhysicalOperationalFailoverV1WitnessGrantReservation(
        grant_id=fields["grant_id"],
        grant_nonce=fields["grant_nonce"],
        grant_replay_key_sha256=fields["grant_replay_key_sha256"],
        issued_at=_render_time(fields["issued_at"], code=code),
        expires_at=_render_time(fields["expires_at"], code=code),
        successor_term=_term_from_mapping(fields["successor_term"], code=code),
        activation_route_artifact_sha256=fields["activation_route_artifact_sha256"],
        activation_receiver_permit_sha256=fields["activation_receiver_permit_sha256"],
    )
    try:
        normalized, mapping = ledger._reservation_mapping(reservation, code=code)
    except ledger.PhysicalOperationalFailoverV1WitnessLedgerError as exc:
        raise PhysicalOperationalFailoverV1WitnessLedgerDurableCasStoreError(code) from exc
    if mapping != fields:
        _fail(code)
    return normalized


def _optional_string(value: object, *, code: str) -> str | None:
    if value is not None and type(value) is not str:
        _fail(code)
    return value


def _state_from_mapping(value: object) -> ledger.PhysicalOperationalFailoverV1WitnessLedgerState:
    code = "OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_DURABLE_CAS_STATE_INVALID"
    fields = _exact_mapping(value, fields=_STATE_FIELDS, code=code)
    canonical_request: bytes | None
    encoded = fields["canonical_request_base64"]
    if encoded is None:
        canonical_request = None
    elif type(encoded) is str:
        try:
            canonical_request = base64.b64decode(encoded.encode("ascii"), validate=True)
        except (UnicodeEncodeError, binascii.Error) as exc:
            raise PhysicalOperationalFailoverV1WitnessLedgerDurableCasStoreError(code) from exc
        if base64.b64encode(canonical_request).decode("ascii") != encoded:
            _fail(code)
    else:
        _fail(code)
    replay = fields["consumed_replay_keys"]
    nonces = fields["consumed_nonces"]
    if type(replay) is not list or type(nonces) is not list or any(type(item) is not str for item in replay + nonces):
        _fail(code)
    state = ledger.PhysicalOperationalFailoverV1WitnessLedgerState(
        sequence=fields["sequence"],
        phase=fields["phase"],
        clock_floor=_render_time(fields["clock_floor"], code=code),
        active_term=None if fields["active_term"] is None else _term_from_mapping(fields["active_term"], code=code),
        active_term_sha256=_optional_string(fields["active_term_sha256"], code=code),
        predecessor_term=None
        if fields["predecessor_term"] is None
        else _term_from_mapping(fields["predecessor_term"], code=code),
        predecessor_term_sha256=_optional_string(fields["predecessor_term_sha256"], code=code),
        predecessor_termination_reason=_optional_string(fields["predecessor_termination_reason"], code=code),
        fi_self_fence_receipt_sha256=_optional_string(fields["fi_self_fence_receipt_sha256"], code=code),
        request_sha256=_optional_string(fields["request_sha256"], code=code),
        request_id=_optional_string(fields["request_id"], code=code),
        request_nonce=_optional_string(fields["request_nonce"], code=code),
        canonical_request=canonical_request,
        reservation=None if fields["reservation"] is None else _reservation_from_mapping(fields["reservation"], code=code),
        issued_grant_sha256=_optional_string(fields["issued_grant_sha256"], code=code),
        issued_grant_id=_optional_string(fields["issued_grant_id"], code=code),
        issued_grant_nonce=_optional_string(fields["issued_grant_nonce"], code=code),
        completion_sha256=_optional_string(fields["completion_sha256"], code=code),
        consumed_replay_keys=tuple(replay),
        consumed_nonces=tuple(nonces),
    )
    if _state_mapping(state) != fields:
        _fail(code)
    return state


def _entry_from_mapping(value: object) -> ledger.PhysicalOperationalFailoverV1WitnessLedgerEntry:
    code = "OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_DURABLE_CAS_ENTRY_INVALID"
    fields = _exact_mapping(value, fields=_ENTRY_FIELDS, code=code)
    entry = ledger.PhysicalOperationalFailoverV1WitnessLedgerEntry(
        sequence=fields["sequence"],
        previous_head_sha256=fields["previous_head_sha256"],
        observed_at=_render_time(fields["observed_at"], code=code),
        event=fields["event"],
        state_sha256=fields["state_sha256"],
        entry_sha256=fields["entry_sha256"],
    )
    normalized = _entry_mapping(entry)
    try:
        expected_sha = ledger._entry_sha256(
            sequence=entry.sequence,
            previous_head_sha256=entry.previous_head_sha256,
            observed_at=entry.observed_at,
            event=entry.event,
            state_sha256=entry.state_sha256,
        )
    except ledger.PhysicalOperationalFailoverV1WitnessLedgerError as exc:
        raise PhysicalOperationalFailoverV1WitnessLedgerDurableCasStoreError(code) from exc
    if {**normalized, "entry_sha256": expected_sha} != fields or entry.entry_sha256 != expected_sha:
        _fail(code)
    return entry


def _snapshot(
    entry: ledger.PhysicalOperationalFailoverV1WitnessLedgerEntry,
    state: ledger.PhysicalOperationalFailoverV1WitnessLedgerState,
) -> ledger.PhysicalOperationalFailoverV1WitnessLedgerSnapshot:
    code = "OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_DURABLE_CAS_SNAPSHOT_INVALID"
    snapshot = ledger.PhysicalOperationalFailoverV1WitnessLedgerSnapshot(
        version=entry.sequence,
        head_sha256=entry.entry_sha256,
        entry=entry,
        state=state,
    )
    try:
        checked = ledger._snapshot(snapshot)
    except ledger.PhysicalOperationalFailoverV1WitnessLedgerError as exc:
        raise PhysicalOperationalFailoverV1WitnessLedgerDurableCasStoreError(code) from exc
    if checked != snapshot:
        _fail(code)
    return snapshot


def _record_payload(
    *,
    facts: _Facts,
    entry: ledger.PhysicalOperationalFailoverV1WitnessLedgerEntry,
    state: ledger.PhysicalOperationalFailoverV1WitnessLedgerState,
) -> tuple[bytes, str]:
    body = {
        "schema": PHYSICAL_OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_DURABLE_CAS_SCHEMA,
        "version": _VERSION,
        "mode": _MODE,
        "binding_sha256": facts.binding_sha256,
        "ledger_schema": facts.ledger_schema,
        "ledger_config_identity_sha256": facts.ledger_config_identity_sha256,
        "sequence": entry.sequence,
        "previous_head_sha256": entry.previous_head_sha256,
        "head_sha256": entry.entry_sha256,
        "entry": {**_entry_mapping(entry), "entry_sha256": entry.entry_sha256},
        "state": _state_mapping(state),
    }
    digest = hashlib.sha256(
        _canonical(body, code="OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_DURABLE_CAS_RECORD_INVALID")
    ).hexdigest()
    return (
        _canonical(
            {**body, "record_sha256": digest},
            code="OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_DURABLE_CAS_RECORD_INVALID",
        ),
        digest,
    )


_RECORD_FIELDS = frozenset(
    {
        "schema",
        "version",
        "mode",
        "binding_sha256",
        "ledger_schema",
        "ledger_config_identity_sha256",
        "sequence",
        "previous_head_sha256",
        "head_sha256",
        "entry",
        "state",
        "record_sha256",
    }
)


def _record_from_payload(
    payload: bytes,
    *,
    facts: _Facts,
    expected_sequence: int,
    expected_previous_head_sha256: str,
) -> _Record:
    code = "OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_DURABLE_CAS_RECORD_INVALID"
    decoded = _decode_canonical(payload, code=code)
    fields = _exact_mapping(decoded, fields=_RECORD_FIELDS, code=code)
    entry = _entry_from_mapping(fields["entry"])
    state = _state_from_mapping(fields["state"])
    sequence = _positive(fields["sequence"], code=code)
    previous = _sha256(fields["previous_head_sha256"], code=code, permit_zero=True)
    head = _sha256(fields["head_sha256"], code=code)
    record_sha = _sha256(fields["record_sha256"], code=code)
    if (
        fields["schema"] != PHYSICAL_OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_DURABLE_CAS_SCHEMA
        or fields["version"] != _VERSION
        or fields["mode"] != _MODE
        or fields["binding_sha256"] != facts.binding_sha256
        or fields["ledger_schema"] != facts.ledger_schema
        or fields["ledger_config_identity_sha256"] != facts.ledger_config_identity_sha256
        or sequence != expected_sequence
        or previous != expected_previous_head_sha256
        or entry.sequence != sequence
        or entry.previous_head_sha256 != previous
        or entry.entry_sha256 != head
        or state.sequence != sequence
    ):
        _fail(code)
    snapshot = _snapshot(entry, state)
    expected_payload, expected_sha = _record_payload(facts=facts, entry=entry, state=state)
    if payload != expected_payload or record_sha != expected_sha:
        _fail(code)
    return _Record(snapshot=snapshot, record_sha256=record_sha)


def _current_payload(*, facts: _Facts, record: _Record) -> bytes:
    snapshot = record.snapshot
    return _canonical(
        {
            "schema": PHYSICAL_OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_DURABLE_CAS_SCHEMA,
            "version": _VERSION,
            "mode": _MODE,
            "binding_sha256": facts.binding_sha256,
            "ledger_schema": facts.ledger_schema,
            "ledger_config_identity_sha256": facts.ledger_config_identity_sha256,
            "sequence": snapshot.version,
            "previous_head_sha256": snapshot.entry.previous_head_sha256,
            "head_sha256": snapshot.head_sha256,
            "record_sha256": record.record_sha256,
        },
        code="OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_DURABLE_CAS_CURRENT_INVALID",
    )


def _load_state(storage: _Storage, *, facts: _Facts) -> tuple[_Record, ...]:
    _write_create_only_at(
        storage.root_fd,
        _BINDING_FILENAME,
        facts.binding_payload,
        code="OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_DURABLE_CAS_BINDING_MISMATCH",
        allow_exact_existing=True,
    )
    files: list[tuple[int, str, str]] = []
    for name in _listdir(
        storage.entries_fd,
        code="OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_DURABLE_CAS_ENTRIES_RESIDUE",
    ):
        match = _ENTRY_NAME_RE.fullmatch(name)
        if match is None:
            _fail(
                "OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_DURABLE_CAS_TEMP_RESIDUE"
                if _TEMP_NAME_RE.fullmatch(name)
                else "OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_DURABLE_CAS_ENTRIES_RESIDUE"
            )
        _safe_child_metadata(
            storage.entries_fd,
            name,
            directory=False,
            code="OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_DURABLE_CAS_ENTRY_UNSAFE",
        )
        files.append((int(match.group(1)), match.group(2), name))
    if len(files) > _MAX_RECORDS:
        _fail("OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_DURABLE_CAS_RECORD_LIMIT")
    files.sort()
    expected_sequence = 1
    previous_head = _ZERO_SHA256
    result: list[_Record] = []
    for sequence, filename_head, name in files:
        if sequence != expected_sequence:
            _fail("OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_DURABLE_CAS_RECORD_ROLLBACK")
        record = _record_from_payload(
            _read_file_at(
                storage.entries_fd,
                name,
                code="OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_DURABLE_CAS_RECORD_INVALID",
            ),
            facts=facts,
            expected_sequence=expected_sequence,
            expected_previous_head_sha256=previous_head,
        )
        if record.snapshot.head_sha256 != filename_head:
            _fail("OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_DURABLE_CAS_RECORD_INVALID")
        result.append(record)
        previous_head = record.snapshot.head_sha256
        expected_sequence += 1
    try:
        current = _read_file_at(
            storage.root_fd,
            _CURRENT_FILENAME,
            code="OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_DURABLE_CAS_CURRENT_INVALID",
        )
    except PhysicalOperationalFailoverV1WitnessLedgerDurableCasStoreError as exc:
        if isinstance(exc.__cause__, FileNotFoundError):
            if result:
                _fail("OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_DURABLE_CAS_CURRENT_ROLLBACK")
            return tuple()
        raise
    if not result or current != _current_payload(facts=facts, record=result[-1]):
        _fail("OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_DURABLE_CAS_CURRENT_ROLLBACK")
    return tuple(result)


def _require_checkpoint_callback(checkpoint: object) -> None:
    if not callable(getattr(checkpoint, "attest_v1_witness_ledger_state", None)):
        _fail("OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_DURABLE_CAS_CHECKPOINT_MISSING")


def _checkpoint(
    checkpoint: object,
    *,
    facts: _Facts,
    record: _Record | None,
) -> None:
    callback = getattr(checkpoint, "attest_v1_witness_ledger_state", None)
    if not callable(callback):
        _fail("OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_DURABLE_CAS_CHECKPOINT_MISSING")
    snapshot = None if record is None else record.snapshot
    try:
        result = callback(
            binding_sha256=facts.binding_sha256,
            ledger_schema=facts.ledger_schema,
            initial_fi_term_sha256=facts.initial_fi_term_sha256,
            sequence=0 if snapshot is None else snapshot.version,
            previous_head_sha256=_ZERO_SHA256 if snapshot is None else snapshot.entry.previous_head_sha256,
            head_sha256=_ZERO_SHA256 if snapshot is None else snapshot.head_sha256,
            record_sha256=_ZERO_SHA256 if record is None else record.record_sha256,
        )
    except PhysicalOperationalFailoverV1WitnessLedgerDurableCasStoreError:
        raise
    except Exception as exc:
        raise PhysicalOperationalFailoverV1WitnessLedgerDurableCasStoreError(
            "OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_DURABLE_CAS_CHECKPOINT_REJECTED"
        ) from exc
    if result is not None:
        _fail("OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_DURABLE_CAS_CHECKPOINT_INVALID")


def _validate_expected_head(*, expected_version: object, expected_head_sha256: object) -> tuple[int, str]:
    code = "OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_DURABLE_CAS_EXPECTED_HEAD_INVALID"
    version = _positive(expected_version, code=code, permit_zero=True)
    head = _sha256(expected_head_sha256, code=code, permit_zero=True)
    if (version == 0) != (head == _ZERO_SHA256):
        _fail(code)
    return version, head


def _candidate_snapshot(
    *,
    expected_version: int,
    expected_head_sha256: str,
    entry: object,
    next_state: object,
) -> ledger.PhysicalOperationalFailoverV1WitnessLedgerSnapshot:
    code = "OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_DURABLE_CAS_APPEND_INVALID"
    if type(entry) is not ledger.PhysicalOperationalFailoverV1WitnessLedgerEntry or type(next_state) is not ledger.PhysicalOperationalFailoverV1WitnessLedgerState:
        _fail(code)
    assert isinstance(entry, ledger.PhysicalOperationalFailoverV1WitnessLedgerEntry)
    assert isinstance(next_state, ledger.PhysicalOperationalFailoverV1WitnessLedgerState)
    snapshot = _snapshot(entry, next_state)
    if (
        entry.sequence != expected_version + 1
        or entry.previous_head_sha256 != expected_head_sha256
        or next_state.sequence != entry.sequence
    ):
        _fail(code)
    return snapshot


class PhysicalOperationalFailoverV1WitnessLedgerDurableCasStore:
    """Root-gated local implementation of the Witness ledger persistence seam.

    A successful append is durable and read back before ``True`` is returned.
    A checkpoint failure after the local append is intentionally surfaced as an
    error: the caller must reconcile from ``read_current`` rather than assume a
    retry is safe.  This is fail-closed against ambiguous durable outcomes.
    """

    def __init__(
        self,
        config: RootOwnedPhysicalOperationalFailoverV1WitnessLedgerDurableCasStoreConfig,
        *,
        rollback_checkpoint: PhysicalOperationalFailoverV1WitnessLedgerDurableCasCheckpoint | None,
    ) -> None:
        self._config = config
        self._rollback_checkpoint = rollback_checkpoint

    def read_current(self) -> ledger.PhysicalOperationalFailoverV1WitnessLedgerSnapshot | None:
        facts = _facts(self._config)
        _require_root()
        _require_checkpoint_callback(self._rollback_checkpoint)
        with _locked_storage() as storage:
            records = _load_state(storage, facts=facts)
            current = records[-1] if records else None
            _checkpoint(self._rollback_checkpoint, facts=facts, record=current)
            return None if current is None else current.snapshot

    def append_compare_and_swap(
        self,
        *,
        expected_version: int,
        expected_head_sha256: str,
        entry: ledger.PhysicalOperationalFailoverV1WitnessLedgerEntry,
        next_state: ledger.PhysicalOperationalFailoverV1WitnessLedgerState,
    ) -> bool:
        facts = _facts(self._config)
        _require_root()
        _require_checkpoint_callback(self._rollback_checkpoint)
        expected_version, expected_head = _validate_expected_head(
            expected_version=expected_version,
            expected_head_sha256=expected_head_sha256,
        )
        candidate = _candidate_snapshot(
            expected_version=expected_version,
            expected_head_sha256=expected_head,
            entry=entry,
            next_state=next_state,
        )
        with _locked_storage() as storage:
            records = _load_state(storage, facts=facts)
            current = records[-1] if records else None
            _checkpoint(self._rollback_checkpoint, facts=facts, record=current)
            actual_version = 0 if current is None else current.snapshot.version
            actual_head = _ZERO_SHA256 if current is None else current.snapshot.head_sha256
            if actual_version != expected_version or actual_head != expected_head:
                return False
            payload, record_sha = _record_payload(facts=facts, entry=entry, state=next_state)
            filename = f"{candidate.version:020d}-{candidate.head_sha256}.json"
            _write_create_only_at(
                storage.entries_fd,
                filename,
                payload,
                code="OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_DURABLE_CAS_RECORD_WRITE_FAILED",
                allow_exact_existing=False,
            )
            record = _Record(snapshot=candidate, record_sha256=record_sha)
            _write_current_atomic(storage.root_fd, _current_payload(facts=facts, record=record))
            _checkpoint(self._rollback_checkpoint, facts=facts, record=record)
            readback_records = _load_state(storage, facts=facts)
            readback = readback_records[-1] if readback_records else None
            _checkpoint(self._rollback_checkpoint, facts=facts, record=readback)
            if readback != record:
                _fail("OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_DURABLE_CAS_READBACK_INVALID")
            return True
