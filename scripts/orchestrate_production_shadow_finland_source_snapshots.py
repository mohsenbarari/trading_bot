#!/usr/bin/env python3
"""Create and collect Finland production live-baseline source snapshots.

The controller defaults to a read-only plan.  Apply mode invokes this exact
release file as a bounded host agent on Bot-FI and WebApp-FI.  The host agent
installs one canonical operation binding create-only, runs the exact-release
source snapshot producer in live-baseline mode, and returns only a redacted
artifact inventory.  Remote payload transport is restricted to pinned
SSH/SCP; no object storage, shell interpolation, pull, or build is used.
"""

from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import sys
from typing import Any, BinaryIO, Callable, Iterator, Mapping


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import produce_production_shadow_source_snapshot as SOURCE  # noqa: E402
from scripts import production_shadow_finland_stage as FINLAND_STAGE  # noqa: E402


PLAN_SCHEMA = "production-shadow-finland-source-snapshot-plan-v1"
RESULT_SCHEMA = "production-shadow-finland-source-snapshot-orchestrator-v1"
HOST_REQUEST_SCHEMA = "production-shadow-finland-source-snapshot-host-request-v1"
HOST_RESULT_SCHEMA = "production-shadow-finland-source-snapshot-host-result-v1"
HOST_PREPARE_SCHEMA = (
    "production-shadow-finland-source-snapshot-host-binding-prepare-v1"
)
JOURNAL_SCHEMA = "production-shadow-finland-source-snapshot-journal-v1"

BOT_FI_HOST = "65.109.216.187"
WEBAPP_FI_HOST = "65.109.220.59"
WEBAPP_FI_USER = "root"
WEBAPP_FI_PORT = 37067
ROLES = ("bot_fi", "webapp_fi")
ROLE_PATHS = {"bot_fi": "bot-fi", "webapp_fi": "webapp-fi"}
ROLE_HOSTS = {"bot_fi": BOT_FI_HOST, "webapp_fi": WEBAPP_FI_HOST}
ROLE_TRANSPORTS = {
    "bot_fi": "local-controller",
    "webapp_fi": "trusted-ssh-scp",
}

PYTHON = "/usr/bin/python3"
GIT = "/usr/bin/git"
SSH = "/usr/bin/ssh"
SCP = "/usr/bin/scp"
KNOWN_HOSTS = Path("/root/.ssh/known_hosts")
DEFAULT_SSH_IDENTITY = Path("/root/.ssh/id_ed25519")
PROJECT_ROOT_PREFIX = Path("/srv/trading-bot-three-site-production-shadow")
SECRET_ROOT_PREFIX = Path(
    "/root/secure-envs/trading-bot/three-site-production-shadow"
)
SOURCE_OUTPUT_ROOT = Path(
    "/srv/trading-bot-three-site-production-shadow-source-snapshots"
)
PRODUCER_RELATIVE = Path("scripts/produce_production_shadow_source_snapshot.py")
AGENT_RELATIVE = Path(
    "scripts/orchestrate_production_shadow_finland_source_snapshots.py"
)
BINDING_FILENAME = "source-snapshot-binding-live-baseline.json"
JOURNAL_FILENAME = "finland-live-baseline-source-snapshot-journal.json"
LOCK_FILENAME = "finland-live-baseline-source-snapshot-controller.lock"
COLLECTION_DIRECTORY = Path("source-snapshots/live-baseline")
CONFIRMATION_PREFIX = "COLLECT-PRODUCTION-SHADOW-FINLAND-LIVE-BASELINE-SNAPSHOTS"

SNAPSHOT_FILENAMES = (
    SOURCE.MANIFEST_FILE,
    SOURCE.ARTIFACT_FILES["database-backup"],
    SOURCE.ARTIFACT_FILES["uploads-archive"],
    SOURCE.ARTIFACT_FILES["audit-archive"],
)
MAX_JSON_BYTES = 2 * 1024 * 1024
MAX_ARTIFACT_BYTES = SOURCE.MAX_ARTIFACT_BYTES
MAX_COMMAND_OUTPUT_BYTES = 8 * 1024 * 1024
MAX_JOURNAL_BYTES = 8 * 1024 * 1024
ZERO_SHA256 = "0" * 64
SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
REMOTE_TOKEN_RE = re.compile(r"^[A-Za-z0-9_./:=+-]+$")
JOURNAL_TEMP_RE_TEMPLATE = r"^\.{name}\.[1-9][0-9]*\.[0-9a-f]{{16}}\.tmp$"

HOST_REQUEST_FIELDS = frozenset(
    {
        "schema",
        "action",
        "operation_id",
        "release_sha",
        "role",
        "binding_path",
        "binding_sha256",
        "output_root",
        "pull_policy",
    }
)
HOST_PREPARE_FIELDS = frozenset(
    {
        "schema",
        "status",
        "operation_id",
        "release_sha",
        "role",
        "binding_sha256",
        "need_transfer",
        "partial_reconciled",
        "docker_contacted",
        "production_mutated",
    }
)
HOST_RESULT_FIELDS = frozenset(
    {
        "schema",
        "status",
        "snapshot_status",
        "operation_id",
        "release_sha",
        "role",
        "mode",
        "binding_sha256",
        "files",
        "zero_residue",
        "pull_policy",
        "scratch_network_mode",
        "source_mutated",
        "current_mutated",
        "source_stopped_or_restarted",
        "redis_restored",
    }
)
FILE_RESULT_FIELDS = frozenset({"sha256", "bytes"})
JOURNAL_FIELDS = frozenset(
    {
        "schema",
        "operation_id",
        "release_sha",
        "bindings",
        "status",
        "completed_roles",
        "current_role",
        "role_results",
        "state_sha256",
    }
)
JOURNAL_ROLE_FIELDS = frozenset({"host_result", "collection"})

SAFE_ENV = {
    "PATH": "/usr/bin:/bin",
    "HOME": "/root",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "DOCKER_CONFIG": "/nonexistent",
    "PYTHONDONTWRITEBYTECODE": "1",
}


class FinlandSourceSnapshotOrchestratorError(RuntimeError):
    """The bounded Finland source snapshot orchestration failed closed."""


Runner = Callable[..., subprocess.CompletedProcess[bytes]]
Checkpoint = Callable[[str], None]


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
        raise FinlandSourceSnapshotOrchestratorError(
            "value is not canonical JSON"
        ) from exc


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
            parse_constant=lambda token: (_ for _ in ()).throw(
                ValueError(f"invalid constant: {token}")
            ),
        )
    except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise FinlandSourceSnapshotOrchestratorError(
            f"{label} is not strict JSON"
        ) from exc
    if not isinstance(value, dict):
        raise FinlandSourceSnapshotOrchestratorError(
            f"{label} must contain one JSON object"
        )
    return value


def _canonical_uuid4(value: Any) -> str:
    try:
        return FINLAND_STAGE._canonical_uuid4(value, label="operation_id")
    except FINLAND_STAGE.FinlandStageError as exc:
        raise FinlandSourceSnapshotOrchestratorError(
            "operation id is invalid"
        ) from exc


def _release_sha(value: Any) -> str:
    if (
        not isinstance(value, str)
        or SHA40_RE.fullmatch(value) is None
        or value == "0" * 40
    ):
        raise FinlandSourceSnapshotOrchestratorError(
            "release SHA is invalid"
        )
    return value


def _nonzero_sha256(value: Any, *, label: str) -> str:
    if (
        not isinstance(value, str)
        or SHA256_RE.fullmatch(value) is None
        or value == ZERO_SHA256
    ):
        raise FinlandSourceSnapshotOrchestratorError(
            f"{label} is not a nonzero SHA-256"
        )
    return value


def _bounded_bytes(value: Any, *, label: str, maximum: int) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 1 <= value <= maximum
    ):
        raise FinlandSourceSnapshotOrchestratorError(
            f"{label} is outside its size bound"
        )
    return value


def confirmation_phrase(operation_id: str, release_sha: str) -> str:
    _canonical_uuid4(operation_id)
    _release_sha(release_sha)
    return f"{CONFIRMATION_PREFIX}:{operation_id}:{release_sha}"


def canonical_paths(operation_id: str, release_sha: str) -> dict[str, Any]:
    _canonical_uuid4(operation_id)
    _release_sha(release_sha)
    project_root = PROJECT_ROOT_PREFIX / operation_id
    release_root = project_root / "releases" / release_sha
    operation_secret = SECRET_ROOT_PREFIX / operation_id
    controller_root = operation_secret / "controller"
    collection_root = controller_root / COLLECTION_DIRECTORY
    roles: dict[str, dict[str, Path]] = {}
    for role in ROLES:
        secret_role = operation_secret / ROLE_PATHS[role]
        binding = secret_role / BINDING_FILENAME
        snapshot = (
            SOURCE_OUTPUT_ROOT
            / operation_id
            / role
            / "live-baseline"
        )
        roles[role] = {
            "secret_root": secret_role,
            "binding": binding,
            "binding_transfer": binding.with_name(
                f".{binding.name}.transfer"
            ),
            "snapshot": snapshot,
            "manifest": snapshot / SOURCE.MANIFEST_FILE,
            "collection": collection_root / role,
        }
    return {
        "project_root": project_root,
        "release_root": release_root,
        "agent": release_root / AGENT_RELATIVE,
        "producer": release_root / PRODUCER_RELATIVE,
        "operation_secret": operation_secret,
        "controller_root": controller_root,
        "collection_root": collection_root,
        "journal": controller_root / JOURNAL_FILENAME,
        "lock": controller_root / LOCK_FILENAME,
        "roles": roles,
    }


