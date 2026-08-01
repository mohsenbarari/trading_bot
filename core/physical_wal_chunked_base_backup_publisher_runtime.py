"""Default-off, root-only publisher runtime for the physical base-backup v2 path.

This is intentionally a new, isolated path.  It neither imports nor falls
back to a v1 spool/uploader/runtime, never uses multipart upload, and never
opens a direct WebApp-to-WebApp channel.  It reads one fixed staged regular
file below a configured root, splits it into bounded plaintext chunks, and
uses only injected narrow capabilities for age encryption, one callback-scoped
create-only Object Storage action per permit, source completion signing, and
Witness mediation.

Any upload which cannot receive a timely Witness commitment stays an
unreferenced immutable Object Storage orphan.  This module deliberately has
no delete/retry-by-reuse operation.  It returns authority only as a verified
v2 manifest plus a receiver-facing Witness handoff receipt after contiguous
accepted-state finalization.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
import base64
import hashlib
import os
from pathlib import Path
import re
import stat
from typing import TYPE_CHECKING, Any, Protocol, TypeVar

from core.append_only_sync_delta_batch import (
    CAMPAIGN_ID_RE,
    RELEASE_SHA_RE,
    SHA256_RE,
    VERSION_ID_RE,
    canonical_json_bytes,
)
from core.object_delta_transport_binding import AGE_RECIPIENT_RE
from core.physical_wal_chunked_base_backup_handoff_receipt import (
    VerifiedPhysicalWalChunkedBaseBackupHandoffReceipt,
    require_verified_physical_wal_chunked_base_backup_handoff_receipt,
)
from core.physical_wal_chunked_base_backup_lineage_envelope import (
    VerifiedPhysicalWalChunkedBaseBackupLineageEnvelope,
    build_physical_wal_chunked_base_backup_lineage_envelope,
    require_verified_physical_wal_chunked_base_backup_lineage_envelope,
)
from core.physical_wal_chunked_base_backup_manifest import (
    VerifiedPhysicalWalChunkedBaseBackupManifest,
    require_verified_physical_wal_chunked_base_backup_manifest,
)

if TYPE_CHECKING:  # The verified lineage bridge owns the only legacy import.
    from core.physical_wal_base_backup_spool import VerifiedPhysicalWalBaseBackupBinding
from core.physical_wal_chunked_base_backup_transfer import (
    MAX_PHYSICAL_WAL_CHUNKED_BASE_BACKUP_CHUNK_CIPHERTEXT_BYTES,
    MAX_PHYSICAL_WAL_CHUNKED_BASE_BACKUP_CHUNKS,
    PHYSICAL_WAL_CHUNKED_BASE_BACKUP_TRANSPORT_PLANE,
    PhysicalWalChunkedBaseBackupBinding,
    PhysicalWalChunkedBaseBackupChunk,
    VerifiedPhysicalWalChunkedBaseBackupAcceptedChunkSet,
    VerifiedPhysicalWalChunkedBaseBackupChunkCommitment,
    VerifiedPhysicalWalChunkedBaseBackupChunkPermit,
    VerifiedPhysicalWalChunkedBaseBackupFinalizationPermit,
    VerifiedPhysicalWalChunkedBaseBackupTransferSession,
    derive_physical_wal_chunked_base_backup_chunk_key,
    require_verified_physical_wal_chunked_base_backup_chunk_commitment,
    require_verified_physical_wal_chunked_base_backup_chunk_permit,
    require_verified_physical_wal_chunked_base_backup_finalization_permit,
    require_verified_physical_wal_chunked_base_backup_transfer_session,
    require_verified_physical_wal_chunked_base_backup_witness_accepted_chunk_set,
)


__all__ = (
    "PHYSICAL_WAL_CHUNKED_BASE_BACKUP_PUBLISHER_DEFAULT_ENABLED",
    "PHYSICAL_WAL_CHUNKED_BASE_BACKUP_PUBLISHER_CHECKPOINT_SCHEMA",
    "PHYSICAL_WAL_CHUNKED_BASE_BACKUP_PUBLISHER_RUNTIME_SCHEMA",
    "MAX_PHYSICAL_WAL_CHUNKED_BASE_BACKUP_PUBLISHER_IN_FLIGHT_PLAINTEXT_BYTES",
    "MAX_PHYSICAL_WAL_CHUNKED_BASE_BACKUP_PUBLISHER_PLAINTEXT_BYTES",
    "PhysicalWalChunkedBaseBackupChunkWorker",
    "PhysicalWalChunkedBaseBackupChunkWorkerFactory",
    "PhysicalWalChunkedBaseBackupPublisherCheckpoint",
    "PhysicalWalChunkedBaseBackupPublisherCheckpointSink",
    "PhysicalWalChunkedBaseBackupPublisherAction",
    "PhysicalWalChunkedBaseBackupPublisherObjectHeadObservation",
    "PhysicalWalChunkedBaseBackupPublisherObjectPutObservation",
    "PhysicalWalChunkedBaseBackupPublisherRuntimeError",
    "PhysicalWalChunkedBaseBackupPublisherRuntimeResult",
    "PhysicalWalChunkedBaseBackupWitnessMediator",
    "RootOwnedPhysicalWalChunkedBaseBackupPublisherConfig",
    "canonical_physical_wal_chunked_base_backup_publisher_checkpoint_bytes",
    "execute_root_owned_physical_wal_chunked_base_backup_publisher",
    "validate_root_owned_physical_wal_chunked_base_backup_publisher_config",
)


PHYSICAL_WAL_CHUNKED_BASE_BACKUP_PUBLISHER_RUNTIME_SCHEMA = (
    "gold-trade-physical-wal-chunked-base-backup-publisher-runtime-v2"
)
PHYSICAL_WAL_CHUNKED_BASE_BACKUP_PUBLISHER_DEFAULT_ENABLED = False
MAX_PHYSICAL_WAL_CHUNKED_BASE_BACKUP_PUBLISHER_IN_FLIGHT = 32
MAX_PHYSICAL_WAL_CHUNKED_BASE_BACKUP_PUBLISHER_STAGED_BYTES = 2 * 1024 * 1024 * 1024 * 1024
# A chunk must leave a conservative envelope for age framing/recipients.  The
# runtime still measures the actual ciphertext before any PUT; this limit only
# prevents a configuration from silently treating the ciphertext ceiling as a
# plaintext allowance.
MAX_PHYSICAL_WAL_CHUNKED_BASE_BACKUP_PUBLISHER_PLAINTEXT_BYTES = (
    MAX_PHYSICAL_WAL_CHUNKED_BASE_BACKUP_CHUNK_CIPHERTEXT_BYTES - 64 * 1024
)
MAX_PHYSICAL_WAL_CHUNKED_BASE_BACKUP_PUBLISHER_IN_FLIGHT_PLAINTEXT_BYTES = 512 * 1024 * 1024
PHYSICAL_WAL_CHUNKED_BASE_BACKUP_PUBLISHER_CHECKPOINT_SCHEMA = (
    "gold-trade-physical-wal-chunked-base-backup-publisher-checkpoint-v3"
)
_FILE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,255}$", re.ASCII)
_URL_OR_SECRET_RE = re.compile(
    r"(?i)(?:[a-z][a-z0-9+.-]*://|www\.|bearer\s+|access[_ -]?key|"
    r"authorization|credential|password|private[_ -]?key|secret|token)"
)
_MUTABLE_VERSION_IDS = frozenset({"null", "none", "latest", "current", "head"})
_T = TypeVar("_T")


class PhysicalWalChunkedBaseBackupPublisherRuntimeError(RuntimeError):
    """Fixed-code error from the root-only v2 publisher boundary."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class RootOwnedPhysicalWalChunkedBaseBackupPublisherConfig:
    """No-secret, default-off root policy for a single fixed staged artifact."""

    schema: str = PHYSICAL_WAL_CHUNKED_BASE_BACKUP_PUBLISHER_RUNTIME_SCHEMA
    transfer_binding: PhysicalWalChunkedBaseBackupBinding | None = None
    # This is an already-verified, opaque capture lineage bridge.  It is never
    # a legacy spool result and this runtime never invokes a capture/uploader.
    source_base_backup_binding: "VerifiedPhysicalWalBaseBackupBinding | None" = None
    staged_root: Path | None = None
    staged_filename: str = ""
    staged_owner_uid: int = 0
    maximum_chunk_plaintext_bytes: int = 0
    maximum_in_flight_chunks: int = 1
    enabled: bool = PHYSICAL_WAL_CHUNKED_BASE_BACKUP_PUBLISHER_DEFAULT_ENABLED
    transport_plane: str = PHYSICAL_WAL_CHUNKED_BASE_BACKUP_TRANSPORT_PLANE
    direct_site_control: str = "forbidden"
    destination_object_ingest: str = "pull-only"
    multipart_upload: str = "forbidden"
    v1_fallback: str = "forbidden"


