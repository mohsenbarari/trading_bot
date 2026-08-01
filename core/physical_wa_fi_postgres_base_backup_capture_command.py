"""Fail-closed WA-FI ``pg_basebackup`` capture-command boundary.

This module is deliberately an *installed-adapter seam*, not a process
launcher.  It never imports ``subprocess``, PostgreSQL drivers, Docker, SSH,
or an Object-Storage SDK.  A future root-controlled service may call
``execute_wa_fi_postgres_base_backup_capture_command`` with an injected
runner, age factory, and S3-compatible factory.  The module then gives that
runner one immutable ``pg_basebackup`` invocation and accepts output only from
one root-owned staging directory before handing the completed artifact to the
existing base-backup spool.

The boundary intentionally has no command, host, URL, credential, environment
or path argument.  Its only runtime file is the fixed root-owned JSON policy,
and its only executable identity is the fixed PostgreSQL 15 binary path plus
the SHA-256 pinned by that policy.  Importing this module is inert.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
from typing import Any, Protocol

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from core.append_only_sync_delta_batch import (
    CAMPAIGN_ID_RE,
    RELEASE_SHA_RE,
    SHA256_RE,
    STREAM_GENERATION_ID_RE,
    canonical_json_bytes,
)
from core.object_delta_role_matrix_rollover import (
    ObjectDeltaRoleMatrixRolloverError,
    VerifiedObjectDeltaRoleMatrixWitnessedTerm,
    verify_object_delta_role_matrix_witnessed_term,
)
from core.object_delta_transport_binding import AGE_RECIPIENT_RE
from core.physical_wal_base_backup_spool import (
    MAX_PHYSICAL_BASE_BACKUP_BYTES,
    MAX_SPOOL_RESERVE_BYTES,
    PhysicalWalBaseBackupCompletedArtifact,
    PhysicalWalBaseBackupManifestBinding,
    PhysicalWalBaseBackupSpoolConfig,
    PhysicalWalBaseBackupSpoolError,
    PhysicalWalBaseBackupSpoolResult,
    PhysicalWalBaseBackupUploader,
    authorize_physical_wal_base_backup_binding,
    capture_physical_wal_base_backup,
)
from core.physical_wal_object_manifest import PHYSICAL_WAL_NORMAL_OBJECT_STORAGE_NAMESPACE
from core.physical_wal_object_storage_uploader import (
    PhysicalWalAgeEncryptor,
    PhysicalWalBaseBackupObjectStorageUploader,
    PhysicalWalObjectStorageClient,
    PhysicalWalObjectStorageUploaderConfig,
)


__all__ = (
    "FIXED_WA_FI_PG_BASEBACKUP_COMMAND",
    "FIXED_WA_FI_POSTGRES_BASE_BACKUP_CAPTURE_CONFIG",
    "PHYSICAL_WA_FI_POSTGRES_BASE_BACKUP_CAPTURE_DEFAULT_ENABLED",
    "PHYSICAL_WA_FI_POSTGRES_BASE_BACKUP_CAPTURE_RUNTIME_SCHEMA",
    "PhysicalWaFiPostgresBaseBackupCaptureCommandError",
    "PhysicalWaFiPostgresBaseBackupCaptureCommandResult",
    "PhysicalWaFiPostgresBaseBackupInvocation",
    "PhysicalWaFiPostgresBaseBackupRunner",
    "PhysicalWaFiPostgresBaseBackupRunnerResult",
    "PhysicalWaFiPostgresBaseBackupUploaderFactory",
    "execute_wa_fi_postgres_base_backup_capture_command",
)


PHYSICAL_WA_FI_POSTGRES_BASE_BACKUP_CAPTURE_RUNTIME_SCHEMA = (
    "gold-trade-physical-wa-fi-postgres-base-backup-capture-runtime-v1"
)
PHYSICAL_WA_FI_POSTGRES_BASE_BACKUP_CAPTURE_COMPLETION_SCHEMA = (
    "gold-trade-physical-wa-fi-postgres-base-backup-completion-v1"
)
PHYSICAL_WA_FI_POSTGRES_BASE_BACKUP_CAPTURE_DEFAULT_ENABLED = False

FIXED_WA_FI_POSTGRES_BASE_BACKUP_CAPTURE_CONFIG = Path(
    "/etc/trading-bot/physical-postgres/primary/base-backup-capture.json"
)
FIXED_WA_FI_PG_BASEBACKUP_COMMAND = Path("/usr/lib/postgresql/15/bin/pg_basebackup")

MAX_WA_FI_POSTGRES_BASE_BACKUP_CAPTURE_CONFIG_BYTES = 192 * 1024
MAX_WA_FI_POSTGRES_BASE_BACKUP_COMPLETION_BYTES = 64 * 1024
MAX_WA_FI_POSTGRES_BASE_BACKUP_ATTESTATION_BYTES = 256 * 1024
MAX_WA_FI_PG_BASEBACKUP_BINARY_BYTES = 128 * 1024 * 1024
_RUNTIME_VERSION = 1
_COMPLETION_VERSION = 1
_COPY_CHUNK_BYTES = 1024 * 1024

_CONFIG_FIELDS = frozenset(
    {
        "schema",
        "version",
        "enabled",
        "configuration_sha256",
        "source_site",
        "destination_site",
        "direct_site_control",
        "destination_object_ingest",
        "capture",
        "manifest_binding",
        "witness_term",
        "object_storage_uploader",
    }
)
_CAPTURE_FIELDS = frozenset(
    {
        "source_socket_transport",
        "source_socket_directory",
        "source_port",
        "source_role",
        "password_prompt",
        "capture_root",
        "completed_source_root",
        "completed_artifact_name",
        "spool_root",
        "maximum_base_backup_bytes",
        "spool_reserve_bytes",
        "pg_basebackup_sha256",
    }
)
_MANIFEST_BINDING_FIELDS = frozenset(
    {
        "source_site",
        "destination_site",
        "campaign_id",
        "release_sha",
        "baseline_generation_id",
        "database_system_identifier",
        "timeline_id",
        "wal_segment_size_bytes",
        "baseline_wal_lsn",
        "wal_chain_start_lsn",
        "base_backup_end_lsn",
        "destination_age_recipient",
    }
)
_WITNESS_TERM_FIELDS = frozenset(
    {
        "public_key_base64",
        "maximum_lease_duration_seconds",
        "safety_margin_seconds",
        "proof",
    }
)
_UPLOADER_FIELDS = frozenset(
    {
        "source_site",
        "destination_site",
        "workspace",
        "spool_root",
        "spool_owner_uid",
        "bucket",
        "region",
        "destination_age_recipient",
        "enabled",
        "maximum_plaintext_bytes",
        "direct_site_control",
        "destination_object_ingest",
    }
)
_COMPLETION_FIELDS = frozenset(
    {
        "schema",
        "version",
        "status",
        "configuration_sha256",
        "command_sha256",
        "source_site",
        "destination_site",
        "artifact_filename",
        "plaintext_sha256",
        "plaintext_bytes",
        "completion_attestation_sha256",
        "writer_epoch",
        "writer_lease_id",
        "witness_transition_id",
        "witnessed_term_proof_sha256",
    }
)

_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$", re.ASCII)
_ARTIFACT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,255}$", re.ASCII)
_ROLE_RE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$", re.ASCII)
_BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{2,62}$", re.ASCII)
_REGION_RE = re.compile(r"^[a-z][a-z0-9-]{1,62}$", re.ASCII)
_SYSTEM_IDENTIFIER_RE = re.compile(r"^[1-9][0-9]{0,19}$", re.ASCII)
_LSN_RE = re.compile(r"^(?:0|[1-9A-F][0-9A-F]{0,7})/(?:0|[1-9A-F][0-9A-F]{0,7})$", re.ASCII)
_URL_OR_SECRET_RE = re.compile(
    r"(?i)(?:[a-z][a-z0-9+.-]*://|www\.|bearer\s+|access[_ -]?key|"
    r"authorization|credential|password|private[_ -]?key|secret|token)"
)

_RUN_DIRECTORY_PREFIX = "pg-basebackup-capture-"
_RUN_ARTIFACT_FILENAME = "base.tar"
_RUN_COMPLETION_FILENAME = "completion.json"
_RUN_ATTESTATION_FILENAME = "completion.attestation"


class PhysicalWaFiPostgresBaseBackupCaptureCommandError(RuntimeError):
    """One fixed, redacted capture-boundary failure code."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class PhysicalWaFiPostgresBaseBackupInvocation:
    """The complete immutable invocation offered to an installed runner.

    ``environment`` is deliberately the empty tuple.  A compliant installed
    runner must exec the absolute command directly with this exact argv and no
    inherited credential/environment expansion; it must not invoke a shell.
    """

    command_path: Path
    command_sha256: str
    arguments: tuple[str, ...]
    environment: tuple[tuple[str, str], ...]
    output_directory: Path
    configuration_sha256: str
    writer_epoch: int
    writer_lease_id: str
    witness_transition_id: str
    witnessed_term_proof_sha256: str


