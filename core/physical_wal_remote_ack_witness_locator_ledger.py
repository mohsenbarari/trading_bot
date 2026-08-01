"""Root-only replay and ordering ledger for Witness-signed WAL-ack locators.

This is a deliberately local gate for the Object-Storage remote-ack locator
protocol.  It accepts only *already verified opaque* request and receipt
locators from :mod:`core.physical_wal_remote_ack_object_storage_transport`.
It has no Object Storage, age, PostgreSQL, Witness-query, peer, shell, Docker,
SSH, or deployment implementation.

The ledger is bound to one normal ``webapp_fi -> webapp_ir`` Writer term and
one pinned Witness public key.  It records only one-use locator identity,
nonce, and digest plus hashes of public Object pins; it never persists or
returns a raw locator.  A receipt locator can enter the ledger only after the
matching request Object pin has already entered it.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
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

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

import core.physical_wal_remote_ack as _ack
from core.append_only_sync_delta_batch import (
    AppendOnlySyncDeltaBatchError,
    OBJECT_KEY_RE,
    SHA256_RE,
    VERSION_ID_RE,
    canonical_json_bytes,
)
from core.object_delta_transport_binding import AGE_RECIPIENT_RE
from core.physical_wal_remote_ack import (
    MAX_PHYSICAL_WAL_REMOTE_ACK_BYTES,
    PhysicalWalRemoteAckBinding,
    PhysicalWalRemoteAckError,
)
from core.physical_wal_remote_ack_object_storage_transport import (
    MAX_PHYSICAL_WAL_REMOTE_ACK_TRANSPORT_CIPHERTEXT_BYTES,
    PhysicalWalRemoteAckObjectStorageTransportError,
    VerifiedPhysicalWalRemoteAckReceiptLocator,
    VerifiedPhysicalWalRemoteAckRequestLocator,
    require_verified_physical_wal_remote_ack_receipt_locator,
    require_verified_physical_wal_remote_ack_request_locator,
    verify_physical_wal_remote_ack_receipt_locator,
    verify_physical_wal_remote_ack_request_locator,
)


__all__ = (
    "DEFAULT_PHYSICAL_WAL_REMOTE_ACK_WITNESS_LOCATOR_LEDGER_MAXIMUM_ENTRIES",
    "PHYSICAL_WAL_REMOTE_ACK_WITNESS_LOCATOR_LEDGER_DEFAULT_ENABLED",
    "PHYSICAL_WAL_REMOTE_ACK_WITNESS_LOCATOR_LEDGER_SCHEMA",
    "PHYSICAL_WAL_REMOTE_ACK_WITNESS_LOCATOR_LEDGER_VERSION",
    "PhysicalWalRemoteAckWitnessLocatorLedgerConfig",
    "PhysicalWalRemoteAckWitnessLocatorLedgerError",
    "PhysicalWalRemoteAckWitnessReceiptLocatorAdmission",
    "PhysicalWalRemoteAckWitnessRequestLocatorAdmission",
    "admit_receipt_locator",
    "admit_request_locator",
)


PHYSICAL_WAL_REMOTE_ACK_WITNESS_LOCATOR_LEDGER_SCHEMA = (
    "gold-trade-physical-wal-remote-ack-witness-locator-ledger-v1"
)
PHYSICAL_WAL_REMOTE_ACK_WITNESS_LOCATOR_LEDGER_VERSION = 1
PHYSICAL_WAL_REMOTE_ACK_WITNESS_LOCATOR_LEDGER_DEFAULT_ENABLED = False

DEFAULT_PHYSICAL_WAL_REMOTE_ACK_WITNESS_LOCATOR_LEDGER_MAXIMUM_ENTRIES = 1_024
MAX_PHYSICAL_WAL_REMOTE_ACK_WITNESS_LOCATOR_LEDGER_ENTRIES = 4_096
MAX_PHYSICAL_WAL_REMOTE_ACK_WITNESS_LOCATOR_LEDGER_BYTES = 8 * 1024 * 1024

_LEDGER_DIRECTORY = "physical-wal-remote-ack-witness-locator-ledger"
_LEDGER_FILENAME = "ledger.json"
_LOCK_FILENAME = "ledger.lock"
_SAFE_COMPONENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$", re.ASCII)
_LOCATOR_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$", re.ASCII)
_LOCATOR_NONCE_RE = re.compile(r"^[A-Za-z0-9_-]{22,128}$", re.ASCII)
_ENTRY_KINDS = frozenset({"request", "receipt"})
_LEDGER_FIELDS = frozenset({"schema", "version", "configuration_sha256", "entries"})
_ENTRY_FIELDS = frozenset(
    {
        "sequence",
        "kind",
        "locator_id",
        "locator_nonce",
        "locator_sha256",
        "request_object_pin_sha256",
        "receipt_object_pin_sha256",
        "binding_sha256",
        "issued_at",
        "accepted_at",
    }
)


class PhysicalWalRemoteAckWitnessLocatorLedgerError(RuntimeError):
    """A fixed-code failure from the local locator replay gate."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class PhysicalWalRemoteAckWitnessLocatorLedgerConfig:
    """Root-owned state and exact FI-to-IR Witness route/term pins."""

    state_root: Path | None = None
    expected_binding: PhysicalWalRemoteAckBinding | None = None
    expected_witness_public_key: bytes | None = None
    enabled: bool = PHYSICAL_WAL_REMOTE_ACK_WITNESS_LOCATOR_LEDGER_DEFAULT_ENABLED
    maximum_entries: int = DEFAULT_PHYSICAL_WAL_REMOTE_ACK_WITNESS_LOCATOR_LEDGER_MAXIMUM_ENTRIES