@dataclass(frozen=True)
class PhysicalWalChunkedBaseBackupPublisherObjectPutObservation:
    """Safe create-only PUT outcome: only the immutable version selector."""

    version_id: str


@dataclass(frozen=True)
class PhysicalWalChunkedBaseBackupPublisherObjectHeadObservation:
    """Safe exact HEAD/read-back observation, never a raw client response."""

    object_key: str
    version_id: str
    ciphertext_sha256: str
    ciphertext_bytes: int


@dataclass(frozen=True)
class PhysicalWalChunkedBaseBackupPublisherRuntimeResult:
    """The only returned authority: verified manifest and receiver handoff evidence."""

    manifest: VerifiedPhysicalWalChunkedBaseBackupManifest
    receiver_handoff_receipt: VerifiedPhysicalWalChunkedBaseBackupHandoffReceipt
    staged_plaintext_sha256: str
    staged_plaintext_bytes: int
    uploaded_chunk_count: int


@dataclass(frozen=True)
class PhysicalWalChunkedBaseBackupPublisherCheckpoint:
    """Durable-resume seam, not an implicit resume authority.

    A production checkpoint adapter must persist this append-only after every
    call.  The exact signed session, permit, source-completion, and Witness
    commitment bytes are retained for forensic and resume reconciliation; this
    runtime intentionally has no blind restart path and will reject a supplied
    checkpoint until a separate adapter performs authoritative Witness and
    exact-version Object Storage reconciliation.
    """

    canonical_checkpoint: bytes
    schema: str
    session_id: str
    session_sha256: str
    binding_sha256: str
    lineage_sha256: str
    staged_plaintext_sha256: str
    staged_plaintext_bytes: int
    issued_permit_ids: tuple[str, ...]
    issued_permit_sha256s: tuple[str, ...]
    accepted_commitment_ids: tuple[str, ...]
    accepted_commitment_sha256s: tuple[str, ...]
    canonical_session: bytes
    canonical_issued_permits: tuple[bytes, ...]
    canonical_accepted_completions: tuple[bytes, ...]
    canonical_accepted_commitments: tuple[bytes, ...]



@dataclass(frozen=True)
class _ConfigFacts:
    binding: PhysicalWalChunkedBaseBackupBinding
    source_base_backup_binding: object
    staged_root: Path
    staged_filename: str
    staged_owner_uid: int
    chunk_bytes: int
    maximum_in_flight: int


@dataclass(frozen=True)
class _PublishedChunk:
    permit: VerifiedPhysicalWalChunkedBaseBackupChunkPermit
    chunk: PhysicalWalChunkedBaseBackupChunk
    commitment: VerifiedPhysicalWalChunkedBaseBackupChunkCommitment


@dataclass(frozen=True)
class _CallbackUploadObservation:
    object_key: str
    version_id: str
    ciphertext_sha256: str
    ciphertext_bytes: int


class PhysicalWalChunkedBaseBackupPublisherAction(Protocol):
    """A transient exact-key publisher lease, valid only inside one callback."""

    def put_object_if_none_match(
        self,
        *,
        object_key: str,
        ciphertext: bytes,
        if_none_match: str,
    ) -> PhysicalWalChunkedBaseBackupPublisherObjectPutObservation: ...

    def head_exact_object_version(
        self,
        *,
        object_key: str,
        version_id: str,
    ) -> PhysicalWalChunkedBaseBackupPublisherObjectHeadObservation: ...


class PhysicalWalChunkedBaseBackupChunkWorker(Protocol):
    """One isolated worker capability for exactly one concurrently published chunk.

    It contains no exposed credential/client and must not be returned for a
    second permit.  The factory is called sequentially and the runtime rejects
    identity reuse before submitting work to the executor.
    """

    def encrypt_chunk(self, *, recipient: str, plaintext: bytes) -> bytes: ...

    def with_exact_chunk_publisher(
        self,
        *,
        permit: VerifiedPhysicalWalChunkedBaseBackupChunkPermit,
        callback: Callable[[PhysicalWalChunkedBaseBackupPublisherAction], _T],
    ) -> _T: ...

    def build_completion(
        self,
        *,
        permit: VerifiedPhysicalWalChunkedBaseBackupChunkPermit,
        chunk: PhysicalWalChunkedBaseBackupChunk,
        completed_at: datetime,
    ) -> Mapping[str, Any] | bytes: ...


class PhysicalWalChunkedBaseBackupChunkWorkerFactory(Protocol):
    """Creates a fresh, permit-bound worker before each parallel submission."""

    def open_chunk_worker(
        self,
        *,
        permit: VerifiedPhysicalWalChunkedBaseBackupChunkPermit,
    ) -> PhysicalWalChunkedBaseBackupChunkWorker: ...


class PhysicalWalChunkedBaseBackupPublisherCheckpointSink(Protocol):
    """Append-only durable checkpoint sink; no deletion or resume mutation API."""

    def persist_checkpoint(self, *, checkpoint: bytes) -> None: ...


class PhysicalWalChunkedBaseBackupWitnessMediator(Protocol):
    """Injected Witness boundary; runtime has no Witness signing key or ledger."""

    # Acceptance happens from independent workers.  Adapters must atomically
    # serialize their Witness ledger before returning a commitment.
    parallel_safe: bool

    def open_transfer_session(
        self,
        *,
        binding: PhysicalWalChunkedBaseBackupBinding,
        now: datetime,
    ) -> VerifiedPhysicalWalChunkedBaseBackupTransferSession: ...

    def reserve_chunk_permits(
        self,
        *,
        transfer_session: VerifiedPhysicalWalChunkedBaseBackupTransferSession,
        chunk_indexes: tuple[int, ...],
        now: datetime,
    ) -> Sequence[VerifiedPhysicalWalChunkedBaseBackupChunkPermit]: ...

    def accept_chunk_completion(
        self,
        *,
        transfer_session: VerifiedPhysicalWalChunkedBaseBackupTransferSession,
        chunk_permit: VerifiedPhysicalWalChunkedBaseBackupChunkPermit,
        completion: Mapping[str, Any] | bytes,
        now: datetime,
    ) -> VerifiedPhysicalWalChunkedBaseBackupChunkCommitment: ...

    def begin_accepted_chunk_set(
        self,
        *,
        transfer_session: VerifiedPhysicalWalChunkedBaseBackupTransferSession,
        now: datetime,
    ) -> VerifiedPhysicalWalChunkedBaseBackupAcceptedChunkSet: ...

    def append_accepted_chunk(
        self,
        *,
        accepted_chunk_set: VerifiedPhysicalWalChunkedBaseBackupAcceptedChunkSet,
        chunk_commitment: VerifiedPhysicalWalChunkedBaseBackupChunkCommitment,
        now: datetime,
    ) -> VerifiedPhysicalWalChunkedBaseBackupAcceptedChunkSet: ...

    def issue_finalization_permit(
        self,
        *,
        transfer_session: VerifiedPhysicalWalChunkedBaseBackupTransferSession,
        accepted_chunk_set: VerifiedPhysicalWalChunkedBaseBackupAcceptedChunkSet,
        total_plaintext_sha256: str,
        total_plaintext_bytes: int,
        now: datetime,
    ) -> VerifiedPhysicalWalChunkedBaseBackupFinalizationPermit: ...

    def build_finalized_manifest(
        self,
        *,
        finalization_permit: VerifiedPhysicalWalChunkedBaseBackupFinalizationPermit,
        accepted_chunk_set: VerifiedPhysicalWalChunkedBaseBackupAcceptedChunkSet,
        now: datetime,
    ) -> VerifiedPhysicalWalChunkedBaseBackupManifest: ...

    def issue_receiver_handoff_receipt(
        self,
        *,
        manifest: VerifiedPhysicalWalChunkedBaseBackupManifest,
        lineage_envelope: VerifiedPhysicalWalChunkedBaseBackupLineageEnvelope,
        now: datetime,
    ) -> VerifiedPhysicalWalChunkedBaseBackupHandoffReceipt: ...