@dataclass(frozen=True)
class PhysicalWaFiPostgresBaseBackupRunnerResult:
    """Minimal non-secret runner status; payload travels only via fixed files."""

    exit_code: int


class PhysicalWaFiPostgresBaseBackupRunner(Protocol):
    def run(
        self,
        *,
        invocation: PhysicalWaFiPostgresBaseBackupInvocation,
    ) -> PhysicalWaFiPostgresBaseBackupRunnerResult: ...


class PhysicalWaFiPostgresBaseBackupUploaderFactory(Protocol):
    def __call__(
        self,
        *,
        config: PhysicalWalObjectStorageUploaderConfig,
        age_encryptor_factory: Callable[[], PhysicalWalAgeEncryptor],
        object_storage_client_factory: Callable[[], PhysicalWalObjectStorageClient],
    ) -> PhysicalWalBaseBackupUploader: ...


@dataclass(frozen=True)
class PhysicalWaFiPostgresBaseBackupCaptureCommandResult:
    """Redacted archive/recovery result; never an acknowledgement or authority."""

    completed_artifact_sha256: str
    completed_artifact_bytes: int
    completion_attestation_sha256: str
    route_binding_sha256: str
    object_key: str
    object_version_id: str


@dataclass(frozen=True)
class _CommandFacts:
    path: Path
    sha256: str


@dataclass(frozen=True)
class _CaptureFacts:
    socket_directory: Path
    source_role: str
    capture_root: Path
    completed_source_root: Path
    completed_artifact_name: str
    spool_config: PhysicalWalBaseBackupSpoolConfig
    command_sha256: str


@dataclass(frozen=True)
class _RuntimeFacts:
    configuration_sha256: str
    manifest_binding: PhysicalWalBaseBackupManifestBinding
    witnessed_term: VerifiedObjectDeltaRoleMatrixWitnessedTerm
    capture: _CaptureFacts
    uploader_config: PhysicalWalObjectStorageUploaderConfig
    command: _CommandFacts


def _fail(code: str) -> None:
    raise PhysicalWaFiPostgresBaseBackupCaptureCommandError(code)


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("BASE_BACKUP_CAPTURE_CONFIG_JSON_INVALID")
        result[key] = value
    return result


def _reject_json_constant(_: str) -> None:
    _fail("BASE_BACKUP_CAPTURE_CONFIG_JSON_INVALID")


def _utc(value: object, *, code: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        _fail(code)
    return value.astimezone(timezone.utc)


def _canonical(value: object, *, code: str) -> bytes:
    try:
        return canonical_json_bytes(value)
    except (TypeError, ValueError):
        _fail(code)


def _exact_mapping(value: object, *, fields: frozenset[str], code: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != fields:
        _fail(code)
    return dict(value)


def _safe_text(value: object, *, pattern: re.Pattern[str], code: str) -> str:
    if type(value) is not str or value != value.strip() or "\x00" in value:
        _fail(code)
    if pattern.fullmatch(value) is None or _URL_OR_SECRET_RE.search(value) is not None:
        _fail(code)
    try:
        value.encode("ascii", "strict")
    except UnicodeEncodeError:
        _fail(code)
    return value


def _sha256(value: object, *, code: str) -> str:
    result = _safe_text(value, pattern=SHA256_RE, code=code)
    if result == "0" * 64:
        _fail(code)
    return result


def _positive_int(value: object, *, maximum: int, code: str) -> int:
    if type(value) is not int or not 1 <= value <= maximum:
        _fail(code)
    return value


def _safe_path_text(value: object, *, code: str) -> Path:
    if type(value) is not str or not value or "\x00" in value or _URL_OR_SECRET_RE.search(value):
        _fail(code)
    path = Path(value)
    if (
        not path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts[1:])
        or str(path) != value
    ):
        _fail(code)
    return path


def _fixed_config_path() -> Path:
    path = FIXED_WA_FI_POSTGRES_BASE_BACKUP_CAPTURE_CONFIG
    if (
        not isinstance(path, Path)
        or not path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts[1:])
    ):
        _fail("BASE_BACKUP_CAPTURE_FIXED_CONFIG_PATH_INVALID")
    return path


def _fixed_command_path() -> Path:
    path = FIXED_WA_FI_PG_BASEBACKUP_COMMAND
    if (
        not isinstance(path, Path)
        or not path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts[1:])
    ):
        _fail("BASE_BACKUP_CAPTURE_COMMAND_PATH_INVALID")
    return path


