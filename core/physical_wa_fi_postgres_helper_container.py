"""Fail-closed PostgreSQL 15 helper-container contract for WA-FI capture.

This module is an installed-adapter seam, not a Docker wrapper.  It never
imports ``subprocess``, Docker, PostgreSQL, a network client, SSH, or an
Object-Storage SDK.  A root-controlled runtime may inject one runner which
executes the immutable Docker argv produced here.  Importing it, building an
invocation, and running its tests are local-only operations.

The helper has a deliberately small data-plane: a read-only shared Unix socket
volume for the local WA-FI PostgreSQL server and one fresh root-owned capture
directory for the resulting tar.  It has no host PostgreSQL binary, no TCP
listener, no container network, no FI-to-IR address, no credential field, and
no caller-selected image, command, volume, path, user, or environment.

The helper executes as the image-attested non-root PostgreSQL UID/GID.  It
writes only to a fresh dedicated ``0700`` child owned by that identity.  After
the injected runner exits, root atomically collects the sole expected tar into
the caller's root-owned capture directory and normalizes it to ``root:root
0600``.  The command has a read-only root filesystem, no network, no
capabilities, no-new-privileges, a read-only socket mount, and exactly one
writable helper-output bind.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
from typing import Any, Protocol

from core.append_only_sync_delta_batch import canonical_json_bytes
from core.physical_postgres_deployment_scaffold import (
    PHYSICAL_POSTGRES_DEFAULT_OFF_STATUS,
    PHYSICAL_POSTGRES_LOCAL_BASE_BACKUP_AUTH_PREFLIGHT_SCHEMA,
)


__all__ = (
    "FIXED_WA_FI_POSTGRES_HELPER_CONTAINER_ATTESTATION",
    "FIXED_WA_FI_POSTGRES_HELPER_CONTAINER_CONFIG",
    "FIXED_WA_FI_POSTGRES_HELPER_DOCKER_BINARY",
    "FIXED_WA_FI_POSTGRES_LOCAL_BASE_BACKUP_AUTH_PREFLIGHT",
    "FIXED_WA_FI_POSTGRES_MANIFEST_LOCK",
    "FIXED_WA_FI_POSTGRES_RUNTIME_IDENTITY_ATTESTATION",
    "PHYSICAL_WA_FI_POSTGRES_HELPER_CONTAINER_DEFAULT_ENABLED",
    "PHYSICAL_WA_FI_POSTGRES_HELPER_CONTAINER_SCHEMA",
    "PhysicalWaFiPostgresHelperContainerCaptureRequest",
    "PhysicalWaFiPostgresHelperContainerError",
    "PhysicalWaFiPostgresHelperContainerInvocation",
    "PhysicalWaFiPostgresHelperContainerResult",
    "PhysicalWaFiPostgresHelperContainerRunner",
    "PhysicalWaFiPostgresHelperContainerRunnerResult",
    "build_wa_fi_postgres_helper_container_invocation",
    "execute_wa_fi_postgres_helper_container_capture",
)


PHYSICAL_WA_FI_POSTGRES_HELPER_CONTAINER_SCHEMA = (
    "gold-trade-physical-wa-fi-postgres-helper-container-v1"
)
PHYSICAL_WA_FI_POSTGRES_HELPER_CONTAINER_ATTESTATION_SCHEMA = (
    "gold-trade-physical-wa-fi-postgres-helper-container-installation-attestation-v1"
)
PHYSICAL_WA_FI_POSTGRES_HELPER_CONTAINER_DEFAULT_ENABLED = False

FIXED_WA_FI_POSTGRES_HELPER_CONTAINER_CONFIG = Path(
    "/etc/trading-bot/physical-postgres/primary/base-backup-helper-container.json"
)
FIXED_WA_FI_POSTGRES_HELPER_CONTAINER_ATTESTATION = Path(
    "/etc/trading-bot/physical-postgres/primary/"
    "base-backup-helper-container-installation-attestation.json"
)
FIXED_WA_FI_POSTGRES_HELPER_DOCKER_BINARY = Path("/usr/bin/docker")
FIXED_WA_FI_POSTGRES_MANIFEST_LOCK = Path(
    "/etc/trading-bot/physical-postgres/rendered/manifest-lock.json"
)
FIXED_WA_FI_POSTGRES_LOCAL_BASE_BACKUP_AUTH_PREFLIGHT = Path(
    "/etc/trading-bot/physical-postgres/rendered/primary/"
    "local-base-backup-auth-preflight.json"
)
FIXED_WA_FI_POSTGRES_RUNTIME_IDENTITY_ATTESTATION = Path(
    "/etc/trading-bot/physical-postgres/primary/"
    "postgres-image-runtime-identity-attestation.json"
)

_RUNTIME_VERSION = 1
_ATTESTATION_VERSION = 1
_MAX_CONFIG_BYTES = 192 * 1024
_MAX_ATTESTATION_BYTES = 192 * 1024
_MAX_DOCKER_BINARY_BYTES = 128 * 1024 * 1024
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
        "capture_configuration_sha256",
        "deployment_manifest_lock_sha256",
        "local_base_backup_auth_preflight_sha256",
        "postgres_runtime_identity_attestation_sha256",
        "helper",
    }
)
_ATTESTATION_FIELDS = frozenset(
    {
        "schema",
        "version",
        "configuration_sha256",
        "source_site",
        "destination_site",
        "direct_site_control",
        "destination_object_ingest",
        "capture_configuration_sha256",
        "deployment_manifest_lock_sha256",
        "local_base_backup_auth_preflight_sha256",
        "postgres_runtime_identity_attestation_sha256",
        "docker_binary",
        "helper",
    }
)
_LOCAL_BASE_BACKUP_PREFLIGHT_FIELDS = frozenset(
    {
        "schema",
        "status",
        "campaign_id",
        "release_sha",
        "source_site",
        "destination_site",
        "direct_fi_to_ir_postgres_control",
        "postgres_major",
        "postgres_runtime_identity",
        "postgres_socket_volume",
        "local_base_backup",
        "pg_hba_sha256",
        "pg_ident_sha256",
        "postgresql_conf_sha256",
        "required_role_attributes",
        "not_a_role_creation_authorization",
        "not_a_launch_authorization",
    }
)
_LOCAL_BASE_BACKUP_POLICY_FIELDS = frozenset(
    {
        "transport",
        "socket_directory",
        "port",
        "replication_role",
        "peer_os_users",
        "max_wal_senders",
        "tcp_hba",
        "helper_execution",
    }
)
_RUNTIME_IDENTITY_ATTESTATION_FIELDS = frozenset(
    {
        "schema",
        "version",
        "postgres_image",
        "image_digest",
        "platform",
        "effective_uid",
        "effective_gid",
        "pg_basebackup_entrypoint",
    }
)
_ROLE_PREFLIGHT_FIELDS = frozenset(
    {
        "role",
        "login",
        "replication",
        "superuser",
        "createdb",
        "createrole",
        "bypassrls",
        "inherit",
        "password_authentication",
    }
)
_HELPER_FIELDS = frozenset(
    {
        "postgres_major",
        "image",
        "docker_binary_sha256",
        "network_mode",
        "pull_policy",
        "container_root_filesystem_read_only",
        "drop_all_capabilities",
        "no_new_privileges",
        "pids_limit",
        "socket_volume",
        "socket_mount_target",
        "socket_mount_read_only",
        "socket_directory_owner",
        "socket_directory_mode",
        "socket_file_name",
        "socket_file_owner",
        "socket_file_group",
        "socket_file_mode",
        "output_mount_target",
        "output_directory_mode",
        "entrypoint",
        "source_port",
        "source_role",
        "password_prompt",
    }
)

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_IMAGE_RE = re.compile(
    r"^[a-z0-9][a-z0-9._/-]{1,255}@sha256:[0-9a-f]{64}$", re.ASCII
)
_VOLUME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{2,127}$", re.ASCII)
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$", re.ASCII)
_SAFE_PATH_COMPONENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$", re.ASCII)
_URL_OR_SECRET_RE = re.compile(
    r"(?i)(?:[a-z][a-z0-9+.-]*://|www\.|bearer\s+|access[_ -]?key|"
    r"authorization|credential|password|private[_ -]?key|secret|token)"
)

_FIXED_NETWORK_MODE = "none"
_FIXED_PULL_POLICY = "never"
_FIXED_SOCKET_MOUNT_TARGET = "/var/run/postgresql"
_FIXED_SOCKET_DIRECTORY_OWNER = "postgres"
_FIXED_SOCKET_DIRECTORY_MODE = "0710"
_FIXED_SOCKET_FILE_NAME = ".s.PGSQL.5432"
_FIXED_SOCKET_FILE_OWNER = "postgres"
_FIXED_SOCKET_FILE_GROUP = "postgres"
_FIXED_SOCKET_FILE_MODE = "0770"
_FIXED_OUTPUT_MOUNT_TARGET = "/capture"
_FIXED_ENTRYPOINT = "pg_basebackup"
_FIXED_SOURCE_ROLE = "physical_backup"
_HELPER_OUTPUT_PREFIX = "pg-basebackup-helper-"
_COLLECTED_ARTIFACT_NAME = "base.tar"
_FIXED_HELPER_EXECUTION = "digest-pinned-image-attested-container-v1"


class PhysicalWaFiPostgresHelperContainerError(RuntimeError):
    """One fixed, redacted local helper-container policy failure."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class PhysicalWaFiPostgresHelperContainerCaptureRequest:
    """Non-secret capture facts supplied only by the future capture bridge.

    There is intentionally no Docker image, command, host, port, socket path,
    peer, URL, credential, user, or environment field.  ``capture_output_root``
    must be the fresh root-owned ``0700`` directory selected by that bridge.
    """

    capture_configuration_sha256: str
    capture_output_root: Path
    writer_epoch: int
    writer_lease_id: str
    witness_transition_id: str
    witnessed_term_proof_sha256: str


