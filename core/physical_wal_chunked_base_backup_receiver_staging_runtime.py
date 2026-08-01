"""Default-off, pull-only local staging for a v2 chunked physical base backup.

This module is intentionally a receiver boundary, not an S3/HTTP/age adapter.
It accepts only a verified v2 manifest and its verified, live Witness handoff;
uses one callback-scoped exact-version reader action per signed selector; and
asks an injected narrow age decryptor to write into root-owned O_EXCL files.
It never lists, chooses a mutable alias, contacts a peer, loads credentials,
uses a v1 path, deletes an object, restores PostgreSQL, or promotes a writer.

The receipt ledger is claimed before the first GET.  A failed stage leaves a
private orphan candidate but records FAILED and never returns a stage result.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import re
import stat
from typing import Any, Protocol, TypeVar

from core.append_only_sync_delta_batch import SHA256_RE, VERSION_ID_RE, canonical_json_bytes
from core.physical_wal_chunked_base_backup_handoff_receipt import (
    VerifiedPhysicalWalChunkedBaseBackupHandoffReceipt,
    require_verified_physical_wal_chunked_base_backup_handoff_receipt,
)
from core.physical_wal_chunked_base_backup_manifest import (
    PhysicalWalChunkedBaseBackupManifestChunkSelector,
    VerifiedPhysicalWalChunkedBaseBackupManifest,
    require_verified_physical_wal_chunked_base_backup_manifest,
)
from core.physical_wal_chunked_base_backup_receiver_receipt_ledger import (
    PhysicalWalChunkedBaseBackupReceiverReceiptLedgerClaim,
    PhysicalWalChunkedBaseBackupReceiverReceiptLedgerConfig,
    PhysicalWalChunkedBaseBackupReceiverReceiptLedgerError,
    claim_root_owned_physical_wal_chunked_base_backup_receiver_handoff,
    complete_root_owned_physical_wal_chunked_base_backup_receiver_handoff,
    fail_root_owned_physical_wal_chunked_base_backup_receiver_handoff,
    validate_root_owned_physical_wal_chunked_base_backup_receiver_receipt_ledger_config,
)
from core.physical_wal_chunked_base_backup_transfer import (
    MAX_PHYSICAL_WAL_CHUNKED_BASE_BACKUP_CHUNK_CIPHERTEXT_BYTES,
    MAX_PHYSICAL_WAL_CHUNKED_BASE_BACKUP_CHUNKS,
)


__all__ = (
    "PHYSICAL_WAL_CHUNKED_BASE_BACKUP_RECEIVER_STAGING_DEFAULT_ENABLED",
    "PHYSICAL_WAL_CHUNKED_BASE_BACKUP_RECEIVER_STAGING_SCHEMA",
    "PhysicalWalChunkedBaseBackupAgeDecryptionObservation",
    "PhysicalWalChunkedBaseBackupChunkAgeDecryptor",
    "PhysicalWalChunkedBaseBackupExactVersionGetObservation",
    "PhysicalWalChunkedBaseBackupExactVersionHeadObservation",
    "PhysicalWalChunkedBaseBackupExactVersionReceiver",
    "PhysicalWalChunkedBaseBackupExactVersionReceiverAction",
    "PhysicalWalChunkedBaseBackupReceiverStagingError",
    "PhysicalWalChunkedBaseBackupReceiverStagingResult",
    "RootOwnedPhysicalWalChunkedBaseBackupReceiverStagingConfig",
    "execute_root_owned_physical_wal_chunked_base_backup_receiver_staging",
    "validate_root_owned_physical_wal_chunked_base_backup_receiver_staging_config",
)


PHYSICAL_WAL_CHUNKED_BASE_BACKUP_RECEIVER_STAGING_SCHEMA = (
    "gold-trade-physical-wal-chunked-base-backup-receiver-staging-v2"
)
PHYSICAL_WAL_CHUNKED_BASE_BACKUP_RECEIVER_STAGING_DEFAULT_ENABLED = False
PHYSICAL_WAL_CHUNKED_BASE_BACKUP_RECEIVER_STAGING_STATUS = "staged-not-restored-or-promoted"
MAX_PHYSICAL_WAL_CHUNKED_BASE_BACKUP_RECEIVER_STAGE_BYTES = 2 * 1024 * 1024 * 1024 * 1024
MAX_PHYSICAL_WAL_CHUNKED_BASE_BACKUP_RECEIVER_IO_BYTES = 1024 * 1024
MAX_PHYSICAL_WAL_CHUNKED_BASE_BACKUP_RECEIVER_STAGE_RECEIPT_BYTES = 128 * 1024 * 1024
_FILE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,255}$", re.ASCII)
_SITE_RE = re.compile(r"^webapp_(?:fi|ir)$", re.ASCII)
_URL_OR_SECRET_RE = re.compile(
    r"(?i)(?:[a-z][a-z0-9+.-]*://|www\.|bearer\s+|access[_ -]?key|"
    r"authorization|credential|password|private[_ -]?key|secret|token)"
)
_MUTABLE_VERSION_IDS = frozenset({"null", "none", "latest", "current", "head"})
_T = TypeVar("_T")


class PhysicalWalChunkedBaseBackupReceiverStagingError(RuntimeError):
    """A receiver-side v2 staging operation is unsafe or incomplete."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class RootOwnedPhysicalWalChunkedBaseBackupReceiverStagingConfig:
    """No-secret policy for one fixed receiver and private local stage root."""

    schema: str = PHYSICAL_WAL_CHUNKED_BASE_BACKUP_RECEIVER_STAGING_SCHEMA
    staging_root: Path | None = None
    receipt_ledger_config: PhysicalWalChunkedBaseBackupReceiverReceiptLedgerConfig | None = None
    receiver_site: str = ""
    owner_uid: int = 0
    enabled: bool = PHYSICAL_WAL_CHUNKED_BASE_BACKUP_RECEIVER_STAGING_DEFAULT_ENABLED
    maximum_total_plaintext_bytes: int = MAX_PHYSICAL_WAL_CHUNKED_BASE_BACKUP_RECEIVER_STAGE_BYTES
    direct_site_control: str = "forbidden"
    destination_object_ingest: str = "pull-only"
    v1_fallback: str = "forbidden"
    generic_object_listing: str = "forbidden"
    object_deletion: str = "forbidden"


