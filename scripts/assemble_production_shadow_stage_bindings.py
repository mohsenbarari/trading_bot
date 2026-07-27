#!/usr/bin/env python3
"""Assemble four role-local image-stage summaries for the prepare producer.

The default invocation is validation-only. Apply mode publishes one canonical
controller-local JSON file without replacing an existing destination.
"""

from __future__ import annotations

import argparse
import ctypes
from dataclasses import dataclass
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any, Mapping
from uuid import UUID


ROLE_SUMMARY_SCHEMA = "production-shadow-role-image-stage-binding-v1"
STAGE_BINDINGS_SCHEMA = "production-shadow-image-stage-bindings-v1"
DOCKER_ROLES = ("bot_fi", "webapp_fi", "webapp_ir")
ALL_ROLES = (*DOCKER_ROLES, "witness")
IMAGE_KINDS = ("app", "postgres", "redis", "nginx")
ROLE_SUMMARY_FIELDS = frozenset(
    {
        "schema",
        "operation_id",
        "release_sha",
        "role",
        "stage_operation_manifest_sha256",
        "stage_attestation_sha256",
        "runtime_image_ids",
    }
)
OUTPUT_ROLE_FIELDS = frozenset(
    {
        "stage_operation_manifest_sha256",
        "stage_attestation_sha256",
        "runtime_image_ids",
    }
)
OUTPUT_FIELDS = frozenset(
    {"schema", "operation_id", "release_sha", "roles"}
)
ROOT_UID = 0
INPUT_MODE = 0o600
OUTPUT_DIRECTORY_MODE = 0o700
MAX_JSON_BYTES = 256 * 1024
SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
IMAGE_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
RENAME_NOREPLACE = 1


class StageBindingAssemblyError(RuntimeError):
    """Raised when the four role summaries cannot be assembled safely."""


@dataclass(frozen=True)
class SecurePayload:
    payload: bytes
    sha256: str
    device: int
    inode: int


@dataclass(frozen=True)
class LoadedRoleSummary:
    role: str
    operation_id: str
    release_sha: str
    output_row: dict[str, Any]
    source: SecurePayload


@dataclass(frozen=True)
class AssemblyPreflight:
    output_path: Path
    document: dict[str, Any]
    payload: bytes
    payload_sha256: str
    role_input_sha256s: dict[str, str]
    source_identities: frozenset[tuple[int, int]]
    output_state: str


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise StageBindingAssemblyError(
                "JSON contains a duplicate key"
            )
        result[key] = value
    return result


def _require_controller_root() -> None:
    if os.geteuid() != ROOT_UID:
        raise StageBindingAssemblyError(
            "stage binding assembly requires controller root"
        )


def _canonical_uuid4(value: Any, *, label: str) -> str:
    if not isinstance(value, str):
        raise StageBindingAssemblyError(
            f"{label} must be a canonical UUIDv4"
        )
    try:
        parsed = UUID(value)
    except (AttributeError, ValueError) as exc:
        raise StageBindingAssemblyError(
            f"{label} must be a canonical UUIDv4"
        ) from exc
    if parsed.version != 4 or str(parsed) != value:
        raise StageBindingAssemblyError(
            f"{label} must be a canonical UUIDv4"
        )
    return value


def _nonzero_release_sha(value: Any) -> str:
    if (
        not isinstance(value, str)
        or SHA40_RE.fullmatch(value) is None
        or value == "0" * 40
    ):
        raise StageBindingAssemblyError(
            "role summary release SHA must be a nonzero 40-hex value"
        )
    return value


def _nonzero_sha256(value: Any, *, label: str) -> str:
    if (
        not isinstance(value, str)
        or SHA256_RE.fullmatch(value) is None
        or value == "0" * 64
    ):
        raise StageBindingAssemblyError(
            f"{label} must be a nonzero SHA-256"
        )
    return value


def _absolute_canonical_path(path: Path, *, label: str) -> Path:
    if (
        not isinstance(path, Path)
        or not path.is_absolute()
        or path.name in {"", ".", ".."}
        or path != Path(os.path.abspath(os.fspath(path)))
    ):
        raise StageBindingAssemblyError(
            f"{label} must be an absolute canonical path"
        )
    return path