@dataclass(frozen=True)
class PhysicalWaFiPostgresHelperContainerInvocation:
    """Exact non-secret argv offered to one injected root-controlled runner."""

    docker_binary: Path
    docker_binary_sha256: str
    helper_image: str
    arguments: tuple[str, ...]
    environment: tuple[tuple[str, str], ...]
    capture_output_root: Path
    helper_output_directory: Path
    helper_uid: int
    helper_gid: int
    configuration_sha256: str
    installation_attestation_sha256: str
    capture_configuration_sha256: str
    deployment_manifest_lock_sha256: str
    local_base_backup_auth_preflight_sha256: str
    postgres_runtime_identity_attestation_sha256: str
    writer_epoch: int
    writer_lease_id: str
    witness_transition_id: str
    witnessed_term_proof_sha256: str
    invocation_sha256: str


@dataclass(frozen=True)
class PhysicalWaFiPostgresHelperContainerRunnerResult:
    """The injected runner exposes only a redaction-safe exit status."""

    exit_code: int


class PhysicalWaFiPostgresHelperContainerRunner(Protocol):
    def run(
        self,
        *,
        invocation: PhysicalWaFiPostgresHelperContainerInvocation,
    ) -> PhysicalWaFiPostgresHelperContainerRunnerResult: ...


@dataclass(frozen=True)
class PhysicalWaFiPostgresHelperContainerResult:
    """A successful Docker-run status, not an artifact or promotion proof."""

    configuration_sha256: str
    installation_attestation_sha256: str
    capture_configuration_sha256: str
    deployment_manifest_lock_sha256: str
    local_base_backup_auth_preflight_sha256: str
    invocation_sha256: str
    collected_artifact_path: Path
    collected_artifact_sha256: str
    collected_artifact_bytes: int


@dataclass(frozen=True)
class _HelperFacts:
    postgres_major: int
    image: str
    docker_binary_sha256: str
    socket_volume: str


@dataclass(frozen=True)
class _RuntimeFacts:
    configuration_sha256: str
    installation_attestation_sha256: str
    capture_configuration_sha256: str
    deployment_manifest_lock_sha256: str
    local_base_backup_auth_preflight_sha256: str
    postgres_runtime_identity_attestation_sha256: str
    postgres_runtime_identity: "_RuntimeIdentity"
    helper: _HelperFacts


