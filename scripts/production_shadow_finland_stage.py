#!/usr/bin/env python3
"""Stage one immutable production-shadow release and image set on a Finland host.

The deployed copy is standalone.  It accepts only an operation-bound manifest,
materializes its detached Git release, validates every Docker archive before
the first image-store mutation, and loads the four images without creating or
starting any Docker runtime resource.
"""

from __future__ import annotations

import argparse
import base64
import binascii
from contextlib import contextmanager
import ctypes
from dataclasses import dataclass
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import select
import selectors
import signal
import socket
import stat
import struct
import subprocess
import sys
import tarfile
import threading
import time
from typing import Any, BinaryIO, Callable, Iterator, Mapping, Sequence
from uuid import UUID


MANIFEST_SCHEMA = "production-shadow-finland-image-stage-manifest-v1"
REQUEST_SCHEMA = "production-shadow-finland-image-stage-request-v1"
BOOTSTRAP_REQUEST_SCHEMA = (
    "production-shadow-finland-image-stage-bootstrap-request-v1"
)
VERSION_SCHEMA = "production-shadow-finland-image-stage-agent-version-v1"
ATTESTATION_SCHEMA = "production-shadow-finland-image-stage-attestation-v1"
RESULT_SCHEMA = "production-shadow-finland-image-stage-result-v1"
JOURNAL_SCHEMA = "production-shadow-finland-image-stage-journal-v1"
AGENT_VERSION = 1

PROJECT_ROOT_PREFIX = Path("/srv/trading-bot-three-site-production-shadow")
SECRET_ROOT_PREFIX = Path(
    "/root/secure-envs/trading-bot/three-site-production-shadow"
)
AGENT_FILENAME = "production-shadow-finland-stage.py"
MANIFEST_FILENAME = "image-stage-manifest.json"
ATTESTATION_FILENAME = "image-stage-attestation.json"
JOURNAL_FILENAME = "image-stage-journal.json"
LOCK_FILENAME = "image-stage.lock"
CONVERGENCE_SOURCE_SET_PRODUCER_RELATIVE = Path(
    "scripts/produce_production_shadow_convergence_source_set.py"
)
CONVERGENCE_SOURCE_SET_LAUNCHER_RELATIVE = Path(
    "scripts/production_shadow_convergence_source_set_launcher"
)

GIT = "/usr/bin/git"
DOCKER = "/usr/bin/docker"
IMAGE_ROLES = ("app", "postgres", "redis", "nginx")
STAGE_ROLES = ("bot_fi", "webapp_fi")
ROLE_PATHS = {"bot_fi": "bot-fi", "webapp_fi": "webapp-fi"}
ROLE_HOSTS = {
    "bot_fi": "65.109.216.187",
    "webapp_fi": "65.109.220.59",
}
RELEASE_BOUND_IMAGE_ROLES = frozenset({"app", "postgres"})
POSTGRES_RUNTIME_UID_LABEL = "trading-bot.postgres.runtime-uid"
POSTGRES_RUNTIME_GID_LABEL = "trading-bot.postgres.runtime-gid"
POSTGRES_RUNTIME_UID = 70
POSTGRES_RUNTIME_GID = 70
ARTIFACT_FILENAMES = {
    "release-bundle": "release.bundle",
    "app-image-archive": "app-image.tar",
    "postgres-image-archive": "postgres-image.tar",
    "redis-image-archive": "redis-image.tar",
    "nginx-image-archive": "nginx-image.tar",
}
ARTIFACT_FORMATS = {
    "release-bundle": "git-bundle",
    "app-image-archive": "docker-archive",
    "postgres-image-archive": "docker-archive",
    "redis-image-archive": "docker-archive",
    "nginx-image-archive": "docker-archive",
}
ARTIFACT_KINDS = tuple(ARTIFACT_FILENAMES)
IMAGE_ARTIFACT_FIELDS = frozenset(
    {
        "archive_sha256",
        "archive_bytes",
        "config_digest",
        "content_descriptor",
        "content_identity",
    }
)
DESCRIPTOR_FIELDS = frozenset(
    {
        "architecture",
        "os",
        "created",
        "config_sha256",
        "rootfs_type",
        "rootfs_layers",
    }
)
ARTIFACT_FIELDS = frozenset({"kind", "filename", "sha256", "bytes", "format"})
MANIFEST_FIELDS = frozenset(
    {
        "schema",
        "operation_id",
        "release_sha",
        "release_tree_sha",
        "role",
        "project_name",
        "project_root",
        "release_root",
        "incoming_root",
        "secret_role_root",
        "bootstrap_sha256",
        "artifacts",
        "image_artifacts",
        "postgres_runtime_uid",
        "postgres_runtime_gid",
        "pull_policy",
    }
)
REQUEST_FIELDS = frozenset(
    {
        "schema",
        "action",
        "operation_id",
        "release_sha",
        "release_tree_sha",
        "role",
        "operation_manifest_sha256",
        "agent_sha256",
        "pull_policy",
    }
)
BOOTSTRAP_REQUEST_FIELDS = frozenset(
    {"schema", "action", "operation_id", "role", "agent_sha256"}
)
IMAGE_ATTESTATION_FIELDS = frozenset(
    {
        "role",
        "runtime_image_id",
        "config_digest",
        "content_descriptor",
        "content_identity",
        "source",
    }
)
IMAGE_LOAD_RECONCILIATION_FIELDS = frozenset(
    {
        "schema",
        "operation_id",
        "release_sha",
        "release_tree_sha",
        "role",
        "operation_manifest_sha256",
        "image_role",
        "archive_sha256",
        "content_identity",
        "baseline_runtime_image_ids",
        "runtime_image_id",
        "image",
    }
)
ATTESTATION_FIELDS = frozenset(
    {
        "schema",
        "operation_id",
        "release_sha",
        "release_tree_sha",
        "operation_manifest_sha256",
        "role",
        "image_artifacts",
        "runtime_image_ids",
        "images",
        "images_built",
        "images_pulled",
        "containers_created",
        "containers_started",
        "services_started",
        "networks_created",
        "volumes_created",
        "current_mutated",
        "data_mutated",
    }
)
PHASES = (
    "inputs-verified",
    "release-materialized",
    "archives-verified",
    "app-loaded",
    "postgres-loaded",
    "redis-loaded",
    "nginx-loaded",
    "attested",
)
JOURNAL_FIELDS = frozenset(
    {
        "schema",
        "operation_id",
        "release_sha",
        "release_tree_sha",
        "role",
        "operation_manifest_sha256",
        "status",
        "completed_phases",
        "current_phase",
        "archive_evidence",
        "load_intents",
        "runtime_image_ids",
        "images",
        "events",
        "event_tail_sha256",
        "state_sha256",
    }
)

SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
IMAGE_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
ZERO_SHA256 = "0" * 64
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_JOURNAL_BYTES = 2 * 1024 * 1024
MAX_ATTESTATION_BYTES = 2 * 1024 * 1024
MAX_AGENT_BYTES = 8 * 1024 * 1024
MAX_CONVERGENCE_SOURCE_SET_LAUNCHER_BYTES = 1024 * 1024
MAX_ARTIFACT_BYTES = 64 * 1024 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 250_000
MAX_ARCHIVE_CONFIG_BYTES = 16 * 1024 * 1024
MAX_ARCHIVE_MANIFEST_BYTES = 1024 * 1024
MAX_COMMAND_OUTPUT_BYTES = 8 * 1024 * 1024
MAX_DOCKER_IMAGE_IDS = 4096
MAX_RECONCILIATION_EVIDENCE_BYTES = 2 * 1024 * 1024
PROCESS_GROUP_TERM_SECONDS = 1.0
PROCESS_TREE_QUIESCENCE_SECONDS = 0.25
DOCKER_INVENTORY_SCAN_SECONDS = 300.0
DOCKER_LOAD_RECONCILE_SECONDS = 30.0
DOCKER_LOAD_RECONCILE_INTERVAL_SECONDS = 0.1
PR_SET_CHILD_SUBREAPER = 36
IMAGE_LOAD_RECONCILIATION_SCHEMA = (
    "production-shadow-finland-image-load-reconciliation-v1"
)
SAFE_ENV = {
    "PATH": "/usr/bin:/bin",
    "HOME": "/nonexistent",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "DOCKER_CONFIG": "/nonexistent",
}
SAFE_GIT_ENV = {
    **SAFE_ENV,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_OPTIONAL_LOCKS": "0",
}


class FinlandStageError(RuntimeError):
    """Raised when the bounded stage/load contract cannot be proven."""


class FinlandStageCancellation(FinlandStageError):
    """Raised once when controller liveness or a catchable signal is lost."""


class BoundedStageRunnerError(FinlandStageError):
    """Raised when an isolated stage subprocess cannot be bounded."""


Checkpoint = Callable[[str], None]
Runner = Callable[..., subprocess.CompletedProcess[bytes]]


def canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise FinlandStageError("value is not canonical JSON") from exc


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _strict_json(raw: bytes, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(
            raw.decode("ascii"),
            object_pairs_hook=_strict_object,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"invalid JSON constant: {value}")
            ),
        )
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise FinlandStageError(f"{label} is not strict JSON") from exc
    if not isinstance(value, dict):
        raise FinlandStageError(f"{label} must contain one JSON object")
    return value


def _canonical_uuid4(value: Any, *, label: str) -> str:
    if not isinstance(value, str):
        raise FinlandStageError(f"{label} must be a canonical UUIDv4")
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError) as exc:
        raise FinlandStageError(f"{label} must be a canonical UUIDv4") from exc
    if parsed.version != 4 or str(parsed) != value:
        raise FinlandStageError(f"{label} must be a canonical UUIDv4")
    return value


def _nonzero_sha256(value: Any, *, label: str) -> str:
    if (
        not isinstance(value, str)
        or SHA256_RE.fullmatch(value) is None
        or value == ZERO_SHA256
    ):
        raise FinlandStageError(f"{label} must be a nonzero SHA-256")
    return value


def _bounded_size(value: Any, *, label: str, maximum: int) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 1 <= value <= maximum
    ):
        raise FinlandStageError(f"{label} is outside its size bound")
    return value


def _content_sha256(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value)).hexdigest()


def verify_content_descriptor(descriptor: Any) -> str:
    if not isinstance(descriptor, dict) or set(descriptor) != DESCRIPTOR_FIELDS:
        raise FinlandStageError("image content descriptor fields are not exact")
    layers = descriptor["rootfs_layers"]
    if (
        not isinstance(descriptor["architecture"], str)
        or descriptor["architecture"] != "amd64"
        or not isinstance(descriptor["os"], str)
        or descriptor["os"] != "linux"
        or not isinstance(descriptor["created"], str)
        or not descriptor["created"]
        or not isinstance(descriptor["config_sha256"], str)
        or IMAGE_ID_RE.fullmatch(descriptor["config_sha256"]) is None
        or descriptor["rootfs_type"] != "layers"
        or not isinstance(layers, list)
        or not layers
        or any(
            not isinstance(layer, str) or IMAGE_ID_RE.fullmatch(layer) is None
            for layer in layers
        )
    ):
        raise FinlandStageError("image content descriptor is malformed")
    return _content_sha256(descriptor)


def _content_descriptor(
    *,
    architecture: Any,
    operating_system: Any,
    created: Any,
    config: Any,
    rootfs_type: Any,
    rootfs_layers: Any,
) -> tuple[dict[str, Any], str]:
    if not isinstance(config, dict):
        raise FinlandStageError("image configuration metadata is invalid")
    descriptor = {
        "architecture": str(architecture or ""),
        "os": str(operating_system or ""),
        "created": str(created or ""),
        "config_sha256": _content_sha256(config),
        "rootfs_type": str(rootfs_type or ""),
        "rootfs_layers": (
            list(rootfs_layers) if isinstance(rootfs_layers, list) else rootfs_layers
        ),
    }
    return descriptor, verify_content_descriptor(descriptor)


