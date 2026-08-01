"""Root-owned append-only single-consumption ledger for v2 receiver handoffs.

This deliberately contains no Object Storage, age, network, peer, database,
or v1 staging code.  It reserves a verified receiver handoff *before* a
receiver performs its first exact-version GET.  A receipt ID or nonce is
burned by a create-only index even if later local staging fails; there is no
resume, delete, reset, or successful replay operation in this boundary.
"""

from __future__ import annotations

from collections.abc import Mapping
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
from core.physical_wal_chunked_base_backup_handoff_receipt import (
    VerifiedPhysicalWalChunkedBaseBackupHandoffReceipt,
    require_verified_physical_wal_chunked_base_backup_handoff_receipt,
)
from core.physical_wal_chunked_base_backup_manifest import (
    VerifiedPhysicalWalChunkedBaseBackupManifest,
    require_verified_physical_wal_chunked_base_backup_manifest,
)


__all__ = (
    "PHYSICAL_WAL_CHUNKED_BASE_BACKUP_RECEIVER_RECEIPT_LEDGER_DEFAULT_ENABLED",
    "PHYSICAL_WAL_CHUNKED_BASE_BACKUP_RECEIVER_RECEIPT_LEDGER_SCHEMA",
    "PhysicalWalChunkedBaseBackupReceiverReceiptLedgerClaim",
    "PhysicalWalChunkedBaseBackupReceiverReceiptLedgerConfig",
    "PhysicalWalChunkedBaseBackupReceiverReceiptLedgerError",
    "claim_root_owned_physical_wal_chunked_base_backup_receiver_handoff",
    "complete_root_owned_physical_wal_chunked_base_backup_receiver_handoff",
    "fail_root_owned_physical_wal_chunked_base_backup_receiver_handoff",
    "validate_root_owned_physical_wal_chunked_base_backup_receiver_receipt_ledger_config",
)


PHYSICAL_WAL_CHUNKED_BASE_BACKUP_RECEIVER_RECEIPT_LEDGER_SCHEMA = (
    "gold-trade-physical-wal-chunked-base-backup-receiver-receipt-ledger-v2"
)
PHYSICAL_WAL_CHUNKED_BASE_BACKUP_RECEIVER_RECEIPT_LEDGER_DEFAULT_ENABLED = False
_OPEN_KIND = "physical_wal_chunked_base_backup_receiver_handoff_open"
_TERMINAL_KIND = "physical_wal_chunked_base_backup_receiver_handoff_terminal"
_INDEX_KIND = "physical_wal_chunked_base_backup_receiver_handoff_index"
_STATUS_OPEN = "OPEN"
_STATUS_COMPLETED = "COMPLETED"
_STATUS_FAILED = "FAILED"
_MAX_RECORD_BYTES = 256 * 1024
_MAX_INTENT_JOURNAL_RECORDS = 1_000_000
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$", re.ASCII)
_NONCE_RE = re.compile(r"^[A-Za-z0-9_-]{22,128}$", re.ASCII)
_FAILURE_RE = re.compile(r"^[A-Z][A-Z0-9_]{2,127}$", re.ASCII)
_JOURNAL_NAME_RE = re.compile(r"^[0-9a-f]{64}\.json$", re.ASCII)
_CAPABILITY = object()