@dataclass(frozen=True)
class _RequestFacts:
    capture_configuration_sha256: str
    capture_output_root: Path
    writer_epoch: int
    writer_lease_id: str
    witness_transition_id: str
    witnessed_term_proof_sha256: str


@dataclass(frozen=True)
class _RuntimeIdentity:
    effective_uid: int
    effective_gid: int


def _fail(code: str) -> None:
    raise PhysicalWaFiPostgresHelperContainerError(code)


def _canonical(value: object, *, code: str) -> bytes:
    try:
        return canonical_json_bytes(value)
    except (TypeError, ValueError):
        _fail(code)


def _strict_object(pairs: list[tuple[str, Any]], *, code: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail(code)
        result[key] = value
    return result


def _reject_json_constant(_: str) -> None:
    _fail("HELPER_CONTAINER_JSON_INVALID")


def _exact_mapping(value: object, *, fields: frozenset[str], code: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != fields:
        _fail(code)
    return dict(value)


def _safe_text(value: object, *, pattern: re.Pattern[str], code: str) -> str:
    if type(value) is not str or not value or value != value.strip() or "\x00" in value:
        _fail(code)
    if pattern.fullmatch(value) is None or _URL_OR_SECRET_RE.search(value) is not None:
        _fail(code)
    try:
        value.encode("ascii", "strict")
    except UnicodeEncodeError:
        _fail(code)
    return value


def _sha256(value: object, *, code: str) -> str:
    result = _safe_text(value, pattern=_SHA256_RE, code=code)
    if result == "0" * 64:
        _fail(code)
    return result


def _positive_int(value: object, *, maximum: int, code: str) -> int:
    if type(value) is not int or not 1 <= value <= maximum:
        _fail(code)
    return value


def _fixed_path(value: object, *, code: str) -> Path:
    if (
        not isinstance(value, Path)
        or not value.is_absolute()
        or any(part in {"", ".", ".."} for part in value.parts[1:])
    ):
        _fail(code)
    return value


def _validate_ancestors(path: Path, *, code: str) -> None:
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        _fail(code)
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
                _fail(code)
    except PhysicalWaFiPostgresHelperContainerError:
        raise
    except OSError:
        _fail(code)
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _read_private_file(path: Path, *, maximum_bytes: int, code: str) -> bytes:
    _validate_ancestors(path, code=code)
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
        fingerprint = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mode,
            before.st_uid,
            before.st_nlink,
        )
        if (
            opened.st_dev,
            opened.st_ino,
            opened.st_size,
            opened.st_mode,
            opened.st_uid,
            opened.st_nlink,
        ) != fingerprint:
            _fail(code)
        chunks: list[bytes] = []
        remaining = opened.st_size
        while remaining:
            chunk = os.read(descriptor, remaining)
            if not chunk:
                _fail(code)
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            _fail(code)
        after = os.fstat(descriptor)
        if (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mode,
            after.st_uid,
            after.st_nlink,
        ) != fingerprint:
            _fail(code)
        return b"".join(chunks)
    except PhysicalWaFiPostgresHelperContainerError:
        raise
    except OSError:
        _fail(code)
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _read_canonical_object(
    path: Path,
    *,
    maximum_bytes: int,
    unsafe_code: str,
    json_code: str,
    fields: frozenset[str],
    fields_code: str,
) -> tuple[dict[str, Any], bytes]:
    raw = _read_private_file(path, maximum_bytes=maximum_bytes, code=unsafe_code)
    try:
        value = json.loads(
            raw.decode("ascii", "strict"),
            object_pairs_hook=lambda pairs: _strict_object(pairs, code=json_code),
            parse_constant=_reject_json_constant,
        )
    except PhysicalWaFiPostgresHelperContainerError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        _fail(json_code)
    if type(value) is not dict or _canonical(value, code=json_code) != raw:
        _fail(json_code)
    return _exact_mapping(value, fields=fields, code=fields_code), raw


def _read_canonical_newline_object(
    path: Path,
    *,
    maximum_bytes: int,
    unsafe_code: str,
    json_code: str,
    fields: frozenset[str] | None = None,
    fields_code: str | None = None,
) -> tuple[dict[str, Any], bytes]:
    raw = _read_private_file(path, maximum_bytes=maximum_bytes, code=unsafe_code)
    try:
        value = json.loads(
            raw[:-1].decode("ascii", "strict") if raw.endswith(b"\n") else "",
            object_pairs_hook=lambda pairs: _strict_object(pairs, code=json_code),
            parse_constant=_reject_json_constant,
        )
    except PhysicalWaFiPostgresHelperContainerError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        _fail(json_code)
    if (
        type(value) is not dict
        or _canonical(value, code=json_code) + b"\n" != raw
    ):
        _fail(json_code)
    if fields is not None:
        if fields_code is None:
            _fail(json_code)
        return _exact_mapping(value, fields=fields, code=fields_code), raw
    return dict(value), raw


def _read_runtime_identity(
    *,
    expected_sha256: str,
    expected_image: str,
) -> _RuntimeIdentity:
    path = _fixed_path(
        FIXED_WA_FI_POSTGRES_RUNTIME_IDENTITY_ATTESTATION,
        code="HELPER_CONTAINER_FIXED_RUNTIME_IDENTITY_PATH_INVALID",
    )
    identity, raw = _read_canonical_object(
        path,
        maximum_bytes=_MAX_ATTESTATION_BYTES,
        unsafe_code="HELPER_CONTAINER_RUNTIME_IDENTITY_UNSAFE",
        json_code="HELPER_CONTAINER_RUNTIME_IDENTITY_JSON_INVALID",
        fields=_RUNTIME_IDENTITY_ATTESTATION_FIELDS,
        fields_code="HELPER_CONTAINER_RUNTIME_IDENTITY_FIELDS_INVALID",
    )
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        _fail("HELPER_CONTAINER_RUNTIME_IDENTITY_BINDING_MISMATCH")
    if (
        identity["schema"]
        != "gold-trade-physical-postgres-runtime-identity-attestation-v1"
        or identity["version"] != 1
        or identity["postgres_image"] != expected_image
        or identity["image_digest"] not in expected_image
        or identity["platform"] != "linux/amd64"
        or identity["pg_basebackup_entrypoint"] != _FIXED_ENTRYPOINT
    ):
        _fail("HELPER_CONTAINER_RUNTIME_IDENTITY_INVALID")
    return _RuntimeIdentity(
        effective_uid=_positive_int(
            identity["effective_uid"], maximum=2**31 - 1,
            code="HELPER_CONTAINER_RUNTIME_IDENTITY_INVALID",
        ),
        effective_gid=_positive_int(
            identity["effective_gid"], maximum=2**31 - 1,
            code="HELPER_CONTAINER_RUNTIME_IDENTITY_INVALID",
        ),
    )


def _require_rendered_base_backup_binding(
    *,
    deployment_manifest_lock_sha256: str,
    local_base_backup_auth_preflight_sha256: str,
    postgres_runtime_identity_attestation_sha256: str,
    helper: _HelperFacts,
) -> _RuntimeIdentity:
    """Bind the helper to the rendered socket/HBA/role-preflight policy."""

    manifest_path = _fixed_path(
        FIXED_WA_FI_POSTGRES_MANIFEST_LOCK,
        code="HELPER_CONTAINER_FIXED_MANIFEST_LOCK_PATH_INVALID",
    )
    manifest, manifest_raw = _read_canonical_newline_object(
        manifest_path,
        maximum_bytes=_MAX_ATTESTATION_BYTES,
        unsafe_code="HELPER_CONTAINER_MANIFEST_LOCK_UNSAFE",
        json_code="HELPER_CONTAINER_MANIFEST_LOCK_INVALID",
    )
    if hashlib.sha256(manifest_raw).hexdigest() != deployment_manifest_lock_sha256:
        _fail("HELPER_CONTAINER_MANIFEST_LOCK_BINDING_MISMATCH")
    try:
        primary = manifest["primary"]
        route = manifest["route"]
    except (KeyError, TypeError):
        _fail("HELPER_CONTAINER_MANIFEST_LOCK_INVALID")
    identity = _read_runtime_identity(
        expected_sha256=postgres_runtime_identity_attestation_sha256,
        expected_image=helper.image,
    )
    if (
        type(primary) is not dict
        or type(route) is not dict
        or manifest.get("status") != PHYSICAL_POSTGRES_DEFAULT_OFF_STATUS
        or manifest.get("postgres_major") != 15
        or primary.get("postgres_socket_volume") != helper.socket_volume
        or route.get("source_site") != "webapp_fi"
        or route.get("destination_site") != "webapp_ir"
        or route.get("direct_fi_to_ir_postgres_control") is not False
        or manifest.get("postgres_runtime_identity")
        != {
            "image_digest": helper.image.rsplit("@", 1)[1],
            "platform": "linux/amd64",
            "effective_uid": identity.effective_uid,
            "effective_gid": identity.effective_gid,
            "attestation_sha256": postgres_runtime_identity_attestation_sha256,
        }
    ):
        _fail("HELPER_CONTAINER_MANIFEST_LOCK_INVALID")

    preflight_path = _fixed_path(
        FIXED_WA_FI_POSTGRES_LOCAL_BASE_BACKUP_AUTH_PREFLIGHT,
        code="HELPER_CONTAINER_FIXED_AUTH_PREFLIGHT_PATH_INVALID",
    )
    preflight, preflight_raw = _read_canonical_newline_object(
        preflight_path,
        maximum_bytes=_MAX_ATTESTATION_BYTES,
        unsafe_code="HELPER_CONTAINER_AUTH_PREFLIGHT_UNSAFE",
        json_code="HELPER_CONTAINER_AUTH_PREFLIGHT_JSON_INVALID",
        fields=_LOCAL_BASE_BACKUP_PREFLIGHT_FIELDS,
        fields_code="HELPER_CONTAINER_AUTH_PREFLIGHT_FIELDS_INVALID",
    )
    if hashlib.sha256(preflight_raw).hexdigest() != local_base_backup_auth_preflight_sha256:
        _fail("HELPER_CONTAINER_AUTH_PREFLIGHT_BINDING_MISMATCH")
    policy = _exact_mapping(
        preflight["local_base_backup"],
        fields=_LOCAL_BASE_BACKUP_POLICY_FIELDS,
        code="HELPER_CONTAINER_AUTH_PREFLIGHT_INVALID",
    )
    role = _exact_mapping(
        preflight["required_role_attributes"],
        fields=_ROLE_PREFLIGHT_FIELDS,
        code="HELPER_CONTAINER_AUTH_PREFLIGHT_INVALID",
    )
    if (
        preflight["schema"] != PHYSICAL_POSTGRES_LOCAL_BASE_BACKUP_AUTH_PREFLIGHT_SCHEMA
        or preflight["status"] != PHYSICAL_POSTGRES_DEFAULT_OFF_STATUS
        or preflight["campaign_id"] != manifest.get("campaign_id")
        or preflight["release_sha"] != manifest.get("release_sha")
        or preflight["source_site"] != "webapp_fi"
        or preflight["destination_site"] != "webapp_ir"
        or preflight["direct_fi_to_ir_postgres_control"] is not False
        or preflight["postgres_major"] != 15
        or preflight["postgres_socket_volume"] != helper.socket_volume
        or preflight["postgres_runtime_identity"]
        != {
            "image_digest": helper.image.rsplit("@", 1)[1],
            "platform": "linux/amd64",
            "effective_uid": identity.effective_uid,
            "effective_gid": identity.effective_gid,
            "attestation_sha256": postgres_runtime_identity_attestation_sha256,
        }
        or policy
        != {
            "transport": "unix-socket-only",
            "socket_directory": _FIXED_SOCKET_MOUNT_TARGET,
            "port": 5432,
            "replication_role": _FIXED_SOURCE_ROLE,
            "peer_os_users": ["postgres"],
            "max_wal_senders": 1,
            "tcp_hba": "reject",
            "helper_execution": _FIXED_HELPER_EXECUTION,
        }
        or role
        != {
            "role": _FIXED_SOURCE_ROLE,
            "login": True,
            "replication": True,
            "superuser": False,
            "createdb": False,
            "createrole": False,
            "bypassrls": False,
            "inherit": False,
            "password_authentication": "forbidden",
        }
        or preflight["not_a_role_creation_authorization"] is not True
        or preflight["not_a_launch_authorization"] is not True
        or any(
            _sha256(preflight[field], code="HELPER_CONTAINER_AUTH_PREFLIGHT_INVALID")
            == "0" * 64
            for field in ("pg_hba_sha256", "pg_ident_sha256", "postgresql_conf_sha256")
        )
    ):
        _fail("HELPER_CONTAINER_AUTH_PREFLIGHT_INVALID")
    return identity


def _normalise_helper(value: object, *, code: str) -> _HelperFacts:
    item = _exact_mapping(value, fields=_HELPER_FIELDS, code=code)
    if (
        item["postgres_major"] != 15
        or item["network_mode"] != _FIXED_NETWORK_MODE
        or item["pull_policy"] != _FIXED_PULL_POLICY
        or item["container_root_filesystem_read_only"] is not True
        or item["drop_all_capabilities"] is not True
        or item["no_new_privileges"] is not True
        or item["pids_limit"] != 64
        or item["socket_mount_target"] != _FIXED_SOCKET_MOUNT_TARGET
        or item["socket_mount_read_only"] is not True
        or item["socket_directory_owner"] != _FIXED_SOCKET_DIRECTORY_OWNER
        or item["socket_directory_mode"] != _FIXED_SOCKET_DIRECTORY_MODE
        or item["socket_file_name"] != _FIXED_SOCKET_FILE_NAME
        or item["socket_file_owner"] != _FIXED_SOCKET_FILE_OWNER
        or item["socket_file_group"] != _FIXED_SOCKET_FILE_GROUP
        or item["socket_file_mode"] != _FIXED_SOCKET_FILE_MODE
        or item["output_mount_target"] != _FIXED_OUTPUT_MOUNT_TARGET
        or item["output_directory_mode"] != "0700"
        or item["entrypoint"] != _FIXED_ENTRYPOINT
        or item["source_port"] != 5432
        or item["source_role"] != _FIXED_SOURCE_ROLE
        or item["password_prompt"] != "forbidden"
    ):
        _fail(code)
    return _HelperFacts(
        postgres_major=15,
        image=_safe_text(item["image"], pattern=_IMAGE_RE, code=code),
        docker_binary_sha256=_sha256(item["docker_binary_sha256"], code=code),
        socket_volume=_safe_text(item["socket_volume"], pattern=_VOLUME_RE, code=code),
    )


def _load_runtime() -> _RuntimeFacts:
    if os.geteuid() != 0:
        _fail("HELPER_CONTAINER_ROOT_RUNTIME_REQUIRED")
    config_path = _fixed_path(
        FIXED_WA_FI_POSTGRES_HELPER_CONTAINER_CONFIG,
        code="HELPER_CONTAINER_FIXED_CONFIG_PATH_INVALID",
    )
    config, _ = _read_canonical_object(
        config_path,
        maximum_bytes=_MAX_CONFIG_BYTES,
        unsafe_code="HELPER_CONTAINER_RUNTIME_CONFIG_UNSAFE",
        json_code="HELPER_CONTAINER_RUNTIME_CONFIG_JSON_INVALID",
        fields=_CONFIG_FIELDS,
        fields_code="HELPER_CONTAINER_RUNTIME_CONFIG_FIELDS_INVALID",
    )
    if (
        config["schema"] != PHYSICAL_WA_FI_POSTGRES_HELPER_CONTAINER_SCHEMA
        or type(config["version"]) is not int
        or config["version"] != _RUNTIME_VERSION
    ):
        _fail("HELPER_CONTAINER_RUNTIME_CONFIG_SCHEMA_INVALID")
    if config["enabled"] is not True:
        _fail("HELPER_CONTAINER_DISABLED")
    configuration_sha256 = _sha256(
        config["configuration_sha256"], code="HELPER_CONTAINER_CONFIG_PIN_INVALID"
    )
    unpinned = dict(config)
    del unpinned["configuration_sha256"]
    if hashlib.sha256(
        _canonical(unpinned, code="HELPER_CONTAINER_CONFIG_PIN_INVALID")
    ).hexdigest() != configuration_sha256:
        _fail("HELPER_CONTAINER_CONFIG_PIN_INVALID")
    if (
        config["source_site"] != "webapp_fi"
        or config["destination_site"] != "webapp_ir"
        or config["direct_site_control"] != "forbidden"
        or config["destination_object_ingest"] != "pull-only"
    ):
        _fail("HELPER_CONTAINER_DIRECTION_FORBIDDEN")
    capture_configuration_sha256 = _sha256(
        config["capture_configuration_sha256"], code="HELPER_CONTAINER_CAPTURE_BINDING_INVALID"
    )
    deployment_manifest_lock_sha256 = _sha256(
        config["deployment_manifest_lock_sha256"],
        code="HELPER_CONTAINER_MANIFEST_LOCK_BINDING_INVALID",
    )
    local_base_backup_auth_preflight_sha256 = _sha256(
        config["local_base_backup_auth_preflight_sha256"],
        code="HELPER_CONTAINER_AUTH_PREFLIGHT_BINDING_INVALID",
    )
    postgres_runtime_identity_attestation_sha256 = _sha256(
        config["postgres_runtime_identity_attestation_sha256"],
        code="HELPER_CONTAINER_RUNTIME_IDENTITY_BINDING_INVALID",
    )
    helper = _normalise_helper(config["helper"], code="HELPER_CONTAINER_CONFIG_INVALID")
    runtime_identity = _require_rendered_base_backup_binding(
        deployment_manifest_lock_sha256=deployment_manifest_lock_sha256,
        local_base_backup_auth_preflight_sha256=local_base_backup_auth_preflight_sha256,
        postgres_runtime_identity_attestation_sha256=postgres_runtime_identity_attestation_sha256,
        helper=helper,
    )

    attestation_path = _fixed_path(
        FIXED_WA_FI_POSTGRES_HELPER_CONTAINER_ATTESTATION,
        code="HELPER_CONTAINER_FIXED_ATTESTATION_PATH_INVALID",
    )
    attestation, attestation_raw = _read_canonical_object(
        attestation_path,
        maximum_bytes=_MAX_ATTESTATION_BYTES,
        unsafe_code="HELPER_CONTAINER_ATTESTATION_UNSAFE",
        json_code="HELPER_CONTAINER_ATTESTATION_JSON_INVALID",
        fields=_ATTESTATION_FIELDS,
        fields_code="HELPER_CONTAINER_ATTESTATION_FIELDS_INVALID",
    )
    if (
        attestation["schema"]
        != PHYSICAL_WA_FI_POSTGRES_HELPER_CONTAINER_ATTESTATION_SCHEMA
        or type(attestation["version"]) is not int
        or attestation["version"] != _ATTESTATION_VERSION
        or attestation["configuration_sha256"] != configuration_sha256
        or attestation["source_site"] != "webapp_fi"
        or attestation["destination_site"] != "webapp_ir"
        or attestation["direct_site_control"] != "forbidden"
        or attestation["destination_object_ingest"] != "pull-only"
        or attestation["capture_configuration_sha256"] != capture_configuration_sha256
        or attestation["deployment_manifest_lock_sha256"]
        != deployment_manifest_lock_sha256
        or attestation["local_base_backup_auth_preflight_sha256"]
        != local_base_backup_auth_preflight_sha256
        or attestation["postgres_runtime_identity_attestation_sha256"]
        != postgres_runtime_identity_attestation_sha256
        or attestation["docker_binary"]
        != str(
            _fixed_path(
                FIXED_WA_FI_POSTGRES_HELPER_DOCKER_BINARY,
                code="HELPER_CONTAINER_DOCKER_PATH_INVALID",
            )
        )
        or _normalise_helper(
            attestation["helper"], code="HELPER_CONTAINER_ATTESTATION_INVALID"
        )
        != helper
    ):
        _fail("HELPER_CONTAINER_ATTESTATION_INVALID")
    return _RuntimeFacts(
        configuration_sha256=configuration_sha256,
        installation_attestation_sha256=hashlib.sha256(attestation_raw).hexdigest(),
        capture_configuration_sha256=capture_configuration_sha256,
        deployment_manifest_lock_sha256=deployment_manifest_lock_sha256,
        local_base_backup_auth_preflight_sha256=local_base_backup_auth_preflight_sha256,
        postgres_runtime_identity_attestation_sha256=postgres_runtime_identity_attestation_sha256,
        postgres_runtime_identity=runtime_identity,
        helper=helper,
    )


def _secure_docker_binary(expected_sha256: str) -> Path:
    path = _fixed_path(
        FIXED_WA_FI_POSTGRES_HELPER_DOCKER_BINARY,
        code="HELPER_CONTAINER_DOCKER_PATH_INVALID",
    )
    _validate_ancestors(path, code="HELPER_CONTAINER_DOCKER_UNAVAILABLE")
    if not hasattr(os, "O_NOFOLLOW"):
        _fail("HELPER_CONTAINER_DOCKER_UNAVAILABLE")
    try:
        before = os.lstat(path)
        resolved = path.resolve(strict=True)
    except OSError:
        _fail("HELPER_CONTAINER_DOCKER_UNAVAILABLE")
    if (
        resolved != path
        or stat.S_ISLNK(before.st_mode)
        or not stat.S_ISREG(before.st_mode)
        or before.st_uid != 0
        or before.st_nlink != 1
        or stat.S_IMODE(before.st_mode) & 0o022
        or not before.st_mode & stat.S_IXUSR
        or not 1 <= before.st_size <= _MAX_DOCKER_BINARY_BYTES
    ):
        _fail("HELPER_CONTAINER_DOCKER_UNAVAILABLE")
    descriptor = -1
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0))
        opened = os.fstat(descriptor)
        fingerprint = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mode,
            before.st_uid,
            before.st_nlink,
        )
        if (
            opened.st_dev,
            opened.st_ino,
            opened.st_size,
            opened.st_mode,
            opened.st_uid,
            opened.st_nlink,
        ) != fingerprint:
            _fail("HELPER_CONTAINER_DOCKER_UNAVAILABLE")
        digest = hashlib.sha256()
        total = 0
        while True:
            chunk = os.read(descriptor, _COPY_CHUNK_BYTES)
            if not chunk:
                break
            total += len(chunk)
            if total > _MAX_DOCKER_BINARY_BYTES:
                _fail("HELPER_CONTAINER_DOCKER_UNAVAILABLE")
            digest.update(chunk)
        after = os.fstat(descriptor)
        if (
            total != before.st_size
            or (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mode,
                after.st_uid,
                after.st_nlink,
            )
            != fingerprint
        ):
            _fail("HELPER_CONTAINER_DOCKER_UNAVAILABLE")
    except PhysicalWaFiPostgresHelperContainerError:
        raise
    except OSError:
        _fail("HELPER_CONTAINER_DOCKER_UNAVAILABLE")
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if digest.hexdigest() != expected_sha256:
        _fail("HELPER_CONTAINER_DOCKER_IDENTITY_INVALID")
    return resolved


