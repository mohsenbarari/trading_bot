#!/usr/bin/env python3
"""Prepare one operation-scoped WA-IR shadow database from received artifacts.

The agent never starts a public API, worker, Redis, receiver, or writer.  Its
last permitted action is a one-shot writer fencing command followed by an
exact database-state attestation.  All inputs must already have been installed
by the exact-version Object Storage receiver below one operation directory.
"""

from __future__ import annotations

import argparse
import ast
import ctypes
from contextlib import contextmanager
from dataclasses import dataclass
import errno
import fcntl
import hashlib
import ipaddress
import json
import os
from pathlib import Path, PurePosixPath
import re
import selectors
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
from typing import Any, BinaryIO, Callable, Mapping

from core.docker_image_identity import (
    DockerImageIdentityError,
    image_content_descriptor,
    image_content_descriptor_from_archive_config,
    verify_content_descriptor,
)
from scripts.wa_ir_production_transport_contract import (
    MAX_PAYLOAD_BYTES,
    ProductionTransportError,
    SHA256_RE,
    validate_operation_id,
)


MANIFEST_SCHEMA = "wa-ir-production-operation-v2"
ATTESTATION_SCHEMA = "wa-ir-production-operation-attestation-v2"
STATE_SCHEMA = "wa-ir-production-operation-state-v2"
IMAGE_STAGE_ATTESTATION_SCHEMA = "wa-ir-production-image-stage-attestation-v1"
FINAL_PREPARE_MANIFEST_SCHEMA = "wa-ir-production-final-prepare-material-v1"
OPERATIONS_ROOT = Path("/srv/trading-bot/dark-standby/operations")
PROJECT_ROOT_PREFIX = Path("/srv/trading-bot-three-site-production-shadow")
DATA_ROOT_PREFIX = Path("/srv/trading-bot-three-site-production-shadow-data")
SECRET_ROOT_PREFIX = Path(
    "/root/secure-envs/trading-bot/three-site-production-shadow"
)
ROLE_COMPOSE_RELATIVE_PATH = Path("rendered/webapp-ir/docker-compose.yml")
ROLE_ENV_RELATIVE_PATH = Path("secrets/webapp-ir/runtime.env.role")
ROLE_CA_RELATIVE_PATH = Path("secrets/tls/ca.crt")
EXPECTED_RUNTIME_DESTINATIONS = {
    ROLE_COMPOSE_RELATIVE_PATH.as_posix(),
    ROLE_ENV_RELATIVE_PATH.as_posix(),
    ROLE_CA_RELATIVE_PATH.as_posix(),
}
EXPECTED_FINAL_PREPARE_ARCHIVE_PATHS = {
    ROLE_COMPOSE_RELATIVE_PATH.as_posix(): "role-compose.yml",
    ROLE_ENV_RELATIVE_PATH.as_posix(): "runtime.env.role",
    ROLE_CA_RELATIVE_PATH.as_posix(): "ca.crt",
}
FINAL_PREPARE_ARTIFACT_KIND = "final-prepare-material"
FINAL_PREPARE_DESTINATION_NAME = "final-prepare-material.tar"
FINAL_PREPARE_MANIFEST_NAME = "final-prepare-manifest.json"
IMAGE_ROLES = ("app", "postgres", "redis", "nginx")
IMAGE_ARTIFACT_KINDS = {
    "app": "app-image-archive",
    "postgres": "postgres-image-archive",
    "redis": "redis-image-archive",
    "nginx": "nginx-image-archive",
}
DOCKER = "/usr/bin/docker"
MAX_MANIFEST_BYTES = 256 * 1024
MAX_ARCHIVE_MEMBERS = 200_000
MAX_COMMAND_OUTPUT_BYTES = 2 * 1024 * 1024
MAX_BOOTSTRAP_BYTES = 4 * 1024 * 1024
MAX_STATE_BYTES = 256 * 1024
BOOTSTRAP_RELATIVE_PATH = Path("bootstrap/wa-ir-production-agent.pyz")
_MATERIALIZING_PREFIX = ".wa-ir-materialize-"
DATABASE_FINGERPRINT_ALGORITHM = (
    "pg-copy-jsonl-sha256-canonical-session-v1"
)
DATABASE_FINGERPRINT_SESSION_SETTINGS = {
    "bytea_output": "hex",
    "datestyle": "ISO,YMD",
    "default_transaction_read_only": "on",
    "extra_float_digits": "3",
    "intervalstyle": "iso_8601",
    "standard_conforming_strings": "on",
    "timezone": "UTC",
}
DATABASE_FINGERPRINT_PGOPTIONS = " ".join(
    f"-c {name}={value}"
    for name, value in sorted(DATABASE_FINGERPRINT_SESSION_SETTINGS.items())
)
DATABASE_FINGERPRINT_CLIENT_ENCODING = "UTF8"
EXPECTED_ARTIFACTS = {
    "release-bundle": ("release.bundle", "git-bundle"),
    "app-image-archive": ("app-image.tar", "docker-archive"),
    "postgres-image-archive": (
        "postgres-image.tar",
        "docker-archive",
    ),
    "redis-image-archive": ("redis-image.tar", "docker-archive"),
    "nginx-image-archive": ("nginx-image.tar", "docker-archive"),
    "database-backup": ("database.dump", "postgres-custom"),
    "uploads-archive": ("uploads.tar.gz", "tar-gzip"),
    "audit-archive": ("audit.tar.gz", "tar-gzip"),
}
EXPECTED_SERVICES = {
    "database": "webapp_ir_db",
    "restore": "webapp_ir_restore_tool",
    "roles": "webapp_ir_db_roles",
    "migration": "webapp_ir_migration",
    "roles_post_migration": "webapp_ir_db_roles_post_migration",
    "fencing": "webapp_ir_db_fencing",
    "writer_fence": "webapp_ir_writer_fence",
}
EXPECTED_SAFETY = {
    "allow_public_ingress": False,
    "allow_private_services": False,
    "allow_writer": False,
    "allow_legacy_mutation": False,
    "allow_object_delete": False,
    "allow_persistent_resource_cleanup": False,
    "allow_bounded_ephemeral_oneoff_cleanup": True,
}
_MANIFEST_FIELDS = {
    "schema",
    "operation_id",
    "release_sha",
    "release_tree_sha",
    "bootstrap",
    "expected_migration_revision",
    "source_database",
    "artifacts",
    "image_artifacts",
    "postgres_runtime_uid",
    "postgres_runtime_gid",
    "compose",
    "safety",
}
_BOOTSTRAP_FIELDS = {
    "artifact_kind",
    "destination_name",
    "sha256",
    "bytes",
    "format",
    "source_release_sha",
    "source_release_tree_sha",
}
_ARTIFACT_FIELDS = {"kind", "destination_name", "sha256", "bytes", "format"}
_IMAGE_ARTIFACT_FIELDS = {
    "archive_sha256",
    "archive_bytes",
    "config_digest",
    "content_descriptor",
    "content_identity",
}
_RUNTIME_ENTRY_FIELDS = {
    "archive_path",
    "destination",
    "sha256",
    "bytes",
    "mode",
}
_FINAL_PREPARE_MANIFEST_FIELDS = {
    "schema",
    "operation_id",
    "release_sha",
    "operation_manifest_sha256",
    "stage_attestation_sha256",
    "role",
    "runtime_image_ids",
    "entries",
    "required_env_keys",
}
_COMPOSE_FIELDS = {"relative_path", "project_name", "services"}
_SOURCE_DATABASE_FIELDS = {
    "alembic_revision",
    "fingerprint_algorithm",
    "database_fingerprint_sha256",
    "row_count",
    "table_count",
}
_PHASES = (
    "received",
    "materialized",
    "images-loaded",
    "final-material-installed",
    "database-started",
    "database-restored",
    "database-migrated",
    "writer-fenced",
    "verified",
)
_STATE_FIELDS = {
    "schema",
    "operation_id",
    "release_sha",
    "release_tree_sha",
    "manifest_sha256",
    "completed_phases",
    "evidence",
}
_ENV_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
_IMAGE_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
POSTGRES_RUNTIME_UID_LABEL = "trading-bot.postgres.runtime-uid"
POSTGRES_RUNTIME_GID_LABEL = "trading-bot.postgres.runtime-gid"
_RELEASE_RE = re.compile(r"^[0-9a-f]{40}$")
_REVISION_RE = re.compile(r"^[0-9a-z]{1,64}$")
_ALEMBIC_REVISION_RE = re.compile(r"^[0-9a-z_]{1,64}$")
_TAG_RE = re.compile(
    r"^[a-z0-9][a-z0-9._/-]{0,127}:[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$"
)
_SAFE_DOTENV_VALUE_RE = re.compile(r"^[\x21-\x7e]+$")
_COMPOSE_ENV_REFERENCE_RE = re.compile(r"(?<!\$)\$\{([A-Z][A-Z0-9_]*)")
_COMPOSE_REQUIRED_ENV_REFERENCE_RE = re.compile(
    r"(?<!\$)\$\{([A-Z][A-Z0-9_]*):\?"
)
_FORBIDDEN_PREPARE_ENV_NAMES = {
    "BOT_TOKEN",
    "ARVAN_S3_ACCESS_KEY",
    "ARVAN_S3_SECRET_KEY",
    "DR_BLOB_OBJECT_ENDPOINT",
    "DR_BLOB_OBJECT_REGION",
    "DR_BLOB_OBJECT_BUCKET",
    "DR_BLOB_OBJECT_PREFIX",
    "DR_BLOB_S3_CREDENTIALS_FILE",
    "DR_BLOB_ENCRYPTION_KEYRING_FILE",
    "WEBAPP_IR_DR_PEERS_JSON",
    "WEBAPP_IR_DR_PAIRWISE_KEYS_JSON",
    "WEBAPP_IR_SHADOW_DR_BIND_ADDRESS",
    "WEBAPP_IR_SHADOW_DR_PORT",
    "WEBAPP_IR_PEER_WEBAPP_FI_IP",
}
_SAFE_ENV = {
    "PATH": "/usr/bin:/bin",
    "HOME": "/nonexistent",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "DOCKER_CONFIG": "/nonexistent",
}
_SAFE_GIT_ENV = {
    **_SAFE_ENV,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_OPTIONAL_LOCKS": "0",
}
GIT = "/usr/bin/git"


class ProductionOperationError(RuntimeError):
    """A redacted fail-closed production operation error."""


@dataclass(frozen=True)
class Artifact:
    kind: str
    destination_name: str
    sha256: str
    bytes: int
    format: str


@dataclass(frozen=True)
class ImageArtifact:
    role: str
    artifact_kind: str
    archive_sha256: str
    archive_bytes: int
    config_digest: str
    content_descriptor: Mapping[str, Any]
    content_identity: str


@dataclass(frozen=True)
class Image:
    """Compatibility input for the release sealer's archive validator."""

    role: str
    artifact_kind: str | None
    image_id: str
    repo_tags: tuple[str, ...]
    os: str
    architecture: str
    runtime_uid: int | None = None
    runtime_gid: int | None = None


@dataclass(frozen=True)
class RuntimeEntry:
    archive_path: str
    destination: str
    sha256: str
    bytes: int
    mode: int


@dataclass(frozen=True)
class FinalPrepareManifest:
    operation_id: str
    release_sha: str
    operation_manifest_sha256: str
    stage_attestation_sha256: str
    runtime_image_ids: Mapping[str, str]
    entries: tuple[RuntimeEntry, ...]
    required_env_keys: tuple[str, ...]
    canonical_sha256: str


@dataclass(frozen=True)
class OperationManifest:
    operation_id: str
    release_sha: str
    release_tree_sha: str
    bootstrap_sha256: str
    bootstrap_bytes: int
    expected_migration_revision: str
    source_database: Mapping[str, Any]
    artifacts: Mapping[str, Artifact]
    image_artifacts: Mapping[str, ImageArtifact]
    postgres_runtime_uid: int
    postgres_runtime_gid: int
    project_name: str
    services: Mapping[str, str]
    canonical_sha256: str

    @property
    def images(self) -> tuple[Image, ...]:
        """Compatibility view for release validators that expect archive IDs."""

        return tuple(
            Image(
                role=role,
                artifact_kind=image.artifact_kind,
                image_id=image.config_digest,
                repo_tags=(),
                os=str(image.content_descriptor["os"]),
                architecture=str(
                    image.content_descriptor["architecture"]
                ),
                runtime_uid=(
                    self.postgres_runtime_uid
                    if role == "postgres"
                    else None
                ),
                runtime_gid=(
                    self.postgres_runtime_gid
                    if role == "postgres"
                    else None
                ),
            )
            for role, image in self.image_artifacts.items()
        )


@dataclass(frozen=True)
class CanonicalOperationPaths:
    project_root: Path
    release_root: Path
    data_root: Path
    secret_root: Path
    compose: Path
    runtime_env: Path
    ca: Path
    restore_dump: Path
    postgres: Path
    redis: Path
    uploads: Path
    audit: Path


@dataclass(frozen=True)
class MigrationGraph:
    parents: Mapping[str, tuple[str, ...]]
    sources: Mapping[str, Path]


@dataclass(frozen=True)
class StreamDigest:
    sha256: str
    bytes: int
    records: int


def _strict_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _project_base(operation_id: str) -> str:
    return f"tb3p-{operation_id.replace('-', '')}"


def _canonical_operation_paths(
    manifest: OperationManifest,
) -> CanonicalOperationPaths:
    project_root = PROJECT_ROOT_PREFIX / manifest.operation_id
    data_root = DATA_ROOT_PREFIX / manifest.operation_id
    secret_root = SECRET_ROOT_PREFIX / manifest.operation_id
    return CanonicalOperationPaths(
        project_root=project_root,
        release_root=project_root / "releases" / manifest.release_sha,
        data_root=data_root,
        secret_root=secret_root,
        compose=project_root / ROLE_COMPOSE_RELATIVE_PATH,
        runtime_env=secret_root / "webapp-ir" / "runtime.env.role",
        ca=secret_root / "tls" / "ca.crt",
        restore_dump=data_root
        / "restore-input"
        / "webapp-ir"
        / "database.dump",
        postgres=data_root / "webapp-ir" / "postgres",
        redis=data_root / "webapp-ir" / "redis",
        uploads=data_root / "webapp-ir" / "uploads",
        audit=data_root / "webapp-ir" / "audit",
    )


def _runtime_destination_path(
    paths: CanonicalOperationPaths,
    destination: str,
) -> Path:
    destinations = {
        ROLE_COMPOSE_RELATIVE_PATH.as_posix(): paths.compose,
        ROLE_ENV_RELATIVE_PATH.as_posix(): paths.runtime_env,
        ROLE_CA_RELATIVE_PATH.as_posix(): paths.ca,
    }
    try:
        return destinations[destination]
    except KeyError as exc:
        raise ProductionOperationError(
            "runtime destination is outside the canonical role roots"
        ) from exc


def _state_path(operation_root: Path) -> Path:
    return operation_root / "operation-state.json"


@contextmanager
def _operation_lock(operation_root: Path, *, required_uid: int):
    path = operation_root / "operation.lock"
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDWR
            | os.O_CREAT
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != required_uid
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            raise ProductionOperationError("operation lock file is unsafe")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ProductionOperationError(
                "another operation invocation is already active"
            ) from exc
        os.fsync(descriptor)
        _fsync_directory(operation_root)
        yield
    except OSError as exc:
        raise ProductionOperationError("operation lock is unavailable") from exc
    finally:
        if descriptor >= 0:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            except OSError:
                pass
            os.close(descriptor)


def _reconcile_state_temporaries(path: Path) -> None:
    expected = f".{path.name}.materializing"
    legacy = re.compile(rf"^\.{re.escape(path.name)}\.[1-9][0-9]{{0,19}}\.tmp$")
    candidates: list[Path] = []
    try:
        with os.scandir(path.parent) as entries:
            for entry in entries:
                if entry.name == expected or legacy.fullmatch(entry.name):
                    candidates.append(path.parent / entry.name)
                    if len(candidates) > 1024:
                        raise ProductionOperationError(
                            "operation state temporary inventory is excessive"
                        )
    except ProductionOperationError:
        raise
    except OSError as exc:
        raise ProductionOperationError(
            "operation state temporary inventory is unavailable"
        ) from exc
    removed = False
    for candidate in candidates:
        try:
            metadata = candidate.stat(follow_symlinks=False)
        except OSError as exc:
            raise ProductionOperationError(
                "operation state temporary is unsafe"
            ) from exc
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or not 0 <= metadata.st_size <= MAX_STATE_BYTES
        ):
            raise ProductionOperationError(
                "operation state temporary is unsafe"
            )
        try:
            candidate.unlink()
        except OSError as exc:
            raise ProductionOperationError(
                "operation state temporary could not be reconciled"
            ) from exc
        removed = True
    if removed:
        _fsync_directory(path.parent)


def _write_state(path: Path, state: Mapping[str, Any]) -> None:
    payload = _canonical_json(dict(state)) + b"\n"
    if len(payload) > MAX_STATE_BYTES:
        raise ProductionOperationError("operation state is oversized")
    _reconcile_state_temporaries(path)
    temporary = path.with_name(f".{path.name}.materializing")
    descriptor = -1
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short state write")
            view = view[written:]
        os.fchmod(descriptor, 0o600)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary, path)
        directory_fd = os.open(
            path.parent,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except OSError as exc:
        raise ProductionOperationError("operation state could not be persisted") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        _reconcile_state_temporaries(path)


def _load_or_create_state(
    manifest: OperationManifest,
    *,
    operation_root: Path,
) -> dict[str, Any]:
    path = _state_path(operation_root)
    _reconcile_state_temporaries(path)
    if not path.exists() and not path.is_symlink():
        state: dict[str, Any] = {
            "schema": STATE_SCHEMA,
            "operation_id": manifest.operation_id,
            "release_sha": manifest.release_sha,
            "release_tree_sha": manifest.release_tree_sha,
            "manifest_sha256": manifest.canonical_sha256,
            "completed_phases": ["received"],
            "evidence": {
                "received": {
                    "manifest_sha256": manifest.canonical_sha256,
                    "artifact_count": len(manifest.artifacts),
                }
            },
        }
        _write_state(path, state)
        return state
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or not 1 <= metadata.st_size <= MAX_STATE_BYTES
        ):
            raise ProductionOperationError("operation state file is unsafe")
        chunks: list[bytes] = []
        observed_size = 0
        while observed_size <= MAX_STATE_BYTES:
            chunk = os.read(
                descriptor,
                min(64 * 1024, MAX_STATE_BYTES + 1 - observed_size),
            )
            if not chunk:
                break
            chunks.append(chunk)
            observed_size += len(chunk)
        after = os.fstat(descriptor)
        stable = (
            "st_dev",
            "st_ino",
            "st_mode",
            "st_uid",
            "st_nlink",
            "st_size",
            "st_mtime_ns",
        )
        if (
            any(getattr(metadata, name) != getattr(after, name) for name in stable)
            or observed_size != metadata.st_size
        ):
            raise ProductionOperationError(
                "operation state changed while reading"
            )
        payload = b"".join(chunks)
        state = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_strict_json_object,
        )
    except ProductionOperationError:
        raise
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise ProductionOperationError("operation state is unavailable or invalid") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    completed = state.get("completed_phases") if isinstance(state, dict) else None
    evidence = state.get("evidence") if isinstance(state, dict) else None
    if (
        not isinstance(state, dict)
        or set(state) != _STATE_FIELDS
        or state.get("schema") != STATE_SCHEMA
        or state.get("operation_id") != manifest.operation_id
        or state.get("release_sha") != manifest.release_sha
        or state.get("release_tree_sha") != manifest.release_tree_sha
        or state.get("manifest_sha256") != manifest.canonical_sha256
        or not isinstance(completed, list)
        or completed != list(_PHASES[: len(completed)])
        or not isinstance(evidence, dict)
        or set(evidence) != set(completed)
        or any(not isinstance(value, dict) for value in evidence.values())
    ):
        raise ProductionOperationError("operation state binding or phase order is invalid")
    return state


def _advance_state(
    state: dict[str, Any],
    phase: str,
    evidence: Mapping[str, Any],
    *,
    operation_root: Path,
) -> None:
    completed = state["completed_phases"]
    if phase in completed:
        return
    if len(completed) >= len(_PHASES) or _PHASES[len(completed)] != phase:
        raise ProductionOperationError("operation phase transition is out of order")
    state["completed_phases"] = [*completed, phase]
    state["evidence"] = {**state["evidence"], phase: dict(evidence)}
    _write_state(_state_path(operation_root), state)