class PhysicalWalChunkedBaseBackupReceiverReceiptLedgerError(RuntimeError):
    """A receipt cannot safely be reserved or terminally recorded."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class PhysicalWalChunkedBaseBackupReceiverReceiptLedgerConfig:
    """Fixed, pre-existing root-owned directory for append-only receipt state."""

    ledger_root: Path | None = None
    owner_uid: int = 0
    enabled: bool = PHYSICAL_WAL_CHUNKED_BASE_BACKUP_RECEIVER_RECEIPT_LEDGER_DEFAULT_ENABLED


@dataclass(frozen=True)
class PhysicalWalChunkedBaseBackupReceiverReceiptLedgerClaim:
    """Opaque OPEN claim; it can transition exactly once to a terminal state."""

    receipt_id: str
    receipt_nonce: str
    manifest_id: str
    manifest_sha256: str
    binding_sha256: str
    session_sha256: str
    finalization_permit_sha256: str
    snapshot_sha256: str
    snapshot_bytes: int
    ledger_key_sha256: str
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)


@dataclass(frozen=True)
class _Facts:
    root: Path
    owner_uid: int


def _fail(code: str) -> None:
    raise PhysicalWalChunkedBaseBackupReceiverReceiptLedgerError(code)


def _utc(value: object) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        _fail("CHUNKED_BASE_BACKUP_RECEIVER_LEDGER_CLOCK_INVALID")
    return value.astimezone(timezone.utc)


def _safe_root(value: object) -> Path:
    if not isinstance(value, Path) or not value.is_absolute() or any(part in {".", ".."} for part in value.parts):
        _fail("CHUNKED_BASE_BACKUP_RECEIVER_LEDGER_ROOT_INVALID")
    if len(str(value)) > 4096:
        _fail("CHUNKED_BASE_BACKUP_RECEIVER_LEDGER_ROOT_INVALID")
    return value


def _facts(config: object, *, require_enabled: bool) -> _Facts:
    if type(config) is not PhysicalWalChunkedBaseBackupReceiverReceiptLedgerConfig:
        _fail("CHUNKED_BASE_BACKUP_RECEIVER_LEDGER_CONFIG_INVALID")
    if type(config.enabled) is not bool or (require_enabled and config.enabled is not True):
        _fail("CHUNKED_BASE_BACKUP_RECEIVER_LEDGER_CONFIG_INVALID")
    if type(config.owner_uid) is not int or config.owner_uid != 0:
        _fail("CHUNKED_BASE_BACKUP_RECEIVER_LEDGER_OWNER_INVALID")
    return _Facts(root=_safe_root(config.ledger_root), owner_uid=0)


def validate_root_owned_physical_wal_chunked_base_backup_receiver_receipt_ledger_config(
    config: object,
    *,
    require_enabled: bool = True,
) -> None:
    """Validate no-secret policy only; no ledger file is opened here."""

    _facts(config, require_enabled=require_enabled)


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, item in pairs:
        if key in result:
            _fail("CHUNKED_BASE_BACKUP_RECEIVER_LEDGER_RECORD_INVALID")
        result[key] = item
    return result


def _reject_json_constant(_value: str) -> None:
    _fail("CHUNKED_BASE_BACKUP_RECEIVER_LEDGER_RECORD_INVALID")


def _canonical(value: object) -> bytes:
    try:
        return canonical_json_bytes(value)
    except (TypeError, ValueError):
        _fail("CHUNKED_BASE_BACKUP_RECEIVER_LEDGER_RECORD_INVALID")


def _sha(value: object, *, code: str) -> str:
    if type(value) is not str or SHA256_RE.fullmatch(value) is None or value == "0" * 64:
        _fail(code)
    return value


def _id(value: object, *, code: str) -> str:
    if type(value) is not str or _ID_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _nonce(value: object, *, code: str) -> str:
    if type(value) is not str or _NONCE_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _open_secure_dir(path: Path, *, owner_uid: int, root: bool = False) -> int:
    if os.geteuid() != 0:
        _fail("CHUNKED_BASE_BACKUP_RECEIVER_LEDGER_ROOT_REQUIRED")
    try:
        listed = os.lstat(path)
    except OSError:
        _fail("CHUNKED_BASE_BACKUP_RECEIVER_LEDGER_ROOT_UNAVAILABLE")
    if (
        stat.S_ISLNK(listed.st_mode)
        or not stat.S_ISDIR(listed.st_mode)
        or listed.st_uid != owner_uid
        or stat.S_IMODE(listed.st_mode) != 0o700
    ):
        _fail("CHUNKED_BASE_BACKUP_RECEIVER_LEDGER_ROOT_UNSAFE")
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
        observed = os.fstat(fd)
    except OSError:
        _fail("CHUNKED_BASE_BACKUP_RECEIVER_LEDGER_ROOT_UNSAFE")
    if (
        observed.st_dev != listed.st_dev
        or observed.st_ino != listed.st_ino
        or observed.st_uid != owner_uid
        or not stat.S_ISDIR(observed.st_mode)
        or stat.S_IMODE(observed.st_mode) != 0o700
    ):
        os.close(fd)
        _fail("CHUNKED_BASE_BACKUP_RECEIVER_LEDGER_ROOT_RACED")
    return fd


def _ensure_child_dir(parent_fd: int, name: str, *, owner_uid: int) -> int:
    try:
        os.mkdir(name, 0o700, dir_fd=parent_fd)
        os.fsync(parent_fd)
    except FileExistsError:
        pass
    except OSError:
        _fail("CHUNKED_BASE_BACKUP_RECEIVER_LEDGER_DIRECTORY_CREATE_FAILED")
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(name, flags, dir_fd=parent_fd)
        observed = os.fstat(fd)
    except OSError:
        _fail("CHUNKED_BASE_BACKUP_RECEIVER_LEDGER_DIRECTORY_UNSAFE")
    if (
        not stat.S_ISDIR(observed.st_mode)
        or observed.st_uid != owner_uid
        or stat.S_IMODE(observed.st_mode) != 0o700
    ):
        os.close(fd)
        _fail("CHUNKED_BASE_BACKUP_RECEIVER_LEDGER_DIRECTORY_UNSAFE")
    return fd


def _lock(root_fd: int, *, owner_uid: int) -> int:
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(".receiver-receipt-ledger.lock", flags, 0o600, dir_fd=root_fd)
        observed = os.fstat(fd)
    except OSError:
        _fail("CHUNKED_BASE_BACKUP_RECEIVER_LEDGER_LOCK_UNSAFE")
    if (
        not stat.S_ISREG(observed.st_mode)
        or observed.st_uid != owner_uid
        or stat.S_IMODE(observed.st_mode) != 0o600
        or observed.st_nlink != 1
    ):
        os.close(fd)
        _fail("CHUNKED_BASE_BACKUP_RECEIVER_LEDGER_LOCK_UNSAFE")
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
    except OSError:
        os.close(fd)
        _fail("CHUNKED_BASE_BACKUP_RECEIVER_LEDGER_LOCK_FAILED")
    return fd


def _create_json_exclusive(directory_fd: int, name: str, payload: Mapping[str, Any]) -> None:
    raw = _canonical(dict(payload))
    if not raw or len(raw) > _MAX_RECORD_BYTES:
        _fail("CHUNKED_BASE_BACKUP_RECEIVER_LEDGER_RECORD_INVALID")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(name, flags, 0o600, dir_fd=directory_fd)
    except FileExistsError:
        _fail("CHUNKED_BASE_BACKUP_RECEIVER_LEDGER_RECORD_EXISTS")
    except OSError:
        _fail("CHUNKED_BASE_BACKUP_RECEIVER_LEDGER_RECORD_CREATE_FAILED")
    try:
        offset = 0
        while offset < len(raw):
            written = os.write(fd, raw[offset:])
            if written <= 0:
                _fail("CHUNKED_BASE_BACKUP_RECEIVER_LEDGER_RECORD_WRITE_FAILED")
            offset += written
        os.fsync(fd)
    except PhysicalWalChunkedBaseBackupReceiverReceiptLedgerError:
        raise
    except OSError:
        _fail("CHUNKED_BASE_BACKUP_RECEIVER_LEDGER_RECORD_WRITE_FAILED")
    finally:
        os.close(fd)
    try:
        os.fsync(directory_fd)
    except OSError:
        _fail("CHUNKED_BASE_BACKUP_RECEIVER_LEDGER_DIRECTORY_SYNC_FAILED")


def _read_json(directory_fd: int, name: str) -> dict[str, Any]:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(name, flags, dir_fd=directory_fd)
        observed = os.fstat(fd)
    except FileNotFoundError:
        _fail("CHUNKED_BASE_BACKUP_RECEIVER_LEDGER_RECORD_MISSING")
    except OSError:
        _fail("CHUNKED_BASE_BACKUP_RECEIVER_LEDGER_RECORD_UNSAFE")
    try:
        if (
            not stat.S_ISREG(observed.st_mode)
            or observed.st_uid != 0
            or stat.S_IMODE(observed.st_mode) != 0o600
            or observed.st_nlink != 1
            or observed.st_size < 1
            or observed.st_size > _MAX_RECORD_BYTES
        ):
            _fail("CHUNKED_BASE_BACKUP_RECEIVER_LEDGER_RECORD_UNSAFE")
        raw = b""
        while len(raw) <= _MAX_RECORD_BYTES:
            part = os.read(fd, min(64 * 1024, _MAX_RECORD_BYTES + 1 - len(raw)))
            if not part:
                break
            raw += part
        if not raw or len(raw) > _MAX_RECORD_BYTES:
            _fail("CHUNKED_BASE_BACKUP_RECEIVER_LEDGER_RECORD_INVALID")
        try:
            payload = json.loads(
                raw.decode("ascii", "strict"),
                object_pairs_hook=_strict_object,
                parse_constant=_reject_json_constant,
            )
        except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
            _fail("CHUNKED_BASE_BACKUP_RECEIVER_LEDGER_RECORD_INVALID")
        if not isinstance(payload, dict) or _canonical(payload) != raw:
            _fail("CHUNKED_BASE_BACKUP_RECEIVER_LEDGER_RECORD_INVALID")
        return payload
    finally:
        os.close(fd)


def _index_name(value: str) -> str:
    return hashlib.sha256(value.encode("ascii")).hexdigest() + ".json"


def _ledger_key(receipt_id: str, receipt_nonce: str) -> str:
    return hashlib.sha256((receipt_id + "\x00" + receipt_nonce).encode("ascii")).hexdigest()


def _handoff_facts(
    *,
    manifest: object,
    handoff_receipt: object,
    now: datetime,
) -> tuple[VerifiedPhysicalWalChunkedBaseBackupManifest, VerifiedPhysicalWalChunkedBaseBackupHandoffReceipt]:
    try:
        verified_manifest = require_verified_physical_wal_chunked_base_backup_manifest(manifest, now=now)
        verified_handoff = require_verified_physical_wal_chunked_base_backup_handoff_receipt(
            handoff_receipt,
            manifest=verified_manifest,
            now=now,
        )
    except Exception:
        _fail("CHUNKED_BASE_BACKUP_RECEIVER_LEDGER_HANDOFF_INVALID")
    if (
        verified_handoff.manifest_id != verified_manifest.manifest_id
        or verified_handoff.manifest_sha256 != hashlib.sha256(verified_manifest.canonical_manifest).hexdigest()
        or verified_handoff.snapshot_sha256 != verified_manifest.total_plaintext_sha256
        or verified_handoff.snapshot_bytes != verified_manifest.total_plaintext_bytes
    ):
        _fail("CHUNKED_BASE_BACKUP_RECEIVER_LEDGER_HANDOFF_INVALID")
    return verified_manifest, verified_handoff


def _open_payload(*, claim: PhysicalWalChunkedBaseBackupReceiverReceiptLedgerClaim, now: datetime) -> dict[str, Any]:
    return {
        "schema": PHYSICAL_WAL_CHUNKED_BASE_BACKUP_RECEIVER_RECEIPT_LEDGER_SCHEMA,
        "kind": _OPEN_KIND,
        "status": _STATUS_OPEN,
        "receipt_id": claim.receipt_id,
        "receipt_nonce": claim.receipt_nonce,
        "manifest_id": claim.manifest_id,
        "manifest_sha256": claim.manifest_sha256,
        "binding_sha256": claim.binding_sha256,
        "session_sha256": claim.session_sha256,
        "finalization_permit_sha256": claim.finalization_permit_sha256,
        "snapshot_sha256": claim.snapshot_sha256,
        "snapshot_bytes": claim.snapshot_bytes,
        "ledger_key_sha256": claim.ledger_key_sha256,
        "claimed_at": now.isoformat(),
    }


def _claim_from_payload(value: object) -> PhysicalWalChunkedBaseBackupReceiverReceiptLedgerClaim:
    fields = {
        "schema",
        "kind",
        "status",
        "receipt_id",
        "receipt_nonce",
        "manifest_id",
        "manifest_sha256",
        "binding_sha256",
        "session_sha256",
        "finalization_permit_sha256",
        "snapshot_sha256",
        "snapshot_bytes",
        "ledger_key_sha256",
        "claimed_at",
    }
    if not isinstance(value, Mapping) or set(value) != fields:
        _fail("CHUNKED_BASE_BACKUP_RECEIVER_LEDGER_RECORD_INVALID")
    item = dict(value)
    if (
        item["schema"] != PHYSICAL_WAL_CHUNKED_BASE_BACKUP_RECEIVER_RECEIPT_LEDGER_SCHEMA
        or item["kind"] != _OPEN_KIND
        or item["status"] != _STATUS_OPEN
        or type(item["claimed_at"]) is not str
    ):
        _fail("CHUNKED_BASE_BACKUP_RECEIVER_LEDGER_RECORD_INVALID")
    try:
        claimed_at = datetime.fromisoformat(item["claimed_at"])
    except ValueError:
        _fail("CHUNKED_BASE_BACKUP_RECEIVER_LEDGER_RECORD_INVALID")
    if claimed_at.tzinfo is None or claimed_at.astimezone(timezone.utc).isoformat() != item["claimed_at"]:
        _fail("CHUNKED_BASE_BACKUP_RECEIVER_LEDGER_RECORD_INVALID")
    if type(item["snapshot_bytes"]) is not int or item["snapshot_bytes"] < 1:
        _fail("CHUNKED_BASE_BACKUP_RECEIVER_LEDGER_RECORD_INVALID")
    claim = PhysicalWalChunkedBaseBackupReceiverReceiptLedgerClaim(
        receipt_id=_id(item["receipt_id"], code="CHUNKED_BASE_BACKUP_RECEIVER_LEDGER_RECORD_INVALID"),
        receipt_nonce=_nonce(item["receipt_nonce"], code="CHUNKED_BASE_BACKUP_RECEIVER_LEDGER_RECORD_INVALID"),
        manifest_id=_id(item["manifest_id"], code="CHUNKED_BASE_BACKUP_RECEIVER_LEDGER_RECORD_INVALID"),
        manifest_sha256=_sha(item["manifest_sha256"], code="CHUNKED_BASE_BACKUP_RECEIVER_LEDGER_RECORD_INVALID"),
        binding_sha256=_sha(item["binding_sha256"], code="CHUNKED_BASE_BACKUP_RECEIVER_LEDGER_RECORD_INVALID"),
        session_sha256=_sha(item["session_sha256"], code="CHUNKED_BASE_BACKUP_RECEIVER_LEDGER_RECORD_INVALID"),
        finalization_permit_sha256=_sha(item["finalization_permit_sha256"], code="CHUNKED_BASE_BACKUP_RECEIVER_LEDGER_RECORD_INVALID"),
        snapshot_sha256=_sha(item["snapshot_sha256"], code="CHUNKED_BASE_BACKUP_RECEIVER_LEDGER_RECORD_INVALID"),
        snapshot_bytes=item["snapshot_bytes"],
        ledger_key_sha256=_sha(item["ledger_key_sha256"], code="CHUNKED_BASE_BACKUP_RECEIVER_LEDGER_RECORD_INVALID"),
    )
    if claim.ledger_key_sha256 != _ledger_key(claim.receipt_id, claim.receipt_nonce):
        _fail("CHUNKED_BASE_BACKUP_RECEIVER_LEDGER_RECORD_INVALID")
    object.__setattr__(claim, "_capability", _CAPABILITY)
    return claim


def _index_payload(*, claim: PhysicalWalChunkedBaseBackupReceiverReceiptLedgerClaim, axis: str) -> dict[str, Any]:
    return {
        "schema": PHYSICAL_WAL_CHUNKED_BASE_BACKUP_RECEIVER_RECEIPT_LEDGER_SCHEMA,
        "kind": _INDEX_KIND,
        "axis": axis,
        "receipt_id": claim.receipt_id,
        "receipt_nonce": claim.receipt_nonce,
        "ledger_key_sha256": claim.ledger_key_sha256,
        "manifest_sha256": claim.manifest_sha256,
        "binding_sha256": claim.binding_sha256,
    }


def _intent_payload(*, claim: PhysicalWalChunkedBaseBackupReceiverReceiptLedgerClaim, now: datetime) -> dict[str, Any]:
    """One fsync'd all-axis burn record written before either index exists."""

    return {
        "schema": PHYSICAL_WAL_CHUNKED_BASE_BACKUP_RECEIVER_RECEIPT_LEDGER_SCHEMA,
        "kind": "physical_wal_chunked_base_backup_receiver_handoff_intent",
        "receipt_id": claim.receipt_id,
        "receipt_nonce": claim.receipt_nonce,
        "ledger_key_sha256": claim.ledger_key_sha256,
        "manifest_sha256": claim.manifest_sha256,
        "binding_sha256": claim.binding_sha256,
        "intent_at": now.isoformat(),
    }


