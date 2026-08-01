"""Crash-safe, local-only receipt journal for physical Full-Matrix phases.

The physical Full-Matrix driver owns phase ordering and never derives a live
adapter.  This module supplies only its required root-owned receipt-journal
protocol.  It is default-off and cannot execute a phase, contact a host,
network, Object Storage, PostgreSQL, Docker, SSH, SCP, rsync, or a shell.

A durable phase claim is intentionally conservative: after a process crashes
between claiming a destructive phase and appending its receipt, the pending
claim remains and later callers receive a busy claim rather than retrying the
phase.  Recovery requires an independently reviewed operator procedure; this
local journal never guesses whether a destructive action ran.

The journal stores only canonical redacted phase receipts plus safe claim
metadata.  It is semantically append-only, validates the receipt hash chain
on every read and mutation, and contains no completion, promotion, writer, or
external-effect authority.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import json
import os
from pathlib import Path
import re
import secrets
import stat
from typing import Any
from uuid import UUID

from core import physical_full_matrix_execution_driver as _driver


__all__ = (
    "FIXED_PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_STATE_ROOT",
    "PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_DEFAULT_ENABLED",
    "PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_SCHEMA",
    "PhysicalFullMatrixReceiptJournalError",
    "RootOwnedPhysicalFullMatrixReceiptJournal",
    "RootOwnedPhysicalFullMatrixReceiptJournalConfig",
)


PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_SCHEMA = (
    "gold-trade-physical-full-matrix-receipt-journal-v1"
)
PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_DEFAULT_ENABLED = False

# This is intentionally a constant, not a constructor/config/CLI input.  A
# deployment provisions this root in advance; this module never creates a
# broad directory tree or accepts an alternate recovery path.
FIXED_PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_STATE_ROOT = Path(
    "/var/lib/trading-bot/physical-full-matrix-receipt-journal"
)

_STATE_VERSION = 1
_JOURNAL_MODE = "root-owned-crash-safe-append-only-phase-receipts-v1"
_STATE_FILENAME = "receipt-journal.json"
_LOCK_FILENAME = "receipt-journal.lock"
_TEMP_PREFIX = ".receipt-journal.tmp-"
_MAX_STATE_BYTES = 1024 * 1024
_MAX_RUNS = 64
_MAX_RECEIPT_BYTES = 64 * 1024
_MAX_RECEIPTS_PER_RUN = len(_driver.PHYSICAL_FULL_MATRIX_PHASES)
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_CLAIM_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$", re.ASCII)
_ZERO_SHA256 = "0" * 64

_STATE_FIELDS = frozenset(
    {
        "schema",
        "version",
        "mode",
        "runs",
        "completion_authorized",
        "promotion_authorized",
        "full_matrix_executed",
    }
)
_RUN_FIELDS = frozenset({"run_id", "plan_sha256", "receipts", "pending_claim"})
_PENDING_CLAIM_FIELDS = frozenset(
    {"claim_id", "sequence", "phase_request_sha256"}
)


class PhysicalFullMatrixReceiptJournalError(ValueError):
    """A redacted local journal refusal; it never exposes a path or OS error."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class RootOwnedPhysicalFullMatrixReceiptJournalConfig:
    """Default-off non-secret policy for the one fixed local journal root."""

    schema: str = PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_SCHEMA
    enabled: bool = PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_DEFAULT_ENABLED
    journal_mode: str = _JOURNAL_MODE


@dataclass(frozen=True)
class _ConfigFacts:
    state_root: Path


@dataclass(frozen=True)
class _PendingClaim:
    claim_id: str
    sequence: int
    phase_request_sha256: str


@dataclass(frozen=True)
class _RunState:
    run_id: UUID
    plan_sha256: str
    receipts: tuple[bytes, ...]
    pending_claim: _PendingClaim | None


@dataclass(frozen=True)
class _JournalState:
    runs: dict[str, _RunState]


def _fail(code: str) -> None:
    raise PhysicalFullMatrixReceiptJournalError(code)


