"""Root-owned WA-FI PostgreSQL ``archive_command`` adapter boundary.

This module is the concrete local entrypoint behind the rendered primary
contract::

    wal-spool --config /etc/trading-bot/physical-postgres/primary/wal-spool.json \
      --wal-file %f --wal-path %p

It deliberately does not execute PostgreSQL, a shell, SSH, Docker, a route
change, or a promotion.  When a separately installed root-owned binary calls
``execute_wa_fi_postgres_archive_command`` with explicit age and S3 factories,
the call can publish one completed WAL segment through the existing immutable
local spool and Object-Storage uploader.  Merely importing this module has no
filesystem, credential, age, S3, or network side effect.

The CLI is intentionally hand-parsed rather than permissive: there is one
fixed root-only configuration pathname and exactly three option/value pairs.
No environment or caller-provided config path/factory module is accepted.
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
import stat
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from core.append_only_sync_delta_batch import (
    AppendOnlySyncDeltaBatchError,
    CAMPAIGN_ID_RE,
    RELEASE_SHA_RE,
    SHA256_RE,
    STREAM_GENERATION_ID_RE,
    canonical_json_bytes,
)
from core.object_delta_role_matrix_rollover import (
    ObjectDeltaRoleMatrixRolloverError,
    verify_object_delta_role_matrix_witnessed_term,
)
from core.object_delta_transport_binding import AGE_RECIPIENT_RE
from core.physical_wal_archive_spool import (
    PhysicalWalArchiveManifestBinding,
    PhysicalWalArchiveSpoolConfig,
    PhysicalWalArchiveSpoolError,
    PhysicalWalArchiveSpoolResult,
    VerifiedPhysicalWalArchiveBinding,
    archive_physical_wal_segment,
    authorize_physical_wal_archive_binding,
    parse_postgresql_wal_segment_name,
)
from core.physical_wal_object_manifest import (
    PHYSICAL_WAL_NORMAL_OBJECT_STORAGE_NAMESPACE,
    PHYSICAL_WAL_SUPPORTED_SEGMENT_SIZES_BYTES,
)
from core.physical_wal_object_storage_uploader import (
    PhysicalWalAgeEncryptor,
    PhysicalWalObjectStorageClient,
    PhysicalWalObjectStorageUploader,
    PhysicalWalObjectStorageUploaderConfig,
    PhysicalWalObjectStorageUploaderError,
)


__all__ = (
    "FIXED_WA_FI_POSTGRES_ARCHIVE_COMMAND_CONFIG",
    "PHYSICAL_WA_FI_POSTGRES_ARCHIVE_COMMAND_DEFAULT_ENABLED",
    "PHYSICAL_WA_FI_POSTGRES_ARCHIVE_COMMAND_REPORT_SCHEMA",
    "PHYSICAL_WA_FI_POSTGRES_ARCHIVE_COMMAND_RUNTIME_SCHEMA",
    "PhysicalWaFiPostgresArchiveCommandError",
    "PhysicalWaFiPostgresArchiveCommandResult",
    "execute_wa_fi_postgres_archive_command",
    "render_wa_fi_postgres_archive_command_error",
    "render_wa_fi_postgres_archive_command_result",
)


PHYSICAL_WA_FI_POSTGRES_ARCHIVE_COMMAND_RUNTIME_SCHEMA = (
    "gold-trade-physical-wa-fi-postgres-archive-command-runtime-v1"
)
PHYSICAL_WA_FI_POSTGRES_ARCHIVE_COMMAND_REPORT_SCHEMA = (
    "gold-trade-physical-wa-fi-postgres-archive-command-report-v1"
)
PHYSICAL_WA_FI_POSTGRES_ARCHIVE_COMMAND_DEFAULT_ENABLED = False

# The rendered primary PostgreSQL configuration already uses this exact value.
# It is intentionally a module constant rather than a CLI/environment setting.
FIXED_WA_FI_POSTGRES_ARCHIVE_COMMAND_CONFIG = Path(
    "/etc/trading-bot/physical-postgres/primary/wal-spool.json"
)

MAX_WA_FI_POSTGRES_ARCHIVE_COMMAND_CONFIG_BYTES = 128 * 1024
_RUNTIME_VERSION = 1
_CONFIG_FIELDS = frozenset(
    {
        "schema",
        "version",
        "enabled",
        "configuration_sha256",
        "source_site",
        "destination_site",
        "archive_spool",
        "manifest_binding",
        "route_binding_sha256",
        "witness_term",
        "object_storage_uploader",
    }
)
_ARCHIVE_SPOOL_FIELDS = frozenset(
    {"wal_source_root", "spool_root", "wal_segment_size_bytes"}
)
_MANIFEST_BINDING_FIELDS = frozenset(
    {
        "source_site",
        "destination_site",
        "campaign_id",
        "release_sha",
        "stream_generation_id",
        "baseline_generation_id",
        "baseline_manifest_sha256",
        "baseline_wal_lsn",
        "wal_chain_start_lsn",
        "archive_manifest_sha256",
        "database_system_identifier",
        "timeline_id",
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
_LSN_RE = re.compile(
    r"^(?:0|[1-9A-F][0-9A-F]{0,7})/(?:0|[1-9A-F][0-9A-F]{0,7})$",
    re.ASCII,
)
_SYSTEM_IDENTIFIER_RE = re.compile(r"^[1-9][0-9]{0,19}$", re.ASCII)
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$", re.ASCII)
_BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{2,62}$", re.ASCII)
_REGION_RE = re.compile(r"^[a-z][a-z0-9-]{1,62}$", re.ASCII)
_WAL_SEGMENT_NAME_RE = re.compile(r"^[0-9A-F]{24}$", re.ASCII)
_URL_OR_SECRET_RE = re.compile(
    r"(?i)(?:[a-z][a-z0-9+.-]*://|www\.|bearer\s+|access[_ -]?key|"
    r"authorization|credential|password|private[_ -]?key|secret|token)"
)


class PhysicalWaFiPostgresArchiveCommandError(RuntimeError):
    """A fixed-code, redacted archive-command failure."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class PhysicalWaFiPostgresArchiveCommandResult:
    """Redacted successful result safe for an archive-command status channel."""

    wal_segment_name: str
    snapshot_sha256: str
    handoff_descriptor_sha256: str
    upload_manifest_sha256: str
    object_version_id: str