def _fail(code: str) -> None:
    raise PhysicalWalChunkedBaseBackupPublisherRuntimeError(code)


def _safe_root(value: object) -> Path:
    if not isinstance(value, Path) or not value.is_absolute() or ".." in value.parts:
        _fail("CHUNKED_BASE_BACKUP_PUBLISHER_STAGED_ROOT_INVALID")
    text = str(value)
    if not text or len(text) > 4096 or _URL_OR_SECRET_RE.search(text) is not None:
        _fail("CHUNKED_BASE_BACKUP_PUBLISHER_STAGED_ROOT_INVALID")
    return value


def _safe_binding(value: object) -> PhysicalWalChunkedBaseBackupBinding:
    if type(value) is not PhysicalWalChunkedBaseBackupBinding:
        _fail("CHUNKED_BASE_BACKUP_PUBLISHER_BINDING_INVALID")
    if (
        value.source_site not in {"webapp_fi", "webapp_ir"}
        or value.destination_site not in {"webapp_fi", "webapp_ir"}
        or value.source_site == value.destination_site
        or CAMPAIGN_ID_RE.fullmatch(value.campaign_id) is None
        or RELEASE_SHA_RE.fullmatch(value.release_sha) is None
        or value.transport_plane != PHYSICAL_WAL_CHUNKED_BASE_BACKUP_TRANSPORT_PLANE
        or value.direct_webapp_transport != "forbidden"
        or value.object_storage_namespace not in {"physical-wal", "physical-failback"}
        or SHA256_RE.fullmatch(value.route_commitment_sha256) is None
        or SHA256_RE.fullmatch(value.four_role_binding_sha256) is None
        or AGE_RECIPIENT_RE.fullmatch(value.destination_age_recipient) is None
        or value.writer_term.writer_holder_site != value.source_site
    ):
        _fail("CHUNKED_BASE_BACKUP_PUBLISHER_BINDING_INVALID")
    return value


def _positive(value: object, *, maximum: int, code: str) -> int:
    if type(value) is not int or not 1 <= value <= maximum:
        _fail(code)
    return value


def validate_root_owned_physical_wal_chunked_base_backup_publisher_config(
    config: object,
    *,
    require_enabled: bool = True,
) -> None:
    """Validate no-secret policy only; it does not read the staged file."""

    _config_facts(config, require_enabled=require_enabled)


def _config_facts(config: object, *, require_enabled: bool) -> _ConfigFacts:
    if type(config) is not RootOwnedPhysicalWalChunkedBaseBackupPublisherConfig:
        _fail("CHUNKED_BASE_BACKUP_PUBLISHER_CONFIG_INVALID")
    if (
        config.schema != PHYSICAL_WAL_CHUNKED_BASE_BACKUP_PUBLISHER_RUNTIME_SCHEMA
        or type(config.enabled) is not bool
        or (require_enabled and config.enabled is not True)
        or config.transport_plane != PHYSICAL_WAL_CHUNKED_BASE_BACKUP_TRANSPORT_PLANE
        or config.direct_site_control != "forbidden"
        or config.destination_object_ingest != "pull-only"
        or config.multipart_upload != "forbidden"
        or config.v1_fallback != "forbidden"
    ):
        _fail("CHUNKED_BASE_BACKUP_PUBLISHER_CONFIG_INVALID")
    root = _safe_root(config.staged_root)
    filename = config.staged_filename
    if (
        type(filename) is not str
        or _FILE_NAME_RE.fullmatch(filename) is None
        or "/" in filename
        or "\\" in filename
        or _URL_OR_SECRET_RE.search(filename) is not None
    ):
        _fail("CHUNKED_BASE_BACKUP_PUBLISHER_STAGED_FILENAME_INVALID")
    # ``0`` is intentionally the only accepted owner: this is an installed
    # root-only staging boundary, not a user-controlled workspace adapter.
    if type(config.staged_owner_uid) is not int or config.staged_owner_uid != 0:
        _fail("CHUNKED_BASE_BACKUP_PUBLISHER_OWNER_INVALID")
    owner = 0
    chunk = _positive(
        config.maximum_chunk_plaintext_bytes,
        maximum=MAX_PHYSICAL_WAL_CHUNKED_BASE_BACKUP_PUBLISHER_PLAINTEXT_BYTES,
        code="CHUNKED_BASE_BACKUP_PUBLISHER_CHUNK_SIZE_INVALID",
    )
    inflight = _positive(
        config.maximum_in_flight_chunks,
        maximum=MAX_PHYSICAL_WAL_CHUNKED_BASE_BACKUP_PUBLISHER_IN_FLIGHT,
        code="CHUNKED_BASE_BACKUP_PUBLISHER_IN_FLIGHT_INVALID",
    )
    # A worker retains the plaintext while the independent age ciphertext is
    # produced and read back.  Cap the resident payload envelope, not merely
    # the input batch, before opening the staged descriptor or a session.
    if 2 * chunk * inflight > MAX_PHYSICAL_WAL_CHUNKED_BASE_BACKUP_PUBLISHER_IN_FLIGHT_PLAINTEXT_BYTES:
        _fail("CHUNKED_BASE_BACKUP_PUBLISHER_IN_FLIGHT_MEMORY_CAP_EXCEEDED")
    if config.source_base_backup_binding is None:
        _fail("CHUNKED_BASE_BACKUP_PUBLISHER_SOURCE_LINEAGE_REQUIRED")
    return _ConfigFacts(
        binding=_safe_binding(config.transfer_binding),
        source_base_backup_binding=config.source_base_backup_binding,
        staged_root=root,
        staged_filename=filename,
        staged_owner_uid=owner,
        chunk_bytes=chunk,
        maximum_in_flight=inflight,
    )


def _checked_clock(clock: Callable[[], datetime]) -> datetime:
    if not callable(clock):
        _fail("CHUNKED_BASE_BACKUP_PUBLISHER_CLOCK_INVALID")
    try:
        value = clock()
    except Exception:
        _fail("CHUNKED_BASE_BACKUP_PUBLISHER_CLOCK_INVALID")
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        _fail("CHUNKED_BASE_BACKUP_PUBLISHER_CLOCK_INVALID")
    return value.astimezone(timezone.utc)