def _require_root() -> None:
    try:
        is_root = os.geteuid() == 0
    except OSError:
        is_root = False
    if not is_root:
        _fail("PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_ROOT_RUNTIME_REQUIRED")


def _require_no_follow() -> None:
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        _fail("PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_PLATFORM_NO_NOFOLLOW")


def _sha256(value: object, *, code: str) -> str:
    if type(value) is not str or _HEX64_RE.fullmatch(value) is None or value == _ZERO_SHA256:
        _fail(code)
    return value


def _run_id(value: object, *, code: str) -> UUID:
    if not isinstance(value, UUID) or value.int == 0:
        _fail(code)
    return value


def _sequence(value: object, *, code: str) -> int:
    if type(value) is not int or not 1 <= value <= _MAX_RECEIPTS_PER_RUN:
        _fail(code)
    return value


def _claim_id(value: object, *, code: str) -> str:
    if type(value) is not str or _CLAIM_ID_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _canonical_json(value: object, *, code: str) -> bytes:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError):
        _fail(code)


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if type(key) is not str or key in result:
            _fail("PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_STATE_JSON_INVALID")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> None:
    _fail("PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_STATE_JSON_INVALID")


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
        _fail("PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_STATE_ROOT_UNSAFE")
    _require_no_follow()
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
                _fail("PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_STATE_ROOT_UNSAFE")
    except PhysicalFullMatrixReceiptJournalError:
        raise
    except OSError:
        _fail("PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_STATE_ROOT_UNSAFE")
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass


def _open_state_root() -> tuple[Path, int]:
    root = FIXED_PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_STATE_ROOT
    _validate_ancestors(root)
    try:
        before = os.lstat(root)
        resolved = root.resolve(strict=True)
    except OSError:
        _fail("PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_STATE_ROOT_UNSAFE")
    if (
        resolved != root
        or stat.S_ISLNK(before.st_mode)
        or not stat.S_ISDIR(before.st_mode)
        or before.st_uid != 0
        or stat.S_IMODE(before.st_mode) != 0o700
    ):
        _fail("PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_STATE_ROOT_UNSAFE")
    descriptor = -1
    try:
        descriptor = os.open(
            root,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
        )
        opened = os.fstat(descriptor)
        after = os.lstat(root)
        if (
            _metadata_tuple(opened) != _metadata_tuple(before)
            or _metadata_tuple(after) != _metadata_tuple(before)
            or not stat.S_ISDIR(opened.st_mode)
            or opened.st_uid != 0
            or stat.S_IMODE(opened.st_mode) != 0o700
        ):
            _fail("PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_STATE_ROOT_UNSAFE")
        return resolved, descriptor
    except PhysicalFullMatrixReceiptJournalError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError:
        if descriptor >= 0:
            os.close(descriptor)
        _fail("PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_STATE_ROOT_UNSAFE")


def _safe_child_metadata(
    root_fd: int,
    name: str,
    *,
    maximum_bytes: int | None,
    missing_ok: bool,
    code: str,
) -> os.stat_result | None:
    try:
        metadata = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
    except FileNotFoundError:
        if missing_ok:
            return None
        _fail(code)
    except OSError:
        _fail(code)
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or (maximum_bytes is not None and not 1 <= metadata.st_size <= maximum_bytes)
    ):
        _fail(code)
    return metadata


def _fsync(descriptor: int, *, code: str) -> None:
    try:
        os.fsync(descriptor)
    except OSError:
        _fail(code)