@dataclass(frozen=True)
class PhysicalWalChunkedBaseBackupExactVersionHeadObservation:
    """Safe exact selector metadata; never a raw SDK/client response."""

    object_key: str
    version_id: str
    ciphertext_sha256: str
    ciphertext_bytes: int


@dataclass(frozen=True)
class PhysicalWalChunkedBaseBackupExactVersionGetObservation:
    """Safe exact GET result for bytes written to the supplied FD."""

    object_key: str
    version_id: str
    ciphertext_sha256: str
    ciphertext_bytes: int


@dataclass(frozen=True)
class PhysicalWalChunkedBaseBackupAgeDecryptionObservation:
    """Safe age-decryption readback for bytes written to the supplied FD."""

    object_key: str
    version_id: str
    age_recipient: str
    plaintext_sha256: str
    plaintext_bytes: int


class PhysicalWalChunkedBaseBackupExactVersionReceiverAction(Protocol):
    """Callback-only receiver action restricted to one exact chunk selector."""

    def head_exact_object_version(
        self,
        *,
        object_key: str,
        version_id: str,
    ) -> PhysicalWalChunkedBaseBackupExactVersionHeadObservation: ...

    def get_exact_object_version_to_fd(
        self,
        *,
        object_key: str,
        version_id: str,
        destination_fd: int,
    ) -> PhysicalWalChunkedBaseBackupExactVersionGetObservation: ...


class PhysicalWalChunkedBaseBackupExactVersionReceiver(Protocol):
    """Role facade that never exposes credentials, generic keys, or a client."""

    def with_exact_chunk_receiver(
        self,
        *,
        selector: PhysicalWalChunkedBaseBackupManifestChunkSelector,
        callback: Callable[[PhysicalWalChunkedBaseBackupExactVersionReceiverAction], _T],
    ) -> _T: ...


class PhysicalWalChunkedBaseBackupChunkAgeDecryptor(Protocol):
    """Narrow decryptor capability; it receives FDs and exact manifest pins only."""

    def decrypt_exact_chunk_to_fd(
        self,
        *,
        ciphertext_fd: int,
        plaintext_fd: int,
        object_key: str,
        version_id: str,
        expected_age_recipient: str,
    ) -> PhysicalWalChunkedBaseBackupAgeDecryptionObservation: ...


@dataclass(frozen=True)
class PhysicalWalChunkedBaseBackupReceiverStagingResult:
    """Non-authorizing local result; never restore, replay, promotion, or writer proof."""

    status: str
    stage_directory: Path
    stage_receipt_path: Path
    stage_receipt_sha256: str
    receipt_id: str
    receipt_nonce: str
    manifest_sha256: str
    binding_sha256: str
    total_plaintext_sha256: str
    total_plaintext_bytes: int
    chunk_count: int


@dataclass(frozen=True)
class _ConfigFacts:
    staging_root: Path
    ledger_config: PhysicalWalChunkedBaseBackupReceiverReceiptLedgerConfig
    receiver_site: str
    owner_uid: int
    maximum_total_bytes: int


@dataclass(frozen=True)
class _ExactReadback:
    head: PhysicalWalChunkedBaseBackupExactVersionHeadObservation
    get: PhysicalWalChunkedBaseBackupExactVersionGetObservation


