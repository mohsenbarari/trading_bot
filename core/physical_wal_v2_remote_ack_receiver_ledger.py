"""Root-owned atomic durable ledger for V2 remote acknowledgements.

This is the first V2 component allowed to make a durable-receipt claim.  It
accepts only opaque V2 request/recovery capabilities, creates the destination
replay receipt under an exclusive local lock, atomically fsyncs a canonical
root-owned ledger, and returns a separate opaque ledger capability *after*
that write succeeds.

It has no network, Object Storage, PostgreSQL, Witness, shell, Docker, or
promotion implementation.  A local recovery projection is never enough to
issue a receipt: the caller must also supply the independently revalidated
V2 Full-Matrix recovery bridge, which carries the signed target-readback
attestation.  The ledger cross-pins that bridge to the exact signed request
and stores those immutable commitments with the fsync'd entry.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Iterator, Mapping, Sequence
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

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core.append_only_sync_delta_batch import SHA256_RE, canonical_json_bytes
from core.physical_full_matrix_v2_recovery_evidence import (
    PHYSICAL_FULL_MATRIX_V2_RECOVERY_EVIDENCE_SCHEMA,
    PhysicalFullMatrixV2RecoveryEvidenceError,
    VerifiedPhysicalFullMatrixV2RecoveryEvidence,
    require_verified_physical_full_matrix_v2_recovery_evidence,
)
from core.physical_wal_v2_remote_ack import (
    MAX_PHYSICAL_WAL_V2_REMOTE_ACK_BYTES,
    PhysicalWalV2RemoteAckConfig,
    PhysicalWalV2RemoteAckError,
    PhysicalWalV2RemoteAckReceiverRecoveryEvidence,
    VerifiedPhysicalWalV2RemoteAckEvidence,
    VerifiedPhysicalWalV2RemoteAckReceiverRecoveryEvidence,
    VerifiedPhysicalWalV2RemoteAckRequest,
    build_physical_wal_v2_remote_ack_receipt,
    require_verified_physical_wal_v2_remote_ack_receiver_recovery_evidence,
    require_verified_physical_wal_v2_remote_ack_request,
    verify_physical_wal_v2_remote_ack_evidence,
    verify_physical_wal_v2_remote_ack_request,
)


__all__ = (
    "DEFAULT_PHYSICAL_WAL_V2_REMOTE_ACK_RECEIVER_LEDGER_MAXIMUM_ENTRIES",
    "PHYSICAL_WAL_V2_REMOTE_ACK_RECEIVER_LEDGER_DEFAULT_ENABLED",
    "PHYSICAL_WAL_V2_REMOTE_ACK_RECEIVER_LEDGER_SCHEMA",
    "PhysicalWalV2RemoteAckReceiverLedgerConfig",
    "PhysicalWalV2RemoteAckReceiverLedgerError",
    "PhysicalWalV2RemoteAckReceiverLedgerResult",
    "PhysicalWalV2RemoteAckReceiverLedgerRuntime",
    "VerifiedPhysicalWalV2RemoteAckReceiverLedgerReceipt",
    "issue_physical_wal_v2_remote_ack_receiver_receipt",
    "require_verified_physical_wal_v2_remote_ack_receiver_ledger_receipt",
)


PHYSICAL_WAL_V2_REMOTE_ACK_RECEIVER_LEDGER_SCHEMA = (
    "gold-trade-physical-wal-v2-remote-ack-receiver-ledger-v3"
)
PHYSICAL_WAL_V2_REMOTE_ACK_RECEIVER_LEDGER_VERSION = 3
PHYSICAL_WAL_V2_REMOTE_ACK_RECEIVER_LEDGER_DEFAULT_ENABLED = False
DEFAULT_PHYSICAL_WAL_V2_REMOTE_ACK_RECEIVER_LEDGER_MAXIMUM_ENTRIES = 256
MAX_PHYSICAL_WAL_V2_REMOTE_ACK_RECEIVER_LEDGER_ENTRIES = 4_096
MAX_PHYSICAL_WAL_V2_REMOTE_ACK_RECEIVER_LEDGER_BYTES = 64 * 1024 * 1024

_LEDGER_DIRECTORY = "physical-wal-v2-remote-ack-ledger"
_LEDGER_FILENAME = "ledger.json"
_LOCK_FILENAME = "ledger.lock"
_SAFE_COMPONENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$", re.ASCII)
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$", re.ASCII)
_NONCE_RE = re.compile(r"^[A-Za-z0-9_-]{22,128}$", re.ASCII)
_LSN_RE = re.compile(r"^(?:0|[1-9A-F][0-9A-F]{0,7})/(?:0|[1-9A-F][0-9A-F]{0,7})$", re.ASCII)

_LEDGER_FIELDS = frozenset(
    {"schema", "version", "configuration_sha256", "clock_floor", "entries"}
)
_ENTRY_FIELDS = frozenset(
    {
        "ledger_entry_id",
        "source_request_sha256",
        "source_request_base64",
        "context_sha256",
        "request_id",
        "request_nonce",
        "receiver_recovery_evidence_sha256",
        "receiver_replay_lsn",
        "receiver_observed_at",
        "target_recovery_evidence_sha256",
        "readback_attestation_sha256",
        "readback_attestation_id",
        "readback_attestation_nonce",
        "stage_receipt_sha256",
        "witness_transition_id",
        "target_recovery_observed_at",
        "receipt_id",
        "receipt_nonce",
        "destination_receipt_sha256",
        "destination_receipt_base64",
        "acknowledged_at",
        "committed_at",
        "durable_ledger_entry_sha256",
    }
)
_CAPABILITY = object()


class PhysicalWalV2RemoteAckReceiverLedgerError(ValueError):
    """The local V2 receiver ledger cannot safely issue or revalidate a receipt."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class PhysicalWalV2RemoteAckReceiverLedgerConfig:
    """Root-owned local state and one exact V2 context/key policy."""

    state_root: Path | None = None
    remote_ack_config: PhysicalWalV2RemoteAckConfig | None = None
    enabled: bool = PHYSICAL_WAL_V2_REMOTE_ACK_RECEIVER_LEDGER_DEFAULT_ENABLED
    maximum_entries: int = DEFAULT_PHYSICAL_WAL_V2_REMOTE_ACK_RECEIVER_LEDGER_MAXIMUM_ENTRIES


@dataclass(frozen=True, eq=False)
class VerifiedPhysicalWalV2RemoteAckReceiverLedgerReceipt:
    """Opaque proof that the canonical pair was atomically persisted locally."""

    schema: str
    canonical_source_request: bytes
    canonical_destination_receipt: bytes
    source_request_sha256: str
    destination_receipt_sha256: str
    context_sha256: str
    request_id: str
    request_nonce: str
    receipt_id: str
    receipt_nonce: str
    receiver_recovery_evidence_sha256: str
    receiver_replay_lsn: str
    target_recovery_evidence_sha256: str
    readback_attestation_sha256: str
    readback_attestation_id: str
    readback_attestation_nonce: str
    stage_receipt_sha256: str
    witness_transition_id: str
    target_recovery_observed_at: datetime
    ledger_entry_id: str
    durable_ledger_entry_sha256: str
    committed_at: datetime
    ledger_path: Path
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("PHYSICAL_WAL_V2_REMOTE_ACK_RECEIVER_LEDGER_SERIALIZATION_FORBIDDEN")


@dataclass(frozen=True)
class PhysicalWalV2RemoteAckReceiverLedgerResult:
    """One durable V2 receipt result; it remains less than writer authority."""

    receipt: VerifiedPhysicalWalV2RemoteAckReceiverLedgerReceipt
    remote_ack_evidence: VerifiedPhysicalWalV2RemoteAckEvidence
    idempotent: bool


