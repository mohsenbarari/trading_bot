"""Root-owned durable Witness dispatcher for four role-local immutability.

This module persists a small append-only chain of already-signed approval and
receipt bytes.  It is a dispatcher *seam*, not a transport: an output names a
role and site but contains no host, URL, socket, SSH command, Object-Storage
client, or FI-to-IR/IR-to-FI path.  An independently reviewed Witness inbox /
outbox may carry the opaque bytes to and from the appropriate local role
agent.

Each record is create-only, fsynced, hash-linked, and root-owned.  A restart
rebuilds the only allowed sequence from durable bytes:

``FI publisher -> IR receiver -> IR publisher -> FI receiver``.

The root-owned trusted clock is read internally.  If a partial, expired,
forked, or unlinked tail is found, the runtime blocks rather than reissuing an
immutable probe or guessing a repair.
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

from core.append_only_sync_delta_batch import SHA256_RE, canonical_json_bytes
from core import physical_arvan_s3_four_role_immutability_witness_orchestration as _orchestration
from core import physical_arvan_s3_four_role_live_iam_durable_admission_bridge as _admission
from core import physical_arvan_s3_four_role_live_iam_witness_ledger_runtime as _secure_fs
from core import physical_arvan_s3_role_profiles as _profiles


__all__ = (
    "DEFAULT_PHYSICAL_ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_MAXIMUM_RECORDS",
    "PHYSICAL_ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_DEFAULT_ENABLED",
    "PHYSICAL_ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_SCHEMA",
    "PhysicalArvanS3FourRoleImmutabilityWitnessDispatchLedgerConfig",
    "PhysicalArvanS3FourRoleImmutabilityWitnessDispatchLedgerError",
    "PhysicalArvanS3FourRoleImmutabilityWitnessDispatchLedgerRuntime",
    "PhysicalArvanS3FourRoleImmutabilityWitnessDispatchResult",
    "open_physical_arvan_s3_four_role_immutability_witness_dispatch_ledger",
    "start_physical_arvan_s3_four_role_immutability_witness_dispatch",
    "submit_physical_arvan_s3_four_role_immutability_witness_role_receipt",
)


PHYSICAL_ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_SCHEMA = (
    "gold-trade-physical-arvan-s3-four-role-immutability-witness-dispatch-ledger-v1"
)
PHYSICAL_ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_DEFAULT_ENABLED = False
DEFAULT_PHYSICAL_ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_MAXIMUM_RECORDS = 64

_MAXIMUM_RECORDS = 256
# An advance record contains an approval, a receipt, and the next approval.
# Each wire value is independently bounded by the orchestration grammar at
# 128 KiB, but base64 expansion means the durable envelope needs room for all
# three values plus canonical metadata.
_MAX_RECORD_BYTES = 1024 * 1024
_DIRECTORY = "physical-arvan-s3-four-role-immutability-witness-dispatch-ledger-v1"
_RECORDS_DIRECTORY = "records"
_BINDING_FILENAME = "binding.json"
_LOCK_FILENAME = "ledger.lock"
_BINDING_SCHEMA = "gold-trade-physical-arvan-s3-four-role-immutability-witness-dispatch-binding-v1"
_RECORD_SCHEMA = "gold-trade-physical-arvan-s3-four-role-immutability-witness-dispatch-record-v1"
_START = "start"
_ADVANCE = "advance"
_COMPLETE = "complete"
_KINDS = frozenset({_START, _ADVANCE, _COMPLETE})
_RECORD_RE = re.compile(r"^([0-9]{20})-([0-9a-f]{64})\.json$", re.ASCII)
_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", re.ASCII)
_ROLE_SITE = {
    _profiles.ARVAN_S3_FI_PUBLISHER_ROLE: "webapp_fi",
    _profiles.ARVAN_S3_IR_RECEIVER_ROLE: "webapp_ir",
    _profiles.ARVAN_S3_IR_PUBLISHER_ROLE: "webapp_ir",
    _profiles.ARVAN_S3_FI_RECEIVER_ROLE: "webapp_fi",
}
_CAPABILITY = object()


class PhysicalArvanS3FourRoleImmutabilityWitnessDispatchLedgerError(RuntimeError):
    """A durable Witness-only dispatch transition is unsafe or incomplete."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class PhysicalArvanS3FourRoleImmutabilityWitnessDispatchLedgerConfig:
    """Default-off root-owned persistent boundary for one orchestration pin."""

    state_root: Path | None = None
    binding: _orchestration.PhysicalArvanS3FourRoleImmutabilityWitnessBinding | None = field(
        default=None,
        repr=False,
    )
    enabled: bool = PHYSICAL_ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_DEFAULT_ENABLED
    maximum_records: int = DEFAULT_PHYSICAL_ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_MAXIMUM_RECORDS


class PhysicalArvanS3FourRoleImmutabilityWitnessDispatchLedgerRuntime:
    """Nonserializable root-owned handle for a single append-only ledger."""

    __slots__ = ("_config", "_expected_head_sha256", "_capability")

    def __init__(self, config: "_Config", head_sha256: str, capability: object) -> None:
        if capability is not _CAPABILITY:
            raise TypeError("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_CONSTRUCTION_FORBIDDEN")
        self._config = config
        self._expected_head_sha256 = head_sha256
        self._capability = capability

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_SERIALIZATION_FORBIDDEN")


@dataclass(frozen=True)
class PhysicalArvanS3FourRoleImmutabilityWitnessDispatchResult:
    """Opaque delivery instruction with no network destination or route."""

    schema: str
    status: str
    operation_nonce_sha256: str
    sequence: int
    ledger_head_sha256: str
    target_role: str | None
    target_site: str | None
    approval: bytes | None = field(repr=False)
    preflight_observation: object | None = field(default=None, repr=False)


@dataclass(frozen=True)
class _Config:
    root: Path
    directory: Path
    records: Path
    binding: _orchestration.PhysicalArvanS3FourRoleImmutabilityWitnessBinding
    binding_metadata: dict[str, Any]
    binding_digest: str
    maximum_records: int


@dataclass(frozen=True)
class _Record:
    sequence: int
    previous_head_sha256: str
    kind: str
    created_at: datetime
    operation_nonce_sha256: str
    admission_aggregate_sha256: str
    admission_durable_ledger_head_sha256: str
    expires_at: datetime
    approval: bytes
    approval_sha256: str
    receipt: bytes | None
    receipt_sha256: str | None
    next_approval: bytes | None
    next_approval_sha256: str | None
    record_sha256: str