def _directory_flags() -> int:
    if not hasattr(os, "O_DIRECTORY") or not hasattr(os, "O_NOFOLLOW"):
        raise StageBindingAssemblyError(
            "secure no-follow directory traversal is unavailable"
        )
    return (
        os.O_RDONLY
        | os.O_DIRECTORY
        | getattr(os, "O_CLOEXEC", 0)
        | os.O_NOFOLLOW
    )


def _open_parent(path: Path, *, label: str) -> tuple[int, str]:
    flags = _directory_flags()
    descriptor = -1
    try:
        descriptor = os.open("/", flags)
        for component in path.parts[1:-1]:
            next_descriptor = os.open(
                component,
                flags,
                dir_fd=descriptor,
            )
            metadata = os.fstat(next_descriptor)
            if not stat.S_ISDIR(metadata.st_mode):
                os.close(next_descriptor)
                raise StageBindingAssemblyError(
                    f"{label} parent traversal is unsafe"
                )
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor, path.name
    except StageBindingAssemblyError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError as exc:
        if descriptor >= 0:
            os.close(descriptor)
        raise StageBindingAssemblyError(
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


def _read_secure_leaf(
    directory_fd: int,
    name: str,
    *,
    label: str,
    maximum: int = MAX_JSON_BYTES,
) -> SecurePayload:
    if not hasattr(os, "O_NOFOLLOW"):
        raise StageBindingAssemblyError(
            "secure no-follow file reads are unavailable"
        )
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
            or stat.S_IMODE(before.st_mode) != INPUT_MODE
            or before.st_nlink != 1
            or not 1 <= before.st_size <= maximum
        ):
            raise StageBindingAssemblyError(
                f"{label} is not an exact root-only 0600 file"
            )
        chunks: list[bytes] = []
        remaining = maximum + 1
        while remaining > 0:
            chunk = os.read(descriptor, min(65536, remaining))
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
            len(payload) > maximum
            or _stable_metadata(before) != _stable_metadata(after)
            or _stable_metadata(after) != _stable_metadata(visible)
        ):
            raise StageBindingAssemblyError(
                f"{label} changed while being read"
            )
        return SecurePayload(
            payload=payload,
            sha256=hashlib.sha256(payload).hexdigest(),
            device=after.st_dev,
            inode=after.st_ino,
        )
    except StageBindingAssemblyError:
        raise
    except OSError as exc:
        raise StageBindingAssemblyError(
            f"{label} is unavailable or unsafe"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _read_secure_path(path: Path, *, label: str) -> SecurePayload:
    directory_fd, name = _open_parent(path, label=label)
    try:
        return _read_secure_leaf(directory_fd, name, label=label)
    finally:
        os.close(directory_fd)


def _load_role_summary(
    path: Path,
    *,
    expected_role: str,
) -> LoadedRoleSummary:
    source = _read_secure_path(
        path,
        label=f"{expected_role} role stage summary",
    )
    try:
        document = json.loads(
            source.payload.decode("utf-8"),
            object_pairs_hook=_strict_object,
        )
    except StageBindingAssemblyError:
        raise
    except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise StageBindingAssemblyError(
            f"{expected_role} role stage summary is invalid JSON"
        ) from exc
    if not isinstance(document, dict) or set(document) != ROLE_SUMMARY_FIELDS:
        raise StageBindingAssemblyError(
            f"{expected_role} role stage summary fields are not exact"
        )
    if source.payload != _canonical_json(document):
        raise StageBindingAssemblyError(
            f"{expected_role} role stage summary is not canonical JSON"
        )
    if document["schema"] != ROLE_SUMMARY_SCHEMA:
        raise StageBindingAssemblyError(
            f"{expected_role} role stage summary schema differs"
        )
    operation_id = _canonical_uuid4(
        document["operation_id"],
        label=f"{expected_role} operation ID",
    )
    release_sha = _nonzero_release_sha(document["release_sha"])
    if document["role"] != expected_role:
        raise StageBindingAssemblyError(
            f"{expected_role} role stage summary role differs"
        )
    stage_manifest = _nonzero_sha256(
        document["stage_operation_manifest_sha256"],
        label=f"{expected_role} stage operation manifest",
    )
    stage_attestation = _nonzero_sha256(
        document["stage_attestation_sha256"],
        label=f"{expected_role} stage attestation",
    )
    runtime_image_ids = document["runtime_image_ids"]
    expected_image_keys = (
        set(IMAGE_KINDS) if expected_role in DOCKER_ROLES else set()
    )
    if (
        not isinstance(runtime_image_ids, dict)
        or set(runtime_image_ids) != expected_image_keys
        or any(
            not isinstance(value, str)
            or IMAGE_ID_RE.fullmatch(value) is None
            or value == "sha256:" + "0" * 64
            for value in runtime_image_ids.values()
        )
        or len(set(runtime_image_ids.values()))
        != len(runtime_image_ids)
    ):
        raise StageBindingAssemblyError(
            f"{expected_role} runtime image IDs are invalid"
        )
    output_row = {
        "stage_operation_manifest_sha256": stage_manifest,
        "stage_attestation_sha256": stage_attestation,
        "runtime_image_ids": (
            {
                kind: runtime_image_ids[kind]
                for kind in IMAGE_KINDS
            }
            if expected_role in DOCKER_ROLES
            else {}
        ),
    }
    if set(output_row) != OUTPUT_ROLE_FIELDS:
        raise StageBindingAssemblyError(
            f"{expected_role} output role binding is invalid"
        )
    return LoadedRoleSummary(
        role=expected_role,
        operation_id=operation_id,
        release_sha=release_sha,
        output_row=output_row,
        source=source,
    )


def _temporary_name(output_path: Path, payload: bytes) -> str:
    identity = hashlib.sha256(
        output_path.name.encode("utf-8") + b"\0" + payload
    ).hexdigest()
    return f".production-shadow-stage-bindings-{identity[:24]}.tmp"


def _leaf_exists(directory_fd: int, name: str, *, label: str) -> bool:
    try:
        os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise StageBindingAssemblyError(
            f"{label} cannot be inspected safely"
        ) from exc
    return True


def _assert_output_directory(directory_fd: int) -> None:
    metadata = os.fstat(directory_fd)
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != ROOT_UID
        or stat.S_IMODE(metadata.st_mode) != OUTPUT_DIRECTORY_MODE
    ):
        raise StageBindingAssemblyError(
            "output parent must be a root-owned 0700 directory"
        )