@dataclass(frozen=True)
class _RuntimeFacts:
    configuration_sha256: str
    archive_config: PhysicalWalArchiveSpoolConfig
    verified_binding: VerifiedPhysicalWalArchiveBinding
    uploader_config: PhysicalWalObjectStorageUploaderConfig
    source_root: Path


@dataclass(frozen=True)
class _CliFacts:
    wal_segment_name: str
    wal_path_text: str


def _fail(code: str) -> None:
    raise PhysicalWaFiPostgresArchiveCommandError(code)


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("ARCHIVE_RUNTIME_CONFIG_JSON_INVALID")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> None:
    _fail("ARCHIVE_RUNTIME_CONFIG_JSON_INVALID")


def _utc(value: object, *, code: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        _fail(code)
    return value.astimezone(timezone.utc)


def _exact_mapping(value: object, *, fields: frozenset[str], code: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != fields:
        _fail(code)
    return dict(value)


def _safe_text(value: object, *, pattern: re.Pattern[str], code: str) -> str:
    if type(value) is not str or pattern.fullmatch(value) is None:
        _fail(code)
    if value != value.strip() or "\x00" in value or _URL_OR_SECRET_RE.search(value) is not None:
        _fail(code)
    try:
        value.encode("ascii", "strict")
    except UnicodeEncodeError:
        _fail(code)
    return value


def _sha256(value: object, *, code: str) -> str:
    digest = _safe_text(value, pattern=SHA256_RE, code=code)
    if digest == "0" * 64:
        _fail(code)
    return digest


def _positive_int(value: object, *, maximum: int, code: str) -> int:
    if type(value) is not int or not 1 <= value <= maximum:
        _fail(code)
    return value


def _lsn(value: object, *, code: str) -> str:
    return _safe_text(value, pattern=_LSN_RE, code=code)


def _safe_absolute_path_text(value: object, *, code: str) -> Path:
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
    path = FIXED_WA_FI_POSTGRES_ARCHIVE_COMMAND_CONFIG
    if (
        not isinstance(path, Path)
        or not path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts[1:])
    ):
        _fail("ARCHIVE_FIXED_CONFIG_PATH_INVALID")
    return path


def _validate_config_ancestors(path: Path) -> None:
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        _fail("ARCHIVE_RUNTIME_CONFIG_UNSAFE")
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
                _fail("ARCHIVE_RUNTIME_CONFIG_UNSAFE")
    except PhysicalWaFiPostgresArchiveCommandError:
        raise
    except OSError:
        _fail("ARCHIVE_RUNTIME_CONFIG_UNSAFE")
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _read_fixed_runtime_config_bytes() -> bytes:
    if os.geteuid() != 0:
        _fail("ARCHIVE_ROOT_RUNTIME_REQUIRED")
    path = _fixed_config_path()
    _validate_config_ancestors(path)
    if not hasattr(os, "O_NOFOLLOW"):
        _fail("ARCHIVE_RUNTIME_CONFIG_UNSAFE")
    try:
        before = os.lstat(path)
        resolved = path.resolve(strict=True)
    except OSError:
        _fail("ARCHIVE_RUNTIME_CONFIG_UNSAFE")
    if (
        resolved != path
        or stat.S_ISLNK(before.st_mode)
        or not stat.S_ISREG(before.st_mode)
        or before.st_uid != 0
        or before.st_nlink != 1
        or stat.S_IMODE(before.st_mode) != 0o600
        or not 2 <= before.st_size <= MAX_WA_FI_POSTGRES_ARCHIVE_COMMAND_CONFIG_BYTES
    ):
        _fail("ARCHIVE_RUNTIME_CONFIG_UNSAFE")
    descriptor = -1
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0))
        opened = os.fstat(descriptor)
        expected = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mode,
            before.st_uid,
            before.st_nlink,
        )
        actual = (
            opened.st_dev,
            opened.st_ino,
            opened.st_size,
            opened.st_mode,
            opened.st_uid,
            opened.st_nlink,
        )
        if actual != expected:
            _fail("ARCHIVE_RUNTIME_CONFIG_UNSAFE")
        chunks: list[bytes] = []
        remaining = opened.st_size
        while remaining:
            chunk = os.read(descriptor, remaining)
            if not chunk:
                _fail("ARCHIVE_RUNTIME_CONFIG_UNSAFE")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            _fail("ARCHIVE_RUNTIME_CONFIG_UNSAFE")
        after = os.fstat(descriptor)
        if (
            after.st_dev != opened.st_dev
            or after.st_ino != opened.st_ino
            or after.st_size != opened.st_size
            or after.st_mode != opened.st_mode
            or after.st_uid != opened.st_uid
            or after.st_nlink != opened.st_nlink
        ):
            _fail("ARCHIVE_RUNTIME_CONFIG_UNSAFE")
        return b"".join(chunks)
    except PhysicalWaFiPostgresArchiveCommandError:
        raise
    except OSError:
        _fail("ARCHIVE_RUNTIME_CONFIG_UNSAFE")
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
    except PhysicalWaFiPostgresArchiveCommandError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        _fail("ARCHIVE_RUNTIME_CONFIG_JSON_INVALID")
    try:
        canonical = canonical_json_bytes(decoded)
    except AppendOnlySyncDeltaBatchError:
        _fail("ARCHIVE_RUNTIME_CONFIG_JSON_INVALID")
    if type(decoded) is not dict or canonical != raw:
        _fail("ARCHIVE_RUNTIME_CONFIG_JSON_INVALID")
    return _exact_mapping(decoded, fields=_CONFIG_FIELDS, code="ARCHIVE_RUNTIME_CONFIG_FIELDS_INVALID")