@dataclass(frozen=True)
class PhysicalWalRemoteAckWitnessRequestLocatorAdmission:
    """Redacted local request-gate result; it is not transport authority."""

    locator_sha256: str
    request_object_pin_sha256: str
    binding_sha256: str
    idempotent: bool


@dataclass(frozen=True)
class PhysicalWalRemoteAckWitnessReceiptLocatorAdmission:
    """Redacted local receipt-gate result; it is not an acknowledgement."""

    locator_sha256: str
    request_object_pin_sha256: str
    receipt_object_pin_sha256: str
    binding_sha256: str
    idempotent: bool


@dataclass(frozen=True)
class _NormalisedConfig:
    state_root: Path
    expected_binding: PhysicalWalRemoteAckBinding
    expected_witness_public_key: bytes
    binding_sha256: str
    configuration_sha256: str
    maximum_entries: int


@dataclass(frozen=True)
class _LocatorFacts:
    kind: str
    locator_id: str
    locator_nonce: str
    locator_sha256: str
    request_object_pin_sha256: str
    receipt_object_pin_sha256: str | None
    binding_sha256: str
    issued_at: datetime


@dataclass(frozen=True)
class _LedgerEntry:
    sequence: int
    kind: str
    locator_id: str
    locator_nonce: str
    locator_sha256: str
    request_object_pin_sha256: str
    receipt_object_pin_sha256: str | None
    binding_sha256: str
    issued_at: datetime
    accepted_at: datetime


def _fail(code: str) -> None:
    raise PhysicalWalRemoteAckWitnessLocatorLedgerError(code)


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("WITNESS_LOCATOR_LEDGER_JSON_DUPLICATE_FIELD")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> None:
    _fail("WITNESS_LOCATOR_LEDGER_JSON_CONSTANT_FORBIDDEN")


def _canonical(value: object, *, code: str) -> bytes:
    try:
        return canonical_json_bytes(value)
    except (AppendOnlySyncDeltaBatchError, TypeError, ValueError):
        _fail(code)