def _intent_claim(value: object) -> tuple[str, str, str]:
    fields = {
        "schema",
        "kind",
        "receipt_id",
        "receipt_nonce",
        "ledger_key_sha256",
        "manifest_sha256",
        "binding_sha256",
        "intent_at",
    }
    if not isinstance(value, Mapping) or set(value) != fields:
        _fail("CHUNKED_BASE_BACKUP_RECEIVER_LEDGER_JOURNAL_INVALID")
    item = dict(value)
    if (
        item["schema"] != PHYSICAL_WAL_CHUNKED_BASE_BACKUP_RECEIVER_RECEIPT_LEDGER_SCHEMA
        or item["kind"] != "physical_wal_chunked_base_backup_receiver_handoff_intent"
        or type(item["intent_at"]) is not str
    ):
        _fail("CHUNKED_BASE_BACKUP_RECEIVER_LEDGER_JOURNAL_INVALID")
    try:
        parsed = datetime.fromisoformat(item["intent_at"])
    except ValueError:
        _fail("CHUNKED_BASE_BACKUP_RECEIVER_LEDGER_JOURNAL_INVALID")
    if parsed.tzinfo is None or parsed.astimezone(timezone.utc).isoformat() != item["intent_at"]:
        _fail("CHUNKED_BASE_BACKUP_RECEIVER_LEDGER_JOURNAL_INVALID")
    receipt_id = _id(item["receipt_id"], code="CHUNKED_BASE_BACKUP_RECEIVER_LEDGER_JOURNAL_INVALID")
    receipt_nonce = _nonce(item["receipt_nonce"], code="CHUNKED_BASE_BACKUP_RECEIVER_LEDGER_JOURNAL_INVALID")
    key = _sha(item["ledger_key_sha256"], code="CHUNKED_BASE_BACKUP_RECEIVER_LEDGER_JOURNAL_INVALID")
    _sha(item["manifest_sha256"], code="CHUNKED_BASE_BACKUP_RECEIVER_LEDGER_JOURNAL_INVALID")
    _sha(item["binding_sha256"], code="CHUNKED_BASE_BACKUP_RECEIVER_LEDGER_JOURNAL_INVALID")
    if key != _ledger_key(receipt_id, receipt_nonce):
        _fail("CHUNKED_BASE_BACKUP_RECEIVER_LEDGER_JOURNAL_INVALID")
    return receipt_id, receipt_nonce, key