def _bounded_int(value: Any, *, minimum: int, maximum: int, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ProductionOperationError(f"{label} is not an integer")
    if not minimum <= value <= maximum:
        raise ProductionOperationError(f"{label} is outside its bound")
    return value


def _safe_relative_path(raw: Any, *, label: str) -> str:
    if not isinstance(raw, str) or not raw or "\\" in raw or "\x00" in raw:
        raise ProductionOperationError(f"{label} is invalid")
    path = PurePosixPath(raw)
    if (
        path.is_absolute()
        or raw != path.as_posix()
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ProductionOperationError(f"{label} is outside its operation scope")
    return raw


def _load_manifest_bytes(payload: bytes) -> OperationManifest:
    if not 1 <= len(payload) <= MAX_MANIFEST_BYTES:
        raise ProductionOperationError("operation manifest is empty or oversized")
    try:
        document = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_strict_json_object,
        )
    except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise ProductionOperationError("operation manifest is invalid JSON") from exc
    if (
        not isinstance(document, dict)
        or set(document) != _MANIFEST_FIELDS
        or document.get("schema") != MANIFEST_SCHEMA
    ):
        raise ProductionOperationError("operation manifest schema or fields are invalid")
    try:
        operation_id = validate_operation_id(document["operation_id"])
    except ProductionTransportError as exc:
        raise ProductionOperationError("operation manifest id is invalid") from exc
    release_sha = document.get("release_sha")
    release_tree_sha = document.get("release_tree_sha")
    revision = document.get("expected_migration_revision")
    if (
        not isinstance(release_sha, str)
        or not _RELEASE_RE.fullmatch(release_sha)
        or not isinstance(release_tree_sha, str)
        or not _RELEASE_RE.fullmatch(release_tree_sha)
        or not isinstance(revision, str)
        or not _REVISION_RE.fullmatch(revision)
    ):
        raise ProductionOperationError("release or migration identity is invalid")

    bootstrap = document.get("bootstrap")
    if (
        not isinstance(bootstrap, dict)
        or set(bootstrap) != _BOOTSTRAP_FIELDS
        or bootstrap.get("artifact_kind") != "receiver-bootstrap"
        or bootstrap.get("destination_name") != "wa-ir-production-agent.pyz"
        or bootstrap.get("format") != "python-zipapp"
        or bootstrap.get("source_release_sha") != release_sha
        or bootstrap.get("source_release_tree_sha") != release_tree_sha
        or not isinstance(bootstrap.get("sha256"), str)
        or not SHA256_RE.fullmatch(bootstrap["sha256"])
    ):
        raise ProductionOperationError("bootstrap executable binding is invalid")
    bootstrap_bytes = _bounded_int(
        bootstrap.get("bytes"),
        minimum=1,
        maximum=4 * 1024 * 1024,
        label="bootstrap executable size",
    )

    raw_artifacts = document.get("artifacts")
    if not isinstance(raw_artifacts, list) or len(raw_artifacts) != len(EXPECTED_ARTIFACTS):
        raise ProductionOperationError("operation artifact inventory is incomplete")
    artifacts: dict[str, Artifact] = {}
    destinations: set[str] = set()
    for raw in raw_artifacts:
        if not isinstance(raw, dict) or set(raw) != _ARTIFACT_FIELDS:
            raise ProductionOperationError("operation artifact entry is invalid")
        kind = raw.get("kind")
        if not isinstance(kind, str) or kind not in EXPECTED_ARTIFACTS or kind in artifacts:
            raise ProductionOperationError("operation artifact kind is invalid or duplicate")
        expected_name, expected_format = EXPECTED_ARTIFACTS[kind]
        destination = raw.get("destination_name")
        digest = raw.get("sha256")
        if (
            destination != expected_name
            or destination in destinations
            or raw.get("format") != expected_format
            or not isinstance(digest, str)
            or not SHA256_RE.fullmatch(digest)
        ):
            raise ProductionOperationError("operation artifact binding is invalid")
        size = _bounded_int(
            raw.get("bytes"),
            minimum=1,
            maximum=MAX_PAYLOAD_BYTES,
            label=f"{kind} size",
        )
        artifacts[kind] = Artifact(kind, destination, digest, size, expected_format)
        destinations.add(destination)
    if set(artifacts) != set(EXPECTED_ARTIFACTS):
        raise ProductionOperationError("operation artifact inventory is incomplete")

    raw_images = document.get("image_artifacts")
    if (
        not isinstance(raw_images, dict)
        or set(raw_images) != set(IMAGE_ROLES)
    ):
        raise ProductionOperationError("operation image inventory is incomplete")
    image_artifacts: dict[str, ImageArtifact] = {}
    config_digests: set[str] = set()
    content_identities: set[str] = set()
    for role in IMAGE_ROLES:
        raw = raw_images[role]
        if (
            not isinstance(raw, dict)
            or set(raw) != _IMAGE_ARTIFACT_FIELDS
        ):
            raise ProductionOperationError("operation image entry is invalid")
        artifact_kind = IMAGE_ARTIFACT_KINDS[role]
        archive = artifacts[artifact_kind]
        archive_sha256 = raw.get("archive_sha256")
        archive_bytes = raw.get("archive_bytes")
        config_digest = raw.get("config_digest")
        content_descriptor = raw.get("content_descriptor")
        content_identity = raw.get("content_identity")
        try:
            verified_content_identity = verify_content_descriptor(
                content_descriptor
            )
        except DockerImageIdentityError as exc:
            raise ProductionOperationError(
                "operation image content descriptor is invalid"
            ) from exc
        if (
            archive_sha256 != archive.sha256
            or archive_bytes != archive.bytes
            or not isinstance(config_digest, str)
            or not _IMAGE_ID_RE.fullmatch(config_digest)
            or config_digest in config_digests
            or not isinstance(content_identity, str)
            or not _IMAGE_ID_RE.fullmatch(content_identity)
            or content_identity != verified_content_identity
            or content_identity in content_identities
            or content_identity == config_digest
            or content_descriptor.get("architecture") != "amd64"
            or content_descriptor.get("os") != "linux"
        ):
            raise ProductionOperationError("operation image binding is invalid")
        image_artifacts[role] = ImageArtifact(
            role=role,
            artifact_kind=artifact_kind,
            archive_sha256=archive_sha256,
            archive_bytes=archive_bytes,
            config_digest=config_digest,
            content_descriptor=dict(content_descriptor),
            content_identity=content_identity,
        )
        config_digests.add(config_digest)
        content_identities.add(content_identity)
    if config_digests & content_identities:
        raise ProductionOperationError(
            "operation image config and content identities overlap"
        )
    postgres_runtime_uid = _bounded_int(
        document.get("postgres_runtime_uid"),
        minimum=1,
        maximum=65535,
        label="PostgreSQL runtime uid",
    )
    postgres_runtime_gid = _bounded_int(
        document.get("postgres_runtime_gid"),
        minimum=1,
        maximum=65535,
        label="PostgreSQL runtime gid",
    )

    compose = document.get("compose")
    if not isinstance(compose, dict) or set(compose) != _COMPOSE_FIELDS:
        raise ProductionOperationError("operation Compose binding is invalid")
    project_base = _project_base(operation_id)
    project_name = f"{project_base}-webapp-ir"
    if (
        compose.get("relative_path") != ROLE_COMPOSE_RELATIVE_PATH.as_posix()
        or compose.get("project_name") != project_name
        or compose.get("services") != EXPECTED_SERVICES
    ):
        raise ProductionOperationError("operation Compose scope drifted")

    source_database = document.get("source_database")
    if (
        not isinstance(source_database, dict)
        or set(source_database) != _SOURCE_DATABASE_FIELDS
        or not isinstance(source_database.get("alembic_revision"), str)
        or not _REVISION_RE.fullmatch(source_database["alembic_revision"])
        or not isinstance(source_database.get("database_fingerprint_sha256"), str)
        or not SHA256_RE.fullmatch(source_database["database_fingerprint_sha256"])
        or source_database.get("fingerprint_algorithm")
        != DATABASE_FINGERPRINT_ALGORITHM
    ):
        raise ProductionOperationError("source database attestation is invalid")
    _bounded_int(
        source_database.get("row_count"),
        minimum=0,
        maximum=10**15,
        label="source database row count",
    )
    _bounded_int(
        source_database.get("table_count"),
        minimum=1,
        maximum=100_000,
        label="source database table count",
    )
    if document.get("safety") != EXPECTED_SAFETY:
        raise ProductionOperationError("operation safety policy is not fail-closed")
    return OperationManifest(
        operation_id=operation_id,
        release_sha=release_sha,
        release_tree_sha=release_tree_sha,
        bootstrap_sha256=bootstrap["sha256"],
        bootstrap_bytes=bootstrap_bytes,
        expected_migration_revision=revision,
        source_database=dict(source_database),
        artifacts=artifacts,
        image_artifacts=image_artifacts,
        postgres_runtime_uid=postgres_runtime_uid,
        postgres_runtime_gid=postgres_runtime_gid,
        project_name=project_name,
        services=dict(EXPECTED_SERVICES),
        canonical_sha256=hashlib.sha256(_canonical_json(document)).hexdigest(),
    )


def load_manifest(path: Path, *, required_uid: int) -> OperationManifest:
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != required_uid
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or not 1 <= metadata.st_size <= MAX_MANIFEST_BYTES
        ):
            raise ProductionOperationError("operation manifest file is unsafe")
        payload = b""
        while len(payload) <= MAX_MANIFEST_BYTES:
            chunk = os.read(descriptor, min(64 * 1024, MAX_MANIFEST_BYTES + 1 - len(payload)))
            if not chunk:
                break
            payload += chunk
        after = os.fstat(descriptor)
        if (
            metadata.st_ino != after.st_ino
            or metadata.st_dev != after.st_dev
            or metadata.st_size != after.st_size
            or metadata.st_mtime_ns != after.st_mtime_ns
        ):
            raise ProductionOperationError("operation manifest changed while reading")
    except OSError as exc:
        raise ProductionOperationError("operation manifest is unavailable or unsafe") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    return _load_manifest_bytes(payload)


def _load_final_prepare_manifest_bytes(
    payload: bytes,
    *,
    manifest: OperationManifest,
    expected_stage_attestation_sha256: str,
) -> FinalPrepareManifest:
    if not 1 <= len(payload) <= MAX_MANIFEST_BYTES:
        raise ProductionOperationError(
            "final prepare manifest is empty or oversized"
        )
    try:
        document = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_strict_json_object,
        )
    except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise ProductionOperationError(
            "final prepare manifest is invalid JSON"
        ) from exc
    runtime_image_ids = (
        document.get("runtime_image_ids")
        if isinstance(document, dict)
        else None
    )
    if (
        not isinstance(document, dict)
        or set(document) != _FINAL_PREPARE_MANIFEST_FIELDS
        or document.get("schema") != FINAL_PREPARE_MANIFEST_SCHEMA
        or document.get("operation_id") != manifest.operation_id
        or document.get("release_sha") != manifest.release_sha
        or document.get("operation_manifest_sha256")
        != manifest.canonical_sha256
        or document.get("stage_attestation_sha256")
        != expected_stage_attestation_sha256
        or document.get("role") != "webapp_ir"
        or not isinstance(runtime_image_ids, dict)
        or set(runtime_image_ids) != set(IMAGE_ROLES)
        or any(
            not isinstance(value, str)
            or not _IMAGE_ID_RE.fullmatch(value)
            for value in runtime_image_ids.values()
        )
        or len(set(runtime_image_ids.values())) != len(IMAGE_ROLES)
    ):
        raise ProductionOperationError(
            "final prepare manifest identity binding is invalid"
        )

    raw_entries = document.get("entries")
    raw_required_keys = document.get("required_env_keys")
    if (
        not isinstance(raw_entries, list)
        or len(raw_entries) != len(EXPECTED_RUNTIME_DESTINATIONS)
        or not isinstance(raw_required_keys, list)
        or not raw_required_keys
        or len(raw_required_keys) > 256
        or any(
            not isinstance(key, str)
            or not _ENV_NAME_RE.fullmatch(key)
            for key in raw_required_keys
        )
        or raw_required_keys != sorted(set(raw_required_keys))
    ):
        raise ProductionOperationError(
            "final prepare material inventory is invalid"
        )
    runtime_entries: list[RuntimeEntry] = []
    archive_paths: set[str] = set()
    destinations: set[str] = set()
    for raw in raw_entries:
        if not isinstance(raw, dict) or set(raw) != _RUNTIME_ENTRY_FIELDS:
            raise ProductionOperationError(
                "final prepare material entry is invalid"
            )
        archive_path = _safe_relative_path(
            raw.get("archive_path"),
            label="final prepare archive path",
        )
        destination = _safe_relative_path(
            raw.get("destination"),
            label="final prepare destination",
        )
        digest = raw.get("sha256")
        if (
            archive_path in archive_paths
            or destination in destinations
            or destination not in EXPECTED_RUNTIME_DESTINATIONS
            or archive_path
            != EXPECTED_FINAL_PREPARE_ARCHIVE_PATHS[destination]
            or not isinstance(digest, str)
            or not SHA256_RE.fullmatch(digest)
            or raw.get("mode") != "0600"
        ):
            raise ProductionOperationError(
                "final prepare material entry binding is invalid"
            )
        size = _bounded_int(
            raw.get("bytes"),
            minimum=1,
            maximum=16 * 1024 * 1024,
            label="final prepare material entry size",
        )
        runtime_entries.append(
            RuntimeEntry(archive_path, destination, digest, size, 0o600)
        )
        archive_paths.add(archive_path)
        destinations.add(destination)
    if destinations != EXPECTED_RUNTIME_DESTINATIONS:
        raise ProductionOperationError(
            "final prepare material destination set is incomplete"
        )
    return FinalPrepareManifest(
        operation_id=manifest.operation_id,
        release_sha=manifest.release_sha,
        operation_manifest_sha256=manifest.canonical_sha256,
        stage_attestation_sha256=expected_stage_attestation_sha256,
        runtime_image_ids={
            role: str(runtime_image_ids[role])
            for role in IMAGE_ROLES
        },
        entries=tuple(
            sorted(runtime_entries, key=lambda entry: entry.destination)
        ),
        required_env_keys=tuple(raw_required_keys),
        canonical_sha256=hashlib.sha256(_canonical_json(document)).hexdigest(),
    )


def parse_safe_dotenv(payload: bytes) -> dict[str, str]:
    """Parse a Compose env file without quotes, interpolation, or ambiguity."""

    if not 1 <= len(payload) <= 1024 * 1024 or b"\x00" in payload:
        raise ProductionOperationError("runtime environment is empty or oversized")
    try:
        text = payload.decode("ascii")
    except UnicodeDecodeError as exc:
        raise ProductionOperationError("runtime environment must be ASCII") from exc
    values: dict[str, str] = {}
    for number, raw in enumerate(text.splitlines(), 1):
        if not raw or raw.startswith("#"):
            continue
        if raw != raw.strip() or "=" not in raw:
            raise ProductionOperationError(
                f"runtime environment line {number} is ambiguous"
            )
        key, value = raw.split("=", 1)
        if (
            not _ENV_NAME_RE.fullmatch(key)
            or key in values
            or not value
            or not _SAFE_DOTENV_VALUE_RE.fullmatch(value)
            or value[0] in {'"', "'"}
            or any(character in value for character in ("$", "`", "\\", "#"))
        ):
            raise ProductionOperationError(
                f"runtime environment line {number} is unsafe"
            )
        values[key] = value
    if not values:
        raise ProductionOperationError("runtime environment has no values")
    return values


def _hash_regular_file(
    path: Path,
    *,
    expected_uid: int,
    maximum: int,
    minimum: int = 1,
    expected_mode: int = 0o600,
) -> tuple[str, int]:
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != expected_uid
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) != expected_mode
            or not minimum <= before.st_size <= maximum
        ):
            raise ProductionOperationError("operation artifact file is unsafe")
        digest = hashlib.sha256()
        size = 0
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > maximum:
                raise ProductionOperationError("operation artifact exceeded its bound")
            digest.update(chunk)
        after = os.fstat(descriptor)
        stable = ("st_dev", "st_ino", "st_mode", "st_uid", "st_nlink", "st_size", "st_mtime_ns")
        if any(getattr(before, name) != getattr(after, name) for name in stable):
            raise ProductionOperationError("operation artifact changed while reading")
        return digest.hexdigest(), size
    except OSError as exc:
        raise ProductionOperationError("operation artifact is unavailable or unsafe") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def verify_incoming(
    manifest: OperationManifest,
    *,
    operation_root: Path,
    required_uid: int,
    allow_final_prepare: bool = False,
) -> Mapping[str, Path]:
    incoming = operation_root / "incoming"
    try:
        incoming_metadata = incoming.stat(follow_symlinks=False)
    except OSError as exc:
        raise ProductionOperationError("operation incoming directory is unavailable") from exc
    if (
        not stat.S_ISDIR(incoming_metadata.st_mode)
        or incoming_metadata.st_uid != required_uid
        or stat.S_IMODE(incoming_metadata.st_mode) != 0o700
    ):
        raise ProductionOperationError("operation incoming directory is unsafe")
    expected_names = {
        artifact.destination_name for artifact in manifest.artifacts.values()
    } | {"operation-manifest.json"}
    if allow_final_prepare:
        expected_names.add(FINAL_PREPARE_DESTINATION_NAME)
    try:
        observed_names = {entry.name for entry in os.scandir(incoming)}
    except OSError as exc:
        raise ProductionOperationError("operation incoming directory cannot be enumerated") from exc
    if observed_names != expected_names:
        raise ProductionOperationError("operation incoming artifact set is not exact")
    paths: dict[str, Path] = {}
    for kind, artifact in manifest.artifacts.items():
        path = incoming / artifact.destination_name
        observed = _hash_regular_file(
            path,
            expected_uid=required_uid,
            maximum=MAX_PAYLOAD_BYTES,
        )
        if observed != (artifact.sha256, artifact.bytes):
            raise ProductionOperationError(f"{kind} hash or size differs")
        paths[kind] = path
    return paths


def _validate_tar_members(
    archive: tarfile.TarFile,
    *,
    expected_files: Mapping[str, tuple[str, int]] | None = None,
) -> list[tarfile.TarInfo]:
    members: list[tarfile.TarInfo] = []
    names: set[str] = set()
    regular_files: dict[str, tuple[str, int]] = {}
    total = 0
    for member in archive:
        if len(members) >= MAX_ARCHIVE_MEMBERS:
            raise ProductionOperationError("archive has too many members")
        name = _safe_relative_path(member.name.rstrip("/"), label="archive member")
        if any(part.startswith(_MATERIALIZING_PREFIX) for part in PurePosixPath(name).parts):
            raise ProductionOperationError(
                "archive member uses a reserved materialization name"
            )
        if name in names:
            raise ProductionOperationError("archive contains a duplicate member")
        names.add(name)
        if member.isdir():
            if member.size != 0:
                raise ProductionOperationError("archive directory has content bytes")
        elif member.isreg():
            if member.size < 0 or member.size > MAX_PAYLOAD_BYTES:
                raise ProductionOperationError("archive member size is invalid")
            total += member.size
            if total > MAX_PAYLOAD_BYTES:
                raise ProductionOperationError("archive expanded size is oversized")
            regular_files[name] = ("", member.size)
        else:
            raise ProductionOperationError(
                "archive contains a link, sparse, or special member"
            )
        members.append(member)
    if not members:
        raise ProductionOperationError("archive is empty")
    if expected_files is not None:
        expected_names = set(expected_files)
        if set(regular_files) != expected_names:
            raise ProductionOperationError("runtime archive member set differs")
        for name, (_digest, expected_bytes) in expected_files.items():
            if regular_files[name][1] != expected_bytes:
                raise ProductionOperationError("runtime archive member size differs")
    return members


def verify_tar_archive(
    path: Path,
    *,
    mode: str,
    expected_files: Mapping[str, tuple[str, int]] | None = None,
) -> list[tarfile.TarInfo]:
    try:
        with tarfile.open(path, mode=mode) as archive:
            return _validate_tar_members(archive, expected_files=expected_files)
    except ProductionOperationError:
        raise
    except (OSError, EOFError, tarfile.TarError) as exc:
        raise ProductionOperationError("archive integrity validation failed") from exc


def _ensure_secure_directory(path: Path, *, required_uid: int) -> None:
    if path.exists() or path.is_symlink():
        try:
            metadata = path.stat(follow_symlinks=False)
        except OSError as exc:
            raise ProductionOperationError("operation directory is unsafe") from exc
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != required_uid
            or stat.S_IMODE(metadata.st_mode) != 0o700
        ):
            raise ProductionOperationError("operation directory is unsafe")
        return
    try:
        path.mkdir(mode=0o700)
        _fsync_directory(path.parent)
        _fsync_directory(path)
    except OSError as exc:
        raise ProductionOperationError("operation directory could not be created") from exc


def _require_real_owned_directory_chain(
    path: Path,
    *,
    required_uid: int,
) -> None:
    if not path.is_absolute() or ".." in path.parts:
        raise ProductionOperationError(
            "canonical production directory chain is invalid"
        )
    current = Path(path.anchor)
    for component in path.parts[1:]:
        current /= component
        try:
            metadata = current.stat(follow_symlinks=False)
        except OSError as exc:
            raise ProductionOperationError(
                "canonical production directory ancestor is unavailable"
            ) from exc
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid not in {0, required_uid}
        ):
            raise ProductionOperationError(
                "canonical production directory ancestor is unsafe"
            )


def _require_empty_secure_directory(path: Path, *, required_uid: int, label: str) -> None:
    _ensure_secure_directory(path, required_uid=required_uid)
    try:
        if next(path.iterdir(), None) is not None:
            raise ProductionOperationError(f"{label} must be fresh and empty")
    except OSError as exc:
        raise ProductionOperationError(f"{label} cannot be inspected") from exc


def _ensure_directory_tree(root: Path, relative_parent: PurePosixPath, *, required_uid: int) -> None:
    current = root
    for part in relative_parent.parts:
        current = current / part
        _ensure_secure_directory(current, required_uid=required_uid)


def _fsync_directory(path: Path) -> None:
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        os.fsync(descriptor)
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _materializing_path(destination: Path) -> Path:
    suffix = hashlib.sha256(os.fsencode(destination.name)).hexdigest()[:24]
    return destination.with_name(f"{_MATERIALIZING_PREFIX}{suffix}.tmp")


def _reconcile_materializing_file(
    temporary: Path,
    destination: Path,
    *,
    required_uid: int,
) -> None:
    if not temporary.exists() and not temporary.is_symlink():
        return
    try:
        temporary_metadata = temporary.stat(follow_symlinks=False)
    except OSError as exc:
        raise ProductionOperationError(
            "materialization temporary file is unsafe"
        ) from exc
    if (
        not stat.S_ISREG(temporary_metadata.st_mode)
        or temporary_metadata.st_uid != required_uid
        or stat.S_IMODE(temporary_metadata.st_mode) != 0o600
        or temporary_metadata.st_nlink not in {1, 2}
    ):
        raise ProductionOperationError("materialization temporary file is unsafe")
    if destination.exists() or destination.is_symlink():
        try:
            destination_metadata = destination.stat(follow_symlinks=False)
        except OSError as exc:
            raise ProductionOperationError(
                "materialized destination is unsafe"
            ) from exc
        if (
            stat.S_ISREG(destination_metadata.st_mode)
            and destination_metadata.st_dev == temporary_metadata.st_dev
            and destination_metadata.st_ino == temporary_metadata.st_ino
        ):
            temporary.unlink()
            _fsync_directory(destination.parent)
            return
    if temporary_metadata.st_nlink != 1:
        raise ProductionOperationError(
            "materialization temporary link identity is ambiguous"
        )
    temporary.unlink()
    _fsync_directory(destination.parent)


