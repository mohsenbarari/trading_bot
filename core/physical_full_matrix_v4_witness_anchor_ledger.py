"""Root-local, append-only runtime for the V4 signed Witness anchor.

This is a deliberately narrow persistence boundary.  It accepts a canonical
controller-signed wire request, verifies it against the exact durable current
head, asks an injected root-owned signer to sign already-canonical Witness
bytes, and stores the result in a create-only/fsync'd local ledger.  It has no
network, provider, SSH, Docker, database-promotion, or host-control code.

The local filesystem is not presented as a substitute for the remote Witness
or for human approval.  It is a fail-closed, root-only implementation seam
for a future narrow transport adapter.
"""

from __future__ import annotations

import base64
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
from typing import Protocol

from core import physical_full_matrix_v4_witness_anchor_wire as wire


__all__ = (
    "FIXED_PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_STATE_ROOT",
    "PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_DEFAULT_ENABLED",
    "PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_SCHEMA",
    "PhysicalFullMatrixV4WitnessAnchorLedgerClock",
    "PhysicalFullMatrixV4WitnessAnchorLedgerError",
    "PhysicalFullMatrixV4WitnessAnchorLedgerRootSigner",
    "RootOwnedPhysicalFullMatrixV4WitnessAnchorLedger",
    "RootOwnedPhysicalFullMatrixV4WitnessAnchorLedgerConfig",
)


PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_SCHEMA = (
    "gold-trade-physical-full-matrix-v4-witness-anchor-ledger-v2"
)
PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_DEFAULT_ENABLED = False

# Deployment owns this exact directory.  A constructor never accepts an
# operator-provided path, and all descendants are opened relative to its fd.
FIXED_PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_STATE_ROOT = Path(
    "/var/lib/trading-bot/physical-full-matrix-v4-witness-anchor-ledger"
)

_VERSION = 2
_MODE = "root-owned-v4-witness-local-immutable-anchor-ledger-v2"
_LOCK_FILENAME = "anchor-ledger.lock"
_BINDING_FILENAME = "binding.json"
_CURRENT_FILENAME = "current.json"
_RECORDS_DIRECTORY = "records"
_PENDING_DIRECTORY = "pending"
_MAX_RECORD_BYTES = 256 * 1024
_MAX_RECORDS = 8_192
_ZERO_SHA256 = "0" * 64
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_RECORD_NAME_RE = re.compile(r"^([0-9]{20})-([0-9a-f]{64})\.json$", re.ASCII)
_PENDING_NAME_RE = re.compile(r"^([0-9a-f]{64})\.json$", re.ASCII)
_TEMP_NAME_RE = re.compile(r"^\.current-[0-9a-f]{64}\.tmp$", re.ASCII)
_TIMESTAMP_RE = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z$",
    re.ASCII,
)