def _open_fixed_staged_file(facts: _ConfigFacts) -> tuple[int, int, os.stat_result]:
    if os.geteuid() != 0:
        _fail("CHUNKED_BASE_BACKUP_PUBLISHER_ROOT_REQUIRED")
    try:
        root_lstat = os.lstat(facts.staged_root)
    except OSError:
        _fail("CHUNKED_BASE_BACKUP_PUBLISHER_STAGED_ROOT_UNAVAILABLE")
    if (
        not stat.S_ISDIR(root_lstat.st_mode)
        or stat.S_ISLNK(root_lstat.st_mode)
        or root_lstat.st_uid != facts.staged_owner_uid
        or root_lstat.st_mode & 0o022
    ):
        _fail("CHUNKED_BASE_BACKUP_PUBLISHER_STAGED_ROOT_UNSAFE")
    root_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
    root_flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        root_fd = os.open(facts.staged_root, root_flags)
    except OSError:
        _fail("CHUNKED_BASE_BACKUP_PUBLISHER_STAGED_ROOT_UNSAFE")
    try:
        opened_root = os.fstat(root_fd)
        if (
            opened_root.st_dev != root_lstat.st_dev
            or opened_root.st_ino != root_lstat.st_ino
            or opened_root.st_uid != facts.staged_owner_uid
            or opened_root.st_mode & 0o022
        ):
            _fail("CHUNKED_BASE_BACKUP_PUBLISHER_STAGED_ROOT_RACED")
        file_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        file_fd = os.open(facts.staged_filename, file_flags, dir_fd=root_fd)
        try:
            file_stat = os.fstat(file_fd)
        except Exception:
            os.close(file_fd)
            raise
        if (
            not stat.S_ISREG(file_stat.st_mode)
            or file_stat.st_uid != facts.staged_owner_uid
            or file_stat.st_mode & 0o022
            or file_stat.st_nlink != 1
            or file_stat.st_size < 1
            or file_stat.st_size > MAX_PHYSICAL_WAL_CHUNKED_BASE_BACKUP_PUBLISHER_STAGED_BYTES
        ):
            os.close(file_fd)
            _fail("CHUNKED_BASE_BACKUP_PUBLISHER_STAGED_FILE_UNSAFE")
        return root_fd, file_fd, file_stat
    except PhysicalWalChunkedBaseBackupPublisherRuntimeError:
        os.close(root_fd)
        raise
    except OSError:
        os.close(root_fd)
        _fail("CHUNKED_BASE_BACKUP_PUBLISHER_STAGED_FILE_UNAVAILABLE")


def _same_staged_stat(left: os.stat_result, right: os.stat_result) -> bool:
    """Identity/content-change guard for the one open, root-owned descriptor."""

    return (
        left.st_dev == right.st_dev
        and left.st_ino == right.st_ino
        and left.st_size == right.st_size
        and left.st_mtime_ns == right.st_mtime_ns
        and left.st_ctime_ns == right.st_ctime_ns
        and left.st_uid == right.st_uid
        and stat.S_ISREG(right.st_mode)
        and not right.st_mode & 0o022
        and right.st_nlink == 1
    )


def _hash_and_rewind_verified_staged_file(
    *,
    staged,
    initial_stat: os.stat_result,
    facts: _ConfigFacts,
    lineage_envelope: VerifiedPhysicalWalChunkedBaseBackupLineageEnvelope,
) -> tuple[str, int]:
    """Verify the immutable staged bytes before any Witness session/permit/PUT.

    The descriptor remains open between this bounded first pass and publication;
    no pathname is reopened after the source snapshot has been pinned.
    """

    required_chunks = (initial_stat.st_size + facts.chunk_bytes - 1) // facts.chunk_bytes
    if required_chunks < 1 or required_chunks > MAX_PHYSICAL_WAL_CHUNKED_BASE_BACKUP_CHUNKS:
        _fail("CHUNKED_BASE_BACKUP_PUBLISHER_CHUNK_COUNT_EXCEEDED")
    digest = hashlib.sha256()
    total = 0
    while True:
        try:
            data = staged.read(facts.chunk_bytes)
        except OSError:
            _fail("CHUNKED_BASE_BACKUP_PUBLISHER_STAGED_FILE_UNAVAILABLE")
        if not data:
            break
        if not isinstance(data, bytes) or len(data) > facts.chunk_bytes:
            _fail("CHUNKED_BASE_BACKUP_PUBLISHER_CHUNK_READ_INVALID")
        digest.update(data)
        total += len(data)
    try:
        final_stat = os.fstat(staged.fileno())
        staged.seek(0, os.SEEK_SET)
    except (OSError, ValueError):
        _fail("CHUNKED_BASE_BACKUP_PUBLISHER_STAGED_FILE_UNAVAILABLE")
    source_hash = digest.hexdigest()
    if (
        not _same_staged_stat(initial_stat, final_stat)
        or total != initial_stat.st_size
        or source_hash != lineage_envelope.snapshot_sha256
        or total != lineage_envelope.snapshot_bytes
    ):
        _fail("CHUNKED_BASE_BACKUP_PUBLISHER_STAGED_SNAPSHOT_MISMATCH")
    return source_hash, total


def _checkpoint_binding_sha256(binding: PhysicalWalChunkedBaseBackupBinding) -> str:
    payload = {
        "schema": "gold-trade-physical-wal-chunked-base-backup-publisher-binding-v2",
        "source_site": binding.source_site,
        "destination_site": binding.destination_site,
        "campaign_id": binding.campaign_id,
        "release_sha": binding.release_sha,
        "object_storage_namespace": binding.object_storage_namespace,
        "route_commitment_sha256": binding.route_commitment_sha256,
        "four_role_binding_sha256": binding.four_role_binding_sha256,
        "destination_age_recipient": binding.destination_age_recipient,
        "writer_term": {
            "writer_holder_site": binding.writer_term.writer_holder_site,
            "writer_epoch": binding.writer_term.writer_epoch,
            "writer_lease_id": binding.writer_term.writer_lease_id,
            "witnessed_term_proof_sha256": binding.writer_term.witnessed_term_proof_sha256,
        },
        "transport_plane": binding.transport_plane,
        "direct_webapp_transport": binding.direct_webapp_transport,
    }
    try:
        return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    except (TypeError, ValueError):  # pragma: no cover - binding already normalized.
        _fail("CHUNKED_BASE_BACKUP_PUBLISHER_CHECKPOINT_INVALID")