def _assert_absolute_safe_path(path: Path, *, label: str) -> None:
    if (
        not path.is_absolute()
        or ".." in path.parts
        or "\x00" in str(path)
        or "\n" in str(path)
        or "\r" in str(path)
    ):
        raise FinlandSourceSnapshotOrchestratorError(
            f"{label} path is invalid"
        )


def _assert_directory(
    path: Path,
    *,
    label: str,
    required_uid: int,
    private: bool,
) -> None:
    _assert_absolute_safe_path(path, label=label)
    try:
        metadata = path.stat(follow_symlinks=False)
    except OSError as exc:
        raise FinlandSourceSnapshotOrchestratorError(
            f"{label} directory is unavailable"
        ) from exc
    mode = stat.S_IMODE(metadata.st_mode)
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != required_uid
        or (private and mode != 0o700)
        or (not private and mode & 0o022)
    ):
        raise FinlandSourceSnapshotOrchestratorError(
            f"{label} directory ownership or mode is unsafe"
        )


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
        raise FinlandSourceSnapshotOrchestratorError(
            "directory synchronization failed"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _ensure_private_child(
    path: Path,
    *,
    parent: Path,
    label: str,
    required_uid: int,
) -> str:
    _assert_directory(
        parent,
        label=f"{label} parent",
        required_uid=required_uid,
        private=True,
    )
    if path.parent != parent:
        raise FinlandSourceSnapshotOrchestratorError(
            f"{label} path is not canonical"
        )
    try:
        os.lstat(path)
    except FileNotFoundError:
        try:
            os.mkdir(path, 0o700)
            _fsync_directory(parent)
        except FileExistsError:
            pass
        except OSError as exc:
            raise FinlandSourceSnapshotOrchestratorError(
                f"{label} directory could not be created"
            ) from exc
        publication = "created"
    except OSError as exc:
        raise FinlandSourceSnapshotOrchestratorError(
            f"{label} directory cannot be inspected"
        ) from exc
    else:
        publication = "reused"
    _assert_directory(
        path,
        label=label,
        required_uid=required_uid,
        private=True,
    )
    return publication


def _ensure_host_output_root(*, required_uid: int) -> str:
    path = SOURCE_OUTPUT_ROOT
    parent = path.parent
    _assert_directory(
        parent,
        label="source snapshot output parent",
        required_uid=required_uid,
        private=False,
    )
    try:
        os.lstat(path)
    except FileNotFoundError:
        try:
            os.mkdir(path, 0o700)
            _fsync_directory(parent)
        except FileExistsError:
            pass
        except OSError as exc:
            raise FinlandSourceSnapshotOrchestratorError(
                "source snapshot output root could not be created"
            ) from exc
        publication = "created"
    except OSError as exc:
        raise FinlandSourceSnapshotOrchestratorError(
            "source snapshot output root cannot be inspected"
        ) from exc
    else:
        publication = "reused"
    _assert_directory(
        path,
        label="source snapshot output root",
        required_uid=required_uid,
        private=True,
    )
    return publication


@contextmanager
def _held_file(
    path: Path,
    *,
    label: str,
    required_uid: int,
    expected_mode: int,
    maximum: int,
    allow_two_links: bool = False,
) -> Iterator[tuple[BinaryIO, os.stat_result]]:
    _assert_absolute_safe_path(path, label=label)
    descriptor = -1
    stream: BinaryIO | None = None
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
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
            raise FinlandSourceSnapshotOrchestratorError(
                f"{label} ownership, mode, or size is unsafe"
            )
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
            "st_ctime_ns",
        )
        if any(
            getattr(before, field) != getattr(after, field)
            for field in stable
        ):
            raise FinlandSourceSnapshotOrchestratorError(
                f"{label} changed while being read"
            )
    except FinlandSourceSnapshotOrchestratorError:
        raise
    except OSError as exc:
        raise FinlandSourceSnapshotOrchestratorError(
            f"{label} is unavailable"
        ) from exc
    finally:
        if stream is not None:
            stream.close()
        elif descriptor >= 0:
            os.close(descriptor)


def _hash_file(
    path: Path,
    *,
    label: str,
    required_uid: int,
    expected_mode: int,
    maximum: int,
    allow_two_links: bool = False,
) -> tuple[str, int]:
    with _held_file(
        path,
        label=label,
        required_uid=required_uid,
        expected_mode=expected_mode,
        maximum=maximum,
        allow_two_links=allow_two_links,
    ) as (stream, _metadata):
        digest = hashlib.sha256()
        size = 0
        while chunk := stream.read(1024 * 1024):
            size += len(chunk)
            if size > maximum:
                raise FinlandSourceSnapshotOrchestratorError(
                    f"{label} exceeded its size bound"
                )
            digest.update(chunk)
    return digest.hexdigest(), size


def _read_file(
    path: Path,
    *,
    label: str,
    required_uid: int,
    expected_mode: int,
    maximum: int,
) -> bytes:
    with _held_file(
        path,
        label=label,
        required_uid=required_uid,
        expected_mode=expected_mode,
        maximum=maximum,
    ) as (stream, _metadata):
        payload = stream.read(maximum + 1)
    if not 1 <= len(payload) <= maximum:
        raise FinlandSourceSnapshotOrchestratorError(
            f"{label} is empty or oversized"
        )
    return payload


def _binding(
    path: Path,
    *,
    operation_id: str,
    release_sha: str,
    role: str,
) -> SOURCE.SnapshotBinding:
    try:
        value = SOURCE.load_binding(path)
    except SOURCE.SourceSnapshotError as exc:
        raise FinlandSourceSnapshotOrchestratorError(
            f"{role} source snapshot binding is invalid"
        ) from exc
    if (
        value.operation_id != operation_id
        or value.release_sha != release_sha
        or value.role != role
        or value.mode != "live-baseline"
    ):
        raise FinlandSourceSnapshotOrchestratorError(
            f"{role} source snapshot binding identity differs"
        )
    return value


def load_bindings(
    *,
    operation_id: str,
    release_sha: str,
    bot_fi_binding: Path,
    webapp_fi_binding: Path,
) -> dict[str, SOURCE.SnapshotBinding]:
    result = {
        "bot_fi": _binding(
            bot_fi_binding,
            operation_id=operation_id,
            release_sha=release_sha,
            role="bot_fi",
        ),
        "webapp_fi": _binding(
            webapp_fi_binding,
            operation_id=operation_id,
            release_sha=release_sha,
            role="webapp_fi",
        ),
    }
    first = result["bot_fi"]
    second = result["webapp_fi"]
    for field in (
        "legacy_release_sha",
        "controller_manifest_sha256",
        "approval_sha256",
    ):
        if getattr(first, field) != getattr(second, field):
            raise FinlandSourceSnapshotOrchestratorError(
                "Finland source bindings do not share one controller closure"
            )
    return result


def encode_host_request(document: Mapping[str, Any]) -> str:
    raw = canonical_json(document)
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_host_request(encoded: str) -> dict[str, Any]:
    if (
        not isinstance(encoded, str)
        or not 1 <= len(encoded) <= 64 * 1024
        or re.fullmatch(r"[A-Za-z0-9_-]+", encoded) is None
    ):
        raise FinlandSourceSnapshotOrchestratorError(
            "host request encoding is invalid"
        )
    try:
        raw = base64.b64decode(
            encoded + "=" * (-len(encoded) % 4),
            altchars=b"-_",
            validate=True,
        )
    except ValueError as exc:
        raise FinlandSourceSnapshotOrchestratorError(
            "host request encoding is invalid"
        ) from exc
    document = _strict_json(raw, label="host request")
    if raw != canonical_json(document) or set(document) != HOST_REQUEST_FIELDS:
        raise FinlandSourceSnapshotOrchestratorError(
            "host request is not exact canonical JSON"
        )
    if (
        document["schema"] != HOST_REQUEST_SCHEMA
        or document["action"] not in {"prepare-binding", "snapshot"}
        or document["role"] not in ROLES
        or document["pull_policy"] != "never"
        or document["output_root"] != str(SOURCE_OUTPUT_ROOT)
    ):
        raise FinlandSourceSnapshotOrchestratorError(
            "host request contract is invalid"
        )
    operation_id = _canonical_uuid4(document["operation_id"])
    release_sha = _release_sha(document["release_sha"])
    _nonzero_sha256(
        document["binding_sha256"],
        label="binding SHA-256",
    )
    paths = canonical_paths(operation_id, release_sha)
    if document["binding_path"] != str(
        paths["roles"][document["role"]]["binding"]
    ):
        raise FinlandSourceSnapshotOrchestratorError(
            "host request binding path is not canonical"
        )
    return document


def build_host_request(
    *,
    action: str,
    operation_id: str,
    release_sha: str,
    role: str,
    binding_sha256: str,
) -> dict[str, Any]:
    paths = canonical_paths(operation_id, release_sha)
    document = {
        "schema": HOST_REQUEST_SCHEMA,
        "action": action,
        "operation_id": operation_id,
        "release_sha": release_sha,
        "role": role,
        "binding_path": str(paths["roles"][role]["binding"]),
        "binding_sha256": binding_sha256,
        "output_root": str(SOURCE_OUTPUT_ROOT),
        "pull_policy": "never",
    }
    decode_host_request(encode_host_request(document))
    return document