def _write_or_verify_file(
    destination: Path,
    source: BinaryIO,
    *,
    expected_sha256: str,
    expected_bytes: int,
    required_uid: int,
) -> str:
    temporary = _materializing_path(destination)
    _reconcile_materializing_file(
        temporary,
        destination,
        required_uid=required_uid,
    )
    if destination.exists() or destination.is_symlink():
        observed = _hash_regular_file(
            destination,
            expected_uid=required_uid,
            maximum=expected_bytes,
            minimum=0,
        )
        if observed != (expected_sha256, expected_bytes):
            raise ProductionOperationError("create-only destination already differs")
        return "already-present"
    descriptor = -1
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        digest = hashlib.sha256()
        size = 0
        while True:
            chunk = source.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > expected_bytes:
                raise ProductionOperationError("materialized file exceeded expected size")
            digest.update(chunk)
            view = memoryview(chunk)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise OSError("short write")
                view = view[written:]
        if size != expected_bytes or digest.hexdigest() != expected_sha256:
            raise ProductionOperationError("materialized file identity differs")
        os.fchmod(descriptor, 0o600)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        try:
            os.link(
                temporary,
                destination,
                src_dir_fd=None,
                dst_dir_fd=None,
                follow_symlinks=False,
            )
        except FileExistsError:
            observed = _hash_regular_file(
                destination,
                expected_uid=required_uid,
                maximum=expected_bytes,
                minimum=0,
            )
            if observed != (expected_sha256, expected_bytes):
                raise ProductionOperationError(
                    "concurrent create-only destination differs"
                )
        _fsync_directory(destination.parent)
        temporary.unlink()
        _fsync_directory(destination.parent)
        observed = _hash_regular_file(
            destination,
            expected_uid=required_uid,
            maximum=expected_bytes,
            minimum=0,
        )
        if observed != (expected_sha256, expected_bytes):
            raise ProductionOperationError("published materialized file differs")
    except Exception:
        if descriptor >= 0:
            os.close(descriptor)
            descriptor = -1
        _reconcile_materializing_file(
            temporary,
            destination,
            required_uid=required_uid,
        )
        raise
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    return "created"


def _extract_archive_create_only(
    archive_path: Path,
    destination_root: Path,
    *,
    mode: str,
    required_uid: int,
    expected_entries: Mapping[str, RuntimeEntry] | None = None,
) -> None:
    _ensure_secure_directory(destination_root, required_uid=required_uid)
    expected_files = (
        {
            entry.archive_path: (entry.sha256, entry.bytes)
            for entry in expected_entries.values()
        }
        if expected_entries is not None
        else None
    )
    try:
        with tarfile.open(archive_path, mode=mode) as archive:
            members = _validate_tar_members(archive, expected_files=expected_files)
            for member in members:
                source_relative = PurePosixPath(member.name.rstrip("/"))
                if expected_entries is None:
                    destination_relative = source_relative
                    expected_digest = None
                    expected_bytes = member.size
                elif member.isdir():
                    continue
                else:
                    entry = expected_entries[source_relative.as_posix()]
                    destination_relative = PurePosixPath(entry.destination)
                    expected_digest = entry.sha256
                    expected_bytes = entry.bytes
                _ensure_directory_tree(
                    destination_root,
                    destination_relative.parent,
                    required_uid=required_uid,
                )
                destination = destination_root.joinpath(*destination_relative.parts)
                if member.isdir():
                    _ensure_secure_directory(destination, required_uid=required_uid)
                    continue
                extracted = archive.extractfile(member)
                if extracted is None:
                    raise ProductionOperationError("archive regular member is unreadable")
                if expected_digest is None:
                    source = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024)
                    digest = hashlib.sha256()
                    size = 0
                    while True:
                        chunk = extracted.read(1024 * 1024)
                        if not chunk:
                            break
                        size += len(chunk)
                        if size > member.size:
                            raise ProductionOperationError(
                                "archive member content exceeded its declared size"
                            )
                        digest.update(chunk)
                        source.write(chunk)
                    if size != member.size:
                        raise ProductionOperationError(
                            "archive member content is truncated"
                        )
                    expected_digest = digest.hexdigest()
                    source.seek(0)
                else:
                    source = extracted
                try:
                    _write_or_verify_file(
                        destination,
                        source,
                        expected_sha256=expected_digest,
                        expected_bytes=expected_bytes,
                        required_uid=required_uid,
                    )
                finally:
                    source.close()
    except ProductionOperationError:
        raise
    except (OSError, EOFError, tarfile.TarError) as exc:
        raise ProductionOperationError("archive materialization failed") from exc


def _extract_runtime_archive_create_only(
    archive_path: Path,
    paths: CanonicalOperationPaths,
    *,
    required_uid: int,
    expected_entries: Mapping[str, RuntimeEntry],
) -> None:
    expected_files = {
        entry.archive_path: (entry.sha256, entry.bytes)
        for entry in expected_entries.values()
    }
    try:
        with tarfile.open(archive_path, mode="r:") as archive:
            members = _validate_tar_members(
                archive,
                expected_files=expected_files,
            )
            for member in members:
                if member.isdir():
                    continue
                archive_name = PurePosixPath(
                    member.name.rstrip("/")
                ).as_posix()
                entry = expected_entries[archive_name]
                destination = _runtime_destination_path(
                    paths,
                    entry.destination,
                )
                _ensure_secure_directory(
                    destination.parent,
                    required_uid=required_uid,
                )
                source = archive.extractfile(member)
                if source is None:
                    raise ProductionOperationError(
                        "runtime archive regular member is unreadable"
                    )
                try:
                    _write_or_verify_file(
                        destination,
                        source,
                        expected_sha256=entry.sha256,
                        expected_bytes=entry.bytes,
                        required_uid=required_uid,
                    )
                finally:
                    source.close()
    except ProductionOperationError:
        raise
    except (OSError, EOFError, KeyError, tarfile.TarError) as exc:
        raise ProductionOperationError(
            "runtime archive materialization failed"
        ) from exc


def _load_final_prepare_archive(
    archive_path: Path,
    *,
    manifest: OperationManifest,
    expected_stage_attestation_sha256: str,
    required_uid: int,
) -> FinalPrepareManifest:
    _hash_regular_file(
        archive_path,
        expected_uid=required_uid,
        maximum=MAX_PAYLOAD_BYTES,
    )
    try:
        with tarfile.open(archive_path, mode="r:") as archive:
            members = _validate_tar_members(archive)
            if any(member.isdir() for member in members):
                raise ProductionOperationError(
                    "final prepare archive must contain only regular files"
                )
            if any(
                member.uid != 0
                or member.gid != 0
                or member.mtime != 0
                or stat.S_IMODE(member.mode) != 0o600
                for member in members
            ):
                raise ProductionOperationError(
                    "final prepare archive member metadata is not canonical"
                )
            by_name = {
                PurePosixPath(member.name.rstrip("/")).as_posix(): member
                for member in members
            }
            manifest_member = by_name.get(FINAL_PREPARE_MANIFEST_NAME)
            if (
                manifest_member is None
                or not 1 <= manifest_member.size <= MAX_MANIFEST_BYTES
            ):
                raise ProductionOperationError(
                    "final prepare archive manifest is missing or oversized"
                )
            source = archive.extractfile(manifest_member)
            if source is None:
                raise ProductionOperationError(
                    "final prepare archive manifest is unreadable"
                )
            try:
                final_manifest = _load_final_prepare_manifest_bytes(
                    source.read(MAX_MANIFEST_BYTES + 1),
                    manifest=manifest,
                    expected_stage_attestation_sha256=(
                        expected_stage_attestation_sha256
                    ),
                )
            finally:
                source.close()
            expected = {
                FINAL_PREPARE_MANIFEST_NAME,
                *(entry.archive_path for entry in final_manifest.entries),
            }
            if set(by_name) != expected:
                raise ProductionOperationError(
                    "final prepare archive member set differs"
                )
            for entry in final_manifest.entries:
                member = by_name[entry.archive_path]
                if (
                    member.size != entry.bytes
                    or stat.S_IMODE(member.mode) != entry.mode
                ):
                    raise ProductionOperationError(
                        "final prepare archive member metadata differs"
                    )
                source = archive.extractfile(member)
                if source is None:
                    raise ProductionOperationError(
                        "final prepare archive member is unreadable"
                    )
                try:
                    digest = hashlib.sha256()
                    observed = 0
                    while observed <= entry.bytes:
                        chunk = source.read(
                            min(1024 * 1024, entry.bytes + 1 - observed)
                        )
                        if not chunk:
                            break
                        digest.update(chunk)
                        observed += len(chunk)
                finally:
                    source.close()
                if (
                    observed != entry.bytes
                    or digest.hexdigest() != entry.sha256
                ):
                    raise ProductionOperationError(
                        "final prepare archive member identity differs"
                    )
            return final_manifest
    except ProductionOperationError:
        raise
    except (OSError, EOFError, KeyError, tarfile.TarError) as exc:
        raise ProductionOperationError(
            "final prepare archive validation failed"
        ) from exc


def install_final_prepare_material(
    manifest: OperationManifest,
    archive_path: Path,
    *,
    operation_root: Path,
    expected_stage_attestation_sha256: str,
    expected_runtime_image_ids: Mapping[str, str],
    required_uid: int,
) -> Mapping[str, Any]:
    final_manifest = _load_final_prepare_archive(
        archive_path,
        manifest=manifest,
        expected_stage_attestation_sha256=(
            expected_stage_attestation_sha256
        ),
        required_uid=required_uid,
    )
    if dict(final_manifest.runtime_image_ids) != dict(
        expected_runtime_image_ids
    ):
        raise ProductionOperationError(
            "final prepare runtime image IDs differ from stage evidence"
        )
    canonical = _canonical_operation_paths(manifest)
    _ensure_canonical_operation_directories(
        canonical,
        required_uid=required_uid,
        postgres_runtime_identity=(
            manifest.postgres_runtime_uid,
            manifest.postgres_runtime_gid,
        ),
    )
    entries_by_archive = {
        entry.archive_path: entry for entry in final_manifest.entries
    }
    try:
        with tarfile.open(archive_path, mode="r:") as archive:
            by_name = {
                PurePosixPath(member.name.rstrip("/")).as_posix(): member
                for member in _validate_tar_members(archive)
                if member.isreg()
            }
            for archive_name, entry in entries_by_archive.items():
                source = archive.extractfile(by_name[archive_name])
                if source is None:
                    raise ProductionOperationError(
                        "final prepare archive member is unreadable"
                    )
                try:
                    _write_or_verify_file(
                        _runtime_destination_path(
                            canonical,
                            entry.destination,
                        ),
                        source,
                        expected_sha256=entry.sha256,
                        expected_bytes=entry.bytes,
                        required_uid=required_uid,
                    )
                finally:
                    source.close()
    except ProductionOperationError:
        raise
    except (OSError, EOFError, KeyError, tarfile.TarError) as exc:
        raise ProductionOperationError(
            "final prepare material installation failed"
        ) from exc

    env = parse_safe_dotenv(canonical.runtime_env.read_bytes())
    if set(env) != set(final_manifest.required_env_keys):
        raise ProductionOperationError(
            "runtime environment key set differs from final prepare closure"
        )
    _validate_role_local_environment_closure(canonical.compose, env)
    ca_sha256 = next(
        entry.sha256
        for entry in final_manifest.entries
        if entry.destination == ROLE_CA_RELATIVE_PATH.as_posix()
    )
    expected_values = _runtime_expected_values(
        manifest,
        runtime_image_ids=final_manifest.runtime_image_ids,
        ca_sha256=ca_sha256,
    )
    if any(env.get(key) != value for key, value in expected_values.items()):
        raise ProductionOperationError(
            "runtime environment final operation binding differs"
        )
    _validate_runtime_image_set(
        manifest,
        final_manifest.runtime_image_ids,
    )
    return {
        "final_prepare_manifest_sha256": final_manifest.canonical_sha256,
        "stage_attestation_sha256": (
            final_manifest.stage_attestation_sha256
        ),
        "role_compose_sha256": next(
            entry.sha256
            for entry in final_manifest.entries
            if entry.destination == ROLE_COMPOSE_RELATIVE_PATH.as_posix()
        ),
        "role_env_sha256": next(
            entry.sha256
            for entry in final_manifest.entries
            if entry.destination == ROLE_ENV_RELATIVE_PATH.as_posix()
        ),
        "ca_sha256": ca_sha256,
        "runtime_image_ids": dict(final_manifest.runtime_image_ids),
        "runtime_env": str(canonical.runtime_env),
        "compose": str(canonical.compose),
    }


def _attest_extracted_archive_tree(
    archive_path: Path,
    destination_root: Path,
    *,
    mode: str,
    required_uid: int,
) -> Mapping[str, Any]:
    expected_directories: set[str] = set()
    expected_files: dict[str, tuple[int, str]] = {}
    try:
        with tarfile.open(archive_path, mode=mode) as archive:
            members = _validate_tar_members(archive)
            for member in members:
                relative = PurePosixPath(member.name.rstrip("/"))
                for parent in relative.parents:
                    if parent != PurePosixPath("."):
                        expected_directories.add(parent.as_posix())
                if member.isdir():
                    expected_directories.add(relative.as_posix())
                    continue
                source = archive.extractfile(member)
                if source is None:
                    raise ProductionOperationError(
                        "archive regular member is unreadable"
                    )
                digest = hashlib.sha256()
                size = 0
                while True:
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > member.size:
                        raise ProductionOperationError(
                            "archive member content exceeded its declared size"
                        )
                    digest.update(chunk)
                if size != member.size:
                    raise ProductionOperationError(
                        "archive member content is truncated"
                    )
                expected_files[relative.as_posix()] = (size, digest.hexdigest())
    except ProductionOperationError:
        raise
    except (OSError, EOFError, tarfile.TarError) as exc:
        raise ProductionOperationError(
            "archive tree attestation could not read its source"
        ) from exc

    _ensure_secure_directory(destination_root, required_uid=required_uid)
    observed_directories: set[str] = set()
    observed_files: set[str] = set()
    try:
        for current, directories, files in os.walk(
            destination_root,
            topdown=True,
            followlinks=False,
        ):
            current_path = Path(current)
            for name in directories:
                path = current_path / name
                relative = path.relative_to(destination_root).as_posix()
                metadata = path.stat(follow_symlinks=False)
                if (
                    not stat.S_ISDIR(metadata.st_mode)
                    or metadata.st_uid != required_uid
                    or stat.S_IMODE(metadata.st_mode) != 0o700
                ):
                    raise ProductionOperationError(
                        "materialized archive directory identity differs"
                    )
                observed_directories.add(relative)
            for name in files:
                path = current_path / name
                relative = path.relative_to(destination_root).as_posix()
                expected = expected_files.get(relative)
                if expected is None:
                    raise ProductionOperationError(
                        "materialized archive contains an unexpected file"
                    )
                observed = _hash_regular_file(
                    path,
                    expected_uid=required_uid,
                    minimum=0,
                    maximum=expected[0],
                )
                if observed != (expected[1], expected[0]):
                    raise ProductionOperationError(
                        "materialized archive file identity differs"
                    )
                observed_files.add(relative)
    except ProductionOperationError:
        raise
    except OSError as exc:
        raise ProductionOperationError(
            "materialized archive tree cannot be inspected"
        ) from exc
    if (
        observed_directories != expected_directories
        or observed_files != set(expected_files)
    ):
        raise ProductionOperationError(
            "materialized archive tree closure differs"
        )
    inventory = {
        "directories": sorted(expected_directories),
        "files": [
            [name, expected_files[name][0], expected_files[name][1]]
            for name in sorted(expected_files)
        ],
    }
    return {
        "tree_sha256": hashlib.sha256(_canonical_json(inventory)).hexdigest(),
        "directory_count": len(expected_directories),
        "file_count": len(expected_files),
        "expanded_bytes": sum(size for size, _digest in expected_files.values()),
    }


def _copy_create_only(
    source_path: Path,
    destination: Path,
    *,
    artifact: Artifact,
    required_uid: int,
) -> None:
    _ensure_directory_tree(
        destination.parent.parent,
        PurePosixPath(destination.parent.name),
        required_uid=required_uid,
    )
    descriptor = os.open(
        source_path,
        os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        with os.fdopen(descriptor, "rb", closefd=False) as source:
            _write_or_verify_file(
                destination,
                source,
                expected_sha256=artifact.sha256,
                expected_bytes=artifact.bytes,
                required_uid=required_uid,
            )
    finally:
        os.close(descriptor)


def _validate_image_labels(
    role: str,
    labels: Any,
    *,
    release_sha: str,
    postgres_runtime_uid: int | None,
    postgres_runtime_gid: int | None,
) -> None:
    if role in {"app", "postgres"} and (
        not isinstance(labels, dict)
        or labels.get("org.opencontainers.image.revision") != release_sha
    ):
        raise ProductionOperationError(
            f"{role} image lacks the exact OCI release revision"
        )
    if role == "postgres" and postgres_runtime_uid is not None and (
        not isinstance(labels, dict)
        or labels.get(POSTGRES_RUNTIME_UID_LABEL)
        != str(postgres_runtime_uid)
        or labels.get(POSTGRES_RUNTIME_GID_LABEL)
        != str(postgres_runtime_gid)
    ):
        raise ProductionOperationError(
            "PostgreSQL image runtime ownership labels differ"
        )


def _docker_archive_identity(
    path: Path,
    image: ImageArtifact | Image,
    *,
    release_sha: str,
    postgres_runtime_uid: int | None = None,
    postgres_runtime_gid: int | None = None,
) -> Mapping[str, Any]:
    legacy = isinstance(image, Image)
    if legacy:
        expected_config_digest = image.image_id
        expected_tags = list(image.repo_tags)
        expected_content_descriptor = None
        expected_content_identity = None
        if postgres_runtime_uid is None:
            postgres_runtime_uid = image.runtime_uid
        if postgres_runtime_gid is None:
            postgres_runtime_gid = image.runtime_gid
    else:
        expected_config_digest = image.config_digest
        expected_tags = []
        expected_content_descriptor = image.content_descriptor
        expected_content_identity = image.content_identity
        if _hash_regular_file(
            path,
            expected_uid=os.geteuid(),
            maximum=image.archive_bytes,
        ) != (image.archive_sha256, image.archive_bytes):
            raise ProductionOperationError(
                "Docker archive hash or size differs from its image binding"
            )
    try:
        with tarfile.open(path, mode="r:") as archive:
            members = _validate_tar_members(archive)
            by_name = {member.name.rstrip("/"): member for member in members if member.isreg()}
            manifest_member = by_name.get("manifest.json")
            if manifest_member is None or manifest_member.size > 1024 * 1024:
                raise ProductionOperationError("Docker archive manifest is missing or oversized")
            source = archive.extractfile(manifest_member)
            if source is None:
                raise ProductionOperationError("Docker archive manifest is unreadable")
            try:
                raw_manifest = source.read(1024 * 1024 + 1)
            finally:
                source.close()
            manifest = json.loads(
                raw_manifest.decode("utf-8"),
                object_pairs_hook=_strict_json_object,
            )
            if not isinstance(manifest, list) or len(manifest) != 1:
                raise ProductionOperationError("Docker archive must contain exactly one image")
            entry = manifest[0]
            if (
                not isinstance(entry, dict)
                or set(entry) - {"Config", "RepoTags", "Layers", "LayerSources"}
                or not isinstance(entry.get("Config"), str)
                or not isinstance(entry.get("Layers"), list)
                or not entry["Layers"]
                or any(not isinstance(layer, str) for layer in entry["Layers"])
            ):
                raise ProductionOperationError("Docker archive manifest entry is invalid")
            config_name = _safe_relative_path(entry["Config"], label="Docker config path")
            config_member = by_name.get(config_name)
            if config_member is None or not 1 <= config_member.size <= 16 * 1024 * 1024:
                raise ProductionOperationError("Docker image config is missing or oversized")
            config_source = archive.extractfile(config_member)
            if config_source is None:
                raise ProductionOperationError("Docker image config is unreadable")
            try:
                config_payload = config_source.read(16 * 1024 * 1024 + 1)
            finally:
                config_source.close()
            observed_config_digest = (
                "sha256:" + hashlib.sha256(config_payload).hexdigest()
            )
            if (
                observed_config_digest != expected_config_digest
                or config_name
                != f"{expected_config_digest.removeprefix('sha256:')}.json"
            ):
                raise ProductionOperationError(
                    "Docker image config digest differs from its archive binding"
                )
            try:
                config_document = json.loads(
                    config_payload.decode("utf-8"),
                    object_pairs_hook=_strict_json_object,
                )
            except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
                raise ProductionOperationError("Docker image config is invalid JSON") from exc
            config_values = (
                config_document.get("config")
                if isinstance(config_document, dict)
                else None
            )
            labels = (
                config_values.get("Labels")
                if isinstance(config_values, dict)
                else None
            )
            _validate_image_labels(
                image.role,
                labels,
                release_sha=release_sha,
                postgres_runtime_uid=postgres_runtime_uid,
                postgres_runtime_gid=postgres_runtime_gid,
            )
            tags = entry.get("RepoTags")
            tags = [] if tags is None else tags
            if tags != expected_tags:
                raise ProductionOperationError("Docker archive tag set differs")
            for layer in entry["Layers"]:
                layer_name = _safe_relative_path(layer, label="Docker layer path")
                if layer_name not in by_name:
                    raise ProductionOperationError("Docker archive layer is missing")
            try:
                descriptor, content_identity = (
                    image_content_descriptor_from_archive_config(
                        config_document
                    )
                )
            except DockerImageIdentityError as exc:
                raise ProductionOperationError(
                    "Docker archive semantic content identity is invalid"
                ) from exc
            if (
                legacy
                and (
                    descriptor["architecture"] != image.architecture
                    or descriptor["os"] != image.os
                )
            ):
                raise ProductionOperationError(
                    "Docker archive platform differs from its image binding"
                )
            if (
                expected_content_descriptor is not None
                and (
                    descriptor != expected_content_descriptor
                    or content_identity != expected_content_identity
                )
            ):
                raise ProductionOperationError(
                    "Docker archive semantic content identity differs"
                )
            return descriptor
    except ProductionOperationError:
        raise
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError, tarfile.TarError) as exc:
        raise ProductionOperationError("Docker archive validation failed") from exc