def _preflight_output(
    output_path: Path,
    payload: bytes,
    *,
    source_identities: frozenset[tuple[int, int]],
) -> str:
    directory_fd, output_name = _open_parent(
        output_path,
        label="stage bindings output",
    )
    try:
        _assert_output_directory(directory_fd)
        output_exists = _leaf_exists(
            directory_fd,
            output_name,
            label="stage bindings output",
        )
        if output_exists:
            observed = _read_secure_leaf(
                directory_fd,
                output_name,
                label="existing stage bindings output",
            )
            if (observed.device, observed.inode) in source_identities:
                raise StageBindingAssemblyError(
                    "output aliases a role stage summary"
                )
            if observed.payload != payload:
                raise StageBindingAssemblyError(
                    "existing stage bindings output differs"
                )
        temporary_name = _temporary_name(output_path, payload)
        temporary_exists = _leaf_exists(
            directory_fd,
            temporary_name,
            label="stage bindings temporary",
        )
        if temporary_exists:
            temporary = _read_secure_leaf(
                directory_fd,
                temporary_name,
                label="stage bindings temporary",
            )
            if (temporary.device, temporary.inode) in source_identities:
                raise StageBindingAssemblyError(
                    "stage bindings temporary aliases an input"
                )
            if temporary.payload != payload:
                raise StageBindingAssemblyError(
                    "stage bindings temporary differs"
                )
        if output_exists and temporary_exists:
            return "identical-with-recoverable-temporary"
        if output_exists:
            return "identical"
        if temporary_exists:
            return "recoverable-temporary"
        return "absent"
    finally:
        os.close(directory_fd)


