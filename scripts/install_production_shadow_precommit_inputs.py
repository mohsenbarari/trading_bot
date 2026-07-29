#!/usr/bin/env python3
"""Install one FI production-shadow role's immutable precommit inputs.

The default invocation is validation-only. Apply mode creates only
operation-derived directories and files, publishes the worker manifest last,
and never invokes Docker, SSH, a network client, or a service command.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import ctypes
from dataclasses import dataclass
import errno
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import stat
import sys
import tarfile
from typing import Any, Iterator, Mapping, Sequence
from uuid import UUID

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.docker_image_identity import (
    DockerImageIdentityError,
    verify_content_descriptor,
)
from scripts import production_shadow_precommit_worker as WORKER
from scripts import produce_production_shadow_prepare_material as PREPARE
from scripts import produce_production_shadow_source_snapshot as SOURCE


ROOT_UID = 0
ROOT_GID = 0
FILE_MODE = 0o600
PRIVATE_DIRECTORY_MODE = 0o700
MAX_ROLE_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_JSON_BYTES = 2 * 1024 * 1024
RENAME_NOREPLACE = 1
SOURCE_ARTIFACT_FILES = {
    "database-backup": "database.dump",
    "uploads-archive": "uploads.tar.gz",
    "audit-archive": "audit.tar.gz",
}
ROLE_ARCHIVE_MEMBERS = frozenset(
    {
        PREPARE.FINAL_PREPARE_MANIFEST_NAME,
        "role-compose.yml",
        "runtime.env.role",
        "ca.crt",
    }
)


class PrecommitInputInstallError(RuntimeError):
    """Raised when a precommit installation cannot be proven exact."""


@dataclass(frozen=True)
class FileIdentity:
    path: Path
    sha256: str
    bytes: int
    device: int
    inode: int


@dataclass(frozen=True)
class SourceArtifact:
    kind: str
    path: Path
    sha256: str
    bytes: int
    restored_tree_sha256: str | None
    identity: FileIdentity


@dataclass(frozen=True)
class OutputSpec:
    kind: str
    path: Path
    sha256: str
    bytes: int
    payload: bytes | None = None
    source: Path | None = None


@dataclass(frozen=True)
class OutputState:
    destination: str
    temporary: str


@dataclass(frozen=True)
class InstallationPlan:
    manifest_document: Mapping[str, Any]
    manifest_payload: bytes
    manifest: WORKER.PrecommitManifest
    paths: WORKER.OperationPaths
    role_material_payload: bytes
    role_material_identity: FileIdentity
    role_payloads: Mapping[str, bytes]
    source_snapshot_document: Mapping[str, Any]
    source_snapshot_sha256: str
    source_snapshot_identity: FileIdentity
    source_artifacts: Mapping[str, SourceArtifact]
    outputs: tuple[OutputSpec, ...]
    output_states: Mapping[str, OutputState]


def _canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise PrecommitInputInstallError(
            "document contains non-canonical JSON data"
        ) from exc


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PrecommitInputInstallError("JSON contains a duplicate key")
        result[key] = value
    return result


def _canonical_path(path: Path, *, label: str) -> Path:
    if (
        not isinstance(path, Path)
        or not path.is_absolute()
        or path.name in {"", ".", ".."}
        or path != Path(os.path.abspath(os.fspath(path)))
    ):
        raise PrecommitInputInstallError(
            f"{label} must be an absolute canonical path"
        )
    return path


def _canonical_uuid4(value: Any) -> str:
    if not isinstance(value, str):
        raise PrecommitInputInstallError(
            "operation ID must be a canonical UUIDv4"
        )
    try:
        parsed = UUID(value)
    except (AttributeError, ValueError) as exc:
        raise PrecommitInputInstallError(
            "operation ID must be a canonical UUIDv4"
        ) from exc
    if parsed.version != 4 or str(parsed) != value:
        raise PrecommitInputInstallError(
            "operation ID must be a canonical UUIDv4"
        )
    return value


def _nonzero_sha256(value: Any, *, label: str) -> str:
    if (
        not isinstance(value, str)
        or WORKER.SHA256_RE.fullmatch(value) is None
        or value == "0" * 64
    ):
        raise PrecommitInputInstallError(
            f"{label} must be a nonzero SHA-256"
        )
    return value


def _bounded_int(
    value: Any,
    *,
    minimum: int,
    maximum: int,
    label: str,
) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not minimum <= value <= maximum
    ):
        raise PrecommitInputInstallError(f"{label} is outside its bound")
    return value


def _directory_flags() -> int:
    if not hasattr(os, "O_DIRECTORY") or not hasattr(os, "O_NOFOLLOW"):
        raise PrecommitInputInstallError(
            "secure no-follow directory traversal is unavailable"
        )
    return (
        os.O_RDONLY
        | os.O_DIRECTORY
        | getattr(os, "O_CLOEXEC", 0)
        | os.O_NOFOLLOW
    )


def _open_parent(
    path: Path,
    *,
    label: str,
    missing_ok: bool = False,
) -> tuple[int, str] | None:
    flags = _directory_flags()
    descriptor = -1
    try:
        descriptor = os.open("/", flags)
        for component in path.parts[1:-1]:
            try:
                next_descriptor = os.open(
                    component,
                    flags,
                    dir_fd=descriptor,
                )
            except FileNotFoundError:
                if missing_ok:
                    os.close(descriptor)
                    return None
                raise
            metadata = os.fstat(next_descriptor)
            if not stat.S_ISDIR(metadata.st_mode):
                os.close(next_descriptor)
                raise PrecommitInputInstallError(
                    f"{label} parent traversal is unsafe"
                )
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor, path.name
    except PrecommitInputInstallError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError as exc:
        if descriptor >= 0:
            os.close(descriptor)
        raise PrecommitInputInstallError(
            f"{label} parent traversal is unsafe"
        ) from exc


def _stable_metadata(metadata: os.stat_result) -> tuple[int, ...]:
    return tuple(
        getattr(metadata, field)
        for field in (
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
    )


def _assert_root_directory(
    descriptor: int,
    *,
    label: str,
    exact_mode: int | None = None,
) -> os.stat_result:
    metadata = os.fstat(descriptor)
    mode = stat.S_IMODE(metadata.st_mode)
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != ROOT_UID
        or metadata.st_gid != ROOT_GID
        or (
            mode != exact_mode
            if exact_mode is not None
            else bool(mode & 0o022)
        )
    ):
        raise PrecommitInputInstallError(f"{label} directory is unsafe")
    return metadata


def _open_root_directory(
    path: Path,
    *,
    label: str,
    exact_mode: int | None = None,
) -> int:
    canonical = _canonical_path(path, label=label)
    opened = _open_parent(canonical, label=label)
    if opened is None:
        raise PrecommitInputInstallError(f"{label} directory is unavailable")
    parent_fd, name = opened
    descriptor = -1
    try:
        descriptor = os.open(name, _directory_flags(), dir_fd=parent_fd)
        _assert_root_directory(
            descriptor,
            label=label,
            exact_mode=exact_mode,
        )
        return descriptor
    except PrecommitInputInstallError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError as exc:
        if descriptor >= 0:
            os.close(descriptor)
        raise PrecommitInputInstallError(
            f"{label} directory is unavailable or unsafe"
        ) from exc
    finally:
        os.close(parent_fd)


def _read_secure_leaf(
    directory_fd: int,
    name: str,
    *,
    path: Path,
    label: str,
    maximum: int,
) -> tuple[bytes, FileIdentity]:
    descriptor = -1
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY
            | os.O_NONBLOCK
            | getattr(os, "O_CLOEXEC", 0)
            | os.O_NOFOLLOW,
            dir_fd=directory_fd,
        )
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != ROOT_UID
            or before.st_gid != ROOT_GID
            or stat.S_IMODE(before.st_mode) != FILE_MODE
            or before.st_nlink != 1
            or not 1 <= before.st_size <= maximum
        ):
            raise PrecommitInputInstallError(
                f"{label} is not an exact root-only 0600 file"
            )
        chunks: list[bytes] = []
        remaining = maximum + 1
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        after = os.fstat(descriptor)
        visible = os.stat(
            name,
            dir_fd=directory_fd,
            follow_symlinks=False,
        )
        if (
            len(payload) != before.st_size
            or len(payload) > maximum
            or _stable_metadata(before) != _stable_metadata(after)
            or _stable_metadata(after) != _stable_metadata(visible)
        ):
            raise PrecommitInputInstallError(
                f"{label} changed while being read"
            )
        digest = hashlib.sha256(payload).hexdigest()
        return payload, FileIdentity(
            path=path,
            sha256=digest,
            bytes=len(payload),
            device=after.st_dev,
            inode=after.st_ino,
        )
    except PrecommitInputInstallError:
        raise
    except OSError as exc:
        raise PrecommitInputInstallError(
            f"{label} is unavailable or unsafe"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _read_secure_path(
    path: Path,
    *,
    label: str,
    maximum: int,
) -> tuple[bytes, FileIdentity]:
    canonical = _canonical_path(path, label=label)
    opened = _open_parent(canonical, label=label)
    if opened is None:
        raise PrecommitInputInstallError(f"{label} is unavailable")
    directory_fd, name = opened
    try:
        return _read_secure_leaf(
            directory_fd,
            name,
            path=canonical,
            label=label,
            maximum=maximum,
        )
    finally:
        os.close(directory_fd)


def _hash_secure_path(
    path: Path,
    *,
    label: str,
    maximum: int,
    expected_sha256: str | None = None,
    expected_bytes: int | None = None,
) -> FileIdentity:
    canonical = _canonical_path(path, label=label)
    opened = _open_parent(canonical, label=label)
    if opened is None:
        raise PrecommitInputInstallError(f"{label} is unavailable")
    directory_fd, name = opened
    descriptor = -1
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY
            | os.O_NONBLOCK
            | getattr(os, "O_CLOEXEC", 0)
            | os.O_NOFOLLOW,
            dir_fd=directory_fd,
        )
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != ROOT_UID
            or before.st_gid != ROOT_GID
            or stat.S_IMODE(before.st_mode) != FILE_MODE
            or before.st_nlink != 1
            or not 1 <= before.st_size <= maximum
            or (
                expected_bytes is not None
                and before.st_size != expected_bytes
            )
        ):
            raise PrecommitInputInstallError(
                f"{label} is not an exact root-only 0600 file"
            )
        digest = hashlib.sha256()
        observed = 0
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            observed += len(chunk)
            if observed > maximum:
                raise PrecommitInputInstallError(
                    f"{label} exceeds its size bound"
                )
            digest.update(chunk)
        after = os.fstat(descriptor)
        visible = os.stat(
            name,
            dir_fd=directory_fd,
            follow_symlinks=False,
        )
        observed_sha256 = digest.hexdigest()
        if (
            observed != before.st_size
            or _stable_metadata(before) != _stable_metadata(after)
            or _stable_metadata(after) != _stable_metadata(visible)
            or (
                expected_sha256 is not None
                and observed_sha256 != expected_sha256
            )
            or (
                expected_bytes is not None
                and observed != expected_bytes
            )
        ):
            raise PrecommitInputInstallError(f"{label} identity differs")
        return FileIdentity(
            path=canonical,
            sha256=observed_sha256,
            bytes=observed,
            device=after.st_dev,
            inode=after.st_ino,
        )
    except PrecommitInputInstallError:
        raise
    except OSError as exc:
        raise PrecommitInputInstallError(
            f"{label} is unavailable or unsafe"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(directory_fd)


def _parse_json_payload(
    payload: bytes,
    *,
    label: str,
    newline: bool,
) -> Mapping[str, Any]:
    try:
        document = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_strict_object,
        )
    except PrecommitInputInstallError:
        raise
    except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise PrecommitInputInstallError(f"{label} is invalid JSON") from exc
    if not isinstance(document, dict):
        raise PrecommitInputInstallError(f"{label} must be a JSON object")
    expected = _canonical_json(document) + (b"\n" if newline else b"")
    if payload != expected:
        raise PrecommitInputInstallError(f"{label} is not canonical JSON")
    return document


def _load_precommit_manifest_source(
    path: Path,
    *,
    expected_role: str,
) -> tuple[
    Mapping[str, Any],
    bytes,
    WORKER.PrecommitManifest,
    FileIdentity,
]:
    payload, identity = _read_secure_path(
        path,
        label="precommit manifest input",
        maximum=MAX_JSON_BYTES,
    )
    document = _parse_json_payload(
        payload,
        label="precommit manifest input",
        newline=True,
    )
    if (
        set(document) != WORKER.MANIFEST_FIELDS
        or document.get("schema") != WORKER.MANIFEST_SCHEMA
    ):
        raise PrecommitInputInstallError(
            "precommit manifest fields are not exact"
        )
    operation_id = _canonical_uuid4(document["operation_id"])
    role = document.get("role")
    release_sha = document.get("release_sha")
    release_tree_sha = document.get("release_tree_sha")
    target_revision = document.get("target_migration_revision")
    if (
        expected_role not in WORKER.ROLE_NAMES
        or role != expected_role
        or not isinstance(release_sha, str)
        or WORKER.SHA40_RE.fullmatch(release_sha) is None
        or release_sha == "0" * 40
        or not isinstance(release_tree_sha, str)
        or WORKER.SHA40_RE.fullmatch(release_tree_sha) is None
        or release_tree_sha == "0" * 40
        or not isinstance(target_revision, str)
        or WORKER.REVISION_RE.fullmatch(target_revision) is None
        or document["postgres_runtime_uid"] != WORKER.POSTGRES_RUNTIME_UID
        or document["postgres_runtime_gid"] != WORKER.POSTGRES_RUNTIME_GID
    ):
        raise PrecommitInputInstallError(
            "precommit release, role, or migration identity is invalid"
        )

    runtime_image_ids = document.get("runtime_image_ids")
    if (
        not isinstance(runtime_image_ids, dict)
        or set(runtime_image_ids) != WORKER.IMAGE_FIELDS
        or len(set(runtime_image_ids.values())) != len(runtime_image_ids)
        or any(
            not isinstance(value, str)
            or WORKER.IMAGE_ID_RE.fullmatch(value) is None
            or value == f"sha256:{'0' * 64}"
            for value in runtime_image_ids.values()
        )
    ):
        raise PrecommitInputInstallError(
            "precommit runtime image inventory is invalid"
        )

    raw_images = document.get("image_artifacts")
    if not isinstance(raw_images, dict) or set(raw_images) != WORKER.IMAGE_FIELDS:
        raise PrecommitInputInstallError(
            "precommit image artifact inventory is invalid"
        )
    image_artifacts: dict[str, WORKER.ImageArtifactBinding] = {}
    for kind in sorted(WORKER.IMAGE_FIELDS):
        row = raw_images[kind]
        if (
            not isinstance(row, dict)
            or set(row) != WORKER.IMAGE_ARTIFACT_FIELDS
        ):
            raise PrecommitInputInstallError(
                "precommit image artifact fields are not exact"
            )
        config_digest = row["config_digest"]
        content_identity = row["content_identity"]
        if (
            not isinstance(config_digest, str)
            or WORKER.IMAGE_ID_RE.fullmatch(config_digest) is None
            or config_digest == f"sha256:{'0' * 64}"
            or not isinstance(content_identity, str)
            or WORKER.IMAGE_ID_RE.fullmatch(content_identity) is None
            or content_identity == f"sha256:{'0' * 64}"
        ):
            raise PrecommitInputInstallError(
                f"{kind} image identity is invalid"
            )
        try:
            observed_identity = verify_content_descriptor(
                row["content_descriptor"]
            )
        except DockerImageIdentityError as exc:
            raise PrecommitInputInstallError(
                f"{kind} image content descriptor is invalid"
            ) from exc
        if (
            row["content_descriptor"]["architecture"] != "amd64"
            or row["content_descriptor"]["os"] != "linux"
            or observed_identity != content_identity
        ):
            raise PrecommitInputInstallError(
                f"{kind} image content identity differs"
            )
        image_artifacts[kind] = WORKER.ImageArtifactBinding(
            archive_sha256=_nonzero_sha256(
                row["archive_sha256"],
                label=f"{kind} image archive",
            ),
            archive_bytes=_bounded_int(
                row["archive_bytes"],
                minimum=1,
                maximum=WORKER.MAX_FILE_BYTES,
                label=f"{kind} image archive bytes",
            ),
            config_digest=config_digest,
            content_descriptor=dict(row["content_descriptor"]),
            content_identity=content_identity,
        )
    for field in ("archive_sha256", "config_digest", "content_identity"):
        if len(
            {
                getattr(image_artifacts[kind], field)
                for kind in WORKER.IMAGE_FIELDS
            }
        ) != len(WORKER.IMAGE_FIELDS):
            raise PrecommitInputInstallError(
                f"precommit image {field} values must be distinct"
            )

    raw_artifacts = document.get("artifacts")
    if (
        not isinstance(raw_artifacts, dict)
        or set(raw_artifacts) != set(WORKER.ARTIFACT_KINDS)
    ):
        raise PrecommitInputInstallError(
            "precommit artifact inventory is incomplete"
        )
    artifacts: dict[str, WORKER.ArtifactBinding] = {}
    for kind in WORKER.ARTIFACT_KINDS:
        row = raw_artifacts[kind]
        if (
            not isinstance(row, dict)
            or set(row) != WORKER.ARTIFACT_FIELDS
        ):
            raise PrecommitInputInstallError(
                "precommit artifact fields are not exact"
            )
        tree = row["restored_tree_sha256"]
        if kind in {"uploads-archive", "audit-archive"}:
            tree = _nonzero_sha256(tree, label=f"{kind} restored tree")
        elif tree is not None:
            raise PrecommitInputInstallError(
                f"{kind} must not declare a restored tree digest"
            )
        artifacts[kind] = WORKER.ArtifactBinding(
            sha256=_nonzero_sha256(row["sha256"], label=kind),
            bytes=_bounded_int(
                row["bytes"],
                minimum=1,
                maximum=WORKER.MAX_FILE_BYTES,
                label=f"{kind} bytes",
            ),
            restored_tree_sha256=tree,
        )

    source_database = document.get("source_database")
    if (
        not isinstance(source_database, dict)
        or set(source_database) != WORKER.SOURCE_DATABASE_FIELDS
        or not isinstance(source_database["alembic_revision"], str)
        or WORKER.REVISION_RE.fullmatch(
            source_database["alembic_revision"]
        )
        is None
        or source_database["fingerprint_algorithm"]
        != "pg-copy-jsonl-sha256-canonical-session-v1"
    ):
        raise PrecommitInputInstallError(
            "precommit source database binding is invalid"
        )
    _nonzero_sha256(
        source_database["database_fingerprint_sha256"],
        label="source database fingerprint",
    )
    _bounded_int(
        source_database["row_count"],
        minimum=0,
        maximum=10**15,
        label="source database row count",
    )
    _bounded_int(
        source_database["table_count"],
        minimum=1,
        maximum=100_000,
        label="source database table count",
    )
    for field in (
        "controller_manifest_sha256",
        "approval_sha256",
        "role_material_sha256",
        "canonical_compose_sha256",
        "role_compose_sha256",
        "environment_sha256",
        "worker_sha256",
        "acceptance_producer_sha256",
    ):
        _nonzero_sha256(document[field], label=field)
    if artifacts["role-material"].sha256 != document["role_material_sha256"]:
        raise PrecommitInputInstallError(
            "role material differs from its top-level binding"
        )
    for kind in WORKER.IMAGE_FIELDS:
        artifact = artifacts[f"{kind}-image-archive"]
        image = image_artifacts[kind]
        if (
            artifact.sha256 != image.archive_sha256
            or artifact.bytes != image.archive_bytes
        ):
            raise PrecommitInputInstallError(
                f"{kind} image archive binding differs"
            )

    manifest = WORKER.PrecommitManifest(
        operation_id=operation_id,
        role=role,
        release_sha=release_sha,
        release_tree_sha=release_tree_sha,
        controller_manifest_sha256=document["controller_manifest_sha256"],
        approval_sha256=document["approval_sha256"],
        role_material_sha256=document["role_material_sha256"],
        canonical_compose_sha256=document["canonical_compose_sha256"],
        role_compose_sha256=document["role_compose_sha256"],
        environment_sha256=document["environment_sha256"],
        worker_sha256=document["worker_sha256"],
        acceptance_producer_sha256=document[
            "acceptance_producer_sha256"
        ],
        image_artifacts=image_artifacts,
        runtime_image_ids=dict(runtime_image_ids),
        artifacts=artifacts,
        source_database=dict(source_database),
        target_migration_revision=target_revision,
        postgres_runtime_uid=document["postgres_runtime_uid"],
        postgres_runtime_gid=document["postgres_runtime_gid"],
        canonical_sha256=hashlib.sha256(_canonical_json(document)).hexdigest(),
    )
    return document, payload, manifest, identity


def _validate_role_environment(
    payload: bytes,
    *,
    internal: Mapping[str, Any],
    manifest: WORKER.PrecommitManifest,
    paths: WORKER.OperationPaths,
) -> None:
    try:
        values = WORKER.parse_env_values(payload.decode("ascii"))
    except (UnicodeError, ValueError, RuntimeError) as exc:
        raise PrecommitInputInstallError(
            "role environment is invalid"
        ) from exc
    required_keys = internal.get("required_env_keys")
    if (
        not isinstance(required_keys, list)
        or any(not isinstance(value, str) for value in required_keys)
        or required_keys != sorted(set(required_keys))
        or required_keys != sorted(values)
    ):
        raise PrecommitInputInstallError(
            "role environment key closure differs"
        )
    if any(
        fragment in name
        for name in values
        for fragment in PREPARE.FORBIDDEN_PREPARE_ENV_FRAGMENTS
    ):
        raise PrecommitInputInstallError(
            "role environment contains activation or provider material"
        )
    expected = {
        "PRODUCTION_SHADOW_OPERATION_ID": manifest.operation_id,
        "PRODUCTION_SHADOW_PROJECT": paths.project_base,
        "PRODUCTION_SHADOW_CGROUP_PARENT": paths.project_base,
        "PRODUCTION_SHADOW_PROJECT_ROOT": str(paths.project_root),
        "PRODUCTION_SHADOW_RELEASE_ROOT": str(paths.release_root),
        "PRODUCTION_SHADOW_DATA_ROOT": str(paths.data_root),
        "PRODUCTION_SHADOW_SECRET_ROOT": str(paths.secret_root),
        "PRODUCTION_SHADOW_RELEASE_SHA": manifest.release_sha,
        "PRODUCTION_SHADOW_APP_IMAGE_ID": manifest.runtime_image_ids["app"],
        "PRODUCTION_SHADOW_POSTGRES_IMAGE_ID": (
            manifest.runtime_image_ids["postgres"]
        ),
        "PRODUCTION_SHADOW_REDIS_IMAGE_ID": (
            manifest.runtime_image_ids["redis"]
        ),
        "PRODUCTION_SHADOW_NGINX_IMAGE_ID": (
            manifest.runtime_image_ids["nginx"]
        ),
    }
    if any(values.get(key) != value for key, value in expected.items()):
        raise PrecommitInputInstallError(
            "role environment differs from the operation identity"
        )
    database = values.get(WORKER.ROLE_SERVICES[manifest.role]["database_env"])
    if not isinstance(database, str) or WORKER.NAME_RE.fullmatch(database) is None:
        raise PrecommitInputInstallError(
            "role environment database name is invalid"
        )


def _load_role_material(
    path: Path,
    *,
    manifest: WORKER.PrecommitManifest,
    paths: WORKER.OperationPaths,
) -> tuple[bytes, FileIdentity, Mapping[str, bytes]]:
    payload, identity = _read_secure_path(
        path,
        label="role material input",
        maximum=MAX_ROLE_ARCHIVE_BYTES,
    )
    binding = manifest.artifacts["role-material"]
    if identity.sha256 != binding.sha256 or identity.bytes != binding.bytes:
        raise PrecommitInputInstallError("role material identity differs")
    members: dict[str, bytes] = {}
    try:
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:") as archive:
            rows = archive.getmembers()
            if (
                len(rows) != len(ROLE_ARCHIVE_MEMBERS)
                or len({row.name for row in rows}) != len(rows)
                or {row.name for row in rows} != ROLE_ARCHIVE_MEMBERS
            ):
                raise PrecommitInputInstallError(
                    "role material member closure differs"
                )
            for row in rows:
                pure = PurePosixPath(row.name)
                if (
                    not row.isreg()
                    or pure.is_absolute()
                    or not pure.parts
                    or ".." in pure.parts
                    or row.uid != ROOT_UID
                    or row.gid != ROOT_GID
                    or stat.S_IMODE(row.mode) != FILE_MODE
                    or row.mtime != 0
                    or not 1 <= row.size <= MAX_JSON_BYTES
                ):
                    raise PrecommitInputInstallError(
                        "role material contains an unsafe member"
                    )
                stream = archive.extractfile(row)
                if stream is None:
                    raise PrecommitInputInstallError(
                        "role material member is unreadable"
                    )
                member_payload = stream.read(MAX_JSON_BYTES + 1)
                if len(member_payload) != row.size:
                    raise PrecommitInputInstallError(
                        "role material member size differs"
                    )
                members[row.name] = member_payload
    except PrecommitInputInstallError:
        raise
    except (EOFError, OSError, tarfile.TarError) as exc:
        raise PrecommitInputInstallError(
            "role material archive is invalid"
        ) from exc

    internal = _parse_json_payload(
        members[PREPARE.FINAL_PREPARE_MANIFEST_NAME],
        label="role material manifest",
        newline=False,
    )
    if (
        set(internal) != PREPARE.FINAL_PREPARE_FIELDS
        or internal.get("schema") != PREPARE.FI_FINAL_PREPARE_SCHEMA
        or internal.get("operation_id") != manifest.operation_id
        or internal.get("release_sha") != manifest.release_sha
        or internal.get("role") != manifest.role
        or internal.get("runtime_image_ids")
        != dict(manifest.runtime_image_ids)
    ):
        raise PrecommitInputInstallError(
            "role material manifest binding differs"
        )
    _nonzero_sha256(
        internal.get("operation_manifest_sha256"),
        label="role stage operation manifest",
    )
    _nonzero_sha256(
        internal.get("stage_attestation_sha256"),
        label="role stage attestation",
    )
    entries = internal.get("entries")
    if not isinstance(entries, list) or len(entries) != 3:
        raise PrecommitInputInstallError(
            "role material entry closure differs"
        )
    by_name: dict[str, Mapping[str, Any]] = {}
    for entry in entries:
        if (
            not isinstance(entry, dict)
            or set(entry) != PREPARE.FINAL_PREPARE_ENTRY_FIELDS
            or not isinstance(entry.get("archive_path"), str)
            or entry["archive_path"] in by_name
        ):
            raise PrecommitInputInstallError(
                "role material entry fields are not exact"
            )
        by_name[entry["archive_path"]] = entry
    expected_destinations = {
        "role-compose.yml": (
            f"rendered/{manifest.role.replace('_', '-')}/docker-compose.yml"
        ),
        "runtime.env.role": (
            f"secrets/{manifest.role.replace('_', '-')}/runtime.env.role"
        ),
        "ca.crt": "secrets/tls/ca.crt",
    }
    if set(by_name) != set(expected_destinations):
        raise PrecommitInputInstallError(
            "role material entry closure differs"
        )
    for name, destination in expected_destinations.items():
        entry = by_name[name]
        member_payload = members[name]
        if (
            entry["destination"] != destination
            or entry["mode"] != "0600"
            or entry["sha256"]
            != hashlib.sha256(member_payload).hexdigest()
            or entry["bytes"] != len(member_payload)
        ):
            raise PrecommitInputInstallError(
                f"role material {name} binding differs"
            )
    if (
        hashlib.sha256(members["role-compose.yml"]).hexdigest()
        != manifest.role_compose_sha256
        or hashlib.sha256(members["runtime.env.role"]).hexdigest()
        != manifest.environment_sha256
    ):
        raise PrecommitInputInstallError(
            "role Compose or environment differs from precommit binding"
        )
    _validate_role_environment(
        members["runtime.env.role"],
        internal=internal,
        manifest=manifest,
        paths=paths,
    )
    try:
        PREPARE._validate_ca_certificate(  # noqa: SLF001
            members["ca.crt"],
            operation_id=manifest.operation_id,
        )
    except PREPARE.PrepareMaterialError as exc:
        raise PrecommitInputInstallError(
            "role material CA certificate is invalid"
        ) from exc
    return payload, identity, members


def _source_binding_from_document(
    document: Mapping[str, Any],
    manifest: WORKER.PrecommitManifest,
) -> SOURCE.SnapshotBinding:
    source_project = SOURCE.SOURCE_PROJECTS[manifest.role]
    expected_images = {
        **SOURCE.SOURCE_IMAGE_REFERENCES[manifest.role],
        "restore_postgres": (
            f"trading_bot_postgres_boottime:15-{manifest.release_sha}"
        ),
    }
    expected_volumes = {
        kind: f"{source_project}_{suffix}"
        for kind, suffix in SOURCE.VOLUME_SUFFIXES.items()
    }
    return SOURCE.SnapshotBinding(
        operation_id=manifest.operation_id,
        release_sha=manifest.release_sha,
        legacy_release_sha=document["legacy_release_sha"],
        role=manifest.role,
        source_project=source_project,
        containers=dict(SOURCE.SOURCE_CONTAINERS),
        images=expected_images,
        volumes=expected_volumes,
        controller_manifest_sha256=manifest.controller_manifest_sha256,
        approval_sha256=manifest.approval_sha256,
        mode="live-baseline",
        canonical_sha256=document["binding_sha256"],
    )


def _validate_snapshot_archive(
    path: Path,
    *,
    binding: WORKER.ArtifactBinding,
    snapshot: Mapping[str, Any],
    label: str,
) -> None:
    opened = _open_parent(path, label=label)
    if opened is None:
        raise PrecommitInputInstallError(f"{label} is unavailable")
    directory_fd, name = opened
    descriptor = -1
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY
            | os.O_NONBLOCK
            | getattr(os, "O_CLOEXEC", 0)
            | os.O_NOFOLLOW,
            dir_fd=directory_fd,
        )
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != ROOT_UID
            or before.st_gid != ROOT_GID
            or stat.S_IMODE(before.st_mode) != FILE_MODE
            or before.st_nlink != 1
            or before.st_size != binding.bytes
        ):
            raise PrecommitInputInstallError(f"{label} is unsafe")
        names: set[str] = set()
        member_count = 0
        expanded_bytes = 0
        os.lseek(descriptor, 0, os.SEEK_SET)
        with os.fdopen(os.dup(descriptor), "rb") as stream:
            with tarfile.open(fileobj=stream, mode="r:gz") as archive:
                for member in archive:
                    candidate = PurePosixPath(member.name.rstrip("/"))
                    normalized = candidate.as_posix()
                    member_count += 1
                    expanded_bytes += member.size
                    mode = stat.S_IMODE(member.mode)
                    if (
                        member_count > SOURCE.MAX_TREE_MEMBERS
                        or expanded_bytes > SOURCE.MAX_ARTIFACT_BYTES
                        or candidate.is_absolute()
                        or not candidate.parts
                        or ".." in candidate.parts
                        or normalized in names
                        or member.issym()
                        or member.islnk()
                        or member.isdev()
                        or not (member.isdir() or member.isfile())
                        or member.uid != ROOT_UID
                        or member.gid != ROOT_GID
                        or member.mtime != 0
                        or bool(mode & 0o6022)
                        or (member.isdir() and member.size != 0)
                    ):
                        raise PrecommitInputInstallError(
                            f"{label} contains an unsafe tar member"
                        )
                    names.add(normalized)
        after = os.fstat(descriptor)
        visible = os.stat(
            name,
            dir_fd=directory_fd,
            follow_symlinks=False,
        )
        if (
            not names
            or member_count != snapshot["member_count"]
            or expanded_bytes != snapshot["expanded_bytes"]
            or _stable_metadata(before) != _stable_metadata(after)
            or _stable_metadata(after) != _stable_metadata(visible)
        ):
            raise PrecommitInputInstallError(
                f"{label} archive shape differs"
            )
    except PrecommitInputInstallError:
        raise
    except (EOFError, OSError, tarfile.TarError) as exc:
        raise PrecommitInputInstallError(f"{label} archive is invalid") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(directory_fd)


def _load_source_snapshot(
    path: Path,
    *,
    manifest: WORKER.PrecommitManifest,
) -> tuple[
    Mapping[str, Any],
    FileIdentity,
    Mapping[str, SourceArtifact],
]:
    payload, identity = _read_secure_path(
        path,
        label="source snapshot manifest",
        maximum=MAX_JSON_BYTES,
    )
    document = _parse_json_payload(
        payload,
        label="source snapshot manifest",
        newline=False,
    )
    expected_top = {
        "schema": SOURCE.MANIFEST_SCHEMA,
        "status": "source-snapshot-created",
        "operation_id": manifest.operation_id,
        "role": manifest.role,
        "mode": "live-baseline",
        "release_sha": manifest.release_sha,
        "source_project": SOURCE.SOURCE_PROJECTS[manifest.role],
        "controller_manifest_sha256": manifest.controller_manifest_sha256,
        "approval_sha256": manifest.approval_sha256,
        "freeze_evidence_sha256": None,
        "source_mutated": False,
        "current_mutated": False,
        "source_stopped_or_restarted": False,
        "redis_restored": False,
    }
    legacy = document.get("legacy_release_sha")
    if (
        set(document) != SOURCE.MANIFEST_FIELDS
        or any(
            type(document.get(key)) is not type(value)
            or document.get(key) != value
            for key, value in expected_top.items()
        )
        or not isinstance(legacy, str)
        or SOURCE.SHA40_RE.fullmatch(legacy) is None
        or legacy in {"0" * 40, manifest.release_sha}
    ):
        raise PrecommitInputInstallError(
            "source snapshot operation binding differs"
        )
    _nonzero_sha256(
        document.get("binding_sha256"),
        label="source snapshot binding",
    )
    binding = _source_binding_from_document(document, manifest)
    try:
        SOURCE._validate_completed_source(  # noqa: SLF001
            document["source"],
            binding,
        )
        SOURCE._validate_source_database(  # noqa: SLF001
            document["source_database"]
        )
        SOURCE._validate_completed_redis(  # noqa: SLF001
            document["redis_rollback_only"],
            binding,
        )
        SOURCE._validate_completed_restore(  # noqa: SLF001
            document["restore_drill"],
            binding,
            document["source"],
        )
    except SOURCE.SourceSnapshotError as exc:
        raise PrecommitInputInstallError(
            "source snapshot evidence is invalid"
        ) from exc
    if (
        document["source_database"] != dict(manifest.source_database)
        or document["restore_drill"]["postgres_image_id"]
        != manifest.runtime_image_ids["postgres"]
    ):
        raise PrecommitInputInstallError(
            "source snapshot database or restore image binding differs"
        )
    artifacts = document.get("artifacts")
    snapshots = document.get("file_snapshots")
    if (
        not isinstance(artifacts, dict)
        or set(artifacts) != set(SOURCE_ARTIFACT_FILES)
        or not isinstance(snapshots, dict)
        or set(snapshots) != {"uploads", "audit"}
    ):
        raise PrecommitInputInstallError(
            "source snapshot artifact closure differs"
        )

    source_artifacts: dict[str, SourceArtifact] = {}
    source_directory = identity.path.parent
    for kind, filename in SOURCE_ARTIFACT_FILES.items():
        row = artifacts[kind]
        expected = manifest.artifacts[kind]
        expected_row = {
            "sha256": expected.sha256,
            "bytes": expected.bytes,
            "restored_tree_sha256": expected.restored_tree_sha256,
        }
        if (
            not isinstance(row, dict)
            or set(row) != SOURCE.ARTIFACT_FIELDS
            or row != expected_row
        ):
            raise PrecommitInputInstallError(
                f"source snapshot {kind} binding differs"
            )
        artifact_path = source_directory / filename
        artifact_identity = _hash_secure_path(
            artifact_path,
            label=f"source snapshot {kind}",
            maximum=SOURCE.MAX_ARTIFACT_BYTES,
            expected_sha256=expected.sha256,
            expected_bytes=expected.bytes,
        )
        source_artifacts[kind] = SourceArtifact(
            kind=kind,
            path=artifact_path,
            sha256=expected.sha256,
            bytes=expected.bytes,
            restored_tree_sha256=expected.restored_tree_sha256,
            identity=artifact_identity,
        )
        if kind != "database-backup":
            snapshot_name = kind.removesuffix("-archive")
            snapshot = snapshots.get(snapshot_name)
            tree = expected.restored_tree_sha256
            if (
                not isinstance(snapshot, dict)
                or set(snapshot) != SOURCE.FILE_SNAPSHOT_FIELDS
                or snapshot["source_volume"]
                != binding.volumes[snapshot_name]
                or any(
                    snapshot[field] != tree
                    for field in (
                        "pre_tree_sha256",
                        "archive_tree_sha256",
                        "post_tree_sha256",
                    )
                )
                or type(snapshot["member_count"]) is not int
                or not 1
                <= snapshot["member_count"]
                <= SOURCE.MAX_TREE_MEMBERS
                or type(snapshot["expanded_bytes"]) is not int
                or not 0
                <= snapshot["expanded_bytes"]
                <= SOURCE.MAX_ARTIFACT_BYTES
                or type(snapshot["stable_attempt"]) is not int
                or not 1
                <= snapshot["stable_attempt"]
                <= SOURCE.MAX_SNAPSHOT_ATTEMPTS
            ):
                raise PrecommitInputInstallError(
                    f"source snapshot {kind} tree binding differs"
                )
            _validate_snapshot_archive(
                artifact_path,
                binding=expected,
                snapshot=snapshot,
                label=f"source snapshot {kind}",
            )
    return document, identity, source_artifacts


def _assert_existing_directory(
    path: Path,
    *,
    label: str,
    exact_mode: int | None = None,
) -> None:
    descriptor = _open_root_directory(
        path,
        label=label,
        exact_mode=exact_mode,
    )
    os.close(descriptor)


def _assert_root_contract(
    paths: WORKER.OperationPaths,
    *,
    operation_id: str,
) -> None:
    prefixes = (
        WORKER.PROJECT_ROOT_PREFIX,
        WORKER.DATA_ROOT_PREFIX,
        WORKER.SECRET_ROOT_PREFIX,
    )
    for index, prefix in enumerate(prefixes):
        _canonical_path(prefix, label=f"operation root prefix {index + 1}")
    for first_index, first in enumerate(prefixes):
        for second in prefixes[first_index + 1 :]:
            if first == second or first in second.parents or second in first.parents:
                raise PrecommitInputInstallError(
                    "operation root prefixes must be disjoint"
                )
    expected_roots = (
        (paths.project_root, WORKER.PROJECT_ROOT_PREFIX),
        (paths.data_root, WORKER.DATA_ROOT_PREFIX),
        (paths.secret_root, WORKER.SECRET_ROOT_PREFIX),
    )
    for root, prefix in expected_roots:
        if root != prefix / operation_id:
            raise PrecommitInputInstallError(
                "operation root is not UUID-derived"
            )
    for prefix, label in (
        (WORKER.PROJECT_ROOT_PREFIX, "project root prefix"),
        (WORKER.DATA_ROOT_PREFIX, "data root prefix"),
        (WORKER.SECRET_ROOT_PREFIX, "secret root prefix"),
    ):
        _assert_existing_directory(prefix, label=label)
    _assert_existing_directory(
        paths.project_root,
        label="operation project root",
    )
    _assert_existing_directory(
        paths.project_root / "incoming",
        label="operation incoming root",
    )
    _assert_existing_directory(
        paths.release_root,
        label="materialized release root",
    )


def _directory_chain_state(
    base: Path,
    parts: Sequence[str],
    *,
    label: str,
) -> tuple[str, ...]:
    descriptor = _open_root_directory(base, label=f"{label} base")
    states: list[str] = []
    try:
        for index, component in enumerate(parts):
            if (
                not component
                or component in {".", ".."}
                or "/" in component
                or "\0" in component
            ):
                raise PrecommitInputInstallError(
                    f"{label} directory component is invalid"
                )
            try:
                child = os.open(
                    component,
                    _directory_flags(),
                    dir_fd=descriptor,
                )
            except FileNotFoundError:
                states.extend("absent" for _ in parts[index:])
                break
            except OSError as exc:
                raise PrecommitInputInstallError(
                    f"{label} directory chain is unsafe"
                ) from exc
            _assert_root_directory(
                child,
                label=f"{label} {component}",
                exact_mode=PRIVATE_DIRECTORY_MODE,
            )
            os.close(descriptor)
            descriptor = child
            states.append("existing")
        return tuple(states)
    finally:
        os.close(descriptor)


def _temporary_name(spec: OutputSpec) -> str:
    identity = hashlib.sha256(
        spec.kind.encode("ascii")
        + b"\0"
        + spec.path.name.encode("utf-8")
        + b"\0"
        + spec.sha256.encode("ascii")
    ).hexdigest()
    return f".precommit-install-{identity[:32]}.tmp"


def _leaf_identity(
    directory_fd: int,
    name: str,
    *,
    path: Path,
    label: str,
    expected_sha256: str,
    expected_bytes: int,
) -> FileIdentity:
    descriptor = -1
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY
            | os.O_NONBLOCK
            | getattr(os, "O_CLOEXEC", 0)
            | os.O_NOFOLLOW,
            dir_fd=directory_fd,
        )
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != ROOT_UID
            or before.st_gid != ROOT_GID
            or stat.S_IMODE(before.st_mode) != FILE_MODE
            or before.st_nlink != 1
            or before.st_size != expected_bytes
        ):
            raise PrecommitInputInstallError(f"{label} is unsafe")
        digest = hashlib.sha256()
        observed = 0
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            observed += len(chunk)
            digest.update(chunk)
        after = os.fstat(descriptor)
        visible = os.stat(
            name,
            dir_fd=directory_fd,
            follow_symlinks=False,
        )
        if (
            observed != expected_bytes
            or digest.hexdigest() != expected_sha256
            or _stable_metadata(before) != _stable_metadata(after)
            or _stable_metadata(after) != _stable_metadata(visible)
        ):
            raise PrecommitInputInstallError(f"{label} identity differs")
        return FileIdentity(
            path=path,
            sha256=expected_sha256,
            bytes=expected_bytes,
            device=after.st_dev,
            inode=after.st_ino,
        )
    except PrecommitInputInstallError:
        raise
    except OSError as exc:
        raise PrecommitInputInstallError(f"{label} is unsafe") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _leaf_exists(directory_fd: int, name: str, *, label: str) -> bool:
    try:
        os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise PrecommitInputInstallError(
            f"{label} cannot be inspected safely"
        ) from exc
    return True


def _inspect_output(
    spec: OutputSpec,
    *,
    source_identities: frozenset[tuple[int, int]],
) -> OutputState:
    opened = _open_parent(
        spec.path,
        label=f"{spec.kind} output",
        missing_ok=True,
    )
    if opened is None:
        return OutputState(destination="absent", temporary="absent")
    directory_fd, destination_name = opened
    try:
        _assert_root_directory(
            directory_fd,
            label=f"{spec.kind} output parent",
            exact_mode=PRIVATE_DIRECTORY_MODE,
        )
        destination_state = "absent"
        if _leaf_exists(
            directory_fd,
            destination_name,
            label=f"{spec.kind} output",
        ):
            identity = _leaf_identity(
                directory_fd,
                destination_name,
                path=spec.path,
                label=f"existing {spec.kind} output",
                expected_sha256=spec.sha256,
                expected_bytes=spec.bytes,
            )
            if (identity.device, identity.inode) in source_identities:
                raise PrecommitInputInstallError(
                    f"{spec.kind} output aliases an input"
                )
            destination_state = "identical"
        temporary_name = _temporary_name(spec)
        temporary_state = "absent"
        if _leaf_exists(
            directory_fd,
            temporary_name,
            label=f"{spec.kind} temporary",
        ):
            identity = _leaf_identity(
                directory_fd,
                temporary_name,
                path=spec.path.with_name(temporary_name),
                label=f"{spec.kind} temporary",
                expected_sha256=spec.sha256,
                expected_bytes=spec.bytes,
            )
            if (identity.device, identity.inode) in source_identities:
                raise PrecommitInputInstallError(
                    f"{spec.kind} temporary aliases an input"
                )
            temporary_state = "recoverable"
        return OutputState(
            destination=destination_state,
            temporary=temporary_state,
        )
    finally:
        os.close(directory_fd)


def _verify_staged_artifacts(
    manifest: WORKER.PrecommitManifest,
    paths: WORKER.OperationPaths,
) -> None:
    for kind in (
        "release-bundle",
        "app-image-archive",
        "postgres-image-archive",
        "redis-image-archive",
        "nginx-image-archive",
    ):
        binding = manifest.artifacts[kind]
        try:
            observed = WORKER._hash_root_file(  # noqa: SLF001
                paths.artifacts[kind],
                label=f"staged {kind}",
                maximum=WORKER.MAX_FILE_BYTES,
            )
        except WORKER.PrecommitWorkerError as exc:
            raise PrecommitInputInstallError(
                f"staged {kind} is unavailable or unsafe"
            ) from exc
        if observed != (binding.sha256, binding.bytes):
            raise PrecommitInputInstallError(
                f"staged {kind} identity differs"
            )
    try:
        WORKER._verify_release(manifest, paths)  # noqa: SLF001
        WORKER._verify_image_archives(manifest, paths)  # noqa: SLF001
    except WORKER.PrecommitWorkerError as exc:
        raise PrecommitInputInstallError(
            "staged release or image semantic identity differs"
        ) from exc


def _output_specs(
    *,
    manifest_document: Mapping[str, Any],
    manifest_payload: bytes,
    manifest: WORKER.PrecommitManifest,
    paths: WORKER.OperationPaths,
    role_material_payload: bytes,
    role_payloads: Mapping[str, bytes],
    source_artifacts: Mapping[str, SourceArtifact],
) -> tuple[OutputSpec, ...]:
    specs = [
        OutputSpec(
            kind="role-material",
            path=paths.artifacts["role-material"],
            sha256=manifest.artifacts["role-material"].sha256,
            bytes=manifest.artifacts["role-material"].bytes,
            payload=role_material_payload,
        ),
        OutputSpec(
            kind="compose",
            path=paths.compose,
            sha256=manifest.role_compose_sha256,
            bytes=len(role_payloads["role-compose.yml"]),
            payload=role_payloads["role-compose.yml"],
        ),
        OutputSpec(
            kind="environment",
            path=paths.environment,
            sha256=manifest.environment_sha256,
            bytes=len(role_payloads["runtime.env.role"]),
            payload=role_payloads["runtime.env.role"],
        ),
        OutputSpec(
            kind="ca",
            path=paths.secret_root / "tls" / "ca.crt",
            sha256=hashlib.sha256(role_payloads["ca.crt"]).hexdigest(),
            bytes=len(role_payloads["ca.crt"]),
            payload=role_payloads["ca.crt"],
        ),
    ]
    for kind in (
        "database-backup",
        "uploads-archive",
        "audit-archive",
    ):
        artifact = source_artifacts[kind]
        specs.append(
            OutputSpec(
                kind=kind,
                path=paths.artifacts[kind],
                sha256=artifact.sha256,
                bytes=artifact.bytes,
                source=artifact.path,
            )
        )
    canonical_manifest = _canonical_json(manifest_document) + b"\n"
    if canonical_manifest != manifest_payload:
        raise PrecommitInputInstallError(
            "precommit manifest canonical bytes changed"
        )
    specs.append(
        OutputSpec(
            kind="manifest",
            path=paths.manifest,
            sha256=hashlib.sha256(canonical_manifest).hexdigest(),
            bytes=len(canonical_manifest),
            payload=canonical_manifest,
        )
    )
    return tuple(specs)


def preflight_installation(
    *,
    role: str,
    precommit_manifest: Path,
    role_material: Path,
    source_snapshot_manifest: Path,
) -> InstallationPlan:
    if os.geteuid() != ROOT_UID or os.getegid() != ROOT_GID:
        raise PrecommitInputInstallError(
            "precommit input installer must run as root:root"
        )
    if role not in WORKER.ROLE_NAMES:
        raise PrecommitInputInstallError("installer role is invalid")
    input_paths = (
        _canonical_path(
            precommit_manifest,
            label="precommit manifest input",
        ),
        _canonical_path(role_material, label="role material input"),
        _canonical_path(
            source_snapshot_manifest,
            label="source snapshot manifest",
        ),
    )
    if len(set(input_paths)) != len(input_paths):
        raise PrecommitInputInstallError("installer input paths must be distinct")
    (
        manifest_document,
        manifest_payload,
        manifest,
        manifest_identity,
    ) = _load_precommit_manifest_source(
        input_paths[0],
        expected_role=role,
    )
    paths = WORKER.operation_paths(
        manifest.operation_id,
        manifest.release_sha,
        manifest.role,
    )
    _assert_root_contract(paths, operation_id=manifest.operation_id)
    role_payload, role_identity, role_payloads = _load_role_material(
        input_paths[1],
        manifest=manifest,
        paths=paths,
    )
    (
        source_document,
        source_identity,
        source_artifacts,
    ) = _load_source_snapshot(
        input_paths[2],
        manifest=manifest,
    )
    _verify_staged_artifacts(manifest, paths)

    _directory_chain_state(
        paths.project_root,
        ("rendered", role.replace("_", "-")),
        label="rendered role",
    )
    _directory_chain_state(
        WORKER.DATA_ROOT_PREFIX,
        (
            manifest.operation_id,
            "restore-input",
            role.replace("_", "-"),
        ),
        label="restore input",
    )
    _directory_chain_state(
        WORKER.SECRET_ROOT_PREFIX,
        (manifest.operation_id, role.replace("_", "-")),
        label="role secret",
    )
    _directory_chain_state(
        WORKER.SECRET_ROOT_PREFIX,
        (manifest.operation_id, "tls"),
        label="TLS secret",
    )

    outputs = _output_specs(
        manifest_document=manifest_document,
        manifest_payload=manifest_payload,
        manifest=manifest,
        paths=paths,
        role_material_payload=role_payload,
        role_payloads=role_payloads,
        source_artifacts=source_artifacts,
    )
    all_input_identities = (
        manifest_identity,
        role_identity,
        source_identity,
        *(artifact.identity for artifact in source_artifacts.values()),
    )
    physical = {
        (identity.device, identity.inode) for identity in all_input_identities
    }
    if len(physical) != len(all_input_identities):
        raise PrecommitInputInstallError(
            "installer inputs must be physically distinct"
        )
    input_path_set = {identity.path for identity in all_input_identities}
    if any(spec.path in input_path_set for spec in outputs):
        raise PrecommitInputInstallError(
            "installer output must not alias an input path"
        )
    output_paths = [spec.path for spec in outputs]
    if len(set(output_paths)) != len(output_paths):
        raise PrecommitInputInstallError(
            "installer output paths must be distinct"
        )
    states = {
        spec.kind: _inspect_output(spec, source_identities=frozenset(physical))
        for spec in outputs
    }
    manifest_state = states["manifest"]
    other_states = {
        kind: state for kind, state in states.items() if kind != "manifest"
    }
    if manifest_state.destination == "identical" and any(
        state.destination != "identical" for state in other_states.values()
    ):
        raise PrecommitInputInstallError(
            "installed manifest exists without its complete input closure"
        )
    if manifest_state.temporary == "recoverable" and any(
        state.destination != "identical" for state in other_states.values()
    ):
        raise PrecommitInputInstallError(
            "manifest temporary exists before the input closure is complete"
        )
    return InstallationPlan(
        manifest_document=manifest_document,
        manifest_payload=manifest_payload,
        manifest=manifest,
        paths=paths,
        role_material_payload=role_payload,
        role_material_identity=role_identity,
        role_payloads=role_payloads,
        source_snapshot_document=source_document,
        source_snapshot_sha256=source_identity.sha256,
        source_snapshot_identity=source_identity,
        source_artifacts=source_artifacts,
        outputs=outputs,
        output_states=states,
    )


def _mkdir_open_at(
    parent_fd: int,
    name: str,
    *,
    label: str,
) -> int:
    try:
        os.mkdir(name, mode=PRIVATE_DIRECTORY_MODE, dir_fd=parent_fd)
        os.fsync(parent_fd)
    except FileExistsError:
        pass
    except OSError as exc:
        raise PrecommitInputInstallError(
            f"{label} directory could not be created"
        ) from exc
    try:
        descriptor = os.open(name, _directory_flags(), dir_fd=parent_fd)
    except OSError as exc:
        raise PrecommitInputInstallError(
            f"{label} directory is unsafe"
        ) from exc
    try:
        _assert_root_directory(
            descriptor,
            label=label,
            exact_mode=PRIVATE_DIRECTORY_MODE,
        )
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _create_chain(
    base: Path,
    parts: Sequence[str],
    *,
    label: str,
) -> None:
    descriptor = _open_root_directory(base, label=f"{label} base")
    try:
        for component in parts:
            child = _mkdir_open_at(
                descriptor,
                component,
                label=f"{label} {component}",
            )
            os.close(descriptor)
            descriptor = child
    finally:
        os.close(descriptor)


def _create_operation_directories(plan: InstallationPlan) -> None:
    role_path = plan.manifest.role.replace("_", "-")
    _create_chain(
        plan.paths.project_root,
        ("rendered", role_path),
        label="rendered role",
    )
    _create_chain(
        WORKER.DATA_ROOT_PREFIX,
        (plan.manifest.operation_id, "restore-input", role_path),
        label="restore input",
    )
    _create_chain(
        WORKER.SECRET_ROOT_PREFIX,
        (plan.manifest.operation_id, role_path),
        label="role secret",
    )
    _create_chain(
        WORKER.SECRET_ROOT_PREFIX,
        (plan.manifest.operation_id, "tls"),
        label="TLS secret",
    )


def _write_all(descriptor: int, payload: bytes, *, label: str) -> None:
    view = memoryview(payload)
    written = 0
    while written < len(view):
        try:
            count = os.write(descriptor, view[written:])
        except OSError as exc:
            raise PrecommitInputInstallError(
                f"{label} write failed"
            ) from exc
        if count <= 0:
            raise PrecommitInputInstallError(
                f"{label} write made no progress"
            )
        written += count


def _copy_source_to_descriptor(
    source: Path,
    destination_fd: int,
    *,
    expected_sha256: str,
    expected_bytes: int,
    label: str,
) -> None:
    opened = _open_parent(source, label=f"{label} source")
    if opened is None:
        raise PrecommitInputInstallError(f"{label} source is unavailable")
    directory_fd, name = opened
    source_fd = -1
    try:
        source_fd = os.open(
            name,
            os.O_RDONLY
            | os.O_NONBLOCK
            | getattr(os, "O_CLOEXEC", 0)
            | os.O_NOFOLLOW,
            dir_fd=directory_fd,
        )
        before = os.fstat(source_fd)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != ROOT_UID
            or before.st_gid != ROOT_GID
            or stat.S_IMODE(before.st_mode) != FILE_MODE
            or before.st_nlink != 1
            or before.st_size != expected_bytes
        ):
            raise PrecommitInputInstallError(f"{label} source is unsafe")
        digest = hashlib.sha256()
        observed = 0
        while True:
            chunk = os.read(source_fd, 1024 * 1024)
            if not chunk:
                break
            observed += len(chunk)
            if observed > expected_bytes:
                raise PrecommitInputInstallError(
                    f"{label} source exceeds its binding"
                )
            digest.update(chunk)
            _write_all(destination_fd, chunk, label=label)
        after = os.fstat(source_fd)
        visible = os.stat(
            name,
            dir_fd=directory_fd,
            follow_symlinks=False,
        )
        if (
            observed != expected_bytes
            or digest.hexdigest() != expected_sha256
            or _stable_metadata(before) != _stable_metadata(after)
            or _stable_metadata(after) != _stable_metadata(visible)
        ):
            raise PrecommitInputInstallError(
                f"{label} source changed while being copied"
            )
    except PrecommitInputInstallError:
        raise
    except OSError as exc:
        raise PrecommitInputInstallError(
            f"{label} source is unavailable or unsafe"
        ) from exc
    finally:
        if source_fd >= 0:
            os.close(source_fd)
        os.close(directory_fd)


def _rename_noreplace(
    directory_fd: int,
    source_name: str,
    destination_name: str,
) -> None:
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        renameat2 = libc.renameat2
    except (AttributeError, OSError) as exc:
        raise PrecommitInputInstallError(
            "atomic create-only rename is unavailable"
        ) from exc
    renameat2.argtypes = (
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    )
    renameat2.restype = ctypes.c_int
    result = renameat2(
        directory_fd,
        os.fsencode(source_name),
        directory_fd,
        os.fsencode(destination_name),
        RENAME_NOREPLACE,
    )
    if result == 0:
        return
    observed_errno = ctypes.get_errno()
    if observed_errno == errno.EEXIST:
        raise FileExistsError(destination_name)
    if observed_errno == errno.ENOENT:
        raise FileNotFoundError(source_name)
    raise PrecommitInputInstallError(
        "atomic create-only publication failed"
    ) from OSError(observed_errno, os.strerror(observed_errno))


def _unlink_exact_temporary(
    directory_fd: int,
    name: str,
    *,
    spec: OutputSpec,
) -> None:
    if not _leaf_exists(
        directory_fd,
        name,
        label=f"{spec.kind} temporary",
    ):
        return
    _leaf_identity(
        directory_fd,
        name,
        path=spec.path.with_name(name),
        label=f"{spec.kind} temporary",
        expected_sha256=spec.sha256,
        expected_bytes=spec.bytes,
    )
    try:
        os.unlink(name, dir_fd=directory_fd)
        os.fsync(directory_fd)
    except OSError as exc:
        raise PrecommitInputInstallError(
            f"{spec.kind} temporary could not be removed"
        ) from exc


def _write_temporary(
    directory_fd: int,
    temporary_name: str,
    *,
    spec: OutputSpec,
) -> None:
    descriptor = -1
    created = False
    try:
        descriptor = os.open(
            temporary_name,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | os.O_NOFOLLOW,
            FILE_MODE,
            dir_fd=directory_fd,
        )
        created = True
        os.fchmod(descriptor, FILE_MODE)
        if spec.payload is not None:
            _write_all(descriptor, spec.payload, label=spec.kind)
        elif spec.source is not None:
            _copy_source_to_descriptor(
                spec.source,
                descriptor,
                expected_sha256=spec.sha256,
                expected_bytes=spec.bytes,
                label=spec.kind,
            )
        else:
            raise PrecommitInputInstallError(
                f"{spec.kind} has no installation source"
            )
        os.fsync(descriptor)
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != ROOT_UID
            or metadata.st_gid != ROOT_GID
            or stat.S_IMODE(metadata.st_mode) != FILE_MODE
            or metadata.st_nlink != 1
            or metadata.st_size != spec.bytes
        ):
            raise PrecommitInputInstallError(
                f"{spec.kind} temporary identity differs"
            )
    except FileExistsError:
        _leaf_identity(
            directory_fd,
            temporary_name,
            path=spec.path.with_name(temporary_name),
            label=f"{spec.kind} temporary",
            expected_sha256=spec.sha256,
            expected_bytes=spec.bytes,
        )
        return
    except Exception:
        if descriptor >= 0:
            os.close(descriptor)
            descriptor = -1
        if created:
            try:
                os.unlink(temporary_name, dir_fd=directory_fd)
                os.fsync(directory_fd)
            except OSError:
                pass
        raise
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    os.fsync(directory_fd)
    _leaf_identity(
        directory_fd,
        temporary_name,
        path=spec.path.with_name(temporary_name),
        label=f"{spec.kind} temporary",
        expected_sha256=spec.sha256,
        expected_bytes=spec.bytes,
    )


def _publish_spec(spec: OutputSpec) -> str:
    opened = _open_parent(spec.path, label=f"{spec.kind} output")
    if opened is None:
        raise PrecommitInputInstallError(
            f"{spec.kind} output parent is unavailable"
        )
    directory_fd, destination_name = opened
    temporary_name = _temporary_name(spec)
    try:
        _assert_root_directory(
            directory_fd,
            label=f"{spec.kind} output parent",
            exact_mode=PRIVATE_DIRECTORY_MODE,
        )
        if _leaf_exists(
            directory_fd,
            destination_name,
            label=f"{spec.kind} output",
        ):
            _leaf_identity(
                directory_fd,
                destination_name,
                path=spec.path,
                label=f"existing {spec.kind} output",
                expected_sha256=spec.sha256,
                expected_bytes=spec.bytes,
            )
            _unlink_exact_temporary(
                directory_fd,
                temporary_name,
                spec=spec,
            )
            return "reused"
        _write_temporary(
            directory_fd,
            temporary_name,
            spec=spec,
        )
        try:
            _rename_noreplace(
                directory_fd,
                temporary_name,
                destination_name,
            )
            publication = "created"
        except (FileExistsError, FileNotFoundError):
            publication = "reused"
        os.fsync(directory_fd)
        _leaf_identity(
            directory_fd,
            destination_name,
            path=spec.path,
            label=f"published {spec.kind} output",
            expected_sha256=spec.sha256,
            expected_bytes=spec.bytes,
        )
        _unlink_exact_temporary(
            directory_fd,
            temporary_name,
            spec=spec,
        )
        return publication
    finally:
        os.close(directory_fd)


@contextmanager
def _installation_lock() -> Iterator[None]:
    descriptor = _open_root_directory(
        WORKER.SECRET_ROOT_PREFIX,
        label="secret root prefix",
    )
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise PrecommitInputInstallError(
                "another precommit input installation is active"
            ) from exc
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _verify_installed_without_runtime(plan: InstallationPlan) -> None:
    try:
        loaded = WORKER.load_manifest(plan.paths.manifest)
        if loaded != plan.manifest:
            raise PrecommitInputInstallError(
                "installed manifest semantic identity differs"
            )
        WORKER._verify_operation_directory_chains(plan.paths)  # noqa: SLF001
        WORKER._verify_artifacts(loaded, plan.paths)  # noqa: SLF001
        WORKER._verify_release(loaded, plan.paths)  # noqa: SLF001
        WORKER._verify_image_archives(loaded, plan.paths)  # noqa: SLF001
        WORKER._verify_role_material(loaded, plan.paths)  # noqa: SLF001
    except WORKER.PrecommitWorkerError as exc:
        raise PrecommitInputInstallError(
            "installed worker input closure failed verification"
        ) from exc
    ca_spec = next(spec for spec in plan.outputs if spec.kind == "ca")
    _hash_secure_path(
        ca_spec.path,
        label="installed role CA",
        maximum=MAX_JSON_BYTES,
        expected_sha256=ca_spec.sha256,
        expected_bytes=ca_spec.bytes,
    )


def confirmation_phrase(
    operation_id: str,
    role: str,
    release_sha: str,
) -> str:
    return (
        "install-production-shadow-precommit-inputs:"
        f"{operation_id}:{role}:{release_sha}"
    )


def _summary(
    plan: InstallationPlan,
    *,
    status: str,
    publications: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "schema": WORKER.MANIFEST_SCHEMA,
        "status": status,
        "operation_id": plan.manifest.operation_id,
        "role": plan.manifest.role,
        "release_sha": plan.manifest.release_sha,
        "manifest_sha256": plan.manifest.canonical_sha256,
        "source_snapshot_sha256": plan.source_snapshot_sha256,
        "output_states": {
            kind: {
                "destination": state.destination,
                "temporary": state.temporary,
            }
            for kind, state in sorted(plan.output_states.items())
        },
        "required_confirmation": confirmation_phrase(
            plan.manifest.operation_id,
            plan.manifest.role,
            plan.manifest.release_sha,
        ),
        "network_io": False,
        "docker_invoked": False,
        "service_mutated": False,
        "current_mutated": False,
        "source_mutated": False,
    }
    if publications is not None:
        result["publications"] = dict(sorted(publications.items()))
    return result


def execute_installation(
    *,
    role: str,
    precommit_manifest: Path,
    role_material: Path,
    source_snapshot_manifest: Path,
    apply: bool = False,
    confirm: str | None = None,
) -> dict[str, Any]:
    plan = preflight_installation(
        role=role,
        precommit_manifest=precommit_manifest,
        role_material=role_material,
        source_snapshot_manifest=source_snapshot_manifest,
    )
    required = confirmation_phrase(
        plan.manifest.operation_id,
        plan.manifest.role,
        plan.manifest.release_sha,
    )
    if not apply:
        if confirm is not None:
            raise PrecommitInputInstallError(
                "--confirm is valid only with --apply"
            )
        return _summary(plan, status="planned")
    if confirm != required:
        raise PrecommitInputInstallError(
            f"apply requires --confirm {required}"
        )
    with _installation_lock():
        current = preflight_installation(
            role=role,
            precommit_manifest=precommit_manifest,
            role_material=role_material,
            source_snapshot_manifest=source_snapshot_manifest,
        )
        _create_operation_directories(current)
        publications: dict[str, str] = {}
        for spec in current.outputs:
            if spec.kind == "manifest":
                continue
            publications[spec.kind] = _publish_spec(spec)
        manifest_spec = next(
            spec for spec in current.outputs if spec.kind == "manifest"
        )
        publications["manifest"] = _publish_spec(manifest_spec)
        _verify_installed_without_runtime(current)
        status = (
            "already-installed"
            if all(value == "reused" for value in publications.values())
            else "installed"
        )
        return _summary(
            current,
            status=status,
            publications=publications,
        )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--role",
        required=True,
        choices=WORKER.ROLE_NAMES,
    )
    parser.add_argument(
        "--precommit-manifest",
        "--manifest",
        dest="precommit_manifest",
        required=True,
        type=Path,
    )
    parser.add_argument("--role-material", required=True, type=Path)
    parser.add_argument(
        "--source-snapshot-manifest",
        "--source-snapshot",
        dest="source_snapshot_manifest",
        required=True,
        type=Path,
    )
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = execute_installation(
            role=args.role,
            precommit_manifest=args.precommit_manifest,
            role_material=args.role_material,
            source_snapshot_manifest=args.source_snapshot_manifest,
            apply=args.apply,
            confirm=args.confirm,
        )
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0
    except PrecommitInputInstallError as exc:
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "error": str(exc),
                    "error_class": type(exc).__name__,
                    "source_mutated": False,
                    "current_mutated": False,
                    "service_mutated": False,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 1
    except Exception:
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "error": "precommit input installation failed closed",
                    "error_class": "PrecommitInputInstallError",
                    "source_mutated": False,
                    "current_mutated": False,
                    "service_mutated": False,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