def _runtime_expected_values(
    manifest: OperationManifest,
    *,
    runtime_image_ids: Mapping[str, str],
    ca_sha256: str,
) -> Mapping[str, str]:
    project_base = _project_base(manifest.operation_id)
    paths = _canonical_operation_paths(manifest)
    return {
        "PRODUCTION_SHADOW_OPERATION_ID": manifest.operation_id,
        "PRODUCTION_SHADOW_PROJECT": project_base,
        "PRODUCTION_SHADOW_CGROUP_PARENT": project_base,
        "PRODUCTION_SHADOW_PROJECT_ROOT": str(paths.project_root),
        "PRODUCTION_SHADOW_RELEASE_ROOT": str(paths.release_root),
        "PRODUCTION_SHADOW_DATA_ROOT": str(paths.data_root),
        "PRODUCTION_SHADOW_SECRET_ROOT": str(paths.secret_root),
        "PRODUCTION_SHADOW_RELEASE_SHA": manifest.release_sha,
        "PRODUCTION_SHADOW_APP_IMAGE_ID": runtime_image_ids["app"],
        "PRODUCTION_SHADOW_POSTGRES_IMAGE_ID": runtime_image_ids["postgres"],
        "PRODUCTION_SHADOW_REDIS_IMAGE_ID": runtime_image_ids["redis"],
        "PRODUCTION_SHADOW_NGINX_IMAGE_ID": runtime_image_ids["nginx"],
        "PRODUCTION_SHADOW_DR_CA_SHA256": ca_sha256,
    }


def _runtime_image_ids_from_env_values(
    values: Mapping[str, str],
) -> Mapping[str, str]:
    result = {
        "app": values.get("PRODUCTION_SHADOW_APP_IMAGE_ID", ""),
        "postgres": values.get("PRODUCTION_SHADOW_POSTGRES_IMAGE_ID", ""),
        "redis": values.get("PRODUCTION_SHADOW_REDIS_IMAGE_ID", ""),
        "nginx": values.get("PRODUCTION_SHADOW_NGINX_IMAGE_ID", ""),
    }
    if (
        any(not _IMAGE_ID_RE.fullmatch(value) for value in result.values())
        or len(set(result.values())) != len(IMAGE_ROLES)
    ):
        raise ProductionOperationError(
            "runtime environment image IDs are invalid or ambiguous"
        )
    return result


def _installed_runtime_image_ids(
    manifest: OperationManifest,
) -> Mapping[str, str]:
    runtime_env = _canonical_operation_paths(manifest).runtime_env
    try:
        payload = runtime_env.read_bytes()
    except OSError as exc:
        raise ProductionOperationError(
            "final prepare runtime environment is unavailable"
        ) from exc
    return _runtime_image_ids_from_env_values(parse_safe_dotenv(payload))


def _validate_role_local_environment_closure(
    compose_path: Path,
    env: Mapping[str, str],
) -> None:
    try:
        compose_payload = compose_path.read_bytes()
        compose_text = compose_payload.decode("ascii")
    except (OSError, UnicodeDecodeError) as exc:
        raise ProductionOperationError(
            "rendered role-local Compose is unavailable or non-ASCII"
        ) from exc
    if (
        not 1 <= len(compose_payload) <= 2 * 1024 * 1024
        or "\x00" in compose_text
        or "bot_fi_" in compose_text
        or "webapp_fi_" in compose_text
    ):
        raise ProductionOperationError(
            "rendered role-local Compose contains foreign role material"
        )
    referenced = set(_COMPOSE_ENV_REFERENCE_RE.findall(compose_text))
    required = set(_COMPOSE_REQUIRED_ENV_REFERENCE_RE.findall(compose_text))
    if required - set(env) or set(env) - referenced:
        raise ProductionOperationError(
            "runtime environment is not the exact rendered Compose closure"
        )
    forbidden = {
        key
        for key in env
        if key.startswith(("BOT_FI_", "WEBAPP_FI_", "PRODUCTION_SHADOW_WITNESS_"))
        or key in _FORBIDDEN_PREPARE_ENV_NAMES
        or key.startswith("WRITER_WITNESS_")
    }
    if forbidden:
        raise ProductionOperationError(
            "runtime environment contains private-plane, cross-role, or provider material"
        )


def _ensure_canonical_operation_directories(
    paths: CanonicalOperationPaths,
    *,
    required_uid: int,
    postgres_runtime_identity: tuple[int, int] | None = None,
) -> None:
    for prefix in (
        PROJECT_ROOT_PREFIX,
        DATA_ROOT_PREFIX,
        SECRET_ROOT_PREFIX,
    ):
        if not prefix.is_absolute() or ".." in prefix.parts:
            raise ProductionOperationError(
                "canonical production root is not absolute and normalized"
            )
        _require_real_owned_directory_chain(
            prefix.parent,
            required_uid=required_uid,
        )
        _ensure_secure_directory(prefix, required_uid=required_uid)
        _require_real_owned_directory_chain(
            prefix,
            required_uid=required_uid,
        )
    for root in (
        paths.project_root,
        paths.data_root,
        paths.secret_root,
    ):
        _ensure_secure_directory(root, required_uid=required_uid)
    for root, relative in (
        (paths.project_root, PurePosixPath("releases")),
        (
            paths.project_root,
            PurePosixPath("rendered/webapp-ir"),
        ),
        (
            paths.data_root,
            PurePosixPath("restore-input/webapp-ir"),
        ),
        (paths.data_root, PurePosixPath("webapp-ir/redis")),
        (paths.data_root, PurePosixPath("webapp-ir/uploads")),
        (paths.data_root, PurePosixPath("webapp-ir/audit")),
        (paths.secret_root, PurePosixPath("webapp-ir")),
        (paths.secret_root, PurePosixPath("tls")),
    ):
        _ensure_directory_tree(
            root,
            relative,
            required_uid=required_uid,
        )
    if postgres_runtime_identity is None:
        _ensure_directory_tree(
            paths.data_root,
            PurePosixPath("webapp-ir/postgres"),
            required_uid=required_uid,
        )
        return

    _require_real_owned_directory_chain(
        paths.postgres.parent,
        required_uid=required_uid,
    )
    if not paths.postgres.exists() and not paths.postgres.is_symlink():
        _ensure_secure_directory(paths.postgres, required_uid=required_uid)
    try:
        metadata = paths.postgres.stat(follow_symlinks=False)
    except OSError as exc:
        raise ProductionOperationError(
            "operation PostgreSQL directory is unsafe"
        ) from exc
    allowed_owners = {
        (required_uid, os.getegid()),
        postgres_runtime_identity,
    }
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or (metadata.st_uid, metadata.st_gid) not in allowed_owners
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise ProductionOperationError(
            "operation PostgreSQL directory is unsafe"
        )


def materialize_stage(
    manifest: OperationManifest,
    paths: Mapping[str, Path],
    *,
    operation_root: Path,
    required_uid: int,
) -> Mapping[str, Any]:
    canonical = _canonical_operation_paths(manifest)
    _ensure_canonical_operation_directories(
        canonical,
        required_uid=required_uid,
    )
    _require_empty_secure_directory(
        canonical.redis,
        required_uid=required_uid,
        label="WA-IR Redis directory",
    )

    _materialize_release_bundle(
        paths["release-bundle"],
        canonical.release_root,
        manifest=manifest,
        required_uid=required_uid,
    )
    _copy_create_only(
        paths["database-backup"],
        canonical.restore_dump,
        artifact=manifest.artifacts["database-backup"],
        required_uid=required_uid,
    )
    archive_trees: dict[str, Mapping[str, Any]] = {}
    for kind, target in (
        ("uploads-archive", canonical.uploads),
        ("audit-archive", canonical.audit),
    ):
        _extract_archive_create_only(
            paths[kind],
            target,
            mode="r:gz",
            required_uid=required_uid,
        )
        if _hash_regular_file(
            paths[kind],
            expected_uid=required_uid,
            maximum=MAX_PAYLOAD_BYTES,
        ) != (
            manifest.artifacts[kind].sha256,
            manifest.artifacts[kind].bytes,
        ):
            raise ProductionOperationError(
                f"{kind} changed during create-only extraction"
            )
        archive_trees[kind] = _attest_extracted_archive_tree(
            paths[kind],
            target,
            mode="r:gz",
            required_uid=required_uid,
        )
    for image in manifest.image_artifacts.values():
        _docker_archive_identity(
            paths[image.artifact_kind],
            image,
            release_sha=manifest.release_sha,
            postgres_runtime_uid=manifest.postgres_runtime_uid,
            postgres_runtime_gid=manifest.postgres_runtime_gid,
        )
    return {
        "release_root": str(canonical.release_root),
        "secrets_root": str(canonical.secret_root),
        "data_root": str(canonical.data_root),
        "runtime_material_installed": False,
        "uploads_tree": dict(archive_trees["uploads-archive"]),
        "audit_tree": dict(archive_trees["audit-archive"]),
    }


def _run(
    arguments: list[str],
    *,
    timeout: int,
    stdin: BinaryIO | int | None = subprocess.DEVNULL,
    env: Mapping[str, str] = _SAFE_ENV,
) -> str:
    try:
        result = subprocess.run(
            arguments,
            stdin=stdin,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=timeout,
            env=dict(env),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ProductionOperationError(
            f"required command is unavailable: {Path(arguments[0]).name}"
        ) from exc
    if len(result.stdout) > MAX_COMMAND_OUTPUT_BYTES or len(result.stderr) > MAX_COMMAND_OUTPUT_BYTES:
        raise ProductionOperationError("required command output exceeded its bound")
    if result.returncode != 0:
        raise ProductionOperationError(
            f"required command failed closed: {Path(arguments[0]).name}"
        )
    try:
        return result.stdout.decode("utf-8").strip()
    except UnicodeDecodeError as exc:
        raise ProductionOperationError("required command returned non-UTF-8 output") from exc


def _run_streaming_sha256(
    arguments: list[str],
    *,
    timeout: int,
    stdin: BinaryIO | int | None = subprocess.DEVNULL,
    env: Mapping[str, str] = _SAFE_ENV,
) -> StreamDigest:
    process: subprocess.Popen[bytes] | None = None
    selector: selectors.BaseSelector | None = None
    try:
        process = subprocess.Popen(
            arguments,
            stdin=stdin,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=dict(env),
        )
        if process.stdout is None or process.stderr is None:
            raise ProductionOperationError(
                "streaming command pipes are unavailable"
            )
        selector = selectors.DefaultSelector()
        for stream, label in (
            (process.stdout, "stdout"),
            (process.stderr, "stderr"),
        ):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, label)
        digest = hashlib.sha256()
        stdout_bytes = 0
        record_count = 0
        last_byte: int | None = None
        stderr_bytes = 0
        stderr_oversized = False
        deadline = time.monotonic() + timeout
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(arguments, timeout)
            events = selector.select(min(1.0, remaining))
            if not events:
                continue
            for key, _mask in events:
                try:
                    chunk = os.read(key.fd, 1024 * 1024)
                except BlockingIOError:
                    continue
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                if key.data == "stdout":
                    stdout_bytes += len(chunk)
                    digest.update(chunk)
                    record_count += chunk.count(b"\n")
                    last_byte = chunk[-1]
                else:
                    stderr_bytes += len(chunk)
                    if stderr_bytes > MAX_COMMAND_OUTPUT_BYTES:
                        stderr_oversized = True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(arguments, timeout)
        return_code = process.wait(timeout=remaining)
        if stderr_oversized:
            raise ProductionOperationError(
                "streaming command stderr exceeded its bound"
            )
        if return_code != 0:
            raise ProductionOperationError(
                f"required streaming command failed closed: "
                f"{Path(arguments[0]).name}"
            )
        if stdout_bytes and last_byte != ord("\n"):
            raise ProductionOperationError(
                "streaming command returned a truncated record"
            )
        return StreamDigest(
            sha256=digest.hexdigest(),
            bytes=stdout_bytes,
            records=record_count,
        )
    except ProductionOperationError:
        raise
    except (OSError, subprocess.SubprocessError) as exc:
        raise ProductionOperationError(
            f"required streaming command is unavailable: "
            f"{Path(arguments[0]).name}"
        ) from exc
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            try:
                process.wait(timeout=30)
            except subprocess.SubprocessError:
                pass
        if selector is not None:
            selector.close()
        if process is not None:
            if process.stdout is not None:
                process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()


def _rename_directory_noreplace(source: Path, destination: Path) -> None:
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        renameat2 = libc.renameat2
    except (AttributeError, OSError) as exc:
        raise ProductionOperationError(
            "atomic no-replace directory publication is unavailable"
        ) from exc
    renameat2.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    renameat2.restype = ctypes.c_int
    result = renameat2(
        -100,
        os.fsencode(source),
        -100,
        os.fsencode(destination),
        1,
    )
    if result == 0:
        return
    error = ctypes.get_errno()
    if error == errno.EEXIST:
        raise ProductionOperationError(
            "release destination appeared during no-replace publication"
        )
    raise ProductionOperationError(
        "release directory could not be published atomically"
    ) from OSError(error, os.strerror(error))


def _remove_release_temporary(path: Path, *, required_uid: int) -> None:
    if not path.exists() and not path.is_symlink():
        return
    try:
        metadata = path.stat(follow_symlinks=False)
    except OSError as exc:
        raise ProductionOperationError(
            "release materialization temporary is unsafe"
        ) from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != required_uid
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise ProductionOperationError("release materialization temporary is unsafe")
    try:
        shutil.rmtree(path)
        _fsync_directory(path.parent)
    except OSError as exc:
        raise ProductionOperationError(
            "release materialization temporary could not be reconciled"
        ) from exc


def _fsync_release_tree(root: Path, *, required_uid: int) -> None:
    for current, directories, files in os.walk(root, topdown=False, followlinks=False):
        current_path = Path(current)
        for name in files:
            path = current_path / name
            metadata = path.stat(follow_symlinks=False)
            if metadata.st_uid != required_uid:
                raise ProductionOperationError("staged release ownership differs")
            if stat.S_ISLNK(metadata.st_mode):
                continue
            if not stat.S_ISREG(metadata.st_mode):
                raise ProductionOperationError(
                    "staged release contains a special file"
                )
            descriptor = os.open(
                path,
                os.O_RDONLY
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
            )
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        for name in directories:
            path = current_path / name
            metadata = path.stat(follow_symlinks=False)
            if stat.S_ISLNK(metadata.st_mode):
                continue
            if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != required_uid:
                raise ProductionOperationError(
                    "staged release directory identity differs"
                )
            _fsync_directory(path)
        _fsync_directory(current_path)


def _verify_materialized_release(
    release_root: Path,
    *,
    manifest: OperationManifest,
) -> None:
    try:
        metadata = release_root.stat(follow_symlinks=False)
        git_metadata = (release_root / ".git").stat(follow_symlinks=False)
    except OSError as exc:
        raise ProductionOperationError("staged release is unavailable") from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or not stat.S_ISDIR(git_metadata.st_mode)
    ):
        raise ProductionOperationError("staged release layout is unsafe")
    head = _run(
        [GIT, "-C", str(release_root), "rev-parse", "HEAD"],
        timeout=30,
        env=_SAFE_GIT_ENV,
    )
    tree = _run(
        [GIT, "-C", str(release_root), "rev-parse", "HEAD^{tree}"],
        timeout=30,
        env=_SAFE_GIT_ENV,
    )
    branch = _run(
        [GIT, "-C", str(release_root), "rev-parse", "--abbrev-ref", "HEAD"],
        timeout=30,
        env=_SAFE_GIT_ENV,
    )
    status = _run(
        [
            GIT,
            "-C",
            str(release_root),
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
        ],
        timeout=30,
        env=_SAFE_GIT_ENV,
    )
    remotes = _run(
        [GIT, "-C", str(release_root), "remote"],
        timeout=30,
        env=_SAFE_GIT_ENV,
    )
    if (
        head != manifest.release_sha
        or tree != manifest.release_tree_sha
        or branch != "HEAD"
        or status
        or remotes
    ):
        raise ProductionOperationError(
            "staged release is not exact, detached, clean, and remote-free"
        )


def _materialize_release_bundle(
    bundle: Path,
    release_root: Path,
    *,
    manifest: OperationManifest,
    required_uid: int,
) -> None:
    if release_root.exists() or release_root.is_symlink():
        try:
            metadata = release_root.stat(follow_symlinks=False)
        except OSError as exc:
            raise ProductionOperationError("release destination is unsafe") from exc
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != required_uid
            or stat.S_IMODE(metadata.st_mode) != 0o700
        ):
            raise ProductionOperationError("release destination is unsafe")
        if (release_root / ".git").exists():
            _verify_materialized_release(release_root, manifest=manifest)
            return
        if any(release_root.iterdir()):
            raise ProductionOperationError(
                "release destination is nonempty before Git bundle checkout"
            )
        release_root.rmdir()
        _fsync_directory(release_root.parent)

    temporary = release_root.with_name(f".{release_root.name}.materializing")
    _remove_release_temporary(temporary, required_uid=required_uid)
    try:
        temporary.mkdir(mode=0o700)
        _fsync_directory(temporary.parent)
        _run(
            [
                GIT,
                "-c",
                "core.hooksPath=/dev/null",
                "clone",
                "--no-checkout",
                "--no-hardlinks",
                str(bundle),
                str(temporary),
            ],
            timeout=300,
            env=_SAFE_GIT_ENV,
        )
        _run(
            [GIT, "-C", str(temporary), "remote", "remove", "origin"],
            timeout=30,
            env=_SAFE_GIT_ENV,
        )
        _run(
            [
                GIT,
                "-C",
                str(temporary),
                "-c",
                "core.hooksPath=/dev/null",
                "checkout",
                "--detach",
                manifest.release_sha,
            ],
            timeout=300,
            env=_SAFE_GIT_ENV,
        )
        _run(
            [
                GIT,
                "-C",
                str(temporary),
                "config",
                "--local",
                "core.hooksPath",
                "/dev/null",
            ],
            timeout=30,
            env=_SAFE_GIT_ENV,
        )
        temporary.chmod(0o700)
        _verify_materialized_release(temporary, manifest=manifest)
        _fsync_release_tree(temporary, required_uid=required_uid)
        _rename_directory_noreplace(temporary, release_root)
        _fsync_directory(release_root.parent)
        _verify_materialized_release(release_root, manifest=manifest)
    except Exception:
        _remove_release_temporary(temporary, required_uid=required_uid)
        raise


def _migration_assignment(module: ast.Module, name: str) -> Any:
    values: list[Any] = []
    for statement in module.body:
        target: ast.expr | None = None
        value: ast.expr | None = None
        if isinstance(statement, ast.Assign) and len(statement.targets) == 1:
            target = statement.targets[0]
            value = statement.value
        elif isinstance(statement, ast.AnnAssign):
            target = statement.target
            value = statement.value
        if isinstance(target, ast.Name) and target.id == name and value is not None:
            try:
                values.append(ast.literal_eval(value))
            except (TypeError, ValueError) as exc:
                raise ProductionOperationError(
                    f"migration {name} assignment is not literal"
                ) from exc
    if len(values) != 1:
        raise ProductionOperationError(
            f"migration must define exactly one literal {name}"
        )
    return values[0]


def _load_migration_graph(release_root: Path) -> MigrationGraph:
    versions = release_root / "migrations" / "versions"
    try:
        versions_metadata = versions.lstat()
        candidates = sorted(versions.iterdir())
    except OSError as exc:
        raise ProductionOperationError(
            "staged release migration directory is unavailable"
        ) from exc
    if (
        not stat.S_ISDIR(versions_metadata.st_mode)
        or versions_metadata.st_uid != os.geteuid()
        or versions.is_symlink()
        or not candidates
        or len(candidates) > 10_000
    ):
        raise ProductionOperationError(
            "staged release migration directory is unsafe"
        )
    parents: dict[str, tuple[str, ...]] = {}
    sources: dict[str, Path] = {}
    for path in candidates:
        try:
            metadata = path.lstat()
            if path.suffix != ".py" or path.name == "__init__.py":
                if (
                    not stat.S_ISREG(metadata.st_mode)
                    or metadata.st_uid != os.geteuid()
                    or metadata.st_nlink != 1
                    or stat.S_IMODE(metadata.st_mode) & 0o022
                ):
                    raise ProductionOperationError(
                        "staged release migration directory contains an unsafe entry"
                    )
                continue
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.geteuid()
                or metadata.st_nlink != 1
                or not 1 <= metadata.st_size <= 1024 * 1024
            ):
                raise ProductionOperationError(
                    "staged release migration source is unsafe"
                )
            payload = path.read_bytes()
            module = ast.parse(payload, filename=path.name)
        except ProductionOperationError:
            raise
        except (OSError, SyntaxError, ValueError) as exc:
            raise ProductionOperationError(
                "staged release migration source is invalid"
            ) from exc
        revision = _migration_assignment(module, "revision")
        down_revision = _migration_assignment(module, "down_revision")
        if (
            not isinstance(revision, str)
            or not _ALEMBIC_REVISION_RE.fullmatch(revision)
        ):
            raise ProductionOperationError("migration revision is invalid")
        if down_revision is None:
            revision_parents: tuple[str, ...] = ()
        elif (
            isinstance(down_revision, str)
            and _ALEMBIC_REVISION_RE.fullmatch(down_revision)
        ):
            revision_parents = (down_revision,)
        elif (
            isinstance(down_revision, (tuple, list))
            and down_revision
            and all(
                isinstance(value, str) and _ALEMBIC_REVISION_RE.fullmatch(value)
                for value in down_revision
            )
        ):
            revision_parents = tuple(down_revision)
        else:
            raise ProductionOperationError("migration ancestry is invalid")
        if revision in parents or len(set(revision_parents)) != len(revision_parents):
            raise ProductionOperationError("migration revision graph is duplicate")
        parents[revision] = revision_parents
        sources[revision] = path
    unknown = {
        parent
        for values in parents.values()
        for parent in values
        if parent not in parents
    }
    if unknown:
        raise ProductionOperationError(
            "migration graph references an unavailable parent"
        )
    return MigrationGraph(parents=parents, sources=sources)