def _open_lock(root_fd: int) -> int:
    _require_no_follow()
    flags = os.O_RDWR | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    descriptor = -1
    created = False
    try:
        try:
            descriptor = os.open(
                _LOCK_FILENAME,
                flags | os.O_CREAT | os.O_EXCL,
                0o600,
                dir_fd=root_fd,
            )
            created = True
        except FileExistsError:
            descriptor = os.open(_LOCK_FILENAME, flags, dir_fd=root_fd)
        if created:
            os.fchmod(descriptor, 0o600)
            _fsync(
                descriptor,
                code="PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_LOCK_FSYNC_FAILED",
            )
            _fsync(
                root_fd,
                code="PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_DIRECTORY_FSYNC_FAILED",
            )
        before = _safe_child_metadata(
            root_fd,
            _LOCK_FILENAME,
            maximum_bytes=None,
            missing_ok=False,
            code="PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_LOCK_UNSAFE",
        )
        opened = os.fstat(descriptor)
        after = _safe_child_metadata(
            root_fd,
            _LOCK_FILENAME,
            maximum_bytes=None,
            missing_ok=False,
            code="PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_LOCK_UNSAFE",
        )
        if (
            before is None
            or after is None
            or _metadata_tuple(opened) != _metadata_tuple(before)
            or _metadata_tuple(after) != _metadata_tuple(before)
        ):
            _fail("PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_LOCK_UNSAFE")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
        except OSError:
            _fail("PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_LOCK_FAILED")
        return descriptor
    except PhysicalFullMatrixReceiptJournalError:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass
        raise
    except OSError:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass
        _fail("PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_LOCK_OPEN_FAILED")


@contextmanager
def _locked_state_root() -> Iterator[tuple[Path, int]]:
    root, root_fd = _open_state_root()
    lock_fd = -1
    try:
        lock_fd = _open_lock(root_fd)
        yield root, root_fd
    finally:
        if lock_fd >= 0:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            except OSError:
                pass
            try:
                os.close(lock_fd)
            except OSError:
                pass
        try:
            os.close(root_fd)
        except OSError:
            pass


def _receipt_bytes(value: object, *, code: str) -> bytes:
    if type(value) is not str:
        _fail(code)
    try:
        raw = value.encode("ascii", "strict")
    except UnicodeEncodeError:
        _fail(code)
    if not 1 <= len(raw) <= _MAX_RECEIPT_BYTES or not raw.endswith(b"\n"):
        _fail(code)
    return raw


def _parse_receipts(
    *,
    run_id: UUID,
    plan_sha256: str,
    value: object,
) -> tuple[bytes, ...]:
    if type(value) is not list or len(value) > _MAX_RECEIPTS_PER_RUN:
        _fail("PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_RECEIPTS_INVALID")
    result: list[bytes] = []
    previous = _ZERO_SHA256
    request_hashes: set[str] = set()
    receipt_hashes: set[str] = set()
    for expected_sequence, item in enumerate(value, start=1):
        raw = _receipt_bytes(
            item,
            code="PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_RECEIPT_ENCODING_INVALID",
        )
        try:
            receipt = _driver.parse_physical_full_matrix_run_receipt(raw)
        except Exception:
            _fail("PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_RECEIPT_INVALID")
        if (
            receipt.run_id != run_id
            or receipt.plan_sha256 != plan_sha256
            or receipt.sequence != expected_sequence
            or receipt.previous_receipt_sha256 != previous
            or receipt.phase_request_sha256 in request_hashes
            or receipt.receipt_sha256 in receipt_hashes
        ):
            _fail("PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_CHAIN_INVALID")
        request_hashes.add(receipt.phase_request_sha256)
        receipt_hashes.add(receipt.receipt_sha256)
        previous = receipt.receipt_sha256
        result.append(raw)
    return tuple(result)


def _parse_pending(
    value: object,
    *,
    receipt_count: int,
    request_hashes: set[str],
) -> _PendingClaim | None:
    if value is None:
        return None
    if type(value) is not dict or set(value) != _PENDING_CLAIM_FIELDS:
        _fail("PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_PENDING_CLAIM_INVALID")
    pending = _PendingClaim(
        claim_id=_claim_id(
            value["claim_id"],
            code="PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_PENDING_CLAIM_INVALID",
        ),
        sequence=_sequence(
            value["sequence"],
            code="PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_PENDING_CLAIM_INVALID",
        ),
        phase_request_sha256=_sha256(
            value["phase_request_sha256"],
            code="PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_PENDING_CLAIM_INVALID",
        ),
    )
    if pending.sequence != receipt_count + 1 or pending.phase_request_sha256 in request_hashes:
        _fail("PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_PENDING_CLAIM_INVALID")
    return pending