def _secure_empty_capture_output_root(value: object) -> Path:
    if not isinstance(value, Path) or not value.is_absolute():
        _fail("HELPER_CONTAINER_OUTPUT_DIRECTORY_UNSAFE")
    rendered = str(value)
    if (
        not rendered.startswith("/")
        or "\x00" in rendered
        or any(
            _SAFE_PATH_COMPONENT_RE.fullmatch(component) is None
            for component in value.parts[1:]
        )
    ):
        _fail("HELPER_CONTAINER_OUTPUT_DIRECTORY_UNSAFE")
    _validate_ancestors(value, code="HELPER_CONTAINER_OUTPUT_DIRECTORY_UNSAFE")
    try:
        metadata = os.lstat(value)
        resolved = value.resolve(strict=True)
    except OSError:
        _fail("HELPER_CONTAINER_OUTPUT_DIRECTORY_UNSAFE")
    if (
        resolved != value
        or stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_gid != 0
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        _fail("HELPER_CONTAINER_OUTPUT_DIRECTORY_UNSAFE")
    try:
        with os.scandir(resolved) as entries:
            if any(entries):
                _fail("HELPER_CONTAINER_OUTPUT_DIRECTORY_UNSAFE")
    except PhysicalWaFiPostgresHelperContainerError:
        raise
    except OSError:
        _fail("HELPER_CONTAINER_OUTPUT_DIRECTORY_UNSAFE")
    return resolved


def _request_facts(
    value: object,
    *,
    runtime: _RuntimeFacts,
) -> _RequestFacts:
    if type(value) is not PhysicalWaFiPostgresHelperContainerCaptureRequest:
        _fail("HELPER_CONTAINER_CAPTURE_REQUEST_INVALID")
    capture_configuration_sha256 = _sha256(
        value.capture_configuration_sha256,
        code="HELPER_CONTAINER_CAPTURE_REQUEST_INVALID",
    )
    if capture_configuration_sha256 != runtime.capture_configuration_sha256:
        _fail("HELPER_CONTAINER_CAPTURE_BINDING_MISMATCH")
    return _RequestFacts(
        capture_configuration_sha256=capture_configuration_sha256,
        capture_output_root=_secure_empty_capture_output_root(value.capture_output_root),
        writer_epoch=_positive_int(
            value.writer_epoch,
            maximum=2**63 - 1,
            code="HELPER_CONTAINER_CAPTURE_REQUEST_INVALID",
        ),
        writer_lease_id=_safe_text(
            value.writer_lease_id,
            pattern=_SAFE_ID_RE,
            code="HELPER_CONTAINER_CAPTURE_REQUEST_INVALID",
        ),
        witness_transition_id=_safe_text(
            value.witness_transition_id,
            pattern=_SAFE_ID_RE,
            code="HELPER_CONTAINER_CAPTURE_REQUEST_INVALID",
        ),
        witnessed_term_proof_sha256=_sha256(
            value.witnessed_term_proof_sha256,
            code="HELPER_CONTAINER_CAPTURE_REQUEST_INVALID",
        ),
    )


def _allocate_helper_output(
    capture_output_root: Path,
    *,
    identity: _RuntimeIdentity,
) -> Path:
    for _ in range(8):
        path = capture_output_root / (_HELPER_OUTPUT_PREFIX + secrets.token_hex(16))
        try:
            path.mkdir(mode=0o700)
            os.chown(path, identity.effective_uid, identity.effective_gid)
            os.chmod(path, 0o700)
            metadata = os.lstat(path)
            resolved = path.resolve(strict=True)
        except OSError:
            _fail("HELPER_CONTAINER_OUTPUT_DIRECTORY_UNSAFE")
        if (
            resolved == path
            and stat.S_ISDIR(metadata.st_mode)
            and not stat.S_ISLNK(metadata.st_mode)
            and metadata.st_uid == identity.effective_uid
            and metadata.st_gid == identity.effective_gid
            and stat.S_IMODE(metadata.st_mode) == 0o700
        ):
            return resolved
        _fail("HELPER_CONTAINER_OUTPUT_DIRECTORY_UNSAFE")
    _fail("HELPER_CONTAINER_OUTPUT_DIRECTORY_UNSAFE")


def _parse_empty_arguments(arguments: object) -> None:
    if not isinstance(arguments, (list, tuple)) or len(arguments) != 0:
        _fail("HELPER_CONTAINER_ARGUMENTS_FORBIDDEN")


def _invocation_sha256(
    *,
    docker_binary: Path,
    helper: _HelperFacts,
    arguments: tuple[str, ...],
    runtime: _RuntimeFacts,
    request: _RequestFacts,
    helper_output_directory: Path,
) -> str:
    return hashlib.sha256(
        _canonical(
            {
                "docker_binary": str(docker_binary),
                "docker_binary_sha256": helper.docker_binary_sha256,
                "helper_image": helper.image,
                "arguments": list(arguments),
                "environment": [],
                "capture_output_root": str(request.capture_output_root),
                "helper_output_directory": str(helper_output_directory),
                "helper_uid": runtime.postgres_runtime_identity.effective_uid,
                "helper_gid": runtime.postgres_runtime_identity.effective_gid,
                "configuration_sha256": runtime.configuration_sha256,
                "installation_attestation_sha256": runtime.installation_attestation_sha256,
                "capture_configuration_sha256": request.capture_configuration_sha256,
                "deployment_manifest_lock_sha256": runtime.deployment_manifest_lock_sha256,
                "local_base_backup_auth_preflight_sha256": runtime.local_base_backup_auth_preflight_sha256,
                "postgres_runtime_identity_attestation_sha256": runtime.postgres_runtime_identity_attestation_sha256,
                "writer_epoch": request.writer_epoch,
                "writer_lease_id": request.writer_lease_id,
                "witness_transition_id": request.witness_transition_id,
                "witnessed_term_proof_sha256": request.witnessed_term_proof_sha256,
            },
            code="HELPER_CONTAINER_INVOCATION_INVALID",
        )
    ).hexdigest()


def build_wa_fi_postgres_helper_container_invocation(
    arguments: object,
    *,
    request: PhysicalWaFiPostgresHelperContainerCaptureRequest,
) -> PhysicalWaFiPostgresHelperContainerInvocation:
    """Build one immutable local Docker argv; never execute Docker itself."""

    _parse_empty_arguments(arguments)
    runtime = _load_runtime()
    facts = _request_facts(request, runtime=runtime)
    docker_binary = _secure_docker_binary(runtime.helper.docker_binary_sha256)
    helper = runtime.helper
    helper_output_directory = _allocate_helper_output(
        facts.capture_output_root,
        identity=runtime.postgres_runtime_identity,
    )
    docker_arguments = (
        str(docker_binary),
        "--context=default",
        "run",
        "--pull=never",
        "--rm",
        "--network=none",
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges:true",
        "--pids-limit=64",
        "--user="
        + str(runtime.postgres_runtime_identity.effective_uid)
        + ":"
        + str(runtime.postgres_runtime_identity.effective_gid),
        "--entrypoint=" + _FIXED_ENTRYPOINT,
        "--tmpfs=/tmp:rw,noexec,nosuid,nodev,size=64m",
        "--env=PGPASSFILE=/dev/null",
        "--mount",
        "type=volume,src="
        + helper.socket_volume
        + ",dst="
        + _FIXED_SOCKET_MOUNT_TARGET
        + ",readonly",
        "--mount",
        "type=bind,src=" + str(helper_output_directory) + ",dst=" + _FIXED_OUTPUT_MOUNT_TARGET,
        helper.image,
        "--host=" + _FIXED_SOCKET_MOUNT_TARGET,
        "--port=5432",
        "--username=" + _FIXED_SOURCE_ROLE,
        "--no-password",
        "--format=tar",
        "--wal-method=none",
        "--checkpoint=fast",
        "--pgdata=" + _FIXED_OUTPUT_MOUNT_TARGET,
    )
    invocation_sha256 = _invocation_sha256(
        docker_binary=docker_binary,
        helper=helper,
        arguments=docker_arguments,
        runtime=runtime,
        request=facts,
        helper_output_directory=helper_output_directory,
    )
    return PhysicalWaFiPostgresHelperContainerInvocation(
        docker_binary=docker_binary,
        docker_binary_sha256=helper.docker_binary_sha256,
        helper_image=helper.image,
        arguments=docker_arguments,
        environment=(),
        capture_output_root=facts.capture_output_root,
        helper_output_directory=helper_output_directory,
        helper_uid=runtime.postgres_runtime_identity.effective_uid,
        helper_gid=runtime.postgres_runtime_identity.effective_gid,
        configuration_sha256=runtime.configuration_sha256,
        installation_attestation_sha256=runtime.installation_attestation_sha256,
        capture_configuration_sha256=facts.capture_configuration_sha256,
        deployment_manifest_lock_sha256=runtime.deployment_manifest_lock_sha256,
        local_base_backup_auth_preflight_sha256=runtime.local_base_backup_auth_preflight_sha256,
        postgres_runtime_identity_attestation_sha256=runtime.postgres_runtime_identity_attestation_sha256,
        writer_epoch=facts.writer_epoch,
        writer_lease_id=facts.writer_lease_id,
        witness_transition_id=facts.witness_transition_id,
        witnessed_term_proof_sha256=facts.witnessed_term_proof_sha256,
        invocation_sha256=invocation_sha256,
    )


def _collect_helper_artifact(
    invocation: PhysicalWaFiPostgresHelperContainerInvocation,
) -> tuple[Path, str, int]:
    """Atomically move the sole helper artifact into the root-only parent."""

    try:
        directory = os.lstat(invocation.helper_output_directory)
        if (
            not stat.S_ISDIR(directory.st_mode)
            or stat.S_ISLNK(directory.st_mode)
            or directory.st_uid == 0
            or directory.st_uid != invocation.helper_uid
            or directory.st_gid != invocation.helper_gid
            or stat.S_IMODE(directory.st_mode) != 0o700
        ):
            _fail("HELPER_CONTAINER_COLLECTION_FAILED")
        with os.scandir(invocation.helper_output_directory) as entries:
            names = tuple(entry.name for entry in entries)
        if names != (_COLLECTED_ARTIFACT_NAME,):
            _fail("HELPER_CONTAINER_COLLECTION_FAILED")
        source = invocation.helper_output_directory / _COLLECTED_ARTIFACT_NAME
        destination = invocation.capture_output_root / _COLLECTED_ARTIFACT_NAME
        source_stat = os.lstat(source)
        if (
            stat.S_ISLNK(source_stat.st_mode)
            or not stat.S_ISREG(source_stat.st_mode)
            or source_stat.st_nlink != 1
            or source_stat.st_uid != directory.st_uid
            or source_stat.st_gid != directory.st_gid
            or stat.S_IMODE(source_stat.st_mode) != 0o600
            or source_stat.st_size < 1
        ):
            _fail("HELPER_CONTAINER_COLLECTION_FAILED")
        try:
            os.lstat(destination)
        except FileNotFoundError:
            pass
        else:
            _fail("HELPER_CONTAINER_COLLECTION_FAILED")
        os.rename(source, destination)
        os.chown(destination, 0, 0)
        os.chmod(destination, 0o600)
        digest = hashlib.sha256()
        with destination.open("rb") as handle:
            while chunk := handle.read(_COPY_CHUNK_BYTES):
                digest.update(chunk)
        final = os.lstat(destination)
        if (
            final.st_uid != 0
            or final.st_gid != 0
            or stat.S_IMODE(final.st_mode) != 0o600
            or final.st_nlink != 1
            or final.st_size < 1
        ):
            _fail("HELPER_CONTAINER_COLLECTION_FAILED")
        return destination, digest.hexdigest(), final.st_size
    except PhysicalWaFiPostgresHelperContainerError:
        raise
    except OSError:
        _fail("HELPER_CONTAINER_COLLECTION_FAILED")


def execute_wa_fi_postgres_helper_container_capture(
    arguments: object,
    *,
    request: PhysicalWaFiPostgresHelperContainerCaptureRequest,
    runner: PhysicalWaFiPostgresHelperContainerRunner | None,
) -> PhysicalWaFiPostgresHelperContainerResult:
    """Call only one injected runner after all local policy checks succeed."""

    invocation = build_wa_fi_postgres_helper_container_invocation(
        arguments,
        request=request,
    )
    if runner is None or not callable(getattr(runner, "run", None)):
        _fail("HELPER_CONTAINER_RUNNER_REQUIRED")
    try:
        runner_result = runner.run(invocation=invocation)
    except Exception:
        _fail("HELPER_CONTAINER_RUNNER_FAILED")
    if (
        type(runner_result) is not PhysicalWaFiPostgresHelperContainerRunnerResult
        or type(runner_result.exit_code) is not int
        or runner_result.exit_code != 0
    ):
        _fail("HELPER_CONTAINER_RUNNER_FAILED")
    artifact_path, artifact_sha256, artifact_bytes = _collect_helper_artifact(invocation)
    return PhysicalWaFiPostgresHelperContainerResult(
        configuration_sha256=invocation.configuration_sha256,
        installation_attestation_sha256=invocation.installation_attestation_sha256,
        capture_configuration_sha256=invocation.capture_configuration_sha256,
        deployment_manifest_lock_sha256=invocation.deployment_manifest_lock_sha256,
        local_base_backup_auth_preflight_sha256=invocation.local_base_backup_auth_preflight_sha256,
        invocation_sha256=invocation.invocation_sha256,
        collected_artifact_path=artifact_path,
        collected_artifact_sha256=artifact_sha256,
        collected_artifact_bytes=artifact_bytes,
    )