def image_content_descriptor_from_archive(
    config: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    rootfs = config.get("rootfs") if isinstance(config, dict) else None
    if not isinstance(rootfs, dict):
        raise FinlandStageError("Docker archive rootfs metadata is invalid")
    return _content_descriptor(
        architecture=config.get("architecture"),
        operating_system=config.get("os"),
        created=config.get("created"),
        config=config.get("config"),
        rootfs_type=rootfs.get("type"),
        rootfs_layers=rootfs.get("diff_ids"),
    )


def image_content_descriptor_from_inspect(
    image: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    rootfs = image.get("RootFS") if isinstance(image, dict) else None
    if not isinstance(rootfs, dict):
        raise FinlandStageError("Docker inspect rootfs metadata is invalid")
    return _content_descriptor(
        architecture=image.get("Architecture"),
        operating_system=image.get("Os"),
        created=image.get("Created"),
        config=image.get("Config"),
        rootfs_type=rootfs.get("Type"),
        rootfs_layers=rootfs.get("Layers"),
    )


def canonical_paths(
    operation_id: str,
    release_sha: str,
    role: str,
) -> dict[str, Path | str]:
    operation_id = _canonical_uuid4(operation_id, label="operation_id")
    if not isinstance(release_sha, str) or SHA40_RE.fullmatch(release_sha) is None:
        raise FinlandStageError("release_sha is invalid")
    if role not in STAGE_ROLES:
        raise FinlandStageError("role is not a Finland Docker role")
    role_path = ROLE_PATHS[role]
    project_root = PROJECT_ROOT_PREFIX / operation_id
    secret_role_root = SECRET_ROOT_PREFIX / operation_id / role_path
    return {
        "project_name": (
            f"tb3p-{operation_id.replace('-', '')}-{role_path}"
        ),
        "project_root": project_root,
        "release_root": project_root / "releases" / release_sha,
        "incoming_root": project_root / "incoming" / role_path,
        "secret_role_root": secret_role_root,
        "agent": project_root / "incoming" / role_path / AGENT_FILENAME,
        "manifest": project_root / "incoming" / role_path / MANIFEST_FILENAME,
        "secret_manifest": secret_role_root / MANIFEST_FILENAME,
        "attestation": secret_role_root / ATTESTATION_FILENAME,
        "journal": secret_role_root / JOURNAL_FILENAME,
        "lock": secret_role_root / LOCK_FILENAME,
    }


def image_load_reconciliation_path(
    paths: Mapping[str, Path | str],
    image_role: str,
) -> Path:
    if image_role not in IMAGE_ROLES:
        raise FinlandStageError("image reconciliation role is invalid")
    root = paths.get("secret_role_root")
    if not isinstance(root, Path) or not root.is_absolute():
        raise FinlandStageError("image reconciliation root is invalid")
    return root / f"image-load-{image_role}-reconciliation.json"


def validate_manifest(document: dict[str, Any]) -> dict[str, Any]:
    if set(document) != MANIFEST_FIELDS:
        raise FinlandStageError("stage manifest fields are not exact")
    if document["schema"] != MANIFEST_SCHEMA:
        raise FinlandStageError("stage manifest schema is invalid")
    operation_id = _canonical_uuid4(document["operation_id"], label="operation_id")
    release_sha = document["release_sha"]
    release_tree_sha = document["release_tree_sha"]
    role = document["role"]
    if (
        not isinstance(release_sha, str)
        or SHA40_RE.fullmatch(release_sha) is None
        or not isinstance(release_tree_sha, str)
        or SHA40_RE.fullmatch(release_tree_sha) is None
    ):
        raise FinlandStageError("release commit or tree identity is invalid")
    paths = canonical_paths(operation_id, release_sha, role)
    for field in (
        "project_name",
        "project_root",
        "release_root",
        "incoming_root",
        "secret_role_root",
    ):
        expected = str(paths[field]) if isinstance(paths[field], Path) else paths[field]
        if document[field] != expected:
            raise FinlandStageError(f"{field} differs from its canonical value")
    _nonzero_sha256(document["bootstrap_sha256"], label="bootstrap_sha256")
    if (
        document["postgres_runtime_uid"] != POSTGRES_RUNTIME_UID
        or document["postgres_runtime_gid"] != POSTGRES_RUNTIME_GID
    ):
        raise FinlandStageError("PostgreSQL runtime UID/GID must be 70")
    if document["pull_policy"] != "never":
        raise FinlandStageError("stage manifest pull policy must be never")

    artifacts = document["artifacts"]
    if not isinstance(artifacts, dict) or set(artifacts) != set(ARTIFACT_KINDS):
        raise FinlandStageError("stage artifact kinds are not exact")
    for kind in ARTIFACT_KINDS:
        row = artifacts[kind]
        if not isinstance(row, dict) or set(row) != ARTIFACT_FIELDS:
            raise FinlandStageError(f"artifact {kind} fields are not exact")
        if (
            row["kind"] != kind
            or row["filename"] != ARTIFACT_FILENAMES[kind]
            or row["format"] != ARTIFACT_FORMATS[kind]
        ):
            raise FinlandStageError(f"artifact {kind} identity is not canonical")
        _nonzero_sha256(row["sha256"], label=f"artifact {kind} sha256")
        _bounded_size(
            row["bytes"],
            label=f"artifact {kind} bytes",
            maximum=MAX_ARTIFACT_BYTES,
        )
    if len({artifacts[kind]["sha256"] for kind in ARTIFACT_KINDS}) != len(
        ARTIFACT_KINDS
    ):
        raise FinlandStageError("artifact SHA-256 values must be distinct")

    image_artifacts = document["image_artifacts"]
    if (
        not isinstance(image_artifacts, dict)
        or set(image_artifacts) != set(IMAGE_ROLES)
    ):
        raise FinlandStageError("image artifact roles are not exact")
    for role_name in IMAGE_ROLES:
        row = image_artifacts[role_name]
        if not isinstance(row, dict) or set(row) != IMAGE_ARTIFACT_FIELDS:
            raise FinlandStageError(
                f"image artifact {role_name} fields are not exact"
            )
        _nonzero_sha256(
            row["archive_sha256"],
            label=f"image artifact {role_name} archive_sha256",
        )
        _bounded_size(
            row["archive_bytes"],
            label=f"image artifact {role_name} archive_bytes",
            maximum=MAX_ARTIFACT_BYTES,
        )
        for field in ("config_digest", "content_identity"):
            value = row[field]
            if (
                not isinstance(value, str)
                or IMAGE_ID_RE.fullmatch(value) is None
                or value == "sha256:" + ZERO_SHA256
            ):
                raise FinlandStageError(
                    f"image artifact {role_name} {field} is invalid"
                )
        if verify_content_descriptor(row["content_descriptor"]) != row[
            "content_identity"
        ]:
            raise FinlandStageError(
                f"image artifact {role_name} content identity differs"
            )
        artifact_kind = f"{role_name}-image-archive"
        if (
            artifacts[artifact_kind]["sha256"] != row["archive_sha256"]
            or artifacts[artifact_kind]["bytes"] != row["archive_bytes"]
        ):
            raise FinlandStageError(
                f"image artifact {role_name} differs from artifact inventory"
            )
    for field in ("archive_sha256", "config_digest", "content_identity"):
        if len(
            {image_artifacts[role_name][field] for role_name in IMAGE_ROLES}
        ) != len(IMAGE_ROLES):
            raise FinlandStageError(f"all four image {field} values must be distinct")
    return document


def load_manifest_bytes(
    raw: bytes,
    *,
    expected_sha256: str | None = None,
) -> tuple[dict[str, Any], str]:
    if not 1 <= len(raw) <= MAX_MANIFEST_BYTES:
        raise FinlandStageError("stage manifest is empty or oversized")
    digest = hashlib.sha256(raw).hexdigest()
    if expected_sha256 is not None and digest != _nonzero_sha256(
        expected_sha256, label="operation_manifest_sha256"
    ):
        raise FinlandStageError("stage manifest SHA-256 differs")
    document = validate_manifest(_strict_json(raw, label="stage manifest"))
    if raw != canonical_json(document):
        raise FinlandStageError("stage manifest bytes are not canonical")
    return document, digest


def _decode_request(
    encoded: str,
    *,
    bootstrap: bool,
) -> dict[str, Any]:
    if (
        not isinstance(encoded, str)
        or not encoded
        or len(encoded) > 64 * 1024
        or re.fullmatch(r"[A-Za-z0-9_-]+", encoded) is None
    ):
        raise FinlandStageError("request encoding is invalid")
    try:
        padding = "=" * (-len(encoded) % 4)
        raw = base64.b64decode(
            encoded + padding,
            altchars=b"-_",
            validate=True,
        )
    except (ValueError, binascii.Error) as exc:
        raise FinlandStageError("request encoding is invalid") from exc
    document = _strict_json(raw, label="stage request")
    if raw != canonical_json(document):
        raise FinlandStageError("stage request bytes are not canonical")
    expected_fields = (
        BOOTSTRAP_REQUEST_FIELDS if bootstrap else REQUEST_FIELDS
    )
    if set(document) != expected_fields:
        raise FinlandStageError("stage request fields are not exact")
    expected_schema = BOOTSTRAP_REQUEST_SCHEMA if bootstrap else REQUEST_SCHEMA
    expected_action = "install-bootstrap" if bootstrap else "stage"
    if (
        document["schema"] != expected_schema
        or document["action"] != expected_action
    ):
        raise FinlandStageError("stage request operation is invalid")
    _canonical_uuid4(document["operation_id"], label="operation_id")
    if document["role"] not in STAGE_ROLES:
        raise FinlandStageError("stage request role is invalid")
    _nonzero_sha256(document["agent_sha256"], label="agent_sha256")
    if not bootstrap:
        if (
            not isinstance(document["release_sha"], str)
            or SHA40_RE.fullmatch(document["release_sha"]) is None
            or not isinstance(document["release_tree_sha"], str)
            or SHA40_RE.fullmatch(document["release_tree_sha"]) is None
            or document["pull_policy"] != "never"
        ):
            raise FinlandStageError("stage request release or pull policy is invalid")
        _nonzero_sha256(
            document["operation_manifest_sha256"],
            label="operation_manifest_sha256",
        )
    return document


def encode_request(document: Mapping[str, Any]) -> str:
    return base64.urlsafe_b64encode(canonical_json(document)).decode("ascii").rstrip(
        "="
    )


@contextmanager
def _private_umask() -> Iterator[None]:
    previous = os.umask(0o077)
    try:
        yield
    finally:
        os.umask(previous)


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
    except OSError as exc:
        raise FinlandStageError("operation directory could not be synchronized") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _assert_directory(
    path: Path,
    *,
    required_uid: int,
    private: bool,
) -> None:
    try:
        metadata = path.stat(follow_symlinks=False)
    except OSError as exc:
        raise FinlandStageError("operation directory is unavailable") from exc
    mode = stat.S_IMODE(metadata.st_mode)
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != required_uid
        or (private and mode != 0o700)
        or (not private and mode & 0o022)
    ):
        raise FinlandStageError("operation directory ownership or mode is unsafe")


def _ensure_private_directory(path: Path, *, required_uid: int) -> None:
    if path.exists() or path.is_symlink():
        _assert_directory(path, required_uid=required_uid, private=True)
        return
    try:
        with _private_umask():
            path.mkdir(mode=0o700)
        _fsync_directory(path.parent)
    except OSError as exc:
        raise FinlandStageError("operation directory could not be created") from exc
    _assert_directory(path, required_uid=required_uid, private=True)


def ensure_operation_directories(
    operation_id: str,
    release_sha: str,
    role: str,
    *,
    required_uid: int = 0,
) -> dict[str, Path | str]:
    paths = canonical_paths(operation_id, release_sha, role)
    _assert_directory(
        PROJECT_ROOT_PREFIX,
        required_uid=required_uid,
        private=False,
    )
    _ensure_private_directory(
        paths["project_root"],  # type: ignore[arg-type]
        required_uid=required_uid,
    )
    _ensure_private_directory(
        paths["project_root"] / "releases",  # type: ignore[operator]
        required_uid=required_uid,
    )
    _ensure_private_directory(
        paths["project_root"] / "incoming",  # type: ignore[operator]
        required_uid=required_uid,
    )
    _ensure_private_directory(
        paths["incoming_root"],  # type: ignore[arg-type]
        required_uid=required_uid,
    )
    _assert_directory(
        SECRET_ROOT_PREFIX,
        required_uid=required_uid,
        private=False,
    )
    secret_operation = SECRET_ROOT_PREFIX / operation_id
    _ensure_private_directory(secret_operation, required_uid=required_uid)
    _ensure_private_directory(
        paths["secret_role_root"],  # type: ignore[arg-type]
        required_uid=required_uid,
    )
    return paths


def observe_local_ipv4_addresses() -> set[str]:
    addresses: set[str] = set()
    try:
        interfaces = socket.if_nameindex()
        handle = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    except OSError as exc:
        raise FinlandStageError(
            "cannot inspect local host network identity"
        ) from exc
    try:
        for _, name in interfaces:
            try:
                packed = struct.pack("256s", name.encode("ascii")[:15])
                result = fcntl.ioctl(handle.fileno(), 0x8915, packed)
            except (OSError, UnicodeEncodeError):
                continue
            addresses.add(socket.inet_ntoa(result[20:24]))
    finally:
        handle.close()
    if not addresses:
        raise FinlandStageError("local host has no observable IPv4 identity")
    return addresses


def _verify_role_host(
    role: str,
    *,
    observed_host_addresses: set[str] | None,
) -> None:
    addresses = (
        observe_local_ipv4_addresses()
        if observed_host_addresses is None
        else set(observed_host_addresses)
    )
    if ROLE_HOSTS[role] not in addresses:
        raise FinlandStageError(
            "local host identity differs from the manifest-bound Finland role"
        )


@contextmanager
def _held_file(
    path: Path,
    *,
    required_uid: int,
    expected_mode: int,
    maximum: int,
    allow_two_links: bool = False,
    nonblocking: bool = False,
) -> Iterator[tuple[BinaryIO, os.stat_result]]:
    descriptor = -1
    stream: BinaryIO | None = None
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | (getattr(os, "O_NONBLOCK", 0) if nonblocking else 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        before = os.fstat(descriptor)
        allowed_links = {1, 2} if allow_two_links else {1}
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != required_uid
            or stat.S_IMODE(before.st_mode) != expected_mode
            or before.st_nlink not in allowed_links
            or not 1 <= before.st_size <= maximum
        ):
            raise FinlandStageError("operation file ownership or mode is unsafe")
        stream = os.fdopen(descriptor, "rb", closefd=True)
        descriptor = -1
        yield stream, before
        after = os.fstat(stream.fileno())
        stable = (
            "st_dev",
            "st_ino",
            "st_mode",
            "st_uid",
            "st_gid",
            "st_nlink",
            "st_size",
            "st_mtime_ns",
        )
        if any(getattr(before, field) != getattr(after, field) for field in stable):
            raise FinlandStageError("operation file changed while being read")
    except FinlandStageError:
        raise
    except OSError as exc:
        raise FinlandStageError("operation file could not be read safely") from exc
    finally:
        if stream is not None:
            stream.close()
        elif descriptor >= 0:
            os.close(descriptor)


def hash_secure_file(
    path: Path,
    *,
    required_uid: int,
    expected_mode: int,
    maximum: int,
    allow_two_links: bool = False,
) -> tuple[str, int]:
    with _held_file(
        path,
        required_uid=required_uid,
        expected_mode=expected_mode,
        maximum=maximum,
        allow_two_links=allow_two_links,
    ) as (stream, before):
        digest = hashlib.sha256()
        size = 0
        while chunk := stream.read(1024 * 1024):
            size += len(chunk)
            if size > maximum:
                raise FinlandStageError("operation file exceeds its size bound")
            digest.update(chunk)
    return digest.hexdigest(), size


def _read_secure_file(
    path: Path,
    *,
    required_uid: int,
    expected_mode: int,
    maximum: int,
    allow_two_links: bool = False,
) -> bytes:
    with _held_file(
        path,
        required_uid=required_uid,
        expected_mode=expected_mode,
        maximum=maximum,
        allow_two_links=allow_two_links,
    ) as (stream, _before):
        payload = stream.read(maximum + 1)
    if not 1 <= len(payload) <= maximum:
        raise FinlandStageError("operation file is empty or oversized")
    return payload


def transfer_partial_path(destination: Path) -> Path:
    return destination.with_name(f".{destination.name}.transfer")


def _publish_transfer_partial(
    destination: Path,
    *,
    expected_sha256: str,
    expected_bytes: int,
    required_uid: int,
    mode: int,
) -> None:
    partial = transfer_partial_path(destination)
    if partial.exists() or partial.is_symlink():
        try:
            partial_metadata = partial.stat(follow_symlinks=False)
        except OSError as exc:
            raise FinlandStageError("incoming transfer partial is unsafe") from exc
        if (
            not stat.S_ISREG(partial_metadata.st_mode)
            or partial_metadata.st_uid != required_uid
            or stat.S_IMODE(partial_metadata.st_mode) != mode
            or partial_metadata.st_nlink not in {1, 2}
            or not 1 <= partial_metadata.st_size <= expected_bytes
        ):
            raise FinlandStageError("incoming transfer partial is unsafe")
        if partial_metadata.st_nlink == 2:
            try:
                destination_metadata = destination.stat(follow_symlinks=False)
            except OSError as exc:
                raise FinlandStageError(
                    "incoming transfer link identity is ambiguous"
                ) from exc
            if (
                not stat.S_ISREG(destination_metadata.st_mode)
                or partial_metadata.st_dev != destination_metadata.st_dev
                or partial_metadata.st_ino != destination_metadata.st_ino
            ):
                raise FinlandStageError(
                    "incoming transfer link identity is ambiguous"
                )
            partial.unlink()
            _fsync_directory(destination.parent)
    if destination.exists() or destination.is_symlink():
        if hash_secure_file(
            destination,
            required_uid=required_uid,
            expected_mode=mode,
            maximum=max(expected_bytes, 1),
        ) != (expected_sha256, expected_bytes):
            raise FinlandStageError("create-only incoming destination differs")
        if partial.exists() or partial.is_symlink():
            if hash_secure_file(
                partial,
                required_uid=required_uid,
                expected_mode=mode,
                maximum=max(expected_bytes, 1),
            ) != (expected_sha256, expected_bytes):
                raise FinlandStageError("incoming transfer partial differs")
            partial.unlink()
            _fsync_directory(destination.parent)
        return
    if hash_secure_file(
        partial,
        required_uid=required_uid,
        expected_mode=mode,
        maximum=max(expected_bytes, 1),
    ) != (expected_sha256, expected_bytes):
        raise FinlandStageError("incoming transfer partial identity differs")
    try:
        os.link(partial, destination, follow_symlinks=False)
        _fsync_directory(destination.parent)
        partial.unlink()
        _fsync_directory(destination.parent)
    except FileExistsError as exc:
        raise FinlandStageError("create-only incoming destination appeared") from exc
    except OSError as exc:
        raise FinlandStageError("incoming transfer could not be published") from exc
    if hash_secure_file(
        destination,
        required_uid=required_uid,
        expected_mode=mode,
        maximum=max(expected_bytes, 1),
    ) != (expected_sha256, expected_bytes):
        raise FinlandStageError("published incoming file identity differs")


def _write_create_only(
    destination: Path,
    payload: bytes,
    *,
    required_uid: int,
    mode: int,
    maximum: int,
) -> str:
    if not 1 <= len(payload) <= maximum:
        raise FinlandStageError("create-only payload is empty or oversized")
    expected = hashlib.sha256(payload).hexdigest()
    temporary = destination.with_name(f".{destination.name}.materializing")
    if temporary.exists() or temporary.is_symlink():
        try:
            temporary_metadata = temporary.stat(follow_symlinks=False)
        except OSError as exc:
            raise FinlandStageError("create-only temporary is unsafe") from exc
        if (
            not stat.S_ISREG(temporary_metadata.st_mode)
            or temporary_metadata.st_uid != required_uid
            or stat.S_IMODE(temporary_metadata.st_mode) != mode
            or temporary_metadata.st_nlink not in {1, 2}
            or not 0 <= temporary_metadata.st_size <= maximum
        ):
            raise FinlandStageError("create-only temporary is unsafe")
        if temporary_metadata.st_nlink == 2:
            try:
                destination_metadata = destination.stat(follow_symlinks=False)
            except OSError as exc:
                raise FinlandStageError(
                    "create-only temporary link identity is ambiguous"
                ) from exc
            if (
                not stat.S_ISREG(destination_metadata.st_mode)
                or temporary_metadata.st_dev != destination_metadata.st_dev
                or temporary_metadata.st_ino != destination_metadata.st_ino
            ):
                raise FinlandStageError(
                    "create-only temporary link identity is ambiguous"
                )
        temporary.unlink()
        _fsync_directory(destination.parent)
    if destination.exists() or destination.is_symlink():
        if hash_secure_file(
            destination,
            required_uid=required_uid,
            expected_mode=mode,
            maximum=maximum,
        ) != (expected, len(payload)):
            raise FinlandStageError("create-only destination differs")
        return expected
    descriptor = -1
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            mode,
        )
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short create-only write")
            view = view[written:]
        os.fchmod(descriptor, mode)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        _publish_transfer_like_temporary(
            temporary,
            destination,
            expected_sha256=expected,
            expected_bytes=len(payload),
            required_uid=required_uid,
            mode=mode,
            maximum=maximum,
        )
    except FinlandStageError:
        raise
    except OSError as exc:
        raise FinlandStageError("create-only destination could not be written") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    return expected