def _fail(code: str) -> None:
    raise PhysicalWalChunkedBaseBackupReceiverStagingError(code)


def _safe_root(value: object) -> Path:
    if not isinstance(value, Path) or not value.is_absolute() or any(part in {".", ".."} for part in value.parts):
        _fail("CHUNKED_BASE_BACKUP_RECEIVER_STAGE_ROOT_INVALID")
    text = str(value)
    if not text or len(text) > 4096 or _URL_OR_SECRET_RE.search(text) is not None:
        _fail("CHUNKED_BASE_BACKUP_RECEIVER_STAGE_ROOT_INVALID")
    return value


def _config_facts(config: object, *, require_enabled: bool) -> _ConfigFacts:
    if type(config) is not RootOwnedPhysicalWalChunkedBaseBackupReceiverStagingConfig:
        _fail("CHUNKED_BASE_BACKUP_RECEIVER_STAGE_CONFIG_INVALID")
    if (
        config.schema != PHYSICAL_WAL_CHUNKED_BASE_BACKUP_RECEIVER_STAGING_SCHEMA
        or type(config.enabled) is not bool
        or (require_enabled and config.enabled is not True)
        or type(config.owner_uid) is not int
        or config.owner_uid != 0
        or type(config.receiver_site) is not str
        or _SITE_RE.fullmatch(config.receiver_site) is None
        or config.direct_site_control != "forbidden"
        or config.destination_object_ingest != "pull-only"
        or config.v1_fallback != "forbidden"
        or config.generic_object_listing != "forbidden"
        or config.object_deletion != "forbidden"
        or type(config.maximum_total_plaintext_bytes) is not int
        or not 1 <= config.maximum_total_plaintext_bytes <= MAX_PHYSICAL_WAL_CHUNKED_BASE_BACKUP_RECEIVER_STAGE_BYTES
    ):
        _fail("CHUNKED_BASE_BACKUP_RECEIVER_STAGE_CONFIG_INVALID")
    root = _safe_root(config.staging_root)
    if type(config.receipt_ledger_config) is not PhysicalWalChunkedBaseBackupReceiverReceiptLedgerConfig:
        _fail("CHUNKED_BASE_BACKUP_RECEIVER_STAGE_LEDGER_CONFIG_INVALID")
    try:
        validate_root_owned_physical_wal_chunked_base_backup_receiver_receipt_ledger_config(
            config.receipt_ledger_config,
            require_enabled=True,
        )
    except PhysicalWalChunkedBaseBackupReceiverReceiptLedgerError:
        _fail("CHUNKED_BASE_BACKUP_RECEIVER_STAGE_LEDGER_CONFIG_INVALID")
    if config.receipt_ledger_config.ledger_root == root:
        _fail("CHUNKED_BASE_BACKUP_RECEIVER_STAGE_LEDGER_ROOT_COLLIDES")
    return _ConfigFacts(
        staging_root=root,
        ledger_config=config.receipt_ledger_config,
        receiver_site=config.receiver_site,
        owner_uid=0,
        maximum_total_bytes=config.maximum_total_plaintext_bytes,
    )


def validate_root_owned_physical_wal_chunked_base_backup_receiver_staging_config(
    config: object,
    *,
    require_enabled: bool = True,
) -> None:
    """Validate static no-secret policy without touching a stage path."""

    _config_facts(config, require_enabled=require_enabled)


def _clock(clock: Callable[[], datetime]) -> datetime:
    if not callable(clock):
        _fail("CHUNKED_BASE_BACKUP_RECEIVER_STAGE_CLOCK_INVALID")
    try:
        value = clock()
    except Exception:
        _fail("CHUNKED_BASE_BACKUP_RECEIVER_STAGE_CLOCK_INVALID")
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        _fail("CHUNKED_BASE_BACKUP_RECEIVER_STAGE_CLOCK_INVALID")
    return value.astimezone(timezone.utc)


def _open_secure_root(path: Path, *, owner_uid: int) -> int:
    if os.geteuid() != 0:
        _fail("CHUNKED_BASE_BACKUP_RECEIVER_STAGE_ROOT_REQUIRED")
    try:
        listed = os.lstat(path)
    except OSError:
        _fail("CHUNKED_BASE_BACKUP_RECEIVER_STAGE_ROOT_UNAVAILABLE")
    if (
        stat.S_ISLNK(listed.st_mode)
        or not stat.S_ISDIR(listed.st_mode)
        or listed.st_uid != owner_uid
        or stat.S_IMODE(listed.st_mode) != 0o700
    ):
        _fail("CHUNKED_BASE_BACKUP_RECEIVER_STAGE_ROOT_UNSAFE")
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
        observed = os.fstat(fd)
    except OSError:
        _fail("CHUNKED_BASE_BACKUP_RECEIVER_STAGE_ROOT_UNSAFE")
    if (
        observed.st_dev != listed.st_dev
        or observed.st_ino != listed.st_ino
        or not stat.S_ISDIR(observed.st_mode)
        or observed.st_uid != owner_uid
        or stat.S_IMODE(observed.st_mode) != 0o700
    ):
        os.close(fd)
        _fail("CHUNKED_BASE_BACKUP_RECEIVER_STAGE_ROOT_RACED")
    return fd