def preflight_assembly(
    role_paths: Mapping[str, Path],
    output_path: Path,
) -> AssemblyPreflight:
    _require_controller_root()
    if not isinstance(role_paths, Mapping) or set(role_paths) != set(ALL_ROLES):
        raise StageBindingAssemblyError(
            "exactly four canonical role paths are required"
        )
    canonical_role_paths = {
        role: _absolute_canonical_path(
            role_paths[role],
            label=f"{role} role stage summary path",
        )
        for role in ALL_ROLES
    }
    canonical_output = _absolute_canonical_path(
        output_path,
        label="stage bindings output path",
    )
    all_paths = [
        *(os.fspath(canonical_role_paths[role]) for role in ALL_ROLES),
        os.fspath(canonical_output),
    ]
    if len(set(all_paths)) != len(all_paths):
        raise StageBindingAssemblyError(
            "role summary and output paths must be distinct"
        )

    loaded = {
        role: _load_role_summary(
            canonical_role_paths[role],
            expected_role=role,
        )
        for role in ALL_ROLES
    }
    source_identities = frozenset(
        (summary.source.device, summary.source.inode)
        for summary in loaded.values()
    )
    if len(source_identities) != len(ALL_ROLES):
        raise StageBindingAssemblyError(
            "role stage summary files must be physically distinct"
        )
    operation_ids = {summary.operation_id for summary in loaded.values()}
    release_shas = {summary.release_sha for summary in loaded.values()}
    if len(operation_ids) != 1 or len(release_shas) != 1:
        raise StageBindingAssemblyError(
            "role summaries do not bind one operation and release"
        )
    operation_id = next(iter(operation_ids))
    release_sha = next(iter(release_shas))
    document = {
        "schema": STAGE_BINDINGS_SCHEMA,
        "operation_id": operation_id,
        "release_sha": release_sha,
        "roles": {
            role: loaded[role].output_row
            for role in ALL_ROLES
        },
    }
    if set(document) != OUTPUT_FIELDS:
        raise StageBindingAssemblyError("assembled output fields are invalid")
    payload = _canonical_json(document)
    output_state = _preflight_output(
        canonical_output,
        payload,
        source_identities=source_identities,
    )
    return AssemblyPreflight(
        output_path=canonical_output,
        document=document,
        payload=payload,
        payload_sha256=hashlib.sha256(payload).hexdigest(),
        role_input_sha256s={
            role: loaded[role].source.sha256
            for role in ALL_ROLES
        },
        source_identities=source_identities,
        output_state=output_state,
    )


def _fsync_directory(directory_fd: int) -> None:
    try:
        os.fsync(directory_fd)
    except OSError as exc:
        raise StageBindingAssemblyError(
            "stage bindings output directory cannot be synchronized"
        ) from exc


