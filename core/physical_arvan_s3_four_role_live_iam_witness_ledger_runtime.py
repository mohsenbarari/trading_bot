"""Root-owned append-only durable runtime for live four-role IAM evidence.

The pure :mod:`physical_arvan_s3_four_role_live_iam_evidence` module defines
the Witness nonce state machine but intentionally has no durable boundary.
This adapter supplies that narrow boundary.  Every transition is written as a
new, fsynced record plus a separate fsynced head record before the associated
signed permit or aggregate is returned to its caller:

``OPEN -> permit``, ``COMMITTED -> aggregate``, ``EXPIRED -> receipt``.

Records are create-only and are never compacted, deleted, or overwritten by
this runtime.  A record without its corresponding head is treated as a
crash-like partial tail and blocks use rather than being repaired silently.
The append chain, immutable head files, and a runtime-held expected head catch
fork/replay/rollback within an active process.  As with every local durable
store, a hostile root that removes *all* independently stored anchors before a
fresh process opens it cannot be distinguished cryptographically; the runtime
therefore never claims to provide remote anti-rollback authority.

No network, Object Storage, S3 SDK, credential loading, direct-site API, or
signer persistence exists here.  A caller supplies an already-loaded Witness
signer only for the pure transition that needs it; neither private key bytes,
permit bytes, nor aggregate bytes are persisted.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any

from core.append_only_sync_delta_batch import canonical_json_bytes
from core import physical_arvan_s3_four_role_live_iam_evidence as _live_iam


__all__ = (
    "DEFAULT_PHYSICAL_ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_MAXIMUM_RECORDS",
    "PHYSICAL_ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_DEFAULT_ENABLED",
    "PHYSICAL_ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_RUNTIME_SCHEMA",
    "PhysicalArvanS3FourRoleLiveIamWitnessLedgerRuntime",
    "PhysicalArvanS3FourRoleLiveIamWitnessLedgerRuntimeConfig",
    "PhysicalArvanS3FourRoleLiveIamWitnessLedgerRuntimeError",
    "VerifiedPhysicalArvanS3FourRoleLiveIamWitnessLedgerState",
    "expire_physical_arvan_s3_four_role_live_iam_witness_ledger_nonce",
    "issue_physical_arvan_s3_four_role_live_iam_witness_ledger_nonce_permit",
    "open_physical_arvan_s3_four_role_live_iam_witness_ledger_runtime",
    "read_physical_arvan_s3_four_role_live_iam_witness_ledger_state",
    "seal_physical_arvan_s3_four_role_live_iam_witness_ledger_aggregate",
    "verify_physical_arvan_s3_four_role_live_iam_witness_ledger_aggregate",
)


PHYSICAL_ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_RUNTIME_SCHEMA = (
    "gold-trade-physical-arvan-s3-four-role-live-iam-witness-ledger-runtime-v1"
)
PHYSICAL_ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_DEFAULT_ENABLED = False
DEFAULT_PHYSICAL_ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_MAXIMUM_RECORDS = 256
_MAXIMUM_RECORDS = 1_024
_MAX_FILE_BYTES = 256 * 1024
_LEDGER_DIRECTORY = "physical-arvan-s3-four-role-live-iam-witness-ledger-v1"
_RECORDS_DIRECTORY = "records"
_HEADS_DIRECTORY = "heads"
_BINDING_FILENAME = "binding.json"
_LOCK_FILENAME = "ledger.lock"
_RECORD_SCHEMA = "gold-trade-physical-arvan-s3-four-role-live-iam-witness-ledger-record-v1"
_HEAD_SCHEMA = "gold-trade-physical-arvan-s3-four-role-live-iam-witness-ledger-head-v1"
_BINDING_SCHEMA = "gold-trade-physical-arvan-s3-four-role-live-iam-witness-ledger-binding-v1"
_TRANSITIONS = frozenset({"open", "committed", "expired"})
_RECORD_RE = re.compile(r"^([0-9]{20})-([0-9a-f]{64})\.json$", re.ASCII)
_HEAD_RE = re.compile(r"^([0-9]{20})-([0-9a-f]{64})\.head$", re.ASCII)
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_RUNTIME_CAPABILITY = object()
_STATE_CAPABILITY = object()


class PhysicalArvanS3FourRoleLiveIamWitnessLedgerRuntimeError(RuntimeError):
    """Fixed-code failure at the root-owned append-only Witness boundary."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class PhysicalArvanS3FourRoleLiveIamWitnessLedgerRuntimeConfig:
    """Default-off root-owned state boundary for one exact evidence binding."""

    state_root: Path | None = None
    evidence_binding: _live_iam.PhysicalArvanS3FourRoleLiveIamEvidenceBinding | None = None
    enabled: bool = PHYSICAL_ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_DEFAULT_ENABLED
    maximum_records: int = DEFAULT_PHYSICAL_ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_MAXIMUM_RECORDS