def _decode_public_key(value: object) -> bytes:
    if type(value) is not str:
        _fail("ARCHIVE_WITNESS_KEY_INVALID")
    try:
        key = base64.b64decode(value.encode("ascii", "strict"), validate=True)
        Ed25519PublicKey.from_public_bytes(key)
    except (UnicodeEncodeError, binascii.Error, ValueError):
        _fail("ARCHIVE_WITNESS_KEY_INVALID")
    if len(key) != 32 or key == b"\x00" * 32:
        _fail("ARCHIVE_WITNESS_KEY_INVALID")
    return key


def _normalise_manifest_binding(value: object) -> PhysicalWalArchiveManifestBinding:
    item = _exact_mapping(value, fields=_MANIFEST_BINDING_FIELDS, code="ARCHIVE_BINDING_INVALID")
    if item["source_site"] != "webapp_fi" or item["destination_site"] != "webapp_ir":
        _fail("ARCHIVE_DIRECTION_FORBIDDEN")
    if type(item["campaign_id"]) is not str or CAMPAIGN_ID_RE.fullmatch(item["campaign_id"]) is None:
        _fail("ARCHIVE_BINDING_INVALID")
    if type(item["release_sha"]) is not str or RELEASE_SHA_RE.fullmatch(item["release_sha"]) is None:
        _fail("ARCHIVE_BINDING_INVALID")
    if type(item["stream_generation_id"]) is not str or STREAM_GENERATION_ID_RE.fullmatch(item["stream_generation_id"]) is None:
        _fail("ARCHIVE_BINDING_INVALID")
    if type(item["baseline_generation_id"]) is not str or STREAM_GENERATION_ID_RE.fullmatch(item["baseline_generation_id"]) is None:
        _fail("ARCHIVE_BINDING_INVALID")
    if type(item["database_system_identifier"]) is not str or _SYSTEM_IDENTIFIER_RE.fullmatch(item["database_system_identifier"]) is None:
        _fail("ARCHIVE_BINDING_INVALID")
    if type(item["timeline_id"]) is not int or not 1 <= item["timeline_id"] <= 0xFFFFFFFF:
        _fail("ARCHIVE_BINDING_INVALID")
    recipient = _safe_text(item["destination_age_recipient"], pattern=AGE_RECIPIENT_RE, code="ARCHIVE_BINDING_INVALID")
    return PhysicalWalArchiveManifestBinding(
        source_site="webapp_fi",
        destination_site="webapp_ir",
        campaign_id=item["campaign_id"],
        release_sha=item["release_sha"],
        stream_generation_id=item["stream_generation_id"],
        baseline_generation_id=item["baseline_generation_id"],
        baseline_manifest_sha256=_sha256(item["baseline_manifest_sha256"], code="ARCHIVE_BINDING_INVALID"),
        baseline_wal_lsn=_lsn(item["baseline_wal_lsn"], code="ARCHIVE_BINDING_INVALID"),
        wal_chain_start_lsn=_lsn(item["wal_chain_start_lsn"], code="ARCHIVE_BINDING_INVALID"),
        archive_manifest_sha256=_sha256(item["archive_manifest_sha256"], code="ARCHIVE_BINDING_INVALID"),
        database_system_identifier=item["database_system_identifier"],
        timeline_id=item["timeline_id"],
        destination_age_recipient=recipient,
    )