def _build_checkpoint(
    *,
    session: VerifiedPhysicalWalChunkedBaseBackupTransferSession,
    lineage_sha256: str,
    staged_plaintext_sha256: str,
    staged_plaintext_bytes: int,
    issued_permits: Sequence[VerifiedPhysicalWalChunkedBaseBackupChunkPermit],
    accepted_commitments: Sequence[VerifiedPhysicalWalChunkedBaseBackupChunkCommitment],
) -> PhysicalWalChunkedBaseBackupPublisherCheckpoint:
    """Build one canonical, non-secret, append-only resume checkpoint blob."""

    try:
        live_session = require_verified_physical_wal_chunked_base_backup_transfer_session(
            session, now=session.issued_at
        )
    except Exception:
        _fail("CHUNKED_BASE_BACKUP_PUBLISHER_CHECKPOINT_INVALID")
    if SHA256_RE.fullmatch(staged_plaintext_sha256) is None or staged_plaintext_sha256 == "0" * 64:
        _fail("CHUNKED_BASE_BACKUP_PUBLISHER_CHECKPOINT_INVALID")
    if SHA256_RE.fullmatch(lineage_sha256) is None or lineage_sha256 == "0" * 64:
        _fail("CHUNKED_BASE_BACKUP_PUBLISHER_CHECKPOINT_INVALID")
    if type(staged_plaintext_bytes) is not int or staged_plaintext_bytes < 1:
        _fail("CHUNKED_BASE_BACKUP_PUBLISHER_CHECKPOINT_INVALID")
    permits: list[dict[str, object]] = []
    normalized_permits: list[VerifiedPhysicalWalChunkedBaseBackupChunkPermit] = []
    seen_permit_ids: set[str] = set()
    for permit in issued_permits:
        try:
            verified_permit = require_verified_physical_wal_chunked_base_backup_chunk_permit(
                permit, now=permit.issued_at
            )
        except Exception:
            _fail("CHUNKED_BASE_BACKUP_PUBLISHER_CHECKPOINT_INVALID")
        if (
            verified_permit.session.canonical_session != live_session.canonical_session
            or verified_permit.permit_id in seen_permit_ids
        ):
            _fail("CHUNKED_BASE_BACKUP_PUBLISHER_CHECKPOINT_INVALID")
        seen_permit_ids.add(verified_permit.permit_id)
        normalized_permits.append(verified_permit)
        permits.append(
            {
                "permit_id": verified_permit.permit_id,
                "permit_sha256": hashlib.sha256(verified_permit.canonical_permit).hexdigest(),
                "canonical_permit_base64": base64.b64encode(verified_permit.canonical_permit).decode("ascii"),
            }
        )
    commitments: list[dict[str, object]] = []
    normalized_commitments: list[VerifiedPhysicalWalChunkedBaseBackupChunkCommitment] = []
    seen_commitments: set[str] = set()
    seen_commitment_indexes: set[int] = set()
    for commitment in accepted_commitments:
        try:
            verified_commitment = require_verified_physical_wal_chunked_base_backup_chunk_commitment(
                commitment, now=commitment.committed_at
            )
        except Exception:
            _fail("CHUNKED_BASE_BACKUP_PUBLISHER_CHECKPOINT_INVALID")
        if (
            verified_commitment.completion.permit.session.canonical_session != live_session.canonical_session
            or verified_commitment.commitment_id in seen_commitments
            or verified_commitment.chunk.index in seen_commitment_indexes
        ):
            _fail("CHUNKED_BASE_BACKUP_PUBLISHER_CHECKPOINT_INVALID")
        seen_commitments.add(verified_commitment.commitment_id)
        seen_commitment_indexes.add(verified_commitment.chunk.index)
        normalized_commitments.append(verified_commitment)
        commitments.append(
            {
                "chunk_index": verified_commitment.chunk.index,
                "commitment_id": verified_commitment.commitment_id,
                "commitment_sha256": hashlib.sha256(verified_commitment.canonical_commitment).hexdigest(),
                # A commitment only pins the source completion digest.  Resume
                # reconciliation must retain the signed completion itself so
                # it can prove the historical acceptance without trusting a
                # process-local verified capability after a crash.
                "canonical_completion_base64": base64.b64encode(
                    verified_commitment.completion.canonical_completion
                ).decode("ascii"),
                "canonical_commitment_base64": base64.b64encode(verified_commitment.canonical_commitment).decode("ascii"),
            }
        )
    if permits != sorted(permits, key=lambda item: str(item["permit_id"])):
        permits.sort(key=lambda item: str(item["permit_id"]))
    commitments.sort(key=lambda item: int(item["chunk_index"]))
    payload = {
        "schema": PHYSICAL_WAL_CHUNKED_BASE_BACKUP_PUBLISHER_CHECKPOINT_SCHEMA,
        "session_id": live_session.session_id,
        "session_sha256": hashlib.sha256(live_session.canonical_session).hexdigest(),
        "binding_sha256": _checkpoint_binding_sha256(live_session.binding),
        "lineage_sha256": lineage_sha256,
        "staged_plaintext_sha256": staged_plaintext_sha256,
        "staged_plaintext_bytes": staged_plaintext_bytes,
        "canonical_session_base64": base64.b64encode(live_session.canonical_session).decode("ascii"),
        "issued_permits": permits,
        "accepted_commitments": commitments,
    }
    try:
        raw = canonical_json_bytes(payload)
    except (TypeError, ValueError):  # pragma: no cover - normalized above.
        _fail("CHUNKED_BASE_BACKUP_PUBLISHER_CHECKPOINT_INVALID")
    return PhysicalWalChunkedBaseBackupPublisherCheckpoint(
        canonical_checkpoint=raw,
        schema=PHYSICAL_WAL_CHUNKED_BASE_BACKUP_PUBLISHER_CHECKPOINT_SCHEMA,
        session_id=live_session.session_id,
        session_sha256=hashlib.sha256(live_session.canonical_session).hexdigest(),
        binding_sha256=_checkpoint_binding_sha256(live_session.binding),
        lineage_sha256=lineage_sha256,
        staged_plaintext_sha256=staged_plaintext_sha256,
        staged_plaintext_bytes=staged_plaintext_bytes,
        issued_permit_ids=tuple(item["permit_id"] for item in permits),  # type: ignore[arg-type]
        issued_permit_sha256s=tuple(item["permit_sha256"] for item in permits),  # type: ignore[arg-type]
        accepted_commitment_ids=tuple(item["commitment_id"] for item in commitments),  # type: ignore[arg-type]
        accepted_commitment_sha256s=tuple(item["commitment_sha256"] for item in commitments),  # type: ignore[arg-type]
        canonical_session=live_session.canonical_session,
        canonical_issued_permits=tuple(permit.canonical_permit for permit in normalized_permits),
        canonical_accepted_completions=tuple(
            commitment.completion.canonical_completion for commitment in normalized_commitments
        ),
        canonical_accepted_commitments=tuple(
            commitment.canonical_commitment for commitment in normalized_commitments
        ),
    )


def canonical_physical_wal_chunked_base_backup_publisher_checkpoint_bytes(
    value: PhysicalWalChunkedBaseBackupPublisherCheckpoint,
) -> bytes:
    """Return the exact durable blob an external resume adapter must retain.

    This helper is deliberately *not* a resume parser.  A later adapter must
    reconcile this evidence with the Witness ledger and issue fresh permits;
    expired permits are never replayed by this publisher.
    """

    if type(value) is not PhysicalWalChunkedBaseBackupPublisherCheckpoint:
        _fail("CHUNKED_BASE_BACKUP_PUBLISHER_CHECKPOINT_INVALID")
    if (
        value.schema != PHYSICAL_WAL_CHUNKED_BASE_BACKUP_PUBLISHER_CHECKPOINT_SCHEMA
        or not isinstance(value.canonical_checkpoint, bytes)
        or not value.canonical_checkpoint
    ):
        _fail("CHUNKED_BASE_BACKUP_PUBLISHER_CHECKPOINT_INVALID")
    return value.canonical_checkpoint


def _persist_checkpoint(
    *,
    checkpoint_sink: PhysicalWalChunkedBaseBackupPublisherCheckpointSink,
    session: VerifiedPhysicalWalChunkedBaseBackupTransferSession,
    lineage_sha256: str,
    staged_plaintext_sha256: str,
    staged_plaintext_bytes: int,
    issued_permits: Sequence[VerifiedPhysicalWalChunkedBaseBackupChunkPermit],
    accepted_commitments: Sequence[VerifiedPhysicalWalChunkedBaseBackupChunkCommitment],
) -> None:
    if checkpoint_sink is None or not callable(getattr(checkpoint_sink, "persist_checkpoint", None)):
        _fail("CHUNKED_BASE_BACKUP_PUBLISHER_CHECKPOINT_SINK_INVALID")
    checkpoint = _build_checkpoint(
        session=session,
        lineage_sha256=lineage_sha256,
        staged_plaintext_sha256=staged_plaintext_sha256,
        staged_plaintext_bytes=staged_plaintext_bytes,
        issued_permits=issued_permits,
        accepted_commitments=accepted_commitments,
    )
    try:
        checkpoint_sink.persist_checkpoint(
            checkpoint=canonical_physical_wal_chunked_base_backup_publisher_checkpoint_bytes(checkpoint)
        )
    except PhysicalWalChunkedBaseBackupPublisherRuntimeError:
        raise
    except Exception:
        _fail("CHUNKED_BASE_BACKUP_PUBLISHER_CHECKPOINT_PERSIST_FAILED")