def _publish_transfer_like_temporary(
    temporary: Path,
    destination: Path,
    *,
    expected_sha256: str,
    expected_bytes: int,
    required_uid: int,
    mode: int,
    maximum: int,
) -> None:
    if hash_secure_file(
        temporary,
        required_uid=required_uid,
        expected_mode=mode,
        maximum=maximum,
    ) != (expected_sha256, expected_bytes):
        raise FinlandStageError("create-only temporary identity differs")
    try:
        os.link(temporary, destination, follow_symlinks=False)
    except FileExistsError:
        if hash_secure_file(
            destination,
            required_uid=required_uid,
            expected_mode=mode,
            maximum=maximum,
        ) != (expected_sha256, expected_bytes):
            raise FinlandStageError("create-only destination differs")
    except OSError as exc:
        raise FinlandStageError("create-only publication failed") from exc
    _fsync_directory(destination.parent)
    temporary.unlink()
    _fsync_directory(destination.parent)


def install_bootstrap(
    request: Mapping[str, Any],
    *,
    executing_path: Path,
    required_uid: int = 0,
    observed_host_addresses: set[str] | None = None,
) -> dict[str, Any]:
    if os.geteuid() != required_uid or required_uid != 0:
        raise FinlandStageError("Finland stage agent must run as root")
    request = _decode_request(
        encode_request(request),
        bootstrap=True,
    )
    _verify_role_host(
        str(request["role"]),
        observed_host_addresses=observed_host_addresses,
    )
    paths = ensure_operation_directories(
        str(request["operation_id"]),
        "0" * 40,
        str(request["role"]),
        required_uid=required_uid,
    )
    # Bootstrap installation is independent of a release.  Replace the
    # placeholder release-derived values with the exact incoming path.
    incoming = (
        PROJECT_ROOT_PREFIX
        / str(request["operation_id"])
        / "incoming"
        / ROLE_PATHS[str(request["role"])]
    )
    destination = incoming / AGENT_FILENAME
    expected_partial = transfer_partial_path(destination)
    if executing_path != expected_partial:
        raise FinlandStageError("bootstrap agent is not at its fixed transfer path")
    expected_sha = _nonzero_sha256(
        request["agent_sha256"], label="agent_sha256"
    )
    observed = hash_secure_file(
        executing_path,
        required_uid=required_uid,
        expected_mode=0o700,
        maximum=MAX_AGENT_BYTES,
        allow_two_links=True,
    )
    if observed[0] != expected_sha:
        raise FinlandStageError("bootstrap agent SHA-256 differs")
    _publish_transfer_partial(
        destination,
        expected_sha256=expected_sha,
        expected_bytes=observed[1],
        required_uid=required_uid,
        mode=0o700,
    )
    return {
        "schema": VERSION_SCHEMA,
        "version": AGENT_VERSION,
        "agent_sha256": expected_sha,
        "installed_path": str(destination),
    }


def _agent_version(
    path: Path,
    *,
    expected_sha256: str,
    required_uid: int,
) -> dict[str, Any]:
    observed, size = hash_secure_file(
        path,
        required_uid=required_uid,
        expected_mode=0o700,
        maximum=MAX_AGENT_BYTES,
    )
    if observed != _nonzero_sha256(expected_sha256, label="agent_sha256"):
        raise FinlandStageError("stage agent SHA-256 differs")
    return {
        "schema": VERSION_SCHEMA,
        "version": AGENT_VERSION,
        "agent_sha256": observed,
        "agent_bytes": size,
    }


def _anonymous_read_pipe_identity(
    descriptor: int,
    *,
    label: str,
) -> tuple[int, int]:
    if type(descriptor) is not int or descriptor < 0:
        raise FinlandStageError(f"{label} descriptor is invalid")
    try:
        metadata = os.fstat(descriptor)
        flags = fcntl.fcntl(descriptor, fcntl.F_GETFL)
        target = os.readlink(f"/proc/self/fd/{descriptor}")
    except OSError as exc:
        raise FinlandStageError(f"{label} descriptor is unavailable") from exc
    if (
        not stat.S_ISFIFO(metadata.st_mode)
        or flags & os.O_ACCMODE != os.O_RDONLY
        or target != f"pipe:[{metadata.st_ino}]"
    ):
        raise FinlandStageError(
            f"{label} must be an anonymous read-only pipe"
        )
    try:
        entries = tuple(Path("/proc/self/fd").iterdir())
    except OSError as exc:
        raise FinlandStageError(
            f"{label} descriptor closure cannot be inspected"
        ) from exc
    for entry in entries:
        if not entry.name.isdecimal() or int(entry.name, 10) == descriptor:
            continue
        candidate = int(entry.name, 10)
        try:
            observed = os.fstat(candidate)
            observed_flags = fcntl.fcntl(candidate, fcntl.F_GETFL)
        except OSError:
            continue
        if (
            (observed.st_dev, observed.st_ino)
            == (metadata.st_dev, metadata.st_ino)
            and observed_flags & os.O_ACCMODE in {os.O_WRONLY, os.O_RDWR}
        ):
            raise FinlandStageError(
                f"{label} writer end is held by the stage process"
            )
    return metadata.st_dev, metadata.st_ino


class ControllerLivenessGuard:
    """Deliver one catchable cancellation when controller authority is lost."""

    _WAKE_SIGNAL = signal.SIGUSR1
    _HANDLED_SIGNALS = (
        signal.SIGHUP,
        signal.SIGTERM,
        signal.SIGINT,
        _WAKE_SIGNAL,
    )

    def __init__(self, control_fd: int | None) -> None:
        if threading.current_thread() is not threading.main_thread():
            raise FinlandStageError(
                "Finland stage execution must run in the main thread"
            )
        self._fd: int | None = None
        if control_fd is not None:
            _anonymous_read_pipe_identity(
                control_fd,
                label="controller liveness",
            )
            try:
                self._fd = os.dup(control_fd)
                os.set_inheritable(self._fd, False)
                os.set_blocking(self._fd, False)
            except OSError as exc:
                raise FinlandStageError(
                    "controller liveness pipe cannot be secured"
                ) from exc
        self._cancelled = threading.Event()
        self._exception_delivered = threading.Event()
        self._stopping = threading.Event()
        self._reason = "controller liveness was lost"
        self._old_handlers: dict[int, Any] = {}
        self._monitor: threading.Thread | None = None

    def _cancel(self, reason: str, *, wake_main: bool) -> None:
        if self._cancelled.is_set():
            return
        self._reason = reason
        self._cancelled.set()
        if wake_main:
            main_ident = threading.main_thread().ident
            if main_ident is not None:
                try:
                    signal.pthread_kill(main_ident, self._WAKE_SIGNAL)
                except (OSError, RuntimeError):
                    pass

    def _sample(self) -> None:
        if self._fd is None:
            return
        ready, _write, _error = select.select([self._fd], [], [], 0)
        if not ready:
            return
        try:
            payload = os.read(self._fd, 1)
        except BlockingIOError:
            return
        self._cancel(
            (
                "controller liveness pipe reached EOF"
                if payload == b""
                else "controller liveness pipe carried forbidden data"
            ),
            wake_main=False,
        )
        self.check()

    def _handle_signal(self, signum: int, _frame: Any) -> None:
        if signum == self._WAKE_SIGNAL and self._cancelled.is_set():
            pass
        else:
            self._cancel(
                f"Finland stage agent received signal {signum}",
                wake_main=False,
            )
        self.check()

    def _monitor_control(self) -> None:
        assert self._fd is not None
        selector = selectors.DefaultSelector()
        try:
            selector.register(self._fd, selectors.EVENT_READ)
            while not self._stopping.is_set():
                if not selector.select(0.05):
                    continue
                try:
                    payload = os.read(self._fd, 1)
                except BlockingIOError:
                    continue
                except OSError:
                    if self._stopping.is_set():
                        return
                    payload = b""
                self._cancel(
                    (
                        "controller liveness pipe reached EOF"
                        if payload == b""
                        else "controller liveness pipe carried forbidden data"
                    ),
                    wake_main=True,
                )
                return
        finally:
            selector.close()

    def __enter__(self) -> ControllerLivenessGuard:
        try:
            for signum in self._HANDLED_SIGNALS:
                self._old_handlers[signum] = signal.getsignal(signum)
                signal.signal(signum, self._handle_signal)
            self._sample()
            if self._fd is not None:
                self._monitor = threading.Thread(
                    target=self._monitor_control,
                    name="finland-stage-controller-liveness",
                    daemon=True,
                )
                self._monitor.start()
            self.check()
            return self
        except BaseException:
            self._restore()
            raise

    def check(self) -> None:
        if (
            self._cancelled.is_set()
            and not self._exception_delivered.is_set()
        ):
            self._exception_delivered.set()
            raise FinlandStageCancellation(self._reason)

    def _restore(self) -> None:
        self._stopping.set()
        if self._monitor is not None:
            self._monitor.join(timeout=1)
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None
        for signum, handler in self._old_handlers.items():
            signal.signal(signum, handler)
        self._old_handlers.clear()

    def __exit__(self, error_type: Any, _value: Any, _traceback: Any) -> None:
        deliver_after_restore = (
            self._cancelled.is_set()
            and error_type is None
            and not self._exception_delivered.is_set()
        )
        reason = self._reason
        self._exception_delivered.set()
        self._restore()
        if deliver_after_restore:
            raise FinlandStageCancellation(reason)