def _write_temporary(
    directory_fd: int,
    name: str,
    payload: bytes,
) -> None:
    descriptor = -1
    try:
        descriptor = os.open(
            name,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | os.O_NOFOLLOW,
            INPUT_MODE,
            dir_fd=directory_fd,
        )
        os.fchmod(descriptor, INPUT_MODE)
        view = memoryview(payload)
        written = 0
        while written < len(view):
            count = os.write(descriptor, view[written:])
            if count <= 0:
                raise StageBindingAssemblyError(
                    "stage bindings temporary write made no progress"
                )
            written += count
        os.fsync(descriptor)
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != ROOT_UID
            or stat.S_IMODE(metadata.st_mode) != INPUT_MODE
            or metadata.st_nlink != 1
            or metadata.st_size != len(payload)
        ):
            raise StageBindingAssemblyError(
                "stage bindings temporary identity differs"
            )
    except FileExistsError:
        observed = _read_secure_leaf(
            directory_fd,
            name,
            label="raced stage bindings temporary",
        )
        if observed.payload != payload:
            raise StageBindingAssemblyError(
                "raced stage bindings temporary differs"
            )
    except StageBindingAssemblyError:
        raise
    except OSError as exc:
        raise StageBindingAssemblyError(
            "stage bindings temporary cannot be created"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    _fsync_directory(directory_fd)


def _rename_noreplace(
    directory_fd: int,
    source_name: str,
    destination_name: str,
) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        raise StageBindingAssemblyError(
            "create-only renameat2 publication is unavailable"
        )
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
    raise StageBindingAssemblyError(
        "stage bindings could not be published create-only"
    )


def _unlink_exact_temporary(
    directory_fd: int,
    name: str,
    payload: bytes,
) -> None:
    if not _leaf_exists(
        directory_fd,
        name,
        label="stage bindings temporary",
    ):
        return
    observed = _read_secure_leaf(
        directory_fd,
        name,
        label="stage bindings temporary",
    )
    if observed.payload != payload:
        raise StageBindingAssemblyError(
            "stage bindings temporary differs"
        )
    try:
        os.unlink(name, dir_fd=directory_fd)
    except OSError as exc:
        raise StageBindingAssemblyError(
            "stage bindings temporary cannot be removed"
        ) from exc
    _fsync_directory(directory_fd)


def publish_assembly(preflight: AssemblyPreflight) -> str:
    _require_controller_root()
    current_state = _preflight_output(
        preflight.output_path,
        preflight.payload,
        source_identities=preflight.source_identities,
    )
    directory_fd, output_name = _open_parent(
        preflight.output_path,
        label="stage bindings output",
    )
    try:
        _assert_output_directory(directory_fd)
        temporary_name = _temporary_name(
            preflight.output_path,
            preflight.payload,
        )
        if current_state in {
            "identical",
            "identical-with-recoverable-temporary",
        }:
            observed = _read_secure_leaf(
                directory_fd,
                output_name,
                label="existing stage bindings output",
            )
            if (
                (observed.device, observed.inode)
                in preflight.source_identities
                or observed.payload != preflight.payload
            ):
                raise StageBindingAssemblyError(
                    "existing stage bindings output changed"
                )
            if current_state == "identical-with-recoverable-temporary":
                _unlink_exact_temporary(
                    directory_fd,
                    temporary_name,
                    preflight.payload,
                )
            return "reused"
        if current_state == "absent":
            _write_temporary(
                directory_fd,
                temporary_name,
                preflight.payload,
            )
        try:
            _rename_noreplace(
                directory_fd,
                temporary_name,
                output_name,
            )
            publication = "created"
        except (FileExistsError, FileNotFoundError):
            publication = "reused"
        _fsync_directory(directory_fd)
        observed = _read_secure_leaf(
            directory_fd,
            output_name,
            label="published stage bindings output",
        )
        if observed.payload != preflight.payload:
            raise StageBindingAssemblyError(
                "published stage bindings output differs"
            )
        _unlink_exact_temporary(
            directory_fd,
            temporary_name,
            preflight.payload,
        )
        return publication
    finally:
        os.close(directory_fd)


def _confirmation(operation_id: str, release_sha: str) -> str:
    return (
        "assemble-production-shadow-stage-bindings:"
        f"{operation_id}:{release_sha}"
    )


def _summary(
    preflight: AssemblyPreflight,
    *,
    status: str,
) -> dict[str, Any]:
    roles = preflight.document["roles"]
    return {
        "status": status,
        "schema": STAGE_BINDINGS_SCHEMA,
        "operation_id": preflight.document["operation_id"],
        "release_sha": preflight.document["release_sha"],
        "roles": {
            role: {
                "input_sha256": preflight.role_input_sha256s[role],
                "stage_operation_manifest_sha256": roles[role][
                    "stage_operation_manifest_sha256"
                ],
                "stage_attestation_sha256": roles[role][
                    "stage_attestation_sha256"
                ],
                "runtime_image_id_count": len(
                    roles[role]["runtime_image_ids"]
                ),
            }
            for role in ALL_ROLES
        },
        "output_sha256": preflight.payload_sha256,
        "output_bytes": len(preflight.payload),
        "output_preflight_state": preflight.output_state,
        "required_confirmation": _confirmation(
            preflight.document["operation_id"],
            preflight.document["release_sha"],
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bot-fi", required=True, type=Path)
    parser.add_argument("--webapp-fi", required=True, type=Path)
    parser.add_argument("--webapp-ir", required=True, type=Path)
    parser.add_argument("--witness", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm")
    arguments = parser.parse_args(argv)
    role_paths = {
        "bot_fi": arguments.bot_fi,
        "webapp_fi": arguments.webapp_fi,
        "webapp_ir": arguments.webapp_ir,
        "witness": arguments.witness,
    }
    try:
        preflight = preflight_assembly(role_paths, arguments.output)
        if not arguments.apply:
            if arguments.confirm is not None:
                raise StageBindingAssemblyError(
                    "--confirm is valid only with --apply"
                )
            result = _summary(preflight, status="planned")
        else:
            required = _confirmation(
                preflight.document["operation_id"],
                preflight.document["release_sha"],
            )
            if arguments.confirm != required:
                raise StageBindingAssemblyError(
                    "apply confirmation differs from the exact "
                    "operation/release confirmation"
                )
            publication = publish_assembly(preflight)
            result = _summary(preflight, status=publication)
    except StageBindingAssemblyError as exc:
        result = {"status": "blocked", "error": str(exc)}
        print(_canonical_json(result).decode("ascii"))
        return 1
    print(_canonical_json(result).decode("ascii"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