def _state_from_mapping(value: object) -> _JournalState:
    if type(value) is not dict or set(value) != _STATE_FIELDS:
        _fail("PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_STATE_FIELDS_INVALID")
    if (
        value["schema"] != PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_SCHEMA
        or value["version"] != _STATE_VERSION
        or value["mode"] != _JOURNAL_MODE
        or value["completion_authorized"] is not False
        or value["promotion_authorized"] is not False
        or value["full_matrix_executed"] is not False
        or type(value["runs"]) is not dict
        or len(value["runs"]) > _MAX_RUNS
    ):
        _fail("PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_STATE_FIELDS_INVALID")
    runs: dict[str, _RunState] = {}
    for key, run_value in value["runs"].items():
        if type(key) is not str or type(run_value) is not dict or set(run_value) != _RUN_FIELDS:
            _fail("PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_RUN_INVALID")
        try:
            run_id = UUID(key)
        except (TypeError, ValueError, AttributeError):
            _fail("PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_RUN_INVALID")
        if run_id.int == 0 or str(run_id) != key:
            _fail("PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_RUN_INVALID")
        if run_value["run_id"] != key:
            _fail("PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_RUN_INVALID")
        plan_sha256 = _sha256(
            run_value["plan_sha256"],
            code="PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_RUN_INVALID",
        )
        receipts = _parse_receipts(
            run_id=run_id,
            plan_sha256=plan_sha256,
            value=run_value["receipts"],
        )
        request_hashes = {
            _driver.parse_physical_full_matrix_run_receipt(item).phase_request_sha256
            for item in receipts
        }
        pending = _parse_pending(
            run_value["pending_claim"],
            receipt_count=len(receipts),
            request_hashes=request_hashes,
        )
        runs[key] = _RunState(
            run_id=run_id,
            plan_sha256=plan_sha256,
            receipts=receipts,
            pending_claim=pending,
        )
    return _JournalState(runs=runs)


def _state_mapping(state: _JournalState) -> dict[str, object]:
    runs: dict[str, object] = {}
    for key, run in state.runs.items():
        pending: dict[str, object] | None = None
        if run.pending_claim is not None:
            pending = {
                "claim_id": run.pending_claim.claim_id,
                "sequence": run.pending_claim.sequence,
                "phase_request_sha256": run.pending_claim.phase_request_sha256,
            }
        runs[key] = {
            "run_id": str(run.run_id),
            "plan_sha256": run.plan_sha256,
            "receipts": [item.decode("ascii") for item in run.receipts],
            "pending_claim": pending,
        }
    return {
        "schema": PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_SCHEMA,
        "version": _STATE_VERSION,
        "mode": _JOURNAL_MODE,
        "runs": runs,
        "completion_authorized": False,
        "promotion_authorized": False,
        "full_matrix_executed": False,
    }


def _empty_state() -> _JournalState:
    return _JournalState(runs={})