def _safe_remote_token(value: str) -> None:
    if (
        not value
        or REMOTE_TOKEN_RE.fullmatch(value) is None
        or any(character in value for character in ("$", "`", ";", "&", "|", "<", ">"))
    ):
        raise FinlandSourceSnapshotOrchestratorError(
            "remote command token is unsafe"
        )


def _remote_command(arguments: list[str]) -> str:
    if not arguments:
        raise FinlandSourceSnapshotOrchestratorError(
            "remote command argv is empty"
        )
    for value in arguments:
        _safe_remote_token(value)
    command = " ".join(arguments)
    if any(character in command for character in ("\n", "\r", "\x00")):
        raise FinlandSourceSnapshotOrchestratorError(
            "remote command contains a line break"
        )
    return command


def _ssh_options(ssh_identity: Path) -> list[str]:
    _assert_absolute_safe_path(ssh_identity, label="SSH identity")
    return [
        "-p",
        str(WEBAPP_FI_PORT),
        "-i",
        str(ssh_identity),
        "-o",
        "BatchMode=yes",
        "-o",
        "ClearAllForwardings=yes",
        "-o",
        "ExitOnForwardFailure=yes",
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "PermitLocalCommand=no",
        "-o",
        "RequestTTY=no",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        f"UserKnownHostsFile={KNOWN_HOSTS}",
        "-o",
        "LogLevel=ERROR",
    ]


def ssh_arguments(
    ssh_identity: Path,
    *,
    remote_arguments: list[str],
) -> list[str]:
    return [
        SSH,
        "-T",
        *_ssh_options(ssh_identity),
        f"{WEBAPP_FI_USER}@{WEBAPP_FI_HOST}",
        _remote_command(remote_arguments),
    ]


def scp_upload_arguments(
    ssh_identity: Path,
    *,
    source: Path,
    remote_destination: Path,
) -> list[str]:
    _assert_absolute_safe_path(source, label="SCP upload source")
    _assert_absolute_safe_path(
        remote_destination,
        label="SCP upload destination",
    )
    if (
        ":" in str(source)
        or remote_destination.parent.parent.parent != SECRET_ROOT_PREFIX
        or _canonical_uuid4(remote_destination.parent.parent.name)
        != remote_destination.parent.parent.name
        or remote_destination.parent
        != (
            SECRET_ROOT_PREFIX
            / remote_destination.parent.parent.name
            / ROLE_PATHS["webapp_fi"]
        )
        or remote_destination.name != f".{BINDING_FILENAME}.transfer"
    ):
        raise FinlandSourceSnapshotOrchestratorError(
            "SCP upload destination is not canonical"
        )
    return [
        SCP,
        "-q",
        "-p",
        "-P",
        str(WEBAPP_FI_PORT),
        "-i",
        str(ssh_identity),
        "-o",
        "BatchMode=yes",
        "-o",
        "ClearAllForwardings=yes",
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        f"UserKnownHostsFile={KNOWN_HOSTS}",
        "-o",
        "LogLevel=ERROR",
        "--",
        str(source),
        f"{WEBAPP_FI_USER}@{WEBAPP_FI_HOST}:{remote_destination}",
    ]


def scp_download_arguments(
    ssh_identity: Path,
    *,
    remote_source: Path,
    destination: Path,
) -> list[str]:
    _assert_absolute_safe_path(remote_source, label="SCP download source")
    _assert_absolute_safe_path(destination, label="SCP download destination")
    try:
        remote_relative = remote_source.relative_to(SOURCE_OUTPUT_ROOT)
        destination_relative = destination.relative_to(SECRET_ROOT_PREFIX)
    except ValueError as exc:
        raise FinlandSourceSnapshotOrchestratorError(
            "SCP download path is not operation-derived"
        ) from exc
    remote_parts = remote_relative.parts
    destination_parts = destination_relative.parts
    if (
        len(remote_parts) != 4
        or len(destination_parts) != 6
        or _canonical_uuid4(remote_parts[0]) != remote_parts[0]
        or destination_parts[0] != remote_parts[0]
        or remote_parts[1:] != (
            "webapp_fi",
            "live-baseline",
            remote_source.name,
        )
        or remote_source.name not in SNAPSHOT_FILENAMES
        or destination_parts[1:5]
        != (
            "controller",
            "source-snapshots",
            "live-baseline",
            "webapp_fi",
        )
        or destination_parts[5] != f".{remote_source.name}.transfer"
    ):
        raise FinlandSourceSnapshotOrchestratorError(
            "SCP download path is not operation-derived"
        )
    return [
        SCP,
        "-q",
        "-p",
        "-P",
        str(WEBAPP_FI_PORT),
        "-i",
        str(ssh_identity),
        "-o",
        "BatchMode=yes",
        "-o",
        "ClearAllForwardings=yes",
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        f"UserKnownHostsFile={KNOWN_HOSTS}",
        "-o",
        "LogLevel=ERROR",
        "--",
        f"{WEBAPP_FI_USER}@{WEBAPP_FI_HOST}:{remote_source}",
        str(destination),
    ]