def _exact_mapping(value: object, *, fields: frozenset[str], code: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != fields:
        _fail(code)
    return dict(value)


def _sha256(value: object, *, code: str) -> str:
    if type(value) is not str or SHA256_RE.fullmatch(value) is None or value == "0" * 64:
        _fail(code)
    return value


def _locator_id(value: object, *, code: str) -> str:
    if type(value) is not str or _LOCATOR_ID_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _locator_nonce(value: object, *, code: str) -> str:
    if type(value) is not str or _LOCATOR_NONCE_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _utc(value: object, *, code: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        _fail(code)
    return value.astimezone(timezone.utc)


def _timestamp(value: object, *, code: str) -> datetime:
    if type(value) is not str or not value or value != value.strip():
        _fail(code)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        _fail(code)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        _fail(code)
    result = parsed.astimezone(timezone.utc)
    if result.isoformat() != value:
        _fail(code)
    return result


def _timestamp_text(value: object, *, code: str) -> str:
    return _utc(value, code=code).isoformat()


def _public_key(value: object, *, code: str) -> bytes:
    if type(value) is not bytes or len(value) != 32 or value == b"\x00" * 32:
        _fail(code)
    try:
        Ed25519PublicKey.from_public_bytes(value)
    except ValueError:
        _fail(code)
    return value


def _binding_mapping(value: object, *, code: str) -> tuple[PhysicalWalRemoteAckBinding, dict[str, Any]]:
    if type(value) is not PhysicalWalRemoteAckBinding:
        _fail(code)
    try:
        raw = _ack._binding_mapping(value)
        normalised = _ack._binding_from_mapping(raw, label="Witness locator ledger")
        if _ack._binding_mapping(normalised) != raw:
            _fail(code)
    except (AttributeError, TypeError, PhysicalWalRemoteAckError):
        _fail(code)
    return normalised, raw


def _binding_sha256(value: object, *, code: str) -> tuple[PhysicalWalRemoteAckBinding, str]:
    binding, mapping = _binding_mapping(value, code=code)
    if (
        binding.source_site != "webapp_fi"
        or binding.destination_site != "webapp_ir"
        or binding.writer_term.writer_holder_site != "webapp_fi"
    ):
        _fail("WITNESS_LOCATOR_LEDGER_FI_TO_IR_ROUTE_REQUIRED")
    return binding, hashlib.sha256(_canonical(mapping, code=code)).hexdigest()


def _validate_state_ancestors(path: Path) -> None:
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        _fail("WITNESS_LOCATOR_LEDGER_PLATFORM_UNSAFE")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    descriptor = -1
    try:
        descriptor = os.open("/", flags)
        for component in path.parts[1:-1]:
            if component in {"", ".", ".."}:
                _fail("WITNESS_LOCATOR_LEDGER_STATE_ROOT_UNSAFE")
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
                _fail("WITNESS_LOCATOR_LEDGER_STATE_ROOT_UNSAFE")
    except PhysicalWalRemoteAckWitnessLocatorLedgerError:
        raise
    except OSError:
        _fail("WITNESS_LOCATOR_LEDGER_STATE_ROOT_UNSAFE")
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _secure_state_root(value: object) -> Path:
    if not isinstance(value, Path) or not value.is_absolute() or any(
        part in {"", ".", ".."} for part in value.parts[1:]
    ):
        _fail("WITNESS_LOCATOR_LEDGER_STATE_ROOT_UNSAFE")
    _validate_state_ancestors(value)
    try:
        resolved = value.resolve(strict=True)
        info = os.lstat(value)
    except OSError:
        _fail("WITNESS_LOCATOR_LEDGER_STATE_ROOT_UNSAFE")
    if (
        resolved != value
        or stat.S_ISLNK(info.st_mode)
        or not stat.S_ISDIR(info.st_mode)
        or info.st_uid != 0
        or stat.S_IMODE(info.st_mode) != 0o700
    ):
        _fail("WITNESS_LOCATOR_LEDGER_STATE_ROOT_UNSAFE")
    return resolved


def _normalise_config(value: object) -> _NormalisedConfig:
    if type(value) is not PhysicalWalRemoteAckWitnessLocatorLedgerConfig:
        _fail("WITNESS_LOCATOR_LEDGER_CONFIG_INVALID")
    if value.enabled is not True:
        _fail("WITNESS_LOCATOR_LEDGER_DISABLED")
    if os.geteuid() != 0:
        _fail("WITNESS_LOCATOR_LEDGER_ROOT_RUNTIME_REQUIRED")
    root = _secure_state_root(value.state_root)
    binding, binding_sha256 = _binding_sha256(
        value.expected_binding,
        code="WITNESS_LOCATOR_LEDGER_EXPECTED_BINDING_INVALID",
    )
    witness_key = _public_key(
        value.expected_witness_public_key,
        code="WITNESS_LOCATOR_LEDGER_WITNESS_KEY_INVALID",
    )
    if (
        type(value.maximum_entries) is not int
        or not 1 <= value.maximum_entries <= MAX_PHYSICAL_WAL_REMOTE_ACK_WITNESS_LOCATOR_LEDGER_ENTRIES
    ):
        _fail("WITNESS_LOCATOR_LEDGER_MAXIMUM_ENTRIES_INVALID")
    configuration_sha256 = hashlib.sha256(
        _canonical(
            {
                "binding": _ack._binding_mapping(binding),
                "binding_sha256": binding_sha256,
                "witness_public_key_sha256": hashlib.sha256(witness_key).hexdigest(),
                "maximum_entries": value.maximum_entries,
            },
            code="WITNESS_LOCATOR_LEDGER_CONFIGURATION_INVALID",
        )
    ).hexdigest()
    return _NormalisedConfig(
        state_root=root,
        expected_binding=binding,
        expected_witness_public_key=witness_key,
        binding_sha256=binding_sha256,
        configuration_sha256=configuration_sha256,
        maximum_entries=value.maximum_entries,
    )


def _object_pin_sha256(value: object, *, role: str, code: str) -> str:
    fields = (
        "role",
        "object_key",
        "version_id",
        "plaintext_sha256",
        "plaintext_bytes",
        "ciphertext_sha256",
        "ciphertext_bytes",
        "age_recipient",
    )
    try:
        item = {field: getattr(value, field) for field in fields}
    except AttributeError:
        _fail(code)
    if (
        item["role"] != role
        or type(item["object_key"]) is not str
        or OBJECT_KEY_RE.fullmatch(item["object_key"]) is None
        or ".." in item["object_key"].split("/")
        or type(item["version_id"]) is not str
        or VERSION_ID_RE.fullmatch(item["version_id"]) is None
        or type(item["plaintext_bytes"]) is not int
        or not 1 <= item["plaintext_bytes"] <= MAX_PHYSICAL_WAL_REMOTE_ACK_BYTES
        or type(item["ciphertext_bytes"]) is not int
        or not item["plaintext_bytes"]
        <= item["ciphertext_bytes"]
        <= MAX_PHYSICAL_WAL_REMOTE_ACK_TRANSPORT_CIPHERTEXT_BYTES
        or type(item["age_recipient"]) is not str
        or AGE_RECIPIENT_RE.fullmatch(item["age_recipient"]) is None
    ):
        _fail(code)
    plaintext_sha256 = _sha256(item["plaintext_sha256"], code=code)
    ciphertext_sha256 = _sha256(item["ciphertext_sha256"], code=code)
    return hashlib.sha256(
        _canonical(
            {
                "role": role,
                "object_key": item["object_key"],
                "version_id": item["version_id"],
                "plaintext_sha256": plaintext_sha256,
                "plaintext_bytes": item["plaintext_bytes"],
                "ciphertext_sha256": ciphertext_sha256,
                "ciphertext_bytes": item["ciphertext_bytes"],
                "age_recipient": item["age_recipient"],
            },
            code=code,
        )
    ).hexdigest()


def _verified_request_locator_facts(
    value: object,
    *,
    config: _NormalisedConfig,
    now: datetime,
) -> _LocatorFacts:
    try:
        checked = require_verified_physical_wal_remote_ack_request_locator(
            value,
            expected_binding=config.expected_binding,
            expected_witness_public_key=config.expected_witness_public_key,
            now=now,
        )
        verified = verify_physical_wal_remote_ack_request_locator(
            locator=checked.signed_locator,
            expected_binding=config.expected_binding,
            expected_witness_public_key=config.expected_witness_public_key,
            now=now,
        )
    except PhysicalWalRemoteAckObjectStorageTransportError:
        _fail("WITNESS_LOCATOR_LEDGER_REQUEST_UNVERIFIED_OR_STALE")
    if type(verified) is not VerifiedPhysicalWalRemoteAckRequestLocator:
        _fail("WITNESS_LOCATOR_LEDGER_REQUEST_UNVERIFIED_OR_STALE")
    binding, binding_sha256 = _binding_sha256(
        verified.binding,
        code="WITNESS_LOCATOR_LEDGER_REQUEST_UNVERIFIED_OR_STALE",
    )
    if binding != config.expected_binding or binding_sha256 != config.binding_sha256:
        _fail("WITNESS_LOCATOR_LEDGER_REQUEST_ROUTE_OR_TERM_MISMATCH")
    locator_hash = hashlib.sha256(verified.signed_locator).hexdigest()
    if _sha256(verified.locator_sha256, code="WITNESS_LOCATOR_LEDGER_REQUEST_UNVERIFIED_OR_STALE") != locator_hash:
        _fail("WITNESS_LOCATOR_LEDGER_REQUEST_UNVERIFIED_OR_STALE")
    return _LocatorFacts(
        kind="request",
        locator_id=_locator_id(verified.locator_id, code="WITNESS_LOCATOR_LEDGER_REQUEST_UNVERIFIED_OR_STALE"),
        locator_nonce=_locator_nonce(verified.locator_nonce, code="WITNESS_LOCATOR_LEDGER_REQUEST_UNVERIFIED_OR_STALE"),
        locator_sha256=locator_hash,
        request_object_pin_sha256=_object_pin_sha256(
            verified.request_object,
            role="request",
            code="WITNESS_LOCATOR_LEDGER_REQUEST_UNVERIFIED_OR_STALE",
        ),
        receipt_object_pin_sha256=None,
        binding_sha256=binding_sha256,
        issued_at=_utc(verified.issued_at, code="WITNESS_LOCATOR_LEDGER_REQUEST_UNVERIFIED_OR_STALE"),
    )


def _verified_receipt_locator_facts(
    value: object,
    *,
    config: _NormalisedConfig,
    now: datetime,
) -> _LocatorFacts:
    try:
        checked = require_verified_physical_wal_remote_ack_receipt_locator(
            value,
            expected_binding=config.expected_binding,
            expected_witness_public_key=config.expected_witness_public_key,
            now=now,
        )
        verified = verify_physical_wal_remote_ack_receipt_locator(
            locator=checked.signed_locator,
            expected_binding=config.expected_binding,
            expected_witness_public_key=config.expected_witness_public_key,
            now=now,
        )
    except PhysicalWalRemoteAckObjectStorageTransportError:
        _fail("WITNESS_LOCATOR_LEDGER_RECEIPT_UNVERIFIED_OR_STALE")
    if type(verified) is not VerifiedPhysicalWalRemoteAckReceiptLocator:
        _fail("WITNESS_LOCATOR_LEDGER_RECEIPT_UNVERIFIED_OR_STALE")
    binding, binding_sha256 = _binding_sha256(
        verified.binding,
        code="WITNESS_LOCATOR_LEDGER_RECEIPT_UNVERIFIED_OR_STALE",
    )
    if binding != config.expected_binding or binding_sha256 != config.binding_sha256:
        _fail("WITNESS_LOCATOR_LEDGER_RECEIPT_ROUTE_OR_TERM_MISMATCH")
    locator_hash = hashlib.sha256(verified.signed_locator).hexdigest()
    if _sha256(verified.locator_sha256, code="WITNESS_LOCATOR_LEDGER_RECEIPT_UNVERIFIED_OR_STALE") != locator_hash:
        _fail("WITNESS_LOCATOR_LEDGER_RECEIPT_UNVERIFIED_OR_STALE")
    return _LocatorFacts(
        kind="receipt",
        locator_id=_locator_id(verified.locator_id, code="WITNESS_LOCATOR_LEDGER_RECEIPT_UNVERIFIED_OR_STALE"),
        locator_nonce=_locator_nonce(verified.locator_nonce, code="WITNESS_LOCATOR_LEDGER_RECEIPT_UNVERIFIED_OR_STALE"),
        locator_sha256=locator_hash,
        request_object_pin_sha256=_object_pin_sha256(
            verified.request_object,
            role="request",
            code="WITNESS_LOCATOR_LEDGER_RECEIPT_UNVERIFIED_OR_STALE",
        ),
        receipt_object_pin_sha256=_object_pin_sha256(
            verified.receipt_object,
            role="receipt",
            code="WITNESS_LOCATOR_LEDGER_RECEIPT_UNVERIFIED_OR_STALE",
        ),
        binding_sha256=binding_sha256,
        issued_at=_utc(verified.issued_at, code="WITNESS_LOCATOR_LEDGER_RECEIPT_UNVERIFIED_OR_STALE"),
    )


def _ledger_directory_if_present(state_root: Path) -> Path | None:
    path = state_root / _LEDGER_DIRECTORY
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return None
    except OSError:
        _fail("WITNESS_LOCATOR_LEDGER_DIRECTORY_UNSAFE")
    try:
        resolved = path.resolve(strict=True)
    except OSError:
        _fail("WITNESS_LOCATOR_LEDGER_DIRECTORY_UNSAFE")
    if (
        resolved != path
        or stat.S_ISLNK(info.st_mode)
        or not stat.S_ISDIR(info.st_mode)
        or info.st_uid != 0
        or stat.S_IMODE(info.st_mode) != 0o700
    ):
        _fail("WITNESS_LOCATOR_LEDGER_DIRECTORY_UNSAFE")
    return path


def _fsync_directory(path: Path) -> None:
    if not hasattr(os, "O_DIRECTORY"):
        _fail("WITNESS_LOCATOR_LEDGER_PLATFORM_UNSAFE")
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
        )
        os.fsync(descriptor)
    except OSError:
        _fail("WITNESS_LOCATOR_LEDGER_DIRECTORY_FSYNC_FAILED")
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _create_ledger_directory(state_root: Path) -> Path:
    path = state_root / _LEDGER_DIRECTORY
    try:
        os.mkdir(path, mode=0o700)
    except FileExistsError:
        pass
    except OSError:
        _fail("WITNESS_LOCATOR_LEDGER_DIRECTORY_CREATE_FAILED")
    directory = _ledger_directory_if_present(state_root)
    if directory is None:
        _fail("WITNESS_LOCATOR_LEDGER_DIRECTORY_CREATE_FAILED")
    _fsync_directory(state_root)
    return directory


def _open_lock(path: Path, *, permit_initial_create: bool) -> int:
    if not hasattr(os, "O_NOFOLLOW"):
        _fail("WITNESS_LOCATOR_LEDGER_PLATFORM_UNSAFE")
    flags = os.O_RDWR | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    descriptor = -1
    try:
        try:
            descriptor = os.open(path, flags)
        except FileNotFoundError:
            if not permit_initial_create:
                _fail("WITNESS_LOCATOR_LEDGER_LOCK_MISSING")
            try:
                descriptor = os.open(path, flags | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                # A concurrent valid first request won the initialization
                # race. Open its exact lock rather than treating the safe
                # create-only collision as an admission failure.
                descriptor = os.open(path, flags)
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_uid != 0
            or stat.S_IMODE(info.st_mode) != 0o600
        ):
            _fail("WITNESS_LOCATOR_LEDGER_LOCK_UNSAFE")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        return descriptor
    except PhysicalWalRemoteAckWitnessLocatorLedgerError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError:
        if descriptor >= 0:
            os.close(descriptor)
        _fail("WITNESS_LOCATOR_LEDGER_LOCK_OPEN_FAILED")


@contextmanager
def _locked_ledger_directory(
    config: _NormalisedConfig,
    *,
    allow_initial_directory: bool,
) -> Iterator[tuple[Path, Path]]:
    directory = _ledger_directory_if_present(config.state_root)
    if directory is None:
        if not allow_initial_directory:
            _fail("WITNESS_LOCATOR_LEDGER_RECEIPT_REQUEST_NOT_ADMITTED")
        # Every request fact has been verified before this call.  Creating the
        # first state directory here therefore cannot be caused by invalid
        # locator input.
        directory = _create_ledger_directory(config.state_root)
    ledger_path = directory / _LEDGER_FILENAME
    lock_path = directory / _LOCK_FILENAME
    # A receipt must never initialize a partial ledger directory: otherwise a
    # receipt-before-request could leave durable lock state behind. Only the
    # fully pre-verified request path may recover an empty first directory.
    permit_initial_lock = allow_initial_directory and not ledger_path.exists()
    descriptor = _open_lock(lock_path, permit_initial_create=permit_initial_lock)
    try:
        yield directory, ledger_path
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _open_existing_ledger(path: Path) -> int:
    if not hasattr(os, "O_NOFOLLOW"):
        _fail("WITNESS_LOCATOR_LEDGER_PLATFORM_UNSAFE")
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0))
    except FileNotFoundError:
        raise
    except OSError:
        _fail("WITNESS_LOCATOR_LEDGER_STATE_OPEN_FAILED")
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_uid != 0
            or stat.S_IMODE(info.st_mode) != 0o600
            or not 1 <= info.st_size <= MAX_PHYSICAL_WAL_REMOTE_ACK_WITNESS_LOCATOR_LEDGER_BYTES
        ):
            _fail("WITNESS_LOCATOR_LEDGER_STATE_UNSAFE")
    except Exception:
        os.close(descriptor)
        raise
    return descriptor