def _mkdir_stage(root_fd: int, *, name: str, owner_uid: int) -> tuple[int, str]:
    if _FILE_RE.fullmatch(name) is None:
        _fail("CHUNKED_BASE_BACKUP_RECEIVER_STAGE_DIRECTORY_INVALID")
    try:
        os.mkdir(name, 0o700, dir_fd=root_fd)
        os.fsync(root_fd)
    except FileExistsError:
        _fail("CHUNKED_BASE_BACKUP_RECEIVER_STAGE_DIRECTORY_EXISTS")
    except OSError:
        _fail("CHUNKED_BASE_BACKUP_RECEIVER_STAGE_DIRECTORY_CREATE_FAILED")
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(name, flags, dir_fd=root_fd)
        observed = os.fstat(fd)
    except OSError:
        _fail("CHUNKED_BASE_BACKUP_RECEIVER_STAGE_DIRECTORY_UNSAFE")
    if (
        not stat.S_ISDIR(observed.st_mode)
        or observed.st_uid != owner_uid
        or stat.S_IMODE(observed.st_mode) != 0o700
    ):
        os.close(fd)
        _fail("CHUNKED_BASE_BACKUP_RECEIVER_STAGE_DIRECTORY_UNSAFE")
    return fd, name


def _open_exclusive_file(directory_fd: int, *, name: str) -> int:
    if _FILE_RE.fullmatch(name) is None:
        _fail("CHUNKED_BASE_BACKUP_RECEIVER_STAGE_FILE_INVALID")
    flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(name, flags, 0o600, dir_fd=directory_fd)
        observed = os.fstat(fd)
    except FileExistsError:
        _fail("CHUNKED_BASE_BACKUP_RECEIVER_STAGE_FILE_EXISTS")
    except OSError:
        _fail("CHUNKED_BASE_BACKUP_RECEIVER_STAGE_FILE_CREATE_FAILED")
    if (
        not stat.S_ISREG(observed.st_mode)
        or observed.st_uid != 0
        or stat.S_IMODE(observed.st_mode) != 0o600
        or observed.st_nlink != 1
    ):
        os.close(fd)
        _fail("CHUNKED_BASE_BACKUP_RECEIVER_STAGE_FILE_UNSAFE")
    return fd


def _sha256_fd(fd: int, *, maximum: int) -> tuple[str, int]:
    try:
        os.fsync(fd)
        before = os.fstat(fd)
        os.lseek(fd, 0, os.SEEK_SET)
    except OSError:
        _fail("CHUNKED_BASE_BACKUP_RECEIVER_STAGE_FILE_READBACK_FAILED")
    if not stat.S_ISREG(before.st_mode) or before.st_size < 1 or before.st_size > maximum:
        _fail("CHUNKED_BASE_BACKUP_RECEIVER_STAGE_FILE_READBACK_INVALID")
    digest = hashlib.sha256()
    total = 0
    while True:
        try:
            data = os.read(fd, MAX_PHYSICAL_WAL_CHUNKED_BASE_BACKUP_RECEIVER_IO_BYTES)
        except OSError:
            _fail("CHUNKED_BASE_BACKUP_RECEIVER_STAGE_FILE_READBACK_FAILED")
        if not data:
            break
        total += len(data)
        if total > maximum:
            _fail("CHUNKED_BASE_BACKUP_RECEIVER_STAGE_FILE_READBACK_INVALID")
        digest.update(data)
    try:
        after = os.fstat(fd)
    except OSError:
        _fail("CHUNKED_BASE_BACKUP_RECEIVER_STAGE_FILE_READBACK_FAILED")
    if (
        before.st_dev != after.st_dev
        or before.st_ino != after.st_ino
        or before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
        or before.st_ctime_ns != after.st_ctime_ns
        or total != before.st_size
    ):
        _fail("CHUNKED_BASE_BACKUP_RECEIVER_STAGE_FILE_RACED")
    return digest.hexdigest(), total


def _version(value: object) -> str:
    if type(value) is not str or VERSION_ID_RE.fullmatch(value) is None or value.casefold() in _MUTABLE_VERSION_IDS:
        _fail("CHUNKED_BASE_BACKUP_RECEIVER_STAGE_VERSION_INVALID")
    return value