def _validate_config_ancestors(path: Path) -> None:
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        _fail("BASE_BACKUP_CAPTURE_RUNTIME_CONFIG_UNSAFE")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    descriptor = -1
    try:
        descriptor = os.open("/", flags)
        for component in path.parts[1:-1]:
            next_descriptor = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
            metadata = os.fstat(descriptor)
            mode = stat.S_IMODE(metadata.st_mode)
            sticky_root_parent = metadata.st_uid == 0 and bool(metadata.st_mode & stat.S_ISVTX)
            if (
                not stat.S_ISDIR(metadata.st_mode)
                or metadata.st_uid != 0
                or (mode & 0o022 and not sticky_root_parent)
            ):
                _fail("BASE_BACKUP_CAPTURE_RUNTIME_CONFIG_UNSAFE")
    except PhysicalWaFiPostgresBaseBackupCaptureCommandError:
        raise
    except OSError:
        _fail("BASE_BACKUP_CAPTURE_RUNTIME_CONFIG_UNSAFE")
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _validate_command_ancestors(path: Path) -> None:
    """Keep the executable pathname stable until the installed runner execs it."""

    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        _fail("BASE_BACKUP_CAPTURE_COMMAND_UNAVAILABLE")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    descriptor = -1
    try:
        descriptor = os.open("/", flags)
        for component in path.parts[1:-1]:
            next_descriptor = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
            metadata = os.fstat(descriptor)
            mode = stat.S_IMODE(metadata.st_mode)
            sticky_root_parent = metadata.st_uid == 0 and bool(metadata.st_mode & stat.S_ISVTX)
            if (
                not stat.S_ISDIR(metadata.st_mode)
                or metadata.st_uid != 0
                or (mode & 0o022 and not sticky_root_parent)
            ):
                _fail("BASE_BACKUP_CAPTURE_COMMAND_UNAVAILABLE")
    except PhysicalWaFiPostgresBaseBackupCaptureCommandError:
        raise
    except OSError:
        _fail("BASE_BACKUP_CAPTURE_COMMAND_UNAVAILABLE")
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _read_fixed_runtime_config_bytes() -> bytes:
    if os.geteuid() != 0:
        _fail("BASE_BACKUP_CAPTURE_ROOT_RUNTIME_REQUIRED")
    path = _fixed_config_path()
    _validate_config_ancestors(path)
    if not hasattr(os, "O_NOFOLLOW"):
        _fail("BASE_BACKUP_CAPTURE_RUNTIME_CONFIG_UNSAFE")
    try:
        before = os.lstat(path)
        resolved = path.resolve(strict=True)
    except OSError:
        _fail("BASE_BACKUP_CAPTURE_RUNTIME_CONFIG_UNSAFE")
    if (
        resolved != path
        or stat.S_ISLNK(before.st_mode)
        or not stat.S_ISREG(before.st_mode)
        or before.st_uid != 0
        or before.st_nlink != 1
        or stat.S_IMODE(before.st_mode) != 0o600
        or not 2 <= before.st_size <= MAX_WA_FI_POSTGRES_BASE_BACKUP_CAPTURE_CONFIG_BYTES
    ):
        _fail("BASE_BACKUP_CAPTURE_RUNTIME_CONFIG_UNSAFE")
    descriptor = -1
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0))
        opened = os.fstat(descriptor)
        before_fingerprint = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mode,
            before.st_uid,
            before.st_nlink,
        )
        opened_fingerprint = (
            opened.st_dev,
            opened.st_ino,
            opened.st_size,
            opened.st_mode,
            opened.st_uid,
            opened.st_nlink,
        )
        if opened_fingerprint != before_fingerprint:
            _fail("BASE_BACKUP_CAPTURE_RUNTIME_CONFIG_UNSAFE")
        chunks: list[bytes] = []
        remaining = opened.st_size
        while remaining:
            chunk = os.read(descriptor, remaining)
            if not chunk:
                _fail("BASE_BACKUP_CAPTURE_RUNTIME_CONFIG_UNSAFE")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            _fail("BASE_BACKUP_CAPTURE_RUNTIME_CONFIG_UNSAFE")
        after = os.fstat(descriptor)
        if (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mode,
            after.st_uid,
            after.st_nlink,
        ) != opened_fingerprint:
            _fail("BASE_BACKUP_CAPTURE_RUNTIME_CONFIG_UNSAFE")
        return b"".join(chunks)
    except PhysicalWaFiPostgresBaseBackupCaptureCommandError:
        raise
    except OSError:
        _fail("BASE_BACKUP_CAPTURE_RUNTIME_CONFIG_UNSAFE")
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _parse_runtime_config() -> dict[str, Any]:
    raw = _read_fixed_runtime_config_bytes()
    try:
        decoded = json.loads(
            raw.decode("ascii", "strict"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except PhysicalWaFiPostgresBaseBackupCaptureCommandError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        _fail("BASE_BACKUP_CAPTURE_RUNTIME_CONFIG_JSON_INVALID")
    if type(decoded) is not dict or _canonical(decoded, code="BASE_BACKUP_CAPTURE_RUNTIME_CONFIG_JSON_INVALID") != raw:
        _fail("BASE_BACKUP_CAPTURE_RUNTIME_CONFIG_JSON_INVALID")
    return _exact_mapping(decoded, fields=_CONFIG_FIELDS, code="BASE_BACKUP_CAPTURE_RUNTIME_CONFIG_FIELDS_INVALID")


def _secure_root(value: object, *, code: str, exact_mode: int = 0o700) -> Path:
    path = _safe_path_text(value, code=code) if type(value) is str else value
    if not isinstance(path, Path) or not path.is_absolute():
        _fail(code)
    try:
        resolved = path.resolve(strict=True)
        metadata = os.lstat(path)
    except OSError:
        _fail(code)
    if (
        resolved != path
        or stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) != exact_mode
    ):
        _fail(code)
    return resolved


def _secure_socket_directory(value: object) -> Path:
    path = _safe_path_text(value, code="BASE_BACKUP_CAPTURE_SOURCE_ROUTE_INVALID")
    try:
        resolved = path.resolve(strict=True)
        metadata = os.lstat(path)
    except OSError:
        _fail("BASE_BACKUP_CAPTURE_SOURCE_ROUTE_INVALID")
    if (
        resolved != path
        or stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) & 0o002
    ):
        _fail("BASE_BACKUP_CAPTURE_SOURCE_ROUTE_INVALID")
    return resolved


def _roots_overlap(left: Path, right: Path) -> bool:
    try:
        left.relative_to(right)
        return True
    except ValueError:
        try:
            right.relative_to(left)
            return True
        except ValueError:
            return False


def _decode_public_key(value: object) -> bytes:
    if type(value) is not str:
        _fail("BASE_BACKUP_CAPTURE_WITNESS_KEY_INVALID")
    try:
        key = base64.b64decode(value.encode("ascii", "strict"), validate=True)
        Ed25519PublicKey.from_public_bytes(key)
    except (UnicodeEncodeError, binascii.Error, ValueError):
        _fail("BASE_BACKUP_CAPTURE_WITNESS_KEY_INVALID")
    if len(key) != 32 or key == b"\x00" * 32:
        _fail("BASE_BACKUP_CAPTURE_WITNESS_KEY_INVALID")
    return key