def _read_canonical_ledger(path: Path) -> dict[str, Any] | None:
    try:
        descriptor = _open_existing_ledger(path)
    except FileNotFoundError:
        return None
    try:
        size = os.fstat(descriptor).st_size
        raw = bytearray()
        while len(raw) < size:
            try:
                chunk = os.read(descriptor, size - len(raw))
            except OSError:
                _fail("WITNESS_LOCATOR_LEDGER_STATE_READ_FAILED")
            if not chunk:
                _fail("WITNESS_LOCATOR_LEDGER_STATE_READ_FAILED")
            raw.extend(chunk)
        try:
            if os.read(descriptor, 1):
                _fail("WITNESS_LOCATOR_LEDGER_STATE_READ_FAILED")
        except OSError:
            _fail("WITNESS_LOCATOR_LEDGER_STATE_READ_FAILED")
    finally:
        os.close(descriptor)
    try:
        parsed = json.loads(
            bytes(raw).decode("ascii", "strict"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except PhysicalWalRemoteAckWitnessLocatorLedgerError:
        raise
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
        _fail("WITNESS_LOCATOR_LEDGER_STATE_JSON_INVALID")
    if type(parsed) is not dict or _canonical(
        parsed, code="WITNESS_LOCATOR_LEDGER_STATE_CANONICAL_INVALID"
    ) != bytes(raw):
        _fail("WITNESS_LOCATOR_LEDGER_STATE_CANONICAL_INVALID")
    return parsed


def _entry_from_mapping(value: object, *, config: _NormalisedConfig) -> _LedgerEntry:
    item = _exact_mapping(value, fields=_ENTRY_FIELDS, code="WITNESS_LOCATOR_LEDGER_ENTRY_FIELDS_INVALID")
    if type(item["sequence"]) is not int or item["sequence"] < 1:
        _fail("WITNESS_LOCATOR_LEDGER_ENTRY_SEQUENCE_INVALID")
    kind = item["kind"]
    if type(kind) is not str or kind not in _ENTRY_KINDS:
        _fail("WITNESS_LOCATOR_LEDGER_ENTRY_KIND_INVALID")
    receipt_pin = item["receipt_object_pin_sha256"]
    if kind == "request":
        if receipt_pin is not None:
            _fail("WITNESS_LOCATOR_LEDGER_ENTRY_PIN_INVALID")
    else:
        receipt_pin = _sha256(receipt_pin, code="WITNESS_LOCATOR_LEDGER_ENTRY_PIN_INVALID")
    binding_sha256 = _sha256(item["binding_sha256"], code="WITNESS_LOCATOR_LEDGER_ENTRY_BINDING_INVALID")
    if binding_sha256 != config.binding_sha256:
        _fail("WITNESS_LOCATOR_LEDGER_CONFIGURATION_CONFLICT")
    return _LedgerEntry(
        sequence=item["sequence"],
        kind=kind,
        locator_id=_locator_id(item["locator_id"], code="WITNESS_LOCATOR_LEDGER_ENTRY_IDENTITY_INVALID"),
        locator_nonce=_locator_nonce(item["locator_nonce"], code="WITNESS_LOCATOR_LEDGER_ENTRY_IDENTITY_INVALID"),
        locator_sha256=_sha256(item["locator_sha256"], code="WITNESS_LOCATOR_LEDGER_ENTRY_HASH_INVALID"),
        request_object_pin_sha256=_sha256(
            item["request_object_pin_sha256"], code="WITNESS_LOCATOR_LEDGER_ENTRY_PIN_INVALID"
        ),
        receipt_object_pin_sha256=receipt_pin,
        binding_sha256=binding_sha256,
        issued_at=_timestamp(item["issued_at"], code="WITNESS_LOCATOR_LEDGER_ENTRY_TIME_INVALID"),
        accepted_at=_timestamp(item["accepted_at"], code="WITNESS_LOCATOR_LEDGER_ENTRY_TIME_INVALID"),
    )


def _load_entries(path: Path, *, config: _NormalisedConfig) -> tuple[_LedgerEntry, ...]:
    raw = _read_canonical_ledger(path)
    if raw is None:
        return ()
    ledger = _exact_mapping(raw, fields=_LEDGER_FIELDS, code="WITNESS_LOCATOR_LEDGER_STATE_FIELDS_INVALID")
    if (
        ledger["schema"] != PHYSICAL_WAL_REMOTE_ACK_WITNESS_LOCATOR_LEDGER_SCHEMA
        or ledger["version"] != PHYSICAL_WAL_REMOTE_ACK_WITNESS_LOCATOR_LEDGER_VERSION
        or _sha256(ledger["configuration_sha256"], code="WITNESS_LOCATOR_LEDGER_CONFIGURATION_INVALID")
        != config.configuration_sha256
        or type(ledger["entries"]) is not list
    ):
        _fail("WITNESS_LOCATOR_LEDGER_CONFIGURATION_CONFLICT")
    if len(ledger["entries"]) > config.maximum_entries:
        _fail("WITNESS_LOCATOR_LEDGER_ENTRY_LIMIT_EXCEEDED")
    entries = tuple(_entry_from_mapping(value, config=config) for value in ledger["entries"])
    if tuple(entry.sequence for entry in entries) != tuple(range(1, len(entries) + 1)):
        _fail("WITNESS_LOCATOR_LEDGER_ENTRY_ORDER_INVALID")
    ids = {entry.locator_id for entry in entries}
    nonces = {entry.locator_nonce for entry in entries}
    hashes = {entry.locator_sha256 for entry in entries}
    if len(ids) != len(entries) or len(nonces) != len(entries) or len(hashes) != len(entries):
        _fail("WITNESS_LOCATOR_LEDGER_REPLAY_INDEX_CONFLICT")
    requests: dict[str, _LedgerEntry] = {}
    receipt_requests: set[str] = set()
    receipt_pins: set[str] = set()
    for entry in entries:
        if entry.kind == "request":
            if entry.request_object_pin_sha256 in requests:
                _fail("WITNESS_LOCATOR_LEDGER_REQUEST_OBJECT_REUSED")
            requests[entry.request_object_pin_sha256] = entry
            continue
        if (
            entry.receipt_object_pin_sha256 is None
            or entry.request_object_pin_sha256 not in requests
            or requests[entry.request_object_pin_sha256].sequence >= entry.sequence
            or entry.request_object_pin_sha256 in receipt_requests
            or entry.receipt_object_pin_sha256 in receipt_pins
        ):
            _fail("WITNESS_LOCATOR_LEDGER_ENTRY_ORDER_INVALID")
        receipt_requests.add(entry.request_object_pin_sha256)
        receipt_pins.add(entry.receipt_object_pin_sha256)
    return entries


def _entry_mapping(value: _LedgerEntry) -> dict[str, Any]:
    return {
        "sequence": value.sequence,
        "kind": value.kind,
        "locator_id": value.locator_id,
        "locator_nonce": value.locator_nonce,
        "locator_sha256": value.locator_sha256,
        "request_object_pin_sha256": value.request_object_pin_sha256,
        "receipt_object_pin_sha256": value.receipt_object_pin_sha256,
        "binding_sha256": value.binding_sha256,
        "issued_at": _timestamp_text(value.issued_at, code="WITNESS_LOCATOR_LEDGER_ENTRY_TIME_INVALID"),
        "accepted_at": _timestamp_text(value.accepted_at, code="WITNESS_LOCATOR_LEDGER_ENTRY_TIME_INVALID"),
    }


def _ledger_mapping(*, config: _NormalisedConfig, entries: Sequence[_LedgerEntry]) -> dict[str, Any]:
    return {
        "schema": PHYSICAL_WAL_REMOTE_ACK_WITNESS_LOCATOR_LEDGER_SCHEMA,
        "version": PHYSICAL_WAL_REMOTE_ACK_WITNESS_LOCATOR_LEDGER_VERSION,
        "configuration_sha256": config.configuration_sha256,
        "entries": [_entry_mapping(entry) for entry in entries],
    }


def _write_atomic_ledger(path: Path, *, value: Mapping[str, Any]) -> None:
    if not hasattr(os, "O_NOFOLLOW"):
        _fail("WITNESS_LOCATOR_LEDGER_PLATFORM_UNSAFE")
    payload = _canonical(value, code="WITNESS_LOCATOR_LEDGER_STATE_CANONICAL_INVALID")
    if not 1 <= len(payload) <= MAX_PHYSICAL_WAL_REMOTE_ACK_WITNESS_LOCATOR_LEDGER_BYTES:
        _fail("WITNESS_LOCATOR_LEDGER_STATE_SIZE_INVALID")
    temporary = path.parent / ("." + path.name + "." + secrets.token_hex(16) + ".tmp")
    descriptor = -1
    replaced = False
    try:
        descriptor = os.open(
            temporary,
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
            _fail("WITNESS_LOCATOR_LEDGER_TEMPORARY_UNSAFE")
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                _fail("WITNESS_LOCATOR_LEDGER_TEMPORARY_WRITE_FAILED")
            view = view[written:]
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary, path)
        replaced = True
        _fsync_directory(path.parent)
        info = os.lstat(path)
        if (
            stat.S_ISLNK(info.st_mode)
            or not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_uid != 0
            or stat.S_IMODE(info.st_mode) != 0o600
        ):
            _fail("WITNESS_LOCATOR_LEDGER_STATE_UNSAFE")
    except PhysicalWalRemoteAckWitnessLocatorLedgerError:
        raise
    except OSError:
        _fail("WITNESS_LOCATOR_LEDGER_STATE_WRITE_FAILED")
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if not replaced:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            except OSError:
                pass


def _existing_or_replay_conflict(
    entries: Sequence[_LedgerEntry],
    *,
    facts: _LocatorFacts,
) -> _LedgerEntry | None:
    by_hash = {entry.locator_sha256: entry for entry in entries}
    by_id = {entry.locator_id: entry for entry in entries}
    by_nonce = {entry.locator_nonce: entry for entry in entries}
    existing = by_hash.get(facts.locator_sha256)
    if existing is not None:
        if (
            existing.kind == facts.kind
            and existing.locator_id == facts.locator_id
            and existing.locator_nonce == facts.locator_nonce
            and existing.request_object_pin_sha256 == facts.request_object_pin_sha256
            and existing.receipt_object_pin_sha256 == facts.receipt_object_pin_sha256
            and existing.binding_sha256 == facts.binding_sha256
        ):
            return existing
        _fail("WITNESS_LOCATOR_LEDGER_LOCATOR_HASH_REUSE_CONFLICT")
    if facts.locator_id in by_id:
        _fail("WITNESS_LOCATOR_LEDGER_LOCATOR_ID_REUSE_CONFLICT")
    if facts.locator_nonce in by_nonce:
        _fail("WITNESS_LOCATOR_LEDGER_LOCATOR_NONCE_REUSE_CONFLICT")
    return None


def _request_admission(
    entry: _LedgerEntry,
    *,
    idempotent: bool,
) -> PhysicalWalRemoteAckWitnessRequestLocatorAdmission:
    return PhysicalWalRemoteAckWitnessRequestLocatorAdmission(
        locator_sha256=entry.locator_sha256,
        request_object_pin_sha256=entry.request_object_pin_sha256,
        binding_sha256=entry.binding_sha256,
        idempotent=idempotent,
    )


def _receipt_admission(
    entry: _LedgerEntry,
    *,
    idempotent: bool,
) -> PhysicalWalRemoteAckWitnessReceiptLocatorAdmission:
    if entry.receipt_object_pin_sha256 is None:
        _fail("WITNESS_LOCATOR_LEDGER_ENTRY_PIN_INVALID")
    return PhysicalWalRemoteAckWitnessReceiptLocatorAdmission(
        locator_sha256=entry.locator_sha256,
        request_object_pin_sha256=entry.request_object_pin_sha256,
        receipt_object_pin_sha256=entry.receipt_object_pin_sha256,
        binding_sha256=entry.binding_sha256,
        idempotent=idempotent,
    )


def admit_request_locator(
    *,
    config: object,
    locator: object,
    now: datetime,
) -> PhysicalWalRemoteAckWitnessRequestLocatorAdmission:
    """Durably admit one verified FI-to-IR request locator exactly once.

    Exact retry returns the same redacted admission marked ``idempotent`` and
    creates no second ledger entry.  Reusing an ID, nonce, digest, or request
    Object pin in a different locator fails closed before any transport call.
    """

    normalized = _normalise_config(config)
    observed_now = _utc(now, code="WITNESS_LOCATOR_LEDGER_CLOCK_INVALID")
    facts = _verified_request_locator_facts(locator, config=normalized, now=observed_now)
    with _locked_ledger_directory(normalized, allow_initial_directory=True) as (_directory, ledger_path):
        entries = _load_entries(ledger_path, config=normalized)
        existing = _existing_or_replay_conflict(entries, facts=facts)
        if existing is not None:
            return _request_admission(existing, idempotent=True)
        if any(entry.request_object_pin_sha256 == facts.request_object_pin_sha256 for entry in entries):
            _fail("WITNESS_LOCATOR_LEDGER_REQUEST_OBJECT_REUSE_CONFLICT")
        if len(entries) >= normalized.maximum_entries:
            _fail("WITNESS_LOCATOR_LEDGER_ENTRY_LIMIT_EXCEEDED")
        entry = _LedgerEntry(
            sequence=len(entries) + 1,
            kind="request",
            locator_id=facts.locator_id,
            locator_nonce=facts.locator_nonce,
            locator_sha256=facts.locator_sha256,
            request_object_pin_sha256=facts.request_object_pin_sha256,
            receipt_object_pin_sha256=None,
            binding_sha256=facts.binding_sha256,
            issued_at=facts.issued_at,
            accepted_at=observed_now,
        )
        _write_atomic_ledger(
            ledger_path,
            value=_ledger_mapping(config=normalized, entries=(*entries, entry)),
        )
        return _request_admission(entry, idempotent=False)


def admit_receipt_locator(
    *,
    config: object,
    locator: object,
    now: datetime,
) -> PhysicalWalRemoteAckWitnessReceiptLocatorAdmission:
    """Durably admit one receipt locator only after its exact request locator.

    The receipt is merely a locator admission.  It neither downloads an
    Object, verifies a remote receipt, nor grants acknowledgement, writer, or
    promotion authority.
    """

    normalized = _normalise_config(config)
    observed_now = _utc(now, code="WITNESS_LOCATOR_LEDGER_CLOCK_INVALID")
    facts = _verified_receipt_locator_facts(locator, config=normalized, now=observed_now)
    # Do not create any filesystem state for a receipt that cannot possibly
    # have a previously admitted request.
    if _ledger_directory_if_present(normalized.state_root) is None:
        _fail("WITNESS_LOCATOR_LEDGER_RECEIPT_REQUEST_NOT_ADMITTED")
    with _locked_ledger_directory(normalized, allow_initial_directory=False) as (_directory, ledger_path):
        entries = _load_entries(ledger_path, config=normalized)
        existing = _existing_or_replay_conflict(entries, facts=facts)
        if existing is not None:
            return _receipt_admission(existing, idempotent=True)
        request_entries = {
            entry.request_object_pin_sha256: entry
            for entry in entries
            if entry.kind == "request"
        }
        if facts.request_object_pin_sha256 not in request_entries:
            _fail("WITNESS_LOCATOR_LEDGER_RECEIPT_REQUEST_NOT_ADMITTED")
        if any(
            entry.kind == "receipt"
            and entry.request_object_pin_sha256 == facts.request_object_pin_sha256
            for entry in entries
        ):
            _fail("WITNESS_LOCATOR_LEDGER_RECEIPT_FOR_REQUEST_REPLAYED")
        if any(
            entry.kind == "receipt"
            and entry.receipt_object_pin_sha256 == facts.receipt_object_pin_sha256
            for entry in entries
        ):
            _fail("WITNESS_LOCATOR_LEDGER_RECEIPT_OBJECT_REUSE_CONFLICT")
        if len(entries) >= normalized.maximum_entries:
            _fail("WITNESS_LOCATOR_LEDGER_ENTRY_LIMIT_EXCEEDED")
        entry = _LedgerEntry(
            sequence=len(entries) + 1,
            kind="receipt",
            locator_id=facts.locator_id,
            locator_nonce=facts.locator_nonce,
            locator_sha256=facts.locator_sha256,
            request_object_pin_sha256=facts.request_object_pin_sha256,
            receipt_object_pin_sha256=facts.receipt_object_pin_sha256,
            binding_sha256=facts.binding_sha256,
            issued_at=facts.issued_at,
            accepted_at=observed_now,
        )
        _write_atomic_ledger(
            ledger_path,
            value=_ledger_mapping(config=normalized, entries=(*entries, entry)),
        )
        return _receipt_admission(entry, idempotent=False)