def _secure_directory(
    value: object,
    *,
    exact_mode: int | None,
    code: str,
) -> Path:
    path = _safe_absolute_path_text(value, code=code) if type(value) is str else value
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
        or (exact_mode is not None and stat.S_IMODE(metadata.st_mode) != exact_mode)
        or (exact_mode is None and stat.S_IMODE(metadata.st_mode) & 0o022)
    ):
        _fail(code)
    return resolved


def _normalise_archive_spool(value: object) -> tuple[PhysicalWalArchiveSpoolConfig, Path]:
    item = _exact_mapping(value, fields=_ARCHIVE_SPOOL_FIELDS, code="ARCHIVE_SPOOL_CONFIG_INVALID")
    source_root = _secure_directory(item["wal_source_root"], exact_mode=None, code="ARCHIVE_SOURCE_ROOT_UNSAFE")
    spool_root = _secure_directory(item["spool_root"], exact_mode=0o700, code="ARCHIVE_SPOOL_ROOT_UNSAFE")
    try:
        source_root.relative_to(spool_root)
        overlaps = True
    except ValueError:
        try:
            spool_root.relative_to(source_root)
            overlaps = True
        except ValueError:
            overlaps = False
    if overlaps:
        _fail("ARCHIVE_SPOOL_CONFIG_INVALID")
    segment_size = item["wal_segment_size_bytes"]
    if type(segment_size) is not int or segment_size not in PHYSICAL_WAL_SUPPORTED_SEGMENT_SIZES_BYTES:
        _fail("ARCHIVE_SPOOL_CONFIG_INVALID")
    return (
        PhysicalWalArchiveSpoolConfig(
            wal_source_root=source_root,
            spool_root=spool_root,
            wal_segment_size_bytes=segment_size,
        ),
        source_root,
    )