@dataclass(frozen=True)
class VerifiedPhysicalArvanS3FourRoleLiveIamWitnessLedgerState:
    """Opaque receipt for one fsynced immutable append-chain head."""

    schema: str
    evidence_binding_sha256: str
    sequence: int
    head_sha256: str
    ledger_sha256: str
    logical_record_count: int
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_STATE_SERIALIZATION_FORBIDDEN")


class PhysicalArvanS3FourRoleLiveIamWitnessLedgerRuntime:
    """Nonserializable handle; all use goes through the narrow functions below."""

    __slots__ = ("_normalised", "_expected_head_sha256", "_capability")

    def __init__(self, normalised: "_NormalisedConfig", expected_head_sha256: str, capability: object) -> None:
        if capability is not _RUNTIME_CAPABILITY:
            raise TypeError("ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_RUNTIME_CONSTRUCTION_FORBIDDEN")
        self._normalised = normalised
        self._expected_head_sha256 = expected_head_sha256
        self._capability = capability

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_RUNTIME_SERIALIZATION_FORBIDDEN")


@dataclass(frozen=True)
class _NormalisedConfig:
    state_root: Path
    ledger_directory: Path
    records_directory: Path
    heads_directory: Path
    binding: _live_iam.PhysicalArvanS3FourRoleLiveIamEvidenceBinding
    binding_metadata: dict[str, Any]
    binding_metadata_sha256: str
    maximum_records: int


@dataclass(frozen=True)
class _LoadedState:
    ledger: _live_iam.PhysicalArvanS3FourRoleLiveIamNonceLedger
    sequence: int
    head_sha256: str
    ledger_sha256: str


def _fail(code: str) -> None:
    raise PhysicalArvanS3FourRoleLiveIamWitnessLedgerRuntimeError(code)


def _canonical(value: object, *, code: str) -> bytes:
    try:
        return canonical_json_bytes(value)
    except (TypeError, ValueError):
        _fail(code)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256(value: object, *, code: str) -> str:
    if type(value) is not str or _HEX64_RE.fullmatch(value) is None or value == "0" * 64:
        _fail(code)
    return value