def _migration_ancestors(
    revision: str,
    graph: MigrationGraph,
) -> set[str]:
    if revision not in graph.parents:
        raise ProductionOperationError(
            "bound migration revision is absent from the staged release"
        )
    observed: set[str] = set()
    active: set[str] = set()

    def visit(value: str) -> None:
        if value in active:
            raise ProductionOperationError("migration graph contains a cycle")
        if value in observed:
            return
        active.add(value)
        for parent in graph.parents[value]:
            visit(parent)
        active.remove(value)
        observed.add(value)

    visit(revision)
    return observed


def _migration_corridor(
    graph: MigrationGraph,
    *,
    source_revision: str,
    target_revision: str,
) -> tuple[str, ...]:
    target_ancestors = _migration_ancestors(target_revision, graph)
    if source_revision not in target_ancestors:
        raise ProductionOperationError(
            "source revision is not an ancestor of the exact target"
        )
    children = {
        parent
        for values in graph.parents.values()
        for parent in values
    }
    heads = set(graph.parents) - children
    if heads != {target_revision}:
        raise ProductionOperationError(
            "manifest target is not the unique staged-release migration head"
        )
    corridor = {
        revision
        for revision in target_ancestors
        if source_revision in _migration_ancestors(revision, graph)
    }
    return tuple(sorted(corridor))


def _concurrent_index_names(
    graph: MigrationGraph,
    corridor: tuple[str, ...],
) -> tuple[str, ...]:
    pattern = re.compile(
        r"\bCREATE\s+(?:UNIQUE\s+)?INDEX\s+CONCURRENTLY\s+"
        r"IF\s+NOT\s+EXISTS\s+\"?([a-z_][a-z0-9_]{0,62})\"?",
        re.IGNORECASE,
    )
    names: set[str] = set()
    for revision in corridor:
        path = graph.sources[revision]
        try:
            module = ast.parse(path.read_bytes(), filename=path.name)
        except (OSError, SyntaxError, ValueError) as exc:
            raise ProductionOperationError(
                "migration source changed during index inspection"
            ) from exc
        for node in ast.walk(module):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                names.update(pattern.findall(node.value))
    return tuple(sorted(names))


def _compose_base(manifest: OperationManifest, *, operation_root: Path) -> list[str]:
    canonical = _canonical_operation_paths(manifest)
    return [
        DOCKER,
        "compose",
        "--project-name",
        manifest.project_name,
        "--env-file",
        str(canonical.runtime_env),
        "--file",
        str(canonical.compose),
    ]


def _project_container_ids(manifest: OperationManifest) -> list[str]:
    output = _run(
        [
            DOCKER,
            "ps",
            "--all",
            "--quiet",
            "--no-trunc",
            "--filter",
            f"label=com.docker.compose.project={manifest.project_name}",
        ],
        timeout=30,
    )
    identifiers = [value for value in output.splitlines() if value]
    if (
        len(identifiers) != len(set(identifiers))
        or any(
            not re.fullmatch(r"[0-9a-f]{64}", value)
            for value in identifiers
        )
    ):
        raise ProductionOperationError(
            "operation project container inventory is invalid"
        )
    return identifiers


def _container_compose_labels(
    identifier: str,
    manifest: OperationManifest,
) -> Mapping[str, str]:
    raw = _run([DOCKER, "inspect", identifier], timeout=30)
    try:
        documents = json.loads(raw, object_pairs_hook=_strict_json_object)
    except (ValueError, json.JSONDecodeError) as exc:
        raise ProductionOperationError(
            "operation project container inspection is invalid"
        ) from exc
    if (
        not isinstance(documents, list)
        or len(documents) != 1
        or not isinstance(documents[0], dict)
        or documents[0].get("Id") != identifier
        or not isinstance(documents[0].get("Config"), dict)
        or not isinstance(documents[0]["Config"].get("Labels"), dict)
    ):
        raise ProductionOperationError(
            "operation project container inspection is invalid"
        )
    labels = documents[0]["Config"]["Labels"]
    if labels.get("com.docker.compose.project") != manifest.project_name:
        raise ProductionOperationError(
            "operation project container identity differs"
        )
    return labels


def _oneoff_ids(
    manifest: OperationManifest,
    *,
    operation_root: Path,
) -> list[str]:
    database_identifiers: list[str] = []
    oneoff_identifiers: list[str] = []
    for identifier in _project_container_ids(manifest):
        labels = _container_compose_labels(identifier, manifest)
        service = labels.get("com.docker.compose.service")
        if (
            service == manifest.services["database"]
            and labels.get("com.docker.compose.oneoff") != "True"
        ):
            if (
                labels.get("trading-bot.production.operation-id")
                != manifest.operation_id
            ):
                raise ProductionOperationError(
                    "operation database container lacks its ownership label"
                )
            database_identifiers.append(identifier)
            continue
        _validate_oneoff_for_cleanup(
            identifier,
            manifest,
            operation_root=operation_root,
        )
        oneoff_identifiers.append(identifier)
    if len(database_identifiers) > 1:
        raise ProductionOperationError(
            "operation project has multiple database containers"
        )
    if database_identifiers:
        database_identifier = database_identifiers[0]
        running = _validate_database_container(
            database_identifier,
            manifest,
        )
        _validate_operation_network(
            manifest,
            expected_container_id=database_identifier,
            allowed_container_ids=set(oneoff_identifiers),
            require_present=True,
            require_attached=running,
        )
    return oneoff_identifiers


def _validate_oneoff_for_cleanup(
    identifier: str,
    manifest: OperationManifest,
    *,
    operation_root: Path,
) -> Mapping[str, Any]:
    raw = _run([DOCKER, "inspect", identifier], timeout=30)
    try:
        documents = json.loads(raw, object_pairs_hook=_strict_json_object)
    except (ValueError, json.JSONDecodeError) as exc:
        raise ProductionOperationError(
            "operation one-shot inspection is invalid"
        ) from exc
    if (
        not isinstance(documents, list)
        or len(documents) != 1
        or not isinstance(documents[0], dict)
    ):
        raise ProductionOperationError("operation one-shot inspection is invalid")
    document = documents[0]
    config = document.get("Config")
    labels = config.get("Labels") if isinstance(config, dict) else None
    mounts = document.get("Mounts")
    service = labels.get("com.docker.compose.service") if isinstance(labels, dict) else None
    allowed_services = set(manifest.services.values()) - {
        manifest.services["database"],
    }
    runtime_image_ids = _installed_runtime_image_ids(manifest)
    expected_image = (
        runtime_image_ids[
            "postgres"
            if service == manifest.services["restore"]
            else "app"
        ]
        if service in allowed_services
        else None
    )
    if (
        not isinstance(document.get("Id"), str)
        or not document["Id"].startswith(identifier)
        or not isinstance(config, dict)
        or config.get("Image") != expected_image
        or not isinstance(labels, dict)
        or labels.get("com.docker.compose.project") != manifest.project_name
        or labels.get("com.docker.compose.oneoff") != "True"
        or labels.get("trading-bot.production.operation-id")
        != manifest.operation_id
        or service not in allowed_services
        or not isinstance(mounts, list)
    ):
        raise ProductionOperationError(
            "refusing to remove a container outside the exact operation"
        )

    expected_ca_source = str(_canonical_operation_paths(manifest).ca)
    observed_ca = False
    observed_anonymous_pgdata = False
    anonymous_volumes: list[str] = []
    for mount in mounts:
        if not isinstance(mount, dict):
            raise ProductionOperationError("operation one-shot mount is invalid")
        if (
            mount.get("Type") == "bind"
            and mount.get("Source") == expected_ca_source
            and mount.get("Destination") == "/run/production-dr-ca/ca.crt"
            and mount.get("RW") is False
        ):
            if observed_ca:
                raise ProductionOperationError(
                    "operation one-shot has a duplicate CA mount"
                )
            observed_ca = True
            continue
        volume_name = mount.get("Name")
        if (
            service == manifest.services["restore"]
            and mount.get("Type") == "volume"
            and isinstance(volume_name, str)
            and re.fullmatch(r"[0-9a-f]{64}", volume_name)
            and mount.get("Source")
            == f"/var/lib/docker/volumes/{volume_name}/_data"
            and mount.get("Destination") == "/var/lib/postgresql/data"
            and mount.get("Driver") == "local"
            and mount.get("RW") is True
        ):
            if observed_anonymous_pgdata:
                raise ProductionOperationError(
                    "operation restore one-shot has duplicate anonymous PGDATA"
                )
            observed_anonymous_pgdata = True
            anonymous_volumes.append(volume_name)
            continue
        raise ProductionOperationError(
            "refusing to remove a one-shot with an unexpected mount"
        )
    if service == manifest.services["restore"]:
        if observed_ca or not observed_anonymous_pgdata:
            raise ProductionOperationError(
                "restore one-shot inherited an unexpected application mount"
            )
    elif not observed_ca or observed_anonymous_pgdata:
        raise ProductionOperationError(
            "application one-shot mount closure differs"
        )
    return {
        "container_id": str(document["Id"]),
        "service": str(service),
        "image_id": str(expected_image),
        "anonymous_volume_names": sorted(anonymous_volumes),
    }


def _cleanup_operation_oneoffs(
    manifest: OperationManifest,
    *,
    operation_root: Path,
    cleanup_evidence: list[Mapping[str, Any]] | None = None,
) -> tuple[Mapping[str, Any], ...]:
    removed: list[Mapping[str, Any]] = []
    stable_empty = 0
    for _attempt in range(40):
        identifiers = _oneoff_ids(
            manifest,
            operation_root=operation_root,
        )
        if not identifiers:
            stable_empty += 1
            if stable_empty >= 2:
                return tuple(removed)
            time.sleep(0.25)
            continue
        stable_empty = 0
        for identifier in identifiers:
            evidence = _validate_oneoff_for_cleanup(
                identifier,
                manifest,
                operation_root=operation_root,
            )
            _run([DOCKER, "rm", "--force", "--volumes", identifier], timeout=60)
            removed.append(evidence)
            if cleanup_evidence is not None and all(
                existing.get("container_id") != evidence["container_id"]
                for existing in cleanup_evidence
            ):
                cleanup_evidence.append(evidence)
        time.sleep(0.25)
    raise ProductionOperationError(
        "operation one-shot residue did not reach stable empty"
    )


def _compose_one_shot(
    prefix: list[str],
    manifest: OperationManifest,
    *,
    profile: str,
    service: str,
    command: list[str] | None = None,
    timeout: int,
    stdin: BinaryIO | int | None = subprocess.DEVNULL,
    cleanup_evidence: list[Mapping[str, Any]] | None = None,
) -> str:
    try:
        compose_path = Path(prefix[prefix.index("--file") + 1])
        env_path = Path(prefix[prefix.index("--env-file") + 1])
    except (ValueError, IndexError) as exc:
        raise ProductionOperationError("Compose command prefix is invalid") from exc
    canonical = _canonical_operation_paths(manifest)
    operation_root = canonical.project_root
    if (
        compose_path != canonical.compose
        or env_path != canonical.runtime_env
    ):
        raise ProductionOperationError("Compose command prefix escaped its operation")
    if _oneoff_ids(
        manifest,
        operation_root=operation_root,
    ):
        raise ProductionOperationError(
            "operation has stale one-shot container residue"
        )
    arguments = [
        *prefix,
        "--profile",
        profile,
        "run",
        "--rm",
        "--no-deps",
        "--label",
        f"trading-bot.production.operation-id={manifest.operation_id}",
        "-T",
        service,
    ]
    if command:
        arguments.extend(command)
    try:
        output = _run(arguments, timeout=timeout, stdin=stdin)
    except ProductionOperationError:
        _cleanup_operation_oneoffs(
            manifest,
            operation_root=operation_root,
            cleanup_evidence=cleanup_evidence,
        )
        raise
    _cleanup_operation_oneoffs(
        manifest,
        operation_root=operation_root,
        cleanup_evidence=cleanup_evidence,
    )
    return output


def _compose_streaming_copy_sha256(
    prefix: list[str],
    manifest: OperationManifest,
    *,
    sql: str,
    timeout: int,
    cleanup_evidence: list[Mapping[str, Any]] | None = None,
) -> StreamDigest:
    try:
        compose_path = Path(prefix[prefix.index("--file") + 1])
        env_path = Path(prefix[prefix.index("--env-file") + 1])
    except (ValueError, IndexError) as exc:
        raise ProductionOperationError("Compose command prefix is invalid") from exc
    canonical = _canonical_operation_paths(manifest)
    operation_root = canonical.project_root
    if (
        compose_path != canonical.compose
        or env_path != canonical.runtime_env
    ):
        raise ProductionOperationError("Compose command prefix escaped its operation")
    if _oneoff_ids(
        manifest,
        operation_root=operation_root,
    ):
        raise ProductionOperationError(
            "operation has stale one-shot container residue"
        )
    arguments = [
        *prefix,
        "--profile",
        "webapp-ir-restore",
        "run",
        "--rm",
        "--no-deps",
        "--label",
        f"trading-bot.production.operation-id={manifest.operation_id}",
        "-T",
        "--env",
        f"PGOPTIONS={DATABASE_FINGERPRINT_PGOPTIONS}",
        "--env",
        f"PGCLIENTENCODING={DATABASE_FINGERPRINT_CLIENT_ENCODING}",
        manifest.services["restore"],
        "psql",
        "-v",
        "ON_ERROR_STOP=1",
        "--no-psqlrc",
        "--quiet",
        "--command",
        sql,
    ]
    try:
        result = _run_streaming_sha256(arguments, timeout=timeout)
    except ProductionOperationError:
        _cleanup_operation_oneoffs(
            manifest,
            operation_root=operation_root,
            cleanup_evidence=cleanup_evidence,
        )
        raise
    _cleanup_operation_oneoffs(
        manifest,
        operation_root=operation_root,
        cleanup_evidence=cleanup_evidence,
    )
    return result


def _inspect_local_image(runtime_image_id: str) -> Mapping[str, Any]:
    if not _IMAGE_ID_RE.fullmatch(runtime_image_id):
        raise ProductionOperationError("local Docker image ID is invalid")
    output = _run([DOCKER, "image", "inspect", runtime_image_id], timeout=60)
    try:
        documents = json.loads(output, object_pairs_hook=_strict_json_object)
    except (ValueError, json.JSONDecodeError) as exc:
        raise ProductionOperationError("Docker image attestation is invalid") from exc
    if (
        not isinstance(documents, list)
        or len(documents) != 1
        or not isinstance(documents[0], dict)
        or documents[0].get("Id") != runtime_image_id
    ):
        raise ProductionOperationError("Docker image identity or platform differs")
    return documents[0]


def _local_image_semantic_evidence(
    document: Mapping[str, Any],
    *,
    image: ImageArtifact,
    manifest: OperationManifest,
) -> Mapping[str, Any]:
    config = document.get("Config")
    labels = config.get("Labels") if isinstance(config, dict) else None
    _validate_image_labels(
        image.role,
        labels,
        release_sha=manifest.release_sha,
        postgres_runtime_uid=manifest.postgres_runtime_uid,
        postgres_runtime_gid=manifest.postgres_runtime_gid,
    )
    try:
        descriptor, content_identity = image_content_descriptor(document)
    except DockerImageIdentityError as exc:
        raise ProductionOperationError(
            "role-local Docker image content identity is invalid"
        ) from exc
    return {
        "content_descriptor": dict(descriptor),
        "content_identity": content_identity,
    }


def _enumerate_local_images(
    manifest: OperationManifest,
) -> Mapping[str, tuple[Mapping[str, Any], ...]]:
    output = _run(
        [DOCKER, "image", "ls", "--all", "--quiet", "--no-trunc"],
        timeout=120,
    )
    observed_runtime_ids = [
        line.strip() for line in output.splitlines() if line.strip()
    ]
    if any(
        not _IMAGE_ID_RE.fullmatch(value)
        for value in observed_runtime_ids
    ):
        raise ProductionOperationError(
            "local Docker image inventory is invalid or ambiguous"
        )
    runtime_ids = sorted(set(observed_runtime_ids))
    expected_by_content = {
        image.content_identity: image
        for image in manifest.image_artifacts.values()
    }
    matches: dict[str, list[Mapping[str, Any]]] = {
        content_identity: []
        for content_identity in expected_by_content
    }
    for runtime_id in runtime_ids:
        document = _inspect_local_image(runtime_id)
        try:
            descriptor, content_identity = image_content_descriptor(
                document
            )
        except DockerImageIdentityError:
            continue
        if content_identity in matches:
            matches[content_identity].append(
                {
                    "runtime_image_id": runtime_id,
                    "content_descriptor": dict(descriptor),
                    "content_identity": content_identity,
                    "document": document,
                }
            )
    return {
        content_identity: tuple(values)
        for content_identity, values in matches.items()
    }


def _validate_runtime_image_set(
    manifest: OperationManifest,
    runtime_image_ids: Mapping[str, str],
) -> list[Mapping[str, Any]]:
    if (
        set(runtime_image_ids) != set(IMAGE_ROLES)
        or any(
            not isinstance(value, str)
            or not _IMAGE_ID_RE.fullmatch(value)
            for value in runtime_image_ids.values()
        )
        or len(set(runtime_image_ids.values())) != len(IMAGE_ROLES)
    ):
        raise ProductionOperationError(
            "role-local runtime image ID set is invalid"
        )
    evidence: list[Mapping[str, Any]] = []
    for role in IMAGE_ROLES:
        image = manifest.image_artifacts[role]
        runtime_image_id = runtime_image_ids[role]
        document = _inspect_local_image(runtime_image_id)
        semantic = _local_image_semantic_evidence(
            document,
            image=image,
            manifest=manifest,
        )
        if (
            semantic["content_descriptor"] != image.content_descriptor
            or semantic["content_identity"] != image.content_identity
        ):
            raise ProductionOperationError(
                "role-local Docker image semantic identity differs"
            )
        evidence.append(
            {
                "role": role,
                "runtime_image_id": runtime_image_id,
                "config_digest": image.config_digest,
                "content_descriptor": semantic["content_descriptor"],
                "content_identity": image.content_identity,
                "source": "object-storage-archive",
            }
        )
    return evidence


def load_images(
    manifest: OperationManifest,
    paths: Mapping[str, Path],
) -> list[Mapping[str, Any]]:
    for image in manifest.image_artifacts.values():
        _docker_archive_identity(
            paths[image.artifact_kind],
            image,
            release_sha=manifest.release_sha,
            postgres_runtime_uid=manifest.postgres_runtime_uid,
            postgres_runtime_gid=manifest.postgres_runtime_gid,
        )
    before = _enumerate_local_images(manifest)
    if any(before.values()):
        raise ProductionOperationError(
            "an unjournaled preexisting image matches the staged content"
        )
    for role in IMAGE_ROLES:
        image = manifest.image_artifacts[role]
        _run(
            [DOCKER, "load", "--input", str(paths[image.artifact_kind])],
            timeout=3600,
        )
    after = _enumerate_local_images(manifest)
    runtime_image_ids: dict[str, str] = {}
    for role in IMAGE_ROLES:
        image = manifest.image_artifacts[role]
        matches = after[image.content_identity]
        if len(matches) != 1:
            raise ProductionOperationError(
                "loaded Docker image semantic match is absent or ambiguous"
            )
        runtime_image_id = str(matches[0]["runtime_image_id"])
        if runtime_image_id in runtime_image_ids.values():
            raise ProductionOperationError(
                "loaded Docker image roles resolve to a shared runtime ID"
            )
        runtime_image_ids[role] = runtime_image_id
    return _validate_runtime_image_set(manifest, runtime_image_ids)


def _image_artifact_bindings(
    manifest: OperationManifest,
) -> Mapping[str, Mapping[str, Any]]:
    return {
        role: {
            "archive_sha256": image.archive_sha256,
            "archive_bytes": image.archive_bytes,
            "config_digest": image.config_digest,
            "content_descriptor": dict(image.content_descriptor),
            "content_identity": image.content_identity,
        }
        for role, image in sorted(manifest.image_artifacts.items())
    }


def _image_stage_attestation(
    manifest: OperationManifest,
    images: list[Mapping[str, Any]],
) -> tuple[Mapping[str, Any], str]:
    if (
        len(images) != len(IMAGE_ROLES)
        or {item.get("role") for item in images} != set(IMAGE_ROLES)
    ):
        raise ProductionOperationError(
            "image stage evidence is incomplete"
        )
    by_role = {str(item["role"]): item for item in images}
    runtime_image_ids = {
        role: str(by_role[role].get("runtime_image_id", ""))
        for role in IMAGE_ROLES
    }
    _validate_runtime_image_set(manifest, runtime_image_ids)
    document = {
        "schema": IMAGE_STAGE_ATTESTATION_SCHEMA,
        "operation_id": manifest.operation_id,
        "release_sha": manifest.release_sha,
        "operation_manifest_sha256": manifest.canonical_sha256,
        "role": "webapp_ir",
        "image_artifacts": _image_artifact_bindings(manifest),
        "runtime_image_ids": runtime_image_ids,
        "images": [
            dict(by_role[role])
            for role in IMAGE_ROLES
        ],
        "containers_started": False,
        "services_started": False,
    }
    return document, hashlib.sha256(_canonical_json(document)).hexdigest()


