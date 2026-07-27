#!/usr/bin/env python3
"""Seal exact release and local-image artifacts for a production shadow operation.

The producer is deliberately local-only. It creates one Git bundle and four
tagless single-image Docker archives from immutable image IDs. It never builds,
pulls, loads, tags, starts, stops, or contacts a remote host.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import tarfile
from typing import Any, Callable, Mapping


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.secure_file_io import (  # noqa: E402
    SecureFileError,
    read_secure_bytes,
    write_secure_atomic_bytes,
    write_secure_new_bytes,
)
from core.docker_image_identity import (  # noqa: E402
    DockerImageIdentityError,
    image_content_descriptor,
    image_content_descriptor_from_archive_config,
    verify_content_descriptor,
)
from scripts.wa_ir_production_transport_contract import (  # noqa: E402
    ProductionTransportError,
    validate_operation_id,
)


ARTIFACT_ROOT = Path(
    "/root/secure-envs/trading-bot/three-site-production-shadow/release-artifacts"
)
GIT = "/usr/bin/git"
DOCKER = "/usr/bin/docker"
JOURNAL_SCHEMA = "production-shadow-release-artifact-journal-v2"
CLOSURE_SCHEMA = "production-shadow-release-artifact-closure-v2"
PLAN_SCHEMA = "production-shadow-release-artifact-plan-v1"
ZERO_SHA256 = "0" * 64
MAX_JOURNAL_BYTES = 1024 * 1024
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_COMMAND_OUTPUT_BYTES = 2 * 1024 * 1024
MAX_ARTIFACT_BYTES = 64 * 1024 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 250_000
MAX_ARCHIVE_CONFIG_BYTES = 16 * 1024 * 1024
MAX_ARCHIVE_MANIFEST_BYTES = 1024 * 1024
RELEASE_RE = re.compile(r"^[0-9a-f]{40}$")
IMAGE_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
CONTENT_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
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
IMAGE_ROLES = ("app", "postgres", "redis", "nginx")
RELEASE_BOUND_IMAGE_ROLES = frozenset({"app", "postgres"})
POSTGRES_RUNTIME_UID_LABEL = "trading-bot.postgres.runtime-uid"
POSTGRES_RUNTIME_GID_LABEL = "trading-bot.postgres.runtime-gid"
EXPECTED_POSTGRES_RUNTIME_UID = 70
EXPECTED_POSTGRES_RUNTIME_GID = 70
IMAGE_FILENAMES = {
    "app": "app-image.tar",
    "postgres": "postgres-image.tar",
    "redis": "redis-image.tar",
    "nginx": "nginx-image.tar",
}
PHASES = (
    "verify-release",
    "seal-release-bundle",
    "seal-app-image",
    "seal-postgres-image",
    "seal-redis-image",
    "seal-nginx-image",
    "seal-closure",
)
ARTIFACT_PHASE_KEYS = {
    "seal-release-bundle": "release_bundle",
    "seal-app-image": "app_image",
    "seal-postgres-image": "postgres_image",
    "seal-redis-image": "redis_image",
    "seal-nginx-image": "nginx_image",
    "seal-closure": "closure_manifest",
}
EXPECTED_ARTIFACT_RECORDS = {
    "release_bundle": ("release.bundle", "git-bundle", False),
    "app_image": ("app-image.tar", "docker-archive", True),
    "postgres_image": ("postgres-image.tar", "docker-archive", True),
    "redis_image": ("redis-image.tar", "docker-archive", True),
    "nginx_image": ("nginx-image.tar", "docker-archive", True),
    "closure_manifest": ("closure-manifest.json", "json-manifest", False),
}
ARTIFACT_RECORD_FIELDS = frozenset(
    {
        "filename",
        "sha256",
        "bytes",
        "kind",
        "source_engine_id",
        "config_digest",
        "content_descriptor",
        "content_identity",
    }
)
JOURNAL_FIELDS = frozenset(
    {
        "schema",
        "operation_id",
        "binding_sha256",
        "release_sha",
        "release_tree_sha",
        "status",
        "completed_phases",
        "current_phase",
        "artifacts",
        "events",
        "event_tail_sha256",
        "state_sha256",
    }
)


class ReleaseArtifactError(RuntimeError):
    """Raised when an artifact cannot be proven exact and safely published."""


Checkpoint = Callable[[str], None]


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _state_hash(journal: Mapping[str, Any]) -> str:
    return _sha256(
        {key: value for key, value in journal.items() if key != "state_sha256"}
    )


def _confirmation(operation_id: str, release_sha: str) -> str:
    return f"SEAL-PRODUCTION-SHADOW-ARTIFACTS:{operation_id}:{release_sha}"


def _validate_identity(
    *,
    operation_id: str,
    release_root: Path,
    release_sha: str,
    release_tree_sha: str,
    image_ids: Mapping[str, str],
) -> tuple[str, Path, dict[str, str], str]:
    try:
        canonical_operation_id = validate_operation_id(operation_id)
    except ProductionTransportError as exc:
        raise ReleaseArtifactError("operation ID is not a canonical nonzero UUID") from exc
    if (
        RELEASE_RE.fullmatch(release_sha) is None
        or RELEASE_RE.fullmatch(release_tree_sha) is None
    ):
        raise ReleaseArtifactError("release commit or tree identity is invalid")
    if set(image_ids) != set(IMAGE_ROLES):
        raise ReleaseArtifactError("exactly four named image IDs are required")
    canonical_images = {role: str(image_ids[role]) for role in IMAGE_ROLES}
    if any(IMAGE_ID_RE.fullmatch(value) is None for value in canonical_images.values()):
        raise ReleaseArtifactError("every image must be addressed by immutable sha256 ID")
    if len(set(canonical_images.values())) != len(canonical_images):
        raise ReleaseArtifactError("image IDs must be distinct across the four roles")
    if not release_root.is_absolute() or ".." in release_root.parts:
        raise ReleaseArtifactError("release root must be an absolute canonical path")
    try:
        canonical_release_root = release_root.resolve(strict=True)
    except OSError as exc:
        raise ReleaseArtifactError("release root is unavailable") from exc
    if canonical_release_root != release_root:
        raise ReleaseArtifactError("release root must not traverse symlinks")
    binding = {
        "operation_id": canonical_operation_id,
        "release_root": str(canonical_release_root),
        "release_sha": release_sha,
        "release_tree_sha": release_tree_sha,
        "images": canonical_images,
    }
    return (
        canonical_operation_id,
        canonical_release_root,
        canonical_images,
        _sha256(binding),
    )


@contextmanager
def _private_umask():
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
        raise ReleaseArtifactError("artifact directory could not be synchronized") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _assert_secure_directory(path: Path, *, owner_uid: int) -> None:
    try:
        metadata = path.stat(follow_symlinks=False)
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise ReleaseArtifactError("artifact directory is unavailable") from exc
    if (
        resolved != path
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != owner_uid
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise ReleaseArtifactError("artifact directory must be real and owner-only")


def _ensure_secure_directory(path: Path, *, owner_uid: int) -> None:
    if path.exists() or path.is_symlink():
        _assert_secure_directory(path, owner_uid=owner_uid)
        return
    try:
        with _private_umask():
            path.mkdir(mode=0o700, parents=True)
        _assert_secure_directory(path, owner_uid=owner_uid)
        _fsync_directory(path)
        _fsync_directory(path.parent)
    except FileExistsError:
        _assert_secure_directory(path, owner_uid=owner_uid)
    except ReleaseArtifactError:
        raise
    except OSError as exc:
        raise ReleaseArtifactError("artifact directory could not be created") from exc


@contextmanager
def _operation_lock(operation_root: Path, *, owner_uid: int):
    lock_path = operation_root / "operation.lock"
    descriptor = -1
    try:
        descriptor = os.open(
            lock_path,
            os.O_RDWR
            | os.O_CREAT
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != owner_uid
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_nlink != 1
        ):
            raise ReleaseArtifactError("operation lock is unsafe")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ReleaseArtifactError("another artifact producer is active") from exc
        os.fsync(descriptor)
        _fsync_directory(operation_root)
        yield
    except ReleaseArtifactError:
        raise
    except OSError as exc:
        raise ReleaseArtifactError("operation lock is unavailable") from exc
    finally:
        if descriptor >= 0:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            except OSError:
                pass
            os.close(descriptor)


def _execute_command(
    arguments: list[str],
    *,
    timeout: int,
    env: Mapping[str, str],
) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(
            arguments,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=timeout,
            env=dict(env),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ReleaseArtifactError(
            f"required local command is unavailable: {Path(arguments[0]).name}"
        ) from exc


def _run(
    arguments: list[str],
    *,
    timeout: int,
    env: Mapping[str, str],
) -> bytes:
    result = _execute_command(arguments, timeout=timeout, env=env)
    if (
        len(result.stdout) > MAX_COMMAND_OUTPUT_BYTES
        or len(result.stderr) > MAX_COMMAND_OUTPUT_BYTES
    ):
        raise ReleaseArtifactError("required local command output exceeded its bound")
    if result.returncode != 0:
        raise ReleaseArtifactError(
            f"required local command failed closed: {Path(arguments[0]).name}"
        )
    return result.stdout


def _run_text(
    arguments: list[str],
    *,
    timeout: int,
    env: Mapping[str, str],
) -> str:
    try:
        return _run(arguments, timeout=timeout, env=env).decode("utf-8").strip()
    except UnicodeDecodeError as exc:
        raise ReleaseArtifactError(
            "required local command returned non-UTF-8 output"
        ) from exc


def _verify_release_source(
    release_root: Path,
    *,
    release_sha: str,
    release_tree_sha: str,
    owner_uid: int,
) -> None:
    try:
        metadata = release_root.stat(follow_symlinks=False)
    except OSError as exc:
        raise ReleaseArtifactError("release root cannot be inspected") from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != owner_uid
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        raise ReleaseArtifactError("release root is not an owner-controlled directory")
    commands = {
        "root": [GIT, "-C", str(release_root), "rev-parse", "--show-toplevel"],
        "head": [GIT, "-C", str(release_root), "rev-parse", "HEAD"],
        "tree": [GIT, "-C", str(release_root), "rev-parse", "HEAD^{tree}"],
        "branch": [
            GIT,
            "-C",
            str(release_root),
            "rev-parse",
            "--abbrev-ref",
            "HEAD",
        ],
        "status": [
            GIT,
            "-C",
            str(release_root),
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
        ],
        "remotes": [GIT, "-C", str(release_root), "remote"],
    }
    observed = {
        name: _run_text(arguments, timeout=60, env=SAFE_GIT_ENV)
        for name, arguments in commands.items()
    }
    if (
        observed["root"] != str(release_root)
        or observed["head"] != release_sha
        or observed["tree"] != release_tree_sha
        or observed["branch"] != "HEAD"
        or observed["status"]
        or observed["remotes"]
    ):
        raise ReleaseArtifactError(
            "release must be exact, detached, clean, and remote-free"
        )


def _hash_regular_file(
    path: Path,
    *,
    owner_uid: int,
    maximum: int = MAX_ARTIFACT_BYTES,
) -> tuple[str, int]:
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
            or before.st_uid != owner_uid
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_nlink != 1
            or not 1 <= before.st_size <= maximum
        ):
            raise ReleaseArtifactError("sealed artifact file is unsafe")
        digest = hashlib.sha256()
        size = 0
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > maximum:
                raise ReleaseArtifactError("sealed artifact exceeds its size bound")
            digest.update(chunk)
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
        )
        if any(getattr(before, field) != getattr(after, field) for field in stable):
            raise ReleaseArtifactError("sealed artifact changed while being read")
        return digest.hexdigest(), size
    except ReleaseArtifactError:
        raise
    except OSError as exc:
        raise ReleaseArtifactError("sealed artifact cannot be read safely") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _temporary_path(destination: Path) -> Path:
    return destination.with_name(f".{destination.name}.materializing")


def _reconcile_temporary(
    destination: Path,
    *,
    owner_uid: int,
) -> None:
    temporary = _temporary_path(destination)
    if not temporary.exists() and not temporary.is_symlink():
        return
    try:
        temporary_metadata = temporary.stat(follow_symlinks=False)
    except OSError as exc:
        raise ReleaseArtifactError("artifact temporary is unsafe") from exc
    if (
        not stat.S_ISREG(temporary_metadata.st_mode)
        or temporary_metadata.st_uid != owner_uid
        or stat.S_IMODE(temporary_metadata.st_mode) != 0o600
        or temporary_metadata.st_nlink not in {1, 2}
        or not 0 <= temporary_metadata.st_size <= MAX_ARTIFACT_BYTES
    ):
        raise ReleaseArtifactError("artifact temporary is unsafe")
    if destination.exists() or destination.is_symlink():
        try:
            destination_metadata = destination.stat(follow_symlinks=False)
        except OSError as exc:
            raise ReleaseArtifactError("artifact destination is unsafe") from exc
        if (
            stat.S_ISREG(destination_metadata.st_mode)
            and temporary_metadata.st_dev == destination_metadata.st_dev
            and temporary_metadata.st_ino == destination_metadata.st_ino
        ):
            temporary.unlink()
            _fsync_directory(destination.parent)
            return
    if temporary_metadata.st_nlink != 1:
        raise ReleaseArtifactError("artifact temporary link identity is ambiguous")
    try:
        temporary.unlink()
        _fsync_directory(destination.parent)
    except OSError as exc:
        raise ReleaseArtifactError("artifact temporary could not be reconciled") from exc


def _publish_temporary(
    destination: Path,
    *,
    owner_uid: int,
) -> tuple[str, int]:
    temporary = _temporary_path(destination)
    temporary_digest, temporary_bytes = _hash_regular_file(
        temporary,
        owner_uid=owner_uid,
    )
    try:
        os.link(temporary, destination, follow_symlinks=False)
    except FileExistsError:
        existing = _hash_regular_file(destination, owner_uid=owner_uid)
        if existing != (temporary_digest, temporary_bytes):
            raise ReleaseArtifactError("create-only artifact destination differs")
    except OSError as exc:
        raise ReleaseArtifactError("artifact could not be published create-only") from exc
    _fsync_directory(destination.parent)
    try:
        temporary.unlink()
    except OSError as exc:
        raise ReleaseArtifactError("published artifact temporary remains unsafe") from exc
    _fsync_directory(destination.parent)
    return _hash_regular_file(destination, owner_uid=owner_uid)


def _artifact_record(
    *,
    filename: str,
    digest: str,
    size: int,
    kind: str,
    source_engine_id: str | None,
    config_digest: str | None,
    content_descriptor: Mapping[str, Any] | None,
    content_identity: str | None,
) -> dict[str, Any]:
    is_image = source_engine_id is not None
    metadata_values = (
        config_digest,
        content_descriptor,
        content_identity,
    )
    metadata_shape_valid = (
        all(value is not None for value in metadata_values)
        if is_image
        else all(value is None for value in metadata_values)
    )
    if (
        not filename
        or "/" in filename
        or SHA256_RE.fullmatch(digest) is None
        or not 1 <= size <= MAX_ARTIFACT_BYTES
        or kind not in {"git-bundle", "docker-archive", "json-manifest"}
        or (
            source_engine_id is not None
            and IMAGE_ID_RE.fullmatch(source_engine_id) is None
        )
        or (
            config_digest is not None
            and CONTENT_ID_RE.fullmatch(config_digest) is None
        )
        or (
            content_identity is not None
            and CONTENT_ID_RE.fullmatch(content_identity) is None
        )
        or not metadata_shape_valid
    ):
        raise ReleaseArtifactError("artifact journal record is invalid")
    if is_image:
        try:
            expected_identity = verify_content_descriptor(content_descriptor)
        except DockerImageIdentityError as exc:
            raise ReleaseArtifactError(
                "artifact journal image descriptor is invalid"
            ) from exc
        if expected_identity != content_identity:
            raise ReleaseArtifactError(
                "artifact journal image content identity differs"
            )
    return {
        "filename": filename,
        "sha256": digest,
        "bytes": size,
        "kind": kind,
        "source_engine_id": source_engine_id,
        "config_digest": config_digest,
        "content_descriptor": (
            dict(content_descriptor)
            if content_descriptor is not None
            else None
        ),
        "content_identity": content_identity,
    }


def _append_event(
    journal: dict[str, Any],
    *,
    kind: str,
    phase: str | None,
    artifact_sha256: str | None = None,
) -> None:
    event = {
        "sequence": len(journal["events"]) + 1,
        "kind": kind,
        "phase": phase,
        "artifact_sha256": artifact_sha256,
        "previous_event_sha256": journal["event_tail_sha256"],
    }
    event["event_sha256"] = _sha256(event)
    journal["events"].append(event)
    journal["event_tail_sha256"] = event["event_sha256"]


def _validate_journal(journal: Any) -> dict[str, Any]:
    if not isinstance(journal, dict) or set(journal) != JOURNAL_FIELDS:
        raise ReleaseArtifactError("artifact journal fields are invalid")
    if (
        journal["schema"] != JOURNAL_SCHEMA
        or RELEASE_RE.fullmatch(str(journal["release_sha"])) is None
        or RELEASE_RE.fullmatch(str(journal["release_tree_sha"])) is None
        or SHA256_RE.fullmatch(str(journal["binding_sha256"])) is None
        or journal["status"] not in {"active", "complete"}
        or journal["state_sha256"] != _state_hash(journal)
    ):
        raise ReleaseArtifactError("artifact journal identity or state hash is invalid")
    try:
        validate_operation_id(journal["operation_id"])
    except ProductionTransportError as exc:
        raise ReleaseArtifactError("artifact journal operation ID is invalid") from exc
    completed = journal["completed_phases"]
    current = journal["current_phase"]
    artifacts = journal["artifacts"]
    events = journal["events"]
    if (
        not isinstance(completed, list)
        or completed != list(PHASES[: len(completed)])
        or len(completed) != len(set(completed))
        or current
        not in ({None} if len(completed) == len(PHASES) else {None, PHASES[len(completed)]})
        or not isinstance(artifacts, dict)
        or not isinstance(events, list)
    ):
        raise ReleaseArtifactError("artifact journal phase prefix is invalid")
    expected_artifact_keys = {
        ARTIFACT_PHASE_KEYS[phase]
        for phase in completed
        if phase in ARTIFACT_PHASE_KEYS
    }
    if set(artifacts) != expected_artifact_keys:
        raise ReleaseArtifactError("artifact journal closure differs from completed phases")
    for key, record in artifacts.items():
        if (
            not isinstance(key, str)
            or not isinstance(record, dict)
            or set(record) != ARTIFACT_RECORD_FIELDS
        ):
            raise ReleaseArtifactError("artifact journal record fields are invalid")
        _artifact_record(
            filename=record["filename"],
            digest=record["sha256"],
            size=record["bytes"],
            kind=record["kind"],
            source_engine_id=record["source_engine_id"],
            config_digest=record["config_digest"],
            content_descriptor=record["content_descriptor"],
            content_identity=record["content_identity"],
        )
        expected_filename, expected_kind, expects_image = (
            EXPECTED_ARTIFACT_RECORDS[key]
        )
        if (
            record["filename"] != expected_filename
            or record["kind"] != expected_kind
            or (record["source_engine_id"] is not None) != expects_image
        ):
            raise ReleaseArtifactError(
                "artifact journal record does not match its exact phase"
            )
    replay_completed: list[str] = []
    replay_current: str | None = None
    previous = ZERO_SHA256
    if not events:
        raise ReleaseArtifactError("artifact journal has no creation event")
    for index, event in enumerate(events, 1):
        if not isinstance(event, dict) or set(event) != {
            "sequence",
            "kind",
            "phase",
            "artifact_sha256",
            "previous_event_sha256",
            "event_sha256",
        }:
            raise ReleaseArtifactError("artifact journal event fields are invalid")
        expected_hash = _sha256(
            {key: value for key, value in event.items() if key != "event_sha256"}
        )
        if (
            event["sequence"] != index
            or event["previous_event_sha256"] != previous
            or event["event_sha256"] != expected_hash
        ):
            raise ReleaseArtifactError("artifact journal event chain is invalid")
        if index == 1:
            if (
                event["kind"] != "journal-created"
                or event["phase"] is not None
                or event["artifact_sha256"] is not None
            ):
                raise ReleaseArtifactError("artifact journal creation event is invalid")
        elif event["kind"] == "phase-started":
            if (
                replay_current is not None
                or len(replay_completed) >= len(PHASES)
                or event["phase"] != PHASES[len(replay_completed)]
                or event["artifact_sha256"] is not None
            ):
                raise ReleaseArtifactError("artifact journal start event is invalid")
            replay_current = event["phase"]
        elif event["kind"] == "phase-completed":
            if event["phase"] != replay_current:
                raise ReleaseArtifactError("artifact journal completion event is invalid")
            artifact_key = ARTIFACT_PHASE_KEYS.get(replay_current)
            expected_digest = (
                artifacts[artifact_key]["sha256"] if artifact_key is not None else None
            )
            if event["artifact_sha256"] != expected_digest:
                raise ReleaseArtifactError("artifact journal event evidence differs")
            replay_completed.append(replay_current)
            replay_current = None
        else:
            raise ReleaseArtifactError("artifact journal event kind is invalid")
        previous = event["event_sha256"]
    if (
        replay_completed != completed
        or replay_current != current
        or journal["event_tail_sha256"] != previous
        or (journal["status"] == "complete") != (completed == list(PHASES))
        or (journal["status"] == "complete" and current is not None)
    ):
        raise ReleaseArtifactError("artifact journal state differs from its event chain")
    return journal


def _reconcile_journal_temporaries(path: Path, *, owner_uid: int) -> None:
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
        raise ReleaseArtifactError(
            "artifact journal temporary inventory is unavailable"
        ) from exc
    if len(candidates) > 64:
        raise ReleaseArtifactError(
            "artifact journal temporary inventory is excessive"
        )
    changed = False
    for candidate in candidates:
        try:
            temporary = candidate.stat(follow_symlinks=False)
        except OSError as exc:
            raise ReleaseArtifactError("artifact journal temporary is unsafe") from exc
        if (
            not stat.S_ISREG(temporary.st_mode)
            or temporary.st_uid != owner_uid
            or stat.S_IMODE(temporary.st_mode) != 0o600
            or temporary.st_nlink not in {1, 2}
            or not 0 <= temporary.st_size <= MAX_JOURNAL_BYTES
        ):
            raise ReleaseArtifactError("artifact journal temporary is unsafe")
        if temporary.st_nlink == 2:
            try:
                published = path.stat(follow_symlinks=False)
            except OSError as exc:
                raise ReleaseArtifactError(
                    "artifact journal temporary link identity is ambiguous"
                ) from exc
            if (
                not stat.S_ISREG(published.st_mode)
                or temporary.st_dev != published.st_dev
                or temporary.st_ino != published.st_ino
            ):
                raise ReleaseArtifactError(
                    "artifact journal temporary link identity is ambiguous"
                )
        try:
            candidate.unlink()
        except OSError as exc:
            raise ReleaseArtifactError(
                "artifact journal temporary could not be reconciled"
            ) from exc
        changed = True
    if changed:
        _fsync_directory(path.parent)


def _write_journal(
    path: Path,
    journal: dict[str, Any],
    *,
    create: bool,
) -> None:
    _reconcile_journal_temporaries(path, owner_uid=0)
    journal["state_sha256"] = _state_hash(journal)
    _validate_journal(journal)
    payload = json.dumps(journal, sort_keys=True, indent=2).encode("utf-8") + b"\n"
    try:
        writer = write_secure_new_bytes if create else write_secure_atomic_bytes
        writer(
            path,
            payload,
            label="production shadow release artifact journal",
            mode=0o600,
            max_size=MAX_JOURNAL_BYTES,
        )
    except SecureFileError as exc:
        raise ReleaseArtifactError("artifact journal could not be persisted") from exc


def _load_or_create_journal(
    path: Path,
    *,
    operation_id: str,
    binding_sha256: str,
    release_sha: str,
    release_tree_sha: str,
) -> dict[str, Any]:
    _reconcile_journal_temporaries(path, owner_uid=0)
    if not path.exists() and not path.is_symlink():
        journal: dict[str, Any] = {
            "schema": JOURNAL_SCHEMA,
            "operation_id": operation_id,
            "binding_sha256": binding_sha256,
            "release_sha": release_sha,
            "release_tree_sha": release_tree_sha,
            "status": "active",
            "completed_phases": [],
            "current_phase": None,
            "artifacts": {},
            "events": [],
            "event_tail_sha256": ZERO_SHA256,
            "state_sha256": "",
        }
        _append_event(journal, kind="journal-created", phase=None)
        _write_journal(path, journal, create=True)
        return journal
    try:
        payload = read_secure_bytes(
            path,
            label="production shadow release artifact journal",
            owner_uid=0,
            max_size=MAX_JOURNAL_BYTES,
        )
        journal = json.loads(payload.decode("utf-8"), object_pairs_hook=_strict_object)
    except (SecureFileError, UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise ReleaseArtifactError("artifact journal is unreadable") from exc
    journal = _validate_journal(journal)
    if (
        journal["operation_id"] != operation_id
        or journal["binding_sha256"] != binding_sha256
        or journal["release_sha"] != release_sha
        or journal["release_tree_sha"] != release_tree_sha
    ):
        raise ReleaseArtifactError("existing artifact journal has different bindings")
    return journal


def _start_phase(path: Path, journal: dict[str, Any], phase: str) -> None:
    if journal["current_phase"] == phase:
        return
    if (
        journal["current_phase"] is not None
        or phase != PHASES[len(journal["completed_phases"])]
    ):
        raise ReleaseArtifactError("artifact phase transition is invalid")
    journal["current_phase"] = phase
    _append_event(journal, kind="phase-started", phase=phase)
    _write_journal(path, journal, create=False)


def _complete_phase(
    path: Path,
    journal: dict[str, Any],
    phase: str,
    *,
    artifact: dict[str, Any] | None,
) -> None:
    if journal["current_phase"] != phase:
        raise ReleaseArtifactError("artifact phase completion is out of order")
    artifact_key = ARTIFACT_PHASE_KEYS.get(phase)
    if (artifact_key is None) != (artifact is None):
        raise ReleaseArtifactError("artifact phase result shape is invalid")
    digest = None
    if artifact_key is not None:
        if artifact is None:
            raise ReleaseArtifactError("artifact phase completed without evidence")
        journal["artifacts"][artifact_key] = artifact
        digest = artifact["sha256"]
    journal["completed_phases"].append(phase)
    journal["current_phase"] = None
    if journal["completed_phases"] == list(PHASES):
        journal["status"] = "complete"
    _append_event(
        journal,
        kind="phase-completed",
        phase=phase,
        artifact_sha256=digest,
    )
    _write_journal(path, journal, create=False)


def _verify_bundle(
    path: Path,
    *,
    release_root: Path,
    release_sha: str,
    owner_uid: int,
) -> tuple[str, int]:
    digest, size = _hash_regular_file(path, owner_uid=owner_uid)
    _run_text(
        [GIT, "-C", str(release_root), "bundle", "verify", str(path)],
        timeout=300,
        env=SAFE_GIT_ENV,
    )
    heads = _run_text(
        [GIT, "bundle", "list-heads", str(path)],
        timeout=60,
        env=SAFE_GIT_ENV,
    ).splitlines()
    if heads != [f"{release_sha} HEAD"]:
        raise ReleaseArtifactError("Git bundle does not contain only the exact HEAD")
    return digest, size


def _seal_bundle(
    destination: Path,
    *,
    release_root: Path,
    release_sha: str,
    release_tree_sha: str,
    owner_uid: int,
    checkpoint: Checkpoint,
) -> dict[str, Any]:
    _reconcile_temporary(destination, owner_uid=owner_uid)
    if destination.exists() or destination.is_symlink():
        digest, size = _verify_bundle(
            destination,
            release_root=release_root,
            release_sha=release_sha,
            owner_uid=owner_uid,
        )
        return _artifact_record(
            filename=destination.name,
            digest=digest,
            size=size,
            kind="git-bundle",
            source_engine_id=None,
            config_digest=None,
            content_descriptor=None,
            content_identity=None,
        )
    temporary = _temporary_path(destination)
    with _private_umask():
        _run(
            [
                GIT,
                "-C",
                str(release_root),
                "-c",
                "core.hooksPath=/dev/null",
                "bundle",
                "create",
                str(temporary),
                "HEAD",
            ],
            timeout=600,
            env=SAFE_GIT_ENV,
        )
    try:
        os.chmod(temporary, 0o600, follow_symlinks=False)
    except OSError as exc:
        raise ReleaseArtifactError("Git bundle temporary permissions are unsafe") from exc
    checkpoint("after-command:seal-release-bundle")
    _verify_release_source(
        release_root,
        release_sha=release_sha,
        release_tree_sha=release_tree_sha,
        owner_uid=owner_uid,
    )
    _verify_bundle(
        temporary,
        release_root=release_root,
        release_sha=release_sha,
        owner_uid=owner_uid,
    )
    digest, size = _publish_temporary(destination, owner_uid=owner_uid)
    checkpoint("after-publish:seal-release-bundle")
    return _artifact_record(
        filename=destination.name,
        digest=digest,
        size=size,
        kind="git-bundle",
        source_engine_id=None,
        config_digest=None,
        content_descriptor=None,
        content_identity=None,
    )


def _inspect_image(
    role: str,
    image_id: str,
    *,
    release_sha: str,
) -> tuple[dict[str, Any], str]:
    try:
        raw = json.loads(
            _run_text(
                [DOCKER, "image", "inspect", image_id],
                timeout=120,
                env=SAFE_ENV,
            ),
            object_pairs_hook=_strict_object,
        )
    except (ValueError, json.JSONDecodeError) as exc:
        raise ReleaseArtifactError("Docker image inspection returned invalid JSON") from exc
    if not isinstance(raw, list) or len(raw) != 1 or not isinstance(raw[0], dict):
        raise ReleaseArtifactError("Docker image inspection is ambiguous")
    image = raw[0]
    config = image.get("Config")
    labels = config.get("Labels") if isinstance(config, dict) else None
    if (
        image.get("Id") != image_id
        or image.get("Os") != "linux"
        or image.get("Architecture") != "amd64"
    ):
        raise ReleaseArtifactError("Docker image identity or platform differs")
    if role in RELEASE_BOUND_IMAGE_ROLES and (
        not isinstance(labels, dict)
        or labels.get("org.opencontainers.image.revision") != release_sha
    ):
        raise ReleaseArtifactError(
            f"{role} image lacks the exact OCI release revision"
        )
    if role == "postgres" and (
        not isinstance(labels, dict)
        or labels.get(POSTGRES_RUNTIME_UID_LABEL)
        != str(EXPECTED_POSTGRES_RUNTIME_UID)
        or labels.get(POSTGRES_RUNTIME_GID_LABEL)
        != str(EXPECTED_POSTGRES_RUNTIME_GID)
    ):
        raise ReleaseArtifactError(
            "postgres image lacks the exact runtime UID/GID contract labels"
        )
    try:
        descriptor, content_identity = image_content_descriptor(image)
    except DockerImageIdentityError as exc:
        raise ReleaseArtifactError(
            "Docker image lacks a canonical semantic content identity"
        ) from exc
    return descriptor, content_identity


def _safe_archive_path(raw: Any, *, label: str) -> str:
    if not isinstance(raw, str) or not raw or "\\" in raw or "\x00" in raw:
        raise ReleaseArtifactError(f"{label} is invalid")
    path = PurePosixPath(raw)
    if (
        path.is_absolute()
        or raw != path.as_posix()
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ReleaseArtifactError(f"{label} is outside the archive")
    return raw


def _archive_members(
    archive: tarfile.TarFile,
) -> dict[str, tarfile.TarInfo]:
    regular: dict[str, tarfile.TarInfo] = {}
    names: set[str] = set()
    total = 0
    count = 0
    for member in archive:
        count += 1
        if count > MAX_ARCHIVE_MEMBERS:
            raise ReleaseArtifactError("Docker archive has too many members")
        name = _safe_archive_path(
            member.name.rstrip("/"),
            label="Docker archive member",
        )
        if name in names:
            raise ReleaseArtifactError("Docker archive contains a duplicate member")
        names.add(name)
        if member.isdir():
            if member.size != 0:
                raise ReleaseArtifactError(
                    "Docker archive directory contains data"
                )
            continue
        if not member.isreg():
            raise ReleaseArtifactError(
                "Docker archive contains a link, sparse, or special member"
            )
        if member.size < 0 or member.size > MAX_ARTIFACT_BYTES:
            raise ReleaseArtifactError("Docker archive member size is invalid")
        total += member.size
        if total > MAX_ARTIFACT_BYTES:
            raise ReleaseArtifactError("Docker archive expanded size is oversized")
        regular[name] = member
    if not names:
        raise ReleaseArtifactError("Docker archive is empty")
    return regular


def _read_archive_member(
    archive: tarfile.TarFile,
    member: tarfile.TarInfo,
    *,
    maximum: int,
    label: str,
) -> bytes:
    if not 1 <= member.size <= maximum:
        raise ReleaseArtifactError(f"{label} is empty or oversized")
    source = archive.extractfile(member)
    if source is None:
        raise ReleaseArtifactError(f"{label} is unreadable")
    try:
        payload = source.read(maximum + 1)
    finally:
        source.close()
    if len(payload) != member.size:
        raise ReleaseArtifactError(f"{label} size differs")
    return payload


def _archive_image_identity(
    path: Path,
    *,
    role: str,
    release_sha: str,
) -> tuple[str, dict[str, Any], str]:
    try:
        with tarfile.open(path, mode="r:") as archive:
            regular = _archive_members(archive)
            manifest_member = regular.get("manifest.json")
            if manifest_member is None:
                raise ReleaseArtifactError("Docker archive manifest is missing")
            manifest_payload = _read_archive_member(
                archive,
                manifest_member,
                maximum=MAX_ARCHIVE_MANIFEST_BYTES,
                label="Docker archive manifest",
            )
            manifest = json.loads(
                manifest_payload.decode("utf-8"),
                object_pairs_hook=_strict_object,
            )
            if not isinstance(manifest, list) or len(manifest) != 1:
                raise ReleaseArtifactError(
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
                raise ReleaseArtifactError(
                    "Docker archive manifest entry is invalid"
                )
            tags = entry.get("RepoTags")
            if tags not in (None, []):
                raise ReleaseArtifactError("Docker archive must be tagless")
            config_name = _safe_archive_path(
                entry["Config"],
                label="Docker archive config path",
            )
            config_member = regular.get(config_name)
            if config_member is None:
                raise ReleaseArtifactError("Docker archive config is missing")
            config_payload = _read_archive_member(
                archive,
                config_member,
                maximum=MAX_ARCHIVE_CONFIG_BYTES,
                label="Docker archive config",
            )
            for layer in entry["Layers"]:
                layer_name = _safe_archive_path(
                    layer,
                    label="Docker archive layer path",
                )
                if layer_name not in regular:
                    raise ReleaseArtifactError("Docker archive layer is missing")
            config = json.loads(
                config_payload.decode("utf-8"),
                object_pairs_hook=_strict_object,
            )
    except ReleaseArtifactError:
        raise
    except (
        OSError,
        UnicodeError,
        ValueError,
        json.JSONDecodeError,
        tarfile.TarError,
    ) as exc:
        raise ReleaseArtifactError("Docker archive validation failed") from exc
    if not isinstance(config, dict):
        raise ReleaseArtifactError("Docker archive config document is invalid")
    try:
        descriptor, content_identity = (
            image_content_descriptor_from_archive_config(config)
        )
    except DockerImageIdentityError as exc:
        raise ReleaseArtifactError(
            "Docker archive lacks a canonical semantic content identity"
        ) from exc
    config_values = config.get("config")
    labels = (
        config_values.get("Labels")
        if isinstance(config_values, dict)
        else None
    )
    if descriptor["os"] != "linux" or descriptor["architecture"] != "amd64":
        raise ReleaseArtifactError("Docker archive platform differs")
    if role in RELEASE_BOUND_IMAGE_ROLES and (
        not isinstance(labels, dict)
        or labels.get("org.opencontainers.image.revision") != release_sha
    ):
        raise ReleaseArtifactError(
            f"{role} archive lacks the exact OCI release revision"
        )
    if role == "postgres" and (
        not isinstance(labels, dict)
        or labels.get(POSTGRES_RUNTIME_UID_LABEL)
        != str(EXPECTED_POSTGRES_RUNTIME_UID)
        or labels.get(POSTGRES_RUNTIME_GID_LABEL)
        != str(EXPECTED_POSTGRES_RUNTIME_GID)
    ):
        raise ReleaseArtifactError(
            "postgres archive lacks the exact runtime UID/GID contract labels"
        )
    config_digest = "sha256:" + hashlib.sha256(config_payload).hexdigest()
    return config_digest, descriptor, content_identity


def _verify_image_archive(
    path: Path,
    *,
    role: str,
    image_id: str,
    expected_content_descriptor: Mapping[str, Any],
    expected_content_identity: str,
    release_sha: str,
    owner_uid: int,
) -> tuple[str, int, str, dict[str, Any], str]:
    if (
        IMAGE_ID_RE.fullmatch(image_id) is None
        or CONTENT_ID_RE.fullmatch(expected_content_identity) is None
    ):
        raise ReleaseArtifactError("Docker archive identity binding is invalid")
    try:
        inspected_identity = verify_content_descriptor(
            expected_content_descriptor
        )
    except DockerImageIdentityError as exc:
        raise ReleaseArtifactError(
            "Docker inspected content descriptor is invalid"
        ) from exc
    if inspected_identity != expected_content_identity:
        raise ReleaseArtifactError(
            "Docker inspected content identity differs from its descriptor"
        )
    digest, size = _hash_regular_file(path, owner_uid=owner_uid)
    config_digest, descriptor, content_identity = _archive_image_identity(
        path,
        role=role,
        release_sha=release_sha,
    )
    if (
        descriptor != dict(expected_content_descriptor)
        or content_identity != expected_content_identity
    ):
        raise ReleaseArtifactError(
            "Docker archive semantic content differs from the inspected image ID"
        )
    return digest, size, config_digest, descriptor, content_identity


def _seal_image(
    destination: Path,
    *,
    role: str,
    image_id: str,
    release_sha: str,
    owner_uid: int,
    checkpoint: Checkpoint,
) -> dict[str, Any]:
    phase = f"seal-{role}-image"
    _reconcile_temporary(destination, owner_uid=owner_uid)
    expected_content_descriptor, expected_content_identity = _inspect_image(
        role,
        image_id,
        release_sha=release_sha,
    )
    if destination.exists() or destination.is_symlink():
        (
            digest,
            size,
            config_digest,
            content_descriptor,
            content_identity,
        ) = _verify_image_archive(
            destination,
            role=role,
            image_id=image_id,
            expected_content_descriptor=expected_content_descriptor,
            expected_content_identity=expected_content_identity,
            release_sha=release_sha,
            owner_uid=owner_uid,
        )
        return _artifact_record(
            filename=destination.name,
            digest=digest,
            size=size,
            kind="docker-archive",
            source_engine_id=image_id,
            config_digest=config_digest,
            content_descriptor=content_descriptor,
            content_identity=content_identity,
        )
    temporary = _temporary_path(destination)
    with _private_umask():
        _run(
            [
                DOCKER,
                "image",
                "save",
                "--output",
                str(temporary),
                image_id,
            ],
            timeout=3600,
            env=SAFE_ENV,
        )
    try:
        os.chmod(temporary, 0o600, follow_symlinks=False)
    except OSError as exc:
        raise ReleaseArtifactError("Docker archive temporary permissions are unsafe") from exc
    checkpoint(f"after-command:{phase}")
    _verify_image_archive(
        temporary,
        role=role,
        image_id=image_id,
        expected_content_descriptor=expected_content_descriptor,
        expected_content_identity=expected_content_identity,
        release_sha=release_sha,
        owner_uid=owner_uid,
    )
    _publish_temporary(destination, owner_uid=owner_uid)
    checkpoint(f"after-publish:{phase}")
    (
        digest,
        size,
        config_digest,
        content_descriptor,
        content_identity,
    ) = _verify_image_archive(
        destination,
        role=role,
        image_id=image_id,
        expected_content_descriptor=expected_content_descriptor,
        expected_content_identity=expected_content_identity,
        release_sha=release_sha,
        owner_uid=owner_uid,
    )
    return _artifact_record(
        filename=destination.name,
        digest=digest,
        size=size,
        kind="docker-archive",
        source_engine_id=image_id,
        config_digest=config_digest,
        content_descriptor=content_descriptor,
        content_identity=content_identity,
    )


def _closure_document(
    *,
    operation_id: str,
    release_sha: str,
    release_tree_sha: str,
    artifacts: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    release = artifacts["release_bundle"]
    images = {
        role: {
            "archive_sha256": artifacts[f"{role}_image"]["sha256"],
            "archive_bytes": artifacts[f"{role}_image"]["bytes"],
            "config_digest": artifacts[f"{role}_image"]["config_digest"],
            "content_descriptor": artifacts[f"{role}_image"][
                "content_descriptor"
            ],
            "content_identity": artifacts[f"{role}_image"]["content_identity"],
        }
        for role in IMAGE_ROLES
    }
    source_engine_observations = {
        role: {
            "image_id": artifacts[f"{role}_image"]["source_engine_id"],
            "informational_only": True,
        }
        for role in IMAGE_ROLES
    }
    verified_image_contracts = {
        role: {
            "os": "linux",
            "architecture": "amd64",
            "repo_tags": [],
            "oci_revision": (
                release_sha if role in RELEASE_BOUND_IMAGE_ROLES else None
            ),
        }
        for role in IMAGE_ROLES
    }
    verified_image_contracts["postgres"]["runtime_user"] = {
        "uid": EXPECTED_POSTGRES_RUNTIME_UID,
        "gid": EXPECTED_POSTGRES_RUNTIME_GID,
        "uid_label": POSTGRES_RUNTIME_UID_LABEL,
        "gid_label": POSTGRES_RUNTIME_GID_LABEL,
    }
    return {
        "schema": CLOSURE_SCHEMA,
        "operation_id": operation_id,
        "release": {
            "commit_sha": release_sha,
            "tree_sha": release_tree_sha,
            "bundle": {
                "filename": release["filename"],
                "sha256": release["sha256"],
                "bytes": release["bytes"],
            },
        },
        "images": images,
        "source_engine_observations": source_engine_observations,
        "verified_image_contracts": verified_image_contracts,
        "constraints": {
            "source_backup_included": False,
            "role_material_included": False,
            "secrets_included": False,
            "network_transfer_performed": False,
            "container_runtime_changed": False,
        },
    }


def _write_create_only_bytes(
    destination: Path,
    payload: bytes,
    *,
    owner_uid: int,
) -> tuple[str, int]:
    expected = (hashlib.sha256(payload).hexdigest(), len(payload))
    _reconcile_temporary(destination, owner_uid=owner_uid)
    if destination.exists() or destination.is_symlink():
        observed = _hash_regular_file(
            destination,
            owner_uid=owner_uid,
            maximum=MAX_MANIFEST_BYTES,
        )
        if observed != expected:
            raise ReleaseArtifactError("create-only closure manifest differs")
        return observed
    temporary = _temporary_path(destination)
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
                raise OSError("short closure manifest write")
            view = view[written:]
        os.fchmod(descriptor, 0o600)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        return _publish_temporary(destination, owner_uid=owner_uid)
    except ReleaseArtifactError:
        raise
    except OSError as exc:
        raise ReleaseArtifactError("closure manifest could not be written") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _seal_closure(
    destination: Path,
    *,
    operation_id: str,
    release_sha: str,
    release_tree_sha: str,
    artifacts: Mapping[str, Mapping[str, Any]],
    owner_uid: int,
    checkpoint: Checkpoint,
) -> dict[str, Any]:
    document = _closure_document(
        operation_id=operation_id,
        release_sha=release_sha,
        release_tree_sha=release_tree_sha,
        artifacts=artifacts,
    )
    payload = json.dumps(document, sort_keys=True, indent=2).encode("utf-8") + b"\n"
    if len(payload) > MAX_MANIFEST_BYTES:
        raise ReleaseArtifactError("closure manifest is oversized")
    digest, size = _write_create_only_bytes(
        destination,
        payload,
        owner_uid=owner_uid,
    )
    checkpoint("after-publish:seal-closure")
    return _artifact_record(
        filename=destination.name,
        digest=digest,
        size=size,
        kind="json-manifest",
        source_engine_id=None,
        config_digest=None,
        content_descriptor=None,
        content_identity=None,
    )


def _verify_record(
    artifacts_root: Path,
    *,
    key: str,
    record: Mapping[str, Any],
    release_root: Path,
    release_sha: str,
    release_tree_sha: str,
    operation_id: str,
    all_records: Mapping[str, Mapping[str, Any]],
    owner_uid: int,
) -> None:
    path = artifacts_root / str(record["filename"])
    if key == "release_bundle":
        observed = _verify_bundle(
            path,
            release_root=release_root,
            release_sha=release_sha,
            owner_uid=owner_uid,
        )
    elif key.endswith("_image"):
        role = key.removesuffix("_image")
        (
            digest,
            size,
            config_digest,
            content_descriptor,
            content_identity,
        ) = _verify_image_archive(
            path,
            role=role,
            image_id=str(record["source_engine_id"]),
            expected_content_descriptor=record["content_descriptor"],
            expected_content_identity=str(record["content_identity"]),
            release_sha=release_sha,
            owner_uid=owner_uid,
        )
        if (
            config_digest != record["config_digest"]
            or content_descriptor != record["content_descriptor"]
            or content_identity != record["content_identity"]
        ):
            raise ReleaseArtifactError(
                "sealed image identity differs from its journal record"
            )
        observed = (digest, size)
    elif key == "closure_manifest":
        expected = json.dumps(
            _closure_document(
                operation_id=operation_id,
                release_sha=release_sha,
                release_tree_sha=release_tree_sha,
                artifacts=all_records,
            ),
            sort_keys=True,
            indent=2,
        ).encode("utf-8") + b"\n"
        try:
            observed_payload = read_secure_bytes(
                path,
                label="production shadow release artifact closure",
                owner_uid=owner_uid,
                max_size=MAX_MANIFEST_BYTES,
            )
        except SecureFileError as exc:
            raise ReleaseArtifactError("closure manifest is unreadable") from exc
        if observed_payload != expected:
            raise ReleaseArtifactError("closure manifest content differs")
        observed = (hashlib.sha256(expected).hexdigest(), len(expected))
    else:
        raise ReleaseArtifactError("artifact journal contains an unknown artifact")
    if observed != (record["sha256"], record["bytes"]):
        raise ReleaseArtifactError("sealed artifact differs from its journal record")


def seal_release_artifacts(
    *,
    operation_id: str,
    release_root: Path,
    release_sha: str,
    release_tree_sha: str,
    image_ids: Mapping[str, str],
    checkpoint: Checkpoint | None = None,
    owner_uid: int = 0,
) -> dict[str, Any]:
    """Seal or safely resume one exact operation-scoped artifact closure."""

    if os.geteuid() != owner_uid or owner_uid != 0:
        raise ReleaseArtifactError("artifact producer must run as root")
    (
        operation_id,
        release_root,
        image_ids,
        binding_sha256,
    ) = _validate_identity(
        operation_id=operation_id,
        release_root=release_root,
        release_sha=release_sha,
        release_tree_sha=release_tree_sha,
        image_ids=image_ids,
    )
    callback = checkpoint if checkpoint is not None else (lambda _name: None)
    _verify_release_source(
        release_root,
        release_sha=release_sha,
        release_tree_sha=release_tree_sha,
        owner_uid=owner_uid,
    )
    _ensure_secure_directory(ARTIFACT_ROOT, owner_uid=owner_uid)
    operation_root = ARTIFACT_ROOT / operation_id
    artifacts_root = operation_root / "artifacts"
    _ensure_secure_directory(operation_root, owner_uid=owner_uid)
    _ensure_secure_directory(artifacts_root, owner_uid=owner_uid)
    journal_path = operation_root / "operation-journal.json"
    with _operation_lock(operation_root, owner_uid=owner_uid):
        journal = _load_or_create_journal(
            journal_path,
            operation_id=operation_id,
            binding_sha256=binding_sha256,
            release_sha=release_sha,
            release_tree_sha=release_tree_sha,
        )
        for phase in PHASES:
            if phase in journal["completed_phases"]:
                artifact_key = ARTIFACT_PHASE_KEYS.get(phase)
                if artifact_key is not None:
                    if artifact_key.endswith("_image"):
                        role = artifact_key.removesuffix("_image")
                        if (
                            journal["artifacts"][artifact_key][
                                "source_engine_id"
                            ]
                            != image_ids[role]
                        ):
                            raise ReleaseArtifactError(
                                "journal image ID differs from the operation binding"
                            )
                    _verify_record(
                        artifacts_root,
                        key=artifact_key,
                        record=journal["artifacts"][artifact_key],
                        release_root=release_root,
                        release_sha=release_sha,
                        release_tree_sha=release_tree_sha,
                        operation_id=operation_id,
                        all_records=journal["artifacts"],
                        owner_uid=owner_uid,
                    )
                continue
            _start_phase(journal_path, journal, phase)
            callback(f"after-journal:{phase}")
            if phase == "verify-release":
                _verify_release_source(
                    release_root,
                    release_sha=release_sha,
                    release_tree_sha=release_tree_sha,
                    owner_uid=owner_uid,
                )
                artifact = None
            elif phase == "seal-release-bundle":
                artifact = _seal_bundle(
                    artifacts_root / "release.bundle",
                    release_root=release_root,
                    release_sha=release_sha,
                    release_tree_sha=release_tree_sha,
                    owner_uid=owner_uid,
                    checkpoint=callback,
                )
            elif phase.startswith("seal-") and phase.endswith("-image"):
                role = phase.removeprefix("seal-").removesuffix("-image")
                artifact = _seal_image(
                    artifacts_root / IMAGE_FILENAMES[role],
                    role=role,
                    image_id=image_ids[role],
                    release_sha=release_sha,
                    owner_uid=owner_uid,
                    checkpoint=callback,
                )
            elif phase == "seal-closure":
                artifact = _seal_closure(
                    artifacts_root / "closure-manifest.json",
                    operation_id=operation_id,
                    release_sha=release_sha,
                    release_tree_sha=release_tree_sha,
                    artifacts=journal["artifacts"],
                    owner_uid=owner_uid,
                    checkpoint=callback,
                )
            else:
                raise ReleaseArtifactError("artifact phase is unsupported")
            _complete_phase(
                journal_path,
                journal,
                phase,
                artifact=artifact,
            )
            callback(f"after-complete:{phase}")
        _validate_journal(journal)
        closure = journal["artifacts"]["closure_manifest"]
        return {
            "status": "sealed",
            "schema": CLOSURE_SCHEMA,
            "operation_id": operation_id,
            "release_sha": release_sha,
            "release_tree_sha": release_tree_sha,
            "artifact_root": str(artifacts_root),
            "closure_manifest": str(
                artifacts_root / closure["filename"]
            ),
            "closure_manifest_sha256": closure["sha256"],
            "journal": str(journal_path),
            "completed_phases": list(journal["completed_phases"]),
        }


def _plan(
    *,
    operation_id: str,
    release_root: Path,
    release_sha: str,
    release_tree_sha: str,
    image_ids: Mapping[str, str],
) -> dict[str, Any]:
    operation_id, release_root, image_ids, binding_sha256 = _validate_identity(
        operation_id=operation_id,
        release_root=release_root,
        release_sha=release_sha,
        release_tree_sha=release_tree_sha,
        image_ids=image_ids,
    )
    return {
        "status": "planned",
        "schema": PLAN_SCHEMA,
        "operation_id": operation_id,
        "release_sha": release_sha,
        "release_tree_sha": release_tree_sha,
        "release_root": str(release_root),
        "image_ids": image_ids,
        "binding_sha256": binding_sha256,
        "artifact_root": str(ARTIFACT_ROOT / operation_id / "artifacts"),
        "phases": list(PHASES),
        "required_confirmation": _confirmation(operation_id, release_sha),
        "allowed_commands": [
            "git read-only identity/status/remote",
            "git bundle create/verify/list-heads",
            "docker image inspect by sha256 ID",
            "docker image save by sha256 ID",
        ],
        "forbidden": [
            "network",
            "build",
            "pull",
            "load",
            "tag",
            "container start/stop/create",
            "source backup",
            "role material",
            "secret material",
            "push",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--operation-id", required=True)
    parser.add_argument("--release-root", required=True, type=Path)
    parser.add_argument("--release-sha", required=True)
    parser.add_argument("--release-tree-sha", required=True)
    for role in IMAGE_ROLES:
        parser.add_argument(f"--{role}-image-id", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm")
    args = parser.parse_args(argv)
    image_ids = {
        role: str(getattr(args, f"{role}_image_id"))
        for role in IMAGE_ROLES
    }
    try:
        if not args.apply:
            result = _plan(
                operation_id=args.operation_id,
                release_root=args.release_root,
                release_sha=args.release_sha,
                release_tree_sha=args.release_tree_sha,
                image_ids=image_ids,
            )
        else:
            expected = _confirmation(args.operation_id, args.release_sha)
            if args.confirm != expected:
                raise ReleaseArtifactError(
                    "apply requires the exact operation/release confirmation"
                )
            result = seal_release_artifacts(
                operation_id=args.operation_id,
                release_root=args.release_root,
                release_sha=args.release_sha,
                release_tree_sha=args.release_tree_sha,
                image_ids=image_ids,
            )
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "error": str(exc),
                    "error_class": type(exc).__name__,
                },
                sort_keys=True,
            )
        )
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