_ACTIVE_EXECUTION_AUTHORITY: ControllerLivenessGuard | None = None


@contextmanager
def _execution_authority(
    control_fd: int | None = None,
) -> Iterator[ControllerLivenessGuard]:
    global _ACTIVE_EXECUTION_AUTHORITY
    if _ACTIVE_EXECUTION_AUTHORITY is not None:
        if control_fd is not None:
            raise FinlandStageError(
                "controller liveness guard is already active"
            )
        yield _ACTIVE_EXECUTION_AUTHORITY
        return
    authority = ControllerLivenessGuard(control_fd)
    with authority:
        _ACTIVE_EXECUTION_AUTHORITY = authority
        try:
            yield authority
        finally:
            _ACTIVE_EXECUTION_AUTHORITY = None


def _enable_child_subreaper() -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0) != 0:
        error = ctypes.get_errno()
        raise BoundedStageRunnerError(
            f"stage child subreaper setup failed with errno {error}"
        )


@dataclass(frozen=True)
class ProcessIdentity:
    process_id: int
    parent_id: int
    process_group: int
    session_id: int
    starttime: int
    state: str

    @property
    def key(self) -> tuple[int, int]:
        return self.process_id, self.starttime


def _proc_identity(process_id: int) -> tuple[int, int, int, int, str]:
    try:
        payload = Path(f"/proc/{process_id}/stat").read_text(
            encoding="ascii"
        )
        fields = payload[payload.rindex(") ") + 2 :].split()
        if len(fields) < 20:
            raise ValueError("short process stat")
        state = fields[0]
        parent = int(fields[1], 10)
        group = int(fields[2], 10)
        session = int(fields[3], 10)
        starttime = int(fields[19], 10)
    except (OSError, UnicodeError, ValueError) as exc:
        raise BoundedStageRunnerError(
            "stage subprocess identity is unavailable"
        ) from exc
    return parent, group, session, starttime, state


def _process_snapshot() -> dict[int, ProcessIdentity]:
    try:
        entries = tuple(Path("/proc").iterdir())
    except OSError as exc:
        raise BoundedStageRunnerError(
            "stage process closure cannot be enumerated"
        ) from exc
    observed: dict[int, ProcessIdentity] = {}
    for entry in entries:
        if not entry.name.isdecimal():
            continue
        process_id = int(entry.name, 10)
        try:
            parent, group, session, starttime, state = _proc_identity(
                process_id
            )
        except BoundedStageRunnerError:
            continue
        observed[process_id] = ProcessIdentity(
            process_id=process_id,
            parent_id=parent,
            process_group=group,
            session_id=session,
            starttime=starttime,
            state=state,
        )
    return observed


def _direct_child_baseline() -> frozenset[tuple[int, int]]:
    owner = os.getpid()
    return frozenset(
        identity.key
        for identity in _process_snapshot().values()
        if identity.parent_id == owner
    )


def _owned_processes(
    root_process_id: int,
    *,
    baseline_children: frozenset[tuple[int, int]],
    include_zombies: bool = False,
) -> tuple[ProcessIdentity, ...]:
    snapshot = _process_snapshot()
    owned_ids = {root_process_id}
    changed = True
    while changed:
        changed = False
        for identity in snapshot.values():
            if (
                identity.process_id not in owned_ids
                and identity.parent_id in owned_ids
            ):
                owned_ids.add(identity.process_id)
                changed = True
    owner = os.getpid()
    for identity in snapshot.values():
        if (
            identity.parent_id == owner
            and identity.key not in baseline_children
        ):
            owned_ids.add(identity.process_id)
    return tuple(
        identity
        for process_id, identity in snapshot.items()
        if process_id in owned_ids
        and (include_zombies or identity.state != "Z")
    )


def _signal_process_identity(
    identity: ProcessIdentity,
    signum: int,
) -> None:
    try:
        current = _proc_identity(identity.process_id)
    except BoundedStageRunnerError:
        return
    if current[3] != identity.starttime:
        return
    try:
        descriptor = os.pidfd_open(identity.process_id, 0)
    except ProcessLookupError:
        return
    except OSError as exc:
        raise BoundedStageRunnerError(
            "stage identity-bound process handle cannot be opened"
        ) from exc
    try:
        refreshed = _proc_identity(identity.process_id)
        if refreshed[3] != identity.starttime:
            return
        signal.pidfd_send_signal(descriptor, signum)
    except ProcessLookupError:
        return
    except OSError as exc:
        raise BoundedStageRunnerError(
            "stage identity-bound process signal failed"
        ) from exc
    finally:
        os.close(descriptor)


def _reap_owned_zombies(
    root_process_id: int,
    *,
    baseline_children: frozenset[tuple[int, int]],
) -> None:
    owner = os.getpid()
    while True:
        reaped = False
        for identity in _owned_processes(
            root_process_id,
            baseline_children=baseline_children,
            include_zombies=True,
        ):
            if (
                identity.process_id == root_process_id
                or identity.parent_id != owner
                or identity.state != "Z"
            ):
                continue
            try:
                waited, _status = os.waitpid(
                    identity.process_id,
                    os.WNOHANG,
                )
            except (ChildProcessError, ProcessLookupError):
                continue
            except OSError as exc:
                raise BoundedStageRunnerError(
                    "stage adopted child could not be reaped"
                ) from exc
            reaped |= waited == identity.process_id
        if not reaped:
            return


def _terminate_process_group(
    process: subprocess.Popen[bytes],
    *,
    baseline_children: frozenset[tuple[int, int]],
) -> None:
    for identity in reversed(
        _owned_processes(
            process.pid,
            baseline_children=baseline_children,
        )
    ):
        _signal_process_identity(identity, signal.SIGTERM)
    deadline = time.monotonic() + PROCESS_GROUP_TERM_SECONDS
    while (
        _owned_processes(
            process.pid,
            baseline_children=baseline_children,
        )
        and time.monotonic() < deadline
    ):
        process.poll()
        _reap_owned_zombies(
            process.pid,
            baseline_children=baseline_children,
        )
        time.sleep(min(0.01, max(0.0, deadline - time.monotonic())))
    for identity in reversed(
        _owned_processes(
            process.pid,
            baseline_children=baseline_children,
        )
    ):
        _signal_process_identity(identity, signal.SIGKILL)
    try:
        process.wait(timeout=PROCESS_GROUP_TERM_SECONDS)
    except subprocess.TimeoutExpired:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=PROCESS_GROUP_TERM_SECONDS)
    absence_deadline = (
        time.monotonic()
        + PROCESS_GROUP_TERM_SECONDS
        + PROCESS_TREE_QUIESCENCE_SECONDS
    )
    stable_since: float | None = None
    while time.monotonic() < absence_deadline:
        _reap_owned_zombies(
            process.pid,
            baseline_children=baseline_children,
        )
        owned = _owned_processes(
            process.pid,
            baseline_children=baseline_children,
            include_zombies=True,
        )
        if owned:
            stable_since = None
            for identity in reversed(owned):
                if identity.state != "Z":
                    _signal_process_identity(identity, signal.SIGKILL)
        else:
            if stable_since is None:
                stable_since = time.monotonic()
            elif (
                time.monotonic() - stable_since
                >= PROCESS_TREE_QUIESCENCE_SECONDS
            ):
                return
        time.sleep(0.01)
    _reap_owned_zombies(
        process.pid,
        baseline_children=baseline_children,
    )
    if _owned_processes(
        process.pid,
        baseline_children=baseline_children,
        include_zombies=True,
    ):
        raise BoundedStageRunnerError(
            "stage subprocess descendants survived forced cleanup"
        )


def _default_runner(
    arguments: Sequence[str],
    *,
    input: bytes | None,
    capture_output: bool,
    check: bool,
    timeout: int | float,
    env: Mapping[str, str],
) -> subprocess.CompletedProcess[bytes]:
    if (
        input is not None
        or capture_output is not True
        or check is not False
        or type(timeout) not in {int, float}
        or not math.isfinite(timeout)
        or timeout <= 0
    ):
        raise BoundedStageRunnerError(
            "stage subprocess execution options are invalid"
        )
    if _ACTIVE_EXECUTION_AUTHORITY is None:
        with _execution_authority():
            return _default_runner(
                arguments,
                input=input,
                capture_output=capture_output,
                check=check,
                timeout=timeout,
                env=env,
            )
    _ACTIVE_EXECUTION_AUTHORITY.check()
    _enable_child_subreaper()
    baseline_children = _direct_child_baseline()
    process: subprocess.Popen[bytes] | None = None
    selector = selectors.DefaultSelector()
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    deadline = time.monotonic() + timeout
    group_cleaned = False
    try:
        process = subprocess.Popen(  # noqa: S603
            list(arguments),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=dict(env),
            close_fds=True,
            shell=False,
            start_new_session=True,
        )
        if process.stdout is None or process.stderr is None:
            raise BoundedStageRunnerError(
                "stage subprocess output pipes are unavailable"
            )
        for label, stream in (
            ("stdout", process.stdout),
            ("stderr", process.stderr),
        ):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, label)
        while selector.get_map():
            _ACTIVE_EXECUTION_AUTHORITY.check()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise BoundedStageRunnerError(
                    "stage subprocess timed out"
                )
            events = selector.select(min(0.1, remaining))
            if not events:
                if process.poll() is not None and not group_cleaned:
                    _terminate_process_group(
                        process,
                        baseline_children=baseline_children,
                    )
                    group_cleaned = True
                continue
            for key, _mask in events:
                try:
                    chunk = os.read(key.fileobj.fileno(), 64 * 1024)
                except BlockingIOError:
                    continue
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                buffer = buffers[key.data]
                if len(buffer) + len(chunk) > MAX_COMMAND_OUTPUT_BYTES:
                    raise BoundedStageRunnerError(
                        f"stage subprocess {key.data} is oversized"
                    )
                buffer.extend(chunk)
            if process.poll() is not None and not group_cleaned:
                _terminate_process_group(
                    process,
                    baseline_children=baseline_children,
                )
                group_cleaned = True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise BoundedStageRunnerError("stage subprocess timed out")
        returncode = process.wait(timeout=remaining)
        return subprocess.CompletedProcess(
            args=list(arguments),
            returncode=returncode,
            stdout=bytes(buffers["stdout"]),
            stderr=bytes(buffers["stderr"]),
        )
    except (FinlandStageCancellation, BoundedStageRunnerError):
        raise
    except (OSError, subprocess.SubprocessError) as exc:
        raise BoundedStageRunnerError(
            "stage subprocess execution failed"
        ) from exc
    finally:
        original_error = sys.exception()
        selector.close()
        cleanup_error: BaseException | None = None
        if process is not None:
            try:
                if not group_cleaned:
                    _terminate_process_group(
                        process,
                        baseline_children=baseline_children,
                    )
            except BaseException as exc:
                cleanup_error = exc
            finally:
                if process.stdout is not None:
                    process.stdout.close()
                if process.stderr is not None:
                    process.stderr.close()
        if cleanup_error is not None:
            if original_error is not None:
                raise original_error from cleanup_error
            raise cleanup_error


def _safe_absolute_command_path(value: str, *, label: str) -> None:
    path = Path(value)
    if (
        not path.is_absolute()
        or ".." in path.parts
        or "\x00" in value
        or "\n" in value
        or "\r" in value
    ):
        raise FinlandStageError(f"{label} path is invalid")


def _validate_stage_command(
    arguments: list[str],
    env: Mapping[str, str],
) -> None:
    if (
        not arguments
        or any(
            not isinstance(token, str)
            or not token
            or "\x00" in token
            or "\n" in token
            or "\r" in token
            for token in arguments
        )
        or arguments[0] not in {GIT, DOCKER}
    ):
        raise FinlandStageError(
            "stage command is outside the executable allowlist"
        )
    if arguments[0] == DOCKER:
        if dict(env) != SAFE_ENV:
            raise FinlandStageError("Docker command environment is unsafe")
        tail = arguments[1:]
        if tail == ["image", "ls", "--all", "--no-trunc", "--quiet"]:
            return
        if (
            len(tail) == 3
            and tail[:2] == ["image", "inspect"]
            and IMAGE_ID_RE.fullmatch(tail[2]) is not None
        ):
            return
        if (
            len(tail) == 4
            and tail[:3] == ["image", "load", "--input"]
        ):
            _safe_absolute_command_path(
                tail[3],
                label="Docker archive",
            )
            return
        raise FinlandStageError(
            "Docker command is outside stage/load boundary"
        )
    if dict(env) != SAFE_GIT_ENV:
        raise FinlandStageError("Git command environment is unsafe")
    tail = arguments[1:]
    if len(tail) == 3 and tail[:2] == ["bundle", "list-heads"]:
        _safe_absolute_command_path(tail[2], label="Git bundle")
        return
    if (
        len(tail) == 7
        and tail[:5]
        == [
            "-c",
            "core.hooksPath=/dev/null",
            "clone",
            "--no-checkout",
            "--no-hardlinks",
        ]
    ):
        _safe_absolute_command_path(tail[5], label="Git clone bundle")
        _safe_absolute_command_path(tail[6], label="Git clone destination")
        return
    if len(tail) < 3 or tail[0] != "-C":
        raise FinlandStageError("Git command argv is outside the allowlist")
    _safe_absolute_command_path(tail[1], label="Git working tree")
    command = tail[2:]
    if command in (
        ["rev-parse", "--show-toplevel"],
        ["rev-parse", "HEAD"],
        ["rev-parse", "HEAD^{tree}"],
        ["rev-parse", "--abbrev-ref", "HEAD"],
        ["status", "--porcelain=v1", "--untracked-files=all"],
        ["remote"],
        ["remote", "remove", "origin"],
        ["config", "--local", "core.hooksPath", "/dev/null"],
    ):
        return
    if len(command) == 3 and command[:2] == ["bundle", "verify"]:
        _safe_absolute_command_path(
            command[2],
            label="Git verification bundle",
        )
        return
    if (
        len(command) == 5
        and command[:4]
        == [
            "-c",
            "core.hooksPath=/dev/null",
            "checkout",
            "--detach",
        ]
        and SHA40_RE.fullmatch(command[4]) is not None
    ):
        return
    raise FinlandStageError("Git command argv is outside the allowlist")