def _validate_compose_config(
    manifest: OperationManifest,
    *,
    operation_root: Path,
) -> Mapping[str, Any]:
    canonical = _canonical_operation_paths(manifest)
    output = _run(
        [
            *_compose_base(manifest, operation_root=operation_root),
            "--profile",
            "*",
            "config",
            "--format",
            "json",
        ],
        timeout=60,
    )
    try:
        runtime_env = parse_safe_dotenv(
            canonical.runtime_env.read_bytes()
        )
    except OSError as exc:
        raise ProductionOperationError(
            "role-local runtime environment is unavailable"
        ) from exc
    if "${" in output:
        raise ProductionOperationError(
            "rendered role-local Compose has an unresolved variable"
        )
    try:
        config = json.loads(output, object_pairs_hook=_strict_json_object)
    except (ValueError, json.JSONDecodeError) as exc:
        raise ProductionOperationError("rendered production shadow Compose is invalid") from exc
    services = config.get("services") if isinstance(config, dict) else None
    networks = config.get("networks") if isinstance(config, dict) else None
    volumes = config.get("volumes") if isinstance(config, dict) else None
    operation = (
        config.get("x-production-shadow-operation")
        if isinstance(config, dict)
        else None
    )
    runtime_image_binding = (
        config.get("x-production-shadow-runtime-image-ids")
        if isinstance(config, dict)
        else None
    )
    image_by_role = _runtime_image_ids_from_env_values(runtime_env)
    expected_service_names = set(manifest.services.values())
    expected_operation = {
        "operation_id": manifest.operation_id,
        "project_root": str(canonical.project_root),
        "release_root": str(canonical.release_root),
        "data_root": str(canonical.data_root),
        "secret_root": str(canonical.secret_root),
        "dr_ca_sha256": runtime_env.get(
            "PRODUCTION_SHADOW_DR_CA_SHA256"
        ),
        "dr_tls_attestation_sha256": runtime_env.get(
            "PRODUCTION_SHADOW_DR_TLS_ATTESTATION_SHA256"
        ),
        "dr_tls_attested_at_epoch": runtime_env.get(
            "PRODUCTION_SHADOW_DR_TLS_ATTESTED_AT_EPOCH"
        ),
    }
    if (
        config.get("name") != manifest.project_name
        or operation != expected_operation
        or runtime_image_binding != image_by_role
        or not isinstance(services, dict)
        or set(services) != expected_service_names
        or not isinstance(networks, dict)
        or set(networks) != {"webapp_ir"}
        or networks.get("webapp_ir")
        != {
            "name": f"{manifest.project_name}_webapp_ir",
            "ipam": {},
            "internal": True,
            "labels": {
                "trading-bot.production.operation-id": manifest.operation_id,
            },
        }
        or volumes not in (None, {})
    ):
        raise ProductionOperationError("rendered production shadow Compose scope differs")

    expected_commands = {
        manifest.services["restore"]: [
            "sh",
            "-ec",
            "echo 'invoke with docker compose run and an explicit restore command' >&2; exit 64",
        ],
        manifest.services["roles"]: [
            "python",
            "scripts/provision_three_site_database_roles.py",
            "--role-prefix",
            "webapp_ir",
        ],
        manifest.services["migration"]: ["python", "manage.py"],
        manifest.services["roles_post_migration"]: [
            "python",
            "scripts/provision_three_site_database_roles.py",
            "--role-prefix",
            "webapp_ir",
        ],
        manifest.services["fencing"]: [
            "python",
            "scripts/activate_three_site_database_fencing.py",
            "--site",
            "webapp_ir",
            "--application-role",
            "webapp_ir_app",
            "--projection-role",
            "webapp_ir_projection",
            "--receiver-role",
            "webapp_ir_receiver",
            "--delivery-role",
            "webapp_ir_delivery",
            "--blob-role",
            "webapp_ir_blob",
            "--effect-role",
            "webapp_ir_effect",
            "--control-role",
            "webapp_ir_control",
            "--observer-role",
            "webapp_ir_observer",
            "--operator",
            "production-shadow-compose",
            "--apply",
            "--confirm",
            "ENABLE-THREE-SITE-DATABASE-FENCING",
        ],
        manifest.services["writer_fence"]: [
            "python",
            "scripts/manage_webapp_writer.py",
            "fence",
            "--expected-epoch",
            "1",
            "--expected-active-site",
            "webapp_fi",
            "--operator",
            f"production-shadow:{manifest.operation_id}",
            "--reason",
            "initialize WebApp-IR as an operation-bound locally fenced standby",
            "--apply",
            "--confirm",
            "writer:fence:webapp_ir:1:1",
        ],
    }
    expected_dependencies = {
        manifest.services["database"]: set(),
        manifest.services["restore"]: {manifest.services["database"]},
        manifest.services["roles"]: {manifest.services["database"]},
        manifest.services["migration"]: {manifest.services["roles"]},
        manifest.services["roles_post_migration"]: {manifest.services["migration"]},
        manifest.services["fencing"]: {manifest.services["roles_post_migration"]},
        manifest.services["writer_fence"]: {manifest.services["fencing"]},
    }
    expected_dependency_conditions = {
        manifest.services["restore"]: "service_healthy",
        manifest.services["roles"]: "service_healthy",
        manifest.services["migration"]: "service_completed_successfully",
        manifest.services["roles_post_migration"]: "service_completed_successfully",
        manifest.services["fencing"]: "service_completed_successfully",
        manifest.services["writer_fence"]: "service_completed_successfully",
    }
    expected_profiles = {
        manifest.services["database"]: {
            "webapp-ir-data-ready",
            "webapp-ir-restore",
            "webapp-ir-prepare",
            "webapp-ir-private",
            "webapp-ir-acceptance",
            "webapp-ir-activation",
            "webapp-ir-effects",
            "webapp-ir-observe",
        },
        manifest.services["restore"]: {"webapp-ir-restore"},
        manifest.services["roles"]: {"webapp-ir-prepare"},
        manifest.services["migration"]: {"webapp-ir-prepare"},
        manifest.services["roles_post_migration"]: {"webapp-ir-prepare"},
        manifest.services["fencing"]: {"webapp-ir-prepare"},
        manifest.services["writer_fence"]: {"webapp-ir-prepare"},
    }
    owner_environment = {
        "TZ",
        "ENVIRONMENT",
        "TRUSTED_PROXY_CIDRS",
        "TOPOLOGY_SCHEMA_VERSION",
        "THREE_SITE_DR_ENABLED",
        "DR_EVENT_PROTOCOL_ENABLED",
        "DR_EVENT_PROTOCOL_STRICT",
        "DR_SYNC_VERIFY_TLS",
        "DR_SYNC_CA_BUNDLE",
        "RELEASE_SHA",
        "BACKGROUND_JOBS_ENABLED",
        "SERVER_MODE",
        "LOGICAL_AUTHORITY",
        "PHYSICAL_SITE",
        "DATABASE_URL",
        "SYNC_DATABASE_URL",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "POSTGRES_DB",
        "FRONTEND_URL",
        "PUBLIC_WEBAPP_URL",
        "JWT_SECRET_KEY",
        "REDIS_URL",
        "REDIS_HOST",
    }
    role_password_environment = {
        "THREE_SITE_APP_DB_PASSWORD",
        "THREE_SITE_RECEIVER_DB_PASSWORD",
        "THREE_SITE_DELIVERY_DB_PASSWORD",
        "THREE_SITE_PROJECTION_DB_PASSWORD",
        "THREE_SITE_BLOB_DB_PASSWORD",
        "THREE_SITE_EFFECT_DB_PASSWORD",
        "THREE_SITE_CONTROL_DB_PASSWORD",
        "THREE_SITE_OBSERVER_DB_PASSWORD",
    }
    expected_environment_names = {
        manifest.services["database"]: {
            "TZ",
            "PGTZ",
            "POSTGRES_USER",
            "POSTGRES_PASSWORD",
            "POSTGRES_DB",
        },
        manifest.services["restore"]: {
            "PGHOST",
            "PGUSER",
            "PGPASSWORD",
            "PGDATABASE",
        },
        manifest.services["roles"]: owner_environment | role_password_environment,
        manifest.services["migration"]: owner_environment,
        manifest.services["roles_post_migration"]: (
            owner_environment | role_password_environment
        ),
        manifest.services["fencing"]: owner_environment,
        manifest.services["writer_fence"]: owner_environment
        | {
            "DR_CONTROL_DATABASE_URL",
            "TRADING_BOT_SERVICE",
            "WRITER_WITNESS_REQUIRED",
            "WRITER_WITNESS_AUTO_RENEW_ENABLED",
        },
    }
    required_runtime_names = {
        "WEBAPP_IR_POSTGRES_USER",
        "WEBAPP_IR_POSTGRES_PASSWORD",
        "WEBAPP_IR_POSTGRES_DB",
        "WEBAPP_IR_PUBLIC_WEBAPP_URL",
        "WEBAPP_IR_APP_DB_PASSWORD",
        "WEBAPP_IR_RECEIVER_DB_PASSWORD",
        "WEBAPP_IR_DELIVERY_DB_PASSWORD",
        "WEBAPP_IR_PROJECTION_DB_PASSWORD",
        "WEBAPP_IR_BLOB_DB_PASSWORD",
        "WEBAPP_IR_EFFECT_DB_PASSWORD",
        "WEBAPP_IR_CONTROL_DB_PASSWORD",
        "WEBAPP_IR_OBSERVER_DB_PASSWORD",
    }
    if required_runtime_names - set(runtime_env):
        raise ProductionOperationError(
            "role-local runtime environment lacks prepare credentials"
        )
    owner_user = runtime_env["WEBAPP_IR_POSTGRES_USER"]
    owner_password = runtime_env["WEBAPP_IR_POSTGRES_PASSWORD"]
    database_name = runtime_env["WEBAPP_IR_POSTGRES_DB"]
    public_url = runtime_env["WEBAPP_IR_PUBLIC_WEBAPP_URL"]
    owner_environment_values = {
        "TZ": "UTC",
        "ENVIRONMENT": "production",
        "TRUSTED_PROXY_CIDRS": runtime_env.get(
            "PRODUCTION_SHADOW_TRUSTED_PROXY_CIDRS",
            "127.0.0.1/32,::1/128,172.16.0.0/12",
        ),
        "TOPOLOGY_SCHEMA_VERSION": "three-site-dr-v1",
        "THREE_SITE_DR_ENABLED": "true",
        "DR_EVENT_PROTOCOL_ENABLED": "true",
        "DR_EVENT_PROTOCOL_STRICT": "true",
        "DR_SYNC_VERIFY_TLS": "true",
        "DR_SYNC_CA_BUNDLE": "/run/production-dr-ca/ca.crt",
        "RELEASE_SHA": manifest.release_sha,
        "BACKGROUND_JOBS_ENABLED": "false",
        "SERVER_MODE": "iran",
        "LOGICAL_AUTHORITY": "webapp",
        "PHYSICAL_SITE": "webapp_ir",
        "DATABASE_URL": (
            f"postgresql+asyncpg://{owner_user}:{owner_password}"
            f"@webapp_ir_db/{database_name}"
        ),
        "SYNC_DATABASE_URL": (
            f"postgresql://{owner_user}:{owner_password}"
            f"@webapp_ir_db/{database_name}"
        ),
        "POSTGRES_USER": owner_user,
        "POSTGRES_PASSWORD": owner_password,
        "POSTGRES_DB": database_name,
        "FRONTEND_URL": public_url,
        "PUBLIC_WEBAPP_URL": public_url,
        "JWT_SECRET_KEY": "production-shadow-prepare-does-not-serve-jwt-webapp-ir",
        "REDIS_URL": "redis://webapp_ir_redis:6379/0",
        "REDIS_HOST": "webapp_ir_redis",
    }
    role_password_values = {
        "THREE_SITE_APP_DB_PASSWORD": runtime_env["WEBAPP_IR_APP_DB_PASSWORD"],
        "THREE_SITE_RECEIVER_DB_PASSWORD": runtime_env[
            "WEBAPP_IR_RECEIVER_DB_PASSWORD"
        ],
        "THREE_SITE_DELIVERY_DB_PASSWORD": runtime_env[
            "WEBAPP_IR_DELIVERY_DB_PASSWORD"
        ],
        "THREE_SITE_PROJECTION_DB_PASSWORD": runtime_env[
            "WEBAPP_IR_PROJECTION_DB_PASSWORD"
        ],
        "THREE_SITE_BLOB_DB_PASSWORD": runtime_env["WEBAPP_IR_BLOB_DB_PASSWORD"],
        "THREE_SITE_EFFECT_DB_PASSWORD": runtime_env[
            "WEBAPP_IR_EFFECT_DB_PASSWORD"
        ],
        "THREE_SITE_CONTROL_DB_PASSWORD": runtime_env[
            "WEBAPP_IR_CONTROL_DB_PASSWORD"
        ],
        "THREE_SITE_OBSERVER_DB_PASSWORD": runtime_env[
            "WEBAPP_IR_OBSERVER_DB_PASSWORD"
        ],
    }
    control_password = runtime_env["WEBAPP_IR_CONTROL_DB_PASSWORD"]
    control_sync_url = (
        f"postgresql://webapp_ir_control:{control_password}"
        f"@webapp_ir_db/{database_name}"
    )
    control_async_url = (
        f"postgresql+asyncpg://webapp_ir_control:{control_password}"
        f"@webapp_ir_db/{database_name}"
    )
    expected_environment_values = {
        manifest.services["database"]: {
            "TZ": "UTC",
            "PGTZ": "UTC",
            "POSTGRES_USER": owner_user,
            "POSTGRES_PASSWORD": owner_password,
            "POSTGRES_DB": database_name,
        },
        manifest.services["restore"]: {
            "PGHOST": "webapp_ir_db",
            "PGUSER": owner_user,
            "PGPASSWORD": owner_password,
            "PGDATABASE": database_name,
        },
        manifest.services["roles"]: {
            **owner_environment_values,
            **role_password_values,
        },
        manifest.services["migration"]: dict(owner_environment_values),
        manifest.services["roles_post_migration"]: {
            **owner_environment_values,
            **role_password_values,
        },
        manifest.services["fencing"]: dict(owner_environment_values),
        manifest.services["writer_fence"]: {
            **owner_environment_values,
            "TRADING_BOT_SERVICE": "writer_control_cli",
            "DATABASE_URL": control_async_url,
            "SYNC_DATABASE_URL": control_sync_url,
            "DR_CONTROL_DATABASE_URL": control_async_url,
            "POSTGRES_USER": "webapp_ir_control",
            "POSTGRES_PASSWORD": control_password,
            "WRITER_WITNESS_REQUIRED": "false",
            "WRITER_WITNESS_AUTO_RENEW_ENABLED": "false",
        },
    }
    ca_source = str(canonical.ca)
    expected_mounts = {
        manifest.services["database"]: {
            (
                "bind",
                str(canonical.postgres),
                "/var/lib/postgresql/data",
                False,
            ),
        },
        manifest.services["restore"]: set(),
        manifest.services["roles"]: {
            ("bind", ca_source, "/run/production-dr-ca/ca.crt", True),
        },
        manifest.services["migration"]: {
            ("bind", ca_source, "/run/production-dr-ca/ca.crt", True),
        },
        manifest.services["roles_post_migration"]: {
            ("bind", ca_source, "/run/production-dr-ca/ca.crt", True),
        },
        manifest.services["fencing"]: {
            ("bind", ca_source, "/run/production-dr-ca/ca.crt", True),
        },
        manifest.services["writer_fence"]: {
            ("bind", ca_source, "/run/production-dr-ca/ca.crt", True),
        },
    }
    for name, service in services.items():
        if not isinstance(service, dict):
            raise ProductionOperationError("rendered service is invalid")
        expected_image = (
            image_by_role["postgres"]
            if name in {manifest.services["database"], manifest.services["restore"]}
            else image_by_role["app"]
        )
        postgres_service = name in {
            manifest.services["database"],
            manifest.services["restore"],
        }
        service_networks = service.get("networks")
        dependencies = service.get("depends_on", {})
        dependency_names = set(dependencies) if isinstance(dependencies, dict) else set()
        profiles = service.get("profiles")
        environment = service.get("environment", {})
        labels = service.get("labels", {})
        if (
            service.get("image") != expected_image
            or service.get("pull_policy") != "never"
            or "build" in service
            or service.get("cgroup_parent")
            != _project_base(manifest.operation_id)
            or service.get("cpus") != (2 if postgres_service else 1)
            or service.get("mem_limit")
            != ("2147483648" if postgres_service else "805306368")
            or service.get("pids_limit") != (512 if postgres_service else 256)
            or service.get("ports") is not None
            or service.get("privileged") not in {None, False}
            or service.get("devices") is not None
            or service.get("pid") is not None
            or service.get("ipc") is not None
            or service.get("network_mode") is not None
            or service.get("entrypoint") is not None
            or service.get("extra_hosts") is not None
            or service.get("cap_add") is not None
            or any(
                service.get(field) is not None
                for field in (
                    "volumes_from",
                    "security_opt",
                    "sysctls",
                    "ulimits",
                    "userns_mode",
                    "runtime",
                    "dns",
                    "dns_search",
                    "links",
                )
            )
            or not isinstance(service_networks, dict)
            or set(service_networks) != {"webapp_ir"}
            or dependency_names != expected_dependencies[name]
            or not isinstance(profiles, list)
            or set(profiles) != expected_profiles[name]
            or not isinstance(environment, dict)
            or set(environment) != expected_environment_names[name]
            or environment != expected_environment_values[name]
            or labels
            != {
                "trading-bot.production.operation-id": manifest.operation_id,
            }
            or any(
                not isinstance(key, str)
                or not isinstance(value, (str, int, bool))
                or "${" in str(value)
                or key.startswith(("BOT_FI_", "WEBAPP_FI_"))
                for key, value in environment.items()
            )
        ):
            raise ProductionOperationError(
                f"invoked service {name} is outside the exact prepare allowlist"
            )
        if name != manifest.services["database"]:
            only_dependency = next(iter(expected_dependencies[name]))
            binding = dependencies.get(only_dependency)
            if (
                not isinstance(binding, dict)
                or binding.get("condition") != expected_dependency_conditions[name]
                or binding.get("required", True) is not True
                or binding.get("restart", False) is not False
            ):
                raise ProductionOperationError(
                    f"invoked service {name} dependency condition differs"
                )
        if name == manifest.services["database"]:
            if (
                service.get("restart") != "unless-stopped"
                or service.get("command")
                != [
                    "postgres",
                    "-c",
                    "timezone=UTC",
                    "-c",
                    "log_timezone=UTC",
                ]
                or set(environment)
                != {"TZ", "PGTZ", "POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB"}
            ):
                raise ProductionOperationError("shadow database restart policy differs")
        else:
            if service.get("restart") != "no" or service.get("command") != expected_commands[name]:
                raise ProductionOperationError(
                    f"one-shot service {name} command or restart policy differs"
                )
        if name not in {manifest.services["database"], manifest.services["restore"]} and (
            environment.get("PHYSICAL_SITE") != "webapp_ir"
            or environment.get("ENVIRONMENT") != "production"
        ):
            raise ProductionOperationError(
                f"one-shot service {name} runtime identity differs"
            )
        if name == manifest.services["writer_fence"] and (
            environment.get("WRITER_WITNESS_REQUIRED") != "false"
            or environment.get("WRITER_WITNESS_AUTO_RENEW_ENABLED") != "false"
            or any(
                key.startswith("WRITER_WITNESS_")
                and key
                not in {
                    "WRITER_WITNESS_REQUIRED",
                    "WRITER_WITNESS_AUTO_RENEW_ENABLED",
                }
                for key in environment
            )
        ):
            raise ProductionOperationError(
                "writer fence one-shot received a Witness capability"
            )
        service_volumes = service.get("volumes") or []
        if not isinstance(service_volumes, list):
            raise ProductionOperationError("invoked service volumes are invalid")
        observed_mounts: set[tuple[str, str, str, bool]] = set()
        for mount in service_volumes:
            if not isinstance(mount, dict):
                raise ProductionOperationError("invoked service mount is invalid")
            mount_type = mount.get("type")
            source = mount.get("source")
            target = mount.get("target")
            if (
                mount_type not in {"volume", "bind"}
                or not isinstance(source, str)
                or not isinstance(target, str)
            ):
                raise ProductionOperationError("invoked service mount type is invalid")
            observed_mounts.add(
                (
                    mount_type,
                    source,
                    target,
                    mount.get("read_only") is True,
                )
            )
        if observed_mounts != expected_mounts[name]:
            raise ProductionOperationError(
                f"invoked service {name} mount closure differs"
            )
    return config


def _psql(
    prefix: list[str],
    manifest: OperationManifest,
    sql: str,
    *,
    timeout: int = 60,
    cleanup_evidence: list[Mapping[str, Any]] | None = None,
) -> str:
    return _compose_one_shot(
        prefix,
        manifest,
        profile="webapp-ir-restore",
        service=manifest.services["restore"],
        command=[
            "psql",
            "-v",
            "ON_ERROR_STOP=1",
            "-Atqc",
            sql,
        ],
        timeout=timeout,
        cleanup_evidence=cleanup_evidence,
    )


def _operation_network_name(manifest: OperationManifest) -> str:
    return f"{manifest.project_name}_webapp_ir"


def _operation_network_present(manifest: OperationManifest) -> bool:
    raw = _run(
        [DOCKER, "network", "ls", "--format", "{{.Name}}"],
        timeout=30,
    )
    names = raw.splitlines() if raw else []
    if (
        len(names) != len(set(names))
        or any(
            not name
            or "\x00" in name
            or name != name.strip()
            for name in names
        )
    ):
        raise ProductionOperationError(
            "Docker network inventory is invalid"
        )
    return _operation_network_name(manifest) in names