class PhysicalWalV2RemoteAckReceiverLedgerRuntime(Protocol):
    """The real root-owned local runtime contract implemented by this module."""

    def issue_after_durable_recovery(
        self,
        *,
        config: PhysicalWalV2RemoteAckReceiverLedgerConfig,
        source_request: VerifiedPhysicalWalV2RemoteAckRequest,
        receiver_recovery_evidence: VerifiedPhysicalWalV2RemoteAckReceiverRecoveryEvidence,
        target_recovery_evidence: VerifiedPhysicalFullMatrixV2RecoveryEvidence,
        remote_ack_evidence: VerifiedPhysicalWalV2RemoteAckEvidence | None,
        destination_signer: object | None,
        now: datetime,
    ) -> PhysicalWalV2RemoteAckReceiverLedgerResult: ...


@dataclass(frozen=True)
class _ConfigFacts:
    state_root: Path
    remote_ack_config: PhysicalWalV2RemoteAckConfig
    maximum_entries: int
    configuration_sha256: str


@dataclass(frozen=True)
class _Entry:
    ledger_entry_id: str
    source_request_sha256: str
    source_request: bytes
    context_sha256: str
    request_id: str
    request_nonce: str
    receiver_recovery_evidence_sha256: str
    receiver_replay_lsn: str
    receiver_observed_at: datetime
    target_recovery_evidence_sha256: str
    readback_attestation_sha256: str
    readback_attestation_id: str
    readback_attestation_nonce: str
    stage_receipt_sha256: str
    witness_transition_id: str
    target_recovery_observed_at: datetime
    receipt_id: str
    receipt_nonce: str
    destination_receipt_sha256: str
    destination_receipt: bytes
    acknowledged_at: datetime
    committed_at: datetime
    durable_ledger_entry_sha256: str


def _fail(code: str) -> None:
    raise PhysicalWalV2RemoteAckReceiverLedgerError(code)


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("V2_REMOTE_ACK_LEDGER_JSON_DUPLICATE_FIELD")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> None:
    _fail("V2_REMOTE_ACK_LEDGER_JSON_CONSTANT_FORBIDDEN")


def _canonical(value: object, *, code: str) -> bytes:
    try:
        return canonical_json_bytes(value)
    except (TypeError, ValueError) as exc:
        raise PhysicalWalV2RemoteAckReceiverLedgerError(code) from exc