def _run(
    arguments: list[str],
    *,
    timeout: int,
    env: Mapping[str, str],
    runner: Runner | None,
) -> bytes:
    _validate_stage_command(arguments, env)
    active_runner = _default_runner if runner is None else runner
    try:
        completed = active_runner(
            arguments,
            input=None,
            capture_output=True,
            check=False,
            timeout=timeout,
            env=dict(env),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise FinlandStageError(
            f"required command is unavailable: {Path(arguments[0]).name}"
        ) from exc
    if (
        len(completed.stdout) > MAX_COMMAND_OUTPUT_BYTES
        or len(completed.stderr) > MAX_COMMAND_OUTPUT_BYTES
    ):
        raise FinlandStageError("required command output exceeded its bound")
    if completed.returncode != 0:
        raise FinlandStageError(
            f"required command failed closed: {Path(arguments[0]).name}"
        )
    return completed.stdout


def _run_text(
    arguments: list[str],
    *,
    timeout: int,
    env: Mapping[str, str],
    runner: Runner,
) -> str:
    try:
        return _run(
            arguments,
            timeout=timeout,
            env=env,
            runner=runner,
        ).decode("utf-8").strip()
    except UnicodeDecodeError as exc:
        raise FinlandStageError("required command returned non-UTF-8 output") from exc


def _safe_archive_path(raw: Any, *, label: str) -> str:
    if not isinstance(raw, str) or not raw or "\\" in raw or "\x00" in raw:
        raise FinlandStageError(f"{label} is invalid")
    path = PurePosixPath(raw)
    if (
        path.is_absolute()
        or raw != path.as_posix()
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise FinlandStageError(f"{label} is outside the archive")
    return raw


def _archive_members(archive: tarfile.TarFile) -> dict[str, tarfile.TarInfo]:
    regular: dict[str, tarfile.TarInfo] = {}
    names: set[str] = set()
    expanded = 0
    for count, member in enumerate(archive, 1):
        if count > MAX_ARCHIVE_MEMBERS:
            raise FinlandStageError("Docker archive has too many members")
        name = _safe_archive_path(
            member.name.rstrip("/"),
            label="Docker archive member",
        )
        if name in names:
            raise FinlandStageError("Docker archive contains a duplicate member")
        names.add(name)
        if member.isdir():
            if member.size != 0:
                raise FinlandStageError("Docker archive directory contains data")
            continue
        if not member.isreg():
            raise FinlandStageError(
                "Docker archive contains a link, sparse, or special member"
            )
        if member.size < 0 or member.size > MAX_ARTIFACT_BYTES:
            raise FinlandStageError("Docker archive member size is invalid")
        expanded += member.size
        if expanded > MAX_ARTIFACT_BYTES:
            raise FinlandStageError("Docker archive expanded size is oversized")
        regular[name] = member
    if not names:
        raise FinlandStageError("Docker archive is empty")
    return regular


def _read_archive_member(
    archive: tarfile.TarFile,
    member: tarfile.TarInfo,
    *,
    maximum: int,
    label: str,
) -> bytes:
    if not 1 <= member.size <= maximum:
        raise FinlandStageError(f"{label} is empty or oversized")
    source = archive.extractfile(member)
    if source is None:
        raise FinlandStageError(f"{label} is unreadable")
    try:
        payload = source.read(maximum + 1)
    finally:
        source.close()
    if len(payload) != member.size:
        raise FinlandStageError(f"{label} size differs")
    return payload


def verify_image_archive(
    path: Path,
    *,
    image_role: str,
    release_sha: str,
    expected: Mapping[str, Any],
    required_uid: int = 0,
) -> dict[str, Any]:
    expected_digest = _nonzero_sha256(
        expected["archive_sha256"],
        label=f"{image_role} archive SHA-256",
    )
    expected_bytes = _bounded_size(
        expected["archive_bytes"],
        label=f"{image_role} archive bytes",
        maximum=MAX_ARTIFACT_BYTES,
    )
    observed = hash_secure_file(
        path,
        required_uid=required_uid,
        expected_mode=0o600,
        maximum=MAX_ARTIFACT_BYTES,
    )
    if observed != (expected_digest, expected_bytes):
        raise FinlandStageError(f"{image_role} archive identity differs")
    try:
        with _held_file(
            path,
            required_uid=required_uid,
            expected_mode=0o600,
            maximum=MAX_ARTIFACT_BYTES,
        ) as (stream, _before):
            with tarfile.open(fileobj=stream, mode="r:") as archive:
                regular = _archive_members(archive)
                manifest_member = regular.get("manifest.json")
                if manifest_member is None:
                    raise FinlandStageError("Docker archive manifest is missing")
                manifest_raw = _read_archive_member(
                    archive,
                    manifest_member,
                    maximum=MAX_ARCHIVE_MANIFEST_BYTES,
                    label="Docker archive manifest",
                )
                manifest = json.loads(
                    manifest_raw.decode("utf-8"),
                    object_pairs_hook=_strict_object,
                )
                if not isinstance(manifest, list) or len(manifest) != 1:
                    raise FinlandStageError(
                        "Docker archive must contain exactly one image"
                    )
                entry = manifest[0]
                if (
                    not isinstance(entry, dict)
                    or set(entry) - {"Config", "RepoTags", "Layers", "LayerSources"}
                    or not {"Config", "Layers"} <= set(entry)
                    or not isinstance(entry["Config"], str)
                    or not isinstance(entry["Layers"], list)
                    or not entry["Layers"]
                    or any(not isinstance(layer, str) for layer in entry["Layers"])
                    or len(entry["Layers"]) != len(set(entry["Layers"]))
                    or (
                        "LayerSources" in entry
                        and not isinstance(entry["LayerSources"], dict)
                    )
                ):
                    raise FinlandStageError(
                        "Docker archive manifest entry is invalid"
                    )
                if entry.get("RepoTags") not in (None, []):
                    raise FinlandStageError("Docker archive must be tagless")
                if "repositories" in regular:
                    raise FinlandStageError(
                        "Docker archive contains a legacy tag repository"
                    )
                config_name = _safe_archive_path(
                    entry["Config"], label="Docker archive config path"
                )
                config_member = regular.get(config_name)
                if config_member is None:
                    raise FinlandStageError("Docker archive config is missing")
                config_raw = _read_archive_member(
                    archive,
                    config_member,
                    maximum=MAX_ARCHIVE_CONFIG_BYTES,
                    label="Docker archive config",
                )
                layer_names = [
                    _safe_archive_path(
                        layer,
                        label="Docker archive layer path",
                    )
                    for layer in entry["Layers"]
                ]
                if config_name in layer_names or "manifest.json" in layer_names:
                    raise FinlandStageError("Docker archive member roles overlap")
                if any(layer not in regular for layer in layer_names):
                    raise FinlandStageError("Docker archive layer is missing")
                config = json.loads(
                    config_raw.decode("utf-8"),
                    object_pairs_hook=_strict_object,
                )
    except FinlandStageError:
        raise
    except (
        OSError,
        UnicodeError,
        ValueError,
        json.JSONDecodeError,
        tarfile.TarError,
    ) as exc:
        raise FinlandStageError("Docker archive validation failed") from exc
    if not isinstance(config, dict):
        raise FinlandStageError("Docker archive config is invalid")
    config_digest = "sha256:" + hashlib.sha256(config_raw).hexdigest()
    if config_name != f"{config_digest.removeprefix('sha256:')}.json":
        raise FinlandStageError("Docker archive config path differs from its digest")
    descriptor, content_identity = image_content_descriptor_from_archive(config)
    labels_parent = config.get("config")
    labels = (
        labels_parent.get("Labels") if isinstance(labels_parent, dict) else None
    )
    if image_role in RELEASE_BOUND_IMAGE_ROLES and (
        not isinstance(labels, dict)
        or labels.get("org.opencontainers.image.revision") != release_sha
    ):
        raise FinlandStageError(
            f"{image_role} archive lacks the exact release label"
        )
    if image_role == "postgres" and (
        not isinstance(labels, dict)
        or labels.get(POSTGRES_RUNTIME_UID_LABEL) != str(POSTGRES_RUNTIME_UID)
        or labels.get(POSTGRES_RUNTIME_GID_LABEL) != str(POSTGRES_RUNTIME_GID)
    ):
        raise FinlandStageError(
            "postgres archive lacks exact runtime UID/GID labels"
        )
    if (
        config_digest != expected["config_digest"]
        or descriptor != expected["content_descriptor"]
        or content_identity != expected["content_identity"]
    ):
        raise FinlandStageError(
            f"{image_role} archive semantic descriptor differs"
        )
    return {
        "archive_sha256": observed[0],
        "archive_bytes": observed[1],
        "config_digest": config_digest,
        "content_descriptor": descriptor,
        "content_identity": content_identity,
    }


def _verify_release_bundle(
    bundle: Path,
    *,
    release_sha: str,
    expected_sha256: str,
    expected_bytes: int,
    required_uid: int,
    runner: Runner,
) -> None:
    observed = hash_secure_file(
        bundle,
        required_uid=required_uid,
        expected_mode=0o600,
        maximum=MAX_ARTIFACT_BYTES,
    )
    if observed != (expected_sha256, expected_bytes):
        raise FinlandStageError("release bundle identity differs")
    heads = _run_text(
        [GIT, "bundle", "list-heads", str(bundle)],
        timeout=60,
        env=SAFE_GIT_ENV,
        runner=runner,
    ).splitlines()
    if heads != [f"{release_sha} HEAD"]:
        raise FinlandStageError("Git bundle does not contain only the exact release")


def _release_child_metadata(path: Path, *, label: str) -> os.stat_result | None:
    try:
        return path.stat(follow_symlinks=False)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise FinlandStageError(f"{label} is unavailable") from exc


def _convergence_source_set_feature_paths(
    release_root: Path,
    *,
    required_uid: int,
) -> tuple[Path, Path] | None:
    """Return the current source-set pair, or permit a truly legacy release.

    The controller source-set producer requires its fixed shell launcher. A
    release predating that feature contains neither file, which remains valid
    for historical staging. A partial pair is not a legacy release and must
    fail before Git verification can present it as usable.
    """

    scripts_directory = release_root / "scripts"
    scripts_metadata = _release_child_metadata(
        scripts_directory,
        label="materialized release scripts directory",
    )
    if scripts_metadata is None:
        return None
    _assert_directory(
        scripts_directory,
        required_uid=required_uid,
        private=False,
    )

    producer = release_root / CONVERGENCE_SOURCE_SET_PRODUCER_RELATIVE
    launcher = release_root / CONVERGENCE_SOURCE_SET_LAUNCHER_RELATIVE
    producer_metadata = _release_child_metadata(
        producer,
        label="convergence source-set producer",
    )
    launcher_metadata = _release_child_metadata(
        launcher,
        label="convergence source-set launcher",
    )
    if producer_metadata is None and launcher_metadata is None:
        return None
    if producer_metadata is None or launcher_metadata is None:
        raise FinlandStageError(
            "materialized release has an incomplete convergence source-set feature"
        )
    if not stat.S_ISREG(producer_metadata.st_mode):
        raise FinlandStageError("convergence source-set producer is unsafe")
    if not stat.S_ISREG(launcher_metadata.st_mode):
        raise FinlandStageError("convergence source-set launcher is unsafe")
    return producer, launcher


def _install_convergence_source_set_launcher(
    release_root: Path,
    *,
    required_uid: int,
) -> bool:
    """Set mode only after validating the checkout's root-owned launcher."""

    paths = _convergence_source_set_feature_paths(
        release_root,
        required_uid=required_uid,
    )
    if paths is None:
        return False
    _producer, launcher = paths
    descriptor = -1
    try:
        descriptor = os.open(
            launcher,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NONBLOCK", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != required_uid
            or before.st_gid != required_uid
            or before.st_nlink != 1
            or not 1 <= before.st_size <= MAX_CONVERGENCE_SOURCE_SET_LAUNCHER_BYTES
        ):
            raise FinlandStageError("convergence source-set launcher is unsafe")
        os.fchmod(descriptor, 0o700)
        os.fsync(descriptor)
        after = os.fstat(descriptor)
        if (
            not stat.S_ISREG(after.st_mode)
            or after.st_uid != required_uid
            or after.st_gid != required_uid
            or stat.S_IMODE(after.st_mode) != 0o700
            or after.st_nlink != 1
            or not 1 <= after.st_size <= MAX_CONVERGENCE_SOURCE_SET_LAUNCHER_BYTES
        ):
            raise FinlandStageError("convergence source-set launcher is unsafe")
    except FinlandStageError:
        raise
    except OSError as exc:
        raise FinlandStageError(
            "convergence source-set launcher mode could not be fixed"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    _fsync_directory(launcher.parent)
    return True


def _verify_convergence_source_set_launcher(
    release_root: Path,
    *,
    required_uid: int,
) -> bool:
    """Verify the installed launcher before treating its release as exact."""

    paths = _convergence_source_set_feature_paths(
        release_root,
        required_uid=required_uid,
    )
    if paths is None:
        return False
    _producer, launcher = paths
    with _held_file(
        launcher,
        required_uid=required_uid,
        expected_mode=0o700,
        maximum=MAX_CONVERGENCE_SOURCE_SET_LAUNCHER_BYTES,
        nonblocking=True,
    ) as (stream, metadata):
        if metadata.st_gid != required_uid:
            raise FinlandStageError("convergence source-set launcher is unsafe")
        while stream.read(1024 * 1024):
            pass
    return True


def _verify_materialized_release(
    release_root: Path,
    *,
    bundle: Path,
    release_sha: str,
    release_tree_sha: str,
    required_uid: int,
    runner: Runner,
) -> None:
    _assert_directory(release_root, required_uid=required_uid, private=True)
    try:
        git_metadata = (release_root / ".git").stat(follow_symlinks=False)
    except OSError as exc:
        raise FinlandStageError("materialized release Git directory is unavailable") from exc
    if not stat.S_ISDIR(git_metadata.st_mode):
        raise FinlandStageError("materialized release Git layout is unsafe")
    observed = {
        "root": _run_text(
            [GIT, "-C", str(release_root), "rev-parse", "--show-toplevel"],
            timeout=30,
            env=SAFE_GIT_ENV,
            runner=runner,
        ),
        "head": _run_text(
            [GIT, "-C", str(release_root), "rev-parse", "HEAD"],
            timeout=30,
            env=SAFE_GIT_ENV,
            runner=runner,
        ),
        "tree": _run_text(
            [GIT, "-C", str(release_root), "rev-parse", "HEAD^{tree}"],
            timeout=30,
            env=SAFE_GIT_ENV,
            runner=runner,
        ),
        "branch": _run_text(
            [GIT, "-C", str(release_root), "rev-parse", "--abbrev-ref", "HEAD"],
            timeout=30,
            env=SAFE_GIT_ENV,
            runner=runner,
        ),
        "status": _run_text(
            [
                GIT,
                "-C",
                str(release_root),
                "status",
                "--porcelain=v1",
                "--untracked-files=all",
            ],
            timeout=30,
            env=SAFE_GIT_ENV,
            runner=runner,
        ),
        "remotes": _run_text(
            [GIT, "-C", str(release_root), "remote"],
            timeout=30,
            env=SAFE_GIT_ENV,
            runner=runner,
        ),
    }
    if (
        observed["root"] != str(release_root)
        or observed["head"] != release_sha
        or observed["tree"] != release_tree_sha
        or observed["branch"] != "HEAD"
        or observed["status"]
        or observed["remotes"]
    ):
        raise FinlandStageError(
            "release is not exact, detached, clean, and remote-free"
        )
    _run(
        [GIT, "-C", str(release_root), "bundle", "verify", str(bundle)],
        timeout=120,
        env=SAFE_GIT_ENV,
        runner=runner,
    )


def _materialize_release(
    bundle: Path,
    release_root: Path,
    *,
    release_sha: str,
    release_tree_sha: str,
    required_uid: int,
    runner: Runner,
) -> None:
    if not release_root.exists() and not release_root.is_symlink():
        with _private_umask():
            _run(
                [
                    GIT,
                    "-c",
                    "core.hooksPath=/dev/null",
                    "clone",
                    "--no-checkout",
                    "--no-hardlinks",
                    str(bundle),
                    str(release_root),
                ],
                timeout=300,
                env=SAFE_GIT_ENV,
                runner=runner,
            )
        try:
            release_root.chmod(0o700)
        except OSError as exc:
            raise FinlandStageError("release root permissions could not be fixed") from exc
        _fsync_directory(release_root.parent)
    _assert_directory(release_root, required_uid=required_uid, private=True)
    remotes = _run_text(
        [GIT, "-C", str(release_root), "remote"],
        timeout=30,
        env=SAFE_GIT_ENV,
        runner=runner,
    ).splitlines()
    if any(remote != "origin" for remote in remotes):
        raise FinlandStageError("materialized release has an unexpected remote")
    if remotes == ["origin"]:
        _run(
            [GIT, "-C", str(release_root), "remote", "remove", "origin"],
            timeout=30,
            env=SAFE_GIT_ENV,
            runner=runner,
        )
    _run(
        [
            GIT,
            "-C",
            str(release_root),
            "-c",
            "core.hooksPath=/dev/null",
            "checkout",
            "--detach",
            release_sha,
        ],
        timeout=300,
        env=SAFE_GIT_ENV,
        runner=runner,
    )
    _install_convergence_source_set_launcher(
        release_root,
        required_uid=required_uid,
    )
    _verify_convergence_source_set_launcher(
        release_root,
        required_uid=required_uid,
    )
    _run(
        [
            GIT,
            "-C",
            str(release_root),
            "config",
            "--local",
            "core.hooksPath",
            "/dev/null",
        ],
        timeout=30,
        env=SAFE_GIT_ENV,
        runner=runner,
    )
    _verify_materialized_release(
        release_root,
        bundle=bundle,
        release_sha=release_sha,
        release_tree_sha=release_tree_sha,
        required_uid=required_uid,
        runner=runner,
    )


def _docker_command_timeout(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise FinlandStageError(
            "Docker image inventory exceeded its deadline"
        )
    return min(60.0, remaining)


def _docker_image_ids(
    *,
    runner: Runner | None,
    deadline: float,
) -> list[str]:
    raw = _run_text(
        [DOCKER, "image", "ls", "--all", "--no-trunc", "--quiet"],
        timeout=_docker_command_timeout(deadline),
        env=SAFE_ENV,
        runner=runner,
    )
    values = sorted(set(raw.splitlines())) if raw else []
    if (
        len(values) > MAX_DOCKER_IMAGE_IDS
        or any(IMAGE_ID_RE.fullmatch(value) is None for value in values)
    ):
        raise FinlandStageError("Docker image inventory returned an invalid ID")
    return values


def _inspect_image_raw(
    image_id: str,
    *,
    runner: Runner | None,
    deadline: float | None = None,
) -> dict[str, Any]:
    if IMAGE_ID_RE.fullmatch(image_id) is None:
        raise FinlandStageError("runtime image ID is invalid")
    raw = _run_text(
        [DOCKER, "image", "inspect", image_id],
        timeout=(
            60
            if deadline is None
            else _docker_command_timeout(deadline)
        ),
        env=SAFE_ENV,
        runner=runner,
    )
    try:
        document = json.loads(raw, object_pairs_hook=_strict_object)
    except (ValueError, json.JSONDecodeError) as exc:
        raise FinlandStageError("Docker image inspect returned invalid JSON") from exc
    if (
        not isinstance(document, list)
        or len(document) != 1
        or not isinstance(document[0], dict)
        or document[0].get("Id") != image_id
    ):
        raise FinlandStageError("Docker image inspect identity is ambiguous")
    return document[0]


def _runtime_semantic_matches(
    expected: Mapping[str, Any],
    *,
    runner: Runner | None,
    deadline: float | None = None,
) -> list[str]:
    scan_deadline = (
        time.monotonic() + DOCKER_INVENTORY_SCAN_SECONDS
        if deadline is None
        else deadline
    )
    matches: list[str] = []
    for image_id in _docker_image_ids(
        runner=runner,
        deadline=scan_deadline,
    ):
        image = _inspect_image_raw(
            image_id,
            runner=runner,
            deadline=scan_deadline,
        )
        try:
            descriptor, identity = image_content_descriptor_from_inspect(image)
        except FinlandStageError:
            continue
        if (
            descriptor == expected["content_descriptor"]
            and identity == expected["content_identity"]
        ):
            matches.append(image_id)
    return matches


def _verify_runtime_image(
    image_id: str,
    *,
    image_role: str,
    expected: Mapping[str, Any],
    release_sha: str,
    runner: Runner | None,
) -> dict[str, Any]:
    image = _inspect_image_raw(image_id, runner=runner)
    descriptor, identity = image_content_descriptor_from_inspect(image)
    config = image.get("Config")
    labels = config.get("Labels") if isinstance(config, dict) else None
    if (
        descriptor != expected["content_descriptor"]
        or identity != expected["content_identity"]
    ):
        raise FinlandStageError(
            f"loaded {image_role} image semantic descriptor differs"
        )
    if image_role in RELEASE_BOUND_IMAGE_ROLES and (
        not isinstance(labels, dict)
        or labels.get("org.opencontainers.image.revision") != release_sha
    ):
        raise FinlandStageError(f"loaded {image_role} release label differs")
    if image_role == "postgres" and (
        not isinstance(labels, dict)
        or labels.get(POSTGRES_RUNTIME_UID_LABEL) != str(POSTGRES_RUNTIME_UID)
        or labels.get(POSTGRES_RUNTIME_GID_LABEL) != str(POSTGRES_RUNTIME_GID)
    ):
        raise FinlandStageError(
            "loaded postgres runtime UID/GID labels differ"
        )
    return {
        "role": image_role,
        "runtime_image_id": image_id,
        "config_digest": expected["config_digest"],
        "content_descriptor": descriptor,
        "content_identity": identity,
        "source": "docker-load",
    }


def _state_sha256(journal: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        canonical_json(
            {key: value for key, value in journal.items() if key != "state_sha256"}
        )
    ).hexdigest()


def _append_event(
    journal: dict[str, Any],
    *,
    kind: str,
    phase: str | None,
) -> None:
    event = {
        "sequence": len(journal["events"]) + 1,
        "kind": kind,
        "phase": phase,
        "previous_event_sha256": journal["event_tail_sha256"],
    }
    event["event_sha256"] = hashlib.sha256(canonical_json(event)).hexdigest()
    journal["events"].append(event)
    journal["event_tail_sha256"] = event["event_sha256"]


def _validate_journal(journal: Any) -> dict[str, Any]:
    if not isinstance(journal, dict) or set(journal) != JOURNAL_FIELDS:
        raise FinlandStageError("stage journal fields are not exact")
    if (
        journal["schema"] != JOURNAL_SCHEMA
        or journal["status"] not in {"active", "complete"}
        or journal["state_sha256"] != _state_sha256(journal)
    ):
        raise FinlandStageError("stage journal state hash is invalid")
    _canonical_uuid4(journal["operation_id"], label="journal operation_id")
    if (
        SHA40_RE.fullmatch(str(journal["release_sha"])) is None
        or SHA40_RE.fullmatch(str(journal["release_tree_sha"])) is None
        or journal["role"] not in STAGE_ROLES
    ):
        raise FinlandStageError("stage journal identity is invalid")
    _nonzero_sha256(
        journal["operation_manifest_sha256"],
        label="journal operation manifest",
    )
    completed = journal["completed_phases"]
    current = journal["current_phase"]
    if (
        not isinstance(completed, list)
        or completed != list(PHASES[: len(completed)])
        or current
        not in (
            {None}
            if len(completed) == len(PHASES)
            else {None, PHASES[len(completed)]}
        )
        or (journal["status"] == "complete") != (completed == list(PHASES))
    ):
        raise FinlandStageError("stage journal phase prefix is invalid")
    for field in (
        "archive_evidence",
        "load_intents",
        "runtime_image_ids",
        "images",
    ):
        if not isinstance(journal[field], dict):
            raise FinlandStageError(f"stage journal {field} is invalid")
    if not set(journal["archive_evidence"]) <= set(IMAGE_ROLES):
        raise FinlandStageError("stage journal archive evidence is invalid")
    if not set(journal["load_intents"]) <= set(IMAGE_ROLES):
        raise FinlandStageError("stage journal load intent is invalid")
    if not set(journal["runtime_image_ids"]) <= set(IMAGE_ROLES):
        raise FinlandStageError("stage journal runtime image inventory is invalid")
    if set(journal["images"]) != set(journal["runtime_image_ids"]):
        raise FinlandStageError("stage journal image evidence differs")
    if any(
        IMAGE_ID_RE.fullmatch(value) is None
        for value in journal["runtime_image_ids"].values()
    ):
        raise FinlandStageError("stage journal runtime image ID is invalid")
    events = journal["events"]
    if not isinstance(events, list) or not events:
        raise FinlandStageError("stage journal event chain is empty")
    previous = ZERO_SHA256
    for index, event in enumerate(events, 1):
        if (
            not isinstance(event, dict)
            or set(event)
            != {
                "sequence",
                "kind",
                "phase",
                "previous_event_sha256",
                "event_sha256",
            }
        ):
            raise FinlandStageError("stage journal event fields are invalid")
        expected = hashlib.sha256(
            canonical_json(
                {key: value for key, value in event.items() if key != "event_sha256"}
            )
        ).hexdigest()
        if (
            event["sequence"] != index
            or event["previous_event_sha256"] != previous
            or event["event_sha256"] != expected
        ):
            raise FinlandStageError("stage journal event chain is invalid")
        previous = event["event_sha256"]
    if journal["event_tail_sha256"] != previous:
        raise FinlandStageError("stage journal event tail differs")
    return journal


def _journal_temporary(path: Path) -> Path:
    return path.with_name(
        f".{path.name}.{os.getpid()}.{secrets.token_hex(8)}.tmp"
    )


def _reconcile_journal_temporaries(
    path: Path,
    *,
    required_uid: int,
) -> None:
    pattern = re.compile(
        rf"^\.{re.escape(path.name)}\.[1-9][0-9]*\.[0-9a-f]{{16}}\.tmp$"
    )
    try:
        candidates = [
            path.parent / entry.name
            for entry in os.scandir(path.parent)
            if pattern.fullmatch(entry.name)
        ]
    except OSError as exc:
        raise FinlandStageError(
            "stage journal temporary inventory is unavailable"
        ) from exc
    if len(candidates) > 64:
        raise FinlandStageError("stage journal temporary inventory is excessive")
    changed = False
    for candidate in candidates:
        try:
            metadata = candidate.stat(follow_symlinks=False)
        except OSError as exc:
            raise FinlandStageError("stage journal temporary is unsafe") from exc
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != required_uid
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_nlink not in {1, 2}
            or not 0 <= metadata.st_size <= MAX_JOURNAL_BYTES
        ):
            raise FinlandStageError("stage journal temporary is unsafe")
        if metadata.st_nlink == 2:
            try:
                published = path.stat(follow_symlinks=False)
            except OSError as exc:
                raise FinlandStageError(
                    "stage journal temporary link identity is ambiguous"
                ) from exc
            if (
                not stat.S_ISREG(published.st_mode)
                or metadata.st_dev != published.st_dev
                or metadata.st_ino != published.st_ino
            ):
                raise FinlandStageError(
                    "stage journal temporary link identity is ambiguous"
                )
        candidate.unlink()
        changed = True
    if changed:
        _fsync_directory(path.parent)


def _write_journal(
    path: Path,
    journal: dict[str, Any],
    *,
    create: bool,
    required_uid: int,
) -> None:
    _reconcile_journal_temporaries(path, required_uid=required_uid)
    journal["state_sha256"] = _state_sha256(journal)
    _validate_journal(journal)
    payload = canonical_json(journal)
    temporary = _journal_temporary(path)
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
                raise OSError("short stage journal write")
            view = view[written:]
        os.fchmod(descriptor, 0o600)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        if create:
            try:
                os.link(temporary, path, follow_symlinks=False)
            except FileExistsError as exc:
                raise FinlandStageError("stage journal already exists") from exc
        else:
            if not path.exists() or path.is_symlink():
                raise FinlandStageError("stage journal disappeared")
            os.replace(temporary, path)
        _fsync_directory(path.parent)
    except FinlandStageError:
        raise
    except OSError as exc:
        raise FinlandStageError("stage journal could not be persisted") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary.exists() and not temporary.is_symlink():
            metadata = temporary.stat(follow_symlinks=False)
            if (
                stat.S_ISREG(metadata.st_mode)
                and metadata.st_uid == required_uid
                and stat.S_IMODE(metadata.st_mode) == 0o600
                and metadata.st_nlink in {1, 2}
            ):
                temporary.unlink()


def _load_or_create_journal(
    path: Path,
    *,
    manifest: Mapping[str, Any],
    manifest_sha256: str,
    required_uid: int,
) -> dict[str, Any]:
    _reconcile_journal_temporaries(path, required_uid=required_uid)
    if not path.exists() and not path.is_symlink():
        journal: dict[str, Any] = {
            "schema": JOURNAL_SCHEMA,
            "operation_id": manifest["operation_id"],
            "release_sha": manifest["release_sha"],
            "release_tree_sha": manifest["release_tree_sha"],
            "role": manifest["role"],
            "operation_manifest_sha256": manifest_sha256,
            "status": "active",
            "completed_phases": [],
            "current_phase": None,
            "archive_evidence": {},
            "load_intents": {},
            "runtime_image_ids": {},
            "images": {},
            "events": [],
            "event_tail_sha256": ZERO_SHA256,
            "state_sha256": "",
        }
        _append_event(journal, kind="journal-created", phase=None)
        _write_journal(
            path,
            journal,
            create=True,
            required_uid=required_uid,
        )
        return journal
    raw = _read_secure_file(
        path,
        required_uid=required_uid,
        expected_mode=0o600,
        maximum=MAX_JOURNAL_BYTES,
    )
    journal = _validate_journal(_strict_json(raw, label="stage journal"))
    expected = {
        "operation_id": manifest["operation_id"],
        "release_sha": manifest["release_sha"],
        "release_tree_sha": manifest["release_tree_sha"],
        "role": manifest["role"],
        "operation_manifest_sha256": manifest_sha256,
    }
    if any(journal[key] != value for key, value in expected.items()):
        raise FinlandStageError("existing stage journal has different bindings")
    return journal


@contextmanager
def _operation_lock(path: Path, *, required_uid: int) -> Iterator[None]:
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
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_nlink != 1
        ):
            raise FinlandStageError("stage lock is unsafe")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise FinlandStageError("another stage operation holds the lock") from exc
        yield
    except FinlandStageError:
        raise
    except OSError as exc:
        raise FinlandStageError("stage lock could not be acquired") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _start_phase(
    path: Path,
    journal: dict[str, Any],
    phase: str,
    *,
    required_uid: int,
) -> None:
    if journal["current_phase"] == phase:
        return
    if (
        journal["current_phase"] is not None
        or phase != PHASES[len(journal["completed_phases"])]
    ):
        raise FinlandStageError("stage journal phase transition is invalid")
    journal["current_phase"] = phase
    _append_event(journal, kind="phase-started", phase=phase)
    _write_journal(path, journal, create=False, required_uid=required_uid)


def _complete_phase(
    path: Path,
    journal: dict[str, Any],
    phase: str,
    *,
    required_uid: int,
) -> None:
    if journal["current_phase"] != phase:
        raise FinlandStageError("stage journal phase completion is invalid")
    journal["completed_phases"].append(phase)
    journal["current_phase"] = None
    if journal["completed_phases"] == list(PHASES):
        journal["status"] = "complete"
    _append_event(journal, kind="phase-completed", phase=phase)
    _write_journal(path, journal, create=False, required_uid=required_uid)


def _attestation_document(
    manifest: Mapping[str, Any],
    manifest_sha256: str,
    journal: Mapping[str, Any],
) -> dict[str, Any]:
    runtime_ids = {
        role: journal["runtime_image_ids"][role] for role in IMAGE_ROLES
    }
    images = [journal["images"][role] for role in IMAGE_ROLES]
    document = {
        "schema": ATTESTATION_SCHEMA,
        "operation_id": manifest["operation_id"],
        "release_sha": manifest["release_sha"],
        "release_tree_sha": manifest["release_tree_sha"],
        "operation_manifest_sha256": manifest_sha256,
        "role": manifest["role"],
        "image_artifacts": {
            role: manifest["image_artifacts"][role] for role in IMAGE_ROLES
        },
        "runtime_image_ids": runtime_ids,
        "images": images,
        "images_built": False,
        "images_pulled": False,
        "containers_created": False,
        "containers_started": False,
        "services_started": False,
        "networks_created": False,
        "volumes_created": False,
        "current_mutated": False,
        "data_mutated": False,
    }
    if set(document) != ATTESTATION_FIELDS:
        raise FinlandStageError("internal attestation fields are not exact")
    if any(document[field] is not False for field in (
        "images_built",
        "images_pulled",
        "containers_created",
        "containers_started",
        "services_started",
        "networks_created",
        "volumes_created",
        "current_mutated",
        "data_mutated",
    )):
        raise FinlandStageError("forbidden mutation attestation is not false")
    return document


def _validated_load_intent(
    intent: Any,
    *,
    expected: Mapping[str, Any],
) -> list[str]:
    if (
        not isinstance(intent, dict)
        or set(intent)
        != {
            "baseline_runtime_image_ids",
            "archive_sha256",
            "content_identity",
        }
        or intent["archive_sha256"] != expected["archive_sha256"]
        or intent["content_identity"] != expected["content_identity"]
        or not isinstance(intent["baseline_runtime_image_ids"], list)
        or len(intent["baseline_runtime_image_ids"])
        != len(set(intent["baseline_runtime_image_ids"]))
        or any(
            not isinstance(value, str)
            or IMAGE_ID_RE.fullmatch(value) is None
            for value in intent["baseline_runtime_image_ids"]
        )
    ):
        raise FinlandStageError("stage image load intent is invalid")
    return list(intent["baseline_runtime_image_ids"])


def _image_load_reconciliation_document(
    *,
    image_role: str,
    manifest: Mapping[str, Any],
    journal: Mapping[str, Any],
    baseline_runtime_image_ids: list[str],
    runtime_image_id: str,
    image: Mapping[str, Any],
) -> dict[str, Any]:
    document = {
        "schema": IMAGE_LOAD_RECONCILIATION_SCHEMA,
        "operation_id": manifest["operation_id"],
        "release_sha": manifest["release_sha"],
        "release_tree_sha": manifest["release_tree_sha"],
        "role": manifest["role"],
        "operation_manifest_sha256": journal[
            "operation_manifest_sha256"
        ],
        "image_role": image_role,
        "archive_sha256": manifest["image_artifacts"][image_role][
            "archive_sha256"
        ],
        "content_identity": manifest["image_artifacts"][image_role][
            "content_identity"
        ],
        "baseline_runtime_image_ids": sorted(
            baseline_runtime_image_ids
        ),
        "runtime_image_id": runtime_image_id,
        "image": dict(image),
    }
    if (
        set(document) != IMAGE_LOAD_RECONCILIATION_FIELDS
        or set(document["image"]) != IMAGE_ATTESTATION_FIELDS
        or IMAGE_ID_RE.fullmatch(runtime_image_id) is None
        or runtime_image_id in baseline_runtime_image_ids
    ):
        raise FinlandStageError(
            "image load reconciliation evidence is invalid"
        )
    return document


def _record_runtime_image(
    *,
    image_role: str,
    runtime_image_id: str,
    manifest: Mapping[str, Any],
    paths: Mapping[str, Path | str],
    journal: dict[str, Any],
    journal_path: Path,
    baseline_runtime_image_ids: list[str],
    required_uid: int,
    runner: Runner | None,
) -> None:
    if runtime_image_id in baseline_runtime_image_ids:
        raise FinlandStageError(
            f"{image_role} runtime image was present before the load intent"
        )
    evidence = _verify_runtime_image(
        runtime_image_id,
        image_role=image_role,
        expected=manifest["image_artifacts"][image_role],
        release_sha=manifest["release_sha"],
        runner=runner,
    )
    other_runtime_ids = {
        value
        for role_name, value in journal["runtime_image_ids"].items()
        if role_name != image_role
    }
    if runtime_image_id in other_runtime_ids:
        raise FinlandStageError(
            "runtime image IDs must be distinct across roles"
        )
    reconciliation = _image_load_reconciliation_document(
        image_role=image_role,
        manifest=manifest,
        journal=journal,
        baseline_runtime_image_ids=baseline_runtime_image_ids,
        runtime_image_id=runtime_image_id,
        image=evidence,
    )
    _write_create_only(
        image_load_reconciliation_path(paths, image_role),
        canonical_json(reconciliation),
        required_uid=required_uid,
        mode=0o600,
        maximum=MAX_RECONCILIATION_EVIDENCE_BYTES,
    )
    existing_runtime_id = journal["runtime_image_ids"].get(image_role)
    existing_evidence = journal["images"].get(image_role)
    if existing_runtime_id is not None and (
        existing_runtime_id != runtime_image_id
        or existing_evidence != evidence
    ):
        raise FinlandStageError(
            "loaded image differs from stage journal"
        )
    if existing_runtime_id is None:
        journal["runtime_image_ids"][image_role] = runtime_image_id
        journal["images"][image_role] = evidence
        _write_journal(
            journal_path,
            journal,
            create=False,
            required_uid=required_uid,
        )


def _poll_late_runtime_image(
    expected: Mapping[str, Any],
    *,
    runner: Runner | None,
) -> list[str]:
    deadline = time.monotonic() + DOCKER_LOAD_RECONCILE_SECONDS
    while True:
        matches = _runtime_semantic_matches(
            expected,
            runner=runner,
            deadline=deadline,
        )
        if len(matches) > 1:
            raise FinlandStageError(
                "late Docker image reconciliation is ambiguous"
            )
        if matches or time.monotonic() >= deadline:
            return matches
        time.sleep(
            min(
                DOCKER_LOAD_RECONCILE_INTERVAL_SECONDS,
                max(0.0, deadline - time.monotonic()),
            )
        )


def _stage_image(
    image_role: str,
    *,
    manifest: Mapping[str, Any],
    paths: Mapping[str, Path | str],
    journal: dict[str, Any],
    journal_path: Path,
    required_uid: int,
    runner: Runner | None,
    checkpoint: Checkpoint,
) -> None:
    expected = manifest["image_artifacts"][image_role]
    if image_role in journal["runtime_image_ids"]:
        baseline = _validated_load_intent(
            journal["load_intents"].get(image_role),
            expected=expected,
        )
        _record_runtime_image(
            image_role=image_role,
            runtime_image_id=journal["runtime_image_ids"][image_role],
            manifest=manifest,
            paths=paths,
            journal=journal,
            journal_path=journal_path,
            baseline_runtime_image_ids=baseline,
            required_uid=required_uid,
            runner=runner,
        )
        return
    intent = journal["load_intents"].get(image_role)
    if intent is None:
        baseline = _docker_image_ids(
            runner=runner,
            deadline=time.monotonic() + DOCKER_INVENTORY_SCAN_SECONDS,
        )
        matches = _runtime_semantic_matches(expected, runner=runner)
        if matches:
            raise FinlandStageError(
                f"{image_role} semantic image already existed before this operation"
            )
        intent = {
            "baseline_runtime_image_ids": baseline,
            "archive_sha256": expected["archive_sha256"],
            "content_identity": expected["content_identity"],
        }
        journal["load_intents"][image_role] = intent
        _write_journal(
            journal_path,
            journal,
            create=False,
            required_uid=required_uid,
        )
        checkpoint(f"after-intent:{image_role}")
    baseline = _validated_load_intent(intent, expected=expected)

    matches = _runtime_semantic_matches(expected, runner=runner)
    if not matches:
        archive_path = (
            paths["incoming_root"] / ARTIFACT_FILENAMES[f"{image_role}-image-archive"]  # type: ignore[operator]
        )
        try:
            _run(
                [DOCKER, "image", "load", "--input", str(archive_path)],
                timeout=1800,
                env=SAFE_ENV,
                runner=runner,
            )
            checkpoint(f"after-load:{image_role}")
            matches = _poll_late_runtime_image(
                expected,
                runner=runner,
            )
        except BaseException as load_error:
            try:
                matches = _poll_late_runtime_image(
                    expected,
                    runner=runner,
                )
                if len(matches) == 1:
                    _record_runtime_image(
                        image_role=image_role,
                        runtime_image_id=matches[0],
                        manifest=manifest,
                        paths=paths,
                        journal=journal,
                        journal_path=journal_path,
                        baseline_runtime_image_ids=baseline,
                        required_uid=required_uid,
                        runner=runner,
                    )
            except BaseException as reconciliation_error:
                raise load_error from reconciliation_error
            raise load_error
    if len(matches) != 1:
        raise FinlandStageError(
            f"{image_role} loaded semantic image match is not unique"
        )
    _record_runtime_image(
        image_role=image_role,
        runtime_image_id=matches[0],
        manifest=manifest,
        paths=paths,
        journal=journal,
        journal_path=journal_path,
        baseline_runtime_image_ids=baseline,
        required_uid=required_uid,
        runner=runner,
    )


def _finalize_inputs(
    paths: Mapping[str, Path | str],
    *,
    manifest_sha256: str,
    required_uid: int,
) -> tuple[dict[str, Any], bytes]:
    incoming_manifest = paths["manifest"]
    manifest_partial = transfer_partial_path(incoming_manifest)  # type: ignore[arg-type]
    source_path = (
        manifest_partial
        if manifest_partial.exists() or manifest_partial.is_symlink()
        else incoming_manifest
    )
    raw = _read_secure_file(
        source_path,  # type: ignore[arg-type]
        required_uid=required_uid,
        expected_mode=0o600,
        maximum=MAX_MANIFEST_BYTES,
    )
    manifest, observed_sha = load_manifest_bytes(
        raw,
        expected_sha256=manifest_sha256,
    )
    if observed_sha != manifest_sha256:
        raise FinlandStageError("stage manifest SHA-256 differs")
    expected_paths = canonical_paths(
        manifest["operation_id"],
        manifest["release_sha"],
        manifest["role"],
    )
    if any(paths[key] != expected_paths[key] for key in expected_paths):
        raise FinlandStageError("stage request and manifest paths differ")
    _publish_transfer_partial(
        incoming_manifest,  # type: ignore[arg-type]
        expected_sha256=manifest_sha256,
        expected_bytes=len(raw),
        required_uid=required_uid,
        mode=0o600,
    )
    for kind in ARTIFACT_KINDS:
        row = manifest["artifacts"][kind]
        destination = (
            paths["incoming_root"] / row["filename"]  # type: ignore[operator]
        )
        if (
            transfer_partial_path(destination).exists()
            or transfer_partial_path(destination).is_symlink()
        ):
            _publish_transfer_partial(
                destination,
                expected_sha256=row["sha256"],
                expected_bytes=row["bytes"],
                required_uid=required_uid,
                mode=0o600,
            )
        elif hash_secure_file(
            destination,
            required_uid=required_uid,
            expected_mode=0o600,
            maximum=MAX_ARTIFACT_BYTES,
        ) != (row["sha256"], row["bytes"]):
            raise FinlandStageError(f"incoming artifact {kind} differs")
    _write_create_only(
        paths["secret_manifest"],  # type: ignore[arg-type]
        raw,
        required_uid=required_uid,
        mode=0o600,
        maximum=MAX_MANIFEST_BYTES,
    )
    expected_names = {
        AGENT_FILENAME,
        MANIFEST_FILENAME,
        *(
            manifest["artifacts"][kind]["filename"]
            for kind in ARTIFACT_KINDS
        ),
    }
    try:
        observed_names = {entry.name for entry in os.scandir(paths["incoming_root"])}
    except OSError as exc:
        raise FinlandStageError("incoming artifact inventory is unavailable") from exc
    if observed_names != expected_names:
        raise FinlandStageError("incoming artifact inventory is not exact")
    return manifest, raw


def stage_operation(
    request: Mapping[str, Any],
    *,
    required_uid: int = 0,
    runner: Runner | None = None,
    checkpoint: Checkpoint | None = None,
    observed_host_addresses: set[str] | None = None,
    control_fd: int | None = None,
) -> dict[str, Any]:
    if _ACTIVE_EXECUTION_AUTHORITY is None:
        with _execution_authority(control_fd):
            return stage_operation(
                request,
                required_uid=required_uid,
                runner=runner,
                checkpoint=checkpoint,
                observed_host_addresses=observed_host_addresses,
            )
    if control_fd is not None:
        raise FinlandStageError(
            "controller liveness guard is already active"
        )
    _ACTIVE_EXECUTION_AUTHORITY.check()
    if os.geteuid() != required_uid or required_uid != 0:
        raise FinlandStageError("Finland stage agent must run as root")
    request = _decode_request(
        encode_request(request),
        bootstrap=False,
    )
    _verify_role_host(
        str(request["role"]),
        observed_host_addresses=observed_host_addresses,
    )
    callback = checkpoint if checkpoint is not None else (lambda _name: None)
    paths = ensure_operation_directories(
        str(request["operation_id"]),
        str(request["release_sha"]),
        str(request["role"]),
        required_uid=required_uid,
    )
    agent_path = paths["agent"]
    version = _agent_version(
        agent_path,  # type: ignore[arg-type]
        expected_sha256=str(request["agent_sha256"]),
        required_uid=required_uid,
    )
    manifest, manifest_raw = _finalize_inputs(
        paths,
        manifest_sha256=str(request["operation_manifest_sha256"]),
        required_uid=required_uid,
    )
    if (
        request["operation_id"] != manifest["operation_id"]
        or request["release_sha"] != manifest["release_sha"]
        or request["release_tree_sha"] != manifest["release_tree_sha"]
        or request["role"] != manifest["role"]
        or request["agent_sha256"] != manifest["bootstrap_sha256"]
        or request["pull_policy"] != manifest["pull_policy"]
        or version["agent_sha256"] != manifest["bootstrap_sha256"]
        or hashlib.sha256(manifest_raw).hexdigest()
        != request["operation_manifest_sha256"]
    ):
        raise FinlandStageError("stage request differs from its manifest")

    with _operation_lock(
        paths["lock"],  # type: ignore[arg-type]
        required_uid=required_uid,
    ):
        journal_path = paths["journal"]
        journal = _load_or_create_journal(
            journal_path,  # type: ignore[arg-type]
            manifest=manifest,
            manifest_sha256=str(request["operation_manifest_sha256"]),
            required_uid=required_uid,
        )
        release_bundle = (
            paths["incoming_root"] / ARTIFACT_FILENAMES["release-bundle"]  # type: ignore[operator]
        )
        release_artifact = manifest["artifacts"]["release-bundle"]
        _verify_release_bundle(
            release_bundle,
            release_sha=manifest["release_sha"],
            expected_sha256=release_artifact["sha256"],
            expected_bytes=release_artifact["bytes"],
            required_uid=required_uid,
            runner=runner,
        )
        if "release-materialized" in journal["completed_phases"]:
            _assert_directory(
                paths["release_root"],  # type: ignore[arg-type]
                required_uid=required_uid,
                private=True,
            )
            _install_convergence_source_set_launcher(
                paths["release_root"],  # type: ignore[arg-type]
                required_uid=required_uid,
            )
        verified_archives = {}
        for image_role in IMAGE_ROLES:
            archive = (
                paths["incoming_root"]
                / ARTIFACT_FILENAMES[
                    f"{image_role}-image-archive"
                ]
            )
            verified_archives[image_role] = verify_image_archive(
                archive,
                image_role=image_role,
                release_sha=manifest["release_sha"],
                expected=manifest["image_artifacts"][image_role],
                required_uid=required_uid,
            )
        if (
            journal["archive_evidence"]
            and journal["archive_evidence"] != verified_archives
        ):
            raise FinlandStageError(
                "Docker archive evidence differs from the stage journal"
            )

        for phase in PHASES:
            if phase in journal["completed_phases"]:
                continue
            _start_phase(
                journal_path,  # type: ignore[arg-type]
                journal,
                phase,
                required_uid=required_uid,
            )
            if phase == "inputs-verified":
                for kind in ARTIFACT_KINDS:
                    row = manifest["artifacts"][kind]
                    path = paths["incoming_root"] / row["filename"]  # type: ignore[operator]
                    if hash_secure_file(
                        path,
                        required_uid=required_uid,
                        expected_mode=0o600,
                        maximum=MAX_ARTIFACT_BYTES,
                    ) != (row["sha256"], row["bytes"]):
                        raise FinlandStageError(f"incoming artifact {kind} differs")
            elif phase == "release-materialized":
                _materialize_release(
                    release_bundle,
                    paths["release_root"],  # type: ignore[arg-type]
                    release_sha=manifest["release_sha"],
                    release_tree_sha=manifest["release_tree_sha"],
                    required_uid=required_uid,
                    runner=runner,
                )
            elif phase == "archives-verified":
                journal["archive_evidence"] = verified_archives
                _write_journal(
                    journal_path,  # type: ignore[arg-type]
                    journal,
                    create=False,
                    required_uid=required_uid,
                )
            elif phase.endswith("-loaded"):
                image_role = phase.removesuffix("-loaded")
                _stage_image(
                    image_role,
                    manifest=manifest,
                    paths=paths,
                    journal=journal,
                    journal_path=journal_path,  # type: ignore[arg-type]
                    required_uid=required_uid,
                    runner=runner,
                    checkpoint=callback,
                )
            elif phase == "attested":
                if set(journal["runtime_image_ids"]) != set(IMAGE_ROLES):
                    raise FinlandStageError("runtime image inventory is incomplete")
                if len(set(journal["runtime_image_ids"].values())) != len(
                    IMAGE_ROLES
                ):
                    raise FinlandStageError("runtime image IDs are not distinct")
                document = _attestation_document(
                    manifest,
                    str(request["operation_manifest_sha256"]),
                    journal,
                )
                payload = canonical_json(document)
                _write_create_only(
                    paths["attestation"],  # type: ignore[arg-type]
                    payload,
                    required_uid=required_uid,
                    mode=0o600,
                    maximum=MAX_ATTESTATION_BYTES,
                )
            else:
                raise FinlandStageError("unknown stage phase")
            callback(f"after-phase:{phase}")
            _complete_phase(
                journal_path,  # type: ignore[arg-type]
                journal,
                phase,
                required_uid=required_uid,
            )

        _verify_convergence_source_set_launcher(
            paths["release_root"],  # type: ignore[arg-type]
            required_uid=required_uid,
        )
        _verify_materialized_release(
            paths["release_root"],  # type: ignore[arg-type]
            bundle=release_bundle,
            release_sha=manifest["release_sha"],
            release_tree_sha=manifest["release_tree_sha"],
            required_uid=required_uid,
            runner=runner,
        )
        for image_role in IMAGE_ROLES:
            evidence = _verify_runtime_image(
                journal["runtime_image_ids"][image_role],
                image_role=image_role,
                expected=manifest["image_artifacts"][image_role],
                release_sha=manifest["release_sha"],
                runner=runner,
            )
            if evidence != journal["images"][image_role]:
                raise FinlandStageError("runtime image evidence differs on resume")
        expected_attestation = canonical_json(
            _attestation_document(
                manifest,
                str(request["operation_manifest_sha256"]),
                journal,
            )
        )
        observed_attestation = _read_secure_file(
            paths["attestation"],  # type: ignore[arg-type]
            required_uid=required_uid,
            expected_mode=0o600,
            maximum=MAX_ATTESTATION_BYTES,
        )
        if observed_attestation != expected_attestation:
            raise FinlandStageError("stage attestation differs")
        attestation_sha256 = hashlib.sha256(observed_attestation).hexdigest()
        return {
            "schema": RESULT_SCHEMA,
            "status": "staged",
            "operation_id": manifest["operation_id"],
            "release_sha": manifest["release_sha"],
            "release_tree_sha": manifest["release_tree_sha"],
            "role": manifest["role"],
            "operation_manifest_sha256": request[
                "operation_manifest_sha256"
            ],
            "stage_attestation_sha256": attestation_sha256,
            "stage_attestation_path": str(paths["attestation"]),
            "runtime_image_ids": {
                role_name: journal["runtime_image_ids"][role_name]
                for role_name in IMAGE_ROLES
            },
            "containers_started": False,
            "services_started": False,
            "networks_created": False,
            "volumes_created": False,
            "current_mutated": False,
            "data_mutated": False,
        }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--version", action="store_true")
    mode.add_argument("--install-bootstrap-request-b64")
    mode.add_argument("--request-b64")
    parser.add_argument("--expected-agent-sha256")
    parser.add_argument("--pull", choices=("never",), default="never")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.pull != "never":
            raise FinlandStageError("only --pull never is supported")
        if os.geteuid() != 0:
            raise FinlandStageError("Finland stage agent must run as root")
        if args.version:
            if args.expected_agent_sha256 is None:
                raise FinlandStageError("version readback requires the agent SHA-256")
            result = _agent_version(
                Path(__file__),
                expected_sha256=args.expected_agent_sha256,
                required_uid=0,
            )
        elif args.install_bootstrap_request_b64 is not None:
            with _execution_authority(control_fd=0):
                request = _decode_request(
                    args.install_bootstrap_request_b64,
                    bootstrap=True,
                )
                result = install_bootstrap(
                    request,
                    executing_path=Path(__file__),
                    required_uid=0,
                )
        else:
            with _execution_authority(control_fd=0):
                request = _decode_request(
                    str(args.request_b64),
                    bootstrap=False,
                )
                if args.expected_agent_sha256 not in {
                    None,
                    request["agent_sha256"],
                }:
                    raise FinlandStageError(
                        "CLI agent SHA-256 differs from request"
                    )
                result = stage_operation(request, required_uid=0)
        print(canonical_json(result).decode("ascii"))
        return 0
    except FinlandStageError as exc:
        print(
            canonical_json(
                {
                    "schema": RESULT_SCHEMA,
                    "status": "blocked",
                    "error": str(exc),
                }
            ).decode("ascii"),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