def _run_command(
    arguments: list[str],
    *,
    runner: Runner,
    timeout: int,
    allowed: frozenset[str],
) -> bytes:
    if (
        not arguments
        or arguments[0] not in allowed
        or any(not isinstance(value, str) or not value for value in arguments)
    ):
        raise FinlandSourceSnapshotOrchestratorError(
            "controller command argv is outside its allowlist"
        )
    try:
        completed = runner(
            arguments,
            input=None,
            capture_output=True,
            check=False,
            timeout=timeout,
            env=SAFE_ENV,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise FinlandSourceSnapshotOrchestratorError(
            f"command is unavailable: {Path(arguments[0]).name}"
        ) from exc
    if (
        len(completed.stdout) > MAX_COMMAND_OUTPUT_BYTES
        or len(completed.stderr) > MAX_COMMAND_OUTPUT_BYTES
    ):
        raise FinlandSourceSnapshotOrchestratorError(
            "command output exceeded its bound"
        )
    if completed.returncode != 0:
        raise FinlandSourceSnapshotOrchestratorError(
            f"command failed closed: {Path(arguments[0]).name}"
        )
    return completed.stdout


def _parse_command_json(raw: bytes, *, label: str) -> dict[str, Any]:
    if not 1 <= len(raw) <= MAX_JSON_BYTES + 1:
        raise FinlandSourceSnapshotOrchestratorError(
            f"{label} output is empty or oversized"
        )
    stripped = raw.strip()
    document = _strict_json(stripped, label=label)
    if stripped != canonical_json(document):
        raise FinlandSourceSnapshotOrchestratorError(
            f"{label} output is not canonical JSON"
        )
    return document


def _assert_ssh_material(
    ssh_identity: Path,
    *,
    required_uid: int,
) -> None:
    for path, mode, label in (
        (ssh_identity, 0o600, "SSH identity"),
        (KNOWN_HOSTS, 0o600, "known-hosts"),
    ):
        _hash_file(
            path,
            label=label,
            required_uid=required_uid,
            expected_mode=mode,
            maximum=MAX_JSON_BYTES,
        )


def _validate_exact_release(
    release_root: Path,
    release_sha: str,
    *,
    runner: Runner,
    required_uid: int,
    agent_path: Path,
) -> None:
    expected_agent = release_root / AGENT_RELATIVE
    expected_producer = release_root / PRODUCER_RELATIVE
    if agent_path != expected_agent:
        raise FinlandSourceSnapshotOrchestratorError(
            "host agent is not running from the exact operation release"
        )
    _assert_directory(
        release_root,
        label="operation release root",
        required_uid=required_uid,
        private=True,
    )
    for path, label in (
        (expected_agent, "source snapshot host agent"),
        (expected_producer, "source snapshot producer"),
    ):
        try:
            metadata = path.stat(follow_symlinks=False)
        except OSError as exc:
            raise FinlandSourceSnapshotOrchestratorError(
                f"{label} is unavailable"
            ) from exc
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != required_uid
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) & 0o022
        ):
            raise FinlandSourceSnapshotOrchestratorError(
                f"{label} ownership or mode is unsafe"
            )
    commands = (
        ([GIT, "-C", str(release_root), "rev-parse", "--show-toplevel"], str(release_root)),
        ([GIT, "-C", str(release_root), "rev-parse", "HEAD"], release_sha),
        ([GIT, "-C", str(release_root), "status", "--porcelain=v1", "--untracked-files=all"], ""),
    )
    for arguments, expected in commands:
        observed = _run_command(
            arguments,
            runner=runner,
            timeout=60,
            allowed=frozenset({GIT}),
        ).decode("ascii", errors="strict").strip()
        if observed != expected:
            raise FinlandSourceSnapshotOrchestratorError(
                "operation release is not exact, detached, and clean"
            )
    try:
        detached = runner(
            [GIT, "-C", str(release_root), "symbolic-ref", "-q", "HEAD"],
            input=None,
            capture_output=True,
            check=False,
            timeout=60,
            env=SAFE_ENV,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise FinlandSourceSnapshotOrchestratorError(
            "operation release detached-state check failed"
        ) from exc
    if (
        detached.returncode != 1
        or detached.stdout
        or len(detached.stderr) > MAX_COMMAND_OUTPUT_BYTES
    ):
        raise FinlandSourceSnapshotOrchestratorError(
            "operation release is not detached"
        )


def _binding_file_state(
    path: Path,
    *,
    expected_sha256: str,
    required_uid: int,
    label: str,
    allow_two_links: bool = False,
) -> tuple[bool, bool]:
    try:
        os.lstat(path)
    except FileNotFoundError:
        return False, False
    except OSError as exc:
        raise FinlandSourceSnapshotOrchestratorError(
            f"{label} cannot be inspected"
        ) from exc
    try:
        observed = _hash_file(
            path,
            label=label,
            required_uid=required_uid,
            expected_mode=0o600,
            maximum=MAX_JSON_BYTES,
            allow_two_links=allow_two_links,
        )
    except FinlandSourceSnapshotOrchestratorError as exc:
        raise FinlandSourceSnapshotOrchestratorError(
            f"{label} is foreign or unsafe"
        ) from exc
    return True, observed[0] == expected_sha256


def _prepare_host_binding(
    request: Mapping[str, Any],
    *,
    required_uid: int,
) -> dict[str, Any]:
    paths = canonical_paths(
        str(request["operation_id"]),
        str(request["release_sha"]),
    )
    role = str(request["role"])
    binding = paths["roles"][role]["binding"]
    transfer = paths["roles"][role]["binding_transfer"]
    _assert_directory(
        paths["roles"][role]["secret_root"],
        label="host role secret root",
        required_uid=required_uid,
        private=True,
    )
    final_exists, final_matches = _binding_file_state(
        binding,
        expected_sha256=str(request["binding_sha256"]),
        required_uid=required_uid,
        label="host source binding",
        allow_two_links=True,
    )
    if final_exists and not final_matches:
        raise FinlandSourceSnapshotOrchestratorError(
            "existing host source binding differs"
        )
    transfer_exists, transfer_matches = _binding_file_state(
        transfer,
        expected_sha256=str(request["binding_sha256"]),
        required_uid=required_uid,
        label="host source binding transfer",
        allow_two_links=True,
    )
    partial_reconciled = False
    if final_exists and transfer_exists:
        try:
            final_metadata = binding.stat(follow_symlinks=False)
            transfer_metadata = transfer.stat(follow_symlinks=False)
        except OSError as exc:
            raise FinlandSourceSnapshotOrchestratorError(
                "host binding crash reconciliation is ambiguous"
            ) from exc
        if (
            final_metadata.st_nlink == 2
            and (
                final_metadata.st_dev != transfer_metadata.st_dev
                or final_metadata.st_ino != transfer_metadata.st_ino
            )
        ):
            raise FinlandSourceSnapshotOrchestratorError(
                "host binding crash link identity differs"
            )
        try:
            transfer.unlink()
            _fsync_directory(transfer.parent)
        except OSError as exc:
            raise FinlandSourceSnapshotOrchestratorError(
                "completed host binding transfer could not be reconciled"
            ) from exc
        transfer_exists = False
        transfer_matches = False
        partial_reconciled = True
        final_exists, final_matches = _binding_file_state(
            binding,
            expected_sha256=str(request["binding_sha256"]),
            required_uid=required_uid,
            label="host source binding",
        )
    elif final_exists:
        try:
            final_metadata = binding.stat(follow_symlinks=False)
        except OSError as exc:
            raise FinlandSourceSnapshotOrchestratorError(
                "host source binding link state is unavailable"
            ) from exc
        if final_metadata.st_nlink != 1:
            raise FinlandSourceSnapshotOrchestratorError(
                "host source binding has a foreign hard link"
            )
    elif transfer_exists and not transfer_matches:
        try:
            metadata = transfer.stat(follow_symlinks=False)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != required_uid
                or stat.S_IMODE(metadata.st_mode) != 0o600
                or metadata.st_nlink != 1
            ):
                raise FinlandSourceSnapshotOrchestratorError(
                    "partial host binding transfer is foreign"
                )
            transfer.unlink()
            _fsync_directory(transfer.parent)
        except FinlandSourceSnapshotOrchestratorError:
            raise
        except OSError as exc:
            raise FinlandSourceSnapshotOrchestratorError(
                "partial host binding transfer could not be reconciled"
            ) from exc
        transfer_exists = False
        partial_reconciled = True
    return {
        "schema": HOST_PREPARE_SCHEMA,
        "status": "prepared",
        "operation_id": request["operation_id"],
        "release_sha": request["release_sha"],
        "role": role,
        "binding_sha256": request["binding_sha256"],
        "need_transfer": not final_exists and not transfer_exists,
        "partial_reconciled": partial_reconciled,
        "docker_contacted": False,
        "production_mutated": False,
    }


def _promote_host_binding(
    request: Mapping[str, Any],
    *,
    required_uid: int,
) -> SOURCE.SnapshotBinding:
    paths = canonical_paths(
        str(request["operation_id"]),
        str(request["release_sha"]),
    )
    role = str(request["role"])
    binding = paths["roles"][role]["binding"]
    transfer = paths["roles"][role]["binding_transfer"]
    prepared = _prepare_host_binding(request, required_uid=required_uid)
    if prepared["need_transfer"]:
        raise FinlandSourceSnapshotOrchestratorError(
            "host source binding transfer is absent"
        )
    final_exists, final_matches = _binding_file_state(
        binding,
        expected_sha256=str(request["binding_sha256"]),
        required_uid=required_uid,
        label="host source binding",
    )
    if not final_exists:
        try:
            os.link(transfer, binding, follow_symlinks=False)
            _fsync_directory(binding.parent)
        except FileExistsError:
            pass
        except OSError as exc:
            raise FinlandSourceSnapshotOrchestratorError(
                "host source binding could not be published create-only"
            ) from exc
        final_exists, final_matches = _binding_file_state(
            binding,
            expected_sha256=str(request["binding_sha256"]),
            required_uid=required_uid,
            label="host source binding",
            allow_two_links=True,
        )
        if not final_exists or not final_matches:
            raise FinlandSourceSnapshotOrchestratorError(
                "published host source binding differs"
            )
        try:
            transfer.unlink()
            _fsync_directory(binding.parent)
        except OSError as exc:
            raise FinlandSourceSnapshotOrchestratorError(
                "host source binding transfer cleanup failed"
            ) from exc
    elif not final_matches:
        raise FinlandSourceSnapshotOrchestratorError(
            "existing host source binding differs"
        )
    return _binding(
        binding,
        operation_id=str(request["operation_id"]),
        release_sha=str(request["release_sha"]),
        role=role,
    )


def _snapshot_file_inventory(
    paths: SOURCE.OutputPaths,
    manifest: Mapping[str, Any],
    *,
    required_uid: int,
) -> dict[str, dict[str, Any]]:
    expected: dict[str, tuple[str, int]] = {
        SOURCE.MANIFEST_FILE: (
            hashlib.sha256(canonical_json(manifest)).hexdigest(),
            len(canonical_json(manifest)),
        )
    }
    for kind, filename in SOURCE.ARTIFACT_FILES.items():
        row = manifest["artifacts"][kind]
        expected[filename] = (
            _nonzero_sha256(row["sha256"], label=f"{kind} SHA-256"),
            _bounded_bytes(
                row["bytes"],
                label=f"{kind} bytes",
                maximum=MAX_ARTIFACT_BYTES,
            ),
        )
    result: dict[str, dict[str, Any]] = {}
    for filename in SNAPSHOT_FILENAMES:
        maximum = (
            MAX_JSON_BYTES
            if filename == SOURCE.MANIFEST_FILE
            else MAX_ARTIFACT_BYTES
        )
        observed = _hash_file(
            paths.final / filename,
            label=f"snapshot {filename}",
            required_uid=required_uid,
            expected_mode=0o600,
            maximum=maximum,
        )
        if observed != expected[filename]:
            raise FinlandSourceSnapshotOrchestratorError(
                f"snapshot {filename} differs after producer verification"
            )
        result[filename] = {
            "sha256": observed[0],
            "bytes": observed[1],
        }
    return result