@dataclass(frozen=True)
class _State:
    records: tuple[_Record, ...]
    head_sha256: str


@dataclass(frozen=True)
class _RebuiltOperation:
    operation_nonce_sha256: str
    admission_aggregate_sha256: str
    admission_durable_ledger_head_sha256: str
    expires_at: datetime
    current_approval_raw: bytes | None
    current_approval: _orchestration.VerifiedPhysicalArvanS3FourRoleImmutabilityWitnessApproval | None
    receipts: tuple[_orchestration.VerifiedPhysicalArvanS3FourRoleImmutabilityRoleReceipt, ...]
    complete: bool
    latest_record: _Record


def _fail(code: str) -> None:
    raise PhysicalArvanS3FourRoleImmutabilityWitnessDispatchLedgerError(code)


def _canonical(value: object, *, code: str) -> bytes:
    try:
        return canonical_json_bytes(value)
    except (TypeError, ValueError):
        _fail(code)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256(value: object, *, code: str) -> str:
    if type(value) is not str or SHA256_RE.fullmatch(value) is None or value == "0" * 64:
        _fail(code)
    return value


def _utc(value: object, *, code: str) -> datetime:
    if type(value) is not datetime or value.tzinfo is not timezone.utc or value.microsecond != 0:
        _fail(code)
    return value


def _timestamp(value: datetime, *, code: str) -> str:
    return _utc(value, code=code).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_timestamp(value: object, *, code: str) -> datetime:
    if type(value) is not str or _TIMESTAMP_RE.fullmatch(value) is None:
        _fail(code)
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        _fail(code)


def _host_now() -> datetime:
    try:
        return datetime.now(timezone.utc).replace(microsecond=0)
    except Exception:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_CLOCK_INVALID")


def _require_root() -> None:
    try:
        if os.geteuid() != 0:
            _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_REQUIRES_ROOT")
    except OSError:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_REQUIRES_ROOT")


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_JSON_INVALID")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    del value
    _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_JSON_INVALID")


def _parse_canonical(raw: object, *, code: str) -> dict[str, Any]:
    if type(raw) is not bytes or not raw or len(raw) > _MAX_RECORD_BYTES:
        _fail(code)
    try:
        parsed = json.loads(
            raw.decode("ascii"), object_pairs_hook=_strict_object, parse_constant=_reject_json_constant
        )
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
        _fail(code)
    if type(parsed) is not dict or _canonical(parsed, code=code) != raw:
        _fail(code)
    return parsed


def _b64(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _unb64(value: object, *, code: str, permit_none: bool = False) -> bytes | None:
    if value is None and permit_none:
        return None
    if type(value) is not str:
        _fail(code)
    try:
        result = base64.b64decode(value.encode("ascii"), validate=True)
    except (UnicodeEncodeError, binascii.Error):
        _fail(code)
    if not result or len(result) > _MAX_RECORD_BYTES:
        _fail(code)
    return result


def _binding_metadata(
    binding: _orchestration.PhysicalArvanS3FourRoleImmutabilityWitnessBinding,
) -> dict[str, Any]:
    return {
        "schema": _BINDING_SCHEMA,
        "orchestration_binding_sha256": binding.orchestration_binding_sha256,
        "campaign_id": binding.preflight_binding.campaign_id,
        "release_sha": binding.preflight_binding.release_sha,
        "normal_route_scope_sha256": binding.preflight_binding.normal_route_scope_sha256,
        "reverse_route_scope_sha256": binding.preflight_binding.reverse_route_scope_sha256,
        "four_role_route_binding_sha256": binding.preflight_binding.four_role_route_binding_sha256,
    }


def _config(value: object) -> _Config:
    if type(value) is not PhysicalArvanS3FourRoleImmutabilityWitnessDispatchLedgerConfig:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_CONFIG_INVALID")
    config = value
    if (
        config.enabled is not True
        or type(config.maximum_records) is not int
        or not 1 <= config.maximum_records <= _MAXIMUM_RECORDS
    ):
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_CONFIG_INVALID")
    _require_root()
    try:
        binding = _orchestration._binding(config.binding)
    except _orchestration.PhysicalArvanS3FourRoleImmutabilityWitnessOrchestrationError:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_BINDING_INVALID")
    try:
        root = _secure_fs._secure_state_root(config.state_root)
    except Exception:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_STATE_ROOT_UNSAFE")
    metadata = _binding_metadata(binding)
    digest = _sha256_bytes(
        _canonical(metadata, code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_BINDING_INVALID")
    )
    return _Config(
        root=root,
        directory=root / _DIRECTORY,
        records=root / _DIRECTORY / _RECORDS_DIRECTORY,
        binding=binding,
        binding_metadata=metadata,
        binding_digest=digest,
        maximum_records=config.maximum_records,
    )


def _directory_flags() -> int:
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_PLATFORM_UNSAFE")
    return os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)


def _check_directory_descriptor(descriptor: int, *, code: str) -> None:
    try:
        info = os.fstat(descriptor)
    except OSError:
        _fail(code)
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != 0
        or stat.S_IMODE(info.st_mode) != 0o700
    ):
        _fail(code)


def _open_root(config: _Config) -> int:
    descriptor = -1
    try:
        descriptor = os.open(config.root, _directory_flags())
        _check_directory_descriptor(
            descriptor,
            code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_STATE_ROOT_UNSAFE",
        )
        return descriptor
    except PhysicalArvanS3FourRoleImmutabilityWitnessDispatchLedgerError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError:
        if descriptor >= 0:
            os.close(descriptor)
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_STATE_ROOT_UNSAFE")


def _open_directory_at(parent: int, name: str, *, code: str) -> int:
    descriptor = -1
    try:
        descriptor = os.open(name, _directory_flags(), dir_fd=parent)
        _check_directory_descriptor(descriptor, code=code)
        return descriptor
    except PhysicalArvanS3FourRoleImmutabilityWitnessDispatchLedgerError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError:
        if descriptor >= 0:
            os.close(descriptor)
        _fail(code)


def _ensure_directory_at(parent: int, name: str, *, code: str) -> int:
    try:
        os.mkdir(name, mode=0o700, dir_fd=parent)
        os.fsync(parent)
    except FileExistsError:
        pass
    except OSError:
        _fail(code)
    return _open_directory_at(parent, name, code=code)