def _reject_resume_checkpoint(value: object) -> None:
    """Fail closed until a reconciled resume adapter is installed.

    The runtime cannot decide whether a session was abandoned, whether a permit
    expired after an object PUT, or whether a signed commitment is already in
    the Witness ledger.  It therefore never turns a checkpoint into a fresh
    session, even if the blob looks well-formed.
    """

    if value is None:
        return
    if type(value) is PhysicalWalChunkedBaseBackupPublisherCheckpoint:
        raw = canonical_physical_wal_chunked_base_backup_publisher_checkpoint_bytes(value)
    elif isinstance(value, bytes):
        raw = value
    else:
        _fail("CHUNKED_BASE_BACKUP_PUBLISHER_RESUME_CHECKPOINT_INVALID")
    if not raw or len(raw) > 32 * 1024 * 1024:
        _fail("CHUNKED_BASE_BACKUP_PUBLISHER_RESUME_CHECKPOINT_INVALID")
    _fail("CHUNKED_BASE_BACKUP_PUBLISHER_RESUME_ADAPTER_REQUIRED")


def _version_id(value: object) -> str:
    if type(value) is not str or VERSION_ID_RE.fullmatch(value) is None or value.casefold() in _MUTABLE_VERSION_IDS:
        _fail("CHUNKED_BASE_BACKUP_PUBLISHER_OBJECT_VERSION_INVALID")
    return value


def _head_observation(
    value: object,
    *,
    object_key: str,
    version_id: str,
    ciphertext_sha256: str,
    ciphertext_bytes: int,
) -> PhysicalWalChunkedBaseBackupPublisherObjectHeadObservation:
    if type(value) is not PhysicalWalChunkedBaseBackupPublisherObjectHeadObservation:
        _fail("CHUNKED_BASE_BACKUP_PUBLISHER_CALLBACK_LEAK_OR_HEAD_INVALID")
    if (
        value.object_key != object_key
        or _version_id(value.version_id) != version_id
        or value.ciphertext_sha256 != ciphertext_sha256
        or value.ciphertext_bytes != ciphertext_bytes
    ):
        _fail("CHUNKED_BASE_BACKUP_PUBLISHER_READBACK_MISMATCH")
    return value


def _publish_exact_chunk(
    *,
    worker: PhysicalWalChunkedBaseBackupChunkWorker,
    permit: VerifiedPhysicalWalChunkedBaseBackupChunkPermit,
    ciphertext: bytes,
) -> _CallbackUploadObservation:
    ciphertext_sha256 = hashlib.sha256(ciphertext).hexdigest()
    ciphertext_bytes = len(ciphertext)

    def callback(action: PhysicalWalChunkedBaseBackupPublisherAction) -> _CallbackUploadObservation:
        if action is None:
            _fail("CHUNKED_BASE_BACKUP_PUBLISHER_CALLBACK_LEAK_OR_HEAD_INVALID")
        put = action.put_object_if_none_match(
            object_key=permit.object_key,
            ciphertext=ciphertext,
            if_none_match="*",
        )
        if type(put) is not PhysicalWalChunkedBaseBackupPublisherObjectPutObservation:
            _fail("CHUNKED_BASE_BACKUP_PUBLISHER_CALLBACK_LEAK_OR_PUT_INVALID")
        version = _version_id(put.version_id)
        head = action.head_exact_object_version(object_key=permit.object_key, version_id=version)
        _head_observation(
            head,
            object_key=permit.object_key,
            version_id=version,
            ciphertext_sha256=ciphertext_sha256,
            ciphertext_bytes=ciphertext_bytes,
        )
        return _CallbackUploadObservation(
            object_key=permit.object_key,
            version_id=version,
            ciphertext_sha256=ciphertext_sha256,
            ciphertext_bytes=ciphertext_bytes,
        )

    try:
        result = worker.with_exact_chunk_publisher(permit=permit, callback=callback)
    except PhysicalWalChunkedBaseBackupPublisherRuntimeError:
        raise
    except Exception:
        _fail("CHUNKED_BASE_BACKUP_PUBLISHER_OBJECT_SIDE_EFFECT_FAILED")
    if type(result) is not _CallbackUploadObservation:
        _fail("CHUNKED_BASE_BACKUP_PUBLISHER_CALLBACK_LEAK_OR_PUT_INVALID")
    if (
        result.object_key != permit.object_key
        or result.ciphertext_sha256 != ciphertext_sha256
        or result.ciphertext_bytes != ciphertext_bytes
    ):
        _fail("CHUNKED_BASE_BACKUP_PUBLISHER_CALLBACK_LEAK_OR_PUT_INVALID")
    _version_id(result.version_id)
    return result


def _validated_permits(
    *,
    permits: object,
    transfer_session: VerifiedPhysicalWalChunkedBaseBackupTransferSession,
    expected_indexes: tuple[int, ...],
    now: datetime,
) -> dict[int, VerifiedPhysicalWalChunkedBaseBackupChunkPermit]:
    if isinstance(permits, (str, bytes)) or not isinstance(permits, Sequence) or len(permits) != len(expected_indexes):
        _fail("CHUNKED_BASE_BACKUP_PUBLISHER_PERMIT_SET_INVALID")
    expected = set(expected_indexes)
    result: dict[int, VerifiedPhysicalWalChunkedBaseBackupChunkPermit] = {}
    for candidate in permits:
        try:
            permit = require_verified_physical_wal_chunked_base_backup_chunk_permit(candidate, now=now)
        except Exception:
            _fail("CHUNKED_BASE_BACKUP_PUBLISHER_PERMIT_SET_INVALID")
        if (
            permit.session.canonical_session != transfer_session.canonical_session
            or permit.chunk_index not in expected
            or permit.chunk_index in result
            or permit.object_key
            != derive_physical_wal_chunked_base_backup_chunk_key(
                binding=transfer_session.binding,
                session_id=transfer_session.session_id,
                chunk_index=permit.chunk_index,
                permit_nonce=permit.permit_nonce,
            )
        ):
            _fail("CHUNKED_BASE_BACKUP_PUBLISHER_PERMIT_SET_INVALID")
        result[permit.chunk_index] = permit
    if set(result) != expected:
        _fail("CHUNKED_BASE_BACKUP_PUBLISHER_PERMIT_SET_INVALID")
    return result


def _open_isolated_chunk_workers(
    *,
    worker_factory: PhysicalWalChunkedBaseBackupChunkWorkerFactory,
    permits_by_index: Mapping[int, VerifiedPhysicalWalChunkedBaseBackupChunkPermit],
) -> dict[int, PhysicalWalChunkedBaseBackupChunkWorker]:
    """Open each bounded capability sequentially and reject worker reuse.

    This keeps factory/credential derivation outside the executor.  Only the
    isolated returned worker is passed to one concurrent task, so an adapter
    cannot accidentally share an age identity, Object Storage client facade,
    or completion signer between permits.
    """

    if worker_factory is None or not callable(getattr(worker_factory, "open_chunk_worker", None)):
        _fail("CHUNKED_BASE_BACKUP_PUBLISHER_WORKER_FACTORY_INVALID")
    result: dict[int, PhysicalWalChunkedBaseBackupChunkWorker] = {}
    identities: set[int] = set()
    for index in sorted(permits_by_index):
        permit = permits_by_index[index]
        try:
            worker = worker_factory.open_chunk_worker(permit=permit)
        except PhysicalWalChunkedBaseBackupPublisherRuntimeError:
            raise
        except Exception:
            _fail("CHUNKED_BASE_BACKUP_PUBLISHER_WORKER_FACTORY_FAILED")
        if (
            worker is None
            or id(worker) in identities
            or not callable(getattr(worker, "encrypt_chunk", None))
            or not callable(getattr(worker, "with_exact_chunk_publisher", None))
            or not callable(getattr(worker, "build_completion", None))
        ):
            _fail("CHUNKED_BASE_BACKUP_PUBLISHER_WORKER_ISOLATION_INVALID")
        identities.add(id(worker))
        result[index] = worker
    return result


