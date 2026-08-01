"""Fail-closed reconciliation evidence for a future v2 resume adapter.

The current v2 transfer protocol deliberately binds every immutable object,
permit and Witness commitment to one transfer-session identity.  A process
that has crashed must therefore *not* treat a publisher checkpoint as
permission to open a new session and silently re-upload or reuse old work:
the old accepted chunks cannot be placed in a new-session manifest by the
existing protocol.

This module is the narrow, non-operational seam for a later Witness protocol
extension.  It reads one durable, root-owned checkpoint, verifies all of the
historic signed evidence (including source completions), re-reads the exact
Witness commitment and exact immutable Object Storage version for every
chunk, then requires an entirely fresh, disjoint session/permit plan.  Its
opaque result is evidence only.  No existing publisher executor accepts it,
and this module has no upload, list, delete, fallback, direct WebApp, or
network implementation surface.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any, Protocol
from weakref import WeakKeyDictionary

from core.append_only_sync_delta_batch import (
    CAMPAIGN_ID_RE,
    RELEASE_SHA_RE,
    SHA256_RE,
    VERSION_ID_RE,
    canonical_json_bytes,
)
from core.object_delta_transport_binding import AGE_RECIPIENT_RE
from core.physical_wal_chunked_base_backup_publisher_runtime import (
    PHYSICAL_WAL_CHUNKED_BASE_BACKUP_PUBLISHER_CHECKPOINT_SCHEMA,
)
from core.physical_wal_chunked_base_backup_transfer import (
    MAX_PHYSICAL_WAL_CHUNKED_BASE_BACKUP_CHUNKS,
    MAX_PHYSICAL_WAL_CHUNKED_BASE_BACKUP_CHUNK_CIPHERTEXT_BYTES,
    PHYSICAL_WAL_CHUNKED_BASE_BACKUP_TRANSPORT_PLANE,
    PhysicalWalChunkedBaseBackupBinding,
    VerifiedPhysicalWalChunkedBaseBackupChunkCommitment,
    VerifiedPhysicalWalChunkedBaseBackupChunkPermit,
    VerifiedPhysicalWalChunkedBaseBackupTransferSession,
    require_verified_physical_wal_chunked_base_backup_chunk_permit,
    require_verified_physical_wal_chunked_base_backup_transfer_session,
    verify_physical_wal_chunked_base_backup_chunk_commitment,
    verify_physical_wal_chunked_base_backup_chunk_completion,
    verify_physical_wal_chunked_base_backup_chunk_permit,
    verify_physical_wal_chunked_base_backup_transfer_session,
)


__all__ = (
    "MAX_PHYSICAL_WAL_CHUNKED_BASE_BACKUP_RESUME_CHECKPOINT_BYTES",
    "PHYSICAL_WAL_CHUNKED_BASE_BACKUP_RESUME_ADMISSION_DEFAULT_ENABLED",
    "PHYSICAL_WAL_CHUNKED_BASE_BACKUP_RESUME_ADMISSION_SCHEMA",
    "PhysicalWalChunkedBaseBackupFreshWitnessResumePlan",
    "PhysicalWalChunkedBaseBackupResumeAdmissionError",
    "PhysicalWalChunkedBaseBackupResumeExactObjectHeadObservation",
    "PhysicalWalChunkedBaseBackupResumeReconciler",
    "PhysicalWalChunkedBaseBackupResumeScope",
    "RootOwnedPhysicalWalChunkedBaseBackupResumeAdmissionConfig",
    "VerifiedPhysicalWalChunkedBaseBackupResumeAdmission",
    "admit_root_owned_physical_wal_chunked_base_backup_resume",
    "require_verified_physical_wal_chunked_base_backup_resume_admission",
    "validate_root_owned_physical_wal_chunked_base_backup_resume_admission_config",
)


PHYSICAL_WAL_CHUNKED_BASE_BACKUP_RESUME_ADMISSION_SCHEMA = (
    "gold-trade-physical-wal-chunked-base-backup-resume-admission-v2"
)
PHYSICAL_WAL_CHUNKED_BASE_BACKUP_RESUME_ADMISSION_DEFAULT_ENABLED = False
# This is intentionally bounded.  An overlarge/torn checkpoint is not resume
# authority; a future writer must split or seal its own ledger before it can
# be admitted here.
MAX_PHYSICAL_WAL_CHUNKED_BASE_BACKUP_RESUME_CHECKPOINT_BYTES = 32 * 1024 * 1024

_CHECKPOINT_FILENAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,255}$", re.ASCII)
_URL_OR_SECRET_RE = re.compile(
    r"(?i)(?:[a-z][a-z0-9+.-]*://|www\.|bearer\s+|access[_ -]?key|"
    r"authorization|credential|password|private[_ -]?key|secret|token)"
)
_MUTABLE_VERSION_IDS = frozenset({"null", "none", "latest", "current", "head"})
_CHECKPOINT_FIELDS = frozenset(
    {
        "schema",
        "session_id",
        "session_sha256",
        "binding_sha256",
        "lineage_sha256",
        "staged_plaintext_sha256",
        "staged_plaintext_bytes",
        "canonical_session_base64",
        "issued_permits",
        "accepted_commitments",
    }
)
_PERMIT_RECORD_FIELDS = frozenset(
    {"permit_id", "permit_sha256", "canonical_permit_base64"}
)
_COMMITMENT_RECORD_FIELDS = frozenset(
    {
        "chunk_index",
        "commitment_id",
        "commitment_sha256",
        "canonical_completion_base64",
        "canonical_commitment_base64",
    }
)
_CAPABILITY = object()


class PhysicalWalChunkedBaseBackupResumeAdmissionError(RuntimeError):
    """A historic checkpoint cannot be admitted for a future resume adapter."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class RootOwnedPhysicalWalChunkedBaseBackupResumeAdmissionConfig:
    """No-secret policy for one pre-existing, fsync-sealed checkpoint file.

    The writer of this file is outside this reader boundary and must append/
    seal it durably before requesting admission.  This reader never creates,
    rewrites, deletes, repairs, or selects a checkpoint.
    """

    schema: str = PHYSICAL_WAL_CHUNKED_BASE_BACKUP_RESUME_ADMISSION_SCHEMA
    checkpoint_root: Path | None = None
    checkpoint_filename: str = ""
    owner_uid: int = 0
    enabled: bool = PHYSICAL_WAL_CHUNKED_BASE_BACKUP_RESUME_ADMISSION_DEFAULT_ENABLED
    local_checkpoint_readback: str = "required"
    checkpoint_durability: str = "fsync-sealed-required"
    witness_durable_commitment_readback: str = "required"
    object_storage_exact_version_head: str = "required"
    fresh_witness_session_and_permits: str = "required"
    direct_site_control: str = "forbidden"
    object_storage_list: str = "forbidden"
    object_storage_delete: str = "forbidden"
    multipart_upload: str = "forbidden"
    v1_fallback: str = "forbidden"