def _check_regular_descriptor(descriptor: int, *, permit_empty: bool, code: str) -> os.stat_result:
    try:
        info = os.fstat(descriptor)
    except OSError:
        _fail(code)
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != 0
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_nlink != 1
        or info.st_size < 0
        or info.st_size > _MAX_RECORD_BYTES
        or (not permit_empty and info.st_size < 1)
    ):
        _fail(code)
    return info


def _read_file_at(parent: int, name: str, *, permit_empty: bool, code: str) -> bytes:
    descriptor = -1
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
            dir_fd=parent,
        )
        info = _check_regular_descriptor(descriptor, permit_empty=permit_empty, code=code)
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
    except PhysicalArvanS3FourRoleImmutabilityWitnessDispatchLedgerError:
        raise
    except OSError:
        _fail(code)
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
        except OSError:
            _fail(code)
        if type(written) is not int or written <= 0:
            _fail(code)
        view = view[written:]


def _write_create_only_at(parent: int, name: str, payload: bytes, *, code: str) -> None:
    if type(payload) is not bytes or not 1 <= len(payload) <= _MAX_RECORD_BYTES:
        _fail(code)
    descriptor = -1
    try:
        descriptor = os.open(
            name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
            0o600,
            dir_fd=parent,
        )
        _check_regular_descriptor(
            descriptor,
            permit_empty=True,
            code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_STATE_UNSAFE",
        )
        _write_all(descriptor, payload, code=code)
        os.fsync(descriptor)
    except PhysicalArvanS3FourRoleImmutabilityWitnessDispatchLedgerError:
        raise
    except FileExistsError:
        raise
    except OSError:
        _fail(code)
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass
    try:
        os.fsync(parent)
    except OSError:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_DIRECTORY_FSYNC_FAILED")


def _init_storage(config: _Config) -> None:
    root = _open_root(config)
    directory = -1
    records = -1
    try:
        directory = _ensure_directory_at(
            root,
            _DIRECTORY,
            code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_STATE_WRITE_FAILED",
        )
        records = _ensure_directory_at(
            directory,
            _RECORDS_DIRECTORY,
            code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_STATE_WRITE_FAILED",
        )
        try:
            os.close(records)
        except OSError:
            _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_STATE_UNSAFE")
        records = -1
        payload = _canonical(
            config.binding_metadata,
            code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_BINDING_INVALID",
        )
        try:
            actual = _read_file_at(
                directory,
                _BINDING_FILENAME,
                permit_empty=False,
                code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_STATE_UNSAFE",
            )
        except PhysicalArvanS3FourRoleImmutabilityWitnessDispatchLedgerError as exc:
            if exc.code != "ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_STATE_UNSAFE":
                raise
            try:
                _write_create_only_at(
                    directory,
                    _BINDING_FILENAME,
                    payload,
                    code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_STATE_WRITE_FAILED",
                )
                actual = payload
            except FileExistsError:
                actual = _read_file_at(
                    directory,
                    _BINDING_FILENAME,
                    permit_empty=False,
                    code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_STATE_UNSAFE",
                )
        if actual != payload:
            _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_FOREIGN_BINDING")
        try:
            _read_file_at(
                directory,
                _LOCK_FILENAME,
                permit_empty=False,
                code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_STATE_UNSAFE",
            )
        except PhysicalArvanS3FourRoleImmutabilityWitnessDispatchLedgerError as exc:
            if exc.code != "ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_STATE_UNSAFE":
                raise
            try:
                _write_create_only_at(
                    directory,
                    _LOCK_FILENAME,
                    b"0",
                    code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_STATE_WRITE_FAILED",
                )
            except FileExistsError:
                _read_file_at(
                    directory,
                    _LOCK_FILENAME,
                    permit_empty=False,
                    code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_STATE_UNSAFE",
                )
    finally:
        for descriptor in (records, directory, root):
            if descriptor >= 0:
                try:
                    os.close(descriptor)
                except OSError:
                    pass


@dataclass(frozen=True)
class _OpenStorage:
    directory: int
    records: int
    lock: int


@contextmanager
def _locked(config: _Config) -> Iterator[_OpenStorage]:
    root = _open_root(config)
    directory = -1
    records = -1
    lock = -1
    try:
        directory = _open_directory_at(
            root,
            _DIRECTORY,
            code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_STATE_UNSAFE",
        )
        records = _open_directory_at(
            directory,
            _RECORDS_DIRECTORY,
            code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_STATE_UNSAFE",
        )
        lock = os.open(
            _LOCK_FILENAME,
            os.O_RDWR | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
            dir_fd=directory,
        )
        _check_regular_descriptor(
            lock,
            permit_empty=False,
            code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_STATE_UNSAFE",
        )
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield _OpenStorage(directory=directory, records=records, lock=lock)
    except PhysicalArvanS3FourRoleImmutabilityWitnessDispatchLedgerError:
        raise
    except OSError:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_LOCK_FAILED")
    finally:
        if lock >= 0:
            try:
                fcntl.flock(lock, fcntl.LOCK_UN)
            except OSError:
                pass
        for descriptor in (lock, records, directory, root):
            if descriptor >= 0:
                try:
                    os.close(descriptor)
                except OSError:
                    pass