def _publish_one(
    *,
    permit: VerifiedPhysicalWalChunkedBaseBackupChunkPermit,
    plaintext: bytes,
    worker: PhysicalWalChunkedBaseBackupChunkWorker,
    witness_mediator: PhysicalWalChunkedBaseBackupWitnessMediator,
    transfer_session: VerifiedPhysicalWalChunkedBaseBackupTransferSession,
    clock: Callable[[], datetime],
) -> _PublishedChunk:
    if (
        type(plaintext) is not bytes
        or not plaintext
        or len(plaintext) > MAX_PHYSICAL_WAL_CHUNKED_BASE_BACKUP_PUBLISHER_PLAINTEXT_BYTES
    ):
        _fail("CHUNKED_BASE_BACKUP_PUBLISHER_CHUNK_READ_INVALID")
    try:
        ciphertext = worker.encrypt_chunk(
            recipient=transfer_session.binding.destination_age_recipient,
            plaintext=plaintext,
        )
    except Exception:
        _fail("CHUNKED_BASE_BACKUP_PUBLISHER_AGE_ENCRYPTION_FAILED")
    if (
        not isinstance(ciphertext, bytes)
        or not ciphertext
        or len(ciphertext) > MAX_PHYSICAL_WAL_CHUNKED_BASE_BACKUP_CHUNK_CIPHERTEXT_BYTES
        or len(ciphertext) > permit.max_ciphertext_bytes
    ):
        _fail("CHUNKED_BASE_BACKUP_PUBLISHER_CIPHERTEXT_INVALID")
    upload = _publish_exact_chunk(worker=worker, permit=permit, ciphertext=ciphertext)
    chunk = PhysicalWalChunkedBaseBackupChunk(
        index=permit.chunk_index,
        object_key=permit.object_key,
        version_id=upload.version_id,
        ciphertext_sha256=upload.ciphertext_sha256,
        ciphertext_bytes=upload.ciphertext_bytes,
        plaintext_sha256=hashlib.sha256(plaintext).hexdigest(),
        plaintext_bytes=len(plaintext),
        age_recipient=transfer_session.binding.destination_age_recipient,
    )
    completed_at = _checked_clock(clock)
    try:
        completion = worker.build_completion(
            permit=permit,
            chunk=chunk,
            completed_at=completed_at,
        )
    except Exception:
        _fail("CHUNKED_BASE_BACKUP_PUBLISHER_SOURCE_COMPLETION_FAILED")
    if not isinstance(completion, (bytes, Mapping)):
        _fail("CHUNKED_BASE_BACKUP_PUBLISHER_SOURCE_COMPLETION_INVALID")
    accepted_at = _checked_clock(clock)
    try:
        commitment = witness_mediator.accept_chunk_completion(
            transfer_session=transfer_session,
            chunk_permit=permit,
            completion=completion,
            now=accepted_at,
        )
        commitment = require_verified_physical_wal_chunked_base_backup_chunk_commitment(
            commitment, now=accepted_at
        )
    except PhysicalWalChunkedBaseBackupPublisherRuntimeError:
        raise
    except Exception:
        # The immutable object may exist, but it is deliberately left orphaned.
        _fail("CHUNKED_BASE_BACKUP_PUBLISHER_WITNESS_ACCEPTANCE_FAILED")
    if (
        commitment.chunk != chunk
        or commitment.completion.permit.canonical_permit != permit.canonical_permit
        or commitment.completion.permit.permit_nonce != permit.permit_nonce
    ):
        _fail("CHUNKED_BASE_BACKUP_PUBLISHER_WITNESS_ACCEPTANCE_INVALID")
    return _PublishedChunk(permit=permit, chunk=chunk, commitment=commitment)