def _normalise_uploader(
    value: object,
    *,
    binding: PhysicalWalArchiveManifestBinding,
    archive_config: PhysicalWalArchiveSpoolConfig,
) -> PhysicalWalObjectStorageUploaderConfig:
    item = _exact_mapping(value, fields=_UPLOADER_FIELDS, code="ARCHIVE_UPLOADER_CONFIG_INVALID")
    if (
        item["source_site"] != "webapp_fi"
        or item["destination_site"] != "webapp_ir"
        or item["source_site"] != binding.source_site
        or item["destination_site"] != binding.destination_site
        or item["enabled"] is not True
        or item["direct_site_control"] != "forbidden"
        or item["destination_object_ingest"] != "pull-only"
        or item["destination_age_recipient"] != binding.destination_age_recipient
        or type(item["spool_owner_uid"]) is not int
        or item["spool_owner_uid"] != 0
        or type(item["maximum_plaintext_bytes"]) is not int
        or item["maximum_plaintext_bytes"] != archive_config.wal_segment_size_bytes
    ):
        _fail("ARCHIVE_UPLOADER_CONFIG_INVALID")
    workspace = _secure_directory(item["workspace"], exact_mode=0o700, code="ARCHIVE_UPLOADER_CONFIG_INVALID")
    spool_root = _secure_directory(item["spool_root"], exact_mode=0o700, code="ARCHIVE_UPLOADER_CONFIG_INVALID")
    if spool_root != archive_config.spool_root or workspace == spool_root:
        _fail("ARCHIVE_UPLOADER_CONFIG_INVALID")
    bucket = _safe_text(item["bucket"], pattern=_BUCKET_RE, code="ARCHIVE_UPLOADER_CONFIG_INVALID")
    region = _safe_text(item["region"], pattern=_REGION_RE, code="ARCHIVE_UPLOADER_CONFIG_INVALID")
    recipient = _safe_text(item["destination_age_recipient"], pattern=AGE_RECIPIENT_RE, code="ARCHIVE_UPLOADER_CONFIG_INVALID")
    return PhysicalWalObjectStorageUploaderConfig(
        source_site="webapp_fi",
        destination_site="webapp_ir",
        workspace=workspace,
        spool_root=spool_root,
        spool_owner_uid=0,
        bucket=bucket,
        region=region,
        destination_age_recipient=recipient,
        object_storage_namespace=PHYSICAL_WAL_NORMAL_OBJECT_STORAGE_NAMESPACE,
        enabled=True,
        maximum_plaintext_bytes=archive_config.wal_segment_size_bytes,
        direct_site_control="forbidden",
        destination_object_ingest="pull-only",
    )