def _validate_cipher_observation(
    value: object,
    *,
    selector: PhysicalWalChunkedBaseBackupManifestChunkSelector,
    kind: type[PhysicalWalChunkedBaseBackupExactVersionHeadObservation]
    | type[PhysicalWalChunkedBaseBackupExactVersionGetObservation],
    code: str,
) -> None:
    if type(value) is not kind:
        _fail(code)
    if (
        value.object_key != selector.object_key
        or _version(value.version_id) != selector.version_id
        or value.ciphertext_sha256 != selector.ciphertext_sha256
        or value.ciphertext_bytes != selector.ciphertext_bytes
    ):
        _fail("CHUNKED_BASE_BACKUP_RECEIVER_STAGE_EXACT_READBACK_MISMATCH")


def _read_exact_chunk(
    *,
    receiver: PhysicalWalChunkedBaseBackupExactVersionReceiver,
    selector: PhysicalWalChunkedBaseBackupManifestChunkSelector,
    destination_fd: int,
) -> _ExactReadback:
    def callback(action: PhysicalWalChunkedBaseBackupExactVersionReceiverAction) -> _ExactReadback:
        if action is None:
            _fail("CHUNKED_BASE_BACKUP_RECEIVER_STAGE_CALLBACK_LEAK")
        head = action.head_exact_object_version(
            object_key=selector.object_key,
            version_id=selector.version_id,
        )
        _validate_cipher_observation(
            head,
            selector=selector,
            kind=PhysicalWalChunkedBaseBackupExactVersionHeadObservation,
            code="CHUNKED_BASE_BACKUP_RECEIVER_STAGE_HEAD_INVALID",
        )
        got = action.get_exact_object_version_to_fd(
            object_key=selector.object_key,
            version_id=selector.version_id,
            destination_fd=destination_fd,
        )
        _validate_cipher_observation(
            got,
            selector=selector,
            kind=PhysicalWalChunkedBaseBackupExactVersionGetObservation,
            code="CHUNKED_BASE_BACKUP_RECEIVER_STAGE_GET_INVALID",
        )
        return _ExactReadback(head=head, get=got)

    if receiver is None or not callable(getattr(receiver, "with_exact_chunk_receiver", None)):
        _fail("CHUNKED_BASE_BACKUP_RECEIVER_STAGE_RECEIVER_INVALID")
    try:
        result = receiver.with_exact_chunk_receiver(selector=selector, callback=callback)
    except PhysicalWalChunkedBaseBackupReceiverStagingError:
        raise
    except Exception:
        _fail("CHUNKED_BASE_BACKUP_RECEIVER_STAGE_OBJECT_SIDE_EFFECT_FAILED")
    if type(result) is not _ExactReadback:
        _fail("CHUNKED_BASE_BACKUP_RECEIVER_STAGE_CALLBACK_LEAK")
    return result


def _validate_decryption(
    value: object,
    *,
    selector: PhysicalWalChunkedBaseBackupManifestChunkSelector,
) -> None:
    if type(value) is not PhysicalWalChunkedBaseBackupAgeDecryptionObservation:
        _fail("CHUNKED_BASE_BACKUP_RECEIVER_STAGE_DECRYPTION_INVALID")
    if (
        value.object_key != selector.object_key
        or _version(value.version_id) != selector.version_id
        or value.age_recipient != selector.age_recipient
        or value.plaintext_sha256 != selector.plaintext_sha256
        or value.plaintext_bytes != selector.plaintext_bytes
    ):
        _fail("CHUNKED_BASE_BACKUP_RECEIVER_STAGE_DECRYPTION_MISMATCH")


def _manifest_and_handoff(
    *,
    facts: _ConfigFacts,
    manifest: object,
    handoff_receipt: object,
    now: datetime,
) -> tuple[VerifiedPhysicalWalChunkedBaseBackupManifest, VerifiedPhysicalWalChunkedBaseBackupHandoffReceipt]:
    try:
        verified_manifest = require_verified_physical_wal_chunked_base_backup_manifest(manifest, now=now)
        handoff = require_verified_physical_wal_chunked_base_backup_handoff_receipt(
            handoff_receipt,
            manifest=verified_manifest,
            now=now,
        )
    except Exception:
        _fail("CHUNKED_BASE_BACKUP_RECEIVER_STAGE_HANDOFF_INVALID")
    binding = verified_manifest.finalization_permit.session.binding
    manifest_hash = hashlib.sha256(verified_manifest.canonical_manifest).hexdigest()
    if (
        binding.destination_site != facts.receiver_site
        or binding.source_site == binding.destination_site
        or binding.writer_term.writer_holder_site != binding.source_site
        or handoff.manifest_id != verified_manifest.manifest_id
        or handoff.manifest_sha256 != manifest_hash
        or handoff.snapshot_sha256 != verified_manifest.total_plaintext_sha256
        or handoff.snapshot_bytes != verified_manifest.total_plaintext_bytes
        or handoff.destination_age_recipient != binding.destination_age_recipient
        or verified_manifest.total_plaintext_bytes > facts.maximum_total_bytes
        or not verified_manifest.chunks
        or len(verified_manifest.chunks) > MAX_PHYSICAL_WAL_CHUNKED_BASE_BACKUP_CHUNKS
    ):
        _fail("CHUNKED_BASE_BACKUP_RECEIVER_STAGE_HANDOFF_MISMATCH")
    return verified_manifest, handoff