def _exact_mapping(value: object, *, fields: frozenset[str], code: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != fields:
        _fail(code)
    return dict(value)


def _sha256(value: object, *, code: str) -> str:
    if type(value) is not str or SHA256_RE.fullmatch(value) is None or value == "0" * 64:
        _fail(code)
    return value


def _identifier(value: object, *, code: str) -> str:
    if type(value) is not str or _ID_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _nonce(value: object, *, code: str) -> str:
    if type(value) is not str or _NONCE_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _utc(value: object, *, code: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        _fail(code)
    return value.astimezone(timezone.utc)


def _render_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _timestamp(value: object, *, code: str) -> datetime:
    if type(value) is not str or not value or value != value.strip():
        _fail(code)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        _fail(code)
    result = _utc(parsed, code=code)
    if _render_timestamp(result) != value:
        _fail(code)
    return result


def _trusted_now() -> datetime:
    """Read the receiver host's clock inside this root-owned boundary.

    ``issue`` and ``require`` deliberately do not use their public ``now``
    parameter for admission.  Keeping this small function private also makes
    deterministic tests explicit: they patch the receiver-owned clock rather
    than handing an arbitrary past timestamp to the runtime.
    """

    return datetime.now(timezone.utc)


def _lsn(value: object, *, code: str) -> tuple[str, int]:
    if type(value) is not str or _LSN_RE.fullmatch(value) is None:
        _fail(code)
    high, low = value.split("/", 1)
    return value, (int(high, 16) << 32) | int(low, 16)


def _require_fd_platform() -> None:
    if not hasattr(os, "O_DIRECTORY") or not hasattr(os, "O_NOFOLLOW"):
        _fail("V2_REMOTE_ACK_LEDGER_PLATFORM_UNSAFE")


def _safe_root_path(value: object, *, code: str) -> Path:
    if (
        not isinstance(value, Path)
        or not value.is_absolute()
        or len(value.parts) < 2
        or any(part in {"", ".", ".."} for part in value.parts[1:])
        or len(str(value)) > 4096
    ):
        _fail(code)
    return value


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
    except OSError:
        _fail(code)


def _open_secure_root(value: Path) -> int:
    """Open every path component once and retain an anchored final fd.

    No later ledger operation reopens a pathname below this root.  This
    avoids a caller-controlled ``resolve/lstat/open`` time-of-check race and
    makes a rename of any ancestor irrelevant after it has been opened.
    """

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
                or (not final and (stat.S_IMODE(metadata.st_mode) & 0o022) and not (metadata.st_mode & stat.S_ISVTX))
                or (final and stat.S_IMODE(metadata.st_mode) != 0o700)
            ):
                _fail("V2_REMOTE_ACK_LEDGER_STATE_ROOT_UNSAFE")
        return descriptor
    except PhysicalWalV2RemoteAckReceiverLedgerError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError:
        if descriptor >= 0:
            os.close(descriptor)
        _fail("V2_REMOTE_ACK_LEDGER_STATE_ROOT_UNSAFE")


def _configuration_sha256(value: PhysicalWalV2RemoteAckConfig, *, maximum_entries: int) -> str:
    try:
        payload = {
            "schema": PHYSICAL_WAL_V2_REMOTE_ACK_RECEIVER_LEDGER_SCHEMA,
            "required_target_recovery_bridge_schema": PHYSICAL_FULL_MATRIX_V2_RECOVERY_EVIDENCE_SCHEMA,
            "clock": "root-owned-wall-clock-with-persisted-anti-rollback-floor-v1",
            "remote_ack_context_sha256": value.expected_context_sha256,
            "source_site": value.expected_source_site,
            "destination_site": value.expected_destination_site,
            "source_public_key_base64": base64.b64encode(value.expected_source_public_key).decode("ascii"),
            "destination_public_key_base64": base64.b64encode(value.expected_destination_public_key).decode("ascii"),
            "maximum_evidence_age_seconds": value.maximum_evidence_age_seconds,
            "maximum_entries": maximum_entries,
        }
        return hashlib.sha256(_canonical(payload, code="V2_REMOTE_ACK_LEDGER_CONFIG_INVALID")).hexdigest()
    except (AttributeError, TypeError, ValueError) as exc:
        raise PhysicalWalV2RemoteAckReceiverLedgerError("V2_REMOTE_ACK_LEDGER_CONFIG_INVALID") from exc


def _config(value: object) -> _ConfigFacts:
    if type(value) is not PhysicalWalV2RemoteAckReceiverLedgerConfig:
        _fail("V2_REMOTE_ACK_LEDGER_CONFIG_REQUIRED")
    if value.enabled is not True:
        _fail("V2_REMOTE_ACK_LEDGER_CONFIG_DISABLED")
    if os.geteuid() != 0:
        _fail("V2_REMOTE_ACK_LEDGER_ROOT_RUNTIME_REQUIRED")
    root = _safe_root_path(value.state_root, code="V2_REMOTE_ACK_LEDGER_STATE_ROOT_UNSAFE")
    if type(value.remote_ack_config) is not PhysicalWalV2RemoteAckConfig or value.remote_ack_config.enabled is not True:
        _fail("V2_REMOTE_ACK_LEDGER_CONFIG_INVALID")
    if (
        type(value.maximum_entries) is not int
        or not 1 <= value.maximum_entries <= MAX_PHYSICAL_WAL_V2_REMOTE_ACK_RECEIVER_LEDGER_ENTRIES
    ):
        _fail("V2_REMOTE_ACK_LEDGER_CONFIG_INVALID")
    return _ConfigFacts(
        state_root=root,
        remote_ack_config=value.remote_ack_config,
        maximum_entries=value.maximum_entries,
        configuration_sha256=_configuration_sha256(
            value.remote_ack_config,
            maximum_entries=value.maximum_entries,
        ),
    )


def _safe_child_metadata(parent_fd: int, name: str, *, directory: bool, code: str) -> os.stat_result:
    try:
        metadata = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except OSError:
        _fail(code)
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
        _fail("V2_REMOTE_ACK_LEDGER_PATH_COMPONENT_INVALID")
    created = False
    try:
        os.mkdir(name, 0o700, dir_fd=parent_fd)
        created = True
    except FileExistsError:
        pass
    except OSError:
        _fail("V2_REMOTE_ACK_LEDGER_DIRECTORY_CREATE_FAILED")
    descriptor = -1
    try:
        if created:
            _fsync_fd(parent_fd, code="V2_REMOTE_ACK_LEDGER_DIRECTORY_FSYNC_FAILED")
        before = _safe_child_metadata(
            parent_fd,
            name,
            directory=True,
            code="V2_REMOTE_ACK_LEDGER_DIRECTORY_UNSAFE",
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
            code="V2_REMOTE_ACK_LEDGER_DIRECTORY_UNSAFE",
        )
    except PhysicalWalV2RemoteAckReceiverLedgerError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError:
        if descriptor >= 0:
            os.close(descriptor)
        _fail("V2_REMOTE_ACK_LEDGER_DIRECTORY_UNSAFE")
    if (
        _metadata_tuple(before) != _metadata_tuple(opened)
        or _metadata_tuple(after) != _metadata_tuple(before)
    ):
        os.close(descriptor)
        _fail("V2_REMOTE_ACK_LEDGER_DIRECTORY_UNSAFE")
    return descriptor


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
            _fsync_fd(descriptor, code="V2_REMOTE_ACK_LEDGER_LOCK_FSYNC_FAILED")
            _fsync_fd(directory_fd, code="V2_REMOTE_ACK_LEDGER_DIRECTORY_FSYNC_FAILED")
        before = _safe_child_metadata(
            directory_fd,
            _LOCK_FILENAME,
            directory=False,
            code="V2_REMOTE_ACK_LEDGER_LOCK_UNSAFE",
        )
        opened = os.fstat(descriptor)
        after = _safe_child_metadata(
            directory_fd,
            _LOCK_FILENAME,
            directory=False,
            code="V2_REMOTE_ACK_LEDGER_LOCK_UNSAFE",
        )
        if _metadata_tuple(before) != _metadata_tuple(opened) or _metadata_tuple(after) != _metadata_tuple(before):
            _fail("V2_REMOTE_ACK_LEDGER_LOCK_UNSAFE")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        return descriptor
    except PhysicalWalV2RemoteAckReceiverLedgerError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError:
        if descriptor >= 0:
            os.close(descriptor)
        _fail("V2_REMOTE_ACK_LEDGER_LOCK_OPEN_FAILED")


@contextmanager
def _locked_ledger(config: _ConfigFacts) -> Iterator[tuple[Path, int]]:
    root_fd = _open_secure_root(config.state_root)
    directory_fd = -1
    lock_fd = -1
    try:
        directory_fd = _ensure_child_directory(root_fd, _LEDGER_DIRECTORY)
        lock_fd = _open_lock(directory_fd)
        yield config.state_root / _LEDGER_DIRECTORY / _LEDGER_FILENAME, directory_fd
    finally:
        try:
            if lock_fd >= 0:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
        finally:
            if lock_fd >= 0:
                os.close(lock_fd)
            if directory_fd >= 0:
                os.close(directory_fd)
            try:
                os.close(root_fd)
            except OSError:
                _fail("V2_REMOTE_ACK_LEDGER_STATE_ROOT_CLOSE_FAILED")


def _open_existing_ledger(directory_fd: int) -> int:
    _require_fd_platform()
    descriptor = -1
    try:
        descriptor = os.open(
            _LEDGER_FILENAME,
            os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
            dir_fd=directory_fd,
        )
    except FileNotFoundError:
        raise
    except OSError:
        _fail("V2_REMOTE_ACK_LEDGER_STATE_OPEN_FAILED")
    try:
        before = _safe_child_metadata(
            directory_fd,
            _LEDGER_FILENAME,
            directory=False,
            code="V2_REMOTE_ACK_LEDGER_STATE_UNSAFE",
        )
        metadata = os.fstat(descriptor)
        after = _safe_child_metadata(
            directory_fd,
            _LEDGER_FILENAME,
            directory=False,
            code="V2_REMOTE_ACK_LEDGER_STATE_UNSAFE",
        )
        if (
            _metadata_tuple(before) != _metadata_tuple(metadata)
            or _metadata_tuple(after) != _metadata_tuple(before)
            or not 1 <= metadata.st_size <= MAX_PHYSICAL_WAL_V2_REMOTE_ACK_RECEIVER_LEDGER_BYTES
        ):
            _fail("V2_REMOTE_ACK_LEDGER_STATE_UNSAFE")
    except Exception:
        os.close(descriptor)
        raise
    return descriptor


def _read_canonical_ledger(directory_fd: int) -> dict[str, Any] | None:
    try:
        descriptor = _open_existing_ledger(directory_fd)
    except FileNotFoundError:
        return None
    try:
        size = os.fstat(descriptor).st_size
        payload = bytearray()
        while len(payload) < size:
            try:
                chunk = os.read(descriptor, size - len(payload))
            except OSError:
                _fail("V2_REMOTE_ACK_LEDGER_STATE_READ_FAILED")
            if not chunk:
                _fail("V2_REMOTE_ACK_LEDGER_STATE_READ_FAILED")
            payload.extend(chunk)
        try:
            if os.read(descriptor, 1):
                _fail("V2_REMOTE_ACK_LEDGER_STATE_READ_FAILED")
        except OSError:
            _fail("V2_REMOTE_ACK_LEDGER_STATE_READ_FAILED")
    finally:
        os.close(descriptor)
    raw = bytes(payload)
    try:
        parsed = json.loads(
            raw.decode("ascii", "strict"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except PhysicalWalV2RemoteAckReceiverLedgerError:
        raise
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise PhysicalWalV2RemoteAckReceiverLedgerError("V2_REMOTE_ACK_LEDGER_STATE_JSON_INVALID") from exc
    if type(parsed) is not dict or _canonical(parsed, code="V2_REMOTE_ACK_LEDGER_STATE_CANONICAL_INVALID") != raw:
        _fail("V2_REMOTE_ACK_LEDGER_STATE_CANONICAL_INVALID")
    return dict(parsed)


def _decode_bytes(value: object, *, code: str) -> bytes:
    if type(value) is not str:
        _fail(code)
    try:
        result = base64.b64decode(value.encode("ascii", "strict"), validate=True)
    except (UnicodeEncodeError, binascii.Error):
        _fail(code)
    if not 1 <= len(result) <= MAX_PHYSICAL_WAL_V2_REMOTE_ACK_BYTES:
        _fail(code)
    return result


def _entry_unsigned_mapping(value: _Entry) -> dict[str, object]:
    return {
        "ledger_entry_id": value.ledger_entry_id,
        "source_request_sha256": value.source_request_sha256,
        "source_request_base64": base64.b64encode(value.source_request).decode("ascii"),
        "context_sha256": value.context_sha256,
        "request_id": value.request_id,
        "request_nonce": value.request_nonce,
        "receiver_recovery_evidence_sha256": value.receiver_recovery_evidence_sha256,
        "receiver_replay_lsn": value.receiver_replay_lsn,
        "receiver_observed_at": _render_timestamp(value.receiver_observed_at),
        "target_recovery_evidence_sha256": value.target_recovery_evidence_sha256,
        "readback_attestation_sha256": value.readback_attestation_sha256,
        "readback_attestation_id": value.readback_attestation_id,
        "readback_attestation_nonce": value.readback_attestation_nonce,
        "stage_receipt_sha256": value.stage_receipt_sha256,
        "witness_transition_id": value.witness_transition_id,
        "target_recovery_observed_at": _render_timestamp(value.target_recovery_observed_at),
        "receipt_id": value.receipt_id,
        "receipt_nonce": value.receipt_nonce,
        "destination_receipt_sha256": value.destination_receipt_sha256,
        "destination_receipt_base64": base64.b64encode(value.destination_receipt).decode("ascii"),
        "acknowledged_at": _render_timestamp(value.acknowledged_at),
        "committed_at": _render_timestamp(value.committed_at),
    }


def _entry_digest(value: _Entry) -> str:
    return hashlib.sha256(_canonical(_entry_unsigned_mapping(value), code="V2_REMOTE_ACK_LEDGER_ENTRY_INVALID")).hexdigest()


def _entry_mapping(value: _Entry) -> dict[str, object]:
    return {
        **_entry_unsigned_mapping(value),
        "durable_ledger_entry_sha256": value.durable_ledger_entry_sha256,
    }


def _entry_from_mapping(value: object, *, config: _ConfigFacts) -> _Entry:
    item = _exact_mapping(value, fields=_ENTRY_FIELDS, code="V2_REMOTE_ACK_LEDGER_ENTRY_FIELDS_INVALID")
    entry_id = _identifier(item["ledger_entry_id"], code="V2_REMOTE_ACK_LEDGER_ENTRY_INVALID")
    source_request = _decode_bytes(item["source_request_base64"], code="V2_REMOTE_ACK_LEDGER_ENTRY_INVALID")
    destination_receipt = _decode_bytes(item["destination_receipt_base64"], code="V2_REMOTE_ACK_LEDGER_ENTRY_INVALID")
    source_sha = _sha256(item["source_request_sha256"], code="V2_REMOTE_ACK_LEDGER_ENTRY_INVALID")
    receipt_sha = _sha256(item["destination_receipt_sha256"], code="V2_REMOTE_ACK_LEDGER_ENTRY_INVALID")
    context_sha = _sha256(item["context_sha256"], code="V2_REMOTE_ACK_LEDGER_ENTRY_INVALID")
    request_id = _identifier(item["request_id"], code="V2_REMOTE_ACK_LEDGER_ENTRY_INVALID")
    request_nonce = _nonce(item["request_nonce"], code="V2_REMOTE_ACK_LEDGER_ENTRY_INVALID")
    recovery_sha = _sha256(item["receiver_recovery_evidence_sha256"], code="V2_REMOTE_ACK_LEDGER_ENTRY_INVALID")
    replay_lsn, _replay_value = _lsn(item["receiver_replay_lsn"], code="V2_REMOTE_ACK_LEDGER_ENTRY_INVALID")
    observed_at = _timestamp(item["receiver_observed_at"], code="V2_REMOTE_ACK_LEDGER_ENTRY_INVALID")
    target_evidence_sha = _sha256(item["target_recovery_evidence_sha256"], code="V2_REMOTE_ACK_LEDGER_ENTRY_INVALID")
    readback_attestation_sha = _sha256(item["readback_attestation_sha256"], code="V2_REMOTE_ACK_LEDGER_ENTRY_INVALID")
    readback_attestation_id = _identifier(item["readback_attestation_id"], code="V2_REMOTE_ACK_LEDGER_ENTRY_INVALID")
    readback_attestation_nonce = _nonce(item["readback_attestation_nonce"], code="V2_REMOTE_ACK_LEDGER_ENTRY_INVALID")
    stage_receipt_sha = _sha256(item["stage_receipt_sha256"], code="V2_REMOTE_ACK_LEDGER_ENTRY_INVALID")
    witness_transition_id = _identifier(item["witness_transition_id"], code="V2_REMOTE_ACK_LEDGER_ENTRY_INVALID")
    target_observed_at = _timestamp(item["target_recovery_observed_at"], code="V2_REMOTE_ACK_LEDGER_ENTRY_INVALID")
    receipt_id = _identifier(item["receipt_id"], code="V2_REMOTE_ACK_LEDGER_ENTRY_INVALID")
    receipt_nonce = _nonce(item["receipt_nonce"], code="V2_REMOTE_ACK_LEDGER_ENTRY_INVALID")
    acknowledged_at = _timestamp(item["acknowledged_at"], code="V2_REMOTE_ACK_LEDGER_ENTRY_INVALID")
    committed_at = _timestamp(item["committed_at"], code="V2_REMOTE_ACK_LEDGER_ENTRY_INVALID")
    stored_digest = _sha256(item["durable_ledger_entry_sha256"], code="V2_REMOTE_ACK_LEDGER_ENTRY_INVALID")
    if len({request_id, request_nonce, receipt_id, receipt_nonce, entry_id}) != 5:
        _fail("V2_REMOTE_ACK_LEDGER_ENTRY_IDENTITY_REUSED")
    if hashlib.sha256(source_request).hexdigest() != source_sha or hashlib.sha256(destination_receipt).hexdigest() != receipt_sha:
        _fail("V2_REMOTE_ACK_LEDGER_ENTRY_HASH_MISMATCH")
    try:
        evidence = verify_physical_wal_v2_remote_ack_evidence(
            source_request=source_request,
            destination_receipt=destination_receipt,
            config=config.remote_ack_config,
            now=acknowledged_at,
        )
        request = verify_physical_wal_v2_remote_ack_request(
            source_request=source_request,
            config=config.remote_ack_config,
            now=acknowledged_at,
        )
    except PhysicalWalV2RemoteAckError as exc:
        raise PhysicalWalV2RemoteAckReceiverLedgerError("V2_REMOTE_ACK_LEDGER_ENTRY_SIGNATURE_OR_ROUTE_INVALID") from exc
    if (
        evidence.context_sha256 != context_sha
        or evidence.request_id != request_id
        or evidence.request_nonce != request_nonce
        or evidence.receipt_id != receipt_id
        or evidence.receipt_nonce != receipt_nonce
        or evidence.receiver_recovery_evidence_sha256 != recovery_sha
        or evidence.receiver_replay_lsn != replay_lsn
        or observed_at != target_observed_at
        or observed_at < request.issued_at
        or observed_at > acknowledged_at
        or acknowledged_at > committed_at
    ):
        _fail("V2_REMOTE_ACK_LEDGER_ENTRY_BINDING_MISMATCH")
    result = _Entry(
        ledger_entry_id=entry_id,
        source_request_sha256=source_sha,
        source_request=source_request,
        context_sha256=context_sha,
        request_id=request_id,
        request_nonce=request_nonce,
        receiver_recovery_evidence_sha256=recovery_sha,
        receiver_replay_lsn=replay_lsn,
        receiver_observed_at=observed_at,
        target_recovery_evidence_sha256=target_evidence_sha,
        readback_attestation_sha256=readback_attestation_sha,
        readback_attestation_id=readback_attestation_id,
        readback_attestation_nonce=readback_attestation_nonce,
        stage_receipt_sha256=stage_receipt_sha,
        witness_transition_id=witness_transition_id,
        target_recovery_observed_at=target_observed_at,
        receipt_id=receipt_id,
        receipt_nonce=receipt_nonce,
        destination_receipt_sha256=receipt_sha,
        destination_receipt=destination_receipt,
        acknowledged_at=acknowledged_at,
        committed_at=committed_at,
        durable_ledger_entry_sha256=stored_digest,
    )
    if _entry_digest(result) != stored_digest:
        _fail("V2_REMOTE_ACK_LEDGER_ENTRY_DIGEST_MISMATCH")
    return result


def _load_entries(
    directory_fd: int,
    *,
    config: _ConfigFacts,
    trusted_now: datetime,
) -> tuple[tuple[_Entry, ...], datetime | None]:
    raw = _read_canonical_ledger(directory_fd)
    if raw is None:
        return (), None
    ledger = _exact_mapping(raw, fields=_LEDGER_FIELDS, code="V2_REMOTE_ACK_LEDGER_STATE_FIELDS_INVALID")
    if (
        ledger["schema"] != PHYSICAL_WAL_V2_REMOTE_ACK_RECEIVER_LEDGER_SCHEMA
        or ledger["version"] != PHYSICAL_WAL_V2_REMOTE_ACK_RECEIVER_LEDGER_VERSION
        or _sha256(ledger["configuration_sha256"], code="V2_REMOTE_ACK_LEDGER_CONFIGURATION_INVALID")
        != config.configuration_sha256
        or type(ledger["entries"]) is not list
        or len(ledger["entries"]) > config.maximum_entries
    ):
        _fail("V2_REMOTE_ACK_LEDGER_CONFIGURATION_CONFLICT")
    clock_floor = _timestamp(
        ledger["clock_floor"],
        code="V2_REMOTE_ACK_LEDGER_CLOCK_FLOOR_INVALID",
    )
    if trusted_now < clock_floor:
        _fail("V2_REMOTE_ACK_LEDGER_CLOCK_ROLLBACK_DETECTED")
    entries = tuple(_entry_from_mapping(item, config=config) for item in ledger["entries"])
    if tuple(sorted(entries, key=lambda item: item.request_id)) != entries:
        _fail("V2_REMOTE_ACK_LEDGER_ENTRY_ORDER_INVALID")
    all_identities = tuple(
        identity
        for item in entries
        for identity in (
            item.request_id,
            item.request_nonce,
            item.receipt_id,
            item.receipt_nonce,
            item.ledger_entry_id,
        )
    )
    if len(set(all_identities)) != len(all_identities):
        _fail("V2_REMOTE_ACK_LEDGER_REPLAY_INDEX_CONFLICT")
    if entries and clock_floor < max(item.committed_at for item in entries):
        _fail("V2_REMOTE_ACK_LEDGER_CLOCK_FLOOR_INVALID")
    # A caller that retries after a write whose post-rename directory fsync
    # failed must not receive an idempotent success merely because the name is
    # visible in cache.  A successful fsync here completes that conservative
    # durability confirmation before any existing entry is returned.
    _fsync_fd(directory_fd, code="V2_REMOTE_ACK_LEDGER_DIRECTORY_FSYNC_FAILED")
    return entries, clock_floor


def _ledger_mapping(
    *,
    config: _ConfigFacts,
    entries: Sequence[_Entry],
    clock_floor: datetime,
) -> dict[str, object]:
    return {
        "schema": PHYSICAL_WAL_V2_REMOTE_ACK_RECEIVER_LEDGER_SCHEMA,
        "version": PHYSICAL_WAL_V2_REMOTE_ACK_RECEIVER_LEDGER_VERSION,
        "configuration_sha256": config.configuration_sha256,
        "clock_floor": _render_timestamp(clock_floor),
        "entries": [_entry_mapping(item) for item in sorted(entries, key=lambda item: item.request_id)],
    }


def _write_atomic_ledger(directory_fd: int, *, value: Mapping[str, object]) -> None:
    _require_fd_platform()
    payload = _canonical(value, code="V2_REMOTE_ACK_LEDGER_STATE_CANONICAL_INVALID")
    if not 1 <= len(payload) <= MAX_PHYSICAL_WAL_V2_REMOTE_ACK_RECEIVER_LEDGER_BYTES:
        _fail("V2_REMOTE_ACK_LEDGER_STATE_SIZE_INVALID")
    temporary = "." + _LEDGER_FILENAME + "." + secrets.token_hex(16) + ".tmp"
    descriptor = -1
    replaced = False
    try:
        try:
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
                0o600,
                dir_fd=directory_fd,
            )
        except OSError:
            _fail("V2_REMOTE_ACK_LEDGER_TEMPORARY_OPEN_FAILED")
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_uid != 0
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            _fail("V2_REMOTE_ACK_LEDGER_TEMPORARY_UNSAFE")
        view = memoryview(payload)
        while view:
            try:
                written = os.write(descriptor, view)
            except OSError:
                _fail("V2_REMOTE_ACK_LEDGER_TEMPORARY_WRITE_FAILED")
            if written <= 0:
                _fail("V2_REMOTE_ACK_LEDGER_TEMPORARY_WRITE_FAILED")
            view = view[written:]
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.replace(
            temporary,
            _LEDGER_FILENAME,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
        )
        replaced = True
        _fsync_fd(directory_fd, code="V2_REMOTE_ACK_LEDGER_DIRECTORY_FSYNC_FAILED")
        metadata = _safe_child_metadata(
            directory_fd,
            _LEDGER_FILENAME,
            directory=False,
            code="V2_REMOTE_ACK_LEDGER_STATE_UNSAFE",
        )
        if metadata.st_size != len(payload):
            _fail("V2_REMOTE_ACK_LEDGER_STATE_UNSAFE")
    except PhysicalWalV2RemoteAckReceiverLedgerError:
        raise
    except OSError as exc:
        raise PhysicalWalV2RemoteAckReceiverLedgerError("V2_REMOTE_ACK_LEDGER_STATE_WRITE_FAILED") from exc
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if not replaced:
            try:
                os.unlink(temporary, dir_fd=directory_fd)
            except FileNotFoundError:
                pass
            except OSError:
                pass


def _new_identity(prefix: str, *, pattern: re.Pattern[str]) -> str:
    while True:
        value = prefix + secrets.token_urlsafe(24)
        if pattern.fullmatch(value) is not None:
            return value


def _destination_signer(value: object, *, config: _ConfigFacts) -> object:
    if not isinstance(value, Ed25519PrivateKey):
        _fail("V2_REMOTE_ACK_LEDGER_DESTINATION_SIGNER_REQUIRED")
    try:
        public_key = value.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
    except ValueError:
        _fail("V2_REMOTE_ACK_LEDGER_DESTINATION_SIGNER_INVALID")
    if public_key != config.remote_ack_config.expected_destination_public_key:
        _fail("V2_REMOTE_ACK_LEDGER_DESTINATION_SIGNER_MISMATCH")
    return value


def _request_and_recovery(
    *,
    source_request: object,
    receiver_recovery_evidence: object,
    target_recovery_evidence: object,
    remote_ack_evidence: object | None,
    config: _ConfigFacts,
    now: datetime,
) -> tuple[
    VerifiedPhysicalWalV2RemoteAckRequest,
    VerifiedPhysicalWalV2RemoteAckReceiverRecoveryEvidence,
    VerifiedPhysicalFullMatrixV2RecoveryEvidence,
    VerifiedPhysicalWalV2RemoteAckEvidence | None,
]:
    """Require all three independent facts before durable receipt issuance.

    The receiver-local projection is deliberately only a convenient typed
    projection.  It is admitted here solely when it exactly names the signed,
    revalidated target-readback evidence carried by the Full-Matrix V2
    bridge.  A local dataclass therefore cannot establish PostgreSQL replay
    truth on its own.
    """

    try:
        request = require_verified_physical_wal_v2_remote_ack_request(
            source_request,
            config=config.remote_ack_config,
            now=now,
        )
        recovery = require_verified_physical_wal_v2_remote_ack_receiver_recovery_evidence(
            receiver_recovery_evidence,
            source_request=request,
            config=config.remote_ack_config,
            now=now,
        )
        target = require_verified_physical_full_matrix_v2_recovery_evidence(
            target_recovery_evidence,
            now=now,
        )
    except (PhysicalWalV2RemoteAckError, PhysicalFullMatrixV2RecoveryEvidenceError) as exc:
        raise PhysicalWalV2RemoteAckReceiverLedgerError("V2_REMOTE_ACK_LEDGER_INPUT_INVALID") from exc
    if recovery.evidence.observed_at < request.issued_at or recovery.evidence.observed_at > now:
        _fail("V2_REMOTE_ACK_LEDGER_RECOVERY_TIME_MISMATCH")
    _cross_pin_signed_target_recovery(
        request=request,
        recovery=recovery,
        target=target,
    )
    pair = _require_optional_remote_ack_evidence(
        remote_ack_evidence,
        request=request,
        recovery=recovery,
        config=config,
        now=now,
    )
    return request, recovery, target, pair


def _request_context_mapping(request: VerifiedPhysicalWalV2RemoteAckRequest) -> dict[str, Any]:
    """Extract the signed context after the pure V2 verifier has checked it."""

    try:
        outer = json.loads(request.canonical_request.decode("ascii", "strict"))
        if type(outer) is not dict or type(outer.get("context")) is not dict:
            _fail("V2_REMOTE_ACK_LEDGER_REQUEST_CONTEXT_INVALID")
        return dict(outer["context"])
    except PhysicalWalV2RemoteAckReceiverLedgerError:
        raise
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
        _fail("V2_REMOTE_ACK_LEDGER_REQUEST_CONTEXT_INVALID")


def _context_value(context: Mapping[str, Any], key: str) -> object:
    if key not in context:
        _fail("V2_REMOTE_ACK_LEDGER_REQUEST_CONTEXT_INVALID")
    return context[key]


def _cross_pin_signed_target_recovery(
    *,
    request: VerifiedPhysicalWalV2RemoteAckRequest,
    recovery: VerifiedPhysicalWalV2RemoteAckReceiverRecoveryEvidence,
    target: VerifiedPhysicalFullMatrixV2RecoveryEvidence,
) -> None:
    """Join signed request, local projection, and signed target recovery.

    This intentionally repeats all material route/identity/coverage pins at
    the durable boundary.  Valid pieces from different campaigns, terms,
    manifests, or recovery stages cannot be mixed into one ledger entry.
    """

    context = _request_context_mapping(request)
    binding = target.transfer_binding
    term = _context_value(context, "writer_term")
    if type(term) is not dict:
        _fail("V2_REMOTE_ACK_LEDGER_REQUEST_CONTEXT_INVALID")
    expected: dict[str, object] = {
        "source_site": binding.source_site,
        "destination_site": binding.destination_site,
        "campaign_id": binding.campaign_id,
        "release_sha": binding.release_sha,
        "object_storage_namespace": binding.object_storage_namespace,
        "route_commitment_sha256": binding.route_commitment_sha256,
        "four_role_binding_sha256": binding.four_role_binding_sha256,
        "destination_age_recipient": binding.destination_age_recipient,
        "transport_plane": binding.transport_plane,
        "direct_webapp_transport": binding.direct_webapp_transport,
        "stream_generation_id": target.stream_generation_id,
        "canonical_manifest_sha256": target.manifest_sha256,
        "manifest_id": target.manifest_id,
        "handoff_receipt_id": target.handoff_receipt_id,
        "handoff_receipt_nonce": target.handoff_receipt_nonce,
        "lineage_sha256": target.lineage_sha256,
        "baseline_generation_id": target.baseline_generation_id,
        "database_system_identifier": target.database_system_identifier,
        "timeline_id": target.timeline_id,
        "wal_segment_size_bytes": target.wal_segment_size_bytes,
        "baseline_wal_lsn": target.baseline_wal_lsn,
        "wal_chain_start_lsn": target.wal_chain_start_lsn,
        "base_backup_end_lsn": target.base_backup_end_lsn,
        "target_lsn": target.target_replay_lsn,
        "blob_frontier_scope_sha256": target.blob_frontier_scope_sha256,
        "blob_owner_coverage_sha256": target.blob_owner_coverage_sha256,
        "blob_coverage_id": target.blob_coverage_id,
        "blob_coverage_nonce": target.blob_coverage_nonce,
        "wal_continuity_scope_sha256": target.wal_continuity_scope_sha256,
        "wal_continuity_receipt_id": target.wal_continuity_receipt_id,
        "wal_continuity_receipt_nonce": target.wal_continuity_receipt_nonce,
        "wal_continuity_selector_set_sha256": target.wal_continuity_selector_set_sha256,
        "object_version_set_sha256": target.object_version_set_sha256,
        "coverage_scope_sha256": target.coverage_scope_sha256,
    }
    if any(_context_value(context, name) != expected_value for name, expected_value in expected.items()):
        _fail("V2_REMOTE_ACK_LEDGER_TARGET_RECOVERY_CROSS_PIN_MISMATCH")
    if (
        term.get("writer_holder_site") != binding.writer_term.writer_holder_site
        or term.get("writer_epoch") != binding.writer_term.writer_epoch
        or term.get("writer_lease_id") != binding.writer_term.writer_lease_id
        or term.get("witnessed_term_proof_sha256")
        != binding.writer_term.witnessed_term_proof_sha256
        or _timestamp(
            _context_value(context, "handoff_expires_at"),
            code="V2_REMOTE_ACK_LEDGER_REQUEST_CONTEXT_INVALID",
        )
        != target.handoff_expires_at
        or request.source_site != binding.source_site
        or request.destination_site != binding.destination_site
        or request.target_lsn != target.target_replay_lsn
        or request.object_version_set_sha256 != target.object_version_set_sha256
        or recovery.evidence.source_request_sha256
        != hashlib.sha256(request.canonical_request).hexdigest()
        or recovery.evidence.context_sha256 != request.context_sha256
        or recovery.evidence.receiver_recovery_evidence_sha256
        != target.readback_evidence_sha256
        or recovery.evidence.receiver_site != binding.destination_site
        or recovery.evidence.source_site != binding.source_site
        or recovery.evidence.destination_site != binding.destination_site
        or recovery.evidence.object_version_set_sha256 != target.object_version_set_sha256
        or recovery.evidence.target_lsn != target.target_replay_lsn
        or recovery.evidence.replay_lsn != target.target_replay_lsn
        or recovery.evidence.observed_at != target.observed_at
        or recovery.evidence.in_recovery is not True
        or recovery.evidence.role != "standby"
    ):
        _fail("V2_REMOTE_ACK_LEDGER_TARGET_RECOVERY_CROSS_PIN_MISMATCH")


def _require_optional_remote_ack_evidence(
    value: object | None,
    *,
    request: VerifiedPhysicalWalV2RemoteAckRequest,
    recovery: VerifiedPhysicalWalV2RemoteAckReceiverRecoveryEvidence,
    config: _ConfigFacts,
    now: datetime,
) -> VerifiedPhysicalWalV2RemoteAckEvidence | None:
    """Accept a caller's prior wire pair only as an extra consistency check.

    It is never used as the durable proof; the ledger always stores the
    receiver receipt it generated and fsync'd itself.  Keeping this optional
    preserves the V2 boundary's explicit pair input for callers that already
    observed the non-durable wire acknowledgement.
    """

    if value is None:
        return None
    try:
        from core.physical_wal_v2_remote_ack import (
            require_verified_physical_wal_v2_remote_ack_evidence,
        )

        evidence = require_verified_physical_wal_v2_remote_ack_evidence(
            value,
            config=config.remote_ack_config,
            now=now,
        )
    except PhysicalWalV2RemoteAckError as exc:
        raise PhysicalWalV2RemoteAckReceiverLedgerError(
            "V2_REMOTE_ACK_LEDGER_NON_DURABLE_PAIR_INVALID"
        ) from exc
    if (
        evidence.canonical_request != request.canonical_request
        or evidence.context_sha256 != request.context_sha256
        or evidence.receiver_recovery_evidence_sha256
        != recovery.evidence.receiver_recovery_evidence_sha256
        or evidence.receiver_replay_lsn != recovery.evidence.replay_lsn
        or evidence.acknowledged_at < recovery.evidence.observed_at
        or evidence.acknowledged_at > now
    ):
        _fail("V2_REMOTE_ACK_LEDGER_NON_DURABLE_PAIR_MISMATCH")
    return evidence


def _result_from_entry(
    entry: _Entry,
    *,
    config: _ConfigFacts,
    ledger_path: Path,
    idempotent: bool,
    now: datetime,
) -> PhysicalWalV2RemoteAckReceiverLedgerResult:
    try:
        evidence = verify_physical_wal_v2_remote_ack_evidence(
            source_request=entry.source_request,
            destination_receipt=entry.destination_receipt,
            config=config.remote_ack_config,
            now=now,
        )
    except PhysicalWalV2RemoteAckError as exc:
        raise PhysicalWalV2RemoteAckReceiverLedgerError("V2_REMOTE_ACK_LEDGER_ENTRY_STALE_OR_INVALID") from exc
    if (
        evidence.context_sha256 != entry.context_sha256
        or evidence.request_id != entry.request_id
        or evidence.request_nonce != entry.request_nonce
        or evidence.receipt_id != entry.receipt_id
        or evidence.receipt_nonce != entry.receipt_nonce
        or evidence.receiver_recovery_evidence_sha256 != entry.receiver_recovery_evidence_sha256
        or evidence.receiver_replay_lsn != entry.receiver_replay_lsn
    ):
        _fail("V2_REMOTE_ACK_LEDGER_ENTRY_BINDING_MISMATCH")
    receipt = VerifiedPhysicalWalV2RemoteAckReceiverLedgerReceipt(
        schema=PHYSICAL_WAL_V2_REMOTE_ACK_RECEIVER_LEDGER_SCHEMA,
        canonical_source_request=entry.source_request,
        canonical_destination_receipt=entry.destination_receipt,
        source_request_sha256=entry.source_request_sha256,
        destination_receipt_sha256=entry.destination_receipt_sha256,
        context_sha256=entry.context_sha256,
        request_id=entry.request_id,
        request_nonce=entry.request_nonce,
        receipt_id=entry.receipt_id,
        receipt_nonce=entry.receipt_nonce,
        receiver_recovery_evidence_sha256=entry.receiver_recovery_evidence_sha256,
        receiver_replay_lsn=entry.receiver_replay_lsn,
        target_recovery_evidence_sha256=entry.target_recovery_evidence_sha256,
        readback_attestation_sha256=entry.readback_attestation_sha256,
        readback_attestation_id=entry.readback_attestation_id,
        readback_attestation_nonce=entry.readback_attestation_nonce,
        stage_receipt_sha256=entry.stage_receipt_sha256,
        witness_transition_id=entry.witness_transition_id,
        target_recovery_observed_at=entry.target_recovery_observed_at,
        ledger_entry_id=entry.ledger_entry_id,
        durable_ledger_entry_sha256=entry.durable_ledger_entry_sha256,
        committed_at=entry.committed_at,
        ledger_path=ledger_path,
    )
    object.__setattr__(receipt, "_capability", _CAPABILITY)
    return PhysicalWalV2RemoteAckReceiverLedgerResult(
        receipt=receipt,
        remote_ack_evidence=evidence,
        idempotent=idempotent,
    )


def issue_physical_wal_v2_remote_ack_receiver_receipt(
    *,
    config: PhysicalWalV2RemoteAckReceiverLedgerConfig,
    source_request: VerifiedPhysicalWalV2RemoteAckRequest,
    receiver_recovery_evidence: VerifiedPhysicalWalV2RemoteAckReceiverRecoveryEvidence,
    target_recovery_evidence: VerifiedPhysicalFullMatrixV2RecoveryEvidence,
    destination_signer: object | None,
    remote_ack_evidence: VerifiedPhysicalWalV2RemoteAckEvidence | None = None,
    now: datetime | None = None,
) -> PhysicalWalV2RemoteAckReceiverLedgerResult:
    """Atomically persist one exact V2 replay receipt or fail closed.

    A retry may return the existing canonical bytes only when its request and
    receiver-recovery evidence *and signed target-recovery projection* are
    exact matches.  Any request-ID/nonce reuse with a changed request or
    recovery record is a replay conflict.  ``now`` is retained only for API
    compatibility and is intentionally not an admission clock.
    """

    normalized = _config(config)
    del now
    with _locked_ledger(normalized) as (ledger_path, directory_fd):
        observed_now = _utc(_trusted_now(), code="V2_REMOTE_ACK_LEDGER_CLOCK_INVALID")
        # Check the persisted floor before validating caller-provided proof
        # timestamps, so an attempted local clock rollback has one stable
        # fail-closed outcome rather than being masked by a stale-input error.
        entries, clock_floor = _load_entries(
            directory_fd,
            config=normalized,
            trusted_now=observed_now,
        )
        request, recovery, target, supplied_pair = _request_and_recovery(
            source_request=source_request,
            receiver_recovery_evidence=receiver_recovery_evidence,
            target_recovery_evidence=target_recovery_evidence,
            remote_ack_evidence=remote_ack_evidence,
            config=normalized,
            now=observed_now,
        )
        request_sha = hashlib.sha256(request.canonical_request).hexdigest()
        record: PhysicalWalV2RemoteAckReceiverRecoveryEvidence = recovery.evidence
        same_id = next((item for item in entries if item.request_id == request.request_id), None)
        same_nonce = next((item for item in entries if item.request_nonce == request.request_nonce), None)
        if same_id is not None or same_nonce is not None:
            existing = same_id or same_nonce
            assert existing is not None
            if (
                existing.request_id != request.request_id
                or existing.request_nonce != request.request_nonce
                or existing.source_request_sha256 != request_sha
                or existing.source_request != request.canonical_request
                or existing.context_sha256 != request.context_sha256
                or existing.receiver_recovery_evidence_sha256
                != record.receiver_recovery_evidence_sha256
                or existing.receiver_replay_lsn != record.replay_lsn
                or existing.receiver_observed_at != record.observed_at
                or existing.target_recovery_evidence_sha256 != target.evidence_sha256
                or existing.readback_attestation_sha256 != target.readback_attestation_sha256
                or existing.readback_attestation_id != target.readback_attestation_id
                or existing.readback_attestation_nonce != target.readback_attestation_nonce
                or existing.stage_receipt_sha256 != target.stage_receipt_sha256
                or existing.witness_transition_id != target.witness_transition_id
                or existing.target_recovery_observed_at != target.observed_at
                or (
                    supplied_pair is not None
                    and (
                        existing.destination_receipt != supplied_pair.canonical_receipt
                        or existing.destination_receipt_sha256
                        != hashlib.sha256(supplied_pair.canonical_receipt).hexdigest()
                    )
                )
            ):
                _fail("V2_REMOTE_ACK_LEDGER_REPLAY_CONFLICT")
            return _result_from_entry(
                existing,
                config=normalized,
                ledger_path=ledger_path,
                idempotent=True,
                now=observed_now,
            )
        consumed_identities = {
            identity
            for item in entries
            for identity in (
                item.request_id,
                item.request_nonce,
                item.receipt_id,
                item.receipt_nonce,
                item.ledger_entry_id,
            )
        }
        if request.request_id in consumed_identities or request.request_nonce in consumed_identities:
            _fail("V2_REMOTE_ACK_LEDGER_REPLAY_CONFLICT")
        if len(entries) >= normalized.maximum_entries:
            _fail("V2_REMOTE_ACK_LEDGER_ENTRY_LIMIT_EXCEEDED")
        used = {
            item.request_id for item in entries
        } | {
            item.request_nonce for item in entries
        } | {
            item.receipt_id for item in entries
        } | {
            item.receipt_nonce for item in entries
        } | {
            item.ledger_entry_id for item in entries
        } | {request.request_id, request.request_nonce}
        entry_id = _new_identity("v2-remote-ack-ledger-", pattern=_ID_RE)
        while entry_id in used:
            entry_id = _new_identity("v2-remote-ack-ledger-", pattern=_ID_RE)
        if supplied_pair is None:
            receipt_id = _new_identity("v2-remote-ack-receipt-", pattern=_ID_RE)
            receipt_nonce = _new_identity("", pattern=_NONCE_RE)
            while (
                receipt_id in used
                or receipt_nonce in used
                or receipt_id == entry_id
                or receipt_nonce == entry_id
                or receipt_id == receipt_nonce
            ):
                receipt_id = _new_identity("v2-remote-ack-receipt-", pattern=_ID_RE)
                receipt_nonce = _new_identity("", pattern=_NONCE_RE)
        if supplied_pair is not None:
            evidence = supplied_pair
        else:
            signer = _destination_signer(destination_signer, config=normalized)
            try:
                raw_receipt = build_physical_wal_v2_remote_ack_receipt(
                    config=normalized.remote_ack_config,
                    source_request=request,
                    receiver_recovery_evidence=recovery,
                    receipt_id=receipt_id,
                    receipt_nonce=receipt_nonce,
                    destination_signer=signer,
                    now=observed_now,
                )
                evidence = verify_physical_wal_v2_remote_ack_evidence(
                    source_request=request.canonical_request,
                    destination_receipt=raw_receipt,
                    config=normalized.remote_ack_config,
                    now=observed_now,
                )
            except PhysicalWalV2RemoteAckError as exc:
                raise PhysicalWalV2RemoteAckReceiverLedgerError("V2_REMOTE_ACK_LEDGER_RECEIPT_INVALID") from exc
        if supplied_pair is not None:
            while entry_id in {evidence.receipt_id, evidence.receipt_nonce}:
                entry_id = _new_identity("v2-remote-ack-ledger-", pattern=_ID_RE)
        if (
            evidence.receipt_id in used
            or evidence.receipt_nonce in used
            or evidence.receipt_id == entry_id
            or evidence.receipt_nonce == entry_id
        ):
            _fail("V2_REMOTE_ACK_LEDGER_REPLAY_CONFLICT")
        entry = _Entry(
            ledger_entry_id=entry_id,
            source_request_sha256=request_sha,
            source_request=request.canonical_request,
            context_sha256=request.context_sha256,
            request_id=request.request_id,
            request_nonce=request.request_nonce,
            receiver_recovery_evidence_sha256=record.receiver_recovery_evidence_sha256,
            receiver_replay_lsn=record.replay_lsn,
            receiver_observed_at=record.observed_at,
            target_recovery_evidence_sha256=target.evidence_sha256,
            readback_attestation_sha256=target.readback_attestation_sha256,
            readback_attestation_id=target.readback_attestation_id,
            readback_attestation_nonce=target.readback_attestation_nonce,
            stage_receipt_sha256=target.stage_receipt_sha256,
            witness_transition_id=target.witness_transition_id,
            target_recovery_observed_at=target.observed_at,
            receipt_id=evidence.receipt_id,
            receipt_nonce=evidence.receipt_nonce,
            destination_receipt_sha256=hashlib.sha256(evidence.canonical_receipt).hexdigest(),
            destination_receipt=evidence.canonical_receipt,
            acknowledged_at=evidence.acknowledged_at,
            committed_at=observed_now,
            durable_ledger_entry_sha256="",
        )
        entry = replace(entry, durable_ledger_entry_sha256=_entry_digest(entry))
        _write_atomic_ledger(
            directory_fd,
            value=_ledger_mapping(
                config=normalized,
                entries=(*entries, entry),
                clock_floor=max(
                    observed_now,
                    entry.committed_at,
                    clock_floor or observed_now,
                ),
            ),
        )
        return _result_from_entry(
            entry,
            config=normalized,
            ledger_path=ledger_path,
            idempotent=False,
            now=observed_now,
        )


def require_verified_physical_wal_v2_remote_ack_receiver_ledger_receipt(
    value: object,
    *,
    config: PhysicalWalV2RemoteAckReceiverLedgerConfig,
    source_request: VerifiedPhysicalWalV2RemoteAckRequest,
    receiver_recovery_evidence: VerifiedPhysicalWalV2RemoteAckReceiverRecoveryEvidence,
    target_recovery_evidence: VerifiedPhysicalFullMatrixV2RecoveryEvidence,
    remote_ack_evidence: VerifiedPhysicalWalV2RemoteAckEvidence | None = None,
    now: datetime | None = None,
) -> VerifiedPhysicalWalV2RemoteAckReceiverLedgerReceipt:
    """Re-read and revalidate the exact durable receipt under the ledger lock."""

    if (
        type(value) is not VerifiedPhysicalWalV2RemoteAckReceiverLedgerReceipt
        or value._capability is not _CAPABILITY
    ):
        _fail("V2_REMOTE_ACK_LEDGER_RECEIPT_CAPABILITY_REQUIRED")
    normalized = _config(config)
    del now
    with _locked_ledger(normalized) as (ledger_path, directory_fd):
        observed_now = _utc(_trusted_now(), code="V2_REMOTE_ACK_LEDGER_CLOCK_INVALID")
        entries, _clock_floor = _load_entries(
            directory_fd,
            config=normalized,
            trusted_now=observed_now,
        )
        request, recovery, target, supplied_pair = _request_and_recovery(
            source_request=source_request,
            receiver_recovery_evidence=receiver_recovery_evidence,
            target_recovery_evidence=target_recovery_evidence,
            remote_ack_evidence=remote_ack_evidence,
            config=normalized,
            now=observed_now,
        )
        entry = next((item for item in entries if item.ledger_entry_id == value.ledger_entry_id), None)
        if entry is None:
            _fail("V2_REMOTE_ACK_LEDGER_RECEIPT_ENTRY_MISSING")
        if (
            supplied_pair is not None
            and (
                entry.destination_receipt != supplied_pair.canonical_receipt
                or entry.destination_receipt_sha256
                != hashlib.sha256(supplied_pair.canonical_receipt).hexdigest()
            )
        ):
            _fail("V2_REMOTE_ACK_LEDGER_NON_DURABLE_PAIR_MISMATCH")
        result = _result_from_entry(
            entry,
            config=normalized,
            ledger_path=ledger_path,
            idempotent=False,
            now=observed_now,
        ).receipt
    record = recovery.evidence
    fields = (
        "schema",
        "canonical_source_request",
        "canonical_destination_receipt",
        "source_request_sha256",
        "destination_receipt_sha256",
        "context_sha256",
        "request_id",
        "request_nonce",
        "receipt_id",
        "receipt_nonce",
        "receiver_recovery_evidence_sha256",
        "receiver_replay_lsn",
        "target_recovery_evidence_sha256",
        "readback_attestation_sha256",
        "readback_attestation_id",
        "readback_attestation_nonce",
        "stage_receipt_sha256",
        "witness_transition_id",
        "target_recovery_observed_at",
        "ledger_entry_id",
        "durable_ledger_entry_sha256",
        "committed_at",
        "ledger_path",
    )
    if (
        any(getattr(value, name) != getattr(result, name) for name in fields)
        or value.canonical_source_request != request.canonical_request
        or value.receiver_recovery_evidence_sha256 != record.receiver_recovery_evidence_sha256
        or value.receiver_replay_lsn != record.replay_lsn
        or value.target_recovery_evidence_sha256 != target.evidence_sha256
        or value.readback_attestation_sha256 != target.readback_attestation_sha256
        or value.readback_attestation_id != target.readback_attestation_id
        or value.readback_attestation_nonce != target.readback_attestation_nonce
        or value.stage_receipt_sha256 != target.stage_receipt_sha256
        or value.witness_transition_id != target.witness_transition_id
        or value.target_recovery_observed_at != target.observed_at
    ):
        _fail("V2_REMOTE_ACK_LEDGER_RECEIPT_TAMPERED_OR_DIVERGED")
    return value