def _normalise_manifest_binding(value: object) -> PhysicalWalBaseBackupManifestBinding:
    item = _exact_mapping(value, fields=_MANIFEST_BINDING_FIELDS, code="BASE_BACKUP_CAPTURE_MANIFEST_INVALID")
    if item["source_site"] != "webapp_fi" or item["destination_site"] != "webapp_ir":
        _fail("BASE_BACKUP_CAPTURE_DIRECTION_FORBIDDEN")
    try:
        return PhysicalWalBaseBackupManifestBinding(
            source_site="webapp_fi",
            destination_site="webapp_ir",
            campaign_id=_safe_text(item["campaign_id"], pattern=CAMPAIGN_ID_RE, code="BASE_BACKUP_CAPTURE_MANIFEST_INVALID"),
            release_sha=_safe_text(item["release_sha"], pattern=RELEASE_SHA_RE, code="BASE_BACKUP_CAPTURE_MANIFEST_INVALID"),
            baseline_generation_id=_safe_text(item["baseline_generation_id"], pattern=STREAM_GENERATION_ID_RE, code="BASE_BACKUP_CAPTURE_MANIFEST_INVALID"),
            database_system_identifier=_safe_text(item["database_system_identifier"], pattern=_SYSTEM_IDENTIFIER_RE, code="BASE_BACKUP_CAPTURE_MANIFEST_INVALID"),
            timeline_id=_positive_int(item["timeline_id"], maximum=0xFFFFFFFF, code="BASE_BACKUP_CAPTURE_MANIFEST_INVALID"),
            wal_segment_size_bytes=_positive_int(item["wal_segment_size_bytes"], maximum=1024 * 1024 * 1024, code="BASE_BACKUP_CAPTURE_MANIFEST_INVALID"),
            baseline_wal_lsn=_safe_text(item["baseline_wal_lsn"], pattern=_LSN_RE, code="BASE_BACKUP_CAPTURE_MANIFEST_INVALID"),
            wal_chain_start_lsn=_safe_text(item["wal_chain_start_lsn"], pattern=_LSN_RE, code="BASE_BACKUP_CAPTURE_MANIFEST_INVALID"),
            base_backup_end_lsn=_safe_text(item["base_backup_end_lsn"], pattern=_LSN_RE, code="BASE_BACKUP_CAPTURE_MANIFEST_INVALID"),
            destination_age_recipient=_safe_text(item["destination_age_recipient"], pattern=AGE_RECIPIENT_RE, code="BASE_BACKUP_CAPTURE_MANIFEST_INVALID"),
        )
    except PhysicalWaFiPostgresBaseBackupCaptureCommandError:
        raise


def _normalise_witness_term(value: object, *, now: datetime) -> VerifiedObjectDeltaRoleMatrixWitnessedTerm:
    item = _exact_mapping(value, fields=_WITNESS_TERM_FIELDS, code="BASE_BACKUP_CAPTURE_WITNESS_TERM_INVALID")
    key = _decode_public_key(item["public_key_base64"])
    maximum_duration = _positive_int(
        item["maximum_lease_duration_seconds"], maximum=300, code="BASE_BACKUP_CAPTURE_WITNESS_TERM_INVALID"
    )
    safety_margin = _positive_int(
        item["safety_margin_seconds"], maximum=60, code="BASE_BACKUP_CAPTURE_WITNESS_TERM_INVALID"
    )
    if safety_margin >= maximum_duration or type(item["proof"]) is not dict:
        _fail("BASE_BACKUP_CAPTURE_WITNESS_TERM_INVALID")
    try:
        return verify_object_delta_role_matrix_witnessed_term(
            item["proof"],
            witness_public_key=key,
            maximum_lease_duration_seconds=maximum_duration,
            safety_margin_seconds=safety_margin,
            now=now,
        )
    except ObjectDeltaRoleMatrixRolloverError:
        _fail("BASE_BACKUP_CAPTURE_WITNESS_TERM_INVALID")


def _preflight_manifest_and_term(
    *,
    manifest_binding: PhysicalWalBaseBackupManifestBinding,
    witnessed_term: VerifiedObjectDeltaRoleMatrixWitnessedTerm,
    now: datetime,
) -> None:
    """Reuse the spool's exact route/term normalizer without touching a file."""

    placeholder = PhysicalWalBaseBackupCompletedArtifact(
        artifact_name="preflight-basebackup-0001.tar",
        plaintext_sha256="1" * 64,
        plaintext_bytes=1,
        completion_attestation_sha256="2" * 64,
    )
    try:
        authorize_physical_wal_base_backup_binding(
            manifest_binding=manifest_binding,
            completed_artifact=placeholder,
            witnessed_term=witnessed_term,
            now=now,
        )
    except PhysicalWalBaseBackupSpoolError:
        _fail("BASE_BACKUP_CAPTURE_WITNESS_OR_MANIFEST_INVALID")


def _normalise_capture(value: object) -> _CaptureFacts:
    item = _exact_mapping(value, fields=_CAPTURE_FIELDS, code="BASE_BACKUP_CAPTURE_CONFIG_INVALID")
    if (
        item["source_socket_transport"] != "unix-socket-only"
        or item["source_port"] != 5432
        or item["password_prompt"] != "forbidden"
    ):
        _fail("BASE_BACKUP_CAPTURE_SOURCE_ROUTE_INVALID")
    socket_directory = _secure_socket_directory(item["source_socket_directory"])
    source_role = _safe_text(item["source_role"], pattern=_ROLE_RE, code="BASE_BACKUP_CAPTURE_SOURCE_ROUTE_INVALID")
    if source_role != "replication":
        _fail("BASE_BACKUP_CAPTURE_SOURCE_ROUTE_INVALID")
    capture_root = _secure_root(item["capture_root"], code="BASE_BACKUP_CAPTURE_STAGING_ROOT_UNSAFE")
    completed_source_root = _secure_root(item["completed_source_root"], code="BASE_BACKUP_CAPTURE_SOURCE_ROOT_UNSAFE")
    spool_root = _secure_root(item["spool_root"], code="BASE_BACKUP_CAPTURE_SPOOL_ROOT_UNSAFE")
    if (
        _roots_overlap(capture_root, completed_source_root)
        or _roots_overlap(capture_root, spool_root)
        or _roots_overlap(completed_source_root, spool_root)
    ):
        _fail("BASE_BACKUP_CAPTURE_ROOTS_OVERLAP")
    artifact_name = _safe_text(item["completed_artifact_name"], pattern=_ARTIFACT_NAME_RE, code="BASE_BACKUP_CAPTURE_CONFIG_INVALID")
    maximum_bytes = _positive_int(
        item["maximum_base_backup_bytes"], maximum=MAX_PHYSICAL_BASE_BACKUP_BYTES, code="BASE_BACKUP_CAPTURE_CONFIG_INVALID"
    )
    reserve_bytes = _positive_int(
        item["spool_reserve_bytes"], maximum=MAX_SPOOL_RESERVE_BYTES, code="BASE_BACKUP_CAPTURE_CONFIG_INVALID"
    )
    return _CaptureFacts(
        socket_directory=socket_directory,
        source_role=source_role,
        capture_root=capture_root,
        completed_source_root=completed_source_root,
        completed_artifact_name=artifact_name,
        spool_config=PhysicalWalBaseBackupSpoolConfig(
            source_root=completed_source_root,
            spool_root=spool_root,
            maximum_base_backup_bytes=maximum_bytes,
            spool_reserve_bytes=reserve_bytes,
        ),
        command_sha256=_sha256(item["pg_basebackup_sha256"], code="BASE_BACKUP_CAPTURE_COMMAND_IDENTITY_INVALID"),
    )