@dataclass(frozen=True)
class PhysicalWalChunkedBaseBackupResumeScope:
    """Independently revalidated source snapshot expected by the checkpoint.

    A real adapter must obtain this from a fresh, root-owned staged-file hash
    pass; this evidence boundary accepts no path or raw source bytes.
    """

    transfer_binding: PhysicalWalChunkedBaseBackupBinding
    lineage_sha256: str
    staged_plaintext_sha256: str
    staged_plaintext_bytes: int


@dataclass(frozen=True)
class PhysicalWalChunkedBaseBackupFreshWitnessResumePlan:
    """Fresh Witness evidence required by any later operational adapter.

    It is deliberately not minted here.  The caller must obtain a new live
    session and new live permits through a durable Witness boundary.  Current
    v2 execution does not consume this plan.
    """

    transfer_session: VerifiedPhysicalWalChunkedBaseBackupTransferSession
    chunk_permits: tuple[VerifiedPhysicalWalChunkedBaseBackupChunkPermit, ...]


@dataclass(frozen=True)
class PhysicalWalChunkedBaseBackupResumeExactObjectHeadObservation:
    """The only remote Object Storage observation accepted by reconciliation."""

    object_key: str
    version_id: str
    ciphertext_sha256: str
    ciphertext_bytes: int


class PhysicalWalChunkedBaseBackupResumeReconciler(Protocol):
    """Narrow, read-only capability for exact historic evidence readback.

    The adapter must query the durable Witness ledger by the exact signed
    commitment identity and Object Storage only by the exact immutable
    (object_key, version_id) pair.  It intentionally has no list, delete,
    broad get, generic client, WebApp, or credential surface.
    """

    def read_exact_durable_chunk_commitment(
        self,
        *,
        previous_session: VerifiedPhysicalWalChunkedBaseBackupTransferSession,
        commitment_id: str,
        commitment_sha256: str,
    ) -> bytes: ...

    def head_exact_object_version(
        self,
        *,
        object_key: str,
        version_id: str,
    ) -> PhysicalWalChunkedBaseBackupResumeExactObjectHeadObservation: ...


@dataclass(frozen=True, eq=False)
class VerifiedPhysicalWalChunkedBaseBackupResumeAdmission:
    """Opaque reconciliation evidence; never current resume execution authority."""

    schema: str
    checkpoint_sha256: str
    scope_sha256: str
    previous_session_id: str
    previous_session_sha256: str
    previous_session_nonce: str
    committed_chunk_count: int
    committed_chunk_set_sha256: str
    fresh_session_id: str
    fresh_session_sha256: str
    fresh_session_nonce: str
    fresh_permit_ids: tuple[str, ...]
    fresh_permit_sha256s: tuple[str, ...]
    admitted_at: datetime
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("CHUNKED_BASE_BACKUP_RESUME_ADMISSION_SERIALIZATION_FORBIDDEN")


@dataclass(frozen=True)
class _ConfigFacts:
    checkpoint_root: Path
    checkpoint_filename: str
    owner_uid: int


@dataclass(frozen=True)
class _CheckpointFacts:
    raw_checkpoint: bytes
    checkpoint_sha256: str
    previous_session: VerifiedPhysicalWalChunkedBaseBackupTransferSession
    permits: tuple[VerifiedPhysicalWalChunkedBaseBackupChunkPermit, ...]
    commitments: tuple[VerifiedPhysicalWalChunkedBaseBackupChunkCommitment, ...]
    committed_chunk_set_sha256: str


@dataclass(frozen=True)
class _FreshPlanFacts:
    session: VerifiedPhysicalWalChunkedBaseBackupTransferSession
    permits: tuple[VerifiedPhysicalWalChunkedBaseBackupChunkPermit, ...]


@dataclass(frozen=True)
class _AdmissionFacts:
    checkpoint: _CheckpointFacts
    fresh_plan: _FreshPlanFacts
    scope_sha256: str
    now: datetime


@dataclass(frozen=True)
class _AdmissionState:
    config: RootOwnedPhysicalWalChunkedBaseBackupResumeAdmissionConfig
    scope: PhysicalWalChunkedBaseBackupResumeScope
    witness_public_key: bytes
    source_public_key: bytes
    fresh_plan: PhysicalWalChunkedBaseBackupFreshWitnessResumePlan
    reconciler: PhysicalWalChunkedBaseBackupResumeReconciler


_ADMISSION_STATES: WeakKeyDictionary[
    VerifiedPhysicalWalChunkedBaseBackupResumeAdmission, _AdmissionState
] = WeakKeyDictionary()


def _fail(code: str) -> None:
    raise PhysicalWalChunkedBaseBackupResumeAdmissionError(code)