def _utc(value: object, *, code: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        _fail(code)
    return value.astimezone(timezone.utc)


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_JSON_INVALID")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    del value
    _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_JSON_INVALID")


def _parse_canonical_json(raw: bytes, *, code: str) -> dict[str, Any]:
    if type(raw) is not bytes or not raw or len(raw) > _MAX_FILE_BYTES:
        _fail(code)
    try:
        parsed = json.loads(
            raw.decode("ascii", "strict"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
        _fail(code)
    if type(parsed) is not dict or _canonical(parsed, code=code) != raw:
        _fail(code)
    return parsed


def _exact_mapping(value: object, *, fields: frozenset[str], code: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != fields:
        _fail(code)
    return dict(value)


def _validate_ancestors(path: Path) -> None:
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_PLATFORM_UNSAFE")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    descriptor = -1
    try:
        descriptor = os.open("/", flags)
        for component in path.parts[1:-1]:
            if component in {"", ".", ".."}:
                _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_STATE_ROOT_UNSAFE")
            next_descriptor = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
            info = os.fstat(descriptor)
            mode = stat.S_IMODE(info.st_mode)
            sticky_root_parent = info.st_uid == 0 and bool(info.st_mode & stat.S_ISVTX)
            if (
                not stat.S_ISDIR(info.st_mode)
                or info.st_uid != 0
                or (mode & 0o022 and not sticky_root_parent)
            ):
                _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_STATE_ROOT_UNSAFE")
    except PhysicalArvanS3FourRoleLiveIamWitnessLedgerRuntimeError:
        raise
    except OSError:
        _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_STATE_ROOT_UNSAFE")
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _secure_state_root(value: object) -> Path:
    if not isinstance(value, Path) or not value.is_absolute() or any(
        part in {"", ".", ".."} for part in value.parts[1:]
    ):
        _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_STATE_ROOT_UNSAFE")
    _validate_ancestors(value)
    try:
        info = os.lstat(value)
        resolved = value.resolve(strict=True)
    except OSError:
        _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_STATE_ROOT_UNSAFE")
    if (
        resolved != value
        or stat.S_ISLNK(info.st_mode)
        or not stat.S_ISDIR(info.st_mode)
        or info.st_uid != 0
        or stat.S_IMODE(info.st_mode) != 0o700
    ):
        _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_STATE_ROOT_UNSAFE")
    return resolved


def _check_directory(path: Path, *, code: str) -> Path:
    try:
        info = os.lstat(path)
        resolved = path.resolve(strict=True)
    except OSError:
        _fail(code)
    if (
        resolved != path
        or stat.S_ISLNK(info.st_mode)
        or not stat.S_ISDIR(info.st_mode)
        or info.st_uid != 0
        or stat.S_IMODE(info.st_mode) != 0o700
    ):
        _fail(code)
    return path


def _fsync_directory(path: Path) -> None:
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
        )
        os.fsync(descriptor)
    except OSError:
        _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_DIRECTORY_FSYNC_FAILED")
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _create_directory(parent: Path, name: str) -> Path:
    path = parent / name
    try:
        os.mkdir(path, mode=0o700)
    except FileExistsError:
        pass
    except OSError:
        _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_DIRECTORY_CREATE_FAILED")
    result = _check_directory(path, code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_DIRECTORY_UNSAFE")
    _fsync_directory(parent)
    return result


def _check_regular_file(path: Path, *, permit_empty: bool, code: str) -> os.stat_result:
    try:
        info = os.lstat(path)
    except OSError:
        _fail(code)
    if (
        stat.S_ISLNK(info.st_mode)
        or not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or info.st_uid != 0
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_size < 0
        or info.st_size > _MAX_FILE_BYTES
        or (not permit_empty and info.st_size < 1)
    ):
        _fail(code)
    return info


def _write_create_only(path: Path, payload: bytes, *, code: str) -> None:
    if type(payload) is not bytes or not 1 <= len(payload) <= _MAX_FILE_BYTES:
        _fail(code)
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
            0o600,
        )
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_uid != 0
            or stat.S_IMODE(info.st_mode) != 0o600
        ):
            _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_FILE_UNSAFE")
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                _fail(code)
            view = view[written:]
        os.fsync(descriptor)
    except PhysicalArvanS3FourRoleLiveIamWitnessLedgerRuntimeError:
        raise
    except FileExistsError:
        _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_APPEND_FORK")
    except OSError:
        _fail(code)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    _check_regular_file(path, permit_empty=False, code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_FILE_UNSAFE")
    _fsync_directory(path.parent)


def _read_file(path: Path, *, permit_empty: bool, code: str) -> bytes:
    if not hasattr(os, "O_NOFOLLOW"):
        _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_PLATFORM_UNSAFE")
    descriptor = -1
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0))
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_uid != 0
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_size > _MAX_FILE_BYTES
            or (not permit_empty and info.st_size < 1)
        ):
            _fail(code)
        remaining = info.st_size
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
    except PhysicalArvanS3FourRoleLiveIamWitnessLedgerRuntimeError:
        raise
    except OSError:
        _fail(code)
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _binding_metadata(binding: _live_iam.PhysicalArvanS3FourRoleLiveIamEvidenceBinding) -> dict[str, Any]:
    return {
        "schema": _BINDING_SCHEMA,
        "evidence_binding_sha256": binding.evidence_binding_sha256,
        "campaign_id": binding.campaign_id,
        "release_sha": binding.release_sha,
        "normal_route_scope_sha256": binding.normal_route_scope_sha256,
        "reverse_route_scope_sha256": binding.reverse_route_scope_sha256,
        "four_role_binding_sha256": binding.four_role_binding_sha256,
        "fi_publisher_identity_sha256": binding.fi_publisher_identity_sha256,
        "ir_receiver_identity_sha256": binding.ir_receiver_identity_sha256,
        "ir_publisher_identity_sha256": binding.ir_publisher_identity_sha256,
        "fi_receiver_identity_sha256": binding.fi_receiver_identity_sha256,
    }