def _normalise_uploader(
    value: object,
    *,
    manifest_binding: PhysicalWalBaseBackupManifestBinding,
    capture: _CaptureFacts,
) -> PhysicalWalObjectStorageUploaderConfig:
    item = _exact_mapping(value, fields=_UPLOADER_FIELDS, code="BASE_BACKUP_CAPTURE_UPLOADER_CONFIG_INVALID")
    if (
        item["source_site"] != "webapp_fi"
        or item["destination_site"] != "webapp_ir"
        or item["enabled"] is not True
        or item["direct_site_control"] != "forbidden"
        or item["destination_object_ingest"] != "pull-only"
        or item["destination_age_recipient"] != manifest_binding.destination_age_recipient
        or type(item["spool_owner_uid"]) is not int
        or item["spool_owner_uid"] != 0
        or type(item["maximum_plaintext_bytes"]) is not int
        or item["maximum_plaintext_bytes"] != capture.spool_config.maximum_base_backup_bytes
    ):
        _fail("BASE_BACKUP_CAPTURE_UPLOADER_CONFIG_INVALID")
    workspace = _secure_root(item["workspace"], code="BASE_BACKUP_CAPTURE_UPLOADER_CONFIG_INVALID")
    spool_root = _secure_root(item["spool_root"], code="BASE_BACKUP_CAPTURE_UPLOADER_CONFIG_INVALID")
    if spool_root != capture.spool_config.spool_root or any(
        _roots_overlap(workspace, root)
        for root in (capture.capture_root, capture.completed_source_root, capture.spool_config.spool_root)
    ):
        _fail("BASE_BACKUP_CAPTURE_UPLOADER_CONFIG_INVALID")
    return PhysicalWalObjectStorageUploaderConfig(
        source_site="webapp_fi",
        destination_site="webapp_ir",
        workspace=workspace,
        spool_root=spool_root,
        spool_owner_uid=0,
        bucket=_safe_text(item["bucket"], pattern=_BUCKET_RE, code="BASE_BACKUP_CAPTURE_UPLOADER_CONFIG_INVALID"),
        region=_safe_text(item["region"], pattern=_REGION_RE, code="BASE_BACKUP_CAPTURE_UPLOADER_CONFIG_INVALID"),
        destination_age_recipient=_safe_text(item["destination_age_recipient"], pattern=AGE_RECIPIENT_RE, code="BASE_BACKUP_CAPTURE_UPLOADER_CONFIG_INVALID"),
        object_storage_namespace=PHYSICAL_WAL_NORMAL_OBJECT_STORAGE_NAMESPACE,
        enabled=True,
        maximum_plaintext_bytes=capture.spool_config.maximum_base_backup_bytes,
        direct_site_control="forbidden",
        destination_object_ingest="pull-only",
    )


def _secure_command(expected_sha256: str) -> _CommandFacts:
    path = _fixed_command_path()
    _validate_command_ancestors(path)
    if not hasattr(os, "O_NOFOLLOW"):
        _fail("BASE_BACKUP_CAPTURE_COMMAND_UNAVAILABLE")
    try:
        before = os.lstat(path)
        resolved = path.resolve(strict=True)
    except OSError:
        _fail("BASE_BACKUP_CAPTURE_COMMAND_UNAVAILABLE")
    if (
        resolved != path
        or stat.S_ISLNK(before.st_mode)
        or not stat.S_ISREG(before.st_mode)
        or before.st_uid != 0
        or before.st_nlink != 1
        or stat.S_IMODE(before.st_mode) & 0o022
        or not before.st_mode & stat.S_IXUSR
        or not 1 <= before.st_size <= MAX_WA_FI_PG_BASEBACKUP_BINARY_BYTES
    ):
        _fail("BASE_BACKUP_CAPTURE_COMMAND_UNAVAILABLE")
    descriptor = -1
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0))
        opened = os.fstat(descriptor)
        expected_fingerprint = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mode,
            before.st_uid,
            before.st_nlink,
        )
        actual_fingerprint = (
            opened.st_dev,
            opened.st_ino,
            opened.st_size,
            opened.st_mode,
            opened.st_uid,
            opened.st_nlink,
        )
        if actual_fingerprint != expected_fingerprint:
            _fail("BASE_BACKUP_CAPTURE_COMMAND_UNAVAILABLE")
        digest = hashlib.sha256()
        total = 0
        while True:
            chunk = os.read(descriptor, _COPY_CHUNK_BYTES)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_WA_FI_PG_BASEBACKUP_BINARY_BYTES:
                _fail("BASE_BACKUP_CAPTURE_COMMAND_UNAVAILABLE")
            digest.update(chunk)
        after = os.fstat(descriptor)
        if (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mode,
            after.st_uid,
            after.st_nlink,
        ) != actual_fingerprint or total != before.st_size:
            _fail("BASE_BACKUP_CAPTURE_COMMAND_UNAVAILABLE")
    except PhysicalWaFiPostgresBaseBackupCaptureCommandError:
        raise
    except OSError:
        _fail("BASE_BACKUP_CAPTURE_COMMAND_UNAVAILABLE")
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    digest_text = digest.hexdigest()
    if digest_text != expected_sha256:
        _fail("BASE_BACKUP_CAPTURE_COMMAND_IDENTITY_INVALID")
    return _CommandFacts(path=resolved, sha256=digest_text)


def _normalise_runtime(now: datetime) -> _RuntimeFacts:
    item = _parse_runtime_config()
    if (
        item["schema"] != PHYSICAL_WA_FI_POSTGRES_BASE_BACKUP_CAPTURE_RUNTIME_SCHEMA
        or type(item["version"]) is not int
        or item["version"] != _RUNTIME_VERSION
    ):
        _fail("BASE_BACKUP_CAPTURE_RUNTIME_CONFIG_SCHEMA_INVALID")
    if item["enabled"] is not True:
        _fail("BASE_BACKUP_CAPTURE_DISABLED")
    configuration_sha256 = _sha256(item["configuration_sha256"], code="BASE_BACKUP_CAPTURE_CONFIG_PIN_INVALID")
    unpinned = dict(item)
    del unpinned["configuration_sha256"]
    if hashlib.sha256(_canonical(unpinned, code="BASE_BACKUP_CAPTURE_CONFIG_PIN_INVALID")).hexdigest() != configuration_sha256:
        _fail("BASE_BACKUP_CAPTURE_CONFIG_PIN_INVALID")
    if (
        item["source_site"] != "webapp_fi"
        or item["destination_site"] != "webapp_ir"
        or item["direct_site_control"] != "forbidden"
        or item["destination_object_ingest"] != "pull-only"
    ):
        _fail("BASE_BACKUP_CAPTURE_DIRECTION_FORBIDDEN")
    capture = _normalise_capture(item["capture"])
    manifest_binding = _normalise_manifest_binding(item["manifest_binding"])
    term = _normalise_witness_term(item["witness_term"], now=now)
    _preflight_manifest_and_term(manifest_binding=manifest_binding, witnessed_term=term, now=now)
    uploader_config = _normalise_uploader(
        item["object_storage_uploader"], manifest_binding=manifest_binding, capture=capture
    )
    command = _secure_command(capture.command_sha256)
    return _RuntimeFacts(
        configuration_sha256=configuration_sha256,
        manifest_binding=manifest_binding,
        witnessed_term=term,
        capture=capture,
        uploader_config=uploader_config,
        command=command,
    )