def _normalise_runtime(now: datetime) -> _RuntimeFacts:
    item = _parse_runtime_config()
    if (
        item["schema"] != PHYSICAL_WA_FI_POSTGRES_ARCHIVE_COMMAND_RUNTIME_SCHEMA
        or item["version"] != _RUNTIME_VERSION
    ):
        _fail("ARCHIVE_RUNTIME_CONFIG_SCHEMA_INVALID")
    if item["enabled"] is not True:
        _fail("ARCHIVE_RUNTIME_DISABLED")
    provided_configuration_sha256 = _sha256(
        item["configuration_sha256"], code="ARCHIVE_RUNTIME_CONFIG_PIN_INVALID"
    )
    unpinned = dict(item)
    del unpinned["configuration_sha256"]
    try:
        actual_configuration_sha256 = hashlib.sha256(canonical_json_bytes(unpinned)).hexdigest()
    except AppendOnlySyncDeltaBatchError:
        _fail("ARCHIVE_RUNTIME_CONFIG_PIN_INVALID")
    if actual_configuration_sha256 != provided_configuration_sha256:
        _fail("ARCHIVE_RUNTIME_CONFIG_PIN_INVALID")
    if item["source_site"] != "webapp_fi" or item["destination_site"] != "webapp_ir":
        _fail("ARCHIVE_DIRECTION_FORBIDDEN")
    archive_config, source_root = _normalise_archive_spool(item["archive_spool"])
    manifest_binding = _normalise_manifest_binding(item["manifest_binding"])
    if (
        manifest_binding.source_site != item["source_site"]
        or manifest_binding.destination_site != item["destination_site"]
    ):
        _fail("ARCHIVE_BINDING_INVALID")
    witness = _exact_mapping(item["witness_term"], fields=_WITNESS_TERM_FIELDS, code="ARCHIVE_WITNESS_TERM_INVALID")
    witness_key = _decode_public_key(witness["public_key_base64"])
    maximum_duration = _positive_int(
        witness["maximum_lease_duration_seconds"], maximum=300, code="ARCHIVE_WITNESS_TERM_INVALID"
    )
    safety_margin = _positive_int(
        witness["safety_margin_seconds"], maximum=60, code="ARCHIVE_WITNESS_TERM_INVALID"
    )
    if safety_margin >= maximum_duration or type(witness["proof"]) is not dict:
        _fail("ARCHIVE_WITNESS_TERM_INVALID")
    try:
        term = verify_object_delta_role_matrix_witnessed_term(
            witness["proof"],
            witness_public_key=witness_key,
            maximum_lease_duration_seconds=maximum_duration,
            safety_margin_seconds=safety_margin,
            now=now,
        )
        verified_binding = authorize_physical_wal_archive_binding(
            manifest_binding=manifest_binding,
            witnessed_term=term,
            now=now,
        )
    except (ObjectDeltaRoleMatrixRolloverError, PhysicalWalArchiveSpoolError):
        _fail("ARCHIVE_WITNESS_OR_BINDING_INVALID")
    route_pin = _sha256(item["route_binding_sha256"], code="ARCHIVE_BINDING_PIN_INVALID")
    if verified_binding.route_binding_sha256 != route_pin:
        _fail("ARCHIVE_BINDING_PIN_INVALID")
    uploader_config = _normalise_uploader(
        item["object_storage_uploader"],
        binding=manifest_binding,
        archive_config=archive_config,
    )
    return _RuntimeFacts(
        configuration_sha256=provided_configuration_sha256,
        archive_config=archive_config,
        verified_binding=verified_binding,
        uploader_config=uploader_config,
        source_root=source_root,
    )


def _parse_exact_cli(arguments: object) -> _CliFacts:
    if not isinstance(arguments, (list, tuple)) or len(arguments) != 6:
        _fail("ARCHIVE_CLI_SHAPE_INVALID")
    if any(type(item) is not str for item in arguments):
        _fail("ARCHIVE_CLI_SHAPE_INVALID")
    config_path = _fixed_config_path()
    if (
        arguments[0] != "--config"
        or arguments[1] != str(config_path)
        or arguments[2] != "--wal-file"
        or arguments[4] != "--wal-path"
    ):
        _fail("ARCHIVE_CLI_SHAPE_INVALID")
    if _WAL_SEGMENT_NAME_RE.fullmatch(arguments[3]) is None:
        _fail("ARCHIVE_WAL_SEGMENT_INVALID")
    if type(arguments[5]) is not str or not arguments[5] or "\x00" in arguments[5]:
        _fail("ARCHIVE_WAL_PATH_INVALID")
    return _CliFacts(wal_segment_name=arguments[3], wal_path_text=arguments[5])


def _validate_exact_wal_path(
    cli: _CliFacts,
    *,
    runtime: _RuntimeFacts,
) -> None:
    try:
        parse_postgresql_wal_segment_name(
            cli.wal_segment_name,
            wal_segment_size_bytes=runtime.archive_config.wal_segment_size_bytes,
        )
    except PhysicalWalArchiveSpoolError:
        _fail("ARCHIVE_WAL_SEGMENT_INVALID")
    expected = runtime.source_root / cli.wal_segment_name
    if cli.wal_path_text != str(expected):
        _fail("ARCHIVE_WAL_PATH_INVALID")
    candidate = Path(cli.wal_path_text)
    try:
        metadata = os.lstat(candidate)
        resolved = candidate.resolve(strict=True)
    except OSError:
        _fail("ARCHIVE_WAL_PATH_INVALID")
    if (
        resolved != expected
        or stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or metadata.st_size != runtime.archive_config.wal_segment_size_bytes
    ):
        _fail("ARCHIVE_WAL_PATH_INVALID")