def _journal_rejects_axis(intent_fd: int, *, receipt_id: str, receipt_nonce: str) -> None:
    """Search only the local append-only journal while holding the ledger lock.

    This is not a remote/Object-Storage listing.  It closes the otherwise
    unavoidable crash interval between independently create-only ID and nonce
    indexes: the first fsync'd intent record burns both axes at once.
    """

    try:
        names = os.listdir(intent_fd)
    except OSError:
        _fail("CHUNKED_BASE_BACKUP_RECEIVER_LEDGER_JOURNAL_UNAVAILABLE")
    if len(names) > _MAX_INTENT_JOURNAL_RECORDS:
        _fail("CHUNKED_BASE_BACKUP_RECEIVER_LEDGER_JOURNAL_TOO_LARGE")
    for name in names:
        if _JOURNAL_NAME_RE.fullmatch(name) is None:
            _fail("CHUNKED_BASE_BACKUP_RECEIVER_LEDGER_JOURNAL_UNSAFE")
        # The intent journal is the durable replay fence.  Never treat a
        # malformed, partial, or path-unsafe entry as absent: doing so could
        # reopen a receipt axis after an interrupted claim.
        try:
            prior_id, prior_nonce, prior_key = _intent_claim(_read_json(intent_fd, name))
        except PhysicalWalChunkedBaseBackupReceiverReceiptLedgerError:
            _fail("CHUNKED_BASE_BACKUP_RECEIVER_LEDGER_JOURNAL_INVALID")
        if name != prior_key + ".json":
            _fail("CHUNKED_BASE_BACKUP_RECEIVER_LEDGER_JOURNAL_INVALID")
        if prior_id == receipt_id or prior_nonce == receipt_nonce:
            _fail("CHUNKED_BASE_BACKUP_RECEIVER_LEDGER_RECEIPT_REPLAYED")