def _require_same_runtime(before: _RuntimeFacts, after: _RuntimeFacts) -> None:
    if (
        after.configuration_sha256 != before.configuration_sha256
        or after.command != before.command
        or after.manifest_binding != before.manifest_binding
        or after.witnessed_term != before.witnessed_term
        or after.capture != before.capture
        or after.uploader_config != before.uploader_config
    ):
        _fail("BASE_BACKUP_CAPTURE_RUNTIME_CHANGED")


def _secure_child(root: Path) -> Path:
    if not hasattr(os, "O_NOFOLLOW"):
        _fail("BASE_BACKUP_CAPTURE_STAGING_UNSAFE")
    for _ in range(8):
        candidate = root / (_RUN_DIRECTORY_PREFIX + secrets.token_hex(16))
        try:
            candidate.mkdir(mode=0o700)
        except FileExistsError:
            continue
        except OSError:
            _fail("BASE_BACKUP_CAPTURE_STAGING_UNSAFE")
        try:
            metadata = os.lstat(candidate)
            resolved = candidate.resolve(strict=True)
        except OSError:
            _fail("BASE_BACKUP_CAPTURE_STAGING_UNSAFE")
        if (
            resolved != candidate
            or stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != 0
            or stat.S_IMODE(metadata.st_mode) != 0o700
        ):
            _fail("BASE_BACKUP_CAPTURE_STAGING_UNSAFE")
        return resolved
    _fail("BASE_BACKUP_CAPTURE_STAGING_UNSAFE")


def _verify_staging_directory(path: Path) -> None:
    try:
        metadata = os.lstat(path)
        resolved = path.resolve(strict=True)
    except OSError:
        _fail("BASE_BACKUP_CAPTURE_STAGING_UNSAFE")
    if (
        resolved != path
        or stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        _fail("BASE_BACKUP_CAPTURE_STAGING_UNSAFE")


def _file_fingerprint(metadata: os.stat_result) -> tuple[int, int, int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
        metadata.st_mode,
        metadata.st_nlink,
    )


def _open_secure_regular(
    path: Path,
    *,
    maximum_bytes: int,
    code: str,
) -> tuple[int, tuple[int, int, int, int, int, int, int]]:
    if not hasattr(os, "O_NOFOLLOW"):
        _fail(code)
    try:
        before = os.lstat(path)
        resolved = path.resolve(strict=True)
    except OSError:
        _fail(code)
    if (
        resolved != path
        or stat.S_ISLNK(before.st_mode)
        or not stat.S_ISREG(before.st_mode)
        or before.st_uid != 0
        or before.st_nlink != 1
        or stat.S_IMODE(before.st_mode) != 0o600
        or not 1 <= before.st_size <= maximum_bytes
    ):
        _fail(code)
    descriptor = -1
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0))
        opened = os.fstat(descriptor)
        if _file_fingerprint(opened) != _file_fingerprint(before):
            _fail(code)
        return descriptor, _file_fingerprint(opened)
    except PhysicalWaFiPostgresBaseBackupCaptureCommandError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError:
        if descriptor >= 0:
            os.close(descriptor)
        _fail(code)


def _read_small_secure_file(path: Path, *, maximum_bytes: int, code: str) -> bytes:
    descriptor, before = _open_secure_regular(path, maximum_bytes=maximum_bytes, code=code)
    try:
        chunks: list[bytes] = []
        remaining = before[2]
        while remaining:
            chunk = os.read(descriptor, remaining)
            if not chunk:
                _fail(code)
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            _fail(code)
        if _file_fingerprint(os.fstat(descriptor)) != before:
            _fail(code)
        return b"".join(chunks)
    except OSError:
        _fail(code)
    finally:
        os.close(descriptor)


def _digest_secure_file(path: Path, *, maximum_bytes: int, code: str) -> tuple[str, int]:
    descriptor, before = _open_secure_regular(path, maximum_bytes=maximum_bytes, code=code)
    try:
        digest = hashlib.sha256()
        total = 0
        while True:
            chunk = os.read(descriptor, _COPY_CHUNK_BYTES)
            if not chunk:
                break
            total += len(chunk)
            if total > maximum_bytes:
                _fail(code)
            digest.update(chunk)
        if total != before[2] or _file_fingerprint(os.fstat(descriptor)) != before:
            _fail(code)
        return digest.hexdigest(), total
    except OSError:
        _fail(code)
    finally:
        os.close(descriptor)