def host_agent(
    encoded_request: str,
    *,
    runner: Runner = subprocess.run,
    required_uid: int = 0,
    observed_host_addresses: set[str] | None = None,
    agent_path: Path | None = None,
) -> dict[str, Any]:
    if os.geteuid() != required_uid or required_uid != 0:
        raise FinlandSourceSnapshotOrchestratorError(
            "source snapshot host agent must run as root"
        )
    request = decode_host_request(encoded_request)
    role = str(request["role"])
    try:
        FINLAND_STAGE._verify_role_host(
            role,
            observed_host_addresses=observed_host_addresses,
        )
    except FINLAND_STAGE.FinlandStageError as exc:
        raise FinlandSourceSnapshotOrchestratorError(
            "host identity differs from the bound Finland role"
        ) from exc
    paths = canonical_paths(
        str(request["operation_id"]),
        str(request["release_sha"]),
    )
    actual_agent = (
        Path(__file__).resolve()
        if agent_path is None
        else agent_path
    )
    _validate_exact_release(
        paths["release_root"],
        str(request["release_sha"]),
        runner=runner,
        required_uid=required_uid,
        agent_path=actual_agent,
    )
    if request["action"] == "prepare-binding":
        return _prepare_host_binding(request, required_uid=required_uid)

    binding = _promote_host_binding(request, required_uid=required_uid)
    _ensure_host_output_root(required_uid=required_uid)
    producer_arguments = [
        PYTHON,
        "-B",
        str(paths["producer"]),
        "--binding",
        str(paths["roles"][role]["binding"]),
        "--output-root",
        str(SOURCE_OUTPUT_ROOT),
        "--apply",
        "--confirm",
        SOURCE.confirmation_phrase(binding),
    ]
    raw = _run_command(
        producer_arguments,
        runner=runner,
        timeout=6 * 60 * 60,
        allowed=frozenset({PYTHON}),
    )
    producer_result = _parse_command_json(
        raw,
        label="source snapshot producer",
    )
    if (
        producer_result.get("schema") != SOURCE.MANIFEST_SCHEMA
        or producer_result.get("status") not in {"applied", "resume-verified"}
        or producer_result.get("operation_id") != binding.operation_id
        or producer_result.get("role") != role
        or producer_result.get("mode") != "live-baseline"
        or producer_result.get("manifest")
        != str(paths["roles"][role]["manifest"])
        or producer_result.get("zero_residue") is not True
    ):
        raise FinlandSourceSnapshotOrchestratorError(
            "source snapshot producer result differs"
        )
    source_paths = SOURCE.output_paths(SOURCE_OUTPUT_ROOT, binding)
    try:
        manifest = SOURCE.verify_completed_output(
            source_paths,
            binding,
            freeze_sha256=None,
        )
    except SOURCE.SourceSnapshotError as exc:
        raise FinlandSourceSnapshotOrchestratorError(
            "source snapshot producer output is invalid"
        ) from exc
    files = _snapshot_file_inventory(
        source_paths,
        manifest,
        required_uid=required_uid,
    )
    return {
        "schema": HOST_RESULT_SCHEMA,
        "status": "snapshotted",
        "snapshot_status": producer_result["status"],
        "operation_id": binding.operation_id,
        "release_sha": binding.release_sha,
        "role": role,
        "mode": "live-baseline",
        "binding_sha256": binding.canonical_sha256,
        "files": files,
        "zero_residue": True,
        "pull_policy": "never",
        "scratch_network_mode": "none",
        "source_mutated": False,
        "current_mutated": False,
        "source_stopped_or_restarted": False,
        "redis_restored": False,
    }


def _validate_prepare_result(
    document: Mapping[str, Any],
    *,
    operation_id: str,
    release_sha: str,
    role: str,
    binding_sha256: str,
) -> dict[str, Any]:
    if (
        set(document) != HOST_PREPARE_FIELDS
        or document["schema"] != HOST_PREPARE_SCHEMA
        or document["status"] != "prepared"
        or document["operation_id"] != operation_id
        or document["release_sha"] != release_sha
        or document["role"] != role
        or document["binding_sha256"] != binding_sha256
        or type(document["need_transfer"]) is not bool
        or type(document["partial_reconciled"]) is not bool
        or document["docker_contacted"] is not False
        or document["production_mutated"] is not False
    ):
        raise FinlandSourceSnapshotOrchestratorError(
            "host binding prepare result differs"
        )
    return dict(document)


def _validate_host_result(
    document: Mapping[str, Any],
    *,
    operation_id: str,
    release_sha: str,
    role: str,
    binding_sha256: str,
) -> dict[str, Any]:
    if (
        set(document) != HOST_RESULT_FIELDS
        or document["schema"] != HOST_RESULT_SCHEMA
        or document["status"] != "snapshotted"
        or document["snapshot_status"] not in {"applied", "resume-verified"}
        or document["operation_id"] != operation_id
        or document["release_sha"] != release_sha
        or document["role"] != role
        or document["mode"] != "live-baseline"
        or document["binding_sha256"] != binding_sha256
        or document["zero_residue"] is not True
        or document["pull_policy"] != "never"
        or document["scratch_network_mode"] != "none"
    ):
        raise FinlandSourceSnapshotOrchestratorError(
            "host source snapshot result identity differs"
        )
    for field in (
        "source_mutated",
        "current_mutated",
        "source_stopped_or_restarted",
        "redis_restored",
    ):
        if document[field] is not False:
            raise FinlandSourceSnapshotOrchestratorError(
                f"host source snapshot result {field} is not false"
            )
    files = document["files"]
    if not isinstance(files, dict) or set(files) != set(SNAPSHOT_FILENAMES):
        raise FinlandSourceSnapshotOrchestratorError(
            "host source snapshot file inventory differs"
        )
    for filename, row in files.items():
        if not isinstance(row, dict) or set(row) != FILE_RESULT_FIELDS:
            raise FinlandSourceSnapshotOrchestratorError(
                f"host source snapshot {filename} fields differ"
            )
        _nonzero_sha256(row["sha256"], label=f"{filename} SHA-256")
        _bounded_bytes(
            row["bytes"],
            label=f"{filename} bytes",
            maximum=(
                MAX_JSON_BYTES
                if filename == SOURCE.MANIFEST_FILE
                else MAX_ARTIFACT_BYTES
            ),
        )
    return dict(document)


def _safe_regular_for_reconcile(
    path: Path,
    *,
    required_uid: int,
    maximum: int,
) -> bool:
    try:
        metadata = path.stat(follow_symlinks=False)
    except OSError:
        return False
    return (
        stat.S_ISREG(metadata.st_mode)
        and metadata.st_uid == required_uid
        and stat.S_IMODE(metadata.st_mode) == 0o600
        and metadata.st_nlink == 1
        and 0 <= metadata.st_size <= maximum
    )


def _prepare_collection_partial(
    partial: Path,
    *,
    expected_sha256: str,
    expected_bytes: int,
    required_uid: int,
    maximum: int,
) -> bool:
    try:
        os.lstat(partial)
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise FinlandSourceSnapshotOrchestratorError(
            "collection transfer partial cannot be inspected"
        ) from exc
    try:
        observed = _hash_file(
            partial,
            label="collection transfer partial",
            required_uid=required_uid,
            expected_mode=0o600,
            maximum=maximum,
            allow_two_links=True,
        )
    except FinlandSourceSnapshotOrchestratorError:
        if not _safe_regular_for_reconcile(
            partial,
            required_uid=required_uid,
            maximum=maximum,
        ):
            raise FinlandSourceSnapshotOrchestratorError(
                "collection transfer partial is foreign"
            )
        observed = ("", -1)
    if observed == (expected_sha256, expected_bytes):
        return True
    if not _safe_regular_for_reconcile(
        partial,
        required_uid=required_uid,
        maximum=maximum,
    ):
        raise FinlandSourceSnapshotOrchestratorError(
            "collection transfer partial is foreign"
        )
    try:
        partial.unlink()
        _fsync_directory(partial.parent)
    except OSError as exc:
        raise FinlandSourceSnapshotOrchestratorError(
            "collection transfer partial could not be reconciled"
        ) from exc
    return False