def _validate_operation_network(
    manifest: OperationManifest,
    *,
    expected_container_id: str | None,
    allowed_container_ids: set[str] | None = None,
    require_present: bool,
    require_attached: bool,
) -> Mapping[str, Any] | None:
    expected_name = _operation_network_name(manifest)
    if not _operation_network_present(manifest):
        if require_present:
            raise ProductionOperationError(
                "operation database network is absent"
            )
        return None
    raw = _run([DOCKER, "network", "inspect", expected_name], timeout=30)
    try:
        documents = json.loads(raw, object_pairs_hook=_strict_json_object)
    except (ValueError, json.JSONDecodeError) as exc:
        raise ProductionOperationError(
            "operation database network inspection is invalid"
        ) from exc
    if (
        not isinstance(documents, list)
        or len(documents) != 1
        or not isinstance(documents[0], dict)
    ):
        raise ProductionOperationError(
            "operation database network inspection is invalid"
        )
    document = documents[0]
    labels = document.get("Labels")
    expected_labels = {
        "com.docker.compose.network": "webapp_ir",
        "com.docker.compose.project": manifest.project_name,
        "trading-bot.production.operation-id": manifest.operation_id,
    }
    allowed_label_keys = set(expected_labels) | {
        "com.docker.compose.version",
    }
    if (
        not isinstance(labels, dict)
        or any(
            labels.get(key) != value
            for key, value in expected_labels.items()
        )
        or not set(labels).issubset(allowed_label_keys)
        or (
            "com.docker.compose.version" in labels
            and (
                not isinstance(labels["com.docker.compose.version"], str)
                or not re.fullmatch(
                    r"[0-9]+(?:\.[0-9]+){1,3}(?:[-+][0-9A-Za-z.-]+)?",
                    labels["com.docker.compose.version"],
                )
            )
        )
    ):
        raise ProductionOperationError(
            "operation database network labels differ"
        )
    ipam = document.get("IPAM")
    ipam_config = ipam.get("Config") if isinstance(ipam, dict) else None
    if (
        not isinstance(ipam, dict)
        or ipam.get("Driver") != "default"
        or ipam.get("Options") not in (None, {})
        or not isinstance(ipam_config, list)
        or len(ipam_config) != 1
        or not isinstance(ipam_config[0], dict)
        or set(ipam_config[0]) != {"Subnet", "Gateway"}
    ):
        raise ProductionOperationError(
            "operation database network IPAM differs"
        )
    try:
        subnet = ipaddress.ip_network(
            ipam_config[0]["Subnet"],
            strict=True,
        )
        gateway = ipaddress.ip_address(ipam_config[0]["Gateway"])
    except (TypeError, ValueError) as exc:
        raise ProductionOperationError(
            "operation database network IPAM is invalid"
        ) from exc
    if (
        subnet.version != 4
        or not subnet.is_private
        or gateway.version != 4
        or gateway not in subnet
        or gateway in {subnet.network_address, subnet.broadcast_address}
    ):
        raise ProductionOperationError(
            "operation database network IPAM is unsafe"
        )
    containers = document.get("Containers")
    if not isinstance(containers, dict):
        raise ProductionOperationError(
            "operation database network endpoint inventory is invalid"
        )
    allowed_endpoints = set(allowed_container_ids or ())
    if any(
        not re.fullmatch(r"[0-9a-f]{64}", identifier)
        for identifier in allowed_endpoints
    ):
        raise ProductionOperationError(
            "operation database network endpoint allowlist is invalid"
        )
    expected_endpoints = allowed_endpoints | (
        set()
        if expected_container_id is None
        else {expected_container_id}
    )
    observed_endpoints = set(containers)
    if (
        not observed_endpoints.issubset(expected_endpoints)
        or (
            require_attached
            and expected_container_id not in observed_endpoints
        )
        or any(not isinstance(value, dict) for value in containers.values())
    ):
        raise ProductionOperationError(
            "operation database network has a foreign or missing endpoint"
        )
    network_id = document.get("Id")
    if (
        not isinstance(network_id, str)
        or not re.fullmatch(r"[0-9a-f]{64}", network_id)
        or document.get("Name") != expected_name
        or document.get("Scope") != "local"
        or document.get("Driver") != "bridge"
        or document.get("EnableIPv6") is not False
        or document.get("Internal") is not True
        or document.get("Attachable") is not False
        or document.get("Ingress") is not False
        or document.get("ConfigOnly") is not False
        or document.get("ConfigFrom") != {"Network": ""}
        or document.get("Options") != {}
    ):
        raise ProductionOperationError(
            "operation database network identity differs"
        )
    return {
        "network_id": network_id,
        "network_name": expected_name,
        "driver": "bridge",
        "internal": True,
    }


def _postgres_runtime_identity(
    manifest: OperationManifest,
) -> tuple[int, int]:
    return manifest.postgres_runtime_uid, manifest.postgres_runtime_gid


def _validate_postgres_bind_source(
    manifest: OperationManifest,
    *,
    initialized: bool,
) -> os.stat_result:
    canonical = _canonical_operation_paths(manifest)
    _require_real_owned_directory_chain(
        canonical.postgres.parent,
        required_uid=os.geteuid(),
    )
    try:
        metadata = canonical.postgres.stat(follow_symlinks=False)
    except OSError as exc:
        raise ProductionOperationError(
            "operation PostgreSQL bind source is unavailable"
        ) from exc
    expected_uid, expected_gid = (
        _postgres_runtime_identity(manifest)
        if initialized
        else (os.geteuid(), os.getegid())
    )
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != expected_uid
        or metadata.st_gid != expected_gid
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise ProductionOperationError(
            "operation PostgreSQL bind source ownership is unsafe"
        )
    return metadata


def _validate_database_container(
    identifier: str,
    manifest: OperationManifest,
) -> bool:
    canonical = _canonical_operation_paths(manifest)
    raw = _run([DOCKER, "inspect", identifier], timeout=30)
    try:
        documents = json.loads(raw, object_pairs_hook=_strict_json_object)
    except (ValueError, json.JSONDecodeError) as exc:
        raise ProductionOperationError(
            "operation database container inspection is invalid"
        ) from exc
    if (
        not isinstance(documents, list)
        or len(documents) != 1
        or not isinstance(documents[0], dict)
    ):
        raise ProductionOperationError(
            "operation database container inspection is invalid"
        )
    document = documents[0]
    config = document.get("Config")
    labels = config.get("Labels") if isinstance(config, dict) else None
    host_config = document.get("HostConfig")
    network_settings = document.get("NetworkSettings")
    networks = (
        network_settings.get("Networks")
        if isinstance(network_settings, dict)
        else None
    )
    postgres_image = _installed_runtime_image_ids(manifest)["postgres"]
    expected_network = f"{manifest.project_name}_webapp_ir"
    restart_policy = (
        host_config.get("RestartPolicy")
        if isinstance(host_config, dict)
        else None
    )
    state = document.get("State")
    running = state.get("Running") if isinstance(state, dict) else None
    status = state.get("Status") if isinstance(state, dict) else None
    if (
        not isinstance(document.get("Id"), str)
        or document["Id"] != identifier
        or document.get("Image") != postgres_image
        or not isinstance(config, dict)
        or config.get("Image") != postgres_image
        or not isinstance(labels, dict)
        or labels.get("com.docker.compose.project") != manifest.project_name
        or labels.get("com.docker.compose.service")
        != manifest.services["database"]
        or labels.get("com.docker.compose.oneoff") == "True"
        or labels.get("trading-bot.production.operation-id")
        != manifest.operation_id
        or not isinstance(host_config, dict)
        or host_config.get("Privileged") is not False
        or host_config.get("PortBindings") not in (None, {})
        or host_config.get("NetworkMode") != expected_network
        or not isinstance(restart_policy, dict)
        or restart_policy.get("Name") != "unless-stopped"
        or not isinstance(networks, dict)
        or set(networks) != {expected_network}
        or not isinstance(state, dict)
        or type(running) is not bool
        or (
            running is True
            and status != "running"
        )
        or (
            running is False
            and status not in {"created", "exited"}
        )
    ):
        raise ProductionOperationError(
            "operation database container identity differs"
        )
    mounts = document.get("Mounts")
    if not isinstance(mounts, list) or len(mounts) != 1:
        raise ProductionOperationError(
            "operation database container mount closure differs"
        )
    mount = mounts[0]
    if (
        not isinstance(mount, dict)
        or mount.get("Type") != "bind"
        or mount.get("Source") != str(canonical.postgres)
        or mount.get("Destination") != "/var/lib/postgresql/data"
        or mount.get("RW") is not True
        or mount.get("Propagation") != "rprivate"
    ):
        raise ProductionOperationError(
            "operation database container mount closure differs"
        )
    _validate_postgres_bind_source(
        manifest,
        initialized=status != "created",
    )
    return bool(running)


def _table_fingerprint_copy_sql(table: str) -> str:
    if not re.fullmatch(r"^[a-z_][a-z0-9_]{0,62}$", table):
        raise ProductionOperationError(
            "database table identifier is unsupported"
        )
    return (
        "COPY ("
        "SELECT row_to_json(source_row)::text "
        f'FROM public."{table}" AS source_row '
        'ORDER BY row_to_json(source_row)::text COLLATE "C"'
        ") TO STDOUT WITH (FORMAT text, ENCODING 'UTF8')"
    )


def _sequence_fingerprint_copy_sql() -> str:
    return (
        "COPY ("
        "SELECT json_build_object("
        "'sequence_name',sequencename,'last_value',last_value"
        ")::text "
        "FROM pg_sequences WHERE schemaname='public' "
        'ORDER BY sequencename COLLATE "C"'
        ") TO STDOUT WITH (FORMAT text, ENCODING 'UTF8')"
    )


def _fingerprint_from_streams(
    tables: list[str],
    stream_copy: Callable[[str], StreamDigest],
) -> tuple[str, int, int]:
    if tables != sorted(set(tables)):
        raise ProductionOperationError(
            "database table inventory is not exact"
        )
    summaries: list[list[Any]] = []
    row_count = 0
    for table in tables:
        result = stream_copy(_table_fingerprint_copy_sql(table))
        if (
            not isinstance(result, StreamDigest)
            or not SHA256_RE.fullmatch(result.sha256)
            or result.bytes < 0
            or result.records < 0
        ):
            raise ProductionOperationError(
                "database table stream attestation is invalid"
            )
        row_count += result.records
        summaries.append(
            [table, result.records, result.bytes, result.sha256]
        )
    sequences = stream_copy(_sequence_fingerprint_copy_sql())
    if (
        not isinstance(sequences, StreamDigest)
        or not SHA256_RE.fullmatch(sequences.sha256)
        or sequences.bytes < 0
        or sequences.records < 0
    ):
        raise ProductionOperationError(
            "database sequence stream attestation is invalid"
        )
    fingerprint = hashlib.sha256(
        _canonical_json(
            {
                "algorithm": DATABASE_FINGERPRINT_ALGORITHM,
                "session_settings": {
                    **DATABASE_FINGERPRINT_SESSION_SETTINGS,
                    "client_encoding": DATABASE_FINGERPRINT_CLIENT_ENCODING,
                },
                "tables": summaries,
                "sequences": {
                    "records": sequences.records,
                    "bytes": sequences.bytes,
                    "sha256": sequences.sha256,
                },
            }
        )
    ).hexdigest()
    return fingerprint, row_count, len(tables)


def _database_fingerprint(
    prefix: list[str],
    manifest: OperationManifest,
    *,
    cleanup_evidence: list[Mapping[str, Any]] | None = None,
) -> tuple[str, int, int]:
    tables = [
        value
        for value in _psql(
            prefix,
            manifest,
            "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename",
            cleanup_evidence=cleanup_evidence,
        ).splitlines()
        if value
    ]
    return _fingerprint_from_streams(
        tables,
        lambda sql: _compose_streaming_copy_sha256(
            prefix,
            manifest,
            sql=sql,
            timeout=300 if "pg_sequences" in sql else 1800,
            cleanup_evidence=cleanup_evidence,
        ),
    )


def _concurrent_index_status(
    prefix: list[str],
    manifest: OperationManifest,
    names: tuple[str, ...],
    *,
    cleanup_evidence: list[Mapping[str, Any]] | None = None,
) -> Mapping[str, tuple[bool, bool]]:
    if not names:
        return {}
    literals = ",".join(f"'{name}'" for name in names)
    rows = _psql(
        prefix,
        manifest,
        "SELECT c.relname || '|' || i.indisvalid::text || '|' || "
        "i.indisready::text "
        "FROM pg_index i "
        "JOIN pg_class c ON c.oid=i.indexrelid "
        "JOIN pg_namespace n ON n.oid=c.relnamespace "
        f"WHERE n.nspname='public' AND c.relname IN ({literals}) "
        "ORDER BY c.relname",
        cleanup_evidence=cleanup_evidence,
    )
    status: dict[str, tuple[bool, bool]] = {}
    for row in rows.splitlines():
        name, separator, remainder = row.partition("|")
        valid, second_separator, ready = remainder.partition("|")
        if (
            not separator
            or not second_separator
            or name not in names
            or name in status
            or valid not in {"true", "false", "t", "f"}
            or ready not in {"true", "false", "t", "f"}
        ):
            raise ProductionOperationError(
                "concurrent migration index attestation is invalid"
            )
        status[name] = (valid in {"true", "t"}, ready in {"true", "t"})
    return status


def _repair_invalid_concurrent_indexes(
    prefix: list[str],
    manifest: OperationManifest,
    names: tuple[str, ...],
    *,
    cleanup_evidence: list[Mapping[str, Any]] | None = None,
) -> tuple[str, ...]:
    status = _concurrent_index_status(
        prefix,
        manifest,
        names,
        cleanup_evidence=cleanup_evidence,
    )
    invalid = tuple(
        name
        for name in names
        if name in status and status[name] != (True, True)
    )
    for name in invalid:
        _psql(
            prefix,
            manifest,
            f'DROP INDEX CONCURRENTLY IF EXISTS public."{name}"',
            timeout=300,
            cleanup_evidence=cleanup_evidence,
        )
    after = _concurrent_index_status(
        prefix,
        manifest,
        names,
        cleanup_evidence=cleanup_evidence,
    )
    if any(name in after and after[name] != (True, True) for name in invalid):
        raise ProductionOperationError(
            "invalid concurrent migration index residue could not be removed"
        )
    return invalid


def prepare_database(
    manifest: OperationManifest,
    *,
    operation_root: Path,
    completed_phases: set[str] | None = None,
    phase_done: Callable[[str, Mapping[str, Any]], None] | None = None,
) -> Mapping[str, Any]:
    completed = set(completed_phases or ())
    cleanup_evidence: list[Mapping[str, Any]] = []
    canonical = _canonical_operation_paths(manifest)

    def mark(phase: str, evidence: Mapping[str, Any]) -> None:
        if phase not in completed:
            if phase_done is not None:
                phase_done(phase, evidence)
            completed.add(phase)

    _validate_compose_config(manifest, operation_root=operation_root)
    migration_graph = _load_migration_graph(canonical.release_root)
    migration_corridor = _migration_corridor(
        migration_graph,
        source_revision=str(manifest.source_database["alembic_revision"]),
        target_revision=manifest.expected_migration_revision,
    )
    concurrent_indexes = _concurrent_index_names(
        migration_graph,
        migration_corridor,
    )
    prefix = _compose_base(manifest, operation_root=operation_root)
    _cleanup_operation_oneoffs(
        manifest,
        operation_root=operation_root,
        cleanup_evidence=cleanup_evidence,
    )
    runtime_env = parse_safe_dotenv(
        canonical.runtime_env.read_bytes()
    )
    database_name = runtime_env.get("WEBAPP_IR_POSTGRES_DB", "")
    if not re.fullmatch(r"^[a-z_][a-z0-9_]{0,62}$", database_name):
        raise ProductionOperationError("WA-IR database name is invalid")

    postgres_root = canonical.postgres
    try:
        nonempty_postgres = next(postgres_root.iterdir(), None) is not None
    except OSError as exc:
        raise ProductionOperationError(
            "operation PostgreSQL directory cannot be inspected"
        ) from exc

    def database_container_id() -> str:
        value = _run(
            [
                *prefix,
                "ps",
                "--all",
                "--quiet",
                manifest.services["database"],
            ],
            timeout=30,
        )
        if value and not re.fullmatch(r"[0-9a-f]{64}", value):
            raise ProductionOperationError(
                "operation database container inventory is invalid"
            )
        return value

    existing_id = database_container_id()
    if "database-started" not in completed:
        if nonempty_postgres and not existing_id:
            raise ProductionOperationError(
                "nonempty operation PostgreSQL directory lacks its exact container"
            )
        adopted_existing = bool(existing_id)
        if existing_id:
            running = _validate_database_container(existing_id, manifest)
            _validate_operation_network(
                manifest,
                expected_container_id=existing_id,
                require_present=True,
                require_attached=running,
            )
        else:
            _validate_operation_network(
                manifest,
                expected_container_id=None,
                require_present=False,
                require_attached=False,
            )
            _run(
                [
                    *prefix,
                    "--profile",
                    "webapp-ir-data-ready",
                    "create",
                    "--no-build",
                    "--pull",
                    "never",
                    "--no-recreate",
                    manifest.services["database"],
                ],
                timeout=300,
            )
            existing_id = database_container_id()
            if not existing_id:
                raise ProductionOperationError(
                    "operation database container was not created"
                )
            running = _validate_database_container(existing_id, manifest)
            _validate_operation_network(
                manifest,
                expected_container_id=existing_id,
                require_present=True,
                require_attached=running,
            )
        if not running:
            _run([DOCKER, "start", existing_id], timeout=300)
            started = False
            last_start_validation_error: ProductionOperationError | None = None
            for attempt in range(40):
                try:
                    started = _validate_database_container(
                        existing_id,
                        manifest,
                    )
                    last_start_validation_error = None
                except ProductionOperationError as exc:
                    last_start_validation_error = exc
                    started = False
                if started:
                    break
                if attempt < 39:
                    time.sleep(0.25)
            if not started:
                raise ProductionOperationError(
                    "operation database container did not reach its exact "
                    "initialized identity"
                ) from last_start_validation_error
            _validate_operation_network(
                manifest,
                expected_container_id=existing_id,
                require_present=True,
                require_attached=True,
            )
        mark(
            "database-started",
            {
                "service": manifest.services["database"],
                "project": manifest.project_name,
                "container_id": existing_id,
                "adopted_after_lost_attestation": adopted_existing,
            },
        )
    else:
        if not existing_id:
            raise ProductionOperationError(
                "persisted database phase lacks its exact container"
            )
        if not _validate_database_container(existing_id, manifest):
            raise ProductionOperationError(
                "persisted database container is not running"
            )
        _validate_operation_network(
            manifest,
            expected_container_id=existing_id,
            require_present=True,
            require_attached=True,
        )
    ready = False
    for _attempt in range(60):
        try:
            if (
                _psql(
                    prefix,
                    manifest,
                    "SELECT 1",
                    timeout=10,
                    cleanup_evidence=cleanup_evidence,
                )
                == "1"
            ):
                ready = True
                break
        except ProductionOperationError:
            time.sleep(1)
    if not ready:
        raise ProductionOperationError("operation-owned shadow database is not ready")
    public_table_count = _psql(
        prefix,
        manifest,
        "SELECT count(*) FROM pg_class WHERE relkind='r' AND relnamespace="
        "(SELECT oid FROM pg_namespace WHERE nspname='public')",
        cleanup_evidence=cleanup_evidence,
    )
    if not public_table_count.isdigit():
        raise ProductionOperationError("operation database table count is invalid")

    restore_input = canonical.restore_dump.parent
    expected_writer_state = {
        "active_site": None,
        "writer_epoch": 1,
        "control_state": "fenced",
        "witness_lease_id": None,
    }

    def revision() -> str:
        return _psql(
            prefix,
            manifest,
            "SELECT version_num FROM alembic_version",
            cleanup_evidence=cleanup_evidence,
        )

    def source_restore_attestation() -> tuple[str, int, int]:
        restored_revision = revision()
        if restored_revision != manifest.source_database["alembic_revision"]:
            raise ProductionOperationError("restored database revision differs")
        observed = _database_fingerprint(
            prefix,
            manifest,
            cleanup_evidence=cleanup_evidence,
        )
        if (
            observed[0] != manifest.source_database["database_fingerprint_sha256"]
            or observed[1] != manifest.source_database["row_count"]
            or observed[2] != manifest.source_database["table_count"]
        ):
            raise ProductionOperationError("restored database fingerprint differs")
        return observed

    fingerprint = str(manifest.source_database["database_fingerprint_sha256"])
    row_count = int(manifest.source_database["row_count"])
    table_count = int(manifest.source_database["table_count"])
    if "database-restored" not in completed:
        if public_table_count == "0":
            with (restore_input / "database.dump").open("rb") as source:
                _compose_one_shot(
                    prefix,
                    manifest,
                    profile="webapp-ir-restore",
                    service=manifest.services["restore"],
                    command=[
                        "pg_restore",
                        "--exit-on-error",
                        "--single-transaction",
                        "--no-owner",
                        "--no-acl",
                        "--dbname",
                        database_name,
                    ],
                    timeout=3600,
                    stdin=source,
                    cleanup_evidence=cleanup_evidence,
                )
        fingerprint, row_count, table_count = source_restore_attestation()
        mark(
            "database-restored",
            {
                "source_revision": manifest.source_database["alembic_revision"],
                "database_fingerprint_sha256": fingerprint,
                "database_row_count": row_count,
                "database_table_count": table_count,
                "adopted_after_lost_attestation": public_table_count != "0",
            },
        )
    elif "database-migrated" not in completed:
        resume_revision = revision()
        if resume_revision == manifest.source_database["alembic_revision"]:
            fingerprint, row_count, table_count = source_restore_attestation()
        elif resume_revision not in migration_corridor:
            raise ProductionOperationError(
                "database revision is outside the bound source-to-target corridor"
            )

    migrated_revision = revision()
    if "database-migrated" not in completed:
        if migrated_revision not in migration_corridor:
            raise ProductionOperationError(
                "database revision is outside the bound source-to-target corridor"
            )
        repaired_indexes: tuple[str, ...] = ()
        if migrated_revision == manifest.expected_migration_revision:
            target_index_status = _concurrent_index_status(
                prefix,
                manifest,
                concurrent_indexes,
                cleanup_evidence=cleanup_evidence,
            )
            if set(target_index_status) != set(concurrent_indexes) or any(
                status != (True, True)
                for status in target_index_status.values()
            ):
                raise ProductionOperationError(
                    "target revision concurrent migration indexes are incomplete"
                )
            # The migration command may have committed immediately before a
            # controller/process loss. Re-run only idempotent post-migration
            # role/fencing gates, never the migration itself.
            service_order = ("roles_post_migration", "fencing")
        elif migrated_revision == manifest.source_database["alembic_revision"]:
            repaired_indexes = _repair_invalid_concurrent_indexes(
                prefix,
                manifest,
                concurrent_indexes,
                cleanup_evidence=cleanup_evidence,
            )
            service_order = (
                "roles",
                "migration",
                "roles_post_migration",
                "fencing",
            )
        else:
            repaired_indexes = _repair_invalid_concurrent_indexes(
                prefix,
                manifest,
                concurrent_indexes,
                cleanup_evidence=cleanup_evidence,
            )
            service_order = ("migration", "roles_post_migration", "fencing")
        resumed_from_revision = migrated_revision
        for service_key in service_order:
            _compose_one_shot(
                prefix,
                manifest,
                profile="webapp-ir-prepare",
                service=manifest.services[service_key],
                timeout=900,
                cleanup_evidence=cleanup_evidence,
            )
        migrated_revision = revision()
        if migrated_revision != manifest.expected_migration_revision:
            raise ProductionOperationError("migrated database revision differs")
        target_index_status = _concurrent_index_status(
            prefix,
            manifest,
            concurrent_indexes,
            cleanup_evidence=cleanup_evidence,
        )
        if set(target_index_status) != set(concurrent_indexes) or any(
            status != (True, True) for status in target_index_status.values()
        ):
            raise ProductionOperationError(
                "migrated database concurrent index closure differs"
            )
        mark(
            "database-migrated",
            {
                "migration_revision": migrated_revision,
                "resumed_from_revision": resumed_from_revision,
                "repaired_concurrent_indexes": list(repaired_indexes),
                "service_order": list(service_order),
            },
        )
    else:
        if migrated_revision != manifest.expected_migration_revision:
            raise ProductionOperationError(
                "persisted migration phase revision differs"
            )
        persisted_index_status = _concurrent_index_status(
            prefix,
            manifest,
            concurrent_indexes,
            cleanup_evidence=cleanup_evidence,
        )
        if set(persisted_index_status) != set(concurrent_indexes) or any(
            status != (True, True)
            for status in persisted_index_status.values()
        ):
            raise ProductionOperationError(
                "persisted migration concurrent index closure differs"
            )

    def writer_state_snapshot() -> Mapping[str, Any]:
        raw = _psql(
            prefix,
            manifest,
            "SELECT json_build_object("
            "'active_site',active_site,'writer_epoch',writer_epoch,"
            "'control_state',control_state,'witness_lease_id',witness_lease_id"
            ")::text FROM webapp_writer_state WHERE authority='webapp'",
            cleanup_evidence=cleanup_evidence,
        )
        try:
            value = json.loads(raw, object_pairs_hook=_strict_json_object)
        except (ValueError, json.JSONDecodeError) as exc:
            raise ProductionOperationError("writer state attestation is invalid") from exc
        if not isinstance(value, dict):
            raise ProductionOperationError("writer state attestation is invalid")
        return value

    writer_state = writer_state_snapshot()
    fence_command_applied = False
    if "writer-fenced" not in completed:
        if writer_state != expected_writer_state:
            fence_output = _compose_one_shot(
                prefix,
                manifest,
                profile="webapp-ir-prepare",
                service=manifest.services["writer_fence"],
                timeout=300,
                cleanup_evidence=cleanup_evidence,
            )
            try:
                fence_attestation = json.loads(
                    fence_output,
                    object_pairs_hook=_strict_json_object,
                )
            except (ValueError, json.JSONDecodeError) as exc:
                raise ProductionOperationError(
                    "writer fence command attestation is invalid"
                ) from exc
            if (
                not isinstance(fence_attestation, dict)
                or fence_attestation.get("status") != "applied"
                or fence_attestation.get("applied") is not True
            ):
                raise ProductionOperationError("writer fence command did not apply")
            fence_command_applied = True
            writer_state = writer_state_snapshot()
        if writer_state != expected_writer_state:
            raise ProductionOperationError("WA-IR writer state is not exactly fenced")
        mark(
            "writer-fenced",
            {
                "writer_state": dict(writer_state),
                "command_applied_in_this_run": fence_command_applied,
            },
        )
    elif writer_state != expected_writer_state:
        raise ProductionOperationError("persisted WA-IR writer fence state drifted")

    _cleanup_operation_oneoffs(
        manifest,
        operation_root=operation_root,
        cleanup_evidence=cleanup_evidence,
    )
    postgres_image_id = _installed_runtime_image_ids(manifest)["postgres"]
    postgres_runtime_uid, postgres_runtime_gid = _postgres_runtime_identity(
        manifest
    )
    database_resource = {
        "container_id": existing_id,
        "image_id": postgres_image_id,
        "project": manifest.project_name,
        "service": manifest.services["database"],
        "mount_type": "bind",
        "data_path": str(postgres_root),
        "data_uid": postgres_runtime_uid,
        "data_gid": postgres_runtime_gid,
    }
    return {
        "database_ready": True,
        "source_revision": manifest.source_database["alembic_revision"],
        "migration_revision": migrated_revision,
        "restored_source_database_fingerprint_sha256": fingerprint,
        "restored_source_database_row_count": row_count,
        "restored_source_database_table_count": table_count,
        "writer_fence_command_applied": (
            fence_command_applied or "writer-fenced" in completed
        ),
        "writer_state": writer_state,
        "database_container": database_resource,
        "database_container_started": True,
        "public_app_started": False,
        "private_dr_workers_started": False,
        "writer_started": False,
        "persistent_resource_cleanup_performed": False,
        "bounded_ephemeral_oneoff_cleanup_performed": bool(cleanup_evidence),
        "removed_ephemeral_resources": [dict(item) for item in cleanup_evidence],
    }