def _write_stage_receipt(
    *,
    stage_fd: int,
    manifest: VerifiedPhysicalWalChunkedBaseBackupManifest,
    handoff: VerifiedPhysicalWalChunkedBaseBackupHandoffReceipt,
    claim: PhysicalWalChunkedBaseBackupReceiverReceiptLedgerClaim,
    total_sha256: str,
    total_bytes: int,
) -> tuple[str, bytes]:
    payload = {
        "schema": PHYSICAL_WAL_CHUNKED_BASE_BACKUP_RECEIVER_STAGING_SCHEMA,
        "status": PHYSICAL_WAL_CHUNKED_BASE_BACKUP_RECEIVER_STAGING_STATUS,
        "receipt_id": handoff.receipt_id,
        "receipt_nonce": handoff.receipt_nonce,
        "manifest_id": manifest.manifest_id,
        "manifest_sha256": hashlib.sha256(manifest.canonical_manifest).hexdigest(),
        "binding_sha256": handoff.binding_sha256,
        "session_sha256": handoff.session_sha256,
        "finalization_permit_sha256": handoff.finalization_permit_sha256,
        "lineage_sha256": handoff.lineage_sha256,
        "snapshot_sha256": handoff.snapshot_sha256,
        "snapshot_bytes": handoff.snapshot_bytes,
        "total_plaintext_sha256": total_sha256,
        "total_plaintext_bytes": total_bytes,
        "chunk_count": len(manifest.chunks),
        "ledger_key_sha256": claim.ledger_key_sha256,
        "chunks": [
            {
                "index": selector.index,
                "object_key": selector.object_key,
                "version_id": selector.version_id,
                "ciphertext_sha256": selector.ciphertext_sha256,
                "ciphertext_bytes": selector.ciphertext_bytes,
                "plaintext_sha256": selector.plaintext_sha256,
                "plaintext_bytes": selector.plaintext_bytes,
            }
            for selector in manifest.chunks
        ],
    }
    try:
        raw = canonical_json_bytes(payload)
    except (TypeError, ValueError):
        _fail("CHUNKED_BASE_BACKUP_RECEIVER_STAGE_RECEIPT_INVALID")
    if not raw or len(raw) > MAX_PHYSICAL_WAL_CHUNKED_BASE_BACKUP_RECEIVER_STAGE_RECEIPT_BYTES:
        _fail("CHUNKED_BASE_BACKUP_RECEIVER_STAGE_RECEIPT_INVALID")
    fd = _open_exclusive_file(stage_fd, name="stage-receipt.json")
    try:
        offset = 0
        while offset < len(raw):
            written = os.write(fd, raw[offset:])
            if written <= 0:
                _fail("CHUNKED_BASE_BACKUP_RECEIVER_STAGE_RECEIPT_WRITE_FAILED")
            offset += written
        os.fsync(fd)
    except PhysicalWalChunkedBaseBackupReceiverStagingError:
        raise
    except OSError:
        _fail("CHUNKED_BASE_BACKUP_RECEIVER_STAGE_RECEIPT_WRITE_FAILED")
    finally:
        os.close(fd)
    try:
        os.fsync(stage_fd)
    except OSError:
        _fail("CHUNKED_BASE_BACKUP_RECEIVER_STAGE_DIRECTORY_SYNC_FAILED")
    return hashlib.sha256(raw).hexdigest(), raw


def _mark_failed_quietly(
    *,
    ledger_config: PhysicalWalChunkedBaseBackupReceiverReceiptLedgerConfig,
    claim: PhysicalWalChunkedBaseBackupReceiverReceiptLedgerClaim,
    failure_code: str,
    clock: Callable[[], datetime],
) -> None:
    try:
        fail_root_owned_physical_wal_chunked_base_backup_receiver_handoff(
            ledger_config,
            claim=claim,
            failure_code=failure_code,
            now=_clock(clock),
        )
    except Exception:
        # The original operation still never succeeds.  The caller receives a
        # distinct error because a claimed receipt lacks the required FAILED
        # durable terminal evidence.
        _fail("CHUNKED_BASE_BACKUP_RECEIVER_STAGE_FAILURE_LEDGER_MARK_FAILED")