def _copy_local_partial(
    source: Path,
    partial: Path,
    *,
    expected_sha256: str,
    expected_bytes: int,
    required_uid: int,
    maximum: int,
) -> None:
    if _prepare_collection_partial(
        partial,
        expected_sha256=expected_sha256,
        expected_bytes=expected_bytes,
        required_uid=required_uid,
        maximum=maximum,
    ):
        return
    descriptor = -1
    try:
        descriptor = os.open(
            partial,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        digest = hashlib.sha256()
        size = 0
        with _held_file(
            source,
            label="local source snapshot artifact",
            required_uid=required_uid,
            expected_mode=0o600,
            maximum=maximum,
        ) as (stream, _metadata):
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
                size += len(chunk)
                view = memoryview(chunk)
                while view:
                    written = os.write(descriptor, view)
                    if written <= 0:
                        raise OSError("short collection write")
                    view = view[written:]
        if (digest.hexdigest(), size) != (
            expected_sha256,
            expected_bytes,
        ):
            raise FinlandSourceSnapshotOrchestratorError(
                "local source snapshot artifact changed from host inventory"
            )
        os.fchmod(descriptor, 0o600)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        _fsync_directory(partial.parent)
    except FinlandSourceSnapshotOrchestratorError:
        raise
    except OSError as exc:
        raise FinlandSourceSnapshotOrchestratorError(
            "local source snapshot collection copy failed"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _publish_collection_file(
    partial: Path,
    destination: Path,
    *,
    expected_sha256: str,
    expected_bytes: int,
    required_uid: int,
    maximum: int,
) -> str:
    try:
        os.lstat(destination)
    except FileNotFoundError:
        exists = False
    except OSError as exc:
        raise FinlandSourceSnapshotOrchestratorError(
            "collection destination cannot be inspected"
        ) from exc
    else:
        exists = True
    if exists:
        if _hash_file(
            destination,
            label="collection destination",
            required_uid=required_uid,
            expected_mode=0o600,
            maximum=maximum,
            allow_two_links=True,
        ) != (expected_sha256, expected_bytes):
            raise FinlandSourceSnapshotOrchestratorError(
                "collection destination differs"
            )
        try:
            destination_metadata = destination.stat(follow_symlinks=False)
        except OSError as exc:
            raise FinlandSourceSnapshotOrchestratorError(
                "collection destination link state is unavailable"
            ) from exc
        if destination_metadata.st_nlink == 2:
            try:
                partial_metadata = partial.stat(follow_symlinks=False)
            except OSError as exc:
                raise FinlandSourceSnapshotOrchestratorError(
                    "collection destination has a foreign hard link"
                ) from exc
            if (
                partial_metadata.st_dev != destination_metadata.st_dev
                or partial_metadata.st_ino != destination_metadata.st_ino
            ):
                raise FinlandSourceSnapshotOrchestratorError(
                    "collection destination hard-link identity differs"
                )
        elif destination_metadata.st_nlink != 1:
            raise FinlandSourceSnapshotOrchestratorError(
                "collection destination has a foreign hard link"
            )
        publication = "reused"
    else:
        if _hash_file(
            partial,
            label="collection transfer partial",
            required_uid=required_uid,
            expected_mode=0o600,
            maximum=maximum,
        ) != (expected_sha256, expected_bytes):
            raise FinlandSourceSnapshotOrchestratorError(
                "collection transfer partial differs"
            )
        try:
            os.link(partial, destination, follow_symlinks=False)
            _fsync_directory(destination.parent)
        except FileExistsError:
            pass
        except OSError as exc:
            raise FinlandSourceSnapshotOrchestratorError(
                "collection destination create-only publication failed"
            ) from exc
        if _hash_file(
            destination,
            label="collection destination",
            required_uid=required_uid,
            expected_mode=0o600,
            maximum=maximum,
            allow_two_links=True,
        ) != (expected_sha256, expected_bytes):
            raise FinlandSourceSnapshotOrchestratorError(
                "published collection destination differs"
            )
        publication = "created"
    try:
        os.lstat(partial)
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise FinlandSourceSnapshotOrchestratorError(
            "collection transfer partial cleanup is ambiguous"
        ) from exc
    else:
        try:
            partial_metadata = partial.stat(follow_symlinks=False)
            destination_metadata = destination.stat(follow_symlinks=False)
        except OSError as exc:
            raise FinlandSourceSnapshotOrchestratorError(
                "collection transfer partial cleanup is ambiguous"
            ) from exc
        if (
            not stat.S_ISREG(partial_metadata.st_mode)
            or partial_metadata.st_uid != required_uid
            or stat.S_IMODE(partial_metadata.st_mode) != 0o600
            or partial_metadata.st_nlink not in {1, 2}
            or partial_metadata.st_size > maximum
            or (
                partial_metadata.st_nlink == 2
                and (
                    partial_metadata.st_dev != destination_metadata.st_dev
                    or partial_metadata.st_ino != destination_metadata.st_ino
                )
            )
        ):
            raise FinlandSourceSnapshotOrchestratorError(
                "collection transfer partial is foreign"
            )
        try:
            partial.unlink()
            _fsync_directory(partial.parent)
        except OSError as exc:
            raise FinlandSourceSnapshotOrchestratorError(
                "collection transfer partial cleanup failed"
            ) from exc
    if _hash_file(
        destination,
        label="collection destination readback",
        required_uid=required_uid,
        expected_mode=0o600,
        maximum=maximum,
    ) != (expected_sha256, expected_bytes):
        raise FinlandSourceSnapshotOrchestratorError(
            "collection destination changed after publication"
        )
    return publication


def _write_local_binding_transfer(
    source: Path,
    destination: Path,
    *,
    expected_sha256: str,
    required_uid: int,
) -> None:
    expected_bytes = source.stat(follow_symlinks=False).st_size
    _copy_local_partial(
        source,
        destination,
        expected_sha256=expected_sha256,
        expected_bytes=expected_bytes,
        required_uid=required_uid,
        maximum=MAX_JSON_BYTES,
    )


def _invoke_host(
    *,
    role: str,
    request: Mapping[str, Any],
    paths: Mapping[str, Any],
    ssh_identity: Path,
    runner: Runner,
) -> dict[str, Any]:
    encoded = encode_host_request(request)
    arguments = [
        PYTHON,
        "-B",
        str(paths["agent"]),
        "--host-request-b64",
        encoded,
    ]
    if role == "webapp_fi":
        arguments = ssh_arguments(
            ssh_identity,
            remote_arguments=arguments,
        )
        allowed = frozenset({SSH})
    else:
        allowed = frozenset({PYTHON})
    raw = _run_command(
        arguments,
        runner=runner,
        timeout=6 * 60 * 60,
        allowed=allowed,
    )
    return _parse_command_json(raw, label=f"{role} host agent")


def _collect_role(
    *,
    role: str,
    host_result: Mapping[str, Any],
    paths: Mapping[str, Any],
    ssh_identity: Path,
    runner: Runner,
    required_uid: int,
) -> dict[str, Any]:
    collection = paths["roles"][role]["collection"]
    _assert_directory(
        collection,
        label=f"{role} collection",
        required_uid=required_uid,
        private=True,
    )
    publications: dict[str, str] = {}
    files: dict[str, dict[str, Any]] = {}
    for filename in SNAPSHOT_FILENAMES:
        row = host_result["files"][filename]
        expected_sha256 = str(row["sha256"])
        expected_bytes = int(row["bytes"])
        maximum = (
            MAX_JSON_BYTES
            if filename == SOURCE.MANIFEST_FILE
            else MAX_ARTIFACT_BYTES
        )
        destination = collection / filename
        partial = collection / f".{filename}.transfer"
        try:
            os.lstat(destination)
        except FileNotFoundError:
            destination_exists = False
        except OSError as exc:
            raise FinlandSourceSnapshotOrchestratorError(
                "collection destination cannot be inspected"
            ) from exc
        else:
            destination_exists = True
        if destination_exists:
            if _hash_file(
                destination,
                label=f"{role} collected {filename}",
                required_uid=required_uid,
                expected_mode=0o600,
                maximum=maximum,
                allow_two_links=True,
            ) != (expected_sha256, expected_bytes):
                raise FinlandSourceSnapshotOrchestratorError(
                    f"{role} collected {filename} differs"
                )
        else:
            partial_ready = _prepare_collection_partial(
                partial,
                expected_sha256=expected_sha256,
                expected_bytes=expected_bytes,
                required_uid=required_uid,
                maximum=maximum,
            )
            if not partial_ready:
                source = paths["roles"][role]["snapshot"] / filename
                if role == "bot_fi":
                    _copy_local_partial(
                        source,
                        partial,
                        expected_sha256=expected_sha256,
                        expected_bytes=expected_bytes,
                        required_uid=required_uid,
                        maximum=maximum,
                    )
                else:
                    _run_command(
                        scp_download_arguments(
                            ssh_identity,
                            remote_source=source,
                            destination=partial,
                        ),
                        runner=runner,
                        timeout=6 * 60 * 60,
                        allowed=frozenset({SCP}),
                    )
                    if _hash_file(
                        partial,
                        label=f"{role} downloaded {filename}",
                        required_uid=required_uid,
                        expected_mode=0o600,
                        maximum=maximum,
                    ) != (expected_sha256, expected_bytes):
                        raise FinlandSourceSnapshotOrchestratorError(
                            f"{role} downloaded {filename} differs"
                        )
        publications[filename] = _publish_collection_file(
            partial,
            destination,
            expected_sha256=expected_sha256,
            expected_bytes=expected_bytes,
            required_uid=required_uid,
            maximum=maximum,
        )
        files[filename] = {
            "path": str(destination),
            "sha256": expected_sha256,
            "bytes": expected_bytes,
        }
    return {"files": files, "publications": publications}


def _verify_collected_role(
    *,
    role: str,
    binding: SOURCE.SnapshotBinding,
    paths: Mapping[str, Any],
) -> dict[str, Any]:
    collection = paths["roles"][role]["collection"]
    collected_paths = SOURCE.OutputPaths(
        operation_root=collection.parent.parent,
        role_root=collection.parent,
        final=collection,
        staging=collection.parent / ".unused",
        manifest=collection / SOURCE.MANIFEST_FILE,
    )
    try:
        document = SOURCE.verify_completed_output(
            collected_paths,
            binding,
            freeze_sha256=None,
        )
    except SOURCE.SourceSnapshotError as exc:
        raise FinlandSourceSnapshotOrchestratorError(
            f"{role} controller collection is not producer-verifiable"
        ) from exc
    return {
        "manifest_path": str(collected_paths.manifest),
        "manifest_sha256": hashlib.sha256(
            canonical_json(document)
        ).hexdigest(),
    }


def _state_sha256(document: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        canonical_json(
            {
                key: value
                for key, value in document.items()
                if key != "state_sha256"
            }
        )
    ).hexdigest()


def _validate_journal(
    document: Any,
    *,
    operation_id: str,
    release_sha: str,
    bindings: Mapping[str, SOURCE.SnapshotBinding],
) -> dict[str, Any]:
    if not isinstance(document, dict) or set(document) != JOURNAL_FIELDS:
        raise FinlandSourceSnapshotOrchestratorError(
            "source snapshot journal fields are not exact"
        )
    if (
        document["schema"] != JOURNAL_SCHEMA
        or document["operation_id"] != operation_id
        or document["release_sha"] != release_sha
        or document["bindings"]
        != {
            role: bindings[role].canonical_sha256
            for role in ROLES
        }
        or document["status"] not in {"active", "complete"}
        or document["state_sha256"] != _state_sha256(document)
    ):
        raise FinlandSourceSnapshotOrchestratorError(
            "source snapshot journal identity or hash differs"
        )
    completed = document["completed_roles"]
    if (
        not isinstance(completed, list)
        or completed != list(ROLES[: len(completed)])
        or document["current_role"]
        not in (
            {None}
            if len(completed) == len(ROLES)
            else {None, ROLES[len(completed)]}
        )
        or not isinstance(document["role_results"], dict)
        or set(document["role_results"]) != set(completed)
        or (document["status"] == "complete")
        != (completed == list(ROLES))
    ):
        raise FinlandSourceSnapshotOrchestratorError(
            "source snapshot journal progress is invalid"
        )
    for role, row in document["role_results"].items():
        if not isinstance(row, dict) or set(row) != JOURNAL_ROLE_FIELDS:
            raise FinlandSourceSnapshotOrchestratorError(
                "source snapshot journal role result differs"
            )
        _validate_host_result(
            row["host_result"],
            operation_id=operation_id,
            release_sha=release_sha,
            role=role,
            binding_sha256=bindings[role].canonical_sha256,
        )
        collection = row["collection"]
        if (
            not isinstance(collection, dict)
            or set(collection) != {"manifest_path", "manifest_sha256"}
            or collection["manifest_path"]
            != str(
                canonical_paths(operation_id, release_sha)["roles"][role][
                    "collection"
                ]
                / SOURCE.MANIFEST_FILE
            )
        ):
            raise FinlandSourceSnapshotOrchestratorError(
                "source snapshot journal collection binding differs"
            )
        _nonzero_sha256(
            collection["manifest_sha256"],
            label="collected manifest SHA-256",
        )
    return document


def _reconcile_journal_temporaries(
    path: Path,
    *,
    required_uid: int,
) -> None:
    pattern = re.compile(
        JOURNAL_TEMP_RE_TEMPLATE.format(name=re.escape(path.name))
    )
    try:
        candidates = [
            path.parent / entry.name
            for entry in os.scandir(path.parent)
            if pattern.fullmatch(entry.name)
        ]
    except OSError as exc:
        raise FinlandSourceSnapshotOrchestratorError(
            "journal temporary inventory is unavailable"
        ) from exc
    if len(candidates) > 32:
        raise FinlandSourceSnapshotOrchestratorError(
            "journal temporary inventory is excessive"
        )
    changed = False
    for candidate in candidates:
        if not _safe_regular_for_reconcile(
            candidate,
            required_uid=required_uid,
            maximum=MAX_JOURNAL_BYTES,
        ):
            raise FinlandSourceSnapshotOrchestratorError(
                "journal temporary is foreign"
            )
        try:
            candidate.unlink()
            changed = True
        except OSError as exc:
            raise FinlandSourceSnapshotOrchestratorError(
                "journal temporary reconciliation failed"
            ) from exc
    if changed:
        _fsync_directory(path.parent)


def _read_journal(
    path: Path,
    *,
    operation_id: str,
    release_sha: str,
    bindings: Mapping[str, SOURCE.SnapshotBinding],
    required_uid: int,
) -> dict[str, Any] | None:
    _reconcile_journal_temporaries(path, required_uid=required_uid)
    try:
        os.lstat(path)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise FinlandSourceSnapshotOrchestratorError(
            "source snapshot journal cannot be inspected"
        ) from exc
    raw = _read_file(
        path,
        label="source snapshot journal",
        required_uid=required_uid,
        expected_mode=0o600,
        maximum=MAX_JOURNAL_BYTES,
    )
    document = _strict_json(raw, label="source snapshot journal")
    if raw != canonical_json(document):
        raise FinlandSourceSnapshotOrchestratorError(
            "source snapshot journal is not canonical JSON"
        )
    return _validate_journal(
        document,
        operation_id=operation_id,
        release_sha=release_sha,
        bindings=bindings,
    )


def _write_journal(
    path: Path,
    document: dict[str, Any],
    *,
    required_uid: int,
    create: bool,
) -> None:
    document["state_sha256"] = _state_sha256(document)
    payload = canonical_json(document)
    if not 1 <= len(payload) <= MAX_JOURNAL_BYTES:
        raise FinlandSourceSnapshotOrchestratorError(
            "source snapshot journal is oversized"
        )
    temporary = path.parent / (
        f".{path.name}.{os.getpid()}.{secrets.token_hex(8)}.tmp"
    )
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
                raise OSError("short journal write")
            view = view[written:]
        os.fchmod(descriptor, 0o600)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        if create:
            try:
                os.link(temporary, path, follow_symlinks=False)
            except FileExistsError as exc:
                raise FinlandSourceSnapshotOrchestratorError(
                    "source snapshot journal already exists"
                ) from exc
        else:
            try:
                metadata = path.stat(follow_symlinks=False)
            except OSError as exc:
                raise FinlandSourceSnapshotOrchestratorError(
                    "source snapshot journal is unavailable"
                ) from exc
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != required_uid
                or stat.S_IMODE(metadata.st_mode) != 0o600
                or metadata.st_nlink != 1
            ):
                raise FinlandSourceSnapshotOrchestratorError(
                    "source snapshot journal is unsafe"
                )
            os.replace(temporary, path)
        _fsync_directory(path.parent)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        _fsync_directory(path.parent)
    except FinlandSourceSnapshotOrchestratorError:
        raise
    except OSError as exc:
        raise FinlandSourceSnapshotOrchestratorError(
            "source snapshot journal write failed"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


@contextmanager
def _controller_lock(path: Path, *, required_uid: int) -> Iterator[None]:
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
            raise FinlandSourceSnapshotOrchestratorError(
                "source snapshot controller lock is unsafe"
            )
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise FinlandSourceSnapshotOrchestratorError(
                "another Finland source snapshot controller is active"
            ) from exc
        yield
    except FinlandSourceSnapshotOrchestratorError:
        raise
    except OSError as exc:
        raise FinlandSourceSnapshotOrchestratorError(
            "source snapshot controller lock is unavailable"
        ) from exc
    finally:
        if descriptor >= 0:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            except OSError:
                pass
            os.close(descriptor)


def _ensure_controller_directories(
    paths: Mapping[str, Any],
    *,
    required_uid: int,
) -> None:
    _assert_directory(
        paths["operation_secret"],
        label="operation secret root",
        required_uid=required_uid,
        private=True,
    )
    _assert_directory(
        paths["controller_root"],
        label="controller secret root",
        required_uid=required_uid,
        private=True,
    )
    first = paths["controller_root"] / "source-snapshots"
    _ensure_private_child(
        first,
        parent=paths["controller_root"],
        label="controller source snapshot root",
        required_uid=required_uid,
    )
    _ensure_private_child(
        paths["collection_root"],
        parent=first,
        label="controller live-baseline collection",
        required_uid=required_uid,
    )
    for role in ROLES:
        _ensure_private_child(
            paths["roles"][role]["collection"],
            parent=paths["collection_root"],
            label=f"{role} controller collection",
            required_uid=required_uid,
        )


def _initial_journal(
    *,
    operation_id: str,
    release_sha: str,
    bindings: Mapping[str, SOURCE.SnapshotBinding],
) -> dict[str, Any]:
    document = {
        "schema": JOURNAL_SCHEMA,
        "operation_id": operation_id,
        "release_sha": release_sha,
        "bindings": {
            role: bindings[role].canonical_sha256
            for role in ROLES
        },
        "status": "active",
        "completed_roles": [],
        "current_role": None,
        "role_results": {},
        "state_sha256": ZERO_SHA256,
    }
    document["state_sha256"] = _state_sha256(document)
    return document


def _controller_result(
    *,
    operation_id: str,
    release_sha: str,
    paths: Mapping[str, Any],
    journal: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "schema": RESULT_SCHEMA,
        "status": "complete",
        "operation_id": operation_id,
        "release_sha": release_sha,
        "roles": {
            role: {
                "host": ROLE_HOSTS[role],
                "transport": ROLE_TRANSPORTS[role],
                "binding_sha256": journal["bindings"][role],
                **journal["role_results"][role]["collection"],
            }
            for role in ROLES
        },
        "collection_root": str(paths["collection_root"]),
        "journal_path": str(paths["journal"]),
        "journal_state_sha256": journal["state_sha256"],
        "pull_policy": "never",
        "build_performed": False,
        "object_storage_used": False,
        "source_mutated": False,
        "current_mutated": False,
        "source_stopped_or_restarted": False,
        "redis_restored": False,
        "only_scratch_container_and_volume": True,
    }


def render_plan(
    *,
    operation_id: str,
    release_sha: str,
    bindings: Mapping[str, SOURCE.SnapshotBinding],
    binding_paths: Mapping[str, Path],
    ssh_identity: Path,
) -> dict[str, Any]:
    paths = canonical_paths(operation_id, release_sha)
    role_plans: dict[str, Any] = {}
    for role in ROLES:
        prepare = build_host_request(
            action="prepare-binding",
            operation_id=operation_id,
            release_sha=release_sha,
            role=role,
            binding_sha256=bindings[role].canonical_sha256,
        )
        snapshot = build_host_request(
            action="snapshot",
            operation_id=operation_id,
            release_sha=release_sha,
            role=role,
            binding_sha256=bindings[role].canonical_sha256,
        )
        prepare_argv = [
            PYTHON,
            "-B",
            str(paths["agent"]),
            "--host-request-b64",
            encode_host_request(prepare),
        ]
        snapshot_argv = [
            PYTHON,
            "-B",
            str(paths["agent"]),
            "--host-request-b64",
            encode_host_request(snapshot),
        ]
        if role == "webapp_fi":
            prepare_argv = ssh_arguments(
                ssh_identity,
                remote_arguments=prepare_argv,
            )
            snapshot_argv = ssh_arguments(
                ssh_identity,
                remote_arguments=snapshot_argv,
            )
        role_plans[role] = {
            "host": ROLE_HOSTS[role],
            "transport": ROLE_TRANSPORTS[role],
            "binding_source": str(binding_paths[role]),
            "binding_sha256": bindings[role].canonical_sha256,
            "binding_destination": str(
                paths["roles"][role]["binding"]
            ),
            "snapshot_directory": str(
                paths["roles"][role]["snapshot"]
            ),
            "collection_directory": str(
                paths["roles"][role]["collection"]
            ),
            "prepare_argv": prepare_argv,
            "snapshot_argv": snapshot_argv,
        }
    return {
        "schema": PLAN_SCHEMA,
        "status": "planned",
        "operation_id": operation_id,
        "release_sha": release_sha,
        "release_root": str(paths["release_root"]),
        "producer": str(paths["producer"]),
        "output_root": str(SOURCE_OUTPUT_ROOT),
        "collection_root": str(paths["collection_root"]),
        "journal_path": str(paths["journal"]),
        "roles": role_plans,
        "required_confirmation": confirmation_phrase(
            operation_id,
            release_sha,
        ),
        "pull_policy": "never",
        "build_performed": False,
        "docker_contacted": False,
        "network_io": False,
        "filesystem_mutated": False,
        "production_mutated": False,
    }


def orchestrate(
    *,
    operation_id: str,
    release_sha: str,
    bot_fi_binding: Path,
    webapp_fi_binding: Path,
    ssh_identity: Path = DEFAULT_SSH_IDENTITY,
    apply: bool = False,
    confirm: str | None = None,
    runner: Runner = subprocess.run,
    required_uid: int = 0,
    checkpoint: Checkpoint | None = None,
    observed_host_addresses: set[str] | None = None,
) -> dict[str, Any]:
    operation_id = _canonical_uuid4(operation_id)
    release_sha = _release_sha(release_sha)
    binding_paths = {
        "bot_fi": bot_fi_binding,
        "webapp_fi": webapp_fi_binding,
    }
    bindings = load_bindings(
        operation_id=operation_id,
        release_sha=release_sha,
        bot_fi_binding=bot_fi_binding,
        webapp_fi_binding=webapp_fi_binding,
    )
    plan = render_plan(
        operation_id=operation_id,
        release_sha=release_sha,
        bindings=bindings,
        binding_paths=binding_paths,
        ssh_identity=ssh_identity,
    )
    if not apply:
        if confirm is not None:
            raise FinlandSourceSnapshotOrchestratorError(
                "--confirm is valid only with --apply"
            )
        return plan
    if confirm != confirmation_phrase(operation_id, release_sha):
        raise FinlandSourceSnapshotOrchestratorError(
            "Finland source snapshot confirmation mismatch"
        )
    if os.geteuid() != required_uid or required_uid != 0:
        raise FinlandSourceSnapshotOrchestratorError(
            "Finland source snapshot controller must run as root"
        )
    try:
        FINLAND_STAGE._verify_role_host(
            "bot_fi",
            observed_host_addresses=observed_host_addresses,
        )
    except FINLAND_STAGE.FinlandStageError as exc:
        raise FinlandSourceSnapshotOrchestratorError(
            "controller host is not Bot-FI"
        ) from exc
    _assert_ssh_material(ssh_identity, required_uid=required_uid)
    paths = canonical_paths(operation_id, release_sha)
    _ensure_controller_directories(paths, required_uid=required_uid)
    callback = checkpoint if checkpoint is not None else (lambda _name: None)

    with _controller_lock(paths["lock"], required_uid=required_uid):
        journal = _read_journal(
            paths["journal"],
            operation_id=operation_id,
            release_sha=release_sha,
            bindings=bindings,
            required_uid=required_uid,
        )
        if journal is None:
            journal = _initial_journal(
                operation_id=operation_id,
                release_sha=release_sha,
                bindings=bindings,
            )
            _write_journal(
                paths["journal"],
                journal,
                required_uid=required_uid,
                create=True,
            )
        for role in ROLES:
            if role in journal["completed_roles"]:
                verified = _verify_collected_role(
                    role=role,
                    binding=bindings[role],
                    paths=paths,
                )
                if verified != journal["role_results"][role]["collection"]:
                    raise FinlandSourceSnapshotOrchestratorError(
                        f"{role} collected snapshot changed after journaling"
                    )
                continue
            if journal["current_role"] not in {None, role}:
                raise FinlandSourceSnapshotOrchestratorError(
                    "source snapshot journal current role differs"
                )
            if journal["current_role"] is None:
                journal["current_role"] = role
                _write_journal(
                    paths["journal"],
                    journal,
                    required_uid=required_uid,
                    create=False,
                )
            callback(f"before-role:{role}")
            prepare_request = build_host_request(
                action="prepare-binding",
                operation_id=operation_id,
                release_sha=release_sha,
                role=role,
                binding_sha256=bindings[role].canonical_sha256,
            )
            prepared = _validate_prepare_result(
                _invoke_host(
                    role=role,
                    request=prepare_request,
                    paths=paths,
                    ssh_identity=ssh_identity,
                    runner=runner,
                ),
                operation_id=operation_id,
                release_sha=release_sha,
                role=role,
                binding_sha256=bindings[role].canonical_sha256,
            )
            if prepared["need_transfer"]:
                transfer = paths["roles"][role]["binding_transfer"]
                if role == "bot_fi":
                    _write_local_binding_transfer(
                        binding_paths[role],
                        transfer,
                        expected_sha256=bindings[role].canonical_sha256,
                        required_uid=required_uid,
                    )
                else:
                    _run_command(
                        scp_upload_arguments(
                            ssh_identity,
                            source=binding_paths[role],
                            remote_destination=transfer,
                        ),
                        runner=runner,
                        timeout=600,
                        allowed=frozenset({SCP}),
                    )
            callback(f"after-binding:{role}")
            snapshot_request = build_host_request(
                action="snapshot",
                operation_id=operation_id,
                release_sha=release_sha,
                role=role,
                binding_sha256=bindings[role].canonical_sha256,
            )
            host_result = _validate_host_result(
                _invoke_host(
                    role=role,
                    request=snapshot_request,
                    paths=paths,
                    ssh_identity=ssh_identity,
                    runner=runner,
                ),
                operation_id=operation_id,
                release_sha=release_sha,
                role=role,
                binding_sha256=bindings[role].canonical_sha256,
            )
            callback(f"after-snapshot:{role}")
            _collect_role(
                role=role,
                host_result=host_result,
                paths=paths,
                ssh_identity=ssh_identity,
                runner=runner,
                required_uid=required_uid,
            )
            collection = _verify_collected_role(
                role=role,
                binding=bindings[role],
                paths=paths,
            )
            journal["role_results"][role] = {
                "host_result": host_result,
                "collection": collection,
            }
            journal["completed_roles"].append(role)
            journal["current_role"] = None
            if journal["completed_roles"] == list(ROLES):
                journal["status"] = "complete"
            _write_journal(
                paths["journal"],
                journal,
                required_uid=required_uid,
                create=False,
            )
            callback(f"after-role:{role}")
        journal = _validate_journal(
            journal,
            operation_id=operation_id,
            release_sha=release_sha,
            bindings=bindings,
        )
        return _controller_result(
            operation_id=operation_id,
            release_sha=release_sha,
            paths=paths,
            journal=journal,
        )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--operation-id")
    parser.add_argument("--release-sha")
    parser.add_argument("--bot-fi-binding", type=Path)
    parser.add_argument("--webapp-fi-binding", type=Path)
    parser.add_argument(
        "--ssh-identity",
        type=Path,
        default=DEFAULT_SSH_IDENTITY,
    )
    parser.add_argument("--pull", choices=("never",), default="never")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm")
    parser.add_argument("--host-request-b64", help=argparse.SUPPRESS)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.pull != "never":
            raise FinlandSourceSnapshotOrchestratorError(
                "only --pull never is supported"
            )
        if args.host_request_b64 is not None:
            forbidden = (
                args.operation_id,
                args.release_sha,
                args.bot_fi_binding,
                args.webapp_fi_binding,
                args.confirm,
            )
            if any(value is not None for value in forbidden) or args.apply:
                raise FinlandSourceSnapshotOrchestratorError(
                    "host request cannot be combined with controller arguments"
                )
            result = host_agent(args.host_request_b64)
        else:
            if (
                args.operation_id is None
                or args.release_sha is None
                or args.bot_fi_binding is None
                or args.webapp_fi_binding is None
            ):
                raise FinlandSourceSnapshotOrchestratorError(
                    "controller identity and both bindings are required"
                )
            result = orchestrate(
                operation_id=args.operation_id,
                release_sha=args.release_sha,
                bot_fi_binding=args.bot_fi_binding,
                webapp_fi_binding=args.webapp_fi_binding,
                ssh_identity=args.ssh_identity,
                apply=args.apply,
                confirm=args.confirm,
            )
        print(canonical_json(result).decode("ascii"))
        return 0
    except (
        FinlandSourceSnapshotOrchestratorError,
        SOURCE.SourceSnapshotError,
        FINLAND_STAGE.FinlandStageError,
    ) as exc:
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
    except Exception:
        print(
            canonical_json(
                {
                    "schema": RESULT_SCHEMA,
                    "status": "blocked",
                    "error": "Finland source snapshot orchestration failed closed",
                }
            ).decode("ascii"),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