def confirmation_phrase(manifest: OperationManifest) -> str:
    return f"prepare-wa-ir:{manifest.operation_id}:{manifest.release_sha}"


def stage_confirmation_phrase(manifest: OperationManifest) -> str:
    return f"stage-wa-ir-images:{manifest.operation_id}:{manifest.release_sha}"


def plan(
    manifest: OperationManifest,
    *,
    operation_root: Path,
    required_uid: int,
    allow_final_prepare: bool = False,
) -> Mapping[str, Any]:
    paths = verify_incoming(
        manifest,
        operation_root=operation_root,
        required_uid=required_uid,
        allow_final_prepare=allow_final_prepare,
    )
    bundle_heads = _run(
        [GIT, "bundle", "list-heads", str(paths["release-bundle"])],
        timeout=60,
        env=_SAFE_GIT_ENV,
    )
    if manifest.release_sha not in {
        line.split()[0]
        for line in bundle_heads.splitlines()
        if line.split()
    }:
        raise ProductionOperationError("Git bundle lacks the exact release commit")
    verify_tar_archive(paths["uploads-archive"], mode="r:gz")
    verify_tar_archive(paths["audit-archive"], mode="r:gz")
    for image in manifest.image_artifacts.values():
        _docker_archive_identity(
            paths[image.artifact_kind],
            image,
            release_sha=manifest.release_sha,
            postgres_runtime_uid=manifest.postgres_runtime_uid,
            postgres_runtime_gid=manifest.postgres_runtime_gid,
        )
    return {
        "schema": ATTESTATION_SCHEMA,
        "status": "planned",
        "operation_id": manifest.operation_id,
        "release_sha": manifest.release_sha,
        "manifest_sha256": manifest.canonical_sha256,
        "required_confirmation": stage_confirmation_phrase(manifest),
        "artifact_count": len(paths),
        "database_container_started": False,
        "public_app_started": False,
        "private_dr_workers_started": False,
        "writer_started": False,
        "persistent_resource_cleanup_performed": False,
        "bounded_ephemeral_oneoff_cleanup_performed": False,
        "removed_ephemeral_resources": [],
        "object_storage_mutated": False,
    }


def _reattest_materialized_stage(
    manifest: OperationManifest,
    paths: Mapping[str, Path],
    state: Mapping[str, Any],
    *,
    required_uid: int,
) -> Mapping[str, Any]:
    canonical = _canonical_operation_paths(manifest)
    _materialize_release_bundle(
        paths["release-bundle"],
        canonical.release_root,
        manifest=manifest,
        required_uid=required_uid,
    )
    dump_artifact = manifest.artifacts["database-backup"]
    if _hash_regular_file(
        canonical.restore_dump,
        expected_uid=required_uid,
        maximum=dump_artifact.bytes,
    ) != (dump_artifact.sha256, dump_artifact.bytes):
        raise ProductionOperationError(
            "persisted database dump identity drifted"
        )
    materialized_evidence = state["evidence"]["materialized"]
    uploads_tree = _attest_extracted_archive_tree(
        paths["uploads-archive"],
        canonical.uploads,
        mode="r:gz",
        required_uid=required_uid,
    )
    audit_tree = _attest_extracted_archive_tree(
        paths["audit-archive"],
        canonical.audit,
        mode="r:gz",
        required_uid=required_uid,
    )
    if (
        materialized_evidence.get("database_dump_sha256")
        != dump_artifact.sha256
        or materialized_evidence.get("uploads_tree_sha256")
        != uploads_tree["tree_sha256"]
        or materialized_evidence.get("audit_tree_sha256")
        != audit_tree["tree_sha256"]
        or materialized_evidence.get("runtime_material_installed")
        is not False
    ):
        raise ProductionOperationError(
            "persisted materialization evidence differs"
        )
    _require_empty_secure_directory(
        canonical.redis,
        required_uid=required_uid,
        label="WA-IR Redis directory",
    )
    return {
        "release_root": str(canonical.release_root),
        "secrets_root": str(canonical.secret_root),
        "data_root": str(canonical.data_root),
        "runtime_material_installed": False,
        "uploads_tree": dict(uploads_tree),
        "audit_tree": dict(audit_tree),
    }


def _stage_images_from_state(
    manifest: OperationManifest,
    state: Mapping[str, Any],
) -> tuple[list[Mapping[str, Any]], Mapping[str, Any], str]:
    evidence = state["evidence"].get("images-loaded")
    if (
        not isinstance(evidence, dict)
        or set(evidence)
        != {
            "image_stage",
            "stage_attestation_sha256",
            "tagless_archives",
        }
        or evidence.get("tagless_archives") is not True
        or not isinstance(evidence.get("image_stage"), dict)
        or not isinstance(evidence.get("stage_attestation_sha256"), str)
        or not SHA256_RE.fullmatch(evidence["stage_attestation_sha256"])
    ):
        raise ProductionOperationError(
            "persisted image stage evidence is invalid"
        )
    stage = evidence["image_stage"]
    runtime_ids = (
        stage.get("runtime_image_ids")
        if isinstance(stage, dict)
        else None
    )
    if not isinstance(runtime_ids, dict):
        raise ProductionOperationError(
            "persisted image stage runtime IDs are invalid"
        )
    images = _validate_runtime_image_set(manifest, runtime_ids)
    observed_stage, observed_sha256 = _image_stage_attestation(
        manifest,
        images,
    )
    if (
        observed_stage != stage
        or observed_sha256 != evidence["stage_attestation_sha256"]
    ):
        raise ProductionOperationError(
            "persisted image stage evidence differs"
        )
    return images, stage, observed_sha256


def execute_stage(
    manifest: OperationManifest,
    *,
    operation_root: Path,
    required_uid: int,
    confirm: str,
) -> Mapping[str, Any]:
    canonical = _canonical_operation_paths(manifest)
    planned = plan(
        manifest,
        operation_root=operation_root,
        required_uid=required_uid,
    )
    if confirm != stage_confirmation_phrase(manifest):
        raise ProductionOperationError(
            "image stage confirmation phrase differs"
        )
    _ensure_canonical_operation_directories(
        canonical,
        required_uid=required_uid,
    )
    paths = verify_incoming(
        manifest,
        operation_root=operation_root,
        required_uid=required_uid,
    )
    state = _load_or_create_state(manifest, operation_root=operation_root)
    completed = set(state["completed_phases"])
    if "materialized" not in completed:
        materialized = materialize_stage(
            manifest,
            paths,
            operation_root=operation_root,
            required_uid=required_uid,
        )
        _advance_state(
            state,
            "materialized",
            {
                "release_sha": manifest.release_sha,
                "release_tree_sha": manifest.release_tree_sha,
                "runtime_material_installed": False,
                "redis_fresh_empty": True,
                "database_dump_sha256": manifest.artifacts[
                    "database-backup"
                ].sha256,
                "uploads_tree_sha256": materialized["uploads_tree"][
                    "tree_sha256"
                ],
                "audit_tree_sha256": materialized["audit_tree"]["tree_sha256"],
            },
            operation_root=operation_root,
        )
        completed.add("materialized")
    else:
        materialized = _reattest_materialized_stage(
            manifest,
            paths,
            state,
            required_uid=required_uid,
        )

    if "images-loaded" not in completed:
        image_attestations = load_images(manifest, paths)
        image_stage, stage_attestation_sha256 = _image_stage_attestation(
            manifest,
            image_attestations,
        )
        _advance_state(
            state,
            "images-loaded",
            {
                "image_stage": dict(image_stage),
                "stage_attestation_sha256": stage_attestation_sha256,
                "tagless_archives": True,
            },
            operation_root=operation_root,
        )
    else:
        (
            image_attestations,
            image_stage,
            stage_attestation_sha256,
        ) = _stage_images_from_state(manifest, state)

    return {
        **planned,
        "status": "wa-ir-images-staged",
        "required_confirmation": None,
        "materialized": materialized,
        "images": list(image_attestations),
        "image_stage": dict(image_stage),
        "stage_attestation_sha256": stage_attestation_sha256,
        "database_container_started": False,
        "private_dr_workers_started": False,
        "persistent_resource_cleanup_performed": False,
        "bounded_ephemeral_oneoff_cleanup_performed": False,
        "removed_ephemeral_resources": [],
        "presigned_url_persisted": False,
        "legacy_resources_mutated": False,
        "completed_phases": list(state["completed_phases"]),
        "operation_state_sha256": hashlib.sha256(
            _canonical_json(state)
        ).hexdigest(),
        "cleanup_policy": (
            "retain only create-only staged release/data and loaded tagless "
            "images; never start a container or service"
        ),
        "functional_boundary": (
            "image stage only; final prepare material and every database "
            "operation require a separate controller step"
        ),
    }


def execute(
    manifest: OperationManifest,
    *,
    operation_root: Path,
    required_uid: int,
    confirm: str,
) -> Mapping[str, Any]:
    planned = plan(
        manifest,
        operation_root=operation_root,
        required_uid=required_uid,
        allow_final_prepare=True,
    )
    if confirm != confirmation_phrase(manifest):
        raise ProductionOperationError("operation confirmation phrase differs")
    paths = verify_incoming(
        manifest,
        operation_root=operation_root,
        required_uid=required_uid,
        allow_final_prepare=True,
    )
    state = _load_or_create_state(manifest, operation_root=operation_root)
    if "images-loaded" not in state["completed_phases"]:
        raise ProductionOperationError(
            "database prepare requires a completed image stage"
        )
    (
        image_attestations,
        image_stage,
        stage_attestation_sha256,
    ) = _stage_images_from_state(manifest, state)
    runtime_image_ids = image_stage["runtime_image_ids"]
    final_archive = (
        operation_root / "incoming" / FINAL_PREPARE_DESTINATION_NAME
    )
    if not final_archive.exists() or final_archive.is_symlink():
        raise ProductionOperationError(
            "database prepare requires final prepare material"
        )
    final_material = install_final_prepare_material(
        manifest,
        final_archive,
        operation_root=operation_root,
        expected_stage_attestation_sha256=stage_attestation_sha256,
        expected_runtime_image_ids=runtime_image_ids,
        required_uid=required_uid,
    )
    if "final-material-installed" not in state["completed_phases"]:
        _advance_state(
            state,
            "final-material-installed",
            dict(final_material),
            operation_root=operation_root,
        )
    elif state["evidence"]["final-material-installed"] != final_material:
        raise ProductionOperationError(
            "persisted final prepare material evidence differs"
        )
    canonical = _canonical_operation_paths(manifest)
    materialized = {
        **_reattest_materialized_stage(
            manifest,
            paths,
            state,
            required_uid=required_uid,
        ),
        "runtime_material_installed": True,
        "runtime_env": str(canonical.runtime_env),
        "compose": str(canonical.compose),
    }

    def phase_done(phase: str, evidence: Mapping[str, Any]) -> None:
        _advance_state(
            state,
            phase,
            evidence,
            operation_root=operation_root,
        )

    database = prepare_database(
        manifest,
        operation_root=operation_root,
        completed_phases=set(state["completed_phases"]),
        phase_done=phase_done,
    )
    if "verified" not in state["completed_phases"]:
        _advance_state(
            state,
            "verified",
            {
                "migration_revision": database["migration_revision"],
                "writer_state": database["writer_state"],
                "one_shot_residue": False,
                "public_app_started": False,
                "private_dr_workers_started": False,
                "persistent_resource_cleanup_performed": False,
                "bounded_ephemeral_oneoff_cleanup_performed": database[
                    "bounded_ephemeral_oneoff_cleanup_performed"
                ],
                "removed_ephemeral_resources": database[
                    "removed_ephemeral_resources"
                ],
            },
            operation_root=operation_root,
        )
    return {
        **planned,
        "status": "wa-ir-shadow-data-ready-fenced",
        "required_confirmation": None,
        "materialized": materialized,
        "images": list(image_attestations),
        "image_stage": dict(image_stage),
        "stage_attestation_sha256": stage_attestation_sha256,
        "final_prepare_material": dict(final_material),
        "database": database,
        "database_container_started": True,
        "private_dr_workers_started": False,
        "persistent_resource_cleanup_performed": False,
        "bounded_ephemeral_oneoff_cleanup_performed": database[
            "bounded_ephemeral_oneoff_cleanup_performed"
        ],
        "removed_ephemeral_resources": database[
            "removed_ephemeral_resources"
        ],
        "presigned_url_persisted": False,
        "legacy_resources_mutated": False,
        "completed_phases": list(state["completed_phases"]),
        "operation_state_sha256": hashlib.sha256(
            _canonical_json(state)
        ).hexdigest(),
        "cleanup_policy": (
            "retain the operation-owned database container, internal network, and "
            "canonical bind data; "
            "remove only exact operation-labeled ephemeral one-shot containers and "
            "their anonymous volumes; never delete Object Storage versions"
        ),
        "functional_boundary": (
            "data-ready and fenced only; private DR services, convergence, routing, "
            "writer lease, and public activation require the separate cutover controller"
        ),
    }


def _operation_root(operation_id: str) -> Path:
    try:
        canonical = validate_operation_id(operation_id)
    except ProductionTransportError as exc:
        raise ProductionOperationError("operation id is invalid") from exc
    root = OPERATIONS_ROOT / canonical
    try:
        metadata = root.stat(follow_symlinks=False)
    except OSError as exc:
        raise ProductionOperationError("operation root is unavailable") from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise ProductionOperationError("operation root is unsafe")
    return root


def _error_payload(message: str) -> Mapping[str, str]:
    return {
        "status": "blocked",
        "error": message,
        "error_class": "ProductionOperationError",
    }


def _verify_executing_bootstrap(
    manifest: OperationManifest,
    *,
    operation_root: Path,
    required_uid: int,
) -> None:
    executable = Path(sys.argv[0])
    expected = operation_root / BOOTSTRAP_RELATIVE_PATH
    if not executable.is_absolute() or executable != expected:
        raise ProductionOperationError(
            "executing bootstrap path differs from the operation binding"
        )
    if _hash_regular_file(
        executable,
        expected_uid=required_uid,
        maximum=MAX_BOOTSTRAP_BYTES,
        expected_mode=0o700,
    ) != (manifest.bootstrap_sha256, manifest.bootstrap_bytes):
        raise ProductionOperationError(
            "executing bootstrap identity differs from the operation manifest"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--operation-id", required=True)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--stage-only", action="store_true")
    action.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm")
    args = parser.parse_args(argv)
    try:
        if os.geteuid() != 0:
            raise ProductionOperationError("production operation agent must run as root")
        root = _operation_root(args.operation_id)
        with _operation_lock(root, required_uid=0):
            manifest = load_manifest(
                root / "incoming" / "operation-manifest.json",
                required_uid=0,
            )
            if manifest.operation_id != args.operation_id:
                raise ProductionOperationError("operation manifest id differs from argv")
            _verify_executing_bootstrap(
                manifest,
                operation_root=root,
                required_uid=0,
            )
            if args.stage_only:
                result = execute_stage(
                    manifest,
                    operation_root=root,
                    required_uid=0,
                    confirm=str(args.confirm or ""),
                )
            elif args.apply:
                result = execute(
                    manifest,
                    operation_root=root,
                    required_uid=0,
                    confirm=str(args.confirm or ""),
                )
            else:
                if args.confirm is not None:
                    raise ProductionOperationError(
                        "--confirm is valid only with --stage-only or --apply"
                    )
                result = plan(manifest, operation_root=root, required_uid=0)
            result = {
                **result,
                "bootstrap_agent_verified": True,
                "bootstrap_agent_sha256": manifest.bootstrap_sha256,
                "bootstrap_agent_bytes": manifest.bootstrap_bytes,
            }
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0
    except ProductionOperationError as exc:
        print(json.dumps(_error_payload(str(exc)), sort_keys=True, separators=(",", ":")))
        return 1
    except Exception:
        print(
            json.dumps(
                _error_payload("production operation failed closed"),
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
