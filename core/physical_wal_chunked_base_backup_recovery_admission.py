"""Pure, fail-closed recovery admission for a staged v2 chunked base backup.

This is deliberately *not* a restore, PostgreSQL, Object Storage, age, or
network runtime.  It accepts the three already-verified v2 capabilities
(``manifest``, fresh Witness ``handoff``, and receiver staging result), then
performs a narrow root-owned local readback of the canonical stage receipt.
The local receipt is treated as evidence only after its path, ownership,
permissions, link count, canonical bytes, and every shared pin are checked.

The returned object is an opaque, process-local capability for a future
readiness/materialization boundary.  Requiring it repeats the local readback
and fresh Witness checks; serializing or constructing a look-alike therefore
does not turn staged files into recovery authority.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any
from weakref import WeakKeyDictionary

from core.append_only_sync_delta_batch import SHA256_RE, canonical_json_bytes
from core.physical_wal_chunked_base_backup_handoff_receipt import (
    VerifiedPhysicalWalChunkedBaseBackupHandoffReceipt,
    require_verified_physical_wal_chunked_base_backup_handoff_receipt,
)
from core.physical_wal_chunked_base_backup_manifest import (
    PhysicalWalChunkedBaseBackupManifestChunkSelector,
    VerifiedPhysicalWalChunkedBaseBackupManifest,
    require_verified_physical_wal_chunked_base_backup_manifest,
)
from core.physical_wal_chunked_base_backup_receiver_staging_runtime import (
    PHYSICAL_WAL_CHUNKED_BASE_BACKUP_RECEIVER_STAGING_SCHEMA,
    PHYSICAL_WAL_CHUNKED_BASE_BACKUP_RECEIVER_STAGING_STATUS,
    PhysicalWalChunkedBaseBackupReceiverStagingResult,
)
from core.physical_wal_chunked_base_backup_transfer import (
    MAX_PHYSICAL_WAL_CHUNKED_BASE_BACKUP_CHUNKS,
    PhysicalWalChunkedBaseBackupBinding,
    PhysicalWalChunkedBaseBackupTransferError,
    PhysicalWalChunkedBaseBackupWriterTerm,
    build_physical_wal_chunked_base_backup_binding,
)


__all__ = (
    "MAX_PHYSICAL_WAL_CHUNKED_BASE_BACKUP_RECOVERY_STAGE_RECEIPT_BYTES",
    "PHYSICAL_WAL_CHUNKED_BASE_BACKUP_RECOVERY_ADMISSION_DEFAULT_ENABLED",
    "PHYSICAL_WAL_CHUNKED_BASE_BACKUP_RECOVERY_ADMISSION_SCHEMA",
    "PhysicalWalChunkedBaseBackupRecoveryAdmissionError",
    "PhysicalWalChunkedBaseBackupRecoveryAdmissionScope",
    "RootOwnedPhysicalWalChunkedBaseBackupRecoveryAdmissionConfig",
    "VerifiedPhysicalWalChunkedBaseBackupRecoveryAdmission",
    "admit_root_owned_physical_wal_chunked_base_backup_recovery",
    "project_verified_physical_wal_chunked_base_backup_recovery_admission",
    "require_verified_physical_wal_chunked_base_backup_recovery_admission",
    "validate_root_owned_physical_wal_chunked_base_backup_recovery_admission_config",
)


PHYSICAL_WAL_CHUNKED_BASE_BACKUP_RECOVERY_ADMISSION_SCHEMA = (
    "gold-trade-physical-wal-chunked-base-backup-recovery-admission-v2"
)
PHYSICAL_WAL_CHUNKED_BASE_BACKUP_RECOVERY_ADMISSION_DEFAULT_ENABLED = False
# The receiver itself writes no larger receipt.  Keeping the same strict upper
# bound here avoids a smaller verifier silently rejecting a valid maximum-size
# receiver result, while the read loop never reads beyond the bound.
MAX_PHYSICAL_WAL_CHUNKED_BASE_BACKUP_RECOVERY_STAGE_RECEIPT_BYTES = 128 * 1024 * 1024
MAX_PHYSICAL_WAL_CHUNKED_BASE_BACKUP_RECOVERY_STAGE_READ_BYTES = 1024 * 1024

_SITE_RE = re.compile(r"^webapp_(?:fi|ir)$", re.ASCII)
_STAGE_DIRECTORY_RE = re.compile(r"^stage-[0-9a-f]{48}$", re.ASCII)
_GENERATION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,127}$", re.ASCII)
_SYSTEM_IDENTIFIER_RE = re.compile(r"^[1-9][0-9]{0,19}$", re.ASCII)
_LSN_RE = re.compile(
    r"^(?:0|[1-9A-F][0-9A-F]{0,7})/(?:0|[1-9A-F][0-9A-F]{0,7})$",
    re.ASCII,
)
_TRANSITION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$", re.ASCII)
_URL_OR_SECRET_RE = re.compile(
    r"(?i)(?:[a-z][a-z0-9+.-]*://|www\.|bearer\s+|access[_ -]?key|"
    r"authorization|credential|password|private[_ -]?key|secret|token)"
)
_STAGE_RECEIPT_FIELDS = frozenset(
    {
        "schema",
        "status",
        "receipt_id",
        "receipt_nonce",
        "manifest_id",
        "manifest_sha256",
        "binding_sha256",
        "session_sha256",
        "finalization_permit_sha256",
        "lineage_sha256",
        "snapshot_sha256",
        "snapshot_bytes",
        "total_plaintext_sha256",
        "total_plaintext_bytes",
        "chunk_count",
        "ledger_key_sha256",
        "chunks",
    }
)
_CAPABILITY = object()


class PhysicalWalChunkedBaseBackupRecoveryAdmissionError(ValueError):
    """A v2 staged base-backup cannot be admitted as recovery evidence."""


@dataclass(frozen=True)
class RootOwnedPhysicalWalChunkedBaseBackupRecoveryAdmissionConfig:
    """No-secret local readback policy; disabled unless explicitly enabled."""

    schema: str = PHYSICAL_WAL_CHUNKED_BASE_BACKUP_RECOVERY_ADMISSION_SCHEMA
    staging_root: Path | None = None
    receiver_site: str = ""
    owner_uid: int = 0
    enabled: bool = PHYSICAL_WAL_CHUNKED_BASE_BACKUP_RECOVERY_ADMISSION_DEFAULT_ENABLED
    local_stage_receipt_readback: str = "required"
    direct_site_control: str = "forbidden"
    remote_object_storage: str = "forbidden"
    v1_fallback: str = "forbidden"
    restore_or_promotion: str = "forbidden"


@dataclass(frozen=True)
class PhysicalWalChunkedBaseBackupRecoveryAdmissionScope:
    """Expected release route and WAL lineage for one recovery target.

    The scope is policy, not evidence.  Every field must exactly match the
    independently verified manifest/handoff capabilities before an admission
    can be minted.  It prevents a valid v2 result from another campaign,
    release, route, or WAL baseline being reused at this boundary.
    """

    transfer_binding: PhysicalWalChunkedBaseBackupBinding
    baseline_generation_id: str
    database_system_identifier: str
    timeline_id: int
    wal_segment_size_bytes: int
    baseline_wal_lsn: str
    wal_chain_start_lsn: str
    base_backup_end_lsn: str
    completion_attestation_sha256: str
    legacy_route_binding_sha256: str
    witness_transition_id: str


@dataclass(frozen=True, eq=False)
class VerifiedPhysicalWalChunkedBaseBackupRecoveryAdmission:
    """Opaque v2 staging evidence, never restore, replay, or writer authority."""

    schema: str
    receiver_site: str
    stage_directory_name: str
    stage_receipt_sha256: str
    scope_sha256: str
    receipt_id: str
    receipt_nonce: str
    manifest_id: str
    manifest_sha256: str
    binding_sha256: str
    session_sha256: str
    finalization_permit_id: str
    finalization_permit_sha256: str
    committed_chunk_set_sha256: str
    lineage_sha256: str
    snapshot_sha256: str
    snapshot_bytes: int
    total_plaintext_sha256: str
    total_plaintext_bytes: int
    chunk_count: int
    baseline_generation_id: str
    database_system_identifier: str
    timeline_id: int
    wal_segment_size_bytes: int
    baseline_wal_lsn: str
    wal_chain_start_lsn: str
    base_backup_end_lsn: str
    completion_attestation_sha256: str
    legacy_route_binding_sha256: str
    witness_transition_id: str
    witness_public_key_sha256: str
    admitted_at: datetime
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("CHUNKED_BASE_BACKUP_RECOVERY_ADMISSION_SERIALIZATION_FORBIDDEN")


@dataclass(frozen=True)
class _ConfigFacts:
    staging_root: Path
    receiver_site: str
    owner_uid: int


@dataclass(frozen=True)
class _AdmissionFacts:
    manifest: VerifiedPhysicalWalChunkedBaseBackupManifest
    handoff: VerifiedPhysicalWalChunkedBaseBackupHandoffReceipt
    stage_receipt_sha256: str
    stage_directory_name: str
    scope_sha256: str
    now: datetime


@dataclass(frozen=True)
class _AdmissionState:
    config: RootOwnedPhysicalWalChunkedBaseBackupRecoveryAdmissionConfig
    scope: PhysicalWalChunkedBaseBackupRecoveryAdmissionScope
    staging_result: PhysicalWalChunkedBaseBackupReceiverStagingResult
    projection_sha256: str


_ADMISSION_STATES: WeakKeyDictionary[
    VerifiedPhysicalWalChunkedBaseBackupRecoveryAdmission, _AdmissionState
] = WeakKeyDictionary()


def _fail(code: str) -> None:
    raise PhysicalWalChunkedBaseBackupRecoveryAdmissionError(code)


def _utc(value: object, *, code: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        _fail(code)
    return value.astimezone(timezone.utc)


def _safe_root(value: object) -> Path:
    if not isinstance(value, Path) or not value.is_absolute():
        _fail("CHUNKED_BASE_BACKUP_RECOVERY_ADMISSION_STAGE_ROOT_INVALID")
    text = str(value)
    if (
        not text
        or text == "/"
        or len(text) > 4096
        or any(part in {".", ".."} for part in value.parts)
        or _URL_OR_SECRET_RE.search(text) is not None
    ):
        _fail("CHUNKED_BASE_BACKUP_RECOVERY_ADMISSION_STAGE_ROOT_INVALID")
    return value


def _config_facts(
    config: object,
    *,
    require_enabled: bool,
) -> _ConfigFacts:
    if type(config) is not RootOwnedPhysicalWalChunkedBaseBackupRecoveryAdmissionConfig:
        _fail("CHUNKED_BASE_BACKUP_RECOVERY_ADMISSION_CONFIG_INVALID")
    if (
        config.schema != PHYSICAL_WAL_CHUNKED_BASE_BACKUP_RECOVERY_ADMISSION_SCHEMA
        or type(config.enabled) is not bool
        or (require_enabled and config.enabled is not True)
        or type(config.owner_uid) is not int
        or config.owner_uid != 0
        or type(config.receiver_site) is not str
        or _SITE_RE.fullmatch(config.receiver_site) is None
        or config.local_stage_receipt_readback != "required"
        or config.direct_site_control != "forbidden"
        or config.remote_object_storage != "forbidden"
        or config.v1_fallback != "forbidden"
        or config.restore_or_promotion != "forbidden"
    ):
        _fail("CHUNKED_BASE_BACKUP_RECOVERY_ADMISSION_CONFIG_INVALID")
    return _ConfigFacts(
        staging_root=_safe_root(config.staging_root),
        receiver_site=config.receiver_site,
        owner_uid=config.owner_uid,
    )


def validate_root_owned_physical_wal_chunked_base_backup_recovery_admission_config(
    config: object,
    *,
    require_enabled: bool = True,
) -> None:
    """Validate no-secret policy only; this function performs no local read."""

    _config_facts(config, require_enabled=require_enabled)


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("CHUNKED_BASE_BACKUP_RECOVERY_ADMISSION_STAGE_RECEIPT_NONCANONICAL")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> None:
    _fail("CHUNKED_BASE_BACKUP_RECOVERY_ADMISSION_STAGE_RECEIPT_NONCANONICAL")


def _canonical(value: object) -> bytes:
    try:
        return canonical_json_bytes(value)
    except (TypeError, ValueError) as exc:
        raise PhysicalWalChunkedBaseBackupRecoveryAdmissionError(
            "CHUNKED_BASE_BACKUP_RECOVERY_ADMISSION_STAGE_RECEIPT_NONCANONICAL"
        ) from exc


def _exact_mapping(value: object, *, fields: frozenset[str], code: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != fields:
        _fail(code)
    return dict(value)


def _nonzero_sha256(value: object, *, code: str) -> str:
    if (
        type(value) is not str
        or SHA256_RE.fullmatch(value) is None
        or value == "0" * 64
    ):
        _fail(code)
    return value


def _strict_text(value: object, *, pattern: re.Pattern[str], code: str) -> str:
    if type(value) is not str or pattern.fullmatch(value) is None:
        _fail(code)
    try:
        value.encode("ascii", "strict")
    except UnicodeEncodeError:
        _fail(code)
    return value


def _positive_int(value: object, *, maximum: int, code: str) -> int:
    if type(value) is not int or not 1 <= value <= maximum:
        _fail(code)
    return value


def _stage_directory_name(value: object) -> str:
    if type(value) is not str or _STAGE_DIRECTORY_RE.fullmatch(value) is None:
        _fail("CHUNKED_BASE_BACKUP_RECOVERY_ADMISSION_STAGE_PATH_INVALID")
    return value


def _open_secure_root(path: Path, *, owner_uid: int) -> int:
    if os.geteuid() != 0:
        _fail("CHUNKED_BASE_BACKUP_RECOVERY_ADMISSION_ROOT_REQUIRED")
    try:
        listed = os.lstat(path)
    except OSError:
        _fail("CHUNKED_BASE_BACKUP_RECOVERY_ADMISSION_STAGE_ROOT_UNAVAILABLE")
    if (
        stat.S_ISLNK(listed.st_mode)
        or not stat.S_ISDIR(listed.st_mode)
        or listed.st_uid != owner_uid
        or stat.S_IMODE(listed.st_mode) != 0o700
    ):
        _fail("CHUNKED_BASE_BACKUP_RECOVERY_ADMISSION_STAGE_ROOT_UNSAFE")
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
        observed = os.fstat(fd)
    except OSError:
        _fail("CHUNKED_BASE_BACKUP_RECOVERY_ADMISSION_STAGE_ROOT_UNSAFE")
    if (
        observed.st_dev != listed.st_dev
        or observed.st_ino != listed.st_ino
        or not stat.S_ISDIR(observed.st_mode)
        or observed.st_uid != owner_uid
        or stat.S_IMODE(observed.st_mode) != 0o700
    ):
        os.close(fd)
        _fail("CHUNKED_BASE_BACKUP_RECOVERY_ADMISSION_STAGE_ROOT_RACED")
    return fd


def _open_secure_stage(root_fd: int, *, name: str, owner_uid: int) -> int:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(name, flags, dir_fd=root_fd)
        observed = os.fstat(fd)
    except OSError:
        _fail("CHUNKED_BASE_BACKUP_RECOVERY_ADMISSION_STAGE_DIRECTORY_UNSAFE")
    if (
        not stat.S_ISDIR(observed.st_mode)
        or observed.st_uid != owner_uid
        or stat.S_IMODE(observed.st_mode) != 0o700
    ):
        os.close(fd)
        _fail("CHUNKED_BASE_BACKUP_RECOVERY_ADMISSION_STAGE_DIRECTORY_UNSAFE")
    return fd


def _read_secure_stage_receipt(
    *,
    stage_fd: int,
    owner_uid: int,
) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open("stage-receipt.json", flags, dir_fd=stage_fd)
        before = os.fstat(fd)
    except OSError:
        _fail("CHUNKED_BASE_BACKUP_RECOVERY_ADMISSION_STAGE_RECEIPT_UNSAFE")
    try:
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != owner_uid
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_nlink != 1
            or before.st_size < 1
            or before.st_size > MAX_PHYSICAL_WAL_CHUNKED_BASE_BACKUP_RECOVERY_STAGE_RECEIPT_BYTES
        ):
            _fail("CHUNKED_BASE_BACKUP_RECOVERY_ADMISSION_STAGE_RECEIPT_UNSAFE")
        chunks: list[bytes] = []
        total = 0
        while True:
            try:
                part = os.read(fd, MAX_PHYSICAL_WAL_CHUNKED_BASE_BACKUP_RECOVERY_STAGE_READ_BYTES)
            except OSError:
                _fail("CHUNKED_BASE_BACKUP_RECOVERY_ADMISSION_STAGE_RECEIPT_READ_FAILED")
            if not part:
                break
            total += len(part)
            if total > MAX_PHYSICAL_WAL_CHUNKED_BASE_BACKUP_RECOVERY_STAGE_RECEIPT_BYTES:
                _fail("CHUNKED_BASE_BACKUP_RECOVERY_ADMISSION_STAGE_RECEIPT_UNSAFE")
            chunks.append(part)
        try:
            after = os.fstat(fd)
        except OSError:
            _fail("CHUNKED_BASE_BACKUP_RECOVERY_ADMISSION_STAGE_RECEIPT_READ_FAILED")
        if (
            before.st_dev != after.st_dev
            or before.st_ino != after.st_ino
            or before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns
            or before.st_ctime_ns != after.st_ctime_ns
            or before.st_nlink != after.st_nlink
            or total != before.st_size
        ):
            _fail("CHUNKED_BASE_BACKUP_RECOVERY_ADMISSION_STAGE_RECEIPT_RACED")
        return b"".join(chunks)
    finally:
        os.close(fd)


def _result_stage_name(
    *,
    result: object,
    facts: _ConfigFacts,
) -> str:
    if type(result) is not PhysicalWalChunkedBaseBackupReceiverStagingResult:
        _fail("CHUNKED_BASE_BACKUP_RECOVERY_ADMISSION_STAGE_RESULT_INVALID")
    if not isinstance(result.stage_directory, Path) or not isinstance(result.stage_receipt_path, Path):
        _fail("CHUNKED_BASE_BACKUP_RECOVERY_ADMISSION_STAGE_PATH_INVALID")
    name = _stage_directory_name(result.stage_directory.name)
    expected_directory = facts.staging_root / name
    expected_receipt = expected_directory / "stage-receipt.json"
    # Compare lexical paths only.  The following openat/O_NOFOLLOW read never
    # trusts either caller path for filesystem traversal.
    if result.stage_directory != expected_directory or result.stage_receipt_path != expected_receipt:
        _fail("CHUNKED_BASE_BACKUP_RECOVERY_ADMISSION_STAGE_PATH_INVALID")
    return name


def _read_stage_receipt_for_result(
    *,
    result: object,
    facts: _ConfigFacts,
) -> tuple[str, bytes]:
    name = _result_stage_name(result=result, facts=facts)
    root_fd = stage_fd = -1
    try:
        root_fd = _open_secure_root(facts.staging_root, owner_uid=facts.owner_uid)
        stage_fd = _open_secure_stage(root_fd, name=name, owner_uid=facts.owner_uid)
        return name, _read_secure_stage_receipt(
            stage_fd=stage_fd,
            owner_uid=facts.owner_uid,
        )
    finally:
        for fd in (stage_fd, root_fd):
            if fd >= 0:
                try:
                    os.close(fd)
                except OSError:
                    pass


def _parse_stage_receipt(raw: object) -> dict[str, Any]:
    if not isinstance(raw, bytes) or not raw or len(raw) > MAX_PHYSICAL_WAL_CHUNKED_BASE_BACKUP_RECOVERY_STAGE_RECEIPT_BYTES:
        _fail("CHUNKED_BASE_BACKUP_RECOVERY_ADMISSION_STAGE_RECEIPT_INVALID")
    try:
        value = json.loads(
            raw.decode("ascii", "strict"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
        _fail("CHUNKED_BASE_BACKUP_RECOVERY_ADMISSION_STAGE_RECEIPT_NONCANONICAL")
    receipt = _exact_mapping(
        value,
        fields=_STAGE_RECEIPT_FIELDS,
        code="CHUNKED_BASE_BACKUP_RECOVERY_ADMISSION_STAGE_RECEIPT_INVALID",
    )
    if _canonical(receipt) != raw:
        _fail("CHUNKED_BASE_BACKUP_RECOVERY_ADMISSION_STAGE_RECEIPT_NONCANONICAL")
    return receipt


def _normalised_scope_binding(value: object) -> PhysicalWalChunkedBaseBackupBinding:
    """Reject caller-shaped bindings before their values contribute to policy.

    ``PhysicalWalChunkedBaseBackupBinding`` is a normal dataclass rather than
    an opaque capability, so a policy must not merely hash one supplied by a
    caller.  Rebuilding it through the V2 normalizer both validates every
    field and gives this contract one canonical representation to compare
    against the signed manifest binding.
    """

    if type(value) is not PhysicalWalChunkedBaseBackupBinding:
        _fail("CHUNKED_BASE_BACKUP_RECOVERY_ADMISSION_SCOPE_INVALID")
    term = value.writer_term
    if (
        type(value.source_site) is not str
        or type(value.destination_site) is not str
        or type(value.campaign_id) is not str
        or type(value.release_sha) is not str
        or type(value.object_storage_namespace) is not str
        or type(value.route_commitment_sha256) is not str
        or type(value.four_role_binding_sha256) is not str
        or type(value.destination_age_recipient) is not str
        or type(value.transport_plane) is not str
        or type(value.direct_webapp_transport) is not str
        or type(term) is not PhysicalWalChunkedBaseBackupWriterTerm
        or type(term.writer_holder_site) is not str
        or type(term.writer_epoch) is not int
        or type(term.writer_lease_id) is not str
        or type(term.witnessed_term_proof_sha256) is not str
    ):
        _fail("CHUNKED_BASE_BACKUP_RECOVERY_ADMISSION_SCOPE_INVALID")
    try:
        normalised = build_physical_wal_chunked_base_backup_binding(
            source_site=value.source_site,
            destination_site=value.destination_site,
            campaign_id=value.campaign_id,
            release_sha=value.release_sha,
            object_storage_namespace=value.object_storage_namespace,
            route_commitment_sha256=value.route_commitment_sha256,
            four_role_binding_sha256=value.four_role_binding_sha256,
            destination_age_recipient=value.destination_age_recipient,
            writer_holder_site=term.writer_holder_site,
            writer_epoch=term.writer_epoch,
            writer_lease_id=term.writer_lease_id,
            witnessed_term_proof_sha256=term.witnessed_term_proof_sha256,
        )
    except PhysicalWalChunkedBaseBackupTransferError as exc:
        raise PhysicalWalChunkedBaseBackupRecoveryAdmissionError(
            "CHUNKED_BASE_BACKUP_RECOVERY_ADMISSION_SCOPE_INVALID"
        ) from exc
    if normalised != value:
        _fail("CHUNKED_BASE_BACKUP_RECOVERY_ADMISSION_SCOPE_NOT_NORMALIZED")
    return normalised


def _scope_sha256(scope: object) -> tuple[PhysicalWalChunkedBaseBackupRecoveryAdmissionScope, str]:
    if type(scope) is not PhysicalWalChunkedBaseBackupRecoveryAdmissionScope:
        _fail("CHUNKED_BASE_BACKUP_RECOVERY_ADMISSION_SCOPE_INVALID")
    binding = _normalised_scope_binding(scope.transfer_binding)
    baseline_generation_id = _strict_text(
        scope.baseline_generation_id,
        pattern=_GENERATION_RE,
        code="CHUNKED_BASE_BACKUP_RECOVERY_ADMISSION_SCOPE_INVALID",
    )
    database_system_identifier = _strict_text(
        scope.database_system_identifier,
        pattern=_SYSTEM_IDENTIFIER_RE,
        code="CHUNKED_BASE_BACKUP_RECOVERY_ADMISSION_SCOPE_INVALID",
    )
    timeline_id = _positive_int(
        scope.timeline_id,
        maximum=0xFFFFFFFF,
        code="CHUNKED_BASE_BACKUP_RECOVERY_ADMISSION_SCOPE_INVALID",
    )
    wal_segment_size_bytes = _positive_int(
        scope.wal_segment_size_bytes,
        maximum=2**31 - 1,
        code="CHUNKED_BASE_BACKUP_RECOVERY_ADMISSION_SCOPE_INVALID",
    )
    if wal_segment_size_bytes != 16 * 1024 * 1024:
        _fail("CHUNKED_BASE_BACKUP_RECOVERY_ADMISSION_SCOPE_INVALID")
    baseline_wal_lsn = _strict_text(
        scope.baseline_wal_lsn,
        pattern=_LSN_RE,
        code="CHUNKED_BASE_BACKUP_RECOVERY_ADMISSION_SCOPE_INVALID",
    )
    wal_chain_start_lsn = _strict_text(
        scope.wal_chain_start_lsn,
        pattern=_LSN_RE,
        code="CHUNKED_BASE_BACKUP_RECOVERY_ADMISSION_SCOPE_INVALID",
    )
    base_backup_end_lsn = _strict_text(
        scope.base_backup_end_lsn,
        pattern=_LSN_RE,
        code="CHUNKED_BASE_BACKUP_RECOVERY_ADMISSION_SCOPE_INVALID",
    )
    completion_attestation_sha256 = _nonzero_sha256(
        scope.completion_attestation_sha256,
        code="CHUNKED_BASE_BACKUP_RECOVERY_ADMISSION_SCOPE_INVALID",
    )
    legacy_route_binding_sha256 = _nonzero_sha256(
        scope.legacy_route_binding_sha256,
        code="CHUNKED_BASE_BACKUP_RECOVERY_ADMISSION_SCOPE_INVALID",
    )
    witness_transition_id = _strict_text(
        scope.witness_transition_id,
        pattern=_TRANSITION_RE,
        code="CHUNKED_BASE_BACKUP_RECOVERY_ADMISSION_SCOPE_INVALID",
    )
    payload = {
        "schema": PHYSICAL_WAL_CHUNKED_BASE_BACKUP_RECOVERY_ADMISSION_SCHEMA,
        "binding": {
            "source_site": binding.source_site,
            "destination_site": binding.destination_site,
            "campaign_id": binding.campaign_id,
            "release_sha": binding.release_sha,
            "object_storage_namespace": binding.object_storage_namespace,
            "route_commitment_sha256": binding.route_commitment_sha256,
            "four_role_binding_sha256": binding.four_role_binding_sha256,
            "destination_age_recipient": binding.destination_age_recipient,
            "writer_holder_site": binding.writer_term.writer_holder_site,
            "writer_epoch": binding.writer_term.writer_epoch,
            "writer_lease_id": binding.writer_term.writer_lease_id,
            "witnessed_term_proof_sha256": binding.writer_term.witnessed_term_proof_sha256,
            "transport_plane": binding.transport_plane,
            "direct_webapp_transport": binding.direct_webapp_transport,
        },
        "baseline_generation_id": baseline_generation_id,
        "database_system_identifier": database_system_identifier,
        "timeline_id": timeline_id,
        "wal_segment_size_bytes": wal_segment_size_bytes,
        "baseline_wal_lsn": baseline_wal_lsn,
        "wal_chain_start_lsn": wal_chain_start_lsn,
        "base_backup_end_lsn": base_backup_end_lsn,
        "completion_attestation_sha256": completion_attestation_sha256,
        "legacy_route_binding_sha256": legacy_route_binding_sha256,
        "witness_transition_id": witness_transition_id,
    }
    return scope, hashlib.sha256(_canonical(payload)).hexdigest()


def _expected_stage_chunks(
    selectors: tuple[PhysicalWalChunkedBaseBackupManifestChunkSelector, ...],
) -> list[dict[str, Any]]:
    return [
        {
            "index": selector.index,
            "object_key": selector.object_key,
            "version_id": selector.version_id,
            "ciphertext_sha256": selector.ciphertext_sha256,
            "ciphertext_bytes": selector.ciphertext_bytes,
            "plaintext_sha256": selector.plaintext_sha256,
            "plaintext_bytes": selector.plaintext_bytes,
        }
        for selector in selectors
    ]


def _assert_stage_receipt(
    *,
    receipt: dict[str, Any],
    raw: bytes,
    stage_directory_name: str,
    staging_result: object,
    manifest: VerifiedPhysicalWalChunkedBaseBackupManifest,
    handoff: VerifiedPhysicalWalChunkedBaseBackupHandoffReceipt,
) -> str:
    if type(staging_result) is not PhysicalWalChunkedBaseBackupReceiverStagingResult:
        _fail("CHUNKED_BASE_BACKUP_RECOVERY_ADMISSION_STAGE_RESULT_INVALID")
    ledger_key_sha256 = _nonzero_sha256(
        receipt.get("ledger_key_sha256"),
        code="CHUNKED_BASE_BACKUP_RECOVERY_ADMISSION_STAGE_RECEIPT_INVALID",
    )
    if stage_directory_name != "stage-" + ledger_key_sha256[:48]:
        _fail("CHUNKED_BASE_BACKUP_RECOVERY_ADMISSION_STAGE_DIRECTORY_BINDING_MISMATCH")
    expected = {
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
        "total_plaintext_sha256": manifest.total_plaintext_sha256,
        "total_plaintext_bytes": manifest.total_plaintext_bytes,
        "chunk_count": len(manifest.chunks),
        "ledger_key_sha256": ledger_key_sha256,
        "chunks": _expected_stage_chunks(manifest.chunks),
    }
    if receipt != expected:
        _fail("CHUNKED_BASE_BACKUP_RECOVERY_ADMISSION_STAGE_RECEIPT_PIN_MISMATCH")
    stage_sha256 = hashlib.sha256(raw).hexdigest()
    if (
        staging_result.status != PHYSICAL_WAL_CHUNKED_BASE_BACKUP_RECEIVER_STAGING_STATUS
        or staging_result.stage_receipt_sha256 != stage_sha256
        or staging_result.receipt_id != handoff.receipt_id
        or staging_result.receipt_nonce != handoff.receipt_nonce
        or staging_result.manifest_sha256 != expected["manifest_sha256"]
        or staging_result.binding_sha256 != handoff.binding_sha256
        or staging_result.total_plaintext_sha256 != manifest.total_plaintext_sha256
        or staging_result.total_plaintext_bytes != manifest.total_plaintext_bytes
        or staging_result.chunk_count != len(manifest.chunks)
    ):
        _fail("CHUNKED_BASE_BACKUP_RECOVERY_ADMISSION_STAGE_RESULT_MISMATCH")
    return stage_sha256


def _assert_scope_and_cross_pins(
    *,
    facts: _ConfigFacts,
    scope: PhysicalWalChunkedBaseBackupRecoveryAdmissionScope,
    manifest: VerifiedPhysicalWalChunkedBaseBackupManifest,
    handoff: VerifiedPhysicalWalChunkedBaseBackupHandoffReceipt,
) -> None:
    binding = manifest.finalization_permit.session.binding
    session = manifest.finalization_permit.session
    permit = manifest.finalization_permit
    if (
        binding != scope.transfer_binding
        or binding.destination_site != facts.receiver_site
        or binding.source_site == binding.destination_site
        or binding.writer_term.writer_holder_site != binding.source_site
        or handoff.destination_age_recipient != binding.destination_age_recipient
        or handoff.manifest_id != manifest.manifest_id
        or handoff.manifest_sha256 != hashlib.sha256(manifest.canonical_manifest).hexdigest()
        or handoff.binding_sha256 == "0" * 64
        or handoff.session_sha256 != hashlib.sha256(session.canonical_session).hexdigest()
        or handoff.finalization_permit_id != permit.finalization_permit_id
        or handoff.finalization_permit_sha256
        != hashlib.sha256(permit.canonical_finalization_permit).hexdigest()
        or handoff.committed_chunk_set_sha256 != permit.committed_chunk_set_sha256
        or handoff.snapshot_sha256 != manifest.total_plaintext_sha256
        or handoff.snapshot_bytes != manifest.total_plaintext_bytes
        or handoff.lineage_sha256 == "0" * 64
        or handoff.baseline_generation_id != scope.baseline_generation_id
        or handoff.database_system_identifier != scope.database_system_identifier
        or handoff.timeline_id != scope.timeline_id
        or handoff.wal_segment_size_bytes != scope.wal_segment_size_bytes
        or handoff.baseline_wal_lsn != scope.baseline_wal_lsn
        or handoff.wal_chain_start_lsn != scope.wal_chain_start_lsn
        or handoff.base_backup_end_lsn != scope.base_backup_end_lsn
        or handoff.completion_attestation_sha256 != scope.completion_attestation_sha256
        or handoff.legacy_route_binding_sha256 != scope.legacy_route_binding_sha256
        or handoff.witness_transition_id != scope.witness_transition_id
        or manifest.witness_public_key != handoff.witness_public_key
        or manifest.total_plaintext_bytes < 1
        or len(manifest.chunks) < 1
        or len(manifest.chunks) > MAX_PHYSICAL_WAL_CHUNKED_BASE_BACKUP_CHUNKS
    ):
        _fail("CHUNKED_BASE_BACKUP_RECOVERY_ADMISSION_CROSS_PIN_MISMATCH")
    identities = (
        session.session_id,
        session.session_nonce,
        permit.finalization_permit_id,
        permit.finalization_permit_nonce,
        manifest.manifest_id,
        manifest.manifest_nonce,
        handoff.receipt_id,
        handoff.receipt_nonce,
    )
    if len(set(identities)) != len(identities):
        _fail("CHUNKED_BASE_BACKUP_RECOVERY_ADMISSION_IDENTITY_REUSE")


def _verified_facts(
    *,
    config: object,
    scope: object,
    manifest: object,
    handoff_receipt: object,
    staging_result: object,
    now: datetime,
) -> _AdmissionFacts:
    config_facts = _config_facts(config, require_enabled=True)
    current = _utc(now, code="CHUNKED_BASE_BACKUP_RECOVERY_ADMISSION_CLOCK_INVALID")
    verified_scope, scope_sha256 = _scope_sha256(scope)
    try:
        verified_manifest = require_verified_physical_wal_chunked_base_backup_manifest(
            manifest,
            now=current,
        )
        handoff = require_verified_physical_wal_chunked_base_backup_handoff_receipt(
            handoff_receipt,
            manifest=verified_manifest,
            now=current,
        )
    except Exception as exc:
        raise PhysicalWalChunkedBaseBackupRecoveryAdmissionError(
            "CHUNKED_BASE_BACKUP_RECOVERY_ADMISSION_V2_CAPABILITY_INVALID"
        ) from exc
    _assert_scope_and_cross_pins(
        facts=config_facts,
        scope=verified_scope,
        manifest=verified_manifest,
        handoff=handoff,
    )
    stage_name, raw = _read_stage_receipt_for_result(
        result=staging_result,
        facts=config_facts,
    )
    receipt = _parse_stage_receipt(raw)
    stage_sha256 = _assert_stage_receipt(
        receipt=receipt,
        raw=raw,
        stage_directory_name=stage_name,
        staging_result=staging_result,
        manifest=verified_manifest,
        handoff=handoff,
    )
    return _AdmissionFacts(
        manifest=verified_manifest,
        handoff=handoff,
        stage_receipt_sha256=stage_sha256,
        stage_directory_name=stage_name,
        scope_sha256=scope_sha256,
        now=current,
    )


def _admission_from_facts(facts: _AdmissionFacts, *, receiver_site: str) -> VerifiedPhysicalWalChunkedBaseBackupRecoveryAdmission:
    manifest = facts.manifest
    handoff = facts.handoff
    permit = manifest.finalization_permit
    result = VerifiedPhysicalWalChunkedBaseBackupRecoveryAdmission(
        schema=PHYSICAL_WAL_CHUNKED_BASE_BACKUP_RECOVERY_ADMISSION_SCHEMA,
        receiver_site=receiver_site,
        stage_directory_name=facts.stage_directory_name,
        stage_receipt_sha256=facts.stage_receipt_sha256,
        scope_sha256=facts.scope_sha256,
        receipt_id=handoff.receipt_id,
        receipt_nonce=handoff.receipt_nonce,
        manifest_id=manifest.manifest_id,
        manifest_sha256=hashlib.sha256(manifest.canonical_manifest).hexdigest(),
        binding_sha256=handoff.binding_sha256,
        session_sha256=handoff.session_sha256,
        finalization_permit_id=permit.finalization_permit_id,
        finalization_permit_sha256=hashlib.sha256(permit.canonical_finalization_permit).hexdigest(),
        committed_chunk_set_sha256=permit.committed_chunk_set_sha256,
        lineage_sha256=handoff.lineage_sha256,
        snapshot_sha256=handoff.snapshot_sha256,
        snapshot_bytes=handoff.snapshot_bytes,
        total_plaintext_sha256=manifest.total_plaintext_sha256,
        total_plaintext_bytes=manifest.total_plaintext_bytes,
        chunk_count=len(manifest.chunks),
        baseline_generation_id=handoff.baseline_generation_id,
        database_system_identifier=handoff.database_system_identifier,
        timeline_id=handoff.timeline_id,
        wal_segment_size_bytes=handoff.wal_segment_size_bytes,
        baseline_wal_lsn=handoff.baseline_wal_lsn,
        wal_chain_start_lsn=handoff.wal_chain_start_lsn,
        base_backup_end_lsn=handoff.base_backup_end_lsn,
        completion_attestation_sha256=handoff.completion_attestation_sha256,
        legacy_route_binding_sha256=handoff.legacy_route_binding_sha256,
        witness_transition_id=handoff.witness_transition_id,
        witness_public_key_sha256=hashlib.sha256(handoff.witness_public_key).hexdigest(),
        admitted_at=facts.now,
    )
    object.__setattr__(result, "_capability", _CAPABILITY)
    return result


def _admission_projection_sha256(
    value: VerifiedPhysicalWalChunkedBaseBackupRecoveryAdmission,
) -> str:
    """Hash every public admission field for the no-I/O projection seam.

    This deliberately excludes the private capability token and all local
    staging paths.  It is not an integrity substitute for
    ``require_verified_*``: that owning verifier remains the only API that
    re-opens the root-owned stage receipt.  The digest merely lets the
    non-authorizing projector reject a mutated object while it confirms
    process-local mint membership without filesystem I/O.
    """

    admitted_at = _utc(
        value.admitted_at,
        code="CHUNKED_BASE_BACKUP_RECOVERY_ADMISSION_CAPABILITY_TAMPERED",
    )
    payload = {
        "schema": value.schema,
        "receiver_site": value.receiver_site,
        "stage_directory_name": value.stage_directory_name,
        "stage_receipt_sha256": value.stage_receipt_sha256,
        "scope_sha256": value.scope_sha256,
        "receipt_id": value.receipt_id,
        "receipt_nonce": value.receipt_nonce,
        "manifest_id": value.manifest_id,
        "manifest_sha256": value.manifest_sha256,
        "binding_sha256": value.binding_sha256,
        "session_sha256": value.session_sha256,
        "finalization_permit_id": value.finalization_permit_id,
        "finalization_permit_sha256": value.finalization_permit_sha256,
        "committed_chunk_set_sha256": value.committed_chunk_set_sha256,
        "lineage_sha256": value.lineage_sha256,
        "snapshot_sha256": value.snapshot_sha256,
        "snapshot_bytes": value.snapshot_bytes,
        "total_plaintext_sha256": value.total_plaintext_sha256,
        "total_plaintext_bytes": value.total_plaintext_bytes,
        "chunk_count": value.chunk_count,
        "baseline_generation_id": value.baseline_generation_id,
        "database_system_identifier": value.database_system_identifier,
        "timeline_id": value.timeline_id,
        "wal_segment_size_bytes": value.wal_segment_size_bytes,
        "baseline_wal_lsn": value.baseline_wal_lsn,
        "wal_chain_start_lsn": value.wal_chain_start_lsn,
        "base_backup_end_lsn": value.base_backup_end_lsn,
        "completion_attestation_sha256": value.completion_attestation_sha256,
        "legacy_route_binding_sha256": value.legacy_route_binding_sha256,
        "witness_transition_id": value.witness_transition_id,
        "witness_public_key_sha256": value.witness_public_key_sha256,
        "admitted_at": admitted_at.isoformat(),
    }
    try:
        return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    except (TypeError, ValueError) as exc:
        raise PhysicalWalChunkedBaseBackupRecoveryAdmissionError(
            "CHUNKED_BASE_BACKUP_RECOVERY_ADMISSION_CAPABILITY_TAMPERED"
        ) from exc


def admit_root_owned_physical_wal_chunked_base_backup_recovery(
    config: RootOwnedPhysicalWalChunkedBaseBackupRecoveryAdmissionConfig,
    *,
    scope: PhysicalWalChunkedBaseBackupRecoveryAdmissionScope,
    manifest: VerifiedPhysicalWalChunkedBaseBackupManifest,
    handoff_receipt: VerifiedPhysicalWalChunkedBaseBackupHandoffReceipt,
    staging_result: PhysicalWalChunkedBaseBackupReceiverStagingResult,
    now: datetime,
) -> VerifiedPhysicalWalChunkedBaseBackupRecoveryAdmission:
    """Admit one safe local v2 stage as opaque recovery evidence.

    This has no side effects.  The only local operation is an O_NOFOLLOW,
    root-owned, canonical receipt readback; it does not open payload files or
    contact any remote service.
    """

    facts = _verified_facts(
        config=config,
        scope=scope,
        manifest=manifest,
        handoff_receipt=handoff_receipt,
        staging_result=staging_result,
        now=now,
    )
    config_facts = _config_facts(config, require_enabled=True)
    result = _admission_from_facts(facts, receiver_site=config_facts.receiver_site)
    _ADMISSION_STATES[result] = _AdmissionState(
        config=config,
        scope=scope,
        staging_result=staging_result,
        projection_sha256=_admission_projection_sha256(result),
    )
    return result


def project_verified_physical_wal_chunked_base_backup_recovery_admission(
    value: object,
) -> VerifiedPhysicalWalChunkedBaseBackupRecoveryAdmission:
    """Return a process-local admission projection without local readback.

    This narrow helper is intentionally **non-authorizing**.  It performs no
    filesystem, provider, network, PostgreSQL, restore, promotion, or writer
    work.  Its only purpose is to let another pure evidence boundary confirm
    that an admission was minted in this process and has not changed since
    minting.  Consumers that need the local stage receipt to be current must
    call ``require_verified_physical_wal_chunked_base_backup_recovery_admission``
    instead.
    """

    if (
        type(value) is not VerifiedPhysicalWalChunkedBaseBackupRecoveryAdmission
        or value._capability is not _CAPABILITY
    ):
        _fail("CHUNKED_BASE_BACKUP_RECOVERY_ADMISSION_CAPABILITY_REQUIRED")
    state = _ADMISSION_STATES.get(value)
    if state is None:
        _fail("CHUNKED_BASE_BACKUP_RECOVERY_ADMISSION_CAPABILITY_REQUIRED")
    if _admission_projection_sha256(value) != state.projection_sha256:
        _fail("CHUNKED_BASE_BACKUP_RECOVERY_ADMISSION_CAPABILITY_TAMPERED")
    return value


def _assert_admission_matches(
    *,
    value: VerifiedPhysicalWalChunkedBaseBackupRecoveryAdmission,
    facts: _AdmissionFacts,
    receiver_site: str,
) -> None:
    manifest = facts.manifest
    handoff = facts.handoff
    permit = manifest.finalization_permit
    if (
        value.schema != PHYSICAL_WAL_CHUNKED_BASE_BACKUP_RECOVERY_ADMISSION_SCHEMA
        or value.receiver_site != receiver_site
        or value.stage_directory_name != facts.stage_directory_name
        or value.stage_receipt_sha256 != facts.stage_receipt_sha256
        or value.scope_sha256 != facts.scope_sha256
        or value.receipt_id != handoff.receipt_id
        or value.receipt_nonce != handoff.receipt_nonce
        or value.manifest_id != manifest.manifest_id
        or value.manifest_sha256 != hashlib.sha256(manifest.canonical_manifest).hexdigest()
        or value.binding_sha256 != handoff.binding_sha256
        or value.session_sha256 != handoff.session_sha256
        or value.finalization_permit_id != permit.finalization_permit_id
        or value.finalization_permit_sha256 != hashlib.sha256(permit.canonical_finalization_permit).hexdigest()
        or value.committed_chunk_set_sha256 != permit.committed_chunk_set_sha256
        or value.lineage_sha256 != handoff.lineage_sha256
        or value.snapshot_sha256 != handoff.snapshot_sha256
        or value.snapshot_bytes != handoff.snapshot_bytes
        or value.total_plaintext_sha256 != manifest.total_plaintext_sha256
        or value.total_plaintext_bytes != manifest.total_plaintext_bytes
        or value.chunk_count != len(manifest.chunks)
        or value.baseline_generation_id != handoff.baseline_generation_id
        or value.database_system_identifier != handoff.database_system_identifier
        or value.timeline_id != handoff.timeline_id
        or value.wal_segment_size_bytes != handoff.wal_segment_size_bytes
        or value.baseline_wal_lsn != handoff.baseline_wal_lsn
        or value.wal_chain_start_lsn != handoff.wal_chain_start_lsn
        or value.base_backup_end_lsn != handoff.base_backup_end_lsn
        or value.completion_attestation_sha256 != handoff.completion_attestation_sha256
        or value.legacy_route_binding_sha256 != handoff.legacy_route_binding_sha256
        or value.witness_transition_id != handoff.witness_transition_id
        or value.witness_public_key_sha256 != hashlib.sha256(handoff.witness_public_key).hexdigest()
        or value.admitted_at.tzinfo is None
        or value.admitted_at.utcoffset() is None
        or value.admitted_at > facts.now
    ):
        _fail("CHUNKED_BASE_BACKUP_RECOVERY_ADMISSION_CAPABILITY_TAMPERED")


def require_verified_physical_wal_chunked_base_backup_recovery_admission(
    value: object,
    *,
    config: RootOwnedPhysicalWalChunkedBaseBackupRecoveryAdmissionConfig,
    scope: PhysicalWalChunkedBaseBackupRecoveryAdmissionScope,
    manifest: VerifiedPhysicalWalChunkedBaseBackupManifest,
    handoff_receipt: VerifiedPhysicalWalChunkedBaseBackupHandoffReceipt,
    now: datetime,
) -> VerifiedPhysicalWalChunkedBaseBackupRecoveryAdmission:
    """Revalidate an opaque admission and re-read its secure local receipt."""

    if (
        type(value) is not VerifiedPhysicalWalChunkedBaseBackupRecoveryAdmission
        or value._capability is not _CAPABILITY
    ):
        _fail("CHUNKED_BASE_BACKUP_RECOVERY_ADMISSION_CAPABILITY_REQUIRED")
    state = _ADMISSION_STATES.get(value)
    if state is None:
        _fail("CHUNKED_BASE_BACKUP_RECOVERY_ADMISSION_CAPABILITY_REQUIRED")
    if type(config) is not RootOwnedPhysicalWalChunkedBaseBackupRecoveryAdmissionConfig or config != state.config:
        _fail("CHUNKED_BASE_BACKUP_RECOVERY_ADMISSION_CONFIG_MISMATCH")
    if type(scope) is not PhysicalWalChunkedBaseBackupRecoveryAdmissionScope or scope != state.scope:
        _fail("CHUNKED_BASE_BACKUP_RECOVERY_ADMISSION_SCOPE_MISMATCH")
    facts = _verified_facts(
        config=config,
        scope=scope,
        manifest=manifest,
        handoff_receipt=handoff_receipt,
        staging_result=state.staging_result,
        now=now,
    )
    config_facts = _config_facts(config, require_enabled=True)
    _assert_admission_matches(value=value, facts=facts, receiver_site=config_facts.receiver_site)
    return value