def claim_root_owned_physical_wal_chunked_base_backup_receiver_handoff(
    config: PhysicalWalChunkedBaseBackupReceiverReceiptLedgerConfig,
    *,
    manifest: VerifiedPhysicalWalChunkedBaseBackupManifest,
    handoff_receipt: VerifiedPhysicalWalChunkedBaseBackupHandoffReceipt,
    now: datetime,
) -> PhysicalWalChunkedBaseBackupReceiverReceiptLedgerClaim:
    """Create OPEN and burn receipt ID+nonce before any remote-side effect."""

    facts = _facts(config, require_enabled=True)
    observed_now = _utc(now)
    verified_manifest, handoff = _handoff_facts(
        manifest=manifest,
        handoff_receipt=handoff_receipt,
        now=observed_now,
    )
    claim = PhysicalWalChunkedBaseBackupReceiverReceiptLedgerClaim(
        receipt_id=handoff.receipt_id,
        receipt_nonce=handoff.receipt_nonce,
        manifest_id=verified_manifest.manifest_id,
        manifest_sha256=hashlib.sha256(verified_manifest.canonical_manifest).hexdigest(),
        binding_sha256=handoff.binding_sha256,
        session_sha256=handoff.session_sha256,
        finalization_permit_sha256=handoff.finalization_permit_sha256,
        snapshot_sha256=handoff.snapshot_sha256,
        snapshot_bytes=handoff.snapshot_bytes,
        ledger_key_sha256=_ledger_key(handoff.receipt_id, handoff.receipt_nonce),
    )
    object.__setattr__(claim, "_capability", _CAPABILITY)
    root_fd = _open_secure_dir(facts.root, owner_uid=facts.owner_uid)
    lock_fd = -1
    id_fd = nonce_fd = open_fd = intent_fd = -1
    try:
        lock_fd = _lock(root_fd, owner_uid=facts.owner_uid)
        id_fd = _ensure_child_dir(root_fd, "receipt-id-index", owner_uid=facts.owner_uid)
        nonce_fd = _ensure_child_dir(root_fd, "receipt-nonce-index", owner_uid=facts.owner_uid)
        open_fd = _ensure_child_dir(root_fd, "open", owner_uid=facts.owner_uid)
        intent_fd = _ensure_child_dir(root_fd, "intent", owner_uid=facts.owner_uid)
        id_name = _index_name(claim.receipt_id)
        nonce_name = _index_name(claim.receipt_nonce)
        open_name = claim.ledger_key_sha256 + ".json"
        _journal_rejects_axis(intent_fd, receipt_id=claim.receipt_id, receipt_nonce=claim.receipt_nonce)
        for directory_fd, name in ((id_fd, id_name), (nonce_fd, nonce_name), (open_fd, open_name)):
            try:
                existing = _read_json(directory_fd, name)
            except PhysicalWalChunkedBaseBackupReceiverReceiptLedgerError as exc:
                if exc.code != "CHUNKED_BASE_BACKUP_RECEIVER_LEDGER_RECORD_MISSING":
                    raise
            else:
                # Parse an OPEN record fully where available; any existing
                # index, foreign collision, or interrupted create burns reuse.
                if directory_fd == open_fd:
                    _claim_from_payload(existing)
                _fail("CHUNKED_BASE_BACKUP_RECEIVER_LEDGER_RECEIPT_REPLAYED")
        # The journal is durable before either independent lookup index.  A
        # process dying below therefore burns both axes, not merely whichever
        # O_EXCL file happened to be created first.
        _create_json_exclusive(intent_fd, open_name, _intent_payload(claim=claim, now=observed_now))
        _create_json_exclusive(id_fd, id_name, _index_payload(claim=claim, axis="receipt_id"))
        _create_json_exclusive(nonce_fd, nonce_name, _index_payload(claim=claim, axis="receipt_nonce"))
        _create_json_exclusive(open_fd, open_name, _open_payload(claim=claim, now=observed_now))
        return claim
    finally:
        for fd in (intent_fd, open_fd, nonce_fd, id_fd, lock_fd, root_fd):
            if fd >= 0:
                try:
                    os.close(fd)
                except OSError:
                    pass