def _normalise_config(value: object) -> _NormalisedConfig:
    if type(value) is not PhysicalArvanS3FourRoleLiveIamWitnessLedgerRuntimeConfig:
        _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_CONFIG_INVALID")
    if value.enabled is not True:
        _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_DISABLED")
    if os.geteuid() != 0:
        _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_ROOT_REQUIRED")
    if type(value.maximum_records) is not int or not 1 <= value.maximum_records <= _MAXIMUM_RECORDS:
        _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_LIMIT_INVALID")
    root = _secure_state_root(value.state_root)
    try:
        binding = _live_iam._require_binding(value.evidence_binding)
    except _live_iam.PhysicalArvanS3FourRoleLiveIamEvidenceError:
        _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_BINDING_INVALID")
    metadata = _binding_metadata(binding)
    metadata_sha256 = _sha256_bytes(_canonical(metadata, code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_BINDING_INVALID"))
    ledger_directory = root / _LEDGER_DIRECTORY
    return _NormalisedConfig(
        state_root=root,
        ledger_directory=ledger_directory,
        records_directory=ledger_directory / _RECORDS_DIRECTORY,
        heads_directory=ledger_directory / _HEADS_DIRECTORY,
        binding=binding,
        binding_metadata=metadata,
        binding_metadata_sha256=metadata_sha256,
        maximum_records=value.maximum_records,
    )


def _initialise_storage(config: _NormalisedConfig) -> None:
    directory = _create_directory(config.state_root, _LEDGER_DIRECTORY)
    _create_directory(directory, _RECORDS_DIRECTORY)
    _create_directory(directory, _HEADS_DIRECTORY)
    binding_path = directory / _BINDING_FILENAME
    binding_payload = _canonical(config.binding_metadata, code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_BINDING_INVALID")
    try:
        info = os.lstat(binding_path)
    except FileNotFoundError:
        try:
            _write_create_only(
                binding_path,
                binding_payload,
                code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_BINDING_WRITE_FAILED",
            )
        except PhysicalArvanS3FourRoleLiveIamWitnessLedgerRuntimeError as exc:
            # A concurrent first opener may safely win the O_EXCL race.  It
            # is accepted only when its exact immutable binding bytes match;
            # no in-place repair or overwrite is attempted.
            if exc.code != "ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_APPEND_FORK" or _read_file(
                binding_path,
                permit_empty=False,
                code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_BINDING_UNSAFE",
            ) != binding_payload:
                _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_FOREIGN_BINDING")
    except OSError:
        _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_BINDING_UNSAFE")
    else:
        if stat.S_ISLNK(info.st_mode):
            _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_BINDING_UNSAFE")
        if _read_file(binding_path, permit_empty=False, code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_BINDING_UNSAFE") != binding_payload:
            _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_FOREIGN_BINDING")
    lock_path = directory / _LOCK_FILENAME
    try:
        os.lstat(lock_path)
    except FileNotFoundError:
        try:
            _write_create_only(
                lock_path,
                b"0",
                code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_LOCK_CREATE_FAILED",
            )
        except PhysicalArvanS3FourRoleLiveIamWitnessLedgerRuntimeError as exc:
            if exc.code != "ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_APPEND_FORK":
                raise
    except OSError:
        _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_LOCK_UNSAFE")
    _check_regular_file(lock_path, permit_empty=False, code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_LOCK_UNSAFE")
    _check_directory(config.ledger_directory, code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_DIRECTORY_UNSAFE")
    _check_directory(config.records_directory, code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_DIRECTORY_UNSAFE")
    _check_directory(config.heads_directory, code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_DIRECTORY_UNSAFE")


def _open_lock(config: _NormalisedConfig) -> int:
    _check_directory(
        config.ledger_directory,
        code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_DIRECTORY_UNSAFE",
    )
    path = config.ledger_directory / _LOCK_FILENAME
    descriptor = -1
    try:
        descriptor = os.open(path, os.O_RDWR | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0))
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_uid != 0
            or stat.S_IMODE(info.st_mode) != 0o600
        ):
            _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_LOCK_UNSAFE")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        return descriptor
    except PhysicalArvanS3FourRoleLiveIamWitnessLedgerRuntimeError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError:
        if descriptor >= 0:
            os.close(descriptor)
        _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_LOCK_OPEN_FAILED")


@contextmanager
def _locked(config: _NormalisedConfig) -> Iterator[None]:
    descriptor = _open_lock(config)
    try:
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _scan_directory(path: Path, *, pattern: re.Pattern[str], code: str) -> dict[int, tuple[str, Path]]:
    result: dict[int, tuple[str, Path]] = {}
    try:
        with os.scandir(path) as entries:
            for entry in entries:
                match = pattern.fullmatch(entry.name)
                if match is None:
                    _fail(code)
                entry_path = path / entry.name
                info = entry.stat(follow_symlinks=False)
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_nlink != 1
                    or info.st_uid != 0
                    or stat.S_IMODE(info.st_mode) != 0o600
                ):
                    _fail(code)
                sequence = int(match.group(1))
                digest = match.group(2)
                if sequence < 1 or sequence in result:
                    _fail(code)
                result[sequence] = (digest, entry_path)
    except PhysicalArvanS3FourRoleLiveIamWitnessLedgerRuntimeError:
        raise
    except OSError:
        _fail(code)
    return result


def _b64decode(value: object, *, code: str) -> bytes:
    if type(value) is not str:
        _fail(code)
    try:
        result = base64.b64decode(value.encode("ascii"), validate=True)
    except (UnicodeEncodeError, binascii.Error):
        _fail(code)
    if not result or len(result) > _MAX_FILE_BYTES:
        _fail(code)
    return result


def _record_unsigned(
    *,
    sequence: int,
    previous_head_sha256: str,
    transition: str,
    ledger_sha256: str,
    ledger_payload_base64: str,
) -> dict[str, Any]:
    return {
        "schema": _RECORD_SCHEMA,
        "sequence": sequence,
        "previous_head_sha256": previous_head_sha256,
        "transition": transition,
        "ledger_sha256": ledger_sha256,
        "ledger_payload_base64": ledger_payload_base64,
    }


def _head_unsigned(*, sequence: int, previous_head_sha256: str, record_sha256: str) -> dict[str, Any]:
    return {
        "schema": _HEAD_SCHEMA,
        "sequence": sequence,
        "previous_head_sha256": previous_head_sha256,
        "record_sha256": record_sha256,
    }


def _record_from_path(
    *,
    path: Path,
    filename_sha256: str,
    config: _NormalisedConfig,
) -> tuple[int, str, str, _live_iam.PhysicalArvanS3FourRoleLiveIamNonceLedger]:
    item = _parse_canonical_json(
        _read_file(path, permit_empty=False, code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_RECORD_READ_FAILED"),
        code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_RECORD_INVALID",
    )
    fields = frozenset(
        {
            "schema",
            "sequence",
            "previous_head_sha256",
            "transition",
            "ledger_sha256",
            "ledger_payload_base64",
            "record_sha256",
        }
    )
    record = _exact_mapping(item, fields=fields, code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_RECORD_INVALID")
    if record["schema"] != _RECORD_SCHEMA or type(record["sequence"]) is not int or record["sequence"] < 1:
        _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_RECORD_INVALID")
    previous_head = _sha256(record["previous_head_sha256"], code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_RECORD_INVALID")
    if record["transition"] not in _TRANSITIONS:
        _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_RECORD_INVALID")
    ledger_sha256 = _sha256(record["ledger_sha256"], code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_RECORD_INVALID")
    payload = _b64decode(record["ledger_payload_base64"], code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_RECORD_INVALID")
    if _sha256_bytes(payload) != ledger_sha256:
        _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_RECORD_INVALID")
    try:
        ledger = _live_iam.parse_physical_arvan_s3_four_role_live_iam_nonce_ledger(
            payload, binding=config.binding
        )
        if _live_iam.serialize_physical_arvan_s3_four_role_live_iam_nonce_ledger(
            ledger, binding=config.binding
        ) != payload:
            _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_RECORD_INVALID")
    except _live_iam.PhysicalArvanS3FourRoleLiveIamEvidenceError:
        _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_RECORD_INVALID")
    unsigned = _record_unsigned(
        sequence=record["sequence"],
        previous_head_sha256=previous_head,
        transition=record["transition"],
        ledger_sha256=ledger_sha256,
        ledger_payload_base64=record["ledger_payload_base64"],
    )
    record_sha256 = _sha256_bytes(_canonical(unsigned, code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_RECORD_INVALID"))
    if _sha256(record["record_sha256"], code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_RECORD_INVALID") != record_sha256 or record_sha256 != filename_sha256:
        _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_RECORD_INVALID")
    return record["sequence"], record_sha256, record["transition"], ledger


def _head_from_path(
    *, path: Path, filename_sha256: str
) -> tuple[int, str, str, str]:
    item = _parse_canonical_json(
        _read_file(path, permit_empty=False, code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_HEAD_READ_FAILED"),
        code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_HEAD_INVALID",
    )
    head = _exact_mapping(
        item,
        fields=frozenset({"schema", "sequence", "previous_head_sha256", "record_sha256", "head_sha256"}),
        code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_HEAD_INVALID",
    )
    if head["schema"] != _HEAD_SCHEMA or type(head["sequence"]) is not int or head["sequence"] < 1:
        _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_HEAD_INVALID")
    previous_head = _sha256(head["previous_head_sha256"], code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_HEAD_INVALID")
    record_sha256 = _sha256(head["record_sha256"], code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_HEAD_INVALID")
    unsigned = _head_unsigned(
        sequence=head["sequence"], previous_head_sha256=previous_head, record_sha256=record_sha256
    )
    head_sha256 = _sha256_bytes(_canonical(unsigned, code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_HEAD_INVALID"))
    if _sha256(head["head_sha256"], code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_HEAD_INVALID") != head_sha256 or head_sha256 != filename_sha256:
        _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_HEAD_INVALID")
    return head["sequence"], previous_head, record_sha256, head_sha256


def _validate_transition(
    *,
    previous: _live_iam.PhysicalArvanS3FourRoleLiveIamNonceLedger,
    current: _live_iam.PhysicalArvanS3FourRoleLiveIamNonceLedger,
    transition: str,
) -> None:
    before = previous.records
    after = current.records
    if transition == "open":
        if len(after) != len(before) + 1 or after[:-1] != before or after[-1].status != "open":
            _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_TRANSITION_INVALID")
        return
    if len(after) != len(before) or not after:
        _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_TRANSITION_INVALID")
    differences = [index for index, (old, new) in enumerate(zip(before, after)) if old != new]
    if differences != [len(after) - 1] or before[-1].status != "open" or after[-1].status != transition:
        _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_TRANSITION_INVALID")
    if transition == "committed" and after[-1].aggregate_sha256 is None:
        _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_TRANSITION_INVALID")
    if transition == "expired" and after[-1].retired_at is None:
        _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_TRANSITION_INVALID")


def _load_state(config: _NormalisedConfig) -> _LoadedState:
    _check_directory(
        config.ledger_directory,
        code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_DIRECTORY_UNSAFE",
    )
    _check_directory(
        config.records_directory,
        code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_DIRECTORY_UNSAFE",
    )
    _check_directory(
        config.heads_directory,
        code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_DIRECTORY_UNSAFE",
    )
    metadata_path = config.ledger_directory / _BINDING_FILENAME
    expected_metadata = _canonical(config.binding_metadata, code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_BINDING_INVALID")
    if _read_file(metadata_path, permit_empty=False, code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_BINDING_UNSAFE") != expected_metadata:
        _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_FOREIGN_BINDING")
    records = _scan_directory(
        config.records_directory,
        pattern=_RECORD_RE,
        code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_RECORD_DIRECTORY_INVALID",
    )
    heads = _scan_directory(
        config.heads_directory,
        pattern=_HEAD_RE,
        code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_HEAD_DIRECTORY_INVALID",
    )
    if set(records) != set(heads):
        _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_PARTIAL_TAIL")
    if len(records) > config.maximum_records:
        _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_RECORD_LIMIT_EXCEEDED")
    expected_sequences = set(range(1, len(records) + 1))
    if set(records) != expected_sequences:
        _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_SEQUENCE_FORK")
    try:
        current = _live_iam.make_physical_arvan_s3_four_role_live_iam_nonce_ledger(binding=config.binding)
    except _live_iam.PhysicalArvanS3FourRoleLiveIamEvidenceError:
        _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_BINDING_INVALID")
    previous_head = config.binding_metadata_sha256
    for sequence in range(1, len(records) + 1):
        record_filename_sha, record_path = records[sequence]
        head_filename_sha, head_path = heads[sequence]
        record_sequence, record_sha, transition, next_ledger = _record_from_path(
            path=record_path, filename_sha256=record_filename_sha, config=config
        )
        head_sequence, head_previous, head_record_sha, head_sha = _head_from_path(
            path=head_path, filename_sha256=head_filename_sha
        )
        if (
            record_sequence != sequence
            or head_sequence != sequence
            or head_previous != previous_head
            or head_record_sha != record_sha
        ):
            _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_CHAIN_FORK")
        # Re-read the record's predecessor pin directly from its canonical raw
        # mapping so a valid head cannot be paired with a record from another
        # sequence.
        record_mapping = _parse_canonical_json(
            _read_file(record_path, permit_empty=False, code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_RECORD_READ_FAILED"),
            code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_RECORD_INVALID",
        )
        if record_mapping.get("previous_head_sha256") != previous_head:
            _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_CHAIN_FORK")
        _validate_transition(previous=current, current=next_ledger, transition=transition)
        current = next_ledger
        previous_head = head_sha
    payload = _live_iam.serialize_physical_arvan_s3_four_role_live_iam_nonce_ledger(
        current, binding=config.binding
    )
    return _LoadedState(
        ledger=current,
        sequence=len(records),
        head_sha256=previous_head,
        ledger_sha256=_sha256_bytes(payload),
    )


def _append_transition(
    *,
    config: _NormalisedConfig,
    state: _LoadedState,
    next_ledger: _live_iam.PhysicalArvanS3FourRoleLiveIamNonceLedger,
    transition: str,
) -> _LoadedState:
    if transition not in _TRANSITIONS:
        _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_TRANSITION_INVALID")
    if state.sequence >= config.maximum_records:
        _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_RECORD_LIMIT_EXCEEDED")
    _validate_transition(previous=state.ledger, current=next_ledger, transition=transition)
    try:
        payload = _live_iam.serialize_physical_arvan_s3_four_role_live_iam_nonce_ledger(
            next_ledger, binding=config.binding
        )
    except _live_iam.PhysicalArvanS3FourRoleLiveIamEvidenceError:
        _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_TRANSITION_INVALID")
    sequence = state.sequence + 1
    ledger_sha256 = _sha256_bytes(payload)
    encoded = base64.b64encode(payload).decode("ascii")
    unsigned_record = _record_unsigned(
        sequence=sequence,
        previous_head_sha256=state.head_sha256,
        transition=transition,
        ledger_sha256=ledger_sha256,
        ledger_payload_base64=encoded,
    )
    record_sha256 = _sha256_bytes(_canonical(unsigned_record, code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_RECORD_INVALID"))
    record_payload = _canonical(
        {**unsigned_record, "record_sha256": record_sha256},
        code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_RECORD_INVALID",
    )
    unsigned_head = _head_unsigned(
        sequence=sequence,
        previous_head_sha256=state.head_sha256,
        record_sha256=record_sha256,
    )
    head_sha256 = _sha256_bytes(_canonical(unsigned_head, code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_HEAD_INVALID"))
    head_payload = _canonical(
        {**unsigned_head, "head_sha256": head_sha256},
        code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_HEAD_INVALID",
    )
    sequence_text = f"{sequence:020d}"
    # The record comes first.  If the process crashes before the head file is
    # fsynced, reload refuses the durable partial tail rather than releasing
    # the associated permit/aggregate or silently deleting it.
    _write_create_only(
        config.records_directory / f"{sequence_text}-{record_sha256}.json",
        record_payload,
        code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_RECORD_WRITE_FAILED",
    )
    _write_create_only(
        config.heads_directory / f"{sequence_text}-{head_sha256}.head",
        head_payload,
        code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_HEAD_WRITE_FAILED",
    )
    loaded = _load_state(config)
    if (
        loaded.sequence != sequence
        or loaded.head_sha256 != head_sha256
        or loaded.ledger_sha256 != ledger_sha256
        or loaded.ledger != next_ledger
    ):
        _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_DURABILITY_VERIFY_FAILED")
    return loaded


def _state_receipt(state: _LoadedState, *, config: _NormalisedConfig) -> VerifiedPhysicalArvanS3FourRoleLiveIamWitnessLedgerState:
    result = VerifiedPhysicalArvanS3FourRoleLiveIamWitnessLedgerState(
        schema=PHYSICAL_ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_RUNTIME_SCHEMA,
        evidence_binding_sha256=config.binding.evidence_binding_sha256,
        sequence=state.sequence,
        head_sha256=state.head_sha256,
        ledger_sha256=state.ledger_sha256,
        logical_record_count=len(state.ledger.records),
    )
    object.__setattr__(result, "_capability", _STATE_CAPABILITY)
    return result


def _require_runtime(value: object) -> PhysicalArvanS3FourRoleLiveIamWitnessLedgerRuntime:
    if (
        type(value) is not PhysicalArvanS3FourRoleLiveIamWitnessLedgerRuntime
        or value._capability is not _RUNTIME_CAPABILITY
        or type(value._normalised) is not _NormalisedConfig
    ):
        _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_RUNTIME_INVALID")
    return value


def _load_runtime_state(runtime: PhysicalArvanS3FourRoleLiveIamWitnessLedgerRuntime) -> _LoadedState:
    loaded = _load_state(runtime._normalised)
    if loaded.head_sha256 != runtime._expected_head_sha256:
        _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_HEAD_ROLLBACK_OR_FORK")
    return loaded


def open_physical_arvan_s3_four_role_live_iam_witness_ledger_runtime(
    config: PhysicalArvanS3FourRoleLiveIamWitnessLedgerRuntimeConfig,
) -> PhysicalArvanS3FourRoleLiveIamWitnessLedgerRuntime:
    """Open or initialize the root-owned append-only state for one binding."""

    normalised = _normalise_config(config)
    _initialise_storage(normalised)
    with _locked(normalised):
        state = _load_state(normalised)
    return PhysicalArvanS3FourRoleLiveIamWitnessLedgerRuntime(
        normalised, state.head_sha256, _RUNTIME_CAPABILITY
    )


def read_physical_arvan_s3_four_role_live_iam_witness_ledger_state(
    runtime: PhysicalArvanS3FourRoleLiveIamWitnessLedgerRuntime,
) -> VerifiedPhysicalArvanS3FourRoleLiveIamWitnessLedgerState:
    """Read the exact current immutable head; stale handles fail closed."""

    checked = _require_runtime(runtime)
    with _locked(checked._normalised):
        return _state_receipt(_load_runtime_state(checked), config=checked._normalised)


def issue_physical_arvan_s3_four_role_live_iam_witness_ledger_nonce_permit(
    *,
    runtime: PhysicalArvanS3FourRoleLiveIamWitnessLedgerRuntime,
    nonce: str,
    issued_at: datetime,
    expires_at: datetime,
    witness_signer: object,
) -> tuple[VerifiedPhysicalArvanS3FourRoleLiveIamWitnessLedgerState, bytes]:
    """Persist OPEN before returning the pure Witness-signed nonce permit."""

    checked = _require_runtime(runtime)
    with _locked(checked._normalised):
        state = _load_runtime_state(checked)
        try:
            next_ledger, permit = _live_iam.issue_physical_arvan_s3_four_role_live_iam_nonce_permit(
                binding=checked._normalised.binding,
                ledger=state.ledger,
                nonce=nonce,
                issued_at=issued_at,
                expires_at=expires_at,
                witness_signer=witness_signer,
            )
        except _live_iam.PhysicalArvanS3FourRoleLiveIamEvidenceError as exc:
            _fail(f"ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_OPEN_{exc.code}")
        durable = _append_transition(
            config=checked._normalised, state=state, next_ledger=next_ledger, transition="open"
        )
        checked._expected_head_sha256 = durable.head_sha256
        return _state_receipt(durable, config=checked._normalised), permit


def seal_physical_arvan_s3_four_role_live_iam_witness_ledger_aggregate(
    *,
    runtime: PhysicalArvanS3FourRoleLiveIamWitnessLedgerRuntime,
    nonce_permit: _live_iam.VerifiedPhysicalArvanS3FourRoleLiveIamNoncePermit,
    normal_publisher_observation: _live_iam.VerifiedPhysicalArvanS3FourRoleLiveIamPublisherObservation,
    normal_witness_forward: _live_iam.VerifiedPhysicalArvanS3FourRoleLiveIamWitnessForward,
    normal_receiver_observation: _live_iam.VerifiedPhysicalArvanS3FourRoleLiveIamReceiverObservation,
    reverse_publisher_observation: _live_iam.VerifiedPhysicalArvanS3FourRoleLiveIamPublisherObservation,
    reverse_witness_forward: _live_iam.VerifiedPhysicalArvanS3FourRoleLiveIamWitnessForward,
    reverse_receiver_observation: _live_iam.VerifiedPhysicalArvanS3FourRoleLiveIamReceiverObservation,
    committed_at: datetime,
    witness_signer: object,
) -> tuple[VerifiedPhysicalArvanS3FourRoleLiveIamWitnessLedgerState, bytes]:
    """Persist COMMITTED before releasing the corresponding aggregate bytes."""

    checked = _require_runtime(runtime)
    with _locked(checked._normalised):
        state = _load_runtime_state(checked)
        try:
            next_ledger, aggregate = _live_iam.seal_physical_arvan_s3_four_role_live_iam_witness_aggregate(
                binding=checked._normalised.binding,
                ledger=state.ledger,
                nonce_permit=nonce_permit,
                normal_publisher_observation=normal_publisher_observation,
                normal_witness_forward=normal_witness_forward,
                normal_receiver_observation=normal_receiver_observation,
                reverse_publisher_observation=reverse_publisher_observation,
                reverse_witness_forward=reverse_witness_forward,
                reverse_receiver_observation=reverse_receiver_observation,
                committed_at=committed_at,
                witness_signer=witness_signer,
            )
        except _live_iam.PhysicalArvanS3FourRoleLiveIamEvidenceError as exc:
            _fail(f"ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_COMMIT_{exc.code}")
        durable = _append_transition(
            config=checked._normalised, state=state, next_ledger=next_ledger, transition="committed"
        )
        checked._expected_head_sha256 = durable.head_sha256
        return _state_receipt(durable, config=checked._normalised), aggregate


def expire_physical_arvan_s3_four_role_live_iam_witness_ledger_nonce(
    *,
    runtime: PhysicalArvanS3FourRoleLiveIamWitnessLedgerRuntime,
    nonce: str,
    retired_at: datetime,
) -> VerifiedPhysicalArvanS3FourRoleLiveIamWitnessLedgerState:
    """Persist EXPIRED before returning the state receipt; no object is deleted."""

    checked = _require_runtime(runtime)
    with _locked(checked._normalised):
        state = _load_runtime_state(checked)
        try:
            next_ledger = _live_iam.expire_physical_arvan_s3_four_role_live_iam_nonce(
                binding=checked._normalised.binding,
                ledger=state.ledger,
                nonce=nonce,
                retired_at=retired_at,
            )
        except _live_iam.PhysicalArvanS3FourRoleLiveIamEvidenceError as exc:
            _fail(f"ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_EXPIRE_{exc.code}")
        durable = _append_transition(
            config=checked._normalised, state=state, next_ledger=next_ledger, transition="expired"
        )
        checked._expected_head_sha256 = durable.head_sha256
        return _state_receipt(durable, config=checked._normalised)


def verify_physical_arvan_s3_four_role_live_iam_witness_ledger_aggregate(
    *,
    runtime: PhysicalArvanS3FourRoleLiveIamWitnessLedgerRuntime,
    aggregate: bytes,
    witness_public_key: bytes,
    observed_at: datetime,
) -> tuple[
    VerifiedPhysicalArvanS3FourRoleLiveIamWitnessLedgerState,
    _live_iam.VerifiedPhysicalArvanS3FourRoleLiveIamWitnessAggregate,
]:
    """Verify raw aggregate bytes only against the latest durable nonce state."""

    checked = _require_runtime(runtime)
    with _locked(checked._normalised):
        state = _load_runtime_state(checked)
        try:
            verified = _live_iam.verify_physical_arvan_s3_four_role_live_iam_witness_aggregate(
                aggregate,
                binding=checked._normalised.binding,
                ledger=state.ledger,
                witness_public_key=witness_public_key,
                observed_at=observed_at,
            )
        except _live_iam.PhysicalArvanS3FourRoleLiveIamEvidenceError as exc:
            _fail(f"ARVAN_S3_FOUR_ROLE_LIVE_IAM_WITNESS_LEDGER_AGGREGATE_{exc.code}")
        return _state_receipt(state, config=checked._normalised), verified