def _record_unsigned(
    *,
    sequence: int,
    previous_head_sha256: str,
    kind: str,
    created_at: datetime,
    operation_nonce_sha256: str,
    admission_aggregate_sha256: str,
    admission_durable_ledger_head_sha256: str,
    expires_at: datetime,
    approval: bytes,
    approval_sha256: str,
    receipt: bytes | None,
    receipt_sha256: str | None,
    next_approval: bytes | None,
    next_approval_sha256: str | None,
) -> dict[str, Any]:
    return {
        "schema": _RECORD_SCHEMA,
        "sequence": sequence,
        "previous_head_sha256": previous_head_sha256,
        "kind": kind,
        "created_at": _timestamp(created_at, code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_RECORD_INVALID"),
        "operation_nonce_sha256": operation_nonce_sha256,
        "admission_aggregate_sha256": admission_aggregate_sha256,
        "admission_durable_ledger_head_sha256": admission_durable_ledger_head_sha256,
        "expires_at": _timestamp(expires_at, code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_RECORD_INVALID"),
        "approval_base64": _b64(approval),
        "approval_sha256": approval_sha256,
        "receipt_base64": None if receipt is None else _b64(receipt),
        "receipt_sha256": receipt_sha256,
        "next_approval_base64": None if next_approval is None else _b64(next_approval),
        "next_approval_sha256": next_approval_sha256,
    }


_RECORD_FIELDS = frozenset(
    {
        "schema",
        "sequence",
        "previous_head_sha256",
        "kind",
        "created_at",
        "operation_nonce_sha256",
        "admission_aggregate_sha256",
        "admission_durable_ledger_head_sha256",
        "expires_at",
        "approval_base64",
        "approval_sha256",
        "receipt_base64",
        "receipt_sha256",
        "next_approval_base64",
        "next_approval_sha256",
        "record_sha256",
    }
)


def _exact_mapping(value: object, *, fields: frozenset[str], code: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != fields:
        _fail(code)
    return dict(value)


def _parse_record(raw: bytes, *, filename_sha256: str) -> _Record:
    item = _exact_mapping(
        _parse_canonical(raw, code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_RECORD_INVALID"),
        fields=_RECORD_FIELDS,
        code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_RECORD_INVALID",
    )
    if (
        item["schema"] != _RECORD_SCHEMA
        or type(item["sequence"]) is not int
        or item["sequence"] < 1
        or type(item["kind"]) is not str
        or item["kind"] not in _KINDS
    ):
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_RECORD_INVALID")
    previous = _sha256(item["previous_head_sha256"], code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_RECORD_INVALID")
    operation = _sha256(item["operation_nonce_sha256"], code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_RECORD_INVALID")
    aggregate = _sha256(item["admission_aggregate_sha256"], code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_RECORD_INVALID")
    durable_head = _sha256(item["admission_durable_ledger_head_sha256"], code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_RECORD_INVALID")
    approval = _unb64(item["approval_base64"], code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_RECORD_INVALID")
    assert approval is not None
    approval_sha = _sha256(item["approval_sha256"], code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_RECORD_INVALID")
    if _sha256_bytes(approval) != approval_sha:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_RECORD_INVALID")
    receipt = _unb64(item["receipt_base64"], code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_RECORD_INVALID", permit_none=True)
    receipt_sha_value = item["receipt_sha256"]
    next_approval = _unb64(item["next_approval_base64"], code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_RECORD_INVALID", permit_none=True)
    next_sha_value = item["next_approval_sha256"]
    if receipt is None:
        if receipt_sha_value is not None:
            _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_RECORD_INVALID")
        receipt_sha = None
    else:
        receipt_sha = _sha256(receipt_sha_value, code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_RECORD_INVALID")
        if _sha256_bytes(receipt) != receipt_sha:
            _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_RECORD_INVALID")
    if next_approval is None:
        if next_sha_value is not None:
            _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_RECORD_INVALID")
        next_sha = None
    else:
        next_sha = _sha256(next_sha_value, code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_RECORD_INVALID")
        if _sha256_bytes(next_approval) != next_sha:
            _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_RECORD_INVALID")
    if (
        (item["kind"] == _START and (receipt is not None or next_approval is not None))
        or (item["kind"] == _ADVANCE and (receipt is None or next_approval is None))
        or (item["kind"] == _COMPLETE and (receipt is None or next_approval is not None))
    ):
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_RECORD_INVALID")
    unsigned = _record_unsigned(
        sequence=item["sequence"],
        previous_head_sha256=previous,
        kind=item["kind"],
        created_at=_parse_timestamp(item["created_at"], code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_RECORD_INVALID"),
        operation_nonce_sha256=operation,
        admission_aggregate_sha256=aggregate,
        admission_durable_ledger_head_sha256=durable_head,
        expires_at=_parse_timestamp(item["expires_at"], code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_RECORD_INVALID"),
        approval=approval,
        approval_sha256=approval_sha,
        receipt=receipt,
        receipt_sha256=receipt_sha,
        next_approval=next_approval,
        next_approval_sha256=next_sha,
    )
    digest = _sha256_bytes(_canonical(unsigned, code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_RECORD_INVALID"))
    if (
        _sha256(item["record_sha256"], code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_RECORD_INVALID") != digest
        or digest != filename_sha256
    ):
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_RECORD_INVALID")
    return _Record(
        sequence=item["sequence"],
        previous_head_sha256=previous,
        kind=item["kind"],
        created_at=_parse_timestamp(item["created_at"], code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_RECORD_INVALID"),
        operation_nonce_sha256=operation,
        admission_aggregate_sha256=aggregate,
        admission_durable_ledger_head_sha256=durable_head,
        expires_at=_parse_timestamp(item["expires_at"], code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_RECORD_INVALID"),
        approval=approval,
        approval_sha256=approval_sha,
        receipt=receipt,
        receipt_sha256=receipt_sha,
        next_approval=next_approval,
        next_approval_sha256=next_sha,
        record_sha256=digest,
    )


def _load_state(config: _Config, storage: _OpenStorage) -> _State:
    expected_binding = _canonical(
        config.binding_metadata,
        code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_BINDING_INVALID",
    )
    try:
        actual_binding = _read_file_at(
            storage.directory,
            _BINDING_FILENAME,
            permit_empty=False,
            code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_STATE_UNSAFE",
        )
    except Exception:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_STATE_UNSAFE")
    if actual_binding != expected_binding:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_FOREIGN_BINDING")
    found: dict[int, tuple[str, str]] = {}
    try:
        with os.scandir(storage.records) as entries:
            for entry in entries:
                match = _RECORD_RE.fullmatch(entry.name)
                if match is None:
                    _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_RECORD_DIRECTORY_INVALID")
                info = entry.stat(follow_symlinks=False)
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_uid != 0
                    or stat.S_IMODE(info.st_mode) != 0o600
                    or info.st_nlink != 1
                ):
                    _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_RECORD_DIRECTORY_INVALID")
                sequence = int(match.group(1))
                if sequence < 1 or sequence in found:
                    _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_RECORD_DIRECTORY_INVALID")
                found[sequence] = (match.group(2), entry.name)
    except PhysicalArvanS3FourRoleImmutabilityWitnessDispatchLedgerError:
        raise
    except OSError:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_STATE_UNSAFE")
    if len(found) > config.maximum_records or set(found) != set(range(1, len(found) + 1)):
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_RECORD_FORK")
    head = config.binding_digest
    records: list[_Record] = []
    for sequence in range(1, len(found) + 1):
        filename_sha, name = found[sequence]
        try:
            raw = _read_file_at(
                storage.records,
                name,
                permit_empty=False,
                code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_STATE_UNSAFE",
            )
        except Exception:
            _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_STATE_UNSAFE")
        record = _parse_record(raw, filename_sha256=filename_sha)
        if record.sequence != sequence or record.previous_head_sha256 != head:
            _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_RECORD_FORK")
        records.append(record)
        head = record.record_sha256
    return _State(records=tuple(records), head_sha256=head)


def _append(
    *,
    config: _Config,
    storage: _OpenStorage,
    state: _State,
    kind: str,
    now: datetime,
    operation_nonce_sha256: str,
    admission_aggregate_sha256: str,
    admission_durable_ledger_head_sha256: str,
    expires_at: datetime,
    approval: bytes,
    receipt: bytes | None,
    next_approval: bytes | None,
) -> _State:
    if len(state.records) >= config.maximum_records:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_RECORD_LIMIT_EXCEEDED")
    approval_sha = _sha256_bytes(approval)
    receipt_sha = None if receipt is None else _sha256_bytes(receipt)
    next_sha = None if next_approval is None else _sha256_bytes(next_approval)
    unsigned = _record_unsigned(
        sequence=len(state.records) + 1,
        previous_head_sha256=state.head_sha256,
        kind=kind,
        created_at=now,
        operation_nonce_sha256=operation_nonce_sha256,
        admission_aggregate_sha256=admission_aggregate_sha256,
        admission_durable_ledger_head_sha256=admission_durable_ledger_head_sha256,
        expires_at=expires_at,
        approval=approval,
        approval_sha256=approval_sha,
        receipt=receipt,
        receipt_sha256=receipt_sha,
        next_approval=next_approval,
        next_approval_sha256=next_sha,
    )
    record_sha = _sha256_bytes(_canonical(unsigned, code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_RECORD_INVALID"))
    payload = _canonical(
        {**unsigned, "record_sha256": record_sha},
        code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_RECORD_INVALID",
    )
    try:
        _write_create_only_at(
            storage.records,
            f"{len(state.records) + 1:020d}-{record_sha}.json",
            payload,
            code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_RECORD_WRITE_FAILED",
        )
    except FileExistsError:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_RECORD_FORK")
    except PhysicalArvanS3FourRoleImmutabilityWitnessDispatchLedgerError:
        raise
    except Exception:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_RECORD_WRITE_FAILED")
    loaded = _load_state(config, storage)
    if len(loaded.records) != len(state.records) + 1 or loaded.head_sha256 != record_sha:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_DURABILITY_VERIFY_FAILED")
    return loaded


_ROLE_ORDER = (
    _profiles.ARVAN_S3_FI_PUBLISHER_ROLE,
    _profiles.ARVAN_S3_IR_RECEIVER_ROLE,
    _profiles.ARVAN_S3_IR_PUBLISHER_ROLE,
    _profiles.ARVAN_S3_FI_RECEIVER_ROLE,
)


def _verify_record_approval(
    *,
    config: _Config,
    record: _Record,
    raw: bytes,
    expected_sha256: str | None = None,
) -> _orchestration.VerifiedPhysicalArvanS3FourRoleImmutabilityWitnessApproval:
    try:
        verified = _orchestration.verify_physical_arvan_s3_four_role_immutability_witness_approval(
            raw,
            binding=config.binding,
            observed_at=record.created_at,
        )
    except _orchestration.PhysicalArvanS3FourRoleImmutabilityWitnessOrchestrationError:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_RECORD_CHAIN_INVALID")
    approval = verified.approval
    expected_sha = record.approval_sha256 if expected_sha256 is None else expected_sha256
    if (
        approval.raw_sha256 != expected_sha
        or approval.operation_nonce_sha256 != record.operation_nonce_sha256
        or approval.admission_aggregate_sha256 != record.admission_aggregate_sha256
        or approval.admission_durable_ledger_head_sha256
        != record.admission_durable_ledger_head_sha256
        or approval.expires_at != record.expires_at
        or approval.issued_at > record.created_at
    ):
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_RECORD_CHAIN_INVALID")
    return verified


def _verify_record_receipt(
    *,
    config: _Config,
    record: _Record,
    raw: bytes,
    approval: _orchestration.VerifiedPhysicalArvanS3FourRoleImmutabilityWitnessApproval,
) -> _orchestration.VerifiedPhysicalArvanS3FourRoleImmutabilityRoleReceipt:
    try:
        verified = _orchestration.verify_physical_arvan_s3_four_role_immutability_role_receipt(
            raw,
            binding=config.binding,
            approval=approval,
            observed_at=record.created_at,
        )
    except _orchestration.PhysicalArvanS3FourRoleImmutabilityWitnessOrchestrationError:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_RECORD_CHAIN_INVALID")
    if (
        verified.raw_sha256 != record.receipt_sha256
        or verified.observed_at > record.created_at
        or verified.operation_nonce_sha256 != record.operation_nonce_sha256
        or verified.admission_aggregate_sha256 != record.admission_aggregate_sha256
        or verified.admission_durable_ledger_head_sha256
        != record.admission_durable_ledger_head_sha256
        or verified.expires_at != record.expires_at
    ):
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_RECORD_CHAIN_INVALID")
    return verified


def _same_record_run(first: _Record, other: _Record) -> None:
    if (
        other.operation_nonce_sha256 != first.operation_nonce_sha256
        or other.admission_aggregate_sha256 != first.admission_aggregate_sha256
        or other.admission_durable_ledger_head_sha256
        != first.admission_durable_ledger_head_sha256
        or other.expires_at != first.expires_at
        or other.created_at < first.created_at
    ):
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_RECORD_CHAIN_INVALID")


def _rebuild(config: _Config, state: _State) -> _RebuiltOperation | None:
    """Reconstruct exactly one durable chain without granting new authority.

    Historical signatures are verified at the persisted Witness time for the
    record that admitted them.  Freshness for a *new* action is deliberately
    checked again by the public functions against their private host clock.
    """

    if not state.records:
        return None
    first = state.records[0]
    if first.kind != _START or first.sequence != 1:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_RECORD_CHAIN_INVALID")
    current_raw = first.approval
    current = _verify_record_approval(config=config, record=first, raw=current_raw)
    if (
        current.approval.stage != _ROLE_ORDER[0]
        or current.approval.issued_at != first.created_at
        or current.approval.prior_receipt_sha256 is not None
        or current.approval.normal_publisher_receipt_sha256 is not None
        or current.approval.shared_bucket_readback is not None
    ):
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_RECORD_CHAIN_INVALID")
    receipts: list[_orchestration.VerifiedPhysicalArvanS3FourRoleImmutabilityRoleReceipt] = []
    normal_receipt_sha256: str | None = None
    normal_bucket: object | None = None
    complete = False
    latest = first
    for index, record in enumerate(state.records[1:], start=1):
        _same_record_run(first, record)
        if (
            complete
            or record.sequence != index + 1
            or record.created_at < latest.created_at
            or record.approval != current_raw
        ):
            _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_RECORD_CHAIN_INVALID")
        verified_current = _verify_record_approval(config=config, record=record, raw=current_raw)
        if verified_current.approval.raw_sha256 != current.approval.raw_sha256:
            _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_RECORD_CHAIN_INVALID")
        if record.receipt is None or record.receipt_sha256 is None:
            _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_RECORD_CHAIN_INVALID")
        receipt = _verify_record_receipt(
            config=config,
            record=record,
            raw=record.receipt,
            approval=verified_current,
        )
        stage_index = len(receipts)
        if stage_index >= len(_ROLE_ORDER) or receipt.stage != _ROLE_ORDER[stage_index]:
            _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_RECORD_CHAIN_INVALID")
        if stage_index == 0:
            normal_receipt_sha256 = receipt.raw_sha256
            try:
                normal_bucket = receipt.readback.bucket_readback
            except AttributeError:
                _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_RECORD_CHAIN_INVALID")
            if normal_bucket is None:
                _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_RECORD_CHAIN_INVALID")
        elif (
            receipt.normal_publisher_receipt_sha256 != normal_receipt_sha256
            or receipt.shared_bucket_readback != normal_bucket
        ):
            _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_RECORD_CHAIN_INVALID")
        receipts.append(receipt)
        if record.kind == _COMPLETE:
            if (
                stage_index != len(_ROLE_ORDER) - 1
                or record.next_approval is not None
                or record.next_approval_sha256 is not None
            ):
                _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_RECORD_CHAIN_INVALID")
            complete = True
            current_raw = None
            current = None
        elif record.kind == _ADVANCE:
            if stage_index >= len(_ROLE_ORDER) - 1 or record.next_approval is None:
                _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_RECORD_CHAIN_INVALID")
            next_approval = _verify_record_approval(
                config=config,
                record=record,
                raw=record.next_approval,
                expected_sha256=record.next_approval_sha256,
            )
            next_value = next_approval.approval
            if (
                record.next_approval_sha256 != next_value.raw_sha256
                or next_value.issued_at != record.created_at
                or next_value.stage != _ROLE_ORDER[stage_index + 1]
                or next_value.prior_receipt_sha256 != receipt.raw_sha256
                or next_value.operation_nonce_sha256 != first.operation_nonce_sha256
                or next_value.admission_aggregate_sha256
                != first.admission_aggregate_sha256
                or next_value.admission_durable_ledger_head_sha256
                != first.admission_durable_ledger_head_sha256
                or next_value.expires_at != first.expires_at
            ):
                _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_RECORD_CHAIN_INVALID")
            if stage_index == 0:
                if (
                    next_value.normal_publisher_receipt_sha256 != normal_receipt_sha256
                    or next_value.shared_bucket_readback != normal_bucket
                    or next_value.retention_floor_publisher_issued_at
                    != receipt.request.observed_at
                ):
                    _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_RECORD_CHAIN_INVALID")
            elif (
                next_value.normal_publisher_receipt_sha256 != normal_receipt_sha256
                or next_value.shared_bucket_readback != normal_bucket
            ):
                _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_RECORD_CHAIN_INVALID")
            current_raw = record.next_approval
            current = next_approval
        else:
            _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_RECORD_CHAIN_INVALID")
        latest = record
    if len(receipts) != len(state.records) - 1:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_RECORD_CHAIN_INVALID")
    if complete != (len(receipts) == len(_ROLE_ORDER)):
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_RECORD_CHAIN_INVALID")
    return _RebuiltOperation(
        operation_nonce_sha256=first.operation_nonce_sha256,
        admission_aggregate_sha256=first.admission_aggregate_sha256,
        admission_durable_ledger_head_sha256=first.admission_durable_ledger_head_sha256,
        expires_at=first.expires_at,
        current_approval_raw=current_raw,
        current_approval=current,
        receipts=tuple(receipts),
        complete=complete,
        latest_record=latest,
    )


def _host_clock_for_state(state: _State) -> datetime:
    now = _host_now()
    if state.records and now < state.records[-1].created_at:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_CLOCK_ROLLBACK")
    return now


def _require_runtime(
    value: object,
) -> PhysicalArvanS3FourRoleImmutabilityWitnessDispatchLedgerRuntime:
    if (
        type(value) is not PhysicalArvanS3FourRoleImmutabilityWitnessDispatchLedgerRuntime
        or value._capability is not _CAPABILITY
    ):
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_RUNTIME_INVALID")
    _require_root()
    return value


def _require_admission(
    *,
    config: _Config,
    admission: object,
    now: datetime,
    operation: _RebuiltOperation | None = None,
) -> _admission.VerifiedPhysicalArvanS3FourRoleLiveIamDurableAdmission:
    try:
        admitted = _admission.require_verified_physical_arvan_s3_four_role_live_iam_durable_admission(
            admission,
            live_iam_binding=config.binding.live_iam_binding,
            failback_binding=config.binding.failback_binding,
            observed_at=now,
        )
    except _admission.PhysicalArvanS3FourRoleLiveIamDurableAdmissionError:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_ADMISSION_INVALID")
    if operation is not None and (
        admitted.aggregate_sha256 != operation.admission_aggregate_sha256
        or admitted.durable_ledger_head_sha256
        != operation.admission_durable_ledger_head_sha256
        or admitted.expires_at != operation.expires_at
    ):
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_ADMISSION_MISMATCH")
    return admitted


def _verify_current_approval(
    *,
    config: _Config,
    operation: _RebuiltOperation,
    now: datetime,
) -> _orchestration.VerifiedPhysicalArvanS3FourRoleImmutabilityWitnessApproval:
    if operation.complete or operation.current_approval_raw is None:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_CHAIN_COMPLETE")
    try:
        verified = _orchestration.verify_physical_arvan_s3_four_role_immutability_witness_approval(
            operation.current_approval_raw,
            binding=config.binding,
            observed_at=now,
        )
    except _orchestration.PhysicalArvanS3FourRoleImmutabilityWitnessOrchestrationError:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_APPROVAL_STALE_OR_INVALID")
    if (
        verified.approval.operation_nonce_sha256 != operation.operation_nonce_sha256
        or verified.approval.admission_aggregate_sha256
        != operation.admission_aggregate_sha256
        or verified.approval.admission_durable_ledger_head_sha256
        != operation.admission_durable_ledger_head_sha256
        or verified.approval.expires_at != operation.expires_at
    ):
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_RECORD_CHAIN_INVALID")
    return verified


def _pending_result(
    *,
    config: _Config,
    state: _State,
    operation: _RebuiltOperation,
    now: datetime,
) -> PhysicalArvanS3FourRoleImmutabilityWitnessDispatchResult:
    verified = _verify_current_approval(config=config, operation=operation, now=now)
    role = verified.approval.stage
    if role not in _ROLE_SITE:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_RECORD_CHAIN_INVALID")
    return PhysicalArvanS3FourRoleImmutabilityWitnessDispatchResult(
        schema=PHYSICAL_ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_SCHEMA,
        status="role-approval-pending",
        operation_nonce_sha256=operation.operation_nonce_sha256,
        sequence=len(state.records),
        ledger_head_sha256=state.head_sha256,
        target_role=role,
        target_site=_ROLE_SITE[role],
        approval=operation.current_approval_raw,
    )


def _complete_result(
    *,
    config: _Config,
    state: _State,
    operation: _RebuiltOperation,
    admission: _admission.VerifiedPhysicalArvanS3FourRoleLiveIamDurableAdmission,
    now: datetime,
) -> PhysicalArvanS3FourRoleImmutabilityWitnessDispatchResult:
    if not operation.complete or len(operation.receipts) != len(_ROLE_ORDER):
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_CHAIN_INCOMPLETE")
    try:
        observation = _orchestration.build_physical_arvan_s3_four_role_immutability_witness_mediated_preflight_observation(
            binding=config.binding,
            admission=admission,
            fi_publisher_receipt=operation.receipts[0],
            ir_receiver_receipt=operation.receipts[1],
            ir_publisher_receipt=operation.receipts[2],
            fi_receiver_receipt=operation.receipts[3],
            observed_at=now,
        )
    except _orchestration.PhysicalArvanS3FourRoleImmutabilityWitnessOrchestrationError:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_FINAL_AGGREGATE_INVALID")
    return PhysicalArvanS3FourRoleImmutabilityWitnessDispatchResult(
        schema=PHYSICAL_ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_SCHEMA,
        status="four-role-immutable-observed",
        operation_nonce_sha256=operation.operation_nonce_sha256,
        sequence=len(state.records),
        ledger_head_sha256=state.head_sha256,
        target_role=None,
        target_site=None,
        approval=None,
        preflight_observation=observation,
    )


def _refresh_runtime(
    *,
    runtime: PhysicalArvanS3FourRoleImmutabilityWitnessDispatchLedgerRuntime,
    state: _State,
) -> None:
    runtime._expected_head_sha256 = state.head_sha256


def _check_runtime_head(
    *,
    runtime: PhysicalArvanS3FourRoleImmutabilityWitnessDispatchLedgerRuntime,
    state: _State,
) -> None:
    if runtime._expected_head_sha256 != state.head_sha256:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_RUNTIME_STALE")


def open_physical_arvan_s3_four_role_immutability_witness_dispatch_ledger(
    config: PhysicalArvanS3FourRoleImmutabilityWitnessDispatchLedgerConfig = (
        PhysicalArvanS3FourRoleImmutabilityWitnessDispatchLedgerConfig()
    ),
) -> PhysicalArvanS3FourRoleImmutabilityWitnessDispatchLedgerRuntime:
    """Open the default-off, root-owned Witness-only dispatch ledger.

    Opening validates bytes and append-chain topology but emits no request and
    performs no provider or peer transport action.
    """

    checked = _config(config)
    _init_storage(checked)
    with _locked(checked) as storage:
        state = _load_state(checked, storage)
        _rebuild(checked, state)
    return PhysicalArvanS3FourRoleImmutabilityWitnessDispatchLedgerRuntime(
        checked,
        state.head_sha256,
        _CAPABILITY,
    )


def start_physical_arvan_s3_four_role_immutability_witness_dispatch(
    *,
    runtime: PhysicalArvanS3FourRoleImmutabilityWitnessDispatchLedgerRuntime,
    admission: _admission.VerifiedPhysicalArvanS3FourRoleLiveIamDurableAdmission,
    operation_nonce_sha256: str,
    normal_probe_nonce_sha256: str,
    witness_signer: object,
) -> PhysicalArvanS3FourRoleImmutabilityWitnessDispatchResult:
    """Durably mint or exactly resume the sole FI-publisher request.

    The initial approval is never returned until its start record, including
    the exact live-IAM admission pins, has been flushed to the Witness state
    directory.  A restart therefore resumes the same opaque approval rather
    than issuing a second immutable probe request.
    """

    active = _require_runtime(runtime)
    config = active._config
    with _locked(config) as storage:
        state = _load_state(config, storage)
        _check_runtime_head(runtime=active, state=state)
        now = _host_clock_for_state(state)
        rebuilt = _rebuild(config, state)
        admitted = _require_admission(config=config, admission=admission, now=now, operation=rebuilt)
        operation = _sha256(
            operation_nonce_sha256,
            code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_OPERATION_INVALID",
        )
        normal_nonce = _sha256(
            normal_probe_nonce_sha256,
            code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_OPERATION_INVALID",
        )
        if operation == normal_nonce:
            _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_NONCE_COLLISION")
        if rebuilt is not None:
            first_approval = _verify_record_approval(
                config=config,
                record=state.records[0],
                raw=state.records[0].approval,
            ).approval
            if (
                rebuilt.operation_nonce_sha256 != operation
                or first_approval.request.probe_nonce_sha256 != normal_nonce
            ):
                _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_OPERATION_ALREADY_BOUND")
            if rebuilt.complete:
                return _complete_result(
                    config=config,
                    state=state,
                    operation=rebuilt,
                    admission=admitted,
                    now=now,
                )
            return _pending_result(config=config, state=state, operation=rebuilt, now=now)
        try:
            approval = _orchestration.issue_physical_arvan_s3_four_role_immutability_initial_witness_approval(
                binding=config.binding,
                admission=admitted,
                operation_nonce_sha256=operation,
                normal_probe_nonce_sha256=normal_nonce,
                issued_at=now,
                witness_signer=witness_signer,
            )
            verified = _orchestration.verify_physical_arvan_s3_four_role_immutability_witness_approval(
                approval,
                binding=config.binding,
                observed_at=now,
            )
        except _orchestration.PhysicalArvanS3FourRoleImmutabilityWitnessOrchestrationError:
            _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_INITIAL_APPROVAL_INVALID")
        updated = _append(
            config=config,
            storage=storage,
            state=state,
            kind=_START,
            now=now,
            operation_nonce_sha256=verified.approval.operation_nonce_sha256,
            admission_aggregate_sha256=verified.approval.admission_aggregate_sha256,
            admission_durable_ledger_head_sha256=verified.approval.admission_durable_ledger_head_sha256,
            expires_at=verified.approval.expires_at,
            approval=approval,
            receipt=None,
            next_approval=None,
        )
        rebuilt = _rebuild(config, updated)
        if rebuilt is None:
            _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_DURABILITY_VERIFY_FAILED")
        _refresh_runtime(runtime=active, state=updated)
        return _pending_result(config=config, state=updated, operation=rebuilt, now=now)


def submit_physical_arvan_s3_four_role_immutability_witness_role_receipt(
    *,
    runtime: PhysicalArvanS3FourRoleImmutabilityWitnessDispatchLedgerRuntime,
    admission: _admission.VerifiedPhysicalArvanS3FourRoleLiveIamDurableAdmission,
    receipt: bytes,
    witness_signer: object | None = None,
    reverse_probe_nonce_sha256: str | None = None,
) -> PhysicalArvanS3FourRoleImmutabilityWitnessDispatchResult:
    """Admit one exact local receipt and durably emit only its next request.

    This is a Witness-local state transition.  It neither invokes a local
    collector nor delivers bytes to another machine.  The returned approval
    remains an opaque inbox/outbox payload for a separately reviewed
    Witness-mediated delivery channel.
    """

    active = _require_runtime(runtime)
    config = active._config
    if type(receipt) is not bytes or not receipt or len(receipt) > _MAX_RECORD_BYTES:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_RECEIPT_INVALID")
    with _locked(config) as storage:
        state = _load_state(config, storage)
        _check_runtime_head(runtime=active, state=state)
        now = _host_clock_for_state(state)
        rebuilt = _rebuild(config, state)
        if rebuilt is None:
            _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_NOT_STARTED")
        admitted = _require_admission(config=config, admission=admission, now=now, operation=rebuilt)
        receipt_sha = _sha256_bytes(receipt)
        tail = state.records[-1]
        if tail.receipt_sha256 == receipt_sha:
            if tail.receipt != receipt:
                _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_RECEIPT_COLLISION")
            if rebuilt.complete:
                return _complete_result(
                    config=config,
                    state=state,
                    operation=rebuilt,
                    admission=admitted,
                    now=now,
                )
            return _pending_result(config=config, state=state, operation=rebuilt, now=now)
        if any(
            record.receipt_sha256 == receipt_sha
            for record in state.records[:-1]
        ):
            _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_RECEIPT_REPLAY")
        current = _verify_current_approval(config=config, operation=rebuilt, now=now)
        try:
            verified_receipt = _orchestration.verify_physical_arvan_s3_four_role_immutability_role_receipt(
                receipt,
                binding=config.binding,
                approval=current,
                observed_at=now,
            )
        except _orchestration.PhysicalArvanS3FourRoleImmutabilityWitnessOrchestrationError:
            _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_RECEIPT_INVALID")
        if verified_receipt.observed_at > now:
            _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_RECEIPT_FUTURE_DATED")
        stage_index = len(rebuilt.receipts)
        if (
            stage_index >= len(_ROLE_ORDER)
            or verified_receipt.stage != _ROLE_ORDER[stage_index]
        ):
            _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_RECEIPT_INVALID")
        if stage_index == len(_ROLE_ORDER) - 1:
            if reverse_probe_nonce_sha256 is not None:
                _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_NONCE_UNEXPECTED")
            updated = _append(
                config=config,
                storage=storage,
                state=state,
                kind=_COMPLETE,
                now=now,
                operation_nonce_sha256=rebuilt.operation_nonce_sha256,
                admission_aggregate_sha256=rebuilt.admission_aggregate_sha256,
                admission_durable_ledger_head_sha256=rebuilt.admission_durable_ledger_head_sha256,
                expires_at=rebuilt.expires_at,
                approval=rebuilt.current_approval_raw or b"",
                receipt=receipt,
                next_approval=None,
            )
            final = _rebuild(config, updated)
            if final is None:
                _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_DURABILITY_VERIFY_FAILED")
            _refresh_runtime(runtime=active, state=updated)
            return _complete_result(
                config=config,
                state=updated,
                operation=final,
                admission=admitted,
                now=now,
            )
        try:
            next_approval = _orchestration.issue_physical_arvan_s3_four_role_immutability_next_witness_approval(
                binding=config.binding,
                prior_receipt=verified_receipt,
                issued_at=now,
                witness_signer=witness_signer,
                reverse_probe_nonce_sha256=reverse_probe_nonce_sha256,
            )
            next_verified = _orchestration.verify_physical_arvan_s3_four_role_immutability_witness_approval(
                next_approval,
                binding=config.binding,
                observed_at=now,
            )
        except _orchestration.PhysicalArvanS3FourRoleImmutabilityWitnessOrchestrationError:
            _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_NEXT_APPROVAL_INVALID")
        updated = _append(
            config=config,
            storage=storage,
            state=state,
            kind=_ADVANCE,
            now=now,
            operation_nonce_sha256=rebuilt.operation_nonce_sha256,
            admission_aggregate_sha256=rebuilt.admission_aggregate_sha256,
            admission_durable_ledger_head_sha256=rebuilt.admission_durable_ledger_head_sha256,
            expires_at=rebuilt.expires_at,
            approval=rebuilt.current_approval_raw or b"",
            receipt=receipt,
            next_approval=next_approval,
        )
        next_rebuilt = _rebuild(config, updated)
        if next_rebuilt is None:
            _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_DURABILITY_VERIFY_FAILED")
        if (
            next_rebuilt.current_approval is None
            or next_verified.approval.raw_sha256
            != next_rebuilt.current_approval.approval.raw_sha256
        ):
            _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_DISPATCH_DURABILITY_VERIFY_FAILED")
        _refresh_runtime(runtime=active, state=updated)
        return _pending_result(config=config, state=updated, operation=next_rebuilt, now=now)
