#!/usr/bin/env python3
"""Produce a bounded legacy-production snapshot for shadow preparation.

The default invocation is plan-only.  Apply mode reads only the three exact
legacy containers and their named volumes.  Its only Docker mutations are one
operation-labelled scratch PostgreSQL container and one scratch volume used to
prove the database dump can be restored and canonically fingerprinted.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import ctypes
from dataclasses import dataclass
import errno
import fcntl
import gzip
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import selectors
import stat
import subprocess
import tarfile
import time
from typing import Any, Callable, Iterator, Mapping, Sequence
from uuid import UUID

from scripts.production_shadow_cutover_controller import PRODUCTION_VHOSTS
from scripts.wa_ir_production_operation import (
    DATABASE_FINGERPRINT_ALGORITHM,
    DATABASE_FINGERPRINT_CLIENT_ENCODING,
    DATABASE_FINGERPRINT_PGOPTIONS,
    ProductionOperationError,
    StreamDigest,
    _fingerprint_from_streams,
    _run_streaming_sha256,
)


BINDING_SCHEMA = "production-shadow-source-snapshot-binding-v1"
FREEZE_SCHEMA = "production-shadow-source-freeze-evidence-v1"
MANIFEST_SCHEMA = "production-shadow-source-snapshot-v1"
DOCKER = "/usr/bin/docker"
TAR = "/usr/bin/tar"
LOCK_ROOT = Path("/run")
MAX_JSON_BYTES = 2 * 1024 * 1024
MAX_ARTIFACT_BYTES = 64 * 1024 * 1024 * 1024
MAX_COMMAND_OUTPUT_BYTES = 8 * 1024 * 1024
MAX_COMMAND_ERROR_BYTES = 2 * 1024 * 1024
MAX_TREE_MEMBERS = 250_000
MAX_PATH_BYTES = 4096
MAX_SNAPSHOT_ATTEMPTS = 3
POSTGRES_RUNTIME_UID = 70
POSTGRES_RUNTIME_GID = 70
SOURCE_PROJECTS = {"bot_fi": "trading_bot", "webapp_fi": "current"}
SOURCE_CONTAINERS = {
    "database": "trading_bot_db",
    "application": "trading_bot_app",
    "redis": "trading_bot_redis",
}
SOURCE_SERVICES = {"database": "db", "application": "app", "redis": "redis"}
SOURCE_IMAGE_REFERENCES = {
    "bot_fi": {
        "database": "postgres:15-alpine",
        "application": "trading_bot_base",
        "redis": "redis:7-alpine",
    },
    "webapp_fi": {
        "database": "postgres:15-alpine",
        "application": "trading_bot_base_iran",
        "redis": "redis:7-alpine",
    },
}
SOURCE_MOUNTS = {
    "database": {"database": "/var/lib/postgresql/data"},
    "application": {
        "uploads": "/app/uploads",
        "audit": "/app/audit_trail",
    },
    "redis": {"redis": "/data"},
}
ARTIFACT_FILES = {
    "database-backup": "database.dump",
    "uploads-archive": "uploads.tar.gz",
    "audit-archive": "audit.tar.gz",
}
MANIFEST_FILE = "source-snapshot-manifest.json"
MODES = ("live-baseline", "frozen-final")
ROLE_NAMES = tuple(sorted(SOURCE_PROJECTS))
IMAGE_KEYS = ("database", "application", "redis", "restore_postgres")
VOLUME_KEYS = ("database", "uploads", "audit", "redis")
VOLUME_SUFFIXES = {
    "database": "postgres_data",
    "uploads": "uploads_data",
    "audit": "audit_data",
    "redis": "redis_data",
}
SCRATCH_PURPOSE = "production-shadow-source-snapshot-restore"
FORBIDDEN_OUTPUT_ROOTS = (
    Path("/srv/trading-bot/current"),
    Path("/srv/trading-bot/shared-data"),
    Path("/var/lib/docker"),
)
LABEL_OPERATION = "trading-bot.production.operation-id"
LABEL_PURPOSE = "trading-bot.production.purpose"
LABEL_ROLE = "trading-bot.production.source-role"
LABEL_MODE = "trading-bot.production.snapshot-mode"
SAFE_ENV = {
    "PATH": "/usr/bin:/bin",
    "HOME": "/nonexistent",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "DOCKER_CONFIG": "/nonexistent",
}
SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
IMAGE_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
CONTAINER_ID_RE = re.compile(r"^[0-9a-f]{64}$")
REVISION_RE = re.compile(r"^[0-9a-z_]{1,64}$")
IDENTIFIER_RE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
DOCKER_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$")
IMAGE_REF_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_./:@+-]{0,255}$")
BINDING_FIELDS = frozenset(
    {
        "schema",
        "operation_id",
        "release_sha",
        "legacy_release_sha",
        "role",
        "source_project",
        "containers",
        "images",
        "volumes",
        "controller_manifest_sha256",
        "approval_sha256",
        "mode",
    }
)
FREEZE_FIELDS = frozenset(
    {
        "schema",
        "operation_id",
        "release_sha",
        "legacy_release_sha",
        "role",
        "source_project",
        "controller_manifest_sha256",
        "approval_sha256",
        "production_vhosts",
        "source_container_ids",
        "freeze_generation_sha256",
        "freeze_active",
        "write_capable_route_count",
        "legacy_writer_process_count",
        "writer_database_client_count",
        "file_mutator_process_count",
    }
)
ARTIFACT_FIELDS = frozenset({"sha256", "bytes", "restored_tree_sha256"})
SOURCE_DATABASE_FIELDS = frozenset(
    {
        "alembic_revision",
        "fingerprint_algorithm",
        "database_fingerprint_sha256",
        "row_count",
        "table_count",
    }
)
FILE_SNAPSHOT_FIELDS = frozenset(
    {
        "source_volume",
        "pre_tree_sha256",
        "archive_tree_sha256",
        "post_tree_sha256",
        "member_count",
        "expanded_bytes",
        "stable_attempt",
    }
)
SOURCE_FIELDS = frozenset(
    {"containers", "images", "volumes", "identity_sha256"}
)
SOURCE_CONTAINER_FIELDS = frozenset(
    {
        "id",
        "name",
        "image_id",
        "image_reference",
        "project",
        "service",
        "running",
        "started_at",
        "restart_count",
        "mounts",
        "other_mount_count",
        "other_mounts_sha256",
    }
)
SOURCE_MOUNT_FIELDS = frozenset(
    {"name", "source", "destination", "rw"}
)
SOURCE_IMAGE_FIELDS = frozenset({"reference", "image_id"})
SOURCE_VOLUME_FIELDS = frozenset(
    {
        "name",
        "driver",
        "mountpoint",
        "labels_sha256",
        "options_sha256",
    }
)
REDIS_ROLLBACK_FIELDS = frozenset(
    {
        "policy",
        "source_volume",
        "tree_sha256",
        "metadata_sha256",
        "member_count",
        "bytes",
        "stable_attempt",
        "archive_created",
        "restore",
    }
)
RESTORE_FIELDS = frozenset(
    {
        "status",
        "postgres_image_reference",
        "postgres_image_id",
        "postgres_runtime_uid",
        "postgres_runtime_gid",
        "scratch_postgres_system_id",
        "single_transaction",
        "network_mode",
        "pull_policy",
        "source_or_current_mounted",
        "recovered_prior_residue",
        "scratch_resources_removed",
        "zero_residue",
    }
)
MANIFEST_FIELDS = frozenset(
    {
        "schema",
        "status",
        "operation_id",
        "role",
        "mode",
        "release_sha",
        "legacy_release_sha",
        "source_project",
        "controller_manifest_sha256",
        "approval_sha256",
        "binding_sha256",
        "freeze_evidence_sha256",
        "source",
        "artifacts",
        "source_database",
        "file_snapshots",
        "redis_rollback_only",
        "restore_drill",
        "source_mutated",
        "current_mutated",
        "source_stopped_or_restarted",
        "redis_restored",
    }
)


class SourceSnapshotError(RuntimeError):
    """A redacted fail-closed source snapshot error."""


class UnstableTreeError(SourceSnapshotError):
    """A retryable source tree stability failure."""


@dataclass(frozen=True)
class SnapshotBinding:
    operation_id: str
    release_sha: str
    legacy_release_sha: str
    role: str
    source_project: str
    containers: Mapping[str, str]
    images: Mapping[str, str]
    volumes: Mapping[str, str]
    controller_manifest_sha256: str
    approval_sha256: str
    mode: str
    canonical_sha256: str


@dataclass(frozen=True)
class OutputPaths:
    operation_root: Path
    role_root: Path
    final: Path
    staging: Path
    manifest: Path


@dataclass(frozen=True)
class ImageIdentity:
    reference: str
    image_id: str
    labels: Mapping[str, str]


@dataclass(frozen=True)
class SourceInventory:
    containers: Mapping[str, Mapping[str, Any]]
    images: Mapping[str, ImageIdentity]
    volumes: Mapping[str, Mapping[str, Any]]
    canonical_sha256: str


@dataclass(frozen=True)
class HeldVolume:
    kind: str
    name: str
    mountpoint: Path
    descriptor: int
    stat_fields: tuple[int, ...]
    inspect_sha256: str


@dataclass(frozen=True)
class TreeEntry:
    path: str
    kind: str
    mode: int
    size: int
    sha256: str | None
    source_uid: int
    source_gid: int
    source_mtime_ns: int
    source_ctime_ns: int

    def stable_row(self) -> list[Any]:
        return [
            self.path,
            self.kind,
            self.mode,
            self.size,
            self.sha256,
            self.source_uid,
            self.source_gid,
            self.source_mtime_ns,
            self.source_ctime_ns,
        ]


@dataclass(frozen=True)
class TreeInventory:
    entries: tuple[TreeEntry, ...]
    member_count: int
    expanded_bytes: int
    stable_sha256: str


@dataclass(frozen=True)
class FileSnapshot:
    artifact_sha256: str
    artifact_bytes: int
    tree_sha256: str
    member_count: int
    expanded_bytes: int
    stable_attempt: int


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def _nonzero_sha256(value: Any, *, label: str) -> str:
    if (
        not isinstance(value, str)
        or SHA256_RE.fullmatch(value) is None
        or value == "0" * 64
    ):
        raise SourceSnapshotError(f"{label} is invalid")
    return value


def _operation_id(value: Any) -> str:
    if not isinstance(value, str):
        raise SourceSnapshotError("operation id is invalid")
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError) as exc:
        raise SourceSnapshotError("operation id is invalid") from exc
    if parsed.version != 4 or str(parsed) != value:
        raise SourceSnapshotError("operation id must be canonical UUIDv4")
    return value


def _secure_canonical_json(
    path: Path,
    *,
    label: str,
    fields: frozenset[str],
) -> tuple[dict[str, Any], str]:
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != 0
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) != 0o600
            or not 1 <= before.st_size <= MAX_JSON_BYTES
        ):
            raise SourceSnapshotError(f"{label} is not an exact root-only file")
        chunks: list[bytes] = []
        remaining = MAX_JSON_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        after = os.fstat(descriptor)
        stable = (
            "st_dev",
            "st_ino",
            "st_mode",
            "st_uid",
            "st_gid",
            "st_nlink",
            "st_size",
            "st_mtime_ns",
            "st_ctime_ns",
        )
        if (
            len(payload) != before.st_size
            or any(getattr(before, key) != getattr(after, key) for key in stable)
        ):
            raise SourceSnapshotError(f"{label} changed while being read")
        try:
            document = json.loads(
                payload.decode("utf-8"),
                object_pairs_hook=_strict_object,
            )
        except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
            raise SourceSnapshotError(f"{label} is invalid JSON") from exc
        if not isinstance(document, dict) or set(document) != fields:
            raise SourceSnapshotError(f"{label} fields are not exact")
        canonical = _canonical_json(document)
        if canonical != payload:
            raise SourceSnapshotError(f"{label} is not canonical JSON")
        return document, hashlib.sha256(canonical).hexdigest()
    except SourceSnapshotError:
        raise
    except OSError as exc:
        raise SourceSnapshotError(f"{label} is unavailable") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def load_binding(path: Path) -> SnapshotBinding:
    document, digest = _secure_canonical_json(
        path,
        label="source snapshot binding",
        fields=BINDING_FIELDS,
    )
    operation_id = _operation_id(document["operation_id"])
    role = document["role"]
    if role not in ROLE_NAMES:
        raise SourceSnapshotError("source role is invalid")
    new_release = document["release_sha"]
    legacy_release = document["legacy_release_sha"]
    if (
        not isinstance(new_release, str)
        or SHA40_RE.fullmatch(new_release) is None
        or not isinstance(legacy_release, str)
        or SHA40_RE.fullmatch(legacy_release) is None
        or new_release == "0" * 40
        or legacy_release == "0" * 40
        or new_release == legacy_release
    ):
        raise SourceSnapshotError("new or legacy release SHA is invalid")
    if document["source_project"] != SOURCE_PROJECTS[role]:
        raise SourceSnapshotError("source project is not canonical for the role")
    containers = document["containers"]
    if containers != SOURCE_CONTAINERS:
        raise SourceSnapshotError("source container names are not canonical")
    images = document["images"]
    expected_images = {
        **SOURCE_IMAGE_REFERENCES[role],
        "restore_postgres": (
            f"trading_bot_postgres_boottime:15-{new_release}"
        ),
    }
    if (
        not isinstance(images, dict)
        or set(images) != set(IMAGE_KEYS)
        or len(set(images.values())) != len(IMAGE_KEYS)
        or any(
            not isinstance(value, str)
            or IMAGE_REF_RE.fullmatch(value) is None
            for value in images.values()
        )
        or images != expected_images
    ):
        raise SourceSnapshotError("source image binding is invalid")
    volumes = document["volumes"]
    expected_volumes = {
        kind: f"{document['source_project']}_{suffix}"
        for kind, suffix in VOLUME_SUFFIXES.items()
    }
    if (
        not isinstance(volumes, dict)
        or set(volumes) != set(VOLUME_KEYS)
        or len(set(volumes.values())) != len(VOLUME_KEYS)
        or any(
            not isinstance(value, str)
            or DOCKER_NAME_RE.fullmatch(value) is None
            for value in volumes.values()
        )
        or volumes != expected_volumes
    ):
        raise SourceSnapshotError("source volume binding is invalid")
    mode = document["mode"]
    if mode not in MODES:
        raise SourceSnapshotError("source snapshot mode is invalid")
    return SnapshotBinding(
        operation_id=operation_id,
        release_sha=new_release,
        legacy_release_sha=legacy_release,
        role=role,
        source_project=document["source_project"],
        containers=dict(containers),
        images=dict(images),
        volumes=dict(volumes),
        controller_manifest_sha256=_nonzero_sha256(
            document["controller_manifest_sha256"],
            label="controller manifest hash",
        ),
        approval_sha256=_nonzero_sha256(
            document["approval_sha256"],
            label="approval hash",
        ),
        mode=mode,
        canonical_sha256=digest,
    )


def _expected_vhosts() -> dict[str, list[str]]:
    return {
        role: list(PRODUCTION_VHOSTS[role])
        for role in sorted(PRODUCTION_VHOSTS)
    }


def load_freeze_evidence(
    path: Path,
    binding: SnapshotBinding,
    *,
    source_container_ids: Mapping[str, str] | None = None,
) -> tuple[dict[str, Any], str]:
    document, digest = _secure_canonical_json(
        path,
        label="source freeze evidence",
        fields=FREEZE_FIELDS,
    )
    expected = {
        "operation_id": binding.operation_id,
        "release_sha": binding.release_sha,
        "legacy_release_sha": binding.legacy_release_sha,
        "role": binding.role,
        "source_project": binding.source_project,
        "controller_manifest_sha256": binding.controller_manifest_sha256,
        "approval_sha256": binding.approval_sha256,
        "production_vhosts": _expected_vhosts(),
        "freeze_active": True,
        "write_capable_route_count": 0,
        "legacy_writer_process_count": 0,
        "writer_database_client_count": 0,
        "file_mutator_process_count": 0,
    }
    if any(
        type(document.get(key)) is not type(value)
        or document.get(key) != value
        for key, value in expected.items()
    ):
        raise SourceSnapshotError(
            "source freeze evidence is not bound to the exact zero-writer state"
        )
    _nonzero_sha256(
        document["freeze_generation_sha256"],
        label="freeze generation hash",
    )
    ids = document["source_container_ids"]
    if (
        not isinstance(ids, dict)
        or set(ids) != set(SOURCE_CONTAINERS)
        or any(
            not isinstance(value, str)
            or CONTAINER_ID_RE.fullmatch(value) is None
            or value == "0" * 64
            for value in ids.values()
        )
        or (
            source_container_ids is not None
            and dict(ids) != dict(source_container_ids)
        )
    ):
        raise SourceSnapshotError(
            "source freeze evidence container identity differs"
        )
    return document, digest


def output_paths(output_root: Path, binding: SnapshotBinding) -> OutputPaths:
    if not output_root.is_absolute():
        raise SourceSnapshotError("snapshot output root must be absolute")
    operation = output_root / binding.operation_id
    role = operation / binding.role
    final = role / binding.mode
    return OutputPaths(
        operation_root=operation,
        role_root=role,
        final=final,
        staging=role / f".{binding.mode}.incomplete",
        manifest=final / MANIFEST_FILE,
    )


def confirmation_phrase(binding: SnapshotBinding) -> str:
    return (
        "produce-production-shadow-source-snapshot:"
        f"{binding.operation_id}:{binding.role}:{binding.mode}:"
        f"{binding.release_sha}"
    )


def build_plan(
    binding: SnapshotBinding,
    *,
    output_root: Path,
    freeze_evidence_sha256: str | None,
) -> dict[str, Any]:
    paths = output_paths(output_root, binding)
    compact = binding.operation_id.replace("-", "")
    scratch = (
        f"tb-prod-src-{compact}-{binding.role.replace('_', '-')}-"
        f"{'base' if binding.mode == 'live-baseline' else 'final'}"
    )
    return {
        "schema": MANIFEST_SCHEMA,
        "status": "planned",
        "operation_id": binding.operation_id,
        "role": binding.role,
        "mode": binding.mode,
        "release_sha": binding.release_sha,
        "legacy_release_sha": binding.legacy_release_sha,
        "source_project": binding.source_project,
        "controller_manifest_sha256": binding.controller_manifest_sha256,
        "approval_sha256": binding.approval_sha256,
        "binding_sha256": binding.canonical_sha256,
        "freeze_evidence_sha256": freeze_evidence_sha256,
        "source": {
            "containers": dict(sorted(binding.containers.items())),
            "images": dict(sorted(binding.images.items())),
            "volumes": dict(sorted(binding.volumes.items())),
        },
        "output_directory": str(paths.final),
        "artifacts": {
            kind: str(paths.final / name)
            for kind, name in sorted(ARTIFACT_FILES.items())
        },
        "manifest": str(paths.manifest),
        "scratch_container": scratch,
        "scratch_volume": f"{scratch}-pgdata",
        "scratch_network_mode": "none",
        "scratch_pull_policy": "never",
        "restore_single_transaction": True,
        "redis_policy": "rollback-tree-hash-only-never-restored",
        "required_confirmation": confirmation_phrase(binding),
        "executes_commands": False,
        "source_mutation": False,
        "scratch_resources_created": False,
    }


def _load_json_output(payload: bytes, *, label: str) -> Any:
    if len(payload) > MAX_COMMAND_OUTPUT_BYTES:
        raise SourceSnapshotError(f"{label} output is oversized")
    try:
        return json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_strict_object,
        )
    except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise SourceSnapshotError(f"{label} output is invalid") from exc


def _run(
    arguments: Sequence[str],
    *,
    timeout: int = 60,
    maximum: int = MAX_COMMAND_OUTPUT_BYTES,
) -> str:
    try:
        result = subprocess.run(
            list(arguments),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            env=SAFE_ENV,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise SourceSnapshotError(
            f"required command is unavailable: {Path(arguments[0]).name}"
        ) from exc
    if (
        result.returncode != 0
        or len(result.stdout) > maximum
        or len(result.stderr) > MAX_COMMAND_ERROR_BYTES
    ):
        raise SourceSnapshotError(
            f"required command failed closed: {Path(arguments[0]).name}"
        )
    try:
        return result.stdout.decode("utf-8").strip()
    except UnicodeDecodeError as exc:
        raise SourceSnapshotError(
            "required command returned non-UTF-8 output"
        ) from exc


def _inspect_optional(kind: str, name: str) -> Mapping[str, Any] | None:
    if kind not in {"container", "image", "volume"}:
        raise SourceSnapshotError("Docker inspection kind is invalid")
    try:
        result = subprocess.run(
            [DOCKER, kind, "inspect", name],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
            env=SAFE_ENV,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise SourceSnapshotError("Docker inspection is unavailable") from exc
    if result.returncode != 0:
        try:
            error = result.stderr.decode("utf-8").strip()
        except UnicodeDecodeError as exc:
            raise SourceSnapshotError(
                "Docker inspection failed ambiguously"
            ) from exc
        escaped = re.escape(name)
        missing_patterns = {
            "container": (
                rf"(?:(?:Error response from daemon|Error): )?"
                rf"(?:No such container: {escaped}|No such object: {escaped})"
            ),
            "volume": (
                rf"(?:(?:Error response from daemon|Error): )?"
                rf"(?:No such volume: {escaped}|No such object: {escaped})"
            ),
            "image": (
                rf"(?:(?:Error response from daemon|Error): )?"
                rf"(?:No such image: {escaped}|No such object: {escaped})"
            ),
        }
        if result.returncode == 1 and re.fullmatch(
            missing_patterns[kind], error
        ):
            return None
        raise SourceSnapshotError("Docker inspection failed ambiguously")
    document = _load_json_output(
        result.stdout,
        label=f"Docker {kind} inspection",
    )
    if (
        not isinstance(document, list)
        or len(document) != 1
        or not isinstance(document[0], dict)
    ):
        raise SourceSnapshotError(f"Docker {kind} inspection is invalid")
    return document[0]


def _inspect_required(kind: str, name: str) -> Mapping[str, Any]:
    document = _inspect_optional(kind, name)
    if document is None:
        raise SourceSnapshotError(f"required Docker {kind} is unavailable")
    return document


def _image_identity(
    reference: str,
    *,
    expected_release_sha: str | None = None,
    allow_missing_release_label: bool = False,
    require_postgres_runtime: bool = False,
) -> ImageIdentity:
    document = _inspect_required("image", reference)
    image_id = document.get("Id")
    config = document.get("Config")
    labels = config.get("Labels") if isinstance(config, dict) else None
    if labels is None:
        labels = {}
    if (
        not isinstance(image_id, str)
        or IMAGE_ID_RE.fullmatch(image_id) is None
        or image_id == "sha256:" + "0" * 64
        or not isinstance(config, dict)
        or not isinstance(labels, dict)
        or any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in labels.items()
        )
    ):
        raise SourceSnapshotError("Docker image identity is invalid")
    release_label = labels.get("org.opencontainers.image.revision")
    if expected_release_sha is not None and (
        (release_label is None and not allow_missing_release_label)
        or (
            release_label is not None
            and release_label != expected_release_sha
        )
    ):
        raise SourceSnapshotError("Docker image release label differs")
    if require_postgres_runtime and (
        labels.get("trading-bot.postgres.runtime-uid")
        != str(POSTGRES_RUNTIME_UID)
        or labels.get("trading-bot.postgres.runtime-gid")
        != str(POSTGRES_RUNTIME_GID)
    ):
        raise SourceSnapshotError(
            "PostgreSQL runtime UID/GID labels differ"
        )
    return ImageIdentity(reference, image_id, dict(labels))


def _critical_container_identity(
    document: Mapping[str, Any],
    *,
    kind: str,
    binding: SnapshotBinding,
    image: ImageIdentity,
) -> dict[str, Any]:
    name = binding.containers[kind]
    config = document.get("Config")
    labels = config.get("Labels") if isinstance(config, dict) else None
    state = document.get("State")
    identifier = document.get("Id")
    restart_count = document.get("RestartCount")
    if (
        not isinstance(identifier, str)
        or CONTAINER_ID_RE.fullmatch(identifier) is None
        or identifier == "0" * 64
        or document.get("Name") != f"/{name}"
        or document.get("Image") != image.image_id
        or not isinstance(config, dict)
        or config.get("Image") != image.reference
        or not isinstance(labels, dict)
        or labels.get("com.docker.compose.project")
        != binding.source_project
        or labels.get("com.docker.compose.service")
        != SOURCE_SERVICES[kind]
        or labels.get("com.docker.compose.oneoff") not in (None, "False")
        or not isinstance(state, dict)
        or type(state.get("Running")) is not bool
        or not isinstance(state.get("StartedAt"), str)
        or not 1 <= len(state["StartedAt"]) <= 128
        or type(restart_count) is not int
        or restart_count < 0
    ):
        raise SourceSnapshotError(
            f"source {kind} container identity or project label differs"
        )
    if kind == "database" and state["Running"] is not True:
        raise SourceSnapshotError("source database container is not running")
    mounts = document.get("Mounts")
    expected_mounts = SOURCE_MOUNTS[kind]
    if (
        not isinstance(mounts, list)
        or not len(expected_mounts) <= len(mounts) <= 128
    ):
        raise SourceSnapshotError(
            f"source {kind} container mount closure differs"
        )
    observed: dict[str, dict[str, Any]] = {}
    other_mounts: list[dict[str, Any]] = []
    destinations: set[str] = set()
    for mount in mounts:
        if not isinstance(mount, dict):
            raise SourceSnapshotError(
                f"source {kind} container mount identity differs"
            )
        destination = mount.get("Destination")
        if (
            not isinstance(destination, str)
            or not Path(destination).is_absolute()
            or destination in destinations
        ):
            raise SourceSnapshotError(
                f"source {kind} container mount destination differs"
            )
        destinations.add(destination)
        matches = [
            volume_kind
            for volume_kind, expected_destination in expected_mounts.items()
            if destination == expected_destination
        ]
        if mount.get("Type") == "volume":
            if len(matches) != 1:
                raise SourceSnapshotError(
                    f"source {kind} container volume destination differs"
                )
            volume_kind = matches[0]
            if (
                mount.get("Name") != binding.volumes[volume_kind]
                or mount.get("RW") is not True
                or not isinstance(mount.get("Source"), str)
                or not Path(mount["Source"]).is_absolute()
            ):
                raise SourceSnapshotError(
                    f"source {kind} container volume mount differs"
                )
            observed[volume_kind] = {
                "name": mount["Name"],
                "source": mount["Source"],
                "destination": destination,
                "rw": True,
            }
            continue
        if (
            mount.get("Type") != "bind"
            or matches
            or not isinstance(mount.get("Source"), str)
            or not Path(mount["Source"]).is_absolute()
            or type(mount.get("RW")) is not bool
            or not isinstance(mount.get("Propagation", ""), str)
        ):
            raise SourceSnapshotError(
                f"source {kind} container non-volume mount differs"
            )
        other_mounts.append(
            {
                "type": "bind",
                "source": mount["Source"],
                "destination": destination,
                "rw": mount["RW"],
                "propagation": mount.get("Propagation", ""),
            }
        )
    if set(observed) != set(expected_mounts):
        raise SourceSnapshotError(
            f"source {kind} container volume inventory differs"
        )
    return {
        "id": identifier,
        "name": name,
        "image_id": image.image_id,
        "image_reference": image.reference,
        "project": binding.source_project,
        "service": SOURCE_SERVICES[kind],
        "running": state["Running"],
        "started_at": state["StartedAt"],
        "restart_count": restart_count,
        "mounts": dict(sorted(observed.items())),
        "other_mount_count": len(other_mounts),
        "other_mounts_sha256": hashlib.sha256(
            _canonical_json(
                sorted(other_mounts, key=lambda row: row["destination"])
            )
        ).hexdigest(),
    }


def _critical_volume_identity(
    document: Mapping[str, Any],
    *,
    kind: str,
    binding: SnapshotBinding,
    expected_mountpoint: str,
) -> dict[str, Any]:
    labels = document.get("Labels")
    options = document.get("Options")
    mountpoint = document.get("Mountpoint")
    if (
        document.get("Name") != binding.volumes[kind]
        or document.get("Driver") != "local"
        or document.get("Scope") not in (None, "local")
        or not isinstance(mountpoint, str)
        or mountpoint != expected_mountpoint
        or not Path(mountpoint).is_absolute()
        or not isinstance(labels, dict)
        or labels.get("com.docker.compose.project")
        != binding.source_project
        or labels.get("com.docker.compose.volume")
        != VOLUME_SUFFIXES[kind]
        or (options is not None and not isinstance(options, dict))
        or any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in (labels or {}).items()
        )
        or any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in (options or {}).items()
        )
    ):
        raise SourceSnapshotError(f"source {kind} volume identity differs")
    return {
        "name": binding.volumes[kind],
        "driver": "local",
        "mountpoint": mountpoint,
        "labels_sha256": hashlib.sha256(
            _canonical_json(dict(sorted((labels or {}).items())))
        ).hexdigest(),
        "options_sha256": hashlib.sha256(
            _canonical_json(dict(sorted((options or {}).items())))
        ).hexdigest(),
    }


def inspect_source(binding: SnapshotBinding) -> SourceInventory:
    images = {
        "application": _image_identity(
            binding.images["application"],
            expected_release_sha=binding.legacy_release_sha,
            allow_missing_release_label=True,
        ),
        "database": _image_identity(binding.images["database"]),
        "redis": _image_identity(binding.images["redis"]),
        "restore_postgres": _image_identity(
            binding.images["restore_postgres"],
            expected_release_sha=binding.release_sha,
            require_postgres_runtime=True,
        ),
    }
    containers: dict[str, Mapping[str, Any]] = {}
    for kind in SOURCE_CONTAINERS:
        containers[kind] = _critical_container_identity(
            _inspect_required("container", binding.containers[kind]),
            kind=kind,
            binding=binding,
            image=images[kind],
        )
    expected_mountpoints = {
        volume_kind: str(mount["source"])
        for container in containers.values()
        for volume_kind, mount in container["mounts"].items()
    }
    if set(expected_mountpoints) != set(VOLUME_KEYS):
        raise SourceSnapshotError("source volume mount inventory is incomplete")
    volumes = {
        kind: _critical_volume_identity(
            _inspect_required("volume", binding.volumes[kind]),
            kind=kind,
            binding=binding,
            expected_mountpoint=expected_mountpoints[kind],
        )
        for kind in VOLUME_KEYS
    }
    public = {
        "containers": containers,
        "images": {
            kind: {
                "reference": identity.reference,
                "image_id": identity.image_id,
            }
            for kind, identity in sorted(images.items())
        },
        "volumes": volumes,
    }
    return SourceInventory(
        containers=containers,
        images=images,
        volumes=volumes,
        canonical_sha256=hashlib.sha256(_canonical_json(public)).hexdigest(),
    )


def _open_absolute_directory(path: Path) -> int:
    if not path.is_absolute() or not path.parts or path == Path("/"):
        raise SourceSnapshotError("Docker volume mountpoint is invalid")
    descriptor = os.open(
        "/",
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOATIME", 0),
    )
    try:
        for component in path.parts[1:]:
            next_descriptor = os.open(
                component,
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_NOATIME", 0),
                dir_fd=descriptor,
            )
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _directory_stat_fields(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_uid,
        value.st_gid,
    )


def hold_volume(
    kind: str,
    identity: Mapping[str, Any],
) -> HeldVolume:
    mountpoint = Path(str(identity["mountpoint"]))
    descriptor = -1
    try:
        descriptor = _open_absolute_directory(mountpoint)
        held = os.fstat(descriptor)
        visible = mountpoint.stat(follow_symlinks=False)
        if (
            not stat.S_ISDIR(held.st_mode)
            or _directory_stat_fields(held) != _directory_stat_fields(visible)
        ):
            raise SourceSnapshotError(
                f"source {kind} volume mountpoint is unsafe"
            )
        return HeldVolume(
            kind=kind,
            name=str(identity["name"]),
            mountpoint=mountpoint,
            descriptor=descriptor,
            stat_fields=_directory_stat_fields(held),
            inspect_sha256=hashlib.sha256(
                _canonical_json(identity)
            ).hexdigest(),
        )
    except SourceSnapshotError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError as exc:
        if descriptor >= 0:
            os.close(descriptor)
        raise SourceSnapshotError(
            f"source {kind} volume mountpoint cannot be held"
        ) from exc


def verify_held_volume(
    held: HeldVolume,
    binding: SnapshotBinding,
) -> None:
    try:
        descriptor_stat = os.fstat(held.descriptor)
        visible_stat = held.mountpoint.stat(follow_symlinks=False)
    except OSError as exc:
        raise SourceSnapshotError(
            f"source {held.kind} volume mountpoint is unavailable"
        ) from exc
    if (
        _directory_stat_fields(descriptor_stat) != held.stat_fields
        or _directory_stat_fields(visible_stat) != held.stat_fields
    ):
        raise SourceSnapshotError(
            f"source {held.kind} volume mountpoint changed"
        )
    document = _inspect_required("volume", held.name)
    current = _critical_volume_identity(
        document,
        kind=held.kind,
        binding=binding,
        expected_mountpoint=str(held.mountpoint),
    )
    if hashlib.sha256(_canonical_json(current)).hexdigest() != held.inspect_sha256:
        raise SourceSnapshotError(
            f"source {held.kind} volume identity changed"
        )


def _safe_component(name: str, prefix: str) -> str:
    if (
        not name
        or name in {".", ".."}
        or "/" in name
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in name)
    ):
        raise SourceSnapshotError("source tree contains an unsafe path")
    try:
        encoded = name.encode("utf-8")
        relative = f"{prefix}/{name}" if prefix else name
        relative_encoded = relative.encode("utf-8")
    except UnicodeError as exc:
        raise SourceSnapshotError(
            "source tree contains a non-UTF-8 path"
        ) from exc
    if len(encoded) > 255 or len(relative_encoded) > MAX_PATH_BYTES:
        raise SourceSnapshotError("source tree path exceeds its bound")
    candidate = PurePosixPath(relative)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise SourceSnapshotError("source tree contains path traversal")
    return relative


def _stable_file_fields(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_uid,
        value.st_gid,
        value.st_nlink,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _normalized_mode(mode: int) -> int:
    return stat.S_IMODE(mode) & ~0o6022


def _scan_tree(root_descriptor: int) -> TreeInventory:
    root = os.fstat(root_descriptor)
    root_device = root.st_dev
    entries: list[TreeEntry] = []
    total_bytes = 0

    def walk(directory: int, prefix: str) -> None:
        nonlocal total_bytes
        before = os.fstat(directory)
        if not stat.S_ISDIR(before.st_mode) or before.st_dev != root_device:
            raise SourceSnapshotError("source tree crosses an unsafe mount")
        try:
            names = sorted(os.listdir(directory))
        except OSError as exc:
            raise UnstableTreeError(
                "source tree cannot be enumerated stably"
            ) from exc
        for name in names:
            relative = _safe_component(name, prefix)
            if len(entries) >= MAX_TREE_MEMBERS:
                raise SourceSnapshotError("source tree has too many members")
            try:
                visible = os.stat(name, dir_fd=directory, follow_symlinks=False)
            except OSError as exc:
                raise UnstableTreeError(
                    "source tree changed during enumeration"
                ) from exc
            if visible.st_dev != root_device:
                raise SourceSnapshotError("source tree crosses an unsafe mount")
            if stat.S_ISDIR(visible.st_mode):
                try:
                    child = os.open(
                        name,
                        os.O_RDONLY
                        | getattr(os, "O_DIRECTORY", 0)
                        | getattr(os, "O_CLOEXEC", 0)
                        | getattr(os, "O_NOFOLLOW", 0)
                        | getattr(os, "O_NOATIME", 0),
                        dir_fd=directory,
                    )
                except OSError as exc:
                    raise UnstableTreeError(
                        "source tree directory changed during enumeration"
                    ) from exc
                try:
                    opened = os.fstat(child)
                    if (
                        opened.st_dev != visible.st_dev
                        or opened.st_ino != visible.st_ino
                        or not stat.S_ISDIR(opened.st_mode)
                    ):
                        raise UnstableTreeError(
                            "source tree directory identity changed"
                        )
                    entries.append(
                        TreeEntry(
                            relative,
                            "directory",
                            _normalized_mode(opened.st_mode),
                            0,
                            None,
                            opened.st_uid,
                            opened.st_gid,
                            opened.st_mtime_ns,
                            opened.st_ctime_ns,
                        )
                    )
                    walk(child, relative)
                    after = os.fstat(child)
                    path_after = os.stat(
                        name,
                        dir_fd=directory,
                        follow_symlinks=False,
                    )
                    stable = ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid")
                    if any(
                        getattr(opened, field) != getattr(after, field)
                        or getattr(opened, field) != getattr(path_after, field)
                        for field in stable
                    ):
                        raise UnstableTreeError(
                            "source tree directory changed while being read"
                        )
                finally:
                    os.close(child)
            elif stat.S_ISREG(visible.st_mode):
                if visible.st_nlink != 1:
                    raise SourceSnapshotError(
                        "source tree contains a hard-linked file"
                    )
                descriptor = -1
                try:
                    descriptor = os.open(
                        name,
                        os.O_RDONLY
                        | getattr(os, "O_CLOEXEC", 0)
                        | getattr(os, "O_NOFOLLOW", 0)
                        | getattr(os, "O_NOATIME", 0),
                        dir_fd=directory,
                    )
                    opened = os.fstat(descriptor)
                    if (
                        not stat.S_ISREG(opened.st_mode)
                        or _stable_file_fields(opened)
                        != _stable_file_fields(visible)
                    ):
                        raise UnstableTreeError(
                            "source tree file identity changed"
                        )
                    if opened.st_size < 0:
                        raise SourceSnapshotError(
                            "source tree file has invalid size"
                        )
                    total_bytes += opened.st_size
                    if total_bytes > MAX_ARTIFACT_BYTES:
                        raise SourceSnapshotError(
                            "source tree expanded size is oversized"
                        )
                    digest = hashlib.sha256()
                    consumed = 0
                    while True:
                        chunk = os.read(descriptor, 1024 * 1024)
                        if not chunk:
                            break
                        consumed += len(chunk)
                        digest.update(chunk)
                    after = os.fstat(descriptor)
                    path_after = os.stat(
                        name,
                        dir_fd=directory,
                        follow_symlinks=False,
                    )
                    if (
                        consumed != opened.st_size
                        or _stable_file_fields(opened)
                        != _stable_file_fields(after)
                        or _stable_file_fields(opened)
                        != _stable_file_fields(path_after)
                    ):
                        raise UnstableTreeError(
                            "source tree file changed while being read"
                        )
                    entries.append(
                        TreeEntry(
                            relative,
                            "file",
                            _normalized_mode(opened.st_mode),
                            opened.st_size,
                            digest.hexdigest(),
                            opened.st_uid,
                            opened.st_gid,
                            opened.st_mtime_ns,
                            opened.st_ctime_ns,
                        )
                    )
                except FileNotFoundError as exc:
                    raise UnstableTreeError(
                        "source tree file disappeared"
                    ) from exc
                finally:
                    if descriptor >= 0:
                        os.close(descriptor)
            else:
                raise SourceSnapshotError(
                    "source tree contains a symlink, device, socket, or FIFO"
                )
        after = os.fstat(directory)
        try:
            names_after = sorted(os.listdir(directory))
        except OSError as exc:
            raise UnstableTreeError(
                "source tree changed after enumeration"
            ) from exc
        stable_directory = ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid")
        if names != names_after or any(
            getattr(before, field) != getattr(after, field)
            for field in stable_directory
        ):
            raise UnstableTreeError("source tree changed during enumeration")

    walk(root_descriptor, "")
    rows = [entry.stable_row() for entry in entries]
    return TreeInventory(
        entries=tuple(entries),
        member_count=len(entries),
        expanded_bytes=total_bytes,
        stable_sha256=hashlib.sha256(_canonical_json(rows)).hexdigest(),
    )


def _open_relative_file(root_descriptor: int, relative: str) -> tuple[int, int]:
    components = PurePosixPath(relative).parts
    if not components:
        raise SourceSnapshotError("source file path is invalid")
    directory = os.dup(root_descriptor)
    try:
        for component in components[:-1]:
            child = os.open(
                component,
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_NOATIME", 0),
                dir_fd=directory,
            )
            os.close(directory)
            directory = child
        descriptor = os.open(
            components[-1],
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NOATIME", 0),
            dir_fd=directory,
        )
        return directory, descriptor
    except Exception:
        os.close(directory)
        raise


def _new_artifact_descriptor(path: Path) -> int:
    try:
        return os.open(
            path,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
    except OSError as exc:
        raise SourceSnapshotError("snapshot artifact already exists or is unsafe") from exc


def _write_staging_manifest(path: Path, payload: bytes) -> None:
    if not isinstance(payload, bytes) or not 1 <= len(payload) <= MAX_JSON_BYTES:
        raise SourceSnapshotError("source snapshot manifest payload is invalid")
    descriptor = _new_artifact_descriptor(path)
    try:
        view = memoryview(payload)
        written = 0
        while written < len(view):
            count = os.write(descriptor, view[written:])
            if count <= 0:
                raise SourceSnapshotError(
                    "source snapshot manifest write made no progress"
                )
            written += count
        os.fchmod(descriptor, 0o600)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
    except Exception:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        raise


def _create_archive(
    root_descriptor: int,
    inventory: TreeInventory,
    destination: Path,
) -> None:
    output_descriptor = _new_artifact_descriptor(destination)
    try:
        with os.fdopen(output_descriptor, "wb", closefd=True) as raw:
            output_descriptor = -1
            with gzip.GzipFile(
                filename="",
                mode="wb",
                fileobj=raw,
                compresslevel=9,
                mtime=0,
            ) as compressed:
                with tarfile.open(
                    fileobj=compressed,
                    mode="w|",
                    format=tarfile.GNU_FORMAT,
                ) as archive:
                    for entry in inventory.entries:
                        member = tarfile.TarInfo(entry.path)
                        member.uid = 0
                        member.gid = 0
                        member.uname = ""
                        member.gname = ""
                        member.mtime = 0
                        member.mode = entry.mode
                        member.pax_headers = {}
                        if entry.kind == "directory":
                            member.type = tarfile.DIRTYPE
                            member.size = 0
                            archive.addfile(member)
                            continue
                        parent = descriptor = -1
                        try:
                            parent, descriptor = _open_relative_file(
                                root_descriptor,
                                entry.path,
                            )
                            before = os.fstat(descriptor)
                            visible = os.stat(
                                PurePosixPath(entry.path).name,
                                dir_fd=parent,
                                follow_symlinks=False,
                            )
                            if (
                                not stat.S_ISREG(before.st_mode)
                                or before.st_nlink != 1
                                or _stable_file_fields(before)
                                != _stable_file_fields(visible)
                                or before.st_size != entry.size
                                or _normalized_mode(before.st_mode) != entry.mode
                            ):
                                raise UnstableTreeError(
                                    "source file changed before archiving"
                                )
                            member.type = tarfile.REGTYPE
                            member.size = entry.size
                            with os.fdopen(
                                descriptor, "rb", closefd=True
                            ) as source:
                                descriptor = -1
                                archive.addfile(member, source)
                                after = os.fstat(source.fileno())
                            path_after = os.stat(
                                PurePosixPath(entry.path).name,
                                dir_fd=parent,
                                follow_symlinks=False,
                            )
                            if (
                                _stable_file_fields(before)
                                != _stable_file_fields(after)
                                or _stable_file_fields(before)
                                != _stable_file_fields(path_after)
                            ):
                                raise UnstableTreeError(
                                    "source file changed while archiving"
                                )
                        finally:
                            if descriptor >= 0:
                                os.close(descriptor)
                            if parent >= 0:
                                os.close(parent)
            raw.flush()
            os.fsync(raw.fileno())
        os.chmod(destination, 0o600, follow_symlinks=False)
    except Exception:
        if output_descriptor >= 0:
            os.close(output_descriptor)
        try:
            destination.unlink()
        except FileNotFoundError:
            pass
        raise


def _validate_archive(path: Path, inventory: TreeInventory) -> tuple[str, int]:
    expected = {entry.path: entry for entry in inventory.entries}
    observed: set[str] = set()
    try:
        with tarfile.open(path, mode="r:gz") as archive:
            for member in archive:
                candidate = PurePosixPath(member.name.rstrip("/"))
                name = candidate.as_posix()
                if (
                    candidate.is_absolute()
                    or not candidate.parts
                    or ".." in candidate.parts
                    or name in observed
                    or name not in expected
                    or member.issym()
                    or member.islnk()
                    or member.isdev()
                    or not (member.isdir() or member.isfile())
                ):
                    raise SourceSnapshotError(
                        "snapshot archive contains an unsafe member"
                    )
                observed.add(name)
                entry = expected[name]
                if (
                    member.uid != 0
                    or member.gid != 0
                    or member.mtime != 0
                    or stat.S_IMODE(member.mode) != entry.mode
                    or (member.isdir()) != (entry.kind == "directory")
                    or member.size != entry.size
                ):
                    raise SourceSnapshotError(
                        "snapshot archive metadata differs"
                    )
                if member.isfile():
                    source = archive.extractfile(member)
                    if source is None:
                        raise SourceSnapshotError(
                            "snapshot archive member is unreadable"
                        )
                    digest = hashlib.sha256()
                    consumed = 0
                    while True:
                        chunk = source.read(1024 * 1024)
                        if not chunk:
                            break
                        consumed += len(chunk)
                        if consumed > entry.size:
                            raise SourceSnapshotError(
                                "snapshot archive member is oversized"
                            )
                        digest.update(chunk)
                    source.close()
                    if (
                        consumed != entry.size
                        or digest.hexdigest() != entry.sha256
                    ):
                        raise UnstableTreeError(
                            "source content changed while archiving"
                        )
        if observed != set(expected) or not observed:
            raise SourceSnapshotError(
                "snapshot archive member inventory differs or is empty"
            )
    except SourceSnapshotError:
        raise
    except (OSError, EOFError, tarfile.TarError) as exc:
        raise SourceSnapshotError("snapshot archive is invalid") from exc
    return _hash_secure_artifact(path)


def _patch_root_tar_header(block: bytes) -> bytes:
    if len(block) != 512:
        raise SourceSnapshotError("canonical tree root header is incomplete")
    name = block[:100].split(b"\0", 1)[0]
    if name not in {b".", b"./"} or block[156:157] != b"5":
        raise SourceSnapshotError("canonical tree root header is invalid")
    result = bytearray(block)
    result[100:108] = b"0000700\0"
    result[148:156] = b"        "
    checksum = sum(result)
    result[148:156] = f"{checksum:06o}\0 ".encode("ascii")
    return bytes(result)


def _canonical_tree_digest(root_descriptor: int) -> str:
    arguments = [
        TAR,
        "--sort=name",
        "--mtime=@0",
        "--owner=0",
        "--group=0",
        "--numeric-owner",
        "--mode=go-w,a-s",
        "--atime-preserve=system",
        "-cf",
        "-",
        "-C",
        f"/proc/self/fd/{root_descriptor}",
        ".",
    ]
    digest = hashlib.sha256()
    first = bytearray()
    transformed = False

    def consume(chunk: bytes) -> None:
        nonlocal transformed
        if transformed:
            digest.update(chunk)
            return
        first.extend(chunk)
        if len(first) >= 512:
            digest.update(_patch_root_tar_header(bytes(first[:512])))
            digest.update(first[512:])
            first.clear()
            transformed = True

    _stream_process(
        arguments,
        stdout_consumer=consume,
        stdout_maximum=MAX_ARTIFACT_BYTES
        + (MAX_TREE_MEMBERS + 64) * 2048,
        timeout=3600,
        pass_fds=(root_descriptor,),
    )
    if not transformed or first:
        raise SourceSnapshotError("canonical tree stream is incomplete")
    return digest.hexdigest()


def _stream_process(
    arguments: Sequence[str],
    *,
    stdout_consumer: Callable[[bytes], None],
    stdout_maximum: int,
    timeout: int,
    pass_fds: Sequence[int] = (),
) -> tuple[int, int]:
    process: subprocess.Popen[bytes] | None = None
    selector = selectors.DefaultSelector()
    stdout_bytes = 0
    stdout_records = 0
    stderr_bytes = 0
    deadline = time.monotonic() + timeout
    try:
        process = subprocess.Popen(
            list(arguments),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=SAFE_ENV,
            pass_fds=tuple(pass_fds),
        )
        if process.stdout is None or process.stderr is None:
            raise SourceSnapshotError("required streaming command is unavailable")
        os.set_blocking(process.stdout.fileno(), False)
        os.set_blocking(process.stderr.fileno(), False)
        selector.register(process.stdout, selectors.EVENT_READ, "stdout")
        selector.register(process.stderr, selectors.EVENT_READ, "stderr")
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise SourceSnapshotError(
                    f"required command timed out: {Path(arguments[0]).name}"
                )
            events = selector.select(min(remaining, 1.0))
            if not events and process.poll() is not None:
                events = [
                    (key, selectors.EVENT_READ)
                    for key in list(selector.get_map().values())
                ]
            for key, _mask in events:
                chunk = os.read(key.fileobj.fileno(), 1024 * 1024)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                if key.data == "stdout":
                    stdout_bytes += len(chunk)
                    stdout_records += chunk.count(b"\n")
                    if stdout_bytes > stdout_maximum:
                        raise SourceSnapshotError(
                            "required command output exceeds its bound"
                        )
                    stdout_consumer(chunk)
                else:
                    stderr_bytes += len(chunk)
                    if stderr_bytes > MAX_COMMAND_ERROR_BYTES:
                        raise SourceSnapshotError(
                            "required command error output exceeds its bound"
                        )
        return_code = process.wait(
            timeout=max(0.1, deadline - time.monotonic())
        )
        if return_code != 0:
            raise SourceSnapshotError(
                f"required command failed closed: {Path(arguments[0]).name}"
            )
        return stdout_bytes, stdout_records
    except (OSError, subprocess.SubprocessError) as exc:
        raise SourceSnapshotError(
            f"required streaming command failed: {Path(arguments[0]).name}"
        ) from exc
    finally:
        selector.close()
        if process is not None and process.poll() is None:
            process.kill()
            process.wait()
        if process is not None:
            if process.stdout is not None:
                process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()


def _hash_secure_artifact(path: Path) -> tuple[str, int]:
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != 0
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) != 0o600
            or not 1 <= before.st_size <= MAX_ARTIFACT_BYTES
        ):
            raise SourceSnapshotError("snapshot artifact is unsafe")
        digest = hashlib.sha256()
        consumed = 0
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            consumed += len(chunk)
            digest.update(chunk)
        after = os.fstat(descriptor)
        if (
            consumed != before.st_size
            or _stable_file_fields(before) != _stable_file_fields(after)
        ):
            raise SourceSnapshotError(
                "snapshot artifact changed while hashing"
            )
        return digest.hexdigest(), consumed
    except SourceSnapshotError:
        raise
    except OSError as exc:
        raise SourceSnapshotError("snapshot artifact is unavailable") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def snapshot_file_volume(
    held: HeldVolume,
    destination: Path,
    binding: SnapshotBinding,
) -> FileSnapshot:
    last_error: Exception | None = None
    for attempt in range(1, MAX_SNAPSHOT_ATTEMPTS + 1):
        try:
            verify_held_volume(held, binding)
            before_inventory = _scan_tree(held.descriptor)
            pre_digest = _canonical_tree_digest(held.descriptor)
            _create_archive(
                held.descriptor,
                before_inventory,
                destination,
            )
            artifact_sha256, artifact_bytes = _validate_archive(
                destination,
                before_inventory,
            )
            archive_digest = _canonical_tree_digest(held.descriptor)
            after_inventory = _scan_tree(held.descriptor)
            post_digest = _canonical_tree_digest(held.descriptor)
            verify_held_volume(held, binding)
            if (
                before_inventory.stable_sha256
                != after_inventory.stable_sha256
                or pre_digest != archive_digest
                or pre_digest != post_digest
            ):
                raise UnstableTreeError(
                    "source tree changed during snapshot"
                )
            return FileSnapshot(
                artifact_sha256=artifact_sha256,
                artifact_bytes=artifact_bytes,
                tree_sha256=pre_digest,
                member_count=before_inventory.member_count,
                expanded_bytes=before_inventory.expanded_bytes,
                stable_attempt=attempt,
            )
        except UnstableTreeError as exc:
            last_error = exc
            try:
                destination.unlink()
            except FileNotFoundError:
                pass
            if attempt < MAX_SNAPSHOT_ATTEMPTS:
                continue
        break
    raise SourceSnapshotError(
        f"source {held.kind} tree did not remain stable within "
        f"{MAX_SNAPSHOT_ATTEMPTS} attempts"
    ) from last_error


def redis_rollback_metadata(
    held: HeldVolume,
    binding: SnapshotBinding,
) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(1, MAX_SNAPSHOT_ATTEMPTS + 1):
        try:
            verify_held_volume(held, binding)
            before = _scan_tree(held.descriptor)
            pre_digest = _canonical_tree_digest(held.descriptor)
            after = _scan_tree(held.descriptor)
            post_digest = _canonical_tree_digest(held.descriptor)
            verify_held_volume(held, binding)
            if (
                before.stable_sha256 != after.stable_sha256
                or pre_digest != post_digest
            ):
                raise UnstableTreeError(
                    "source Redis tree changed during observation"
                )
            return {
                "policy": "sealed-rollback-evidence-only",
                "source_volume": held.name,
                "tree_sha256": pre_digest,
                "metadata_sha256": before.stable_sha256,
                "member_count": before.member_count,
                "bytes": before.expanded_bytes,
                "stable_attempt": attempt,
                "archive_created": False,
                "restore": False,
            }
        except UnstableTreeError as exc:
            last_error = exc
            if attempt < MAX_SNAPSHOT_ATTEMPTS:
                continue
        break
    raise SourceSnapshotError(
        "source Redis tree did not remain stable within the retry bound"
    ) from last_error


def _source_database_environment(
    binding: SnapshotBinding,
) -> tuple[str, str]:
    document = _inspect_required(
        "container", binding.containers["database"]
    )
    config = document.get("Config")
    environment = config.get("Env") if isinstance(config, dict) else None
    if (
        not isinstance(environment, list)
        or any(not isinstance(value, str) for value in environment)
    ):
        raise SourceSnapshotError(
            "source database environment identity is invalid"
        )
    values: dict[str, str] = {}
    for row in environment:
        key, separator, value = row.partition("=")
        if separator and key in {"POSTGRES_USER", "POSTGRES_DB"}:
            if key in values:
                raise SourceSnapshotError(
                    "source database environment is ambiguous"
                )
            values[key] = value
    user = values.get("POSTGRES_USER", "")
    database = values.get("POSTGRES_DB", "")
    if (
        IDENTIFIER_RE.fullmatch(user) is None
        or IDENTIFIER_RE.fullmatch(database) is None
    ):
        raise SourceSnapshotError(
            "source database user or database name is invalid"
        )
    return user, database


def source_dump_arguments(
    binding: SnapshotBinding,
    *,
    user: str,
    database: str,
) -> list[str]:
    if (
        IDENTIFIER_RE.fullmatch(user) is None
        or IDENTIFIER_RE.fullmatch(database) is None
    ):
        raise SourceSnapshotError("source database identifiers are invalid")
    return [
        DOCKER,
        "exec",
        "--env",
        f"PGOPTIONS={DATABASE_FINGERPRINT_PGOPTIONS}",
        "--env",
        f"PGCLIENTENCODING={DATABASE_FINGERPRINT_CLIENT_ENCODING}",
        binding.containers["database"],
        "pg_dump",
        "--format=custom",
        "--no-owner",
        "--no-acl",
        "--no-password",
        "--serializable-deferrable",
        "--username",
        user,
        "--dbname",
        database,
    ]


def create_database_dump(
    binding: SnapshotBinding,
    destination: Path,
    *,
    user: str,
    database: str,
) -> tuple[str, int]:
    descriptor = _new_artifact_descriptor(destination)
    digest = hashlib.sha256()
    written = 0

    def consume(chunk: bytes) -> None:
        nonlocal written
        view = memoryview(chunk)
        offset = 0
        while offset < len(view):
            count = os.write(descriptor, view[offset:])
            if count <= 0:
                raise SourceSnapshotError(
                    "database dump write made no progress"
                )
            offset += count
        written += len(chunk)
        digest.update(chunk)

    try:
        _stream_process(
            source_dump_arguments(
                binding,
                user=user,
                database=database,
            ),
            stdout_consumer=consume,
            stdout_maximum=MAX_ARTIFACT_BYTES,
            timeout=3600,
        )
        if written < 1:
            raise SourceSnapshotError("database dump is empty")
        os.fchmod(descriptor, 0o600)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        observed = _hash_secure_artifact(destination)
        if observed != (digest.hexdigest(), written):
            raise SourceSnapshotError(
                "database dump hash differs after publication"
            )
        return observed
    except Exception:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            destination.unlink()
        except FileNotFoundError:
            pass
        raise


def _scratch_names(binding: SnapshotBinding) -> tuple[str, str]:
    compact = binding.operation_id.replace("-", "")
    suffix = "base" if binding.mode == "live-baseline" else "final"
    container = (
        f"tb-prod-src-{compact}-{binding.role.replace('_', '-')}-{suffix}"
    )
    return container, f"{container}-pgdata"


def _scratch_labels(binding: SnapshotBinding) -> dict[str, str]:
    return {
        LABEL_OPERATION: binding.operation_id,
        LABEL_PURPOSE: SCRATCH_PURPOSE,
        LABEL_ROLE: binding.role,
        LABEL_MODE: binding.mode,
    }


def _validate_scratch_volume(
    document: Mapping[str, Any],
    *,
    binding: SnapshotBinding,
    name: str,
) -> None:
    if (
        document.get("Name") != name
        or document.get("Driver") != "local"
        or document.get("Labels") != _scratch_labels(binding)
        or document.get("Options") not in (None, {})
    ):
        raise SourceSnapshotError("scratch volume identity differs")


def _validate_scratch_container(
    document: Mapping[str, Any],
    *,
    binding: SnapshotBinding,
    name: str,
    volume: str,
    expected_image: ImageIdentity | None,
) -> str:
    config = document.get("Config")
    labels = config.get("Labels") if isinstance(config, dict) else None
    host = document.get("HostConfig")
    restart = host.get("RestartPolicy") if isinstance(host, dict) else None
    mounts = document.get("Mounts")
    identifier = document.get("Id")
    image_id = document.get("Image")
    if (
        not isinstance(identifier, str)
        or CONTAINER_ID_RE.fullmatch(identifier) is None
        or identifier == "0" * 64
        or document.get("Name") != f"/{name}"
        or not isinstance(image_id, str)
        or IMAGE_ID_RE.fullmatch(image_id) is None
        or image_id == "sha256:" + "0" * 64
        or (
            expected_image is not None
            and image_id != expected_image.image_id
        )
        or not isinstance(config, dict)
        or config.get("Image") != image_id
        or not isinstance(labels, dict)
        or any(
            labels.get(key) != value
            for key, value in _scratch_labels(binding).items()
        )
        or not isinstance(host, dict)
        or host.get("NetworkMode") != "none"
        or host.get("PortBindings") not in (None, {})
        or host.get("Privileged") is not False
        or not isinstance(restart, dict)
        or restart.get("Name") != "no"
        or restart.get("MaximumRetryCount") not in (None, 0)
        or not isinstance(mounts, list)
        or len(mounts) != 1
    ):
        raise SourceSnapshotError(
            "scratch container identity or isolation differs"
        )
    mount = mounts[0]
    if (
        not isinstance(mount, dict)
        or mount.get("Type") != "volume"
        or mount.get("Name") != volume
        or mount.get("Destination") != "/var/lib/postgresql/data"
        or mount.get("RW") is not True
    ):
        raise SourceSnapshotError("scratch container mount closure differs")
    return identifier


def cleanup_exact_scratch(
    binding: SnapshotBinding,
    *,
    expected_image: ImageIdentity | None = None,
) -> bool:
    container, volume = _scratch_names(binding)
    removed = False
    container_document = _inspect_optional("container", container)
    if container_document is not None:
        _validate_scratch_container(
            container_document,
            binding=binding,
            name=container,
            volume=volume,
            expected_image=expected_image,
        )
        _run([DOCKER, "container", "rm", "--force", container], timeout=120)
        removed = True
    volume_document = _inspect_optional("volume", volume)
    if volume_document is not None:
        _validate_scratch_volume(
            volume_document,
            binding=binding,
            name=volume,
        )
        _run([DOCKER, "volume", "rm", volume], timeout=120)
        removed = True
    if (
        _inspect_optional("container", container) is not None
        or _inspect_optional("volume", volume) is not None
    ):
        raise SourceSnapshotError(
            "scratch restore drill did not reach exact zero residue"
        )
    return removed


def _scratch_psql_arguments(
    container: str,
    sql: str,
    *,
    streaming: bool,
) -> list[str]:
    arguments = [
        DOCKER,
        "exec",
        "--env",
        f"PGOPTIONS={DATABASE_FINGERPRINT_PGOPTIONS}",
        "--env",
        f"PGCLIENTENCODING={DATABASE_FINGERPRINT_CLIENT_ENCODING}",
        container,
        "psql",
        "-v",
        "ON_ERROR_STOP=1",
        "--no-psqlrc",
        "-U",
        "restore",
        "-d",
        "restore",
    ]
    if streaming:
        arguments.extend(("--quiet", "--command", sql))
    else:
        arguments.extend(("-Atqc", sql))
    return arguments


def _scratch_query(container: str, sql: str) -> str:
    return _run(
        _scratch_psql_arguments(container, sql, streaming=False),
        timeout=300,
    )


def _scratch_stream(container: str, sql: str) -> StreamDigest:
    try:
        return _run_streaming_sha256(
            _scratch_psql_arguments(container, sql, streaming=True),
            timeout=1800,
            env=SAFE_ENV,
        )
    except ProductionOperationError as exc:
        raise SourceSnapshotError(
            "scratch streaming fingerprint failed closed"
        ) from exc


def build_source_database(
    *,
    query: Callable[[str], str],
    stream_copy: Callable[[str], StreamDigest],
) -> dict[str, Any]:
    revision = query("SELECT version_num FROM alembic_version")
    if REVISION_RE.fullmatch(revision) is None:
        raise SourceSnapshotError(
            "restored source migration revision is invalid"
        )
    tables = [
        value
        for value in query(
            "SELECT tablename FROM pg_tables "
            "WHERE schemaname='public' ORDER BY tablename"
        ).splitlines()
        if value
    ]
    try:
        fingerprint, row_count, table_count = _fingerprint_from_streams(
            tables,
            stream_copy,
        )
    except ProductionOperationError as exc:
        raise SourceSnapshotError(
            "restored source fingerprint contract failed"
        ) from exc
    if row_count > 10**15 or not 1 <= table_count <= 100_000:
        raise SourceSnapshotError(
            "restored source database inventory is outside its bound"
        )
    return {
        "alembic_revision": revision,
        "fingerprint_algorithm": DATABASE_FINGERPRINT_ALGORITHM,
        "database_fingerprint_sha256": fingerprint,
        "row_count": row_count,
        "table_count": table_count,
    }


def _restore_dump(
    container: str,
    dump: Path,
) -> None:
    descriptor = -1
    try:
        descriptor = os.open(
            dump,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != 0
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) != 0o600
            or not 1 <= before.st_size <= MAX_ARTIFACT_BYTES
        ):
            raise SourceSnapshotError("database dump is unsafe for restore")
        _run_with_input_descriptor(
            [
                DOCKER,
                "exec",
                "--interactive",
                container,
                "pg_restore",
                "-U",
                "restore",
                "-d",
                "restore",
                "--exit-on-error",
                "--single-transaction",
                "--no-owner",
                "--no-acl",
            ],
            descriptor,
            timeout=3600,
        )
        after = os.fstat(descriptor)
        visible = dump.stat(follow_symlinks=False)
        if (
            _stable_file_fields(before) != _stable_file_fields(after)
            or _stable_file_fields(before) != _stable_file_fields(visible)
        ):
            raise SourceSnapshotError(
                "database restore drill failed closed"
            )
    except (OSError, subprocess.SubprocessError) as exc:
        raise SourceSnapshotError(
            "database restore drill is unavailable"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _run_with_input_descriptor(
    arguments: Sequence[str],
    descriptor: int,
    *,
    timeout: int,
) -> None:
    process: subprocess.Popen[bytes] | None = None
    selector = selectors.DefaultSelector()
    deadline = time.monotonic() + timeout
    stderr_bytes = 0
    try:
        process = subprocess.Popen(
            list(arguments),
            stdin=descriptor,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            env=SAFE_ENV,
        )
        if process.stderr is None:
            raise SourceSnapshotError(
                "required input-stream command is unavailable"
            )
        os.set_blocking(process.stderr.fileno(), False)
        selector.register(process.stderr, selectors.EVENT_READ)
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise SourceSnapshotError(
                    f"required command timed out: {Path(arguments[0]).name}"
                )
            events = selector.select(min(remaining, 1.0))
            if not events and process.poll() is not None:
                events = [
                    (key, selectors.EVENT_READ)
                    for key in list(selector.get_map().values())
                ]
            for key, _mask in events:
                chunk = os.read(key.fileobj.fileno(), 65536)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                stderr_bytes += len(chunk)
                if stderr_bytes > MAX_COMMAND_ERROR_BYTES:
                    raise SourceSnapshotError(
                        "required command error output exceeds its bound"
                    )
        return_code = process.wait(
            timeout=max(0.1, deadline - time.monotonic())
        )
        if return_code != 0:
            raise SourceSnapshotError(
                f"required command failed closed: {Path(arguments[0]).name}"
            )
    except (OSError, subprocess.SubprocessError) as exc:
        raise SourceSnapshotError(
            f"required input-stream command failed: "
            f"{Path(arguments[0]).name}"
        ) from exc
    finally:
        selector.close()
        if process is not None and process.poll() is None:
            process.kill()
            process.wait()
        if process is not None and process.stderr is not None:
            process.stderr.close()


def restore_and_fingerprint(
    binding: SnapshotBinding,
    *,
    dump: Path,
    postgres_image: ImageIdentity,
) -> tuple[dict[str, Any], dict[str, Any]]:
    container, volume = _scratch_names(binding)
    recovered = cleanup_exact_scratch(binding)
    result: dict[str, Any] | None = None
    failure: Exception | None = None
    try:
        observed_volume = _run(
            [
                DOCKER,
                "volume",
                "create",
                "--driver",
                "local",
                *[
                    item
                    for key, value in sorted(_scratch_labels(binding).items())
                    for item in ("--label", f"{key}={value}")
                ],
                volume,
            ],
            timeout=60,
        )
        if observed_volume != volume:
            raise SourceSnapshotError(
                "scratch volume creation identity differs"
            )
        volume_document = _inspect_required("volume", volume)
        _validate_scratch_volume(
            volume_document,
            binding=binding,
            name=volume,
        )
        run_arguments = [
            DOCKER,
            "run",
            "--detach",
            "--name",
            container,
            "--network",
            "none",
            "--pull",
            "never",
            "--restart",
            "no",
            *[
                item
                for key, value in sorted(_scratch_labels(binding).items())
                for item in ("--label", f"{key}={value}")
            ],
            "--mount",
            (
                f"type=volume,source={volume},"
                "target=/var/lib/postgresql/data"
            ),
            "--env",
            "POSTGRES_USER=restore",
            "--env",
            "POSTGRES_DB=restore",
            "--env",
            "POSTGRES_HOST_AUTH_METHOD=trust",
            postgres_image.image_id,
        ]
        observed_container = _run(run_arguments, timeout=120)
        if CONTAINER_ID_RE.fullmatch(observed_container) is None:
            raise SourceSnapshotError(
                "scratch container creation identity differs"
            )
        container_document = _inspect_required("container", container)
        identifier = _validate_scratch_container(
            container_document,
            binding=binding,
            name=container,
            volume=volume,
            expected_image=postgres_image,
        )
        if identifier != observed_container:
            raise SourceSnapshotError(
                "scratch container ID changed after creation"
            )
        ready = False
        for _attempt in range(120):
            probe = subprocess.run(
                [
                    DOCKER,
                    "exec",
                    container,
                    "pg_isready",
                    "-U",
                    "restore",
                    "-d",
                    "restore",
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                env=SAFE_ENV,
                check=False,
            )
            if probe.returncode == 0:
                try:
                    if _scratch_query(container, "SELECT 1") == "1":
                        ready = True
                        break
                except SourceSnapshotError:
                    pass
            time.sleep(1)
        if not ready:
            raise SourceSnapshotError(
                "scratch restore database did not become ready"
            )
        _restore_dump(container, dump)
        source_database = build_source_database(
            query=lambda sql: _scratch_query(container, sql),
            stream_copy=lambda sql: _scratch_stream(container, sql),
        )
        system_id = _scratch_query(
            container,
            "SELECT system_identifier FROM pg_control_system()",
        )
        if re.fullmatch(r"[0-9]{10,20}", system_id) is None:
            raise SourceSnapshotError(
                "scratch PostgreSQL system identity is invalid"
            )
        result = {
            "status": "passed",
            "postgres_image_reference": postgres_image.reference,
            "postgres_image_id": postgres_image.image_id,
            "postgres_runtime_uid": POSTGRES_RUNTIME_UID,
            "postgres_runtime_gid": POSTGRES_RUNTIME_GID,
            "scratch_postgres_system_id": system_id,
            "single_transaction": True,
            "network_mode": "none",
            "pull_policy": "never",
            "source_or_current_mounted": False,
        }
    except Exception as exc:
        failure = exc
    cleanup_error: Exception | None = None
    try:
        cleanup_exact_scratch(
            binding,
            expected_image=postgres_image,
        )
    except Exception as exc:
        cleanup_error = exc
    if cleanup_error is not None:
        raise SourceSnapshotError(
            "validated scratch cleanup failed closed"
        ) from cleanup_error
    if failure is not None:
        if isinstance(failure, SourceSnapshotError):
            raise failure
        raise SourceSnapshotError(
            "source snapshot restore drill failed closed"
        ) from failure
    if result is None:
        raise SourceSnapshotError(
            "source snapshot restore drill produced no result"
        )
    return source_database, {
        **result,
        "recovered_prior_residue": recovered,
        "scratch_resources_removed": True,
        "zero_residue": True,
    }


def _secure_directory(path: Path, *, label: str) -> os.stat_result:
    try:
        metadata = path.stat(follow_symlinks=False)
        descriptor = _open_absolute_directory(path)
        try:
            held = os.fstat(descriptor)
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise SourceSnapshotError(f"{label} is unavailable") from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or _directory_stat_fields(metadata) != _directory_stat_fields(held)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise SourceSnapshotError(f"{label} is not an exact root-only directory")
    return metadata


def _path_overlaps(first: Path, second: Path) -> bool:
    first_text = os.path.abspath(first)
    second_text = os.path.abspath(second)
    try:
        common = os.path.commonpath((first_text, second_text))
    except ValueError as exc:
        raise SourceSnapshotError("snapshot path identity is invalid") from exc
    return common in {first_text, second_text}


def _validate_output_separation(
    output_root: Path,
    inventory: SourceInventory,
) -> None:
    _secure_directory(output_root, label="snapshot output root")
    if any(_path_overlaps(output_root, path) for path in FORBIDDEN_OUTPUT_ROOTS):
        raise SourceSnapshotError(
            "snapshot output overlaps a source, current, or Docker data root"
        )
    for volume in inventory.volumes.values():
        mountpoint = Path(str(volume["mountpoint"]))
        if _path_overlaps(output_root, mountpoint):
            raise SourceSnapshotError(
                "snapshot output overlaps a source volume mountpoint"
            )


def _ensure_private_child(parent: Path, name: str, *, label: str) -> Path:
    _secure_directory(parent, label=f"{label} parent")
    target = parent / name
    try:
        target.mkdir(mode=0o700)
    except FileExistsError:
        pass
    except OSError as exc:
        raise SourceSnapshotError(f"{label} cannot be created") from exc
    _secure_directory(target, label=label)
    return target


def _remove_staging(path: Path) -> None:
    try:
        metadata = path.stat(follow_symlinks=False)
    except FileNotFoundError:
        return
    except OSError as exc:
        raise SourceSnapshotError(
            "incomplete operation output is unavailable"
        ) from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise SourceSnapshotError("incomplete operation output is unsafe")
    allowed = set(ARTIFACT_FILES.values()) | {MANIFEST_FILE}
    try:
        entries = list(os.scandir(path))
    except OSError as exc:
        raise SourceSnapshotError(
            "incomplete operation output cannot be enumerated"
        ) from exc
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        for entry in entries:
            if entry.name not in allowed:
                raise SourceSnapshotError(
                    "incomplete operation output contains an unexpected path"
                )
            item = os.stat(
                entry.name,
                dir_fd=descriptor,
                follow_symlinks=False,
            )
            if (
                not stat.S_ISREG(item.st_mode)
                or item.st_uid != 0
                or item.st_nlink != 1
                or stat.S_IMODE(item.st_mode) != 0o600
            ):
                raise SourceSnapshotError(
                    "incomplete operation output contains an unsafe artifact"
                )
            os.unlink(entry.name, dir_fd=descriptor)
        os.fsync(descriptor)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    try:
        path.rmdir()
    except OSError as exc:
        raise SourceSnapshotError(
            "incomplete operation output cannot be removed"
        ) from exc


def _prepare_staging(paths: OutputPaths, output_root: Path) -> None:
    _secure_directory(output_root, label="snapshot output root")
    _ensure_private_child(
        output_root,
        paths.operation_root.name,
        label="snapshot operation root",
    )
    _ensure_private_child(
        paths.operation_root,
        paths.role_root.name,
        label="snapshot role root",
    )
    if paths.final.exists() or paths.final.is_symlink():
        raise SourceSnapshotError("snapshot final output already exists")
    _remove_staging(paths.staging)
    try:
        paths.staging.mkdir(mode=0o700)
    except OSError as exc:
        raise SourceSnapshotError(
            "snapshot staging output cannot be created"
        ) from exc
    _secure_directory(paths.staging, label="snapshot staging output")


def _publish_staging(paths: OutputPaths) -> None:
    if paths.final.exists() or paths.final.is_symlink():
        raise SourceSnapshotError("snapshot final output already exists")
    try:
        staging_descriptor = os.open(
            paths.staging,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            os.fsync(staging_descriptor)
        finally:
            os.close(staging_descriptor)
        libc = ctypes.CDLL(None, use_errno=True)
        renameat2 = getattr(libc, "renameat2", None)
        if renameat2 is None:
            raise SourceSnapshotError(
                "create-only directory publication is unavailable"
            )
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
            os.fsencode(paths.staging),
            -100,
            os.fsencode(paths.final),
            1,
        )
        if result != 0:
            observed_errno = ctypes.get_errno()
            if observed_errno in {errno.EEXIST, errno.ENOTEMPTY}:
                raise SourceSnapshotError(
                    "snapshot final output already exists"
                )
            raise OSError(
                observed_errno,
                os.strerror(observed_errno),
                str(paths.final),
            )
        descriptor = os.open(
            paths.role_root,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise SourceSnapshotError(
            "snapshot output publication failed"
        ) from exc


def _source_public_inventory(inventory: SourceInventory) -> dict[str, Any]:
    return {
        "containers": dict(sorted(inventory.containers.items())),
        "images": {
            kind: {
                "reference": value.reference,
                "image_id": value.image_id,
            }
            for kind, value in sorted(inventory.images.items())
        },
        "volumes": dict(sorted(inventory.volumes.items())),
        "identity_sha256": inventory.canonical_sha256,
    }


def _manifest_document(
    binding: SnapshotBinding,
    *,
    inventory: SourceInventory,
    freeze_sha256: str | None,
    database: tuple[str, int],
    uploads: FileSnapshot,
    audit: FileSnapshot,
    redis: Mapping[str, Any],
    source_database: Mapping[str, Any],
    restore: Mapping[str, Any],
) -> dict[str, Any]:
    file_snapshots = {
        "uploads": {
            "source_volume": binding.volumes["uploads"],
            "pre_tree_sha256": uploads.tree_sha256,
            "archive_tree_sha256": uploads.tree_sha256,
            "post_tree_sha256": uploads.tree_sha256,
            "member_count": uploads.member_count,
            "expanded_bytes": uploads.expanded_bytes,
            "stable_attempt": uploads.stable_attempt,
        },
        "audit": {
            "source_volume": binding.volumes["audit"],
            "pre_tree_sha256": audit.tree_sha256,
            "archive_tree_sha256": audit.tree_sha256,
            "post_tree_sha256": audit.tree_sha256,
            "member_count": audit.member_count,
            "expanded_bytes": audit.expanded_bytes,
            "stable_attempt": audit.stable_attempt,
        },
    }
    return {
        "schema": MANIFEST_SCHEMA,
        "status": "source-snapshot-created",
        "operation_id": binding.operation_id,
        "role": binding.role,
        "mode": binding.mode,
        "release_sha": binding.release_sha,
        "legacy_release_sha": binding.legacy_release_sha,
        "source_project": binding.source_project,
        "controller_manifest_sha256": binding.controller_manifest_sha256,
        "approval_sha256": binding.approval_sha256,
        "binding_sha256": binding.canonical_sha256,
        "freeze_evidence_sha256": freeze_sha256,
        "source": _source_public_inventory(inventory),
        "artifacts": {
            "database-backup": {
                "sha256": database[0],
                "bytes": database[1],
                "restored_tree_sha256": None,
            },
            "uploads-archive": {
                "sha256": uploads.artifact_sha256,
                "bytes": uploads.artifact_bytes,
                "restored_tree_sha256": uploads.tree_sha256,
            },
            "audit-archive": {
                "sha256": audit.artifact_sha256,
                "bytes": audit.artifact_bytes,
                "restored_tree_sha256": audit.tree_sha256,
            },
        },
        "source_database": dict(source_database),
        "file_snapshots": file_snapshots,
        "redis_rollback_only": dict(redis),
        "restore_drill": dict(restore),
        "source_mutated": False,
        "current_mutated": False,
        "source_stopped_or_restarted": False,
        "redis_restored": False,
    }


def _validate_source_database(value: Any) -> None:
    if (
        not isinstance(value, dict)
        or set(value) != SOURCE_DATABASE_FIELDS
        or not isinstance(value["alembic_revision"], str)
        or REVISION_RE.fullmatch(value["alembic_revision"]) is None
        or value["fingerprint_algorithm"] != DATABASE_FINGERPRINT_ALGORITHM
        or type(value["row_count"]) is not int
        or not 0 <= value["row_count"] <= 10**15
        or type(value["table_count"]) is not int
        or not 1 <= value["table_count"] <= 100_000
    ):
        raise SourceSnapshotError("snapshot source database binding is invalid")
    _nonzero_sha256(
        value["database_fingerprint_sha256"],
        label="source database fingerprint",
    )


def _validate_completed_source(
    value: Any,
    binding: SnapshotBinding,
) -> None:
    if not isinstance(value, dict) or set(value) != SOURCE_FIELDS:
        raise SourceSnapshotError(
            "completed source Docker inventory fields differ"
        )
    containers = value["containers"]
    images = value["images"]
    volumes = value["volumes"]
    if (
        not isinstance(containers, dict)
        or set(containers) != set(SOURCE_CONTAINERS)
        or not isinstance(images, dict)
        or set(images) != set(IMAGE_KEYS)
        or not isinstance(volumes, dict)
        or set(volumes) != set(VOLUME_KEYS)
    ):
        raise SourceSnapshotError(
            "completed source Docker inventory is incomplete"
        )
    for kind, row in images.items():
        if (
            not isinstance(row, dict)
            or set(row) != SOURCE_IMAGE_FIELDS
            or row["reference"] != binding.images[kind]
            or not isinstance(row["image_id"], str)
            or IMAGE_ID_RE.fullmatch(row["image_id"]) is None
            or row["image_id"] == "sha256:" + "0" * 64
        ):
            raise SourceSnapshotError(
                "completed source image identity differs"
            )
    for kind, row in volumes.items():
        if (
            not isinstance(row, dict)
            or set(row) != SOURCE_VOLUME_FIELDS
            or row["name"] != binding.volumes[kind]
            or row["driver"] != "local"
            or not isinstance(row["mountpoint"], str)
            or not Path(row["mountpoint"]).is_absolute()
            or not isinstance(row["labels_sha256"], str)
            or SHA256_RE.fullmatch(row["labels_sha256"]) is None
            or not isinstance(row["options_sha256"], str)
            or SHA256_RE.fullmatch(row["options_sha256"]) is None
        ):
            raise SourceSnapshotError(
                "completed source volume identity differs"
            )
    for kind, row in containers.items():
        if (
            not isinstance(row, dict)
            or set(row) != SOURCE_CONTAINER_FIELDS
            or row["name"] != binding.containers[kind]
            or row["project"] != binding.source_project
            or row["service"] != SOURCE_SERVICES[kind]
            or row["image_reference"] != binding.images[kind]
            or row["image_id"] != images[kind]["image_id"]
            or not isinstance(row["id"], str)
            or CONTAINER_ID_RE.fullmatch(row["id"]) is None
            or row["id"] == "0" * 64
            or type(row["running"]) is not bool
            or not isinstance(row["started_at"], str)
            or not 1 <= len(row["started_at"]) <= 128
            or type(row["restart_count"]) is not int
            or row["restart_count"] < 0
            or not isinstance(row["mounts"], dict)
            or set(row["mounts"]) != set(SOURCE_MOUNTS[kind])
            or type(row["other_mount_count"]) is not int
            or not 0 <= row["other_mount_count"] <= 128
            or not isinstance(row["other_mounts_sha256"], str)
            or SHA256_RE.fullmatch(row["other_mounts_sha256"]) is None
        ):
            raise SourceSnapshotError(
                "completed source container identity differs"
            )
        if kind == "database" and row["running"] is not True:
            raise SourceSnapshotError(
                "completed source database state differs"
            )
        for volume_kind, mount in row["mounts"].items():
            if (
                not isinstance(mount, dict)
                or set(mount) != SOURCE_MOUNT_FIELDS
                or mount["name"] != binding.volumes[volume_kind]
                or mount["source"] != volumes[volume_kind]["mountpoint"]
                or mount["destination"] != SOURCE_MOUNTS[kind][volume_kind]
                or mount["rw"] is not True
            ):
                raise SourceSnapshotError(
                    "completed source container mount differs"
                )
    public = {
        "containers": containers,
        "images": images,
        "volumes": volumes,
    }
    if (
        not isinstance(value["identity_sha256"], str)
        or SHA256_RE.fullmatch(value["identity_sha256"]) is None
        or hashlib.sha256(_canonical_json(public)).hexdigest()
        != value["identity_sha256"]
    ):
        raise SourceSnapshotError(
            "completed source Docker identity hash differs"
        )


def _validate_completed_redis(
    value: Any,
    binding: SnapshotBinding,
) -> None:
    if (
        not isinstance(value, dict)
        or set(value) != REDIS_ROLLBACK_FIELDS
        or value["policy"] != "sealed-rollback-evidence-only"
        or value["source_volume"] != binding.volumes["redis"]
        or not isinstance(value["tree_sha256"], str)
        or SHA256_RE.fullmatch(value["tree_sha256"]) is None
        or not isinstance(value["metadata_sha256"], str)
        or SHA256_RE.fullmatch(value["metadata_sha256"]) is None
        or type(value["member_count"]) is not int
        or not 0 <= value["member_count"] <= MAX_TREE_MEMBERS
        or type(value["bytes"]) is not int
        or not 0 <= value["bytes"] <= MAX_ARTIFACT_BYTES
        or type(value["stable_attempt"]) is not int
        or not 1 <= value["stable_attempt"] <= MAX_SNAPSHOT_ATTEMPTS
        or value["archive_created"] is not False
        or value["restore"] is not False
    ):
        raise SourceSnapshotError(
            "completed Redis rollback metadata differs"
        )


def _validate_completed_restore(
    value: Any,
    binding: SnapshotBinding,
    source: Mapping[str, Any],
) -> None:
    if (
        not isinstance(value, dict)
        or set(value) != RESTORE_FIELDS
        or value["status"] != "passed"
        or value["postgres_image_reference"]
        != binding.images["restore_postgres"]
        or value["postgres_image_id"]
        != source["images"]["restore_postgres"]["image_id"]
        or value["postgres_runtime_uid"] != POSTGRES_RUNTIME_UID
        or value["postgres_runtime_gid"] != POSTGRES_RUNTIME_GID
        or not isinstance(value["scratch_postgres_system_id"], str)
        or re.fullmatch(
            r"[0-9]{10,20}",
            value["scratch_postgres_system_id"],
        )
        is None
        or value["single_transaction"] is not True
        or value["network_mode"] != "none"
        or value["pull_policy"] != "never"
        or value["source_or_current_mounted"] is not False
        or type(value["recovered_prior_residue"]) is not bool
        or value["scratch_resources_removed"] is not True
        or value["zero_residue"] is not True
    ):
        raise SourceSnapshotError(
            "completed restore drill binding differs"
        )


def verify_completed_output(
    paths: OutputPaths,
    binding: SnapshotBinding,
    *,
    freeze_sha256: str | None,
) -> dict[str, Any]:
    _secure_directory(paths.final, label="snapshot final output")
    document, _digest = _secure_canonical_json(
        paths.manifest,
        label="source snapshot manifest",
        fields=MANIFEST_FIELDS,
    )
    expected = {
        "schema": MANIFEST_SCHEMA,
        "status": "source-snapshot-created",
        "operation_id": binding.operation_id,
        "role": binding.role,
        "mode": binding.mode,
        "release_sha": binding.release_sha,
        "legacy_release_sha": binding.legacy_release_sha,
        "source_project": binding.source_project,
        "controller_manifest_sha256": binding.controller_manifest_sha256,
        "approval_sha256": binding.approval_sha256,
        "binding_sha256": binding.canonical_sha256,
        "freeze_evidence_sha256": freeze_sha256,
        "source_mutated": False,
        "current_mutated": False,
        "source_stopped_or_restarted": False,
        "redis_restored": False,
    }
    if any(
        type(document.get(key)) is not type(value)
        or document.get(key) != value
        for key, value in expected.items()
    ):
        raise SourceSnapshotError(
            "completed source snapshot binding differs"
        )
    _validate_completed_source(document["source"], binding)
    artifacts = document["artifacts"]
    if (
        not isinstance(artifacts, dict)
        or set(artifacts) != set(ARTIFACT_FILES)
        or not isinstance(document["file_snapshots"], dict)
        or set(document["file_snapshots"]) != {"uploads", "audit"}
    ):
        raise SourceSnapshotError(
            "completed source snapshot artifact set differs"
        )
    for kind, filename in ARTIFACT_FILES.items():
        row = artifacts[kind]
        if not isinstance(row, dict) or set(row) != ARTIFACT_FIELDS:
            raise SourceSnapshotError(
                "completed source snapshot artifact fields differ"
            )
        expected_hash = _nonzero_sha256(row["sha256"], label=kind)
        if (
            type(row["bytes"]) is not int
            or not 1 <= row["bytes"] <= MAX_ARTIFACT_BYTES
            or _hash_secure_artifact(paths.final / filename)
            != (expected_hash, row["bytes"])
        ):
            raise SourceSnapshotError(
                f"completed {kind} artifact differs"
            )
        if kind == "database-backup":
            if row["restored_tree_sha256"] is not None:
                raise SourceSnapshotError(
                    "database artifact tree digest must be null"
                )
        else:
            tree = _nonzero_sha256(
                row["restored_tree_sha256"],
                label=f"{kind} restored tree",
            )
            snapshot_name = kind.removesuffix("-archive")
            file_snapshot = document["file_snapshots"].get(snapshot_name)
            if (
                not isinstance(file_snapshot, dict)
                or set(file_snapshot) != FILE_SNAPSHOT_FIELDS
                or file_snapshot["source_volume"]
                != binding.volumes[snapshot_name]
                or any(
                    file_snapshot[field] != tree
                    for field in (
                        "pre_tree_sha256",
                        "archive_tree_sha256",
                        "post_tree_sha256",
                    )
                )
                or type(file_snapshot["member_count"]) is not int
                or not 1
                <= file_snapshot["member_count"]
                <= MAX_TREE_MEMBERS
                or type(file_snapshot["expanded_bytes"]) is not int
                or not 0
                <= file_snapshot["expanded_bytes"]
                <= MAX_ARTIFACT_BYTES
                or type(file_snapshot["stable_attempt"]) is not int
                or not 1
                <= file_snapshot["stable_attempt"]
                <= MAX_SNAPSHOT_ATTEMPTS
            ):
                raise SourceSnapshotError(
                    f"completed {kind} tree binding differs"
                )
            _validate_archive_shape(paths.final / filename)
    _validate_source_database(document["source_database"])
    _validate_completed_redis(document["redis_rollback_only"], binding)
    _validate_completed_restore(
        document["restore_drill"],
        binding,
        document["source"],
    )
    return document


def _validate_archive_shape(path: Path) -> None:
    count = 0
    names: set[str] = set()
    expanded = 0
    try:
        with tarfile.open(path, mode="r:gz") as archive:
            for member in archive:
                count += 1
                candidate = PurePosixPath(member.name.rstrip("/"))
                name = candidate.as_posix()
                expanded += member.size
                if (
                    count > MAX_TREE_MEMBERS
                    or expanded > MAX_ARTIFACT_BYTES
                    or candidate.is_absolute()
                    or not candidate.parts
                    or ".." in candidate.parts
                    or name in names
                    or member.issym()
                    or member.islnk()
                    or member.isdev()
                    or not (member.isdir() or member.isfile())
                ):
                    raise SourceSnapshotError(
                        "completed archive contains an unsafe member"
                    )
                names.add(name)
        if count == 0:
            raise SourceSnapshotError("completed archive is empty")
    except SourceSnapshotError:
        raise
    except (OSError, EOFError, tarfile.TarError) as exc:
        raise SourceSnapshotError("completed archive is invalid") from exc


@contextmanager
def operation_lock(binding: SnapshotBinding) -> Iterator[None]:
    try:
        lock_root_metadata = LOCK_ROOT.stat(follow_symlinks=False)
    except OSError as exc:
        raise SourceSnapshotError("snapshot lock root is unavailable") from exc
    if (
        not LOCK_ROOT.is_absolute()
        or not stat.S_ISDIR(lock_root_metadata.st_mode)
        or lock_root_metadata.st_uid != 0
        or stat.S_IMODE(lock_root_metadata.st_mode) & 0o022
    ):
        raise SourceSnapshotError("snapshot lock root is unsafe")
    name = (
        "trading-bot-production-source-snapshot-"
        f"{binding.operation_id}-{binding.role}.lock"
    )
    descriptor = -1
    try:
        descriptor = os.open(
            LOCK_ROOT / name,
            os.O_RDWR
            | os.O_CREAT
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != 0
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            raise SourceSnapshotError("snapshot operation lock is unsafe")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise SourceSnapshotError(
                "another source snapshot operation is active"
            ) from exc
        yield
    except SourceSnapshotError:
        raise
    except OSError as exc:
        raise SourceSnapshotError(
            "snapshot operation lock is unavailable"
        ) from exc
    finally:
        if descriptor >= 0:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            except OSError:
                pass
            os.close(descriptor)


def execute(
    binding: SnapshotBinding,
    *,
    output_root: Path,
    freeze_path: Path | None,
    freeze_sha256: str | None,
) -> dict[str, Any]:
    paths = output_paths(output_root, binding)
    _secure_directory(output_root, label="snapshot output root")
    if any(_path_overlaps(output_root, path) for path in FORBIDDEN_OUTPUT_ROOTS):
        raise SourceSnapshotError(
            "snapshot output overlaps a source, current, or Docker data root"
        )
    if paths.final.exists() and not paths.final.is_symlink():
        cleanup_exact_scratch(binding)
        completed = verify_completed_output(
            paths,
            binding,
            freeze_sha256=freeze_sha256,
        )
        return {
            "schema": MANIFEST_SCHEMA,
            "status": "resume-verified",
            "operation_id": binding.operation_id,
            "role": binding.role,
            "mode": binding.mode,
            "manifest": str(paths.manifest),
            "manifest_sha256": hashlib.sha256(
                _canonical_json(completed)
            ).hexdigest(),
            "zero_residue": True,
        }
    held: dict[str, HeldVolume] = {}
    try:
        inventory_before = inspect_source(binding)
        _validate_output_separation(output_root, inventory_before)
        _prepare_staging(paths, output_root)
        container_ids = {
            kind: str(row["id"])
            for kind, row in inventory_before.containers.items()
        }
        if binding.mode == "frozen-final":
            if freeze_path is None or freeze_sha256 is None:
                raise SourceSnapshotError(
                    "frozen-final mode requires freeze evidence"
                )
            _freeze, observed_freeze_sha256 = load_freeze_evidence(
                freeze_path,
                binding,
                source_container_ids=container_ids,
            )
            if observed_freeze_sha256 != freeze_sha256:
                raise SourceSnapshotError(
                    "source freeze evidence changed before apply"
                )
        for kind in VOLUME_KEYS:
            held[kind] = hold_volume(
                kind,
                inventory_before.volumes[kind],
            )
        user, database_name = _source_database_environment(binding)
        database_path = paths.staging / ARTIFACT_FILES["database-backup"]
        database_artifact = create_database_dump(
            binding,
            database_path,
            user=user,
            database=database_name,
        )
        uploads = snapshot_file_volume(
            held["uploads"],
            paths.staging / ARTIFACT_FILES["uploads-archive"],
            binding,
        )
        audit = snapshot_file_volume(
            held["audit"],
            paths.staging / ARTIFACT_FILES["audit-archive"],
            binding,
        )
        redis = redis_rollback_metadata(held["redis"], binding)
        source_database, restore = restore_and_fingerprint(
            binding,
            dump=database_path,
            postgres_image=inventory_before.images["restore_postgres"],
        )
        for value in held.values():
            verify_held_volume(value, binding)
        inventory_after = inspect_source(binding)
        if inventory_after.canonical_sha256 != inventory_before.canonical_sha256:
            raise SourceSnapshotError(
                "source Docker identity changed during snapshot"
            )
        manifest = _manifest_document(
            binding,
            inventory=inventory_before,
            freeze_sha256=freeze_sha256,
            database=database_artifact,
            uploads=uploads,
            audit=audit,
            redis=redis,
            source_database=source_database,
            restore=restore,
        )
        _write_staging_manifest(
            paths.staging / MANIFEST_FILE,
            _canonical_json(manifest),
        )
        _publish_staging(paths)
        verified = verify_completed_output(
            paths,
            binding,
            freeze_sha256=freeze_sha256,
        )
        return {
            "schema": MANIFEST_SCHEMA,
            "status": "applied",
            "operation_id": binding.operation_id,
            "role": binding.role,
            "mode": binding.mode,
            "manifest": str(paths.manifest),
            "manifest_sha256": hashlib.sha256(
                _canonical_json(verified)
            ).hexdigest(),
            "zero_residue": True,
            "source_mutated": False,
            "redis_restored": False,
        }
    except Exception:
        try:
            cleanup_exact_scratch(binding)
        except Exception as cleanup_error:
            raise SourceSnapshotError(
                "source snapshot failed and scratch cleanup failed closed"
            ) from cleanup_error
        raise
    finally:
        for value in held.values():
            os.close(value.descriptor)


def _error_payload(message: str) -> dict[str, str]:
    return {
        "status": "blocked",
        "error": message,
        "error_class": "SourceSnapshotError",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binding", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--freeze-evidence", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm")
    args = parser.parse_args(argv)
    try:
        binding = load_binding(args.binding)
        freeze_sha256: str | None = None
        if binding.mode == "frozen-final":
            if args.freeze_evidence is None:
                raise SourceSnapshotError(
                    "frozen-final mode requires --freeze-evidence"
                )
            _freeze, freeze_sha256 = load_freeze_evidence(
                args.freeze_evidence,
                binding,
            )
        elif args.freeze_evidence is not None:
            raise SourceSnapshotError(
                "freeze evidence is valid only for frozen-final mode"
            )
        if not args.apply:
            if args.confirm is not None:
                raise SourceSnapshotError(
                    "--confirm is valid only with --apply"
                )
            result = build_plan(
                binding,
                output_root=args.output_root,
                freeze_evidence_sha256=freeze_sha256,
            )
        else:
            required = confirmation_phrase(binding)
            if args.confirm != required:
                raise SourceSnapshotError(
                    f"source snapshot requires --confirm {required}"
                )
            if os.geteuid() != 0:
                raise SourceSnapshotError(
                    "source snapshot apply must run as root"
                )
            with operation_lock(binding):
                result = execute(
                    binding,
                    output_root=args.output_root,
                    freeze_path=args.freeze_evidence,
                    freeze_sha256=freeze_sha256,
                )
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0
    except SourceSnapshotError as exc:
        print(
            json.dumps(
                _error_payload(str(exc)),
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 1
    except Exception:
        print(
            json.dumps(
                _error_payload("source snapshot failed closed"),
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