def execute_root_owned_physical_wal_chunked_base_backup_receiver_staging(
    config: RootOwnedPhysicalWalChunkedBaseBackupReceiverStagingConfig,
    *,
    manifest: VerifiedPhysicalWalChunkedBaseBackupManifest,
    handoff_receipt: VerifiedPhysicalWalChunkedBaseBackupHandoffReceipt,
    exact_version_receiver: PhysicalWalChunkedBaseBackupExactVersionReceiver,
    age_decryptor: PhysicalWalChunkedBaseBackupChunkAgeDecryptor,
    clock: Callable[[], datetime],
) -> PhysicalWalChunkedBaseBackupReceiverStagingResult:
    """Claim once, stage every exact immutable chunk, then append COMPLETED.

    No exception path after claim yields a result.  Its handoff is terminally
    FAILED (or the operation reports that it could not durably mark failure).
    """

    facts = _config_facts(config, require_enabled=True)
    now = _clock(clock)
    verified_manifest, handoff = _manifest_and_handoff(
        facts=facts,
        manifest=manifest,
        handoff_receipt=handoff_receipt,
        now=now,
    )
    if (
        exact_version_receiver is None
        or age_decryptor is None
        or not callable(getattr(age_decryptor, "decrypt_exact_chunk_to_fd", None))
    ):
        _fail("CHUNKED_BASE_BACKUP_RECEIVER_STAGE_DEPENDENCY_INVALID")
    try:
        claim = claim_root_owned_physical_wal_chunked_base_backup_receiver_handoff(
            facts.ledger_config,
            manifest=verified_manifest,
            handoff_receipt=handoff,
            now=now,
        )
    except PhysicalWalChunkedBaseBackupReceiverReceiptLedgerError as exc:
        _fail(exc.code)

    root_fd = stage_fd = -1
    try:
        root_fd = _open_secure_root(facts.staging_root, owner_uid=facts.owner_uid)
        # This deterministic non-overwriting name is only reachable after the
        # receipt is burned in the ledger.  Existing data is never reused.
        stage_name = "stage-" + claim.ledger_key_sha256[:48]
        stage_fd, _ = _mkdir_stage(root_fd, name=stage_name, owner_uid=facts.owner_uid)
        for selector in verified_manifest.chunks:
            cipher_fd = plain_fd = -1
            try:
                cipher_fd = _open_exclusive_file(stage_fd, name=f"ciphertext-{selector.index:08d}.age")
                _read_exact_chunk(
                    receiver=exact_version_receiver,
                    selector=selector,
                    destination_fd=cipher_fd,
                )
                ciphertext_hash, ciphertext_bytes = _sha256_fd(
                    cipher_fd,
                    maximum=MAX_PHYSICAL_WAL_CHUNKED_BASE_BACKUP_CHUNK_CIPHERTEXT_BYTES,
                )
                if (
                    ciphertext_hash != selector.ciphertext_sha256
                    or ciphertext_bytes != selector.ciphertext_bytes
                ):
                    _fail("CHUNKED_BASE_BACKUP_RECEIVER_STAGE_CIPHERTEXT_READBACK_MISMATCH")
                plain_fd = _open_exclusive_file(stage_fd, name=f"plaintext-{selector.index:08d}.bin")
                try:
                    os.lseek(cipher_fd, 0, os.SEEK_SET)
                    observation = age_decryptor.decrypt_exact_chunk_to_fd(
                        ciphertext_fd=cipher_fd,
                        plaintext_fd=plain_fd,
                        object_key=selector.object_key,
                        version_id=selector.version_id,
                        expected_age_recipient=selector.age_recipient,
                    )
                except PhysicalWalChunkedBaseBackupReceiverStagingError:
                    raise
                except Exception:
                    _fail("CHUNKED_BASE_BACKUP_RECEIVER_STAGE_DECRYPTION_FAILED")
                _validate_decryption(observation, selector=selector)
                plaintext_hash, plaintext_bytes = _sha256_fd(
                    plain_fd,
                    maximum=selector.plaintext_bytes,
                )
                if plaintext_hash != selector.plaintext_sha256 or plaintext_bytes != selector.plaintext_bytes:
                    _fail("CHUNKED_BASE_BACKUP_RECEIVER_STAGE_PLAINTEXT_READBACK_MISMATCH")
            finally:
                for fd in (plain_fd, cipher_fd):
                    if fd >= 0:
                        try:
                            os.close(fd)
                        except OSError:
                            pass
        # Recompute the total from staged plaintext files in canonical selector
        # order.  This avoids accepting a decryptor's reported total or any
        # per-chunk metadata as a substitute for actual staged bytes.
        total_digest = hashlib.sha256()
        total_bytes = 0
        for selector in verified_manifest.chunks:
            flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
            try:
                plain_fd = os.open(f"plaintext-{selector.index:08d}.bin", flags, dir_fd=stage_fd)
            except OSError:
                _fail("CHUNKED_BASE_BACKUP_RECEIVER_STAGE_FILE_READBACK_FAILED")
            try:
                before = os.fstat(plain_fd)
                if not stat.S_ISREG(before.st_mode) or before.st_uid != facts.owner_uid or stat.S_IMODE(before.st_mode) != 0o600:
                    _fail("CHUNKED_BASE_BACKUP_RECEIVER_STAGE_FILE_UNSAFE")
                if before.st_nlink != 1:
                    _fail("CHUNKED_BASE_BACKUP_RECEIVER_STAGE_FILE_UNSAFE")
                while True:
                    data = os.read(plain_fd, MAX_PHYSICAL_WAL_CHUNKED_BASE_BACKUP_RECEIVER_IO_BYTES)
                    if not data:
                        break
                    total_digest.update(data)
                    total_bytes += len(data)
                    if total_bytes > facts.maximum_total_bytes:
                        _fail("CHUNKED_BASE_BACKUP_RECEIVER_STAGE_TOTAL_TOO_LARGE")
                after = os.fstat(plain_fd)
                if (
                    before.st_dev != after.st_dev
                    or before.st_ino != after.st_ino
                    or before.st_size != after.st_size
                    or before.st_mtime_ns != after.st_mtime_ns
                    or before.st_ctime_ns != after.st_ctime_ns
                ):
                    _fail("CHUNKED_BASE_BACKUP_RECEIVER_STAGE_FILE_RACED")
            finally:
                os.close(plain_fd)
        total_hash = total_digest.hexdigest()
        if (
            total_hash != verified_manifest.total_plaintext_sha256
            or total_bytes != verified_manifest.total_plaintext_bytes
            or total_hash != handoff.snapshot_sha256
            or total_bytes != handoff.snapshot_bytes
        ):
            _fail("CHUNKED_BASE_BACKUP_RECEIVER_STAGE_TOTAL_MISMATCH")
        # A receipt/permit that ages out while a slow local pull is running is
        # not accepted as a successful stage.  Recheck it immediately before
        # creating local success evidence and terminal completion.
        verified_manifest, handoff = _manifest_and_handoff(
            facts=facts,
            manifest=verified_manifest,
            handoff_receipt=handoff,
            now=_clock(clock),
        )
        stage_receipt_sha256, _raw = _write_stage_receipt(
            stage_fd=stage_fd,
            manifest=verified_manifest,
            handoff=handoff,
            claim=claim,
            total_sha256=total_hash,
            total_bytes=total_bytes,
        )
        try:
            complete_root_owned_physical_wal_chunked_base_backup_receiver_handoff(
                facts.ledger_config,
                claim=claim,
                stage_receipt_sha256=stage_receipt_sha256,
                now=_clock(clock),
            )
        except PhysicalWalChunkedBaseBackupReceiverReceiptLedgerError as exc:
            _fail(exc.code)
        return PhysicalWalChunkedBaseBackupReceiverStagingResult(
            status=PHYSICAL_WAL_CHUNKED_BASE_BACKUP_RECEIVER_STAGING_STATUS,
            stage_directory=facts.staging_root / stage_name,
            stage_receipt_path=facts.staging_root / stage_name / "stage-receipt.json",
            stage_receipt_sha256=stage_receipt_sha256,
            receipt_id=handoff.receipt_id,
            receipt_nonce=handoff.receipt_nonce,
            manifest_sha256=hashlib.sha256(verified_manifest.canonical_manifest).hexdigest(),
            binding_sha256=handoff.binding_sha256,
            total_plaintext_sha256=total_hash,
            total_plaintext_bytes=total_bytes,
            chunk_count=len(verified_manifest.chunks),
        )
    except PhysicalWalChunkedBaseBackupReceiverStagingError as exc:
        _mark_failed_quietly(
            ledger_config=facts.ledger_config,
            claim=claim,
            failure_code=exc.code,
            clock=clock,
        )
        raise
    except Exception:
        _mark_failed_quietly(
            ledger_config=facts.ledger_config,
            claim=claim,
            failure_code="CHUNKED_BASE_BACKUP_RECEIVER_STAGE_UNEXPECTED_FAILURE",
            clock=clock,
        )
        _fail("CHUNKED_BASE_BACKUP_RECEIVER_STAGE_UNEXPECTED_FAILURE")
    finally:
        for fd in (stage_fd, root_fd):
            if fd >= 0:
                try:
                    os.close(fd)
                except OSError:
                    pass