def _terminal(
    config: PhysicalWalChunkedBaseBackupReceiverReceiptLedgerConfig,
    *,
    claim: PhysicalWalChunkedBaseBackupReceiverReceiptLedgerClaim,
    status: str,
    now: datetime,
    stage_receipt_sha256: str | None = None,
    failure_code: str | None = None,
) -> None:
    facts = _facts(config, require_enabled=True)
    observed_now = _utc(now)
    if (
        type(claim) is not PhysicalWalChunkedBaseBackupReceiverReceiptLedgerClaim
        or claim._capability is not _CAPABILITY
        or claim.ledger_key_sha256 != _ledger_key(claim.receipt_id, claim.receipt_nonce)
    ):
        _fail("CHUNKED_BASE_BACKUP_RECEIVER_LEDGER_CLAIM_INVALID")
    if status == _STATUS_COMPLETED:
        receipt_hash = _sha(stage_receipt_sha256, code="CHUNKED_BASE_BACKUP_RECEIVER_LEDGER_TERMINAL_INVALID")
        failure = None
    elif status == _STATUS_FAILED:
        if type(failure_code) is not str or _FAILURE_RE.fullmatch(failure_code) is None:
            _fail("CHUNKED_BASE_BACKUP_RECEIVER_LEDGER_TERMINAL_INVALID")
        receipt_hash = None
        failure = failure_code
    else:  # pragma: no cover - private fixed callers.
        _fail("CHUNKED_BASE_BACKUP_RECEIVER_LEDGER_TERMINAL_INVALID")
    root_fd = _open_secure_dir(facts.root, owner_uid=facts.owner_uid)
    lock_fd = open_fd = terminal_fd = -1
    try:
        lock_fd = _lock(root_fd, owner_uid=facts.owner_uid)
        open_fd = _ensure_child_dir(root_fd, "open", owner_uid=facts.owner_uid)
        terminal_fd = _ensure_child_dir(root_fd, "terminal", owner_uid=facts.owner_uid)
        persisted = _claim_from_payload(_read_json(open_fd, claim.ledger_key_sha256 + ".json"))
        if persisted != claim:
            _fail("CHUNKED_BASE_BACKUP_RECEIVER_LEDGER_CLAIM_FOREIGN")
        payload: dict[str, Any] = {
            "schema": PHYSICAL_WAL_CHUNKED_BASE_BACKUP_RECEIVER_RECEIPT_LEDGER_SCHEMA,
            "kind": _TERMINAL_KIND,
            "status": status,
            "receipt_id": claim.receipt_id,
            "receipt_nonce": claim.receipt_nonce,
            "manifest_sha256": claim.manifest_sha256,
            "binding_sha256": claim.binding_sha256,
            "ledger_key_sha256": claim.ledger_key_sha256,
            "terminal_at": observed_now.isoformat(),
            "stage_receipt_sha256": receipt_hash,
            "failure_code": failure,
        }
        _create_json_exclusive(terminal_fd, claim.ledger_key_sha256 + ".json", payload)
    except PhysicalWalChunkedBaseBackupReceiverReceiptLedgerError as exc:
        if exc.code == "CHUNKED_BASE_BACKUP_RECEIVER_LEDGER_RECORD_EXISTS":
            _fail("CHUNKED_BASE_BACKUP_RECEIVER_LEDGER_CLAIM_ALREADY_TERMINAL")
        raise
    finally:
        for fd in (terminal_fd, open_fd, lock_fd, root_fd):
            if fd >= 0:
                try:
                    os.close(fd)
                except OSError:
                    pass


def complete_root_owned_physical_wal_chunked_base_backup_receiver_handoff(
    config: PhysicalWalChunkedBaseBackupReceiverReceiptLedgerConfig,
    *,
    claim: PhysicalWalChunkedBaseBackupReceiverReceiptLedgerClaim,
    stage_receipt_sha256: str,
    now: datetime,
) -> None:
    """Append the only successful terminal event for a previously OPEN claim."""

    _terminal(
        config,
        claim=claim,
        status=_STATUS_COMPLETED,
        stage_receipt_sha256=stage_receipt_sha256,
        now=now,
    )


def fail_root_owned_physical_wal_chunked_base_backup_receiver_handoff(
    config: PhysicalWalChunkedBaseBackupReceiverReceiptLedgerConfig,
    *,
    claim: PhysicalWalChunkedBaseBackupReceiverReceiptLedgerClaim,
    failure_code: str,
    now: datetime,
) -> None:
    """Append FAILED; it deliberately does not release a receipt for retry."""

    _terminal(
        config,
        claim=claim,
        status=_STATUS_FAILED,
        failure_code=failure_code,
        now=now,
    )