def _parse_completion(raw: bytes, *, runtime: _RuntimeFacts, artifact_sha256: str, artifact_bytes: int, attestation_sha256: str) -> None:
    try:
        value = json.loads(
            raw.decode("ascii", "strict"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except PhysicalWaFiPostgresBaseBackupCaptureCommandError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        _fail("BASE_BACKUP_CAPTURE_COMPLETION_INVALID")
    if type(value) is not dict or _canonical(value, code="BASE_BACKUP_CAPTURE_COMPLETION_INVALID") != raw:
        _fail("BASE_BACKUP_CAPTURE_COMPLETION_INVALID")
    item = _exact_mapping(value, fields=_COMPLETION_FIELDS, code="BASE_BACKUP_CAPTURE_COMPLETION_INVALID")
    term = runtime.witnessed_term
    if (
        item["schema"] != PHYSICAL_WA_FI_POSTGRES_BASE_BACKUP_CAPTURE_COMPLETION_SCHEMA
        or type(item["version"]) is not int
        or item["version"] != _COMPLETION_VERSION
        or item["status"] != "completed"
        or item["configuration_sha256"] != runtime.configuration_sha256
        or item["command_sha256"] != runtime.command.sha256
        or item["source_site"] != "webapp_fi"
        or item["destination_site"] != "webapp_ir"
        or item["artifact_filename"] != _RUN_ARTIFACT_FILENAME
        or _sha256(item["plaintext_sha256"], code="BASE_BACKUP_CAPTURE_COMPLETION_INVALID") != artifact_sha256
        or type(item["plaintext_bytes"]) is not int
        or item["plaintext_bytes"] != artifact_bytes
        or _sha256(item["completion_attestation_sha256"], code="BASE_BACKUP_CAPTURE_COMPLETION_INVALID") != attestation_sha256
        or type(item["writer_epoch"]) is not int
        or item["writer_epoch"] != term.writer_epoch
        or item["writer_lease_id"] != term.writer_lease_id
        or item["witness_transition_id"] != term.witness_transition_id
        or item["witnessed_term_proof_sha256"] != term.proof_sha256
    ):
        _fail("BASE_BACKUP_CAPTURE_COMPLETION_INVALID")


def _copy_completed_artifact(
    *,
    source: Path,
    destination_root: Path,
    artifact_name: str,
    expected_sha256: str,
    expected_bytes: int,
) -> Path:
    destination = destination_root / artifact_name
    try:
        existing = os.lstat(destination)
    except FileNotFoundError:
        existing = None
    except OSError:
        _fail("BASE_BACKUP_CAPTURE_SOURCE_ARTIFACT_UNSAFE")
    if existing is not None:
        actual_sha256, actual_bytes = _digest_secure_file(
            destination,
            maximum_bytes=expected_bytes,
            code="BASE_BACKUP_CAPTURE_SOURCE_ARTIFACT_UNSAFE",
        )
        if actual_sha256 != expected_sha256 or actual_bytes != expected_bytes:
            _fail("BASE_BACKUP_CAPTURE_SOURCE_ARTIFACT_UNSAFE")
        return destination
    source_fd, source_before = _open_secure_regular(
        source,
        maximum_bytes=expected_bytes,
        code="BASE_BACKUP_CAPTURE_ARTIFACT_INVALID",
    )
    temporary = destination_root / ("." + artifact_name + "." + secrets.token_hex(16) + ".tmp")
    destination_fd = -1
    linked = False
    try:
        destination_fd = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
            0o600,
        )
        digest = hashlib.sha256()
        total = 0
        while True:
            chunk = os.read(source_fd, _COPY_CHUNK_BYTES)
            if not chunk:
                break
            total += len(chunk)
            if total > expected_bytes:
                _fail("BASE_BACKUP_CAPTURE_ARTIFACT_INVALID")
            digest.update(chunk)
            view = memoryview(chunk)
            while view:
                written = os.write(destination_fd, view)
                if type(written) is not int or written <= 0:
                    _fail("BASE_BACKUP_CAPTURE_SOURCE_ARTIFACT_UNSAFE")
                view = view[written:]
        os.fsync(destination_fd)
        if (
            total != expected_bytes
            or digest.hexdigest() != expected_sha256
            or _file_fingerprint(os.fstat(source_fd)) != source_before
        ):
            _fail("BASE_BACKUP_CAPTURE_ARTIFACT_INVALID")
        os.close(destination_fd)
        destination_fd = -1
        os.link(temporary, destination, follow_symlinks=False)
        linked = True
        os.unlink(temporary)
        temporary = Path("")
        if not hasattr(os, "O_DIRECTORY"):
            _fail("BASE_BACKUP_CAPTURE_SOURCE_ARTIFACT_UNSAFE")
        root_fd = os.open(destination_root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(root_fd)
        finally:
            os.close(root_fd)
    except FileExistsError:
        if destination_fd >= 0:
            os.close(destination_fd)
            destination_fd = -1
        try:
            os.unlink(temporary)
        except OSError:
            pass
        actual_sha256, actual_bytes = _digest_secure_file(
            destination,
            maximum_bytes=expected_bytes,
            code="BASE_BACKUP_CAPTURE_SOURCE_ARTIFACT_UNSAFE",
        )
        if actual_sha256 != expected_sha256 or actual_bytes != expected_bytes:
            _fail("BASE_BACKUP_CAPTURE_SOURCE_ARTIFACT_UNSAFE")
    except PhysicalWaFiPostgresBaseBackupCaptureCommandError:
        raise
    except OSError:
        _fail("BASE_BACKUP_CAPTURE_SOURCE_ARTIFACT_UNSAFE")
    finally:
        if destination_fd >= 0:
            os.close(destination_fd)
        os.close(source_fd)
        if not linked:
            try:
                os.unlink(temporary)
            except (FileNotFoundError, OSError):
                pass
    actual_sha256, actual_bytes = _digest_secure_file(
        destination,
        maximum_bytes=expected_bytes,
        code="BASE_BACKUP_CAPTURE_SOURCE_ARTIFACT_UNSAFE",
    )
    if actual_sha256 != expected_sha256 or actual_bytes != expected_bytes:
        _fail("BASE_BACKUP_CAPTURE_SOURCE_ARTIFACT_UNSAFE")
    return destination


def _parse_empty_arguments(arguments: object) -> None:
    if not isinstance(arguments, (list, tuple)) or len(arguments) != 0:
        _fail("BASE_BACKUP_CAPTURE_ARGUMENTS_FORBIDDEN")


def _build_uploader(
    *,
    runtime: _RuntimeFacts,
    uploader_factory: PhysicalWaFiPostgresBaseBackupUploaderFactory | None,
    age_encryptor_factory: Callable[[], PhysicalWalAgeEncryptor],
    object_storage_client_factory: Callable[[], PhysicalWalObjectStorageClient],
) -> PhysicalWalBaseBackupUploader:
    try:
        if uploader_factory is None:
            uploader: PhysicalWalBaseBackupUploader = PhysicalWalBaseBackupObjectStorageUploader(
                config=runtime.uploader_config,
                age_encryptor_factory=age_encryptor_factory,
                client_factory=object_storage_client_factory,
            )
        else:
            uploader = uploader_factory(
                config=runtime.uploader_config,
                age_encryptor_factory=age_encryptor_factory,
                object_storage_client_factory=object_storage_client_factory,
            )
    except Exception:
        _fail("BASE_BACKUP_CAPTURE_UPLOADER_FACTORY_FAILED")
    if uploader is None or not callable(getattr(uploader, "upload", None)):
        _fail("BASE_BACKUP_CAPTURE_UPLOADER_INVALID")
    return uploader


def _read_term_recheck_clock(clock: Callable[[], datetime], *, code: str) -> datetime:
    try:
        value = clock()
    except Exception:
        _fail(code)
    return _utc(value, code=code)


def execute_wa_fi_postgres_base_backup_capture_command(
    arguments: object,
    *,
    now: datetime,
    term_recheck_clock: Callable[[], datetime] | None,
    runner: PhysicalWaFiPostgresBaseBackupRunner | None,
    age_encryptor_factory: Callable[[], PhysicalWalAgeEncryptor] | None,
    object_storage_client_factory: Callable[[], PhysicalWalObjectStorageClient] | None,
    uploader_factory: PhysicalWaFiPostgresBaseBackupUploaderFactory | None = None,
) -> PhysicalWaFiPostgresBaseBackupCaptureCommandResult:
    """Run one fixed local capture and hand it to the existing base spool.

    A compliant runner is an installed, root-controlled adapter that directly
    execs only the supplied absolute PostgreSQL 15 command.  This function
    itself does not start a process.  Any failed validation before ``run``
    leaves runner, uploader, age, and Object Storage untouched.
    """

    _parse_empty_arguments(arguments)
    observed_now = _utc(now, code="BASE_BACKUP_CAPTURE_CLOCK_INVALID")
    runtime = _normalise_runtime(observed_now)
    if (
        runner is None
        or not callable(getattr(runner, "run", None))
        or term_recheck_clock is None
        or not callable(term_recheck_clock)
        or age_encryptor_factory is None
        or not callable(age_encryptor_factory)
        or object_storage_client_factory is None
        or not callable(object_storage_client_factory)
        or (uploader_factory is not None and not callable(uploader_factory))
    ):
        _fail("BASE_BACKUP_CAPTURE_DEPENDENCIES_INVALID")

    output_directory = _secure_child(runtime.capture.capture_root)
    invocation = PhysicalWaFiPostgresBaseBackupInvocation(
        command_path=runtime.command.path,
        command_sha256=runtime.command.sha256,
        arguments=(
            str(runtime.command.path),
            "--host=" + str(runtime.capture.socket_directory),
            "--port=5432",
            "--username=" + runtime.capture.source_role,
            "--no-password",
            "--format=tar",
            "--wal-method=none",
            "--checkpoint=fast",
            "--pgdata=" + str(output_directory),
        ),
        environment=(),
        output_directory=output_directory,
        configuration_sha256=runtime.configuration_sha256,
        writer_epoch=runtime.witnessed_term.writer_epoch,
        writer_lease_id=runtime.witnessed_term.writer_lease_id,
        witness_transition_id=runtime.witnessed_term.witness_transition_id,
        witnessed_term_proof_sha256=runtime.witnessed_term.proof_sha256,
    )
    try:
        runner_result = runner.run(invocation=invocation)
    except Exception:
        _fail("BASE_BACKUP_CAPTURE_RUNNER_FAILED")
    if (
        type(runner_result) is not PhysicalWaFiPostgresBaseBackupRunnerResult
        or type(runner_result.exit_code) is not int
        or runner_result.exit_code != 0
    ):
        _fail("BASE_BACKUP_CAPTURE_RUNNER_FAILED")

    completion_now = _read_term_recheck_clock(
        term_recheck_clock, code="BASE_BACKUP_CAPTURE_COMPLETION_CLOCK_INVALID"
    )
    if completion_now < observed_now:
        _fail("BASE_BACKUP_CAPTURE_COMPLETION_CLOCK_INVALID")
    post_runner_runtime = _normalise_runtime(completion_now)
    _require_same_runtime(runtime, post_runner_runtime)
    _verify_staging_directory(output_directory)
    artifact_path = output_directory / _RUN_ARTIFACT_FILENAME
    completion_path = output_directory / _RUN_COMPLETION_FILENAME
    attestation_path = output_directory / _RUN_ATTESTATION_FILENAME
    artifact_sha256, artifact_bytes = _digest_secure_file(
        artifact_path,
        maximum_bytes=runtime.capture.spool_config.maximum_base_backup_bytes,
        code="BASE_BACKUP_CAPTURE_ARTIFACT_INVALID",
    )
    attestation = _read_small_secure_file(
        attestation_path,
        maximum_bytes=MAX_WA_FI_POSTGRES_BASE_BACKUP_ATTESTATION_BYTES,
        code="BASE_BACKUP_CAPTURE_ATTESTATION_INVALID",
    )
    completion = _read_small_secure_file(
        completion_path,
        maximum_bytes=MAX_WA_FI_POSTGRES_BASE_BACKUP_COMPLETION_BYTES,
        code="BASE_BACKUP_CAPTURE_COMPLETION_INVALID",
    )
    attestation_sha256 = hashlib.sha256(attestation).hexdigest()
    _parse_completion(
        completion,
        runtime=post_runner_runtime,
        artifact_sha256=artifact_sha256,
        artifact_bytes=artifact_bytes,
        attestation_sha256=attestation_sha256,
    )
    completed_artifact = PhysicalWalBaseBackupCompletedArtifact(
        artifact_name=runtime.capture.completed_artifact_name,
        plaintext_sha256=artifact_sha256,
        plaintext_bytes=artifact_bytes,
        completion_attestation_sha256=attestation_sha256,
    )
    _copy_completed_artifact(
        source=artifact_path,
        destination_root=runtime.capture.completed_source_root,
        artifact_name=runtime.capture.completed_artifact_name,
        expected_sha256=artifact_sha256,
        expected_bytes=artifact_bytes,
    )

    # Copying a large base backup can take materially longer than the runner.
    # Obtain a fresh pinned term immediately before the effectful handoff.
    handoff_now = _read_term_recheck_clock(
        term_recheck_clock, code="BASE_BACKUP_CAPTURE_COMPLETION_CLOCK_INVALID"
    )
    if handoff_now < completion_now:
        _fail("BASE_BACKUP_CAPTURE_COMPLETION_CLOCK_INVALID")
    handoff_runtime = _normalise_runtime(handoff_now)
    _require_same_runtime(runtime, handoff_runtime)
    try:
        verified_binding = authorize_physical_wal_base_backup_binding(
            manifest_binding=handoff_runtime.manifest_binding,
            completed_artifact=completed_artifact,
            witnessed_term=handoff_runtime.witnessed_term,
            now=handoff_now,
        )
    except PhysicalWalBaseBackupSpoolError:
        _fail("BASE_BACKUP_CAPTURE_COMPLETION_BINDING_INVALID")
    uploader = _build_uploader(
        runtime=handoff_runtime,
        uploader_factory=uploader_factory,
        age_encryptor_factory=age_encryptor_factory,
        object_storage_client_factory=object_storage_client_factory,
    )

    def final_term_recheck_clock() -> datetime:
        recheck_now = _read_term_recheck_clock(
            term_recheck_clock, code="BASE_BACKUP_CAPTURE_COMPLETION_CLOCK_INVALID"
        )
        if recheck_now < handoff_now:
            _fail("BASE_BACKUP_CAPTURE_COMPLETION_CLOCK_INVALID")
        final_runtime = _normalise_runtime(recheck_now)
        _require_same_runtime(runtime, final_runtime)
        return recheck_now

    try:
        captured: PhysicalWalBaseBackupSpoolResult = capture_physical_wal_base_backup(
            config=runtime.capture.spool_config,
            verified_binding=verified_binding,
            uploader=uploader,
            now=handoff_now,
            term_recheck_clock=final_term_recheck_clock,
        )
    except PhysicalWalBaseBackupSpoolError:
        _fail("BASE_BACKUP_CAPTURE_HANDOFF_FAILED")
    return PhysicalWaFiPostgresBaseBackupCaptureCommandResult(
        completed_artifact_sha256=artifact_sha256,
        completed_artifact_bytes=artifact_bytes,
        completion_attestation_sha256=attestation_sha256,
        route_binding_sha256=verified_binding.route_binding_sha256,
        object_key=captured.object_key,
        object_version_id=captured.object_version_id,
    )