def _read_state(root_fd: int) -> _JournalState:
    _require_no_follow()
    descriptor = -1
    try:
        try:
            descriptor = os.open(
                _STATE_FILENAME,
                os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
                dir_fd=root_fd,
            )
        except FileNotFoundError:
            return _empty_state()
        except OSError:
            _fail("PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_STATE_OPEN_FAILED")
        before = _safe_child_metadata(
            root_fd,
            _STATE_FILENAME,
            maximum_bytes=_MAX_STATE_BYTES,
            missing_ok=False,
            code="PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_STATE_UNSAFE",
        )
        opened = os.fstat(descriptor)
        if before is None or _metadata_tuple(opened) != _metadata_tuple(before):
            _fail("PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_STATE_UNSAFE")
        payload = bytearray()
        while len(payload) < opened.st_size:
            try:
                chunk = os.read(descriptor, opened.st_size - len(payload))
            except OSError:
                _fail("PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_STATE_READ_FAILED")
            if not chunk:
                _fail("PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_STATE_READ_FAILED")
            payload.extend(chunk)
        try:
            if os.read(descriptor, 1):
                _fail("PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_STATE_READ_FAILED")
        except OSError:
            _fail("PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_STATE_READ_FAILED")
        after_open = os.fstat(descriptor)
        after_path = _safe_child_metadata(
            root_fd,
            _STATE_FILENAME,
            maximum_bytes=_MAX_STATE_BYTES,
            missing_ok=False,
            code="PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_STATE_UNSAFE",
        )
        if (
            after_path is None
            or _metadata_tuple(after_open) != _metadata_tuple(opened)
            or _metadata_tuple(after_path) != _metadata_tuple(opened)
        ):
            _fail("PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_STATE_CHANGED_DURING_READ")
        raw = bytes(payload)
    except PhysicalFullMatrixReceiptJournalError:
        raise
    except OSError:
        _fail("PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_STATE_READ_FAILED")
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass
    if not raw.endswith(b"\n") or not 2 <= len(raw) <= _MAX_STATE_BYTES:
        _fail("PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_STATE_ENCODING_INVALID")
    try:
        decoded = json.loads(
            raw[:-1].decode("ascii", "strict"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except PhysicalFullMatrixReceiptJournalError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        _fail("PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_STATE_ENCODING_INVALID")
    canonical = _canonical_json(
        decoded,
        code="PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_STATE_ENCODING_INVALID",
    ) + b"\n"
    if canonical != raw:
        _fail("PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_STATE_NONCANONICAL")
    return _state_from_mapping(decoded)


def _write_all(descriptor: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        try:
            written = os.write(descriptor, payload[offset:])
        except OSError:
            _fail("PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_TEMP_WRITE_FAILED")
        if type(written) is not int or written <= 0:
            _fail("PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_TEMP_WRITE_FAILED")
        offset += written


def _remove_own_temp(
    *,
    root_fd: int,
    name: str,
    device: int | None,
    inode: int | None,
) -> None:
    if device is None or inode is None:
        return
    try:
        metadata = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
        if (
            not stat.S_ISLNK(metadata.st_mode)
            and stat.S_ISREG(metadata.st_mode)
            and metadata.st_uid == 0
            and metadata.st_nlink == 1
            and metadata.st_dev == device
            and metadata.st_ino == inode
        ):
            os.unlink(name, dir_fd=root_fd)
    except OSError:
        pass


def _validate_existing_state_target(root_fd: int) -> None:
    metadata = _safe_child_metadata(
        root_fd,
        _STATE_FILENAME,
        maximum_bytes=_MAX_STATE_BYTES,
        missing_ok=True,
        code="PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_STATE_UNSAFE",
    )
    if metadata is not None and metadata.st_size < 2:
        _fail("PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_STATE_UNSAFE")


def _atomic_replace_state(root_fd: int, state: _JournalState) -> None:
    payload = _canonical_json(
        _state_mapping(state),
        code="PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_STATE_SERIALIZATION_FAILED",
    ) + b"\n"
    if not 2 <= len(payload) <= _MAX_STATE_BYTES:
        _fail("PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_STATE_SIZE_INVALID")
    _validate_existing_state_target(root_fd)
    _require_no_follow()
    temporary_name: str | None = None
    temporary_device: int | None = None
    temporary_inode: int | None = None
    descriptor = -1
    replaced = False
    try:
        for _attempt in range(8):
            try:
                candidate = _TEMP_PREFIX + secrets.token_hex(24)
            except Exception:
                _fail("PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_TEMP_NAME_FAILED")
            try:
                descriptor = os.open(
                    candidate,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
                    | getattr(os, "O_CLOEXEC", 0),
                    0o600,
                    dir_fd=root_fd,
                )
                temporary_name = candidate
                break
            except FileExistsError:
                continue
            except OSError:
                _fail("PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_TEMP_OPEN_FAILED")
        if descriptor < 0 or temporary_name is None:
            _fail("PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_TEMP_OPEN_FAILED")
        os.fchmod(descriptor, 0o600)
        initial_metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(initial_metadata.st_mode)
            or initial_metadata.st_uid != 0
            or initial_metadata.st_nlink != 1
            or stat.S_IMODE(initial_metadata.st_mode) != 0o600
        ):
            _fail("PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_TEMP_UNSAFE")
        temporary_device = initial_metadata.st_dev
        temporary_inode = initial_metadata.st_ino
        _write_all(descriptor, payload)
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != 0
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_size != len(payload)
            or metadata.st_dev != temporary_device
            or metadata.st_ino != temporary_inode
        ):
            _fail("PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_TEMP_UNSAFE")
        _fsync(
            descriptor,
            code="PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_TEMP_FSYNC_FAILED",
        )
        try:
            os.close(descriptor)
        except OSError:
            _fail("PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_TEMP_CLOSE_FAILED")
        descriptor = -1
        _validate_existing_state_target(root_fd)
        try:
            os.replace(
                temporary_name,
                _STATE_FILENAME,
                src_dir_fd=root_fd,
                dst_dir_fd=root_fd,
            )
        except OSError:
            _fail("PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_STATE_REPLACE_FAILED")
        replaced = True
        temporary_name = None
        final_fd = -1
        try:
            final_fd = os.open(
                _STATE_FILENAME,
                os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
                dir_fd=root_fd,
            )
            final_metadata = os.fstat(final_fd)
            final_path = _safe_child_metadata(
                root_fd,
                _STATE_FILENAME,
                maximum_bytes=_MAX_STATE_BYTES,
                missing_ok=False,
                code="PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_STATE_UNSAFE",
            )
            if (
                final_path is None
                or _metadata_tuple(final_path) != _metadata_tuple(final_metadata)
                or not stat.S_ISREG(final_metadata.st_mode)
                or final_metadata.st_uid != 0
                or final_metadata.st_nlink != 1
                or stat.S_IMODE(final_metadata.st_mode) != 0o600
                or final_metadata.st_size != len(payload)
                or final_metadata.st_dev != temporary_device
                or final_metadata.st_ino != temporary_inode
            ):
                _fail("PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_STATE_UNSAFE")
            _fsync(
                final_fd,
                code="PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_STATE_FILE_FSYNC_FAILED",
            )
        finally:
            if final_fd >= 0:
                try:
                    os.close(final_fd)
                except OSError:
                    pass
        _fsync(
            root_fd,
            code="PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_DIRECTORY_FSYNC_FAILED",
        )
    except PhysicalFullMatrixReceiptJournalError:
        raise
    except OSError:
        _fail("PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_STATE_WRITE_FAILED")
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if not replaced and temporary_name is not None:
            _remove_own_temp(
                root_fd=root_fd,
                name=temporary_name,
                device=temporary_device,
                inode=temporary_inode,
            )


def _make_claim_id(existing: set[str]) -> str:
    for _attempt in range(8):
        try:
            candidate = "pfm-journal-claim-" + secrets.token_urlsafe(24)
        except Exception:
            _fail("PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_CLAIM_ID_FAILED")
        if _CLAIM_ID_RE.fullmatch(candidate) is not None and candidate not in existing:
            return candidate
    _fail("PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_CLAIM_ID_FAILED")


def _config_facts(value: object) -> _ConfigFacts:
    if type(value) is not RootOwnedPhysicalFullMatrixReceiptJournalConfig:
        _fail("PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_CONFIG_INVALID")
    if (
        value.schema != PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_SCHEMA
        or type(value.enabled) is not bool
        or value.journal_mode != _JOURNAL_MODE
    ):
        _fail("PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_CONFIG_INVALID")
    if value.enabled is not True:
        _fail("PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_DISABLED")
    _require_root()
    return _ConfigFacts(state_root=FIXED_PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_STATE_ROOT)


def _with_run(state: _JournalState, run: _RunState) -> _JournalState:
    runs = dict(state.runs)
    runs[str(run.run_id)] = run
    return _JournalState(runs=runs)


class RootOwnedPhysicalFullMatrixReceiptJournal:
    """Concrete local journal for the driver's claim/read/append protocol.

    Construction is inert. Each method rechecks the default-off/root/fixed-root
    gates so replacement, mode drift, symlink insertion, or a reopened process
    cannot turn a prior object instance into an authority bypass.
    """

    def __init__(self, config: RootOwnedPhysicalFullMatrixReceiptJournalConfig) -> None:
        self._config = config
        self._live_claims: dict[str, _driver.PhysicalFullMatrixPhaseClaim] = {}

    def read_receipts(self, *, run_id: UUID) -> Sequence[bytes]:
        _config_facts(self._config)
        checked_run_id = _run_id(
            run_id,
            code="PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_RUN_ID_INVALID",
        )
        with _locked_state_root() as (_root, root_fd):
            state = _read_state(root_fd)
            run = state.runs.get(str(checked_run_id))
            return () if run is None else tuple(run.receipts)

    def claim_phase(
        self,
        *,
        run_id: UUID,
        plan_sha256: str,
        sequence: int,
        phase_request_sha256: str,
    ) -> _driver.PhysicalFullMatrixPhaseClaim:
        _config_facts(self._config)
        checked_run_id = _run_id(
            run_id,
            code="PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_CLAIM_INPUT_INVALID",
        )
        checked_plan = _sha256(
            plan_sha256,
            code="PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_CLAIM_INPUT_INVALID",
        )
        checked_sequence = _sequence(
            sequence,
            code="PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_CLAIM_INPUT_INVALID",
        )
        checked_request = _sha256(
            phase_request_sha256,
            code="PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_CLAIM_INPUT_INVALID",
        )
        with _locked_state_root() as (_root, root_fd):
            state = _read_state(root_fd)
            key = str(checked_run_id)
            run = state.runs.get(key)
            if run is None:
                run = _RunState(
                    run_id=checked_run_id,
                    plan_sha256=checked_plan,
                    receipts=(),
                    pending_claim=None,
                )
            elif run.plan_sha256 != checked_plan:
                _fail("PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_PLAN_CONFLICT")

            if checked_sequence <= len(run.receipts):
                stored = run.receipts[checked_sequence - 1]
                try:
                    parsed = _driver.parse_physical_full_matrix_run_receipt(stored)
                except Exception:
                    _fail("PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_RECEIPT_INVALID")
                if parsed.phase_request_sha256 != checked_request:
                    _fail("PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_CLAIM_CONFLICT")
                return _driver.PhysicalFullMatrixPhaseClaim(
                    run_id=checked_run_id,
                    plan_sha256=checked_plan,
                    sequence=checked_sequence,
                    phase_request_sha256=checked_request,
                    existing_receipt=stored,
                )
            if checked_sequence != len(run.receipts) + 1:
                _fail("PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_CLAIM_SEQUENCE_INVALID")
            if run.pending_claim is not None:
                if (
                    run.pending_claim.sequence != checked_sequence
                    or run.pending_claim.phase_request_sha256 != checked_request
                ):
                    _fail("PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_PENDING_CLAIM_CONFLICT")
                # The driver recognizes this intentionally empty result as a
                # busy claim and refuses to invoke the phase a second time.
                return _driver.PhysicalFullMatrixPhaseClaim(
                    run_id=checked_run_id,
                    plan_sha256=checked_plan,
                    sequence=checked_sequence,
                    phase_request_sha256=checked_request,
                )

            existing_claim_ids = {
                item.pending_claim.claim_id
                for item in state.runs.values()
                if item.pending_claim is not None
            }
            pending = _PendingClaim(
                claim_id=_make_claim_id(existing_claim_ids),
                sequence=checked_sequence,
                phase_request_sha256=checked_request,
            )
            claimed_run = _RunState(
                run_id=run.run_id,
                plan_sha256=run.plan_sha256,
                receipts=run.receipts,
                pending_claim=pending,
            )
            _atomic_replace_state(root_fd, _with_run(state, claimed_run))
            verified = _read_state(root_fd).runs.get(key)
            if verified != claimed_run:
                _fail("PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_CLAIM_NOT_DURABLE")
            claim = _driver.PhysicalFullMatrixPhaseClaim(
                run_id=checked_run_id,
                plan_sha256=checked_plan,
                sequence=checked_sequence,
                phase_request_sha256=checked_request,
                claim_id=pending.claim_id,
            )
            self._live_claims[pending.claim_id] = claim
            return claim

    def append_claimed(
        self,
        *,
        claim: _driver.PhysicalFullMatrixPhaseClaim,
        canonical_receipt: bytes,
    ) -> bytes:
        _config_facts(self._config)
        if (
            type(claim) is not _driver.PhysicalFullMatrixPhaseClaim
            or claim.claim_id is None
            or claim.existing_receipt is not None
        ):
            _fail("PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_CLAIM_NOT_LIVE")
        checked_claim_id = _claim_id(
            claim.claim_id,
            code="PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_CLAIM_NOT_LIVE",
        )
        if self._live_claims.get(checked_claim_id) is not claim:
            _fail("PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_CLAIM_NOT_LIVE")
        checked_run_id = _run_id(
            claim.run_id,
            code="PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_CLAIM_NOT_LIVE",
        )
        checked_plan = _sha256(
            claim.plan_sha256,
            code="PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_CLAIM_NOT_LIVE",
        )
        checked_sequence = _sequence(
            claim.sequence,
            code="PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_CLAIM_NOT_LIVE",
        )
        checked_request = _sha256(
            claim.phase_request_sha256,
            code="PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_CLAIM_NOT_LIVE",
        )
        if (
            type(canonical_receipt) is not bytes
            or not 1 <= len(canonical_receipt) <= _MAX_RECEIPT_BYTES
        ):
            _fail("PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_RECEIPT_INVALID")
        try:
            receipt = _driver.parse_physical_full_matrix_run_receipt(canonical_receipt)
        except Exception:
            _fail("PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_RECEIPT_INVALID")
        with _locked_state_root() as (_root, root_fd):
            state = _read_state(root_fd)
            run = state.runs.get(str(checked_run_id))
            if run is None or run.plan_sha256 != checked_plan:
                _fail("PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_CLAIM_NOT_PENDING")
            if checked_sequence <= len(run.receipts):
                stored = run.receipts[checked_sequence - 1]
                if stored != canonical_receipt:
                    _fail("PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_APPEND_CONFLICT")
                try:
                    del self._live_claims[checked_claim_id]
                except KeyError:
                    _fail("PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_CLAIM_NOT_LIVE")
                return stored
            pending = run.pending_claim
            if (
                pending is None
                or pending.claim_id != checked_claim_id
                or pending.sequence != checked_sequence
                or pending.phase_request_sha256 != checked_request
                or checked_sequence != len(run.receipts) + 1
            ):
                _fail("PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_CLAIM_NOT_PENDING")
            previous = (
                _ZERO_SHA256
                if not run.receipts
                else _driver.parse_physical_full_matrix_run_receipt(
                    run.receipts[-1]
                ).receipt_sha256
            )
            if (
                receipt.run_id != checked_run_id
                or receipt.plan_sha256 != checked_plan
                or receipt.sequence != checked_sequence
                or receipt.phase_request_sha256 != checked_request
                or receipt.previous_receipt_sha256 != previous
            ):
                _fail("PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_RECEIPT_CLAIM_MISMATCH")
            appended_run = _RunState(
                run_id=run.run_id,
                plan_sha256=run.plan_sha256,
                receipts=run.receipts + (canonical_receipt,),
                pending_claim=None,
            )
            _atomic_replace_state(root_fd, _with_run(state, appended_run))
            verified = _read_state(root_fd).runs.get(str(checked_run_id))
            if (
                verified is None
                or verified.pending_claim is not None
                or verified.receipts != appended_run.receipts
            ):
                _fail("PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_APPEND_NOT_DURABLE")
            try:
                del self._live_claims[checked_claim_id]
            except KeyError:
                _fail("PHYSICAL_FULL_MATRIX_RECEIPT_JOURNAL_CLAIM_NOT_LIVE")
            return verified.receipts[-1]