def execute_root_owned_physical_wal_chunked_base_backup_publisher(
    config: RootOwnedPhysicalWalChunkedBaseBackupPublisherConfig,
    *,
    chunk_worker_factory: PhysicalWalChunkedBaseBackupChunkWorkerFactory,
    witness_mediator: PhysicalWalChunkedBaseBackupWitnessMediator,
    checkpoint_sink: PhysicalWalChunkedBaseBackupPublisherCheckpointSink,
    clock: Callable[[], datetime],
    resume_checkpoint: bytes | PhysicalWalChunkedBaseBackupPublisherCheckpoint | None = None,
) -> PhysicalWalChunkedBaseBackupPublisherRuntimeResult:
    """Publish one staged base backup through the isolated default-off v2 path.

    No generic exception is exposed as an authority.  In particular a stale
    permit after a successful PUT, a source signing failure, a partial batch,
    or an expired finalization yields an exception and never a manifest.
    """

    facts = _config_facts(config, require_enabled=True)
    # A checkpoint is evidence for a *separate*, reconciled resume adapter.
    # It must never fall through to ``open_transfer_session`` here.
    _reject_resume_checkpoint(resume_checkpoint)
    if (
        chunk_worker_factory is None
        or witness_mediator is None
        or checkpoint_sink is None
        or getattr(witness_mediator, "parallel_safe", None) is not True
        or not callable(getattr(witness_mediator, "open_transfer_session", None))
        or not callable(getattr(witness_mediator, "reserve_chunk_permits", None))
        or not callable(getattr(witness_mediator, "accept_chunk_completion", None))
        or not callable(getattr(witness_mediator, "begin_accepted_chunk_set", None))
        or not callable(getattr(witness_mediator, "append_accepted_chunk", None))
        or not callable(getattr(witness_mediator, "issue_finalization_permit", None))
        or not callable(getattr(witness_mediator, "build_finalized_manifest", None))
        or not callable(getattr(witness_mediator, "issue_receiver_handoff_receipt", None))
    ):
        _fail("CHUNKED_BASE_BACKUP_PUBLISHER_DEPENDENCY_INVALID")
    lineage_now = _checked_clock(clock)
    try:
        lineage = build_physical_wal_chunked_base_backup_lineage_envelope(
            source_binding=facts.source_base_backup_binding,
            transfer_binding=facts.binding,
            now=lineage_now,
        )
        lineage = require_verified_physical_wal_chunked_base_backup_lineage_envelope(
            lineage,
            transfer_binding=facts.binding,
            now=lineage_now,
        )
    except Exception:
        _fail("CHUNKED_BASE_BACKUP_PUBLISHER_SOURCE_LINEAGE_INVALID")

    root_fd, file_fd, initial_stat = _open_fixed_staged_file(facts)
    session: VerifiedPhysicalWalChunkedBaseBackupTransferSession | None = None
    source_hash = ""
    source_bytes = 0
    chunk_index = 0
    commitments: dict[int, VerifiedPhysicalWalChunkedBaseBackupChunkCommitment] = {}
    issued_permits: list[VerifiedPhysicalWalChunkedBaseBackupChunkPermit] = []
    try:
        with os.fdopen(file_fd, "rb", closefd=True) as staged:
            file_fd = -1
            # No session, permit, or PUT exists until the staged file has been
            # read through its pinned descriptor and matched to verified source
            # lineage hash/bytes.
            source_hash, source_bytes = _hash_and_rewind_verified_staged_file(
                staged=staged,
                initial_stat=initial_stat,
                facts=facts,
                lineage_envelope=lineage,
            )
            session_now = _checked_clock(clock)
            try:
                candidate_session = witness_mediator.open_transfer_session(
                    binding=facts.binding,
                    now=session_now,
                )
                session = require_verified_physical_wal_chunked_base_backup_transfer_session(
                    candidate_session,
                    now=session_now,
                )
            except Exception:
                _fail("CHUNKED_BASE_BACKUP_PUBLISHER_SESSION_INVALID")
            if session.binding != facts.binding:
                _fail("CHUNKED_BASE_BACKUP_PUBLISHER_SESSION_INVALID")
            _persist_checkpoint(
                checkpoint_sink=checkpoint_sink,
                session=session,
                lineage_sha256=lineage.lineage_sha256,
                staged_plaintext_sha256=source_hash,
                staged_plaintext_bytes=source_bytes,
                issued_permits=(),
                accepted_commitments=(),
            )
            while True:
                batch: list[tuple[int, bytes]] = []
                while len(batch) < facts.maximum_in_flight:
                    try:
                        data = staged.read(facts.chunk_bytes)
                    except OSError:
                        _fail("CHUNKED_BASE_BACKUP_PUBLISHER_STAGED_FILE_UNAVAILABLE")
                    if type(data) is not bytes or len(data) > facts.chunk_bytes:
                        _fail("CHUNKED_BASE_BACKUP_PUBLISHER_CHUNK_READ_INVALID")
                    if not data:
                        break
                    if chunk_index >= MAX_PHYSICAL_WAL_CHUNKED_BASE_BACKUP_CHUNKS:
                        _fail("CHUNKED_BASE_BACKUP_PUBLISHER_CHUNK_COUNT_EXCEEDED")
                    batch.append((chunk_index, data))
                    chunk_index += 1
                if not batch:
                    break
                issued_at = _checked_clock(clock)
                indexes = tuple(index for index, _data in batch)
                try:
                    permits = witness_mediator.reserve_chunk_permits(
                        transfer_session=session,
                        chunk_indexes=indexes,
                        now=issued_at,
                    )
                    permits_by_index = _validated_permits(
                        permits=permits,
                        transfer_session=session,
                        expected_indexes=indexes,
                        now=issued_at,
                    )
                except PhysicalWalChunkedBaseBackupPublisherRuntimeError:
                    raise
                except Exception:
                    _fail("CHUNKED_BASE_BACKUP_PUBLISHER_PERMIT_SET_INVALID")
                ordered_permits = tuple(permits_by_index[index] for index in indexes)
                issued_permits.extend(ordered_permits)
                _persist_checkpoint(
                    checkpoint_sink=checkpoint_sink,
                    session=session,
                    lineage_sha256=lineage.lineage_sha256,
                    staged_plaintext_sha256=source_hash,
                    staged_plaintext_bytes=source_bytes,
                    issued_permits=issued_permits,
                    accepted_commitments=tuple(commitments[index] for index in sorted(commitments)),
                )
                workers_by_index = _open_isolated_chunk_workers(
                    worker_factory=chunk_worker_factory,
                    permits_by_index=permits_by_index,
                )
                with ThreadPoolExecutor(max_workers=len(batch), thread_name_prefix="physical-wal-v2-chunk") as executor:
                    pending = {
                        executor.submit(
                            _publish_one,
                            permit=permits_by_index[index],
                            plaintext=data,
                            worker=workers_by_index[index],
                            witness_mediator=witness_mediator,
                            transfer_session=session,
                            clock=clock,
                        ): index
                        for index, data in batch
                    }
                    for future in as_completed(pending):
                        index = pending[future]
                        try:
                            published = future.result()
                        except PhysicalWalChunkedBaseBackupPublisherRuntimeError:
                            raise
                        except Exception:
                            _fail("CHUNKED_BASE_BACKUP_PUBLISHER_CHUNK_SIDE_EFFECT_FAILED")
                        if published.chunk.index != index or index in commitments:
                            _fail("CHUNKED_BASE_BACKUP_PUBLISHER_WITNESS_ACCEPTANCE_INVALID")
                        commitments[index] = published.commitment
                        _persist_checkpoint(
                            checkpoint_sink=checkpoint_sink,
                            session=session,
                            lineage_sha256=lineage.lineage_sha256,
                            staged_plaintext_sha256=source_hash,
                            staged_plaintext_bytes=source_bytes,
                            issued_permits=issued_permits,
                            accepted_commitments=tuple(
                                commitments[committed_index]
                                for committed_index in sorted(commitments)
                            ),
                        )
            final_stat = os.fstat(staged.fileno())
        if (
            not _same_staged_stat(initial_stat, final_stat)
            or source_bytes != initial_stat.st_size
            or not commitments
        ):
            _fail("CHUNKED_BASE_BACKUP_PUBLISHER_STAGED_FILE_RACED_OR_EMPTY")
    finally:
        if file_fd >= 0:
            os.close(file_fd)
        os.close(root_fd)

    if session is None:  # Defensive: no control-plane action can follow this.
        _fail("CHUNKED_BASE_BACKUP_PUBLISHER_SESSION_INVALID")
    if (
        source_hash != lineage.snapshot_sha256
        or source_bytes != lineage.snapshot_bytes
        or source_hash == ""
    ):
        _fail("CHUNKED_BASE_BACKUP_PUBLISHER_STAGED_SNAPSHOT_MISMATCH")

    accepted_at = _checked_clock(clock)
    try:
        accepted = witness_mediator.begin_accepted_chunk_set(transfer_session=session, now=accepted_at)
        accepted = require_verified_physical_wal_chunked_base_backup_witness_accepted_chunk_set(accepted, now=accepted_at)
        for index in range(chunk_index):
            if index not in commitments:
                _fail("CHUNKED_BASE_BACKUP_PUBLISHER_COMMITMENT_GAP")
            accepted = witness_mediator.append_accepted_chunk(
                accepted_chunk_set=accepted,
                chunk_commitment=commitments[index],
                now=_checked_clock(clock),
            )
            accepted = require_verified_physical_wal_chunked_base_backup_witness_accepted_chunk_set(
                accepted, now=_checked_clock(clock)
            )
    except PhysicalWalChunkedBaseBackupPublisherRuntimeError:
        raise
    except Exception:
        _fail("CHUNKED_BASE_BACKUP_PUBLISHER_CONTIGUOUS_ACCEPTANCE_FAILED")

    finalized_at = _checked_clock(clock)
    try:
        finalization = witness_mediator.issue_finalization_permit(
            transfer_session=session,
            accepted_chunk_set=accepted,
            total_plaintext_sha256=source_hash,
            total_plaintext_bytes=source_bytes,
            now=finalized_at,
        )
        finalization = require_verified_physical_wal_chunked_base_backup_finalization_permit(
            finalization, now=finalized_at
        )
        manifest = witness_mediator.build_finalized_manifest(
            finalization_permit=finalization,
            accepted_chunk_set=accepted,
            now=_checked_clock(clock),
        )
        manifest = require_verified_physical_wal_chunked_base_backup_manifest(
            manifest, now=_checked_clock(clock)
        )
        if manifest.finalization_permit.canonical_finalization_permit != finalization.canonical_finalization_permit:
            _fail("CHUNKED_BASE_BACKUP_PUBLISHER_FINALIZATION_FAILED")
        if (
            manifest.total_plaintext_sha256 != lineage.snapshot_sha256
            or manifest.total_plaintext_bytes != lineage.snapshot_bytes
            or finalization.total_plaintext_sha256 != lineage.snapshot_sha256
            or finalization.total_plaintext_bytes != lineage.snapshot_bytes
        ):
            _fail("CHUNKED_BASE_BACKUP_PUBLISHER_FINALIZATION_FAILED")
    except PhysicalWalChunkedBaseBackupPublisherRuntimeError:
        raise
    except Exception:
        # A caller does not receive a manifest after an expired/invalid finalization.
        _fail("CHUNKED_BASE_BACKUP_PUBLISHER_FINALIZATION_FAILED")
    handoff_at = _checked_clock(clock)
    try:
        handoff = witness_mediator.issue_receiver_handoff_receipt(
            manifest=manifest,
            lineage_envelope=lineage,
            now=handoff_at,
        )
        handoff = require_verified_physical_wal_chunked_base_backup_handoff_receipt(
            handoff, manifest=manifest, now=handoff_at
        )
    except PhysicalWalChunkedBaseBackupPublisherRuntimeError:
        raise
    except Exception:
        _fail("CHUNKED_BASE_BACKUP_PUBLISHER_HANDOFF_RECEIPT_FAILED")
    return PhysicalWalChunkedBaseBackupPublisherRuntimeResult(
        manifest=manifest,
        receiver_handoff_receipt=handoff,
        staged_plaintext_sha256=source_hash,
        staged_plaintext_bytes=source_bytes,
        uploaded_chunk_count=chunk_index,
    )