def _utc(value: object, *, code: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        _fail(code)
    return value.astimezone(timezone.utc)


def _nonzero_sha256(value: object, *, code: str) -> str:
    if type(value) is not str or SHA256_RE.fullmatch(value) is None or value == "0" * 64:
        _fail(code)
    return value


def _positive_int(value: object, *, maximum: int, code: str) -> int:
    if type(value) is not int or not 1 <= value <= maximum:
        _fail(code)
    return value


def _safe_root(value: object) -> Path:
    if not isinstance(value, Path) or not value.is_absolute() or any(part in {".", ".."} for part in value.parts):
        _fail("CHUNKED_BASE_BACKUP_RESUME_ADMISSION_CHECKPOINT_ROOT_INVALID")
    text = str(value)
    if not text or text == "/" or len(text) > 4096 or _URL_OR_SECRET_RE.search(text) is not None:
        _fail("CHUNKED_BASE_BACKUP_RESUME_ADMISSION_CHECKPOINT_ROOT_INVALID")
    return value


def _config_facts(config: object, *, require_enabled: bool) -> _ConfigFacts:
    if type(config) is not RootOwnedPhysicalWalChunkedBaseBackupResumeAdmissionConfig:
        _fail("CHUNKED_BASE_BACKUP_RESUME_ADMISSION_CONFIG_INVALID")
    if (
        config.schema != PHYSICAL_WAL_CHUNKED_BASE_BACKUP_RESUME_ADMISSION_SCHEMA
        or type(config.enabled) is not bool
        or (require_enabled and config.enabled is not True)
        or type(config.owner_uid) is not int
        or config.owner_uid != 0
        or config.local_checkpoint_readback != "required"
        or config.checkpoint_durability != "fsync-sealed-required"
        or config.witness_durable_commitment_readback != "required"
        or config.object_storage_exact_version_head != "required"
        or config.fresh_witness_session_and_permits != "required"
        or config.direct_site_control != "forbidden"
        or config.object_storage_list != "forbidden"
        or config.object_storage_delete != "forbidden"
        or config.multipart_upload != "forbidden"
        or config.v1_fallback != "forbidden"
    ):
        _fail("CHUNKED_BASE_BACKUP_RESUME_ADMISSION_CONFIG_INVALID")
    filename = config.checkpoint_filename
    if (
        type(filename) is not str
        or _CHECKPOINT_FILENAME_RE.fullmatch(filename) is None
        or "/" in filename
        or "\\" in filename
        or _URL_OR_SECRET_RE.search(filename) is not None
    ):
        _fail("CHUNKED_BASE_BACKUP_RESUME_ADMISSION_CHECKPOINT_FILENAME_INVALID")
    return _ConfigFacts(checkpoint_root=_safe_root(config.checkpoint_root), checkpoint_filename=filename, owner_uid=0)


def validate_root_owned_physical_wal_chunked_base_backup_resume_admission_config(
    config: object,
    *,
    require_enabled: bool = True,
) -> None:
    """Validate policy only; no checkpoint or remote evidence is read."""

    _config_facts(config, require_enabled=require_enabled)


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("CHUNKED_BASE_BACKUP_RESUME_ADMISSION_CHECKPOINT_INVALID")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> None:
    _fail("CHUNKED_BASE_BACKUP_RESUME_ADMISSION_CHECKPOINT_INVALID")


def _canonical_payload(raw: object, *, code: str) -> dict[str, Any]:
    if not isinstance(raw, bytes) or not raw or len(raw) > MAX_PHYSICAL_WAL_CHUNKED_BASE_BACKUP_RESUME_CHECKPOINT_BYTES:
        _fail(code)
    try:
        text = raw.decode("utf-8", "strict")
        payload = json.loads(text, object_pairs_hook=_strict_object, parse_constant=_reject_json_constant)
        canonical = canonical_json_bytes(payload)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
        _fail(code)
    if canonical != raw or not isinstance(payload, Mapping):
        _fail(code)
    return dict(payload)


def _exact_mapping(value: object, *, fields: frozenset[str], code: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != fields:
        _fail(code)
    return dict(value)


def _base64_bytes(value: object, *, code: str) -> bytes:
    if type(value) is not str or not value or len(value) > MAX_PHYSICAL_WAL_CHUNKED_BASE_BACKUP_RESUME_CHECKPOINT_BYTES * 2:
        _fail(code)
    try:
        decoded = base64.b64decode(value.encode("ascii", "strict"), validate=True)
    except (UnicodeEncodeError, ValueError):
        _fail(code)
    if not decoded or base64.b64encode(decoded).decode("ascii") != value:
        _fail(code)
    return decoded


def _historic_timestamp(raw: bytes, *, field: str, code: str) -> datetime:
    payload = _canonical_payload(raw, code=code)
    value = payload.get(field)
    if type(value) is not str or not value or value != value.strip():
        _fail(code)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        _fail(code)
    if parsed.tzinfo is None:
        _fail(code)
    normalized = parsed.astimezone(timezone.utc)
    if value != normalized.isoformat():
        _fail(code)
    return normalized


def _binding_mapping(binding: PhysicalWalChunkedBaseBackupBinding) -> dict[str, object]:
    return {
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


def _binding_sha256(binding: object) -> str:
    if type(binding) is not PhysicalWalChunkedBaseBackupBinding:
        _fail("CHUNKED_BASE_BACKUP_RESUME_ADMISSION_SCOPE_INVALID")
    if (
        binding.source_site not in {"webapp_fi", "webapp_ir"}
        or binding.destination_site not in {"webapp_fi", "webapp_ir"}
        or binding.source_site == binding.destination_site
        or CAMPAIGN_ID_RE.fullmatch(binding.campaign_id) is None
        or RELEASE_SHA_RE.fullmatch(binding.release_sha) is None
        or binding.transport_plane != PHYSICAL_WAL_CHUNKED_BASE_BACKUP_TRANSPORT_PLANE
        or binding.direct_webapp_transport != "forbidden"
        or binding.object_storage_namespace not in {"physical-wal", "physical-failback"}
        or SHA256_RE.fullmatch(binding.route_commitment_sha256) is None
        or SHA256_RE.fullmatch(binding.four_role_binding_sha256) is None
        or AGE_RECIPIENT_RE.fullmatch(binding.destination_age_recipient) is None
        or binding.writer_term.writer_holder_site != binding.source_site
    ):
        _fail("CHUNKED_BASE_BACKUP_RESUME_ADMISSION_SCOPE_INVALID")
    try:
        payload = {
            "schema": "gold-trade-physical-wal-chunked-base-backup-publisher-binding-v2",
            **_binding_mapping(binding),
        }
        return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    except (AttributeError, TypeError, ValueError):
        _fail("CHUNKED_BASE_BACKUP_RESUME_ADMISSION_SCOPE_INVALID")


def _scope_facts(scope: object) -> tuple[PhysicalWalChunkedBaseBackupResumeScope, str]:
    if type(scope) is not PhysicalWalChunkedBaseBackupResumeScope:
        _fail("CHUNKED_BASE_BACKUP_RESUME_ADMISSION_SCOPE_REQUIRED")
    binding_sha = _binding_sha256(scope.transfer_binding)
    lineage = _nonzero_sha256(scope.lineage_sha256, code="CHUNKED_BASE_BACKUP_RESUME_ADMISSION_SCOPE_INVALID")
    snapshot_sha = _nonzero_sha256(
        scope.staged_plaintext_sha256,
        code="CHUNKED_BASE_BACKUP_RESUME_ADMISSION_SCOPE_INVALID",
    )
    snapshot_bytes = _positive_int(
        scope.staged_plaintext_bytes,
        maximum=2 * 1024 * 1024 * 1024 * 1024,
        code="CHUNKED_BASE_BACKUP_RESUME_ADMISSION_SCOPE_INVALID",
    )
    try:
        digest = hashlib.sha256(
            canonical_json_bytes(
                {
                    "schema": PHYSICAL_WAL_CHUNKED_BASE_BACKUP_RESUME_ADMISSION_SCHEMA,
                    "binding_sha256": binding_sha,
                    "lineage_sha256": lineage,
                    "staged_plaintext_sha256": snapshot_sha,
                    "staged_plaintext_bytes": snapshot_bytes,
                }
            )
        ).hexdigest()
    except (TypeError, ValueError):  # pragma: no cover - normalized above.
        _fail("CHUNKED_BASE_BACKUP_RESUME_ADMISSION_SCOPE_INVALID")
    return scope, digest


def _open_secure_root(path: Path, *, owner_uid: int) -> int:
    if os.geteuid() != 0:
        _fail("CHUNKED_BASE_BACKUP_RESUME_ADMISSION_ROOT_REQUIRED")
    try:
        listed = os.lstat(path)
    except OSError:
        _fail("CHUNKED_BASE_BACKUP_RESUME_ADMISSION_CHECKPOINT_ROOT_UNAVAILABLE")
    if (
        stat.S_ISLNK(listed.st_mode)
        or not stat.S_ISDIR(listed.st_mode)
        or listed.st_uid != owner_uid
        or stat.S_IMODE(listed.st_mode) != 0o700
    ):
        _fail("CHUNKED_BASE_BACKUP_RESUME_ADMISSION_CHECKPOINT_ROOT_UNSAFE")
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
        observed = os.fstat(fd)
    except OSError:
        _fail("CHUNKED_BASE_BACKUP_RESUME_ADMISSION_CHECKPOINT_ROOT_UNSAFE")
    if (
        observed.st_dev != listed.st_dev
        or observed.st_ino != listed.st_ino
        or not stat.S_ISDIR(observed.st_mode)
        or observed.st_uid != owner_uid
        or stat.S_IMODE(observed.st_mode) != 0o700
    ):
        os.close(fd)
        _fail("CHUNKED_BASE_BACKUP_RESUME_ADMISSION_CHECKPOINT_ROOT_RACED")
    return fd


def _same_file_stat(left: os.stat_result, right: os.stat_result) -> bool:
    return (
        left.st_dev == right.st_dev
        and left.st_ino == right.st_ino
        and left.st_size == right.st_size
        and left.st_mtime_ns == right.st_mtime_ns
        and left.st_ctime_ns == right.st_ctime_ns
        and left.st_uid == right.st_uid
        and stat.S_ISREG(right.st_mode)
        and stat.S_IMODE(right.st_mode) == 0o600
        and right.st_nlink == 1
    )


def _read_durable_checkpoint(facts: _ConfigFacts) -> bytes:
    root_fd = _open_secure_root(facts.checkpoint_root, owner_uid=facts.owner_uid)
    file_fd = -1
    try:
        try:
            listed = os.stat(facts.checkpoint_filename, dir_fd=root_fd, follow_symlinks=False)
            flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
            file_fd = os.open(facts.checkpoint_filename, flags, dir_fd=root_fd)
            initial = os.fstat(file_fd)
        except OSError:
            _fail("CHUNKED_BASE_BACKUP_RESUME_ADMISSION_CHECKPOINT_UNAVAILABLE")
        if (
            stat.S_ISLNK(listed.st_mode)
            or not stat.S_ISREG(initial.st_mode)
            or initial.st_dev != listed.st_dev
            or initial.st_ino != listed.st_ino
            or initial.st_uid != facts.owner_uid
            or stat.S_IMODE(initial.st_mode) != 0o600
            or initial.st_nlink != 1
            or not 1 <= initial.st_size <= MAX_PHYSICAL_WAL_CHUNKED_BASE_BACKUP_RESUME_CHECKPOINT_BYTES
        ):
            _fail("CHUNKED_BASE_BACKUP_RESUME_ADMISSION_CHECKPOINT_UNSAFE")
        chunks: list[bytes] = []
        remaining = initial.st_size + 1
        while remaining:
            try:
                data = os.read(file_fd, min(1024 * 1024, remaining))
            except OSError:
                _fail("CHUNKED_BASE_BACKUP_RESUME_ADMISSION_CHECKPOINT_UNAVAILABLE")
            if not data:
                break
            chunks.append(data)
            remaining -= len(data)
        raw = b"".join(chunks)
        try:
            final = os.fstat(file_fd)
        except OSError:
            _fail("CHUNKED_BASE_BACKUP_RESUME_ADMISSION_CHECKPOINT_UNAVAILABLE")
        if not _same_file_stat(initial, final) or len(raw) != initial.st_size:
            _fail("CHUNKED_BASE_BACKUP_RESUME_ADMISSION_CHECKPOINT_RACED")
        return raw
    finally:
        if file_fd >= 0:
            os.close(file_fd)
        os.close(root_fd)


def _checkpoint_payload(raw: bytes) -> dict[str, Any]:
    payload = _canonical_payload(raw, code="CHUNKED_BASE_BACKUP_RESUME_ADMISSION_CHECKPOINT_INVALID")
    if set(payload) != _CHECKPOINT_FIELDS:
        _fail("CHUNKED_BASE_BACKUP_RESUME_ADMISSION_CHECKPOINT_INVALID")
    if payload["schema"] != PHYSICAL_WAL_CHUNKED_BASE_BACKUP_PUBLISHER_CHECKPOINT_SCHEMA:
        _fail("CHUNKED_BASE_BACKUP_RESUME_ADMISSION_CHECKPOINT_SCHEMA_UNSUPPORTED")
    return payload


def _record_list(value: object, *, code: str) -> list[dict[str, Any]]:
    if type(value) is not list or len(value) > MAX_PHYSICAL_WAL_CHUNKED_BASE_BACKUP_CHUNKS:
        _fail(code)
    return [dict(item) if isinstance(item, Mapping) else _fail(code) for item in value]


def _field_from_canonical(raw: bytes, *, field: str, code: str) -> object:
    return _canonical_payload(raw, code=code).get(field)


def _checkpoint_facts(
    *,
    raw: bytes,
    scope: PhysicalWalChunkedBaseBackupResumeScope,
    witness_public_key: bytes,
    source_public_key: bytes,
    now: datetime,
) -> _CheckpointFacts:
    payload = _checkpoint_payload(raw)
    binding_sha = _binding_sha256(scope.transfer_binding)
    if (
        payload["binding_sha256"] != binding_sha
        or payload["lineage_sha256"] != scope.lineage_sha256
        or payload["staged_plaintext_sha256"] != scope.staged_plaintext_sha256
        or payload["staged_plaintext_bytes"] != scope.staged_plaintext_bytes
    ):
        _fail("CHUNKED_BASE_BACKUP_RESUME_ADMISSION_CHECKPOINT_SCOPE_MISMATCH")
    session_raw = _base64_bytes(
        payload["canonical_session_base64"],
        code="CHUNKED_BASE_BACKUP_RESUME_ADMISSION_CHECKPOINT_INVALID",
    )
    session_issued = _historic_timestamp(
        session_raw,
        field="issued_at",
        code="CHUNKED_BASE_BACKUP_RESUME_ADMISSION_CHECKPOINT_INVALID",
    )
    try:
        previous_session = verify_physical_wal_chunked_base_backup_transfer_session(
            transfer_session=session_raw,
            expected_binding=scope.transfer_binding,
            expected_witness_public_key=witness_public_key,
            now=session_issued,
        )
    except Exception:
        _fail("CHUNKED_BASE_BACKUP_RESUME_ADMISSION_CHECKPOINT_FOREIGN_SESSION")
    session_sha = hashlib.sha256(previous_session.canonical_session).hexdigest()
    if payload["session_id"] != previous_session.session_id or payload["session_sha256"] != session_sha:
        _fail("CHUNKED_BASE_BACKUP_RESUME_ADMISSION_CHECKPOINT_FOREIGN_SESSION")

    permit_records = _record_list(
        payload["issued_permits"],
        code="CHUNKED_BASE_BACKUP_RESUME_ADMISSION_CHECKPOINT_INVALID",
    )
    if not permit_records:
        _fail("CHUNKED_BASE_BACKUP_RESUME_ADMISSION_CHECKPOINT_PARTIAL_OR_NONCONTIGUOUS")
    permits_by_id: dict[str, VerifiedPhysicalWalChunkedBaseBackupChunkPermit] = {}
    permit_ids: list[str] = []
    permit_nonces: list[str] = []
    permit_indexes: set[int] = set()
    for record in permit_records:
        item = _exact_mapping(
            record,
            fields=_PERMIT_RECORD_FIELDS,
            code="CHUNKED_BASE_BACKUP_RESUME_ADMISSION_CHECKPOINT_INVALID",
        )
        permit_raw = _base64_bytes(
            item["canonical_permit_base64"],
            code="CHUNKED_BASE_BACKUP_RESUME_ADMISSION_CHECKPOINT_INVALID",
        )
        permit_issued = _historic_timestamp(
            permit_raw,
            field="issued_at",
            code="CHUNKED_BASE_BACKUP_RESUME_ADMISSION_CHECKPOINT_PERMIT_INVALID",
        )
        try:
            permit = verify_physical_wal_chunked_base_backup_chunk_permit(
                chunk_permit=permit_raw,
                transfer_session=previous_session,
                expected_witness_public_key=witness_public_key,
                now=permit_issued,
                consumed_permit_ids=permit_ids,
                consumed_permit_nonces=permit_nonces,
                reserved_chunk_indexes=permit_indexes,
            )
        except Exception:
            _fail("CHUNKED_BASE_BACKUP_RESUME_ADMISSION_CHECKPOINT_PERMIT_INVALID")
        if (
            item["permit_id"] != permit.permit_id
            or item["permit_sha256"] != hashlib.sha256(permit.canonical_permit).hexdigest()
            or permit.permit_id in permits_by_id
        ):
            _fail("CHUNKED_BASE_BACKUP_RESUME_ADMISSION_CHECKPOINT_PERMIT_INVALID")
        permit_ids.append(permit.permit_id)
        permit_nonces.append(permit.permit_nonce)
        permit_indexes.add(permit.chunk_index)
        permits_by_id[permit.permit_id] = permit
    if permit_ids != sorted(permit_ids) or sorted(permit_indexes) != list(range(len(permit_indexes))):
        _fail("CHUNKED_BASE_BACKUP_RESUME_ADMISSION_CHECKPOINT_PARTIAL_OR_NONCONTIGUOUS")
    permits = tuple(permits_by_id[item.permit_id] for item in sorted(permits_by_id.values(), key=lambda value: value.chunk_index))

    commitment_records = _record_list(
        payload["accepted_commitments"],
        code="CHUNKED_BASE_BACKUP_RESUME_ADMISSION_CHECKPOINT_INVALID",
    )
    if len(commitment_records) != len(permits):
        _fail("CHUNKED_BASE_BACKUP_RESUME_ADMISSION_CHECKPOINT_PARTIAL_OR_NONCONTIGUOUS")
    commitments: list[VerifiedPhysicalWalChunkedBaseBackupChunkCommitment] = []
    commitment_ids: list[str] = []
    commitment_nonces: list[str] = []
    commitment_indexes: set[int] = set()
    commitment_permit_ids: set[str] = set()
    prior_index = -1
    for record in commitment_records:
        item = _exact_mapping(
            record,
            fields=_COMMITMENT_RECORD_FIELDS,
            code="CHUNKED_BASE_BACKUP_RESUME_ADMISSION_CHECKPOINT_INVALID",
        )
        index = _positive_int(
            item["chunk_index"] + 1 if type(item["chunk_index"]) is int else 0,
            maximum=MAX_PHYSICAL_WAL_CHUNKED_BASE_BACKUP_CHUNKS,
            code="CHUNKED_BASE_BACKUP_RESUME_ADMISSION_CHECKPOINT_COMMITMENT_INVALID",
        ) - 1
        if index != prior_index + 1:
            _fail("CHUNKED_BASE_BACKUP_RESUME_ADMISSION_CHECKPOINT_PARTIAL_OR_NONCONTIGUOUS")
        prior_index = index
        completion_raw = _base64_bytes(
            item["canonical_completion_base64"],
            code="CHUNKED_BASE_BACKUP_RESUME_ADMISSION_CHECKPOINT_COMMITMENT_INVALID",
        )
        commitment_raw = _base64_bytes(
            item["canonical_commitment_base64"],
            code="CHUNKED_BASE_BACKUP_RESUME_ADMISSION_CHECKPOINT_COMMITMENT_INVALID",
        )
        permit_id = _field_from_canonical(
            commitment_raw,
            field="permit_id",
            code="CHUNKED_BASE_BACKUP_RESUME_ADMISSION_CHECKPOINT_COMMITMENT_INVALID",
        )
        permit = permits_by_id.get(permit_id) if type(permit_id) is str else None
        committed_at = _historic_timestamp(
            commitment_raw,
            field="committed_at",
            code="CHUNKED_BASE_BACKUP_RESUME_ADMISSION_CHECKPOINT_COMMITMENT_INVALID",
        )
        if permit is None or permit.permit_id in commitment_permit_ids:
            _fail("CHUNKED_BASE_BACKUP_RESUME_ADMISSION_CHECKPOINT_PARTIAL_OR_NONCONTIGUOUS")
        try:
            completion = verify_physical_wal_chunked_base_backup_chunk_completion(
                chunk_completion=completion_raw,
                chunk_permit=permit,
                expected_source_public_key=source_public_key,
                now=committed_at,
            )
            commitment = verify_physical_wal_chunked_base_backup_chunk_commitment(
                chunk_commitment=commitment_raw,
                chunk_completion=completion,
                expected_witness_public_key=witness_public_key,
                now=now,
                consumed_commitment_ids=commitment_ids,
                consumed_commitment_nonces=commitment_nonces,
                committed_chunk_indexes=commitment_indexes,
            )
        except Exception:
            _fail("CHUNKED_BASE_BACKUP_RESUME_ADMISSION_CHECKPOINT_COMMITMENT_INVALID")
        if (
            commitment.chunk.index != index
            or item["commitment_id"] != commitment.commitment_id
            or item["commitment_sha256"] != hashlib.sha256(commitment.canonical_commitment).hexdigest()
            or completion.canonical_completion != completion_raw
            or commitment.canonical_commitment != commitment_raw
        ):
            _fail("CHUNKED_BASE_BACKUP_RESUME_ADMISSION_CHECKPOINT_COMMITMENT_INVALID")
        commitment_ids.append(commitment.commitment_id)
        commitment_nonces.append(commitment.commitment_nonce)
        commitment_indexes.add(commitment.chunk.index)
        commitment_permit_ids.add(permit.permit_id)
        commitments.append(commitment)
    if (
        tuple(item.chunk.index for item in commitments) != tuple(range(len(commitments)))
        or commitment_permit_ids != set(permits_by_id)
        or sum(item.chunk.plaintext_bytes for item in commitments) != scope.staged_plaintext_bytes
    ):
        _fail("CHUNKED_BASE_BACKUP_RESUME_ADMISSION_CHECKPOINT_PARTIAL_OR_NONCONTIGUOUS")
    try:
        committed_set = hashlib.sha256(
            canonical_json_bytes(
                {
                    "schema": PHYSICAL_WAL_CHUNKED_BASE_BACKUP_RESUME_ADMISSION_SCHEMA,
                    "previous_session_sha256": session_sha,
                    "chunks": [
                        {
                            "index": commitment.chunk.index,
                            "commitment_id": commitment.commitment_id,
                            "commitment_sha256": hashlib.sha256(commitment.canonical_commitment).hexdigest(),
                            "object_key": commitment.chunk.object_key,
                            "version_id": commitment.chunk.version_id,
                            "ciphertext_sha256": commitment.chunk.ciphertext_sha256,
                            "ciphertext_bytes": commitment.chunk.ciphertext_bytes,
                            "plaintext_sha256": commitment.chunk.plaintext_sha256,
                            "plaintext_bytes": commitment.chunk.plaintext_bytes,
                        }
                        for commitment in commitments
                    ],
                }
            )
        ).hexdigest()
    except (TypeError, ValueError):  # pragma: no cover - normalized above.
        _fail("CHUNKED_BASE_BACKUP_RESUME_ADMISSION_CHECKPOINT_COMMITMENT_INVALID")
    return _CheckpointFacts(
        raw_checkpoint=raw,
        checkpoint_sha256=hashlib.sha256(raw).hexdigest(),
        previous_session=previous_session,
        permits=permits,
        commitments=tuple(commitments),
        committed_chunk_set_sha256=committed_set,
    )


def _fresh_plan_facts(
    *,
    plan: object,
    checkpoint: _CheckpointFacts,
    scope: PhysicalWalChunkedBaseBackupResumeScope,
    witness_public_key: bytes,
    now: datetime,
) -> _FreshPlanFacts:
    if type(plan) is not PhysicalWalChunkedBaseBackupFreshWitnessResumePlan:
        _fail("CHUNKED_BASE_BACKUP_RESUME_ADMISSION_FRESH_PLAN_REQUIRED")
    if type(plan.chunk_permits) is not tuple:
        _fail("CHUNKED_BASE_BACKUP_RESUME_ADMISSION_FRESH_PLAN_REQUIRED")
    try:
        fresh_session = require_verified_physical_wal_chunked_base_backup_transfer_session(
            plan.transfer_session,
            now=now,
        )
    except Exception:
        _fail("CHUNKED_BASE_BACKUP_RESUME_ADMISSION_FRESH_SESSION_INVALID")
    previous = checkpoint.previous_session
    if (
        fresh_session.binding != scope.transfer_binding
        or fresh_session.witness_public_key != witness_public_key
        or fresh_session.canonical_session == previous.canonical_session
        or fresh_session.session_id == previous.session_id
        or fresh_session.session_nonce == previous.session_nonce
        or fresh_session.issued_at <= previous.expires_at
    ):
        _fail("CHUNKED_BASE_BACKUP_RESUME_ADMISSION_FRESH_SESSION_NOT_FRESH")
    if len(plan.chunk_permits) != len(checkpoint.permits):
        _fail("CHUNKED_BASE_BACKUP_RESUME_ADMISSION_FRESH_PERMIT_SET_MISMATCH")
    prior_ids: set[str] = {item.permit_id for item in checkpoint.permits}
    prior_nonces: set[str] = {item.permit_nonce for item in checkpoint.permits}
    prior_keys: set[str] = {item.chunk.object_key for item in checkpoint.commitments}
    fresh: list[VerifiedPhysicalWalChunkedBaseBackupChunkPermit] = []
    fresh_ids: set[str] = set()
    fresh_nonces: set[str] = set()
    fresh_indexes: set[int] = set()
    for candidate in plan.chunk_permits:
        try:
            permit = require_verified_physical_wal_chunked_base_backup_chunk_permit(candidate, now=now)
        except Exception:
            _fail("CHUNKED_BASE_BACKUP_RESUME_ADMISSION_FRESH_PERMIT_INVALID")
        if (
            permit.session.canonical_session != fresh_session.canonical_session
            or permit.witness_public_key != witness_public_key
            or permit.permit_id in prior_ids
            or permit.permit_nonce in prior_nonces
            or permit.permit_id in fresh_ids
            or permit.permit_nonce in fresh_nonces
            or permit.chunk_index in fresh_indexes
            or permit.object_key in prior_keys
        ):
            _fail("CHUNKED_BASE_BACKUP_RESUME_ADMISSION_FRESH_PERMIT_REUSE")
        fresh.append(permit)
        fresh_ids.add(permit.permit_id)
        fresh_nonces.add(permit.permit_nonce)
        fresh_indexes.add(permit.chunk_index)
    if tuple(sorted(fresh_indexes)) != tuple(range(len(checkpoint.permits))):
        _fail("CHUNKED_BASE_BACKUP_RESUME_ADMISSION_FRESH_PERMIT_SET_MISMATCH")
    fresh.sort(key=lambda item: item.chunk_index)
    return _FreshPlanFacts(session=fresh_session, permits=tuple(fresh))


def _reconcile_exact_remote(
    *,
    reconciler: object,
    checkpoint: _CheckpointFacts,
) -> None:
    if (
        reconciler is None
        or not callable(getattr(reconciler, "read_exact_durable_chunk_commitment", None))
        or not callable(getattr(reconciler, "head_exact_object_version", None))
    ):
        _fail("CHUNKED_BASE_BACKUP_RESUME_ADMISSION_RECONCILER_INVALID")
    for commitment in checkpoint.commitments:
        commitment_sha = hashlib.sha256(commitment.canonical_commitment).hexdigest()
        try:
            durable_raw = reconciler.read_exact_durable_chunk_commitment(
                previous_session=checkpoint.previous_session,
                commitment_id=commitment.commitment_id,
                commitment_sha256=commitment_sha,
            )
        except PhysicalWalChunkedBaseBackupResumeAdmissionError:
            raise
        except Exception:
            _fail("CHUNKED_BASE_BACKUP_RESUME_ADMISSION_WITNESS_RECONCILIATION_INVALID")
        if type(durable_raw) is not bytes or durable_raw != commitment.canonical_commitment:
            _fail("CHUNKED_BASE_BACKUP_RESUME_ADMISSION_WITNESS_RECONCILIATION_MISMATCH")
        try:
            head = reconciler.head_exact_object_version(
                object_key=commitment.chunk.object_key,
                version_id=commitment.chunk.version_id,
            )
        except PhysicalWalChunkedBaseBackupResumeAdmissionError:
            raise
        except Exception:
            _fail("CHUNKED_BASE_BACKUP_RESUME_ADMISSION_OBJECT_RECONCILIATION_INVALID")
        if type(head) is not PhysicalWalChunkedBaseBackupResumeExactObjectHeadObservation:
            _fail("CHUNKED_BASE_BACKUP_RESUME_ADMISSION_OBJECT_RECONCILIATION_INVALID")
        if (
            head.object_key != commitment.chunk.object_key
            or type(head.version_id) is not str
            or VERSION_ID_RE.fullmatch(head.version_id) is None
            or head.version_id.casefold() in _MUTABLE_VERSION_IDS
            or head.version_id != commitment.chunk.version_id
            or head.ciphertext_sha256 != commitment.chunk.ciphertext_sha256
            or type(head.ciphertext_bytes) is not int
            or not 1 <= head.ciphertext_bytes <= MAX_PHYSICAL_WAL_CHUNKED_BASE_BACKUP_CHUNK_CIPHERTEXT_BYTES
            or head.ciphertext_bytes != commitment.chunk.ciphertext_bytes
        ):
            _fail("CHUNKED_BASE_BACKUP_RESUME_ADMISSION_OBJECT_RECONCILIATION_MISMATCH")


def _derive_facts(
    *,
    config: object,
    scope: object,
    witness_public_key: object,
    source_public_key: object,
    fresh_plan: object,
    reconciler: object,
    now: object,
) -> _AdmissionFacts:
    config_facts = _config_facts(config, require_enabled=True)
    typed_scope, scope_sha = _scope_facts(scope)
    observed_now = _utc(now, code="CHUNKED_BASE_BACKUP_RESUME_ADMISSION_CLOCK_INVALID")
    if not isinstance(witness_public_key, bytes) or len(witness_public_key) != 32:
        _fail("CHUNKED_BASE_BACKUP_RESUME_ADMISSION_WITNESS_KEY_INVALID")
    if not isinstance(source_public_key, bytes) or len(source_public_key) != 32:
        _fail("CHUNKED_BASE_BACKUP_RESUME_ADMISSION_SOURCE_KEY_INVALID")
    raw = _read_durable_checkpoint(config_facts)
    checkpoint = _checkpoint_facts(
        raw=raw,
        scope=typed_scope,
        witness_public_key=witness_public_key,
        source_public_key=source_public_key,
        now=observed_now,
    )
    _reconcile_exact_remote(reconciler=reconciler, checkpoint=checkpoint)
    fresh = _fresh_plan_facts(
        plan=fresh_plan,
        checkpoint=checkpoint,
        scope=typed_scope,
        witness_public_key=witness_public_key,
        now=observed_now,
    )
    return _AdmissionFacts(checkpoint=checkpoint, fresh_plan=fresh, scope_sha256=scope_sha, now=observed_now)


def admit_root_owned_physical_wal_chunked_base_backup_resume(
    config: RootOwnedPhysicalWalChunkedBaseBackupResumeAdmissionConfig,
    *,
    scope: PhysicalWalChunkedBaseBackupResumeScope,
    witness_public_key: bytes,
    source_public_key: bytes,
    fresh_plan: PhysicalWalChunkedBaseBackupFreshWitnessResumePlan,
    reconciler: PhysicalWalChunkedBaseBackupResumeReconciler,
    now: datetime,
) -> VerifiedPhysicalWalChunkedBaseBackupResumeAdmission:
    """Mint opaque reconciliation evidence, never an upload/restart authority.

    The existing publisher intentionally continues to reject ``resume_checkpoint``
    after this returns.  A later protocol extension must explicitly consume this
    opaque capability and provide a Witness-signed cross-session continuation
    contract before it may perform any Object Storage side effect.
    """

    facts = _derive_facts(
        config=config,
        scope=scope,
        witness_public_key=witness_public_key,
        source_public_key=source_public_key,
        fresh_plan=fresh_plan,
        reconciler=reconciler,
        now=now,
    )
    previous = facts.checkpoint.previous_session
    fresh = facts.fresh_plan
    result = VerifiedPhysicalWalChunkedBaseBackupResumeAdmission(
        schema=PHYSICAL_WAL_CHUNKED_BASE_BACKUP_RESUME_ADMISSION_SCHEMA,
        checkpoint_sha256=facts.checkpoint.checkpoint_sha256,
        scope_sha256=facts.scope_sha256,
        previous_session_id=previous.session_id,
        previous_session_sha256=hashlib.sha256(previous.canonical_session).hexdigest(),
        previous_session_nonce=previous.session_nonce,
        committed_chunk_count=len(facts.checkpoint.commitments),
        committed_chunk_set_sha256=facts.checkpoint.committed_chunk_set_sha256,
        fresh_session_id=fresh.session.session_id,
        fresh_session_sha256=hashlib.sha256(fresh.session.canonical_session).hexdigest(),
        fresh_session_nonce=fresh.session.session_nonce,
        fresh_permit_ids=tuple(item.permit_id for item in fresh.permits),
        fresh_permit_sha256s=tuple(
            hashlib.sha256(item.canonical_permit).hexdigest() for item in fresh.permits
        ),
        admitted_at=facts.now,
    )
    object.__setattr__(result, "_capability", _CAPABILITY)
    _ADMISSION_STATES[result] = _AdmissionState(
        config=config,
        scope=scope,
        witness_public_key=witness_public_key,
        source_public_key=source_public_key,
        fresh_plan=fresh_plan,
        reconciler=reconciler,
    )
    return result


def require_verified_physical_wal_chunked_base_backup_resume_admission(
    value: object,
    *,
    now: datetime,
) -> VerifiedPhysicalWalChunkedBaseBackupResumeAdmission:
    """Re-read checkpoint/remote evidence and revalidate fresh permits on use."""

    if (
        type(value) is not VerifiedPhysicalWalChunkedBaseBackupResumeAdmission
        or value._capability is not _CAPABILITY
        or value.schema != PHYSICAL_WAL_CHUNKED_BASE_BACKUP_RESUME_ADMISSION_SCHEMA
    ):
        _fail("CHUNKED_BASE_BACKUP_RESUME_ADMISSION_REQUIRED")
    state = _ADMISSION_STATES.get(value)
    if state is None:
        _fail("CHUNKED_BASE_BACKUP_RESUME_ADMISSION_REQUIRED")
    facts = _derive_facts(
        config=state.config,
        scope=state.scope,
        witness_public_key=state.witness_public_key,
        source_public_key=state.source_public_key,
        fresh_plan=state.fresh_plan,
        reconciler=state.reconciler,
        now=now,
    )
    previous = facts.checkpoint.previous_session
    fresh = facts.fresh_plan
    if (
        value.checkpoint_sha256 != facts.checkpoint.checkpoint_sha256
        or value.scope_sha256 != facts.scope_sha256
        or value.previous_session_id != previous.session_id
        or value.previous_session_sha256 != hashlib.sha256(previous.canonical_session).hexdigest()
        or value.previous_session_nonce != previous.session_nonce
        or value.committed_chunk_count != len(facts.checkpoint.commitments)
        or value.committed_chunk_set_sha256 != facts.checkpoint.committed_chunk_set_sha256
        or value.fresh_session_id != fresh.session.session_id
        or value.fresh_session_sha256 != hashlib.sha256(fresh.session.canonical_session).hexdigest()
        or value.fresh_session_nonce != fresh.session.session_nonce
        or value.fresh_permit_ids != tuple(item.permit_id for item in fresh.permits)
        or value.fresh_permit_sha256s
        != tuple(hashlib.sha256(item.canonical_permit).hexdigest() for item in fresh.permits)
    ):
        _fail("CHUNKED_BASE_BACKUP_RESUME_ADMISSION_TAMPERED")
    return value