class PhysicalFullMatrixV4WitnessAnchorLedgerError(RuntimeError):
    """The root-local V4 Witness-anchor ledger rejected unsafe state."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _fail(code: str) -> None:
    raise PhysicalFullMatrixV4WitnessAnchorLedgerError(code)


class PhysicalFullMatrixV4WitnessAnchorLedgerClock(Protocol):
    """Root-owned time source; callers cannot choose verification time."""

    def now_utc(self) -> datetime: ...


class PhysicalFullMatrixV4WitnessAnchorLedgerRootSigner(Protocol):
    """Narrow injected root signer that never exposes a private key to ledger."""

    def witness_public_key(self) -> bytes: ...

    def sign_immutable_anchor_head(
        self,
        *,
        canonical_signed_immutable_head: bytes,
    ) -> bytes: ...

    def sign_read_observation(
        self,
        *,
        canonical_signed_read_observation: bytes,
    ) -> bytes: ...


@dataclass(frozen=True)
class RootOwnedPhysicalFullMatrixV4WitnessAnchorLedgerConfig:
    """Default-off fixed-root policy; all public campaign facts are pinned."""

    schema: str = PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_SCHEMA
    enabled: bool = PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_DEFAULT_ENABLED
    policy: wire.PhysicalFullMatrixV4WitnessAnchorVerificationPolicy | None = None


@dataclass(frozen=True)
class _Facts:
    policy: wire.PhysicalFullMatrixV4WitnessAnchorVerificationPolicy
    genesis: wire.PhysicalFullMatrixV4WitnessAnchorGenesis
    journal_binding_sha256: str
    baseline_plan_binding_sha256: str
    controller_key_id: str
    witness_key_id: str


@dataclass(frozen=True)
class _Record:
    sequence: int
    previous_record_sha256: str
    record_sha256: str
    pending_sha256: str
    replay_id: str
    request_sha256: str
    commitment_sha256: str
    head_sha256: str
    canonical_request: bytes
    canonical_immutable_head: bytes


@dataclass(frozen=True)
class _Current:
    sequence: int
    head_sha256: str
    record_sha256: str


@dataclass(frozen=True)
class _Pending:
    pending_sha256: str
    replay_id: str
    request_sha256: str
    predecessor_sequence: int
    predecessor_head_sha256: str
    canonical_request: bytes


@dataclass(frozen=True)
class _Storage:
    root_fd: int
    records_fd: int
    pending_fd: int


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
        raise PhysicalFullMatrixV4WitnessAnchorLedgerError(code) from exc


def _strict_object(items: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in items:
        if key in result:
            _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def _decode_canonical(value: object, *, code: str) -> dict[str, object]:
    if type(value) is not bytes or not value or len(value) > _MAX_RECORD_BYTES:
        _fail(code)
    try:
        decoded = json.loads(
            value.decode("ascii"),
            object_pairs_hook=_strict_object,
            parse_constant=lambda _item: _fail(code),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, PhysicalFullMatrixV4WitnessAnchorLedgerError):
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
    if type(value) is not int or value < (0 if permit_zero else 1) or value > 2**63 - 1:
        _fail(code)
    return value


def _utc(value: object, *, code: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        _fail(code)
    return value.astimezone(timezone.utc)


def _render_timestamp(value: datetime, *, code: str) -> str:
    observed = _utc(value, code=code)
    if observed.microsecond:
        return observed.isoformat(timespec="microseconds").replace("+00:00", "Z")
    return observed.isoformat(timespec="seconds").replace("+00:00", "Z")


def _timestamp(value: object, *, code: str) -> datetime:
    if type(value) is not str or _TIMESTAMP_RE.fullmatch(value) is None:
        _fail(code)
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        _fail(code)
    result = parsed.astimezone(timezone.utc)
    if _render_timestamp(result, code=code) != value:
        _fail(code)
    return result


def _b64(value: bytes, *, code: str) -> str:
    if type(value) is not bytes or not value:
        _fail(code)
    return base64.b64encode(value).decode("ascii")


def _unb64(value: object, *, code: str) -> bytes:
    if type(value) is not str or not value:
        _fail(code)
    try:
        result = base64.b64decode(value.encode("ascii"), validate=True)
    except (UnicodeEncodeError, ValueError, base64.binascii.Error) as exc:
        raise PhysicalFullMatrixV4WitnessAnchorLedgerError(code) from exc
    if not result or _b64(result, code=code) != value:
        _fail(code)
    return result


def _trusted_now(
    clock: object,
    *,
    floor: datetime | None = None,
) -> datetime:
    callback = getattr(clock, "now_utc", None)
    if not callable(callback):
        _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_CLOCK_MISSING")
    try:
        observed = _utc(
            callback(),
            code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_CLOCK_INVALID",
        )
    except PhysicalFullMatrixV4WitnessAnchorLedgerError:
        raise
    except Exception as exc:
        raise PhysicalFullMatrixV4WitnessAnchorLedgerError(
            "PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_CLOCK_FAILED"
        ) from exc
    if floor is not None and observed < floor:
        _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_CLOCK_REGRESSION")
    return observed


def _facts(
    config: object,
    *,
    signer: object,
    now: datetime,
) -> _Facts:
    if type(config) is not RootOwnedPhysicalFullMatrixV4WitnessAnchorLedgerConfig:
        _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_CONFIG_INVALID")
    if (
        config.schema != PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_SCHEMA
        or config.enabled is not True
        or type(config.policy) is not wire.PhysicalFullMatrixV4WitnessAnchorVerificationPolicy
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_CONFIG_INVALID")
    # This validates the configured signed genesis and all policy key/limit
    # material before filesystem state is opened.
    try:
        verified = wire.verified_physical_full_matrix_v4_witness_anchor_genesis_head(
            policy=config.policy,
            now=now,
        )
    except wire.PhysicalFullMatrixV4WitnessAnchorWireError as exc:
        raise PhysicalFullMatrixV4WitnessAnchorLedgerError(
            "PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_POLICY_INVALID"
        ) from exc
    callback = getattr(signer, "witness_public_key", None)
    if not callable(callback):
        _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_SIGNER_MISSING")
    try:
        signer_public = callback()
    except Exception as exc:
        raise PhysicalFullMatrixV4WitnessAnchorLedgerError(
            "PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_SIGNER_PUBLIC_KEY_FAILED"
        ) from exc
    if type(signer_public) is not bytes or signer_public != config.policy.witness_public_key:
        _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_SIGNER_KEY_MISMATCH")
    genesis = config.policy.genesis
    return _Facts(
        policy=config.policy,
        genesis=genesis,
        journal_binding_sha256=verified.journal_binding_sha256,
        baseline_plan_binding_sha256=verified.baseline_plan_binding_sha256,
        controller_key_id=wire.ed25519_physical_full_matrix_v4_witness_anchor_key_id(
            config.policy.controller_public_key
        ),
        witness_key_id=wire.ed25519_physical_full_matrix_v4_witness_anchor_key_id(
            config.policy.witness_public_key
        ),
    )


def _require_root() -> None:
    try:
        if os.geteuid() != 0:
            _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_ROOT_RUNTIME_REQUIRED")
    except OSError as exc:
        raise PhysicalFullMatrixV4WitnessAnchorLedgerError(
            "PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_ROOT_RUNTIME_REQUIRED"
        ) from exc


def _require_fd_platform() -> None:
    if not all(
        hasattr(os, item)
        for item in ("O_NOFOLLOW", "O_DIRECTORY", "fdatasync")
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_PLATFORM_UNSUPPORTED")


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
    if not isinstance(path, Path) or not path.is_absolute() or ".." in path.parts:
        _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_STATE_ROOT_UNSAFE")
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
                _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_STATE_ROOT_UNSAFE")
    except PhysicalFullMatrixV4WitnessAnchorLedgerError:
        raise
    except OSError as exc:
        raise PhysicalFullMatrixV4WitnessAnchorLedgerError(
            "PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_STATE_ROOT_UNSAFE"
        ) from exc
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass


def _open_secure_root() -> int:
    root = FIXED_PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_STATE_ROOT
    _validate_ancestors(root)
    descriptor = -1
    try:
        before = os.lstat(root)
        resolved = root.resolve(strict=True)
        if (
            resolved != root
            or stat.S_ISLNK(before.st_mode)
            or not stat.S_ISDIR(before.st_mode)
            or before.st_uid != 0
            or stat.S_IMODE(before.st_mode) != 0o700
        ):
            _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_STATE_ROOT_UNSAFE")
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
            _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_STATE_ROOT_UNSAFE")
        return descriptor
    except PhysicalFullMatrixV4WitnessAnchorLedgerError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError as exc:
        if descriptor >= 0:
            os.close(descriptor)
        raise PhysicalFullMatrixV4WitnessAnchorLedgerError(
            "PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_STATE_ROOT_UNSAFE"
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
        raise PhysicalFullMatrixV4WitnessAnchorLedgerError(code) from exc
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


def _ensure_records_directory(root_fd: int) -> int:
    descriptor = -1
    created = False
    try:
        try:
            os.mkdir(_RECORDS_DIRECTORY, 0o700, dir_fd=root_fd)
            created = True
        except FileExistsError:
            pass
        descriptor = os.open(
            _RECORDS_DIRECTORY,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
            dir_fd=root_fd,
        )
        if created:
            os.fchmod(descriptor, 0o700)
            os.fsync(descriptor)
            os.fsync(root_fd)
        before = _safe_child_metadata(
            root_fd,
            _RECORDS_DIRECTORY,
            directory=True,
            code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_RECORDS_UNSAFE",
        )
        opened = os.fstat(descriptor)
        after = _safe_child_metadata(
            root_fd,
            _RECORDS_DIRECTORY,
            directory=True,
            code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_RECORDS_UNSAFE",
        )
        if _metadata_tuple(before) != _metadata_tuple(opened) or _metadata_tuple(after) != _metadata_tuple(before):
            _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_RECORDS_UNSAFE")
        return descriptor
    except PhysicalFullMatrixV4WitnessAnchorLedgerError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError as exc:
        if descriptor >= 0:
            os.close(descriptor)
        raise PhysicalFullMatrixV4WitnessAnchorLedgerError(
            "PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_RECORDS_UNSAFE"
        ) from exc


def _ensure_pending_directory(root_fd: int) -> int:
    descriptor = -1
    created = False
    try:
        try:
            os.mkdir(_PENDING_DIRECTORY, 0o700, dir_fd=root_fd)
            created = True
        except FileExistsError:
            pass
        descriptor = os.open(
            _PENDING_DIRECTORY,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
            dir_fd=root_fd,
        )
        if created:
            os.fchmod(descriptor, 0o700)
            os.fsync(descriptor)
            os.fsync(root_fd)
        before = _safe_child_metadata(
            root_fd,
            _PENDING_DIRECTORY,
            directory=True,
            code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_PENDING_UNSAFE",
        )
        opened = os.fstat(descriptor)
        after = _safe_child_metadata(
            root_fd,
            _PENDING_DIRECTORY,
            directory=True,
            code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_PENDING_UNSAFE",
        )
        if _metadata_tuple(before) != _metadata_tuple(opened) or _metadata_tuple(after) != _metadata_tuple(before):
            _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_PENDING_UNSAFE")
        return descriptor
    except PhysicalFullMatrixV4WitnessAnchorLedgerError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError as exc:
        if descriptor >= 0:
            os.close(descriptor)
        raise PhysicalFullMatrixV4WitnessAnchorLedgerError(
            "PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_PENDING_UNSAFE"
        ) from exc


def _open_lock(root_fd: int) -> int:
    descriptor = -1
    created = False
    try:
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
        before = _safe_child_metadata(
            root_fd,
            _LOCK_FILENAME,
            directory=False,
            code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_LOCK_UNSAFE",
        )
        opened = os.fstat(descriptor)
        after = _safe_child_metadata(
            root_fd,
            _LOCK_FILENAME,
            directory=False,
            code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_LOCK_UNSAFE",
        )
        if _metadata_tuple(before) != _metadata_tuple(opened) or _metadata_tuple(after) != _metadata_tuple(before):
            _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_LOCK_UNSAFE")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        return descriptor
    except PhysicalFullMatrixV4WitnessAnchorLedgerError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError as exc:
        if descriptor >= 0:
            os.close(descriptor)
        raise PhysicalFullMatrixV4WitnessAnchorLedgerError(
            "PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_LOCK_OPEN_FAILED"
        ) from exc


@contextmanager
def _locked_storage() -> Iterator[_Storage]:
    root_fd = _open_secure_root()
    records_fd = -1
    pending_fd = -1
    lock_fd = -1
    try:
        records_fd = _ensure_records_directory(root_fd)
        pending_fd = _ensure_pending_directory(root_fd)
        lock_fd = _open_lock(root_fd)
        yield _Storage(root_fd=root_fd, records_fd=records_fd, pending_fd=pending_fd)
    finally:
        try:
            if lock_fd >= 0:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
        finally:
            for descriptor in (lock_fd, pending_fd, records_fd, root_fd):
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
    except PhysicalFullMatrixV4WitnessAnchorLedgerError:
        raise
    except OSError as exc:
        raise PhysicalFullMatrixV4WitnessAnchorLedgerError(code) from exc
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
            raise PhysicalFullMatrixV4WitnessAnchorLedgerError(code) from exc
        if type(written) is not int or written <= 0:
            _fail(code)
        view = view[written:]


def _check_plain_filename(value: object, *, code: str) -> str:
    if (
        type(value) is not str
        or not value
        or "/" in value
        or "\\" in value
        or value in {".", ".."}
    ):
        _fail(code)
    return value


def _write_create_only_at(parent_fd: int, name: str, payload: bytes, *, code: str) -> None:
    _check_plain_filename(name, code=code)
    if type(payload) is not bytes or not 1 <= len(payload) <= _MAX_RECORD_BYTES:
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
            if _read_file_at(parent_fd, name, code=code) != payload:
                _fail(code)
            return
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != 0
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_size != 0
        ):
            _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_STATE_UNSAFE")
        _write_all(descriptor, payload, code=code)
        os.fdatasync(descriptor)
    except PhysicalFullMatrixV4WitnessAnchorLedgerError:
        raise
    except OSError as exc:
        raise PhysicalFullMatrixV4WitnessAnchorLedgerError(code) from exc
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass
    try:
        os.fsync(parent_fd)
    except OSError as exc:
        raise PhysicalFullMatrixV4WitnessAnchorLedgerError(
            "PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_DIRECTORY_FSYNC_FAILED"
        ) from exc


def _write_current_atomic(root_fd: int, payload: bytes) -> None:
    """Atomically replace current only after its immutable record is durable."""

    if type(payload) is not bytes or not 1 <= len(payload) <= _MAX_RECORD_BYTES:
        _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_CURRENT_INVALID")
    temporary = ".current-" + secrets.token_bytes(32).hex() + ".tmp"
    if _TEMP_NAME_RE.fullmatch(temporary) is None:
        _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_CURRENT_INVALID")
    descriptor = -1
    try:
        # Existing targets must be a secure regular file.  A missing target is
        # valid only for the first completed record.
        try:
            _safe_child_metadata(
                root_fd,
                _CURRENT_FILENAME,
                directory=False,
                code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_CURRENT_UNSAFE",
            )
        except PhysicalFullMatrixV4WitnessAnchorLedgerError as exc:
            if not isinstance(exc.__cause__, FileNotFoundError):
                # _safe_child_metadata deliberately wraps OSErrors; retry an
                # explicit lstat so only absence is accepted here.
                try:
                    os.stat(_CURRENT_FILENAME, dir_fd=root_fd, follow_symlinks=False)
                except FileNotFoundError:
                    pass
                except OSError as inner:
                    raise PhysicalFullMatrixV4WitnessAnchorLedgerError(
                        "PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_CURRENT_UNSAFE"
                    ) from inner
                else:
                    raise exc
            else:
                pass
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
            _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_CURRENT_UNSAFE")
        _write_all(
            descriptor,
            payload,
            code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_CURRENT_WRITE_FAILED",
        )
        os.fdatasync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.rename(
            temporary,
            _CURRENT_FILENAME,
            src_dir_fd=root_fd,
            dst_dir_fd=root_fd,
        )
        _safe_child_metadata(
            root_fd,
            _CURRENT_FILENAME,
            directory=False,
            code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_CURRENT_UNSAFE",
        )
        os.fsync(root_fd)
    except PhysicalFullMatrixV4WitnessAnchorLedgerError:
        raise
    except OSError as exc:
        raise PhysicalFullMatrixV4WitnessAnchorLedgerError(
            "PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_CURRENT_WRITE_FAILED"
        ) from exc
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass


def _binding_body(facts: _Facts) -> dict[str, object]:
    return {
        "schema": PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_SCHEMA,
        "version": _VERSION,
        "mode": _MODE,
        "journal_binding_sha256": facts.journal_binding_sha256,
        "baseline_plan_binding_sha256": facts.baseline_plan_binding_sha256,
        "run_id": str(facts.genesis.run_id),
        "plan_sha256": facts.genesis.plan_sha256,
        "anchor_genesis_sequence": facts.genesis.sequence,
        "anchor_genesis_head_sha256": facts.genesis.head_sha256,
        "canonical_signed_genesis_base64": _b64(
            wire.canonical_physical_full_matrix_v4_witness_anchor_genesis_bytes(
                facts.genesis
            ),
            code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_BINDING_INVALID",
        ),
        "controller_key_id": facts.controller_key_id,
        "witness_key_id": facts.witness_key_id,
        "execution_authorized": False,
        "promotion_authorized": False,
        "full_matrix_executed": False,
    }


def _ensure_binding(storage: _Storage, *, facts: _Facts) -> None:
    payload = _canonical(
        _binding_body(facts),
        code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_BINDING_INVALID",
    )
    _write_create_only_at(
        storage.root_fd,
        _BINDING_FILENAME,
        payload,
        code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_BINDING_INVALID",
    )
    if _read_file_at(
        storage.root_fd,
        _BINDING_FILENAME,
        code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_BINDING_INVALID",
    ) != payload:
        _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_BINDING_INVALID")


_RECORD_BASE_FIELDS = frozenset(
    {
        "schema",
        "version",
        "journal_binding_sha256",
        "baseline_plan_binding_sha256",
        "run_id",
        "plan_sha256",
        "anchor_genesis_sequence",
        "anchor_genesis_head_sha256",
        "sequence",
        "previous_record_sha256",
        "pending_sha256",
        "replay_id",
        "request_sha256",
        "commitment_sha256",
        "head_sha256",
        "canonical_request_base64",
        "canonical_immutable_head_base64",
        "execution_authorized",
        "promotion_authorized",
        "full_matrix_executed",
    }
)
_RECORD_FIELDS = _RECORD_BASE_FIELDS | {"record_sha256"}
_CURRENT_FIELDS = frozenset(
    {
        "schema",
        "version",
        "journal_binding_sha256",
        "baseline_plan_binding_sha256",
        "run_id",
        "plan_sha256",
        "anchor_genesis_sequence",
        "anchor_genesis_head_sha256",
        "sequence",
        "head_sha256",
        "record_sha256",
        "execution_authorized",
        "promotion_authorized",
        "full_matrix_executed",
    }
)


def _record_base_body(
    *,
    facts: _Facts,
    sequence: int,
    previous_record_sha256: str,
    pending_sha256: str,
    replay_id: str,
    request_sha256: str,
    commitment_sha256: str,
    head_sha256: str,
    canonical_request: bytes,
    canonical_immutable_head: bytes,
) -> dict[str, object]:
    if type(replay_id) is not str or _SHA256_RE.fullmatch(replay_id) is None:
        _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_RECORD_INVALID")
    return {
        "schema": PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_SCHEMA,
        "version": _VERSION,
        "journal_binding_sha256": facts.journal_binding_sha256,
        "baseline_plan_binding_sha256": facts.baseline_plan_binding_sha256,
        "run_id": str(facts.genesis.run_id),
        "plan_sha256": facts.genesis.plan_sha256,
        "anchor_genesis_sequence": facts.genesis.sequence,
        "anchor_genesis_head_sha256": facts.genesis.head_sha256,
        "sequence": _positive(sequence, code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_RECORD_INVALID"),
        "previous_record_sha256": _sha256(
            previous_record_sha256,
            code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_RECORD_INVALID",
            permit_zero=True,
        ),
        "pending_sha256": _sha256(
            pending_sha256,
            code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_RECORD_INVALID",
        ),
        "replay_id": replay_id,
        "request_sha256": _sha256(
            request_sha256,
            code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_RECORD_INVALID",
        ),
        "commitment_sha256": _sha256(
            commitment_sha256,
            code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_RECORD_INVALID",
        ),
        "head_sha256": _sha256(
            head_sha256,
            code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_RECORD_INVALID",
        ),
        "canonical_request_base64": _b64(
            canonical_request,
            code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_RECORD_INVALID",
        ),
        "canonical_immutable_head_base64": _b64(
            canonical_immutable_head,
            code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_RECORD_INVALID",
        ),
        "execution_authorized": False,
        "promotion_authorized": False,
        "full_matrix_executed": False,
    }


def _record_payload(
    *,
    facts: _Facts,
    sequence: int,
    previous_record_sha256: str,
    pending_sha256: str,
    replay_id: str,
    request_sha256: str,
    commitment_sha256: str,
    head_sha256: str,
    canonical_request: bytes,
    canonical_immutable_head: bytes,
) -> tuple[bytes, str]:
    body = _record_base_body(
        facts=facts,
        sequence=sequence,
        previous_record_sha256=previous_record_sha256,
        pending_sha256=pending_sha256,
        replay_id=replay_id,
        request_sha256=request_sha256,
        commitment_sha256=commitment_sha256,
        head_sha256=head_sha256,
        canonical_request=canonical_request,
        canonical_immutable_head=canonical_immutable_head,
    )
    record_sha256 = hashlib.sha256(
        _canonical(body, code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_RECORD_INVALID")
    ).hexdigest()
    return (
        _canonical(
            {**body, "record_sha256": record_sha256},
            code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_RECORD_INVALID",
        ),
        record_sha256,
    )


def _record_from_payload(value: bytes, *, facts: _Facts) -> _Record:
    decoded = _decode_canonical(
        value,
        code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_RECORD_INVALID",
    )
    if (
        set(decoded) != _RECORD_FIELDS
        or decoded["schema"] != PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_SCHEMA
        or decoded["version"] != _VERSION
        or decoded["execution_authorized"] is not False
        or decoded["promotion_authorized"] is not False
        or decoded["full_matrix_executed"] is not False
        or decoded["journal_binding_sha256"] != facts.journal_binding_sha256
        or decoded["baseline_plan_binding_sha256"] != facts.baseline_plan_binding_sha256
        or decoded["run_id"] != str(facts.genesis.run_id)
        or decoded["plan_sha256"] != facts.genesis.plan_sha256
        or decoded["anchor_genesis_sequence"] != facts.genesis.sequence
        or decoded["anchor_genesis_head_sha256"] != facts.genesis.head_sha256
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_RECORD_INVALID")
    canonical_request = _unb64(
        decoded["canonical_request_base64"],
        code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_RECORD_INVALID",
    )
    canonical_immutable_head = _unb64(
        decoded["canonical_immutable_head_base64"],
        code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_RECORD_INVALID",
    )
    result = _Record(
        sequence=_positive(
            decoded["sequence"],
            code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_RECORD_INVALID",
        ),
        previous_record_sha256=_sha256(
            decoded["previous_record_sha256"],
            code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_RECORD_INVALID",
            permit_zero=True,
        ),
        pending_sha256=_sha256(
            decoded["pending_sha256"],
            code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_RECORD_INVALID",
        ),
        record_sha256=_sha256(
            decoded["record_sha256"],
            code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_RECORD_INVALID",
        ),
        replay_id=decoded["replay_id"],  # type: ignore[arg-type]
        request_sha256=_sha256(
            decoded["request_sha256"],
            code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_RECORD_INVALID",
        ),
        commitment_sha256=_sha256(
            decoded["commitment_sha256"],
            code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_RECORD_INVALID",
        ),
        head_sha256=_sha256(
            decoded["head_sha256"],
            code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_RECORD_INVALID",
        ),
        canonical_request=canonical_request,
        canonical_immutable_head=canonical_immutable_head,
    )
    payload, expected_sha256 = _record_payload(
        facts=facts,
        sequence=result.sequence,
        previous_record_sha256=result.previous_record_sha256,
        pending_sha256=result.pending_sha256,
        replay_id=result.replay_id,
        request_sha256=result.request_sha256,
        commitment_sha256=result.commitment_sha256,
        head_sha256=result.head_sha256,
        canonical_request=result.canonical_request,
        canonical_immutable_head=result.canonical_immutable_head,
    )
    if expected_sha256 != result.record_sha256 or payload != value:
        _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_RECORD_INVALID")
    return result


def _current_payload(*, facts: _Facts, current: _Current) -> bytes:
    return _canonical(
        {
            "schema": PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_SCHEMA,
            "version": _VERSION,
            "journal_binding_sha256": facts.journal_binding_sha256,
            "baseline_plan_binding_sha256": facts.baseline_plan_binding_sha256,
            "run_id": str(facts.genesis.run_id),
            "plan_sha256": facts.genesis.plan_sha256,
            "anchor_genesis_sequence": facts.genesis.sequence,
            "anchor_genesis_head_sha256": facts.genesis.head_sha256,
            "sequence": current.sequence,
            "head_sha256": current.head_sha256,
            "record_sha256": current.record_sha256,
            "execution_authorized": False,
            "promotion_authorized": False,
            "full_matrix_executed": False,
        },
        code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_CURRENT_INVALID",
    )


def _current_from_payload(value: bytes, *, facts: _Facts) -> _Current:
    decoded = _decode_canonical(
        value,
        code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_CURRENT_INVALID",
    )
    if (
        set(decoded) != _CURRENT_FIELDS
        or decoded["schema"] != PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_SCHEMA
        or decoded["version"] != _VERSION
        or decoded["execution_authorized"] is not False
        or decoded["promotion_authorized"] is not False
        or decoded["full_matrix_executed"] is not False
        or decoded["journal_binding_sha256"] != facts.journal_binding_sha256
        or decoded["baseline_plan_binding_sha256"] != facts.baseline_plan_binding_sha256
        or decoded["run_id"] != str(facts.genesis.run_id)
        or decoded["plan_sha256"] != facts.genesis.plan_sha256
        or decoded["anchor_genesis_sequence"] != facts.genesis.sequence
        or decoded["anchor_genesis_head_sha256"] != facts.genesis.head_sha256
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_CURRENT_INVALID")
    result = _Current(
        sequence=_positive(
            decoded["sequence"],
            code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_CURRENT_INVALID",
        ),
        head_sha256=_sha256(
            decoded["head_sha256"],
            code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_CURRENT_INVALID",
        ),
        record_sha256=_sha256(
            decoded["record_sha256"],
            code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_CURRENT_INVALID",
        ),
    )
    if _current_payload(facts=facts, current=result) != value:
        _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_CURRENT_INVALID")
    return result


_PENDING_BASE_FIELDS = frozenset(
    {
        "schema",
        "version",
        "journal_binding_sha256",
        "baseline_plan_binding_sha256",
        "run_id",
        "plan_sha256",
        "anchor_genesis_sequence",
        "anchor_genesis_head_sha256",
        "replay_id",
        "request_sha256",
        "predecessor_sequence",
        "predecessor_head_sha256",
        "canonical_request_base64",
        "execution_authorized",
        "promotion_authorized",
        "full_matrix_executed",
    }
)
_PENDING_FIELDS = _PENDING_BASE_FIELDS | {"pending_sha256"}


def _pending_payload(
    *,
    facts: _Facts,
    replay_id: str,
    request_sha256: str,
    predecessor_sequence: int,
    predecessor_head_sha256: str,
    canonical_request: bytes,
) -> tuple[bytes, str]:
    if type(replay_id) is not str or _SHA256_RE.fullmatch(replay_id) is None:
        _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_PENDING_INVALID")
    body = {
        "schema": PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_SCHEMA,
        "version": _VERSION,
        "journal_binding_sha256": facts.journal_binding_sha256,
        "baseline_plan_binding_sha256": facts.baseline_plan_binding_sha256,
        "run_id": str(facts.genesis.run_id),
        "plan_sha256": facts.genesis.plan_sha256,
        "anchor_genesis_sequence": facts.genesis.sequence,
        "anchor_genesis_head_sha256": facts.genesis.head_sha256,
        "replay_id": replay_id,
        "request_sha256": _sha256(
            request_sha256,
            code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_PENDING_INVALID",
        ),
        "predecessor_sequence": _positive(
            predecessor_sequence,
            code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_PENDING_INVALID",
            permit_zero=True,
        ),
        "predecessor_head_sha256": _sha256(
            predecessor_head_sha256,
            code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_PENDING_INVALID",
            permit_zero=True,
        ),
        "canonical_request_base64": _b64(
            canonical_request,
            code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_PENDING_INVALID",
        ),
        "execution_authorized": False,
        "promotion_authorized": False,
        "full_matrix_executed": False,
    }
    pending_sha256 = hashlib.sha256(
        _canonical(body, code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_PENDING_INVALID")
    ).hexdigest()
    return (
        _canonical(
            {**body, "pending_sha256": pending_sha256},
            code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_PENDING_INVALID",
        ),
        pending_sha256,
    )


def _pending_from_payload(value: bytes, *, facts: _Facts) -> _Pending:
    decoded = _decode_canonical(
        value,
        code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_PENDING_INVALID",
    )
    if (
        set(decoded) != _PENDING_FIELDS
        or decoded["schema"] != PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_SCHEMA
        or decoded["version"] != _VERSION
        or decoded["execution_authorized"] is not False
        or decoded["promotion_authorized"] is not False
        or decoded["full_matrix_executed"] is not False
        or decoded["journal_binding_sha256"] != facts.journal_binding_sha256
        or decoded["baseline_plan_binding_sha256"] != facts.baseline_plan_binding_sha256
        or decoded["run_id"] != str(facts.genesis.run_id)
        or decoded["plan_sha256"] != facts.genesis.plan_sha256
        or decoded["anchor_genesis_sequence"] != facts.genesis.sequence
        or decoded["anchor_genesis_head_sha256"] != facts.genesis.head_sha256
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_PENDING_INVALID")
    result = _Pending(
        pending_sha256=_sha256(
            decoded["pending_sha256"],
            code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_PENDING_INVALID",
        ),
        replay_id=decoded["replay_id"],  # type: ignore[arg-type]
        request_sha256=_sha256(
            decoded["request_sha256"],
            code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_PENDING_INVALID",
        ),
        predecessor_sequence=_positive(
            decoded["predecessor_sequence"],
            code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_PENDING_INVALID",
            permit_zero=True,
        ),
        predecessor_head_sha256=_sha256(
            decoded["predecessor_head_sha256"],
            code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_PENDING_INVALID",
            permit_zero=True,
        ),
        canonical_request=_unb64(
            decoded["canonical_request_base64"],
            code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_PENDING_INVALID",
        ),
    )
    payload, expected = _pending_payload(
        facts=facts,
        replay_id=result.replay_id,
        request_sha256=result.request_sha256,
        predecessor_sequence=result.predecessor_sequence,
        predecessor_head_sha256=result.predecessor_head_sha256,
        canonical_request=result.canonical_request,
    )
    if expected != result.pending_sha256 or payload != value:
        _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_PENDING_INVALID")
    return result


def _list_directory(descriptor: int, *, code: str) -> tuple[str, ...]:
    try:
        names = os.listdir(descriptor)
    except OSError as exc:
        raise PhysicalFullMatrixV4WitnessAnchorLedgerError(code) from exc
    if not all(type(name) is str for name in names):
        _fail(code)
    return tuple(sorted(names))


def _assert_layout(storage: _Storage) -> None:
    names = set(
        _list_directory(
            storage.root_fd,
            code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_LAYOUT_UNSAFE",
        )
    )
    allowed = {
        _LOCK_FILENAME,
        _BINDING_FILENAME,
        _RECORDS_DIRECTORY,
        _PENDING_DIRECTORY,
        _CURRENT_FILENAME,
    }
    if not names <= allowed:
        # A temp file after a power loss is intentionally an indeterminate
        # state rather than something a new process silently cleans up.
        _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_LAYOUT_UNSAFE")
    if not {_LOCK_FILENAME, _BINDING_FILENAME, _RECORDS_DIRECTORY, _PENDING_DIRECTORY} <= names:
        _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_LAYOUT_UNSAFE")


def _record_names(storage: _Storage) -> tuple[tuple[int, str, str], ...]:
    names = _list_directory(
        storage.records_fd,
        code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_RECORDS_UNSAFE",
    )
    if len(names) > _MAX_RECORDS:
        _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_RECORDS_TOO_LONG")
    result: list[tuple[int, str, str]] = []
    for name in names:
        match = _RECORD_NAME_RE.fullmatch(name)
        if match is None:
            _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_RECORDS_UNSAFE")
        sequence = int(match.group(1))
        if sequence < 1 or sequence > 2**63 - 1:
            _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_RECORDS_UNSAFE")
        result.append((sequence, match.group(2), name))
    if len({item[0] for item in result}) != len(result):
        _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_RECORDS_UNSAFE")
    return tuple(sorted(result))


def _pending_items(storage: _Storage, *, facts: _Facts) -> dict[str, _Pending]:
    names = _list_directory(
        storage.pending_fd,
        code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_PENDING_UNSAFE",
    )
    if len(names) > _MAX_RECORDS:
        _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_PENDING_TOO_LONG")
    result: dict[str, _Pending] = {}
    replay_ids: set[str] = set()
    for name in names:
        match = _PENDING_NAME_RE.fullmatch(name)
        if match is None:
            _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_PENDING_UNSAFE")
        request_sha256 = match.group(1)
        pending = _pending_from_payload(
            _read_file_at(
                storage.pending_fd,
                name,
                code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_PENDING_INVALID",
            ),
            facts=facts,
        )
        if (
            pending.request_sha256 != request_sha256
            or pending.request_sha256 in result
            or pending.replay_id in replay_ids
        ):
            _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_PENDING_INVALID")
        result[pending.request_sha256] = pending
        replay_ids.add(pending.replay_id)
    return result


def _wire_or_fail(callback, *, code: str):
    try:
        return callback()
    except wire.PhysicalFullMatrixV4WitnessAnchorWireError as exc:
        raise PhysicalFullMatrixV4WitnessAnchorLedgerError(code) from exc


def _current_exists(root_fd: int) -> bool:
    try:
        os.stat(_CURRENT_FILENAME, dir_fd=root_fd, follow_symlinks=False)
        return True
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise PhysicalFullMatrixV4WitnessAnchorLedgerError(
            "PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_CURRENT_UNSAFE"
        ) from exc


def _load_state(
    storage: _Storage,
    *,
    facts: _Facts,
    now: datetime,
) -> tuple[
    (
        wire.VerifiedPhysicalFullMatrixV4WitnessAnchorHead
        | wire.VerifiedPhysicalFullMatrixV4WitnessAnchorImmutableHead
    ),
    bytes,
    tuple[_Record, ...],
    frozenset[str],
]:
    """Replay every immutable record and require current to name its tip."""

    _assert_layout(storage)
    names = _record_names(storage)
    pending_by_request_sha256 = _pending_items(storage, facts=facts)
    active = _wire_or_fail(
        lambda: wire.verified_physical_full_matrix_v4_witness_anchor_genesis_head(
            policy=facts.policy,
            now=now,
        ),
        code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_GENESIS_INVALID",
    )
    canonical_active = wire.canonical_physical_full_matrix_v4_witness_anchor_genesis_bytes(
        facts.genesis
    )
    previous_record_sha256 = _ZERO_SHA256
    completed_replay_ids: set[str] = set()
    records: list[_Record] = []
    expected_sequence = facts.genesis.sequence + 1
    for filename_sequence, filename_head_sha256, name in names:
        payload = _read_file_at(
            storage.records_fd,
            name,
            code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_RECORD_INVALID",
        )
        record = _record_from_payload(payload, facts=facts)
        if (
            record.sequence != expected_sequence
            or filename_sequence != record.sequence
            or filename_head_sha256 != record.head_sha256
            or record.previous_record_sha256 != previous_record_sha256
            or record.request_sha256 not in pending_by_request_sha256
        ):
            _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_RECORD_CHAIN_INVALID")
        pending = pending_by_request_sha256[record.request_sha256]
        if (
            pending.pending_sha256 != record.pending_sha256
            or pending.replay_id != record.replay_id
            or pending.canonical_request != record.canonical_request
            or pending.predecessor_sequence != active.sequence
            or pending.predecessor_head_sha256 != active.head_sha256
            or record.replay_id in completed_replay_ids
        ):
            _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_RECORD_CHAIN_INVALID")
        parsed_request = _wire_or_fail(
            lambda: wire.parse_physical_full_matrix_v4_witness_anchor_controller_append_request(
                record.canonical_request
            ),
            code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_RECORD_WIRE_INVALID",
        )
        # Immutable records carry no artificial expiry timestamp.  A stored
        # request is re-checked at its signed issuance instant, then the
        # durable immutable append is chain-verified.  This proves the exact
        # request/head binding without making a restart depend on a past TTL.
        verified_request = _wire_or_fail(
            lambda: wire.verify_physical_full_matrix_v4_witness_anchor_controller_append_request(
                record.canonical_request,
                policy=facts.policy,
                predecessor=active,
                now=parsed_request.issued_at,
                seen_replay_ids=completed_replay_ids,
            ),
            code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_RECORD_WIRE_INVALID",
        )
        verified_head = _wire_or_fail(
            lambda: wire.verify_physical_full_matrix_v4_witness_anchor_immutable_head(
                record.canonical_immutable_head,
                policy=facts.policy,
                expected_predecessor=active,
                append_request=verified_request,
                now=parsed_request.issued_at,
            ),
            code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_RECORD_WIRE_INVALID",
        )
        if (
            verified_request.replay_id != record.replay_id
            or verified_request.request_sha256 != record.request_sha256
            or verified_request.commitment_sha256 != record.commitment_sha256
            or verified_head.head_sha256 != record.head_sha256
            or verified_head.canonical_immutable_head != record.canonical_immutable_head
        ):
            _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_RECORD_CHAIN_INVALID")
        completed_replay_ids.add(record.replay_id)
        active = verified_head
        canonical_active = record.canonical_immutable_head
        previous_record_sha256 = record.record_sha256
        expected_sequence += 1
        records.append(record)

    completed_requests = {item.request_sha256 for item in records}
    if set(pending_by_request_sha256) != completed_requests:
        # A request was durably accepted but its Witness head/record was not.
        # It is irreconcilable automatically and must never be re-signed/retried.
        _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_PENDING_INDETERMINATE")
    has_current = _current_exists(storage.root_fd)
    if not records:
        if has_current:
            _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_CURRENT_ROLLBACK")
        return active, canonical_active, (), frozenset()
    if not has_current:
        # The completed immutable head is already self-verifying and paired
        # with its durable pending request.  Rebuild only the derived pointer
        # exactly from that record; no request is retried and no head is signed.
        final = records[-1]
        _write_current_atomic(
            storage.root_fd,
            _current_payload(
                facts=facts,
                current=_Current(
                    sequence=final.sequence,
                    head_sha256=final.head_sha256,
                    record_sha256=final.record_sha256,
                ),
            ),
        )
    current = _current_from_payload(
        _read_file_at(
            storage.root_fd,
            _CURRENT_FILENAME,
            code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_CURRENT_INVALID",
        ),
        facts=facts,
    )
    final = records[-1]
    if (
        current.sequence != final.sequence
        or current.head_sha256 != final.head_sha256
        or current.record_sha256 != final.record_sha256
        or active.sequence != current.sequence
        or active.head_sha256 != current.head_sha256
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_CURRENT_ROLLBACK")
    return active, canonical_active, tuple(records), frozenset(completed_replay_ids)


def _require_current_fresh(
    value: (
        wire.VerifiedPhysicalFullMatrixV4WitnessAnchorHead
        | wire.VerifiedPhysicalFullMatrixV4WitnessAnchorImmutableHead
    ),
    *,
    now: datetime,
) -> None:
    del now
    if (
        type(value)
        not in {
            wire.VerifiedPhysicalFullMatrixV4WitnessAnchorHead,
            wire.VerifiedPhysicalFullMatrixV4WitnessAnchorImmutableHead,
        }
        or value.sequence < 0
        or _SHA256_RE.fullmatch(value.head_sha256) is None
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_CURRENT_INVALID")


class RootOwnedPhysicalFullMatrixV4WitnessAnchorLedger:
    """V4-only local Witness service with no transport implementation.

    The service is intentionally shaped for a narrow adapter:

    * ``read_signed_head(policy_identity=..., read_challenge=...) -> bytes``
    * ``append_signed_request(policy_identity=..., canonical_controller_append_request=...,
      read_challenge=...) -> bytes``

    Both operations return the V4 transport envelope: exact configured genesis
    or immutable append evidence plus a fresh challenge-bound observation.
    A transport adapter may move those bytes but cannot alter the stable
    record, manufacture a challenge, or make state decisions.
    """

    def __init__(
        self,
        config: RootOwnedPhysicalFullMatrixV4WitnessAnchorLedgerConfig,
        *,
        root_signer: PhysicalFullMatrixV4WitnessAnchorLedgerRootSigner,
        trusted_clock: PhysicalFullMatrixV4WitnessAnchorLedgerClock,
    ) -> None:
        self._config = config
        self._root_signer = root_signer
        self._trusted_clock = trusted_clock

    def _facts(self, now: datetime) -> _Facts:
        return _facts(self._config, signer=self._root_signer, now=now)

    @staticmethod
    def _require_policy_identity(
        *,
        policy_identity: wire.PhysicalFullMatrixV4WitnessAnchorPolicyIdentity,
        facts: _Facts,
    ) -> None:
        """Accept only the adapter's full pinned V4 identity shape.

        This deliberately avoids importing the adapter (and a circular
        dependency), accepting a bare binding string, or duck-typing a generic
        identity object.  Every non-secret campaign/genesis pin is checked.
        """

        if type(policy_identity) is not wire.PhysicalFullMatrixV4WitnessAnchorPolicyIdentity:
            _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_POLICY_IDENTITY_MISMATCH")
        expected_genesis_sha256 = hashlib.sha256(
            wire.canonical_physical_full_matrix_v4_witness_anchor_genesis_bytes(
                facts.genesis
            )
        ).hexdigest()
        if (
            policy_identity.schema
            != wire.PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_POLICY_IDENTITY_SCHEMA
            or policy_identity.journal_binding_sha256 != facts.journal_binding_sha256
            or policy_identity.baseline_plan_binding_sha256
            != facts.baseline_plan_binding_sha256
            or policy_identity.run_id != facts.genesis.run_id
            or policy_identity.plan_sha256 != facts.genesis.plan_sha256
            or type(policy_identity.anchor_genesis_sequence) is not int
            or policy_identity.anchor_genesis_sequence != facts.genesis.sequence
            or policy_identity.anchor_genesis_head_sha256 != facts.genesis.head_sha256
            or policy_identity.canonical_genesis_sha256 != expected_genesis_sha256
        ):
            _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_POLICY_IDENTITY_MISMATCH")

    @staticmethod
    def _require_read_challenge(read_challenge: object) -> str:
        return _sha256(
            read_challenge,
            code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_READ_CHALLENGE_INVALID",
        )

    def _fresh_transport_envelope(
        self,
        *,
        facts: _Facts,
        active: (
            wire.VerifiedPhysicalFullMatrixV4WitnessAnchorHead
            | wire.VerifiedPhysicalFullMatrixV4WitnessAnchorImmutableHead
        ),
        canonical_anchor_head: bytes,
        read_challenge: str,
        now: datetime,
    ) -> bytes:
        """Sign a short-lived read proof only; never mutate the append chain."""

        expires_at = now + timedelta(
            seconds=facts.policy.maximum_attestation_lifetime_seconds
        )
        signing_payload = _wire_or_fail(
            lambda: wire.prepare_physical_full_matrix_v4_witness_anchor_read_observation(
                policy=facts.policy,
                anchor_head=active,
                read_challenge=read_challenge,
                observation_id=secrets.token_bytes(32).hex(),
                observed_at=now,
                expires_at=expires_at,
            ),
            code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_OBSERVATION_PREPARE_FAILED",
        )
        callback = getattr(self._root_signer, "sign_read_observation", None)
        if not callable(callback):
            _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_SIGNER_MISSING")
        try:
            signature = callback(
                canonical_signed_read_observation=(
                    signing_payload.canonical_signed_read_observation
                )
            )
        except Exception as exc:
            raise PhysicalFullMatrixV4WitnessAnchorLedgerError(
                "PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_OBSERVATION_SIGNER_FAILED"
            ) from exc
        after_sign = _trusted_now(self._trusted_clock, floor=now)
        self._facts(after_sign)
        canonical_observation = _wire_or_fail(
            lambda: wire.finalize_physical_full_matrix_v4_witness_anchor_read_observation(
                policy=facts.policy,
                anchor_head=active,
                signing_payload=signing_payload,
                witness_signature=signature,
                now=after_sign,
            ),
            code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_OBSERVATION_OUTPUT_INVALID",
        )
        return _wire_or_fail(
            lambda: wire.build_physical_full_matrix_v4_witness_anchor_transport_envelope(
                canonical_anchor_head=canonical_anchor_head,
                canonical_read_observation=canonical_observation,
                read_challenge=read_challenge,
            ),
            code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_TRANSPORT_INVALID",
        )

    def read_signed_head(
        self,
        *,
        policy_identity: wire.PhysicalFullMatrixV4WitnessAnchorPolicyIdentity,
        read_challenge: str,
    ) -> bytes:
        """Return immutable evidence with one fresh caller-challenge read proof."""

        _require_root()
        before = _trusted_now(self._trusted_clock)
        facts = self._facts(before)
        self._require_policy_identity(policy_identity=policy_identity, facts=facts)
        challenge = self._require_read_challenge(read_challenge)
        with _locked_storage() as storage:
            _ensure_binding(storage, facts=facts)
            active, canonical_head, _records, _replays = _load_state(
                storage,
                facts=facts,
                now=before,
            )
            after = _trusted_now(self._trusted_clock, floor=before)
            _require_current_fresh(active, now=after)
            return self._fresh_transport_envelope(
                facts=facts,
                active=active,
                canonical_anchor_head=canonical_head,
                read_challenge=challenge,
                now=after,
            )

    def append_signed_request(
        self,
        *,
        policy_identity: wire.PhysicalFullMatrixV4WitnessAnchorPolicyIdentity,
        canonical_controller_append_request: bytes,
        read_challenge: str,
    ) -> bytes:
        """Durably append immutable evidence and return a fresh read envelope.

        A request is first stored in the private ``pending`` set.  Any crash
        before its paired completed head is an explicit indeterminate state;
        the request is not retried.  A crash after completed-record fsync but
        before the derived current pointer is recovered exactly from that
        record, without signing or replaying anything.
        """

        _require_root()
        before = _trusted_now(self._trusted_clock)
        facts = self._facts(before)
        self._require_policy_identity(policy_identity=policy_identity, facts=facts)
        challenge = self._require_read_challenge(read_challenge)
        if type(canonical_controller_append_request) is not bytes:
            _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_REQUEST_INVALID")
        with _locked_storage() as storage:
            _ensure_binding(storage, facts=facts)
            active, _canonical_active, records, replay_ids = _load_state(
                storage,
                facts=facts,
                now=before,
            )
            _require_current_fresh(active, now=before)
            verified_request = _wire_or_fail(
                lambda: wire.verify_physical_full_matrix_v4_witness_anchor_controller_append_request(
                    canonical_controller_append_request,
                    policy=facts.policy,
                    predecessor=active,
                    now=before,
                    seen_replay_ids=replay_ids,
                ),
                code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_REQUEST_INVALID",
            )
            pending_payload, pending_sha256 = _pending_payload(
                facts=facts,
                replay_id=verified_request.replay_id,
                request_sha256=verified_request.request_sha256,
                predecessor_sequence=active.sequence,
                predecessor_head_sha256=active.head_sha256,
                canonical_request=verified_request.canonical_request,
            )
            _write_create_only_at(
                storage.pending_fd,
                verified_request.request_sha256 + ".json",
                pending_payload,
                code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_PENDING_WRITE_FAILED",
            )
            after_pending = _trusted_now(self._trusted_clock, floor=before)
            _require_current_fresh(active, now=after_pending)
            signing_payload = _wire_or_fail(
                lambda: wire.prepare_physical_full_matrix_v4_witness_anchor_immutable_head(
                    policy=facts.policy,
                    predecessor=active,
                    append_request=verified_request,
                    now=after_pending,
                ),
                code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_IMMUTABLE_PREPARE_FAILED",
            )
            callback = getattr(self._root_signer, "sign_immutable_anchor_head", None)
            if not callable(callback):
                _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_SIGNER_MISSING")
            try:
                signature = callback(
                    canonical_signed_immutable_head=(
                        signing_payload.canonical_signed_immutable_head
                    )
                )
            except Exception as exc:
                raise PhysicalFullMatrixV4WitnessAnchorLedgerError(
                    "PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_SIGNER_FAILED"
                ) from exc
            after_sign = _trusted_now(self._trusted_clock, floor=after_pending)
            # Do not let a signer/key implementation switch underneath its
            # output; finalization additionally verifies the exact signature.
            self._facts(after_sign)
            if after_sign > verified_request.expires_at:
                _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_REQUEST_EXPIRED_BEFORE_SIGN")
            canonical_immutable_head = _wire_or_fail(
                lambda: wire.finalize_physical_full_matrix_v4_witness_anchor_immutable_head(
                    policy=facts.policy,
                    signing_payload=signing_payload,
                    witness_signature=signature,
                    now=after_sign,
                ),
                code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_SIGNER_OUTPUT_INVALID",
            )
            verified_head = _wire_or_fail(
                lambda: wire.verify_physical_full_matrix_v4_witness_anchor_immutable_head(
                    canonical_immutable_head,
                    policy=facts.policy,
                    expected_predecessor=active,
                    append_request=verified_request,
                    now=after_sign,
                ),
                code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_IMMUTABLE_INVALID",
            )
            previous_record_sha256 = _ZERO_SHA256 if not records else records[-1].record_sha256
            record_payload, record_sha256 = _record_payload(
                facts=facts,
                sequence=verified_head.sequence,
                previous_record_sha256=previous_record_sha256,
                pending_sha256=pending_sha256,
                replay_id=verified_request.replay_id,
                request_sha256=verified_request.request_sha256,
                commitment_sha256=verified_request.commitment_sha256,
                head_sha256=verified_head.head_sha256,
                canonical_request=verified_request.canonical_request,
                canonical_immutable_head=canonical_immutable_head,
            )
            name = f"{verified_head.sequence:020d}-{verified_head.head_sha256}.json"
            _write_create_only_at(
                storage.records_fd,
                name,
                record_payload,
                code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_LEDGER_RECORD_WRITE_FAILED",
            )
            _write_current_atomic(
                storage.root_fd,
                _current_payload(
                    facts=facts,
                    current=_Current(
                        sequence=verified_head.sequence,
                        head_sha256=verified_head.head_sha256,
                        record_sha256=record_sha256,
                    ),
                ),
            )
            after_commit = _trusted_now(self._trusted_clock, floor=after_sign)
            _require_current_fresh(verified_head, now=after_commit)
            return self._fresh_transport_envelope(
                facts=facts,
                active=verified_head,
                canonical_anchor_head=canonical_immutable_head,
                read_challenge=challenge,
                now=after_commit,
            )