def execute_wa_fi_postgres_archive_command(
    arguments: object,
    *,
    now: datetime,
    term_recheck_clock: Callable[[], datetime] | None,
    age_encryptor_factory: Callable[[], PhysicalWalAgeEncryptor] | None,
    object_storage_client_factory: Callable[[], PhysicalWalObjectStorageClient] | None,
) -> PhysicalWaFiPostgresArchiveCommandResult:
    """Execute one exact WA-FI WAL archive handoff through injected factories.

    This is the only effectful function in the module, and it has no default
    dependencies. It rejects the CLI, fixed config, term, binding, and exact
    local WAL path before it constructs the uploader or allows either factory
    to run. The existing spool rechecks the same live Witness term after the
    injected uploader returns and before it records completion.
    """

    cli = _parse_exact_cli(arguments)
    observed_now = _utc(now, code="ARCHIVE_CLOCK_INVALID")
    runtime = _normalise_runtime(observed_now)
    _validate_exact_wal_path(cli, runtime=runtime)
    if (
        term_recheck_clock is None
        or not callable(term_recheck_clock)
        or age_encryptor_factory is None
        or not callable(age_encryptor_factory)
        or object_storage_client_factory is None
        or not callable(object_storage_client_factory)
    ):
        _fail("ARCHIVE_DEPENDENCIES_INVALID")
    try:
        uploader = PhysicalWalObjectStorageUploader(
            config=runtime.uploader_config,
            age_encryptor_factory=age_encryptor_factory,
            client_factory=object_storage_client_factory,
        )
    except Exception:
        _fail("ARCHIVE_UPLOADER_CONSTRUCTION_FAILED")
    try:
        archived: PhysicalWalArchiveSpoolResult = archive_physical_wal_segment(
            segment_name=cli.wal_segment_name,
            config=runtime.archive_config,
            verified_binding=runtime.verified_binding,
            uploader=uploader,
            now=observed_now,
            term_recheck_clock=term_recheck_clock,
        )
    except (PhysicalWalArchiveSpoolError, PhysicalWalObjectStorageUploaderError):
        _fail("ARCHIVE_HANDOFF_FAILED")
    return PhysicalWaFiPostgresArchiveCommandResult(
        wal_segment_name=cli.wal_segment_name,
        snapshot_sha256=archived.snapshot_sha256,
        handoff_descriptor_sha256=archived.handoff_descriptor_sha256,
        upload_manifest_sha256=archived.upload_manifest_sha256,
        object_version_id=archived.object_version_id,
    )


def render_wa_fi_postgres_archive_command_result(
    value: object,
) -> bytes:
    """Return a redacted canonical success report without paths or credentials."""

    if type(value) is not PhysicalWaFiPostgresArchiveCommandResult:
        _fail("ARCHIVE_RESULT_INVALID")
    try:
        parse_postgresql_wal_segment_name(value.wal_segment_name)
    except PhysicalWalArchiveSpoolError:
        _fail("ARCHIVE_RESULT_INVALID")
    payload = {
        "schema": PHYSICAL_WA_FI_POSTGRES_ARCHIVE_COMMAND_REPORT_SCHEMA,
        "status": "archived",
        "wal_segment_name": value.wal_segment_name,
        "snapshot_sha256": _sha256(value.snapshot_sha256, code="ARCHIVE_RESULT_INVALID"),
        "handoff_descriptor_sha256": _sha256(value.handoff_descriptor_sha256, code="ARCHIVE_RESULT_INVALID"),
        "upload_manifest_sha256": _sha256(value.upload_manifest_sha256, code="ARCHIVE_RESULT_INVALID"),
        "object_version_id": _safe_text(value.object_version_id, pattern=_SAFE_ID_RE, code="ARCHIVE_RESULT_INVALID"),
    }
    return canonical_json_bytes(payload)


def render_wa_fi_postgres_archive_command_error(error: object) -> bytes:
    """Return only a fixed failure code; never echo a path, config, or secret."""

    if not isinstance(error, PhysicalWaFiPostgresArchiveCommandError):
        _fail("ARCHIVE_ERROR_INVALID")
    code = _safe_text(error.code, pattern=_SAFE_ID_RE, code="ARCHIVE_ERROR_INVALID")
    return canonical_json_bytes(
        {
            "schema": PHYSICAL_WA_FI_POSTGRES_ARCHIVE_COMMAND_REPORT_SCHEMA,
            "status": "blocked",
            "error": code,
        }
    )
