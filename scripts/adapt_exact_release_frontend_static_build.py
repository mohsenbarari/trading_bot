#!/usr/bin/env python3
"""Derive one controller-bound static manifest from an exact-release build.

This is a local controller-side bridge between the exact-release frontend
builder and the existing WebApp-FI source-adoption package.  It verifies the
root-only candidate and its local-only build receipt, binds them to the
canonical campaign binding, and emits the pre-existing
``expected-static-assets-v2`` shape.  It does not publish, sign, transfer,
install, or execute the build output.

The exact-release receipt is deliberately unsigned local preparation evidence.
This adapter therefore keeps that fact explicit: its output is suitable only
as an input to the separately controller-signed source-adoption package.  A
caller cannot select a release, tree, campaign, or output directory through
this command; those identities come from the receipt and the canonical
campaign binding and must agree exactly.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import tarfile
from typing import Any, Mapping, Sequence


EXACT_BUILD_SCHEMA = "gold-trade-exact-release-frontend-static-build-v1"
EXACT_BUILD_RECEIPT_NAME = "exact-release-frontend-static-build-receipt.json"
EXACT_BUILD_OUTPUT_DIRECTORY_NAME = "static-output"
EXPECTED_STATIC_ASSETS_SCHEMA = "gold-trade-webapp-fi-expected-static-assets-v2"
EXPECTED_STATIC_ASSETS_ROOT = "mini_app_dist"

MAX_EXACT_BUILD_RECEIPT_BYTES = 2 * 1024 * 1024
MAX_EXPECTED_STATIC_ASSETS_MANIFEST_BYTES = 8 * 1024 * 1024
MAX_STATIC_FILES = 100_000
MAX_STATIC_FILE_BYTES = 100 * 1024 * 1024
MAX_STATIC_ARCHIVE_BYTES = 100 * 1024 * 1024
MAX_STATIC_PATH_BYTES = 512

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
GIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


class ExactReleaseStaticAdapterError(RuntimeError):
    """The exact-release build cannot safely become an FI static manifest."""


def canonical_json_bytes(value: Mapping[str, Any] | Sequence[Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ExactReleaseStaticAdapterError("JSON input contains duplicate keys")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ExactReleaseStaticAdapterError("JSON input contains an unsupported constant")


def _require_root_execution() -> None:
    if os.geteuid() != 0:
        raise ExactReleaseStaticAdapterError("exact-release static adaptation must run as root")


def _require_absolute(path: Path, *, field: str) -> Path:
    path = Path(path)
    if (
        not path.is_absolute()
        or "\x00" in str(path)
        or any(part in {"", ".", ".."} for part in path.parts[1:])
        or str(path) != os.path.normpath(str(path))
    ):
        raise ExactReleaseStaticAdapterError(f"{field} must be an absolute canonical path")
    return path


def _require_safe_ancestors(path: Path, *, field: str) -> None:
    """Require a root-controlled lookup path before opening a trusted input."""

    path = _require_absolute(path, field=field)
    current = Path(path.anchor)
    for component in path.parts[1:]:
        current /= component
        try:
            state = current.lstat()
        except OSError as exc:
            raise ExactReleaseStaticAdapterError(f"{field} ancestor does not exist") from exc
        mode = stat.S_IMODE(state.st_mode)
        if (
            stat.S_ISLNK(state.st_mode)
            or not stat.S_ISDIR(state.st_mode)
            or state.st_uid != 0
            or ((mode & 0o022) and not (state.st_mode & stat.S_ISVTX))
        ):
            raise ExactReleaseStaticAdapterError(f"{field} has an unsafe ancestor")


def _require_private_directory(path: Path, *, field: str) -> Path:
    path = _require_absolute(path, field=field)
    _require_safe_ancestors(path.parent, field=field)
    try:
        before = path.lstat()
        resolved = path.resolve(strict=True)
        state = resolved.lstat()
    except OSError as exc:
        raise ExactReleaseStaticAdapterError(f"cannot inspect {field}") from exc
    if (
        resolved != path
        or stat.S_ISLNK(before.st_mode)
        or stat.S_ISLNK(state.st_mode)
        or not stat.S_ISDIR(state.st_mode)
        or state.st_uid != 0
        or stat.S_IMODE(state.st_mode) != 0o700
    ):
        raise ExactReleaseStaticAdapterError(f"{field} must be one root-only mode 0700 non-symlink directory")
    return resolved


def _require_private_file(path: Path, *, field: str, maximum_bytes: int) -> Path:
    path = _require_absolute(path, field=field)
    _require_private_directory(path.parent, field=f"{field} parent")
    try:
        before = path.lstat()
        resolved = path.resolve(strict=True)
        state = resolved.lstat()
    except OSError as exc:
        raise ExactReleaseStaticAdapterError(f"cannot inspect {field}") from exc
    if (
        resolved != path
        or stat.S_ISLNK(before.st_mode)
        or stat.S_ISLNK(state.st_mode)
        or not stat.S_ISREG(state.st_mode)
        or state.st_uid != 0
        or stat.S_IMODE(state.st_mode) != 0o600
        or state.st_nlink != 1
        or not 1 <= state.st_size <= maximum_bytes
    ):
        raise ExactReleaseStaticAdapterError(f"{field} must be one bounded root-only mode 0600 regular non-symlink file")
    return resolved


def _read_private_file(path: Path, *, field: str, maximum_bytes: int) -> bytes:
    path = _require_private_file(path, field=field, maximum_bytes=maximum_bytes)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ExactReleaseStaticAdapterError(f"cannot securely open {field}") from exc
    try:
        before = path.lstat()
        opened = os.fstat(descriptor)
        identity = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_uid != 0
            or stat.S_IMODE(opened.st_mode) != 0o600
            or opened.st_nlink != 1
            or not 1 <= opened.st_size <= maximum_bytes
            or any(getattr(before, item) != getattr(opened, item) for item in identity)
        ):
            raise ExactReleaseStaticAdapterError(f"{field} changed while being opened")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(64 * 1024, maximum_bytes + 1 - total))
            if not chunk:
                break
            total += len(chunk)
            if total > maximum_bytes:
                raise ExactReleaseStaticAdapterError(f"{field} exceeds its size bound")
            chunks.append(chunk)
        after = os.fstat(descriptor)
        if total != opened.st_size or any(getattr(after, item) != getattr(opened, item) for item in identity):
            raise ExactReleaseStaticAdapterError(f"{field} changed while being read")
        return b"".join(chunks)
    except OSError as exc:
        raise ExactReleaseStaticAdapterError(f"cannot read {field}") from exc
    finally:
        os.close(descriptor)


def _parse_canonical_json(payload: bytes, *, field: str) -> dict[str, Any]:
    try:
        value = json.loads(
            payload.decode("ascii"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ExactReleaseStaticAdapterError(f"{field} is not strict ASCII JSON") from exc
    if not isinstance(value, dict) or payload != canonical_json_bytes(value) + b"\n":
        raise ExactReleaseStaticAdapterError(f"{field} is not canonical JSON")
    return value


def _require_sha256(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        raise ExactReleaseStaticAdapterError(f"{field} is invalid")
    return value


def _require_git_sha(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not GIT_SHA_RE.fullmatch(value):
        raise ExactReleaseStaticAdapterError(f"{field} is invalid")
    return value


def _require_nonnegative_int(value: object, *, field: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ExactReleaseStaticAdapterError(f"{field} is invalid")
    return value


def _reject_secret_or_url(value: object, *, field: str = "receipt") -> None:
    forbidden_key_parts = (
        "access_key",
        "authorization",
        "credential",
        "password",
        "private",
        "secret",
        "token",
        "url",
        "uri",
        "endpoint",
    )
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str) or any(part in key.lower() for part in forbidden_key_parts):
                raise ExactReleaseStaticAdapterError(f"{field} contains a prohibited key")
            _reject_secret_or_url(child, field=f"{field}.{key}")
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            _reject_secret_or_url(child, field=f"{field}[{index}]")
        return
    if isinstance(value, (bool, int)) or value is None:
        return
    if not isinstance(value, str):
        raise ExactReleaseStaticAdapterError(f"{field} contains an invalid value")
    lowered = value.lower()
    if (
        "://" in lowered
        or "presigned" in lowered
        or "-----begin" in lowered
        or lowered.startswith(("sk-", "akia", "age-secret-"))
    ):
        raise ExactReleaseStaticAdapterError(f"{field} contains a URL or secret-shaped value")


def _safe_static_path(value: object) -> str:
    if not isinstance(value, str) or not value or len(value.encode("utf-8")) > MAX_STATIC_PATH_BYTES:
        raise ExactReleaseStaticAdapterError("static build output path is invalid")
    if any(ord(character) < 0x20 or ord(character) > 0x7E for character in value):
        raise ExactReleaseStaticAdapterError("static build output path must be printable ASCII")
    pure = PurePosixPath(value)
    if (
        pure.as_posix() != value
        or pure.is_absolute()
        or "\\" in value
        or any(part in {"", ".", ".."} for part in pure.parts)
    ):
        raise ExactReleaseStaticAdapterError("static build output path is invalid")
    try:
        info = tarfile.TarInfo(value)
        info.size = 0
        info.mode = 0o644
        info.uid = 0
        info.gid = 0
        info.uname = ""
        info.gname = ""
        info.mtime = 0
        info.tobuf(format=tarfile.USTAR_FORMAT, encoding="ascii", errors="strict")
    except (UnicodeError, ValueError) as exc:
        raise ExactReleaseStaticAdapterError("static build output path cannot be represented in USTAR") from exc
    return value


def _validate_output_file_state(state: os.stat_result) -> None:
    if (
        not stat.S_ISREG(state.st_mode)
        or state.st_uid != 0
        or stat.S_IMODE(state.st_mode) & (0o022 | 0o7000)
        or state.st_nlink != 1
        or state.st_size < 0
        or state.st_size > MAX_STATIC_FILE_BYTES
    ):
        raise ExactReleaseStaticAdapterError("static build output contains an unsafe file")


def _hash_output_file(path: Path, *, relative: str) -> dict[str, Any]:
    try:
        before = path.lstat()
    except OSError as exc:
        raise ExactReleaseStaticAdapterError("cannot inspect static build output file") from exc
    _validate_output_file_state(before)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ExactReleaseStaticAdapterError("cannot securely open static build output file") from exc
    try:
        opened = os.fstat(descriptor)
        _validate_output_file_state(opened)
        identity = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        if any(getattr(before, item) != getattr(opened, item) for item in identity):
            raise ExactReleaseStaticAdapterError("static build output file changed while being opened")
        digest = hashlib.sha256()
        total = 0
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_STATIC_FILE_BYTES:
                raise ExactReleaseStaticAdapterError("static build output file exceeds its size bound")
            digest.update(chunk)
        after = os.fstat(descriptor)
        if total != opened.st_size or any(getattr(after, item) != getattr(opened, item) for item in identity):
            raise ExactReleaseStaticAdapterError("static build output file changed while being hashed")
        return {"path": relative, "sha256": digest.hexdigest(), "bytes": total}
    except OSError as exc:
        raise ExactReleaseStaticAdapterError("cannot read static build output file") from exc
    finally:
        os.close(descriptor)


def _scan_static_output(path: Path) -> list[dict[str, Any]]:
    path = _require_private_directory(path, field="exact-release static build output")
    files: list[dict[str, Any]] = []
    total = 0

    def visit(directory: Path, prefix: str) -> None:
        try:
            state = directory.lstat()
        except OSError as exc:
            raise ExactReleaseStaticAdapterError("cannot inspect static build output directory") from exc
        if (
            stat.S_ISLNK(state.st_mode)
            or not stat.S_ISDIR(state.st_mode)
            or state.st_uid != 0
            or stat.S_IMODE(state.st_mode) & (0o022 | 0o7000)
        ):
            raise ExactReleaseStaticAdapterError("static build output contains an unsafe directory")
        try:
            with os.scandir(directory) as scanner:
                entries = sorted(scanner, key=lambda item: item.name)
        except OSError as exc:
            raise ExactReleaseStaticAdapterError("cannot enumerate static build output") from exc
        for entry in entries:
            relative = _safe_static_path(entry.name if not prefix else prefix + "/" + entry.name)
            child = directory / entry.name
            try:
                state = child.lstat()
            except OSError as exc:
                raise ExactReleaseStaticAdapterError("cannot inspect static build output entry") from exc
            if stat.S_ISDIR(state.st_mode):
                visit(child, relative)
            elif stat.S_ISREG(state.st_mode):
                files.append(_hash_output_file(child, relative=relative))
            else:
                raise ExactReleaseStaticAdapterError(
                    "static build output may contain only directories and regular files"
                )

    visit(path, "")
    if not files:
        raise ExactReleaseStaticAdapterError("exact-release static build output must contain files")
    if len(files) > MAX_STATIC_FILES:
        raise ExactReleaseStaticAdapterError("exact-release static build output has too many files")
    if [item["path"] for item in files] != sorted(item["path"] for item in files):
        raise ExactReleaseStaticAdapterError("exact-release static build output enumeration is not deterministic")
    total = sum(item["bytes"] for item in files)
    archive_upper_bound = sum(
        tarfile.BLOCKSIZE
        + ((item["bytes"] + tarfile.BLOCKSIZE - 1) // tarfile.BLOCKSIZE) * tarfile.BLOCKSIZE
        for item in files
    ) + tarfile.BLOCKSIZE * 2
    archive_upper_bound += -archive_upper_bound % tarfile.RECORDSIZE
    if total < 1 or archive_upper_bound > MAX_STATIC_ARCHIVE_BYTES:
        raise ExactReleaseStaticAdapterError("exact-release static build output exceeds the FI static archive bound")
    return files


def _validate_output_descriptor(value: object) -> dict[str, Any]:
    expected = {"files_sha256", "file_count", "bytes", "files"}
    if not isinstance(value, Mapping) or set(value) != expected:
        raise ExactReleaseStaticAdapterError("exact-release static build output descriptor is unsupported")
    raw_files = value.get("files")
    if not isinstance(raw_files, list) or not raw_files or len(raw_files) > MAX_STATIC_FILES:
        raise ExactReleaseStaticAdapterError("exact-release static build output files are invalid")
    files: list[dict[str, Any]] = []
    previous = ""
    total = 0
    for item in raw_files:
        if not isinstance(item, Mapping) or set(item) != {"path", "sha256", "bytes"}:
            raise ExactReleaseStaticAdapterError("exact-release static build output file is invalid")
        path = _safe_static_path(item.get("path"))
        digest = _require_sha256(item.get("sha256"), field="exact-release static build output file sha256")
        bytes_value = _require_nonnegative_int(
            item.get("bytes"), field="exact-release static build output file bytes"
        )
        if bytes_value > MAX_STATIC_FILE_BYTES or (previous and path <= previous):
            raise ExactReleaseStaticAdapterError("exact-release static build output file is invalid")
        previous = path
        total += bytes_value
        files.append({"path": path, "sha256": digest, "bytes": bytes_value})
    if total < 1:
        raise ExactReleaseStaticAdapterError("exact-release static build output files are invalid")
    if value.get("files_sha256") != sha256_bytes(canonical_json_bytes(files)):
        raise ExactReleaseStaticAdapterError("exact-release static build output file hash is invalid")
    if (
        _require_nonnegative_int(value.get("file_count"), field="exact-release static build output file count")
        != len(files)
        or _require_nonnegative_int(value.get("bytes"), field="exact-release static build output bytes") != total
    ):
        raise ExactReleaseStaticAdapterError("exact-release static build output descriptor is inconsistent")
    return {"files": files, "files_sha256": value["files_sha256"], "file_count": len(files), "bytes": total}


def _validate_exact_build_receipt(payload: bytes) -> dict[str, Any]:
    _reject_secret_or_url(_parse_canonical_json(payload, field="exact-release static build receipt"))
    value = _parse_canonical_json(payload, field="exact-release static build receipt")
    expected = {
        "schema",
        "status",
        "release_sha",
        "release_tree",
        "source",
        "toolchain",
        "lock",
        "offline_dependency_input",
        "runtime_closure",
        "build_environment_sha256",
        "sandbox_preflight",
        "network_action",
        "object_storage_action",
        "ssh_action",
        "docker_action",
        "service_changed",
        "current_changed",
        "receipt_authority",
        "transport_authority",
        "release_archive",
        "materialized_source",
        "build",
        "output",
        "receipt_sha256",
    }
    if (
        set(value) != expected
        or value.get("schema") != EXACT_BUILD_SCHEMA
        or value.get("status") != "prepared"
    ):
        raise ExactReleaseStaticAdapterError("exact-release static build receipt is unsupported")
    unsigned = {key: child for key, child in value.items() if key != "receipt_sha256"}
    if value.get("receipt_sha256") != sha256_bytes(canonical_json_bytes(unsigned)):
        raise ExactReleaseStaticAdapterError("exact-release static build receipt checksum is invalid")
    release_sha = _require_git_sha(value.get("release_sha"), field="exact-release release_sha")
    release_tree = _require_git_sha(value.get("release_tree"), field="exact-release release_tree")
    _require_sha256(value.get("build_environment_sha256"), field="exact-release build environment checksum")
    for field in (
        "network_action",
        "object_storage_action",
        "ssh_action",
        "docker_action",
        "service_changed",
        "current_changed",
    ):
        if value.get(field) is not False:
            raise ExactReleaseStaticAdapterError("exact-release static build receipt records a forbidden side effect")
    if value.get("sandbox_preflight") != {
        "mount_network_pid_namespace": "passed",
        "privilege_drop": "passed",
    }:
        raise ExactReleaseStaticAdapterError("exact-release static build receipt sandbox preflight is invalid")
    if value.get("receipt_authority") != {
        "unsigned": True,
        "provenance": "local-preparation-only-not-transport-provenance",
        "integration_status": "blocked-pending-external-controller-signature",
    }:
        raise ExactReleaseStaticAdapterError("exact-release static build receipt authority is invalid")
    if value.get("transport_authority") != {
        "local_receipt_only": True,
        "external_controller_signature_required": True,
        "transport_or_install_authorized": False,
    }:
        raise ExactReleaseStaticAdapterError("exact-release static build receipt transport authority is invalid")
    source = value.get("source")
    if not isinstance(source, Mapping) or set(source) != {"tree_file_count", "repository_path_sha256"}:
        raise ExactReleaseStaticAdapterError("exact-release static build receipt source is invalid")
    _require_nonnegative_int(source.get("tree_file_count"), field="exact-release source tree file count", minimum=1)
    _require_sha256(source.get("repository_path_sha256"), field="exact-release source repository checksum")
    for label, descriptor, fields in (
        ("lock", value.get("lock"), {"package_json_sha256", "package_json_bytes", "package_lock_sha256", "package_lock_bytes"}),
        (
            "offline dependency input",
            value.get("offline_dependency_input"),
            {"archive_sha256", "archive_bytes", "files_sha256", "file_count", "bytes"},
        ),
        ("release archive", value.get("release_archive"), {"sha256", "bytes"}),
        ("materialized source", value.get("materialized_source"), {"files_sha256", "file_count", "package_json_sha256", "package_lock_sha256"}),
    ):
        if not isinstance(descriptor, Mapping) or set(descriptor) != fields:
            raise ExactReleaseStaticAdapterError(f"exact-release static build receipt {label} is invalid")
        for key, child in descriptor.items():
            if key.endswith("sha256"):
                _require_sha256(child, field=f"exact-release {label} {key}")
            else:
                _require_nonnegative_int(child, field=f"exact-release {label} {key}", minimum=1)
    toolchain = value.get("toolchain")
    if not isinstance(toolchain, Mapping) or set(toolchain) != {"fixed_policy_sha256", "git", "node", "npm", "sandbox"}:
        raise ExactReleaseStaticAdapterError("exact-release static build receipt toolchain is invalid")
    _require_sha256(toolchain.get("fixed_policy_sha256"), field="exact-release fixed policy checksum")
    for label in ("git", "node"):
        descriptor = toolchain.get(label)
        if not isinstance(descriptor, Mapping) or set(descriptor) != {"path_sha256", "sha256", "version"}:
            raise ExactReleaseStaticAdapterError("exact-release static build receipt toolchain is invalid")
        _require_sha256(descriptor.get("path_sha256"), field=f"exact-release {label} path checksum")
        _require_sha256(descriptor.get("sha256"), field=f"exact-release {label} checksum")
        if not isinstance(descriptor.get("version"), str) or not descriptor["version"]:
            raise ExactReleaseStaticAdapterError("exact-release static build receipt toolchain is invalid")
    npm = toolchain.get("npm")
    if not isinstance(npm, Mapping) or set(npm) != {
        "path_sha256", "sha256", "version", "runtime_path_sha256", "runtime_tree_sha256"
    }:
        raise ExactReleaseStaticAdapterError("exact-release static build receipt npm toolchain is invalid")
    for key in ("path_sha256", "sha256", "runtime_path_sha256", "runtime_tree_sha256"):
        _require_sha256(npm.get(key), field=f"exact-release npm {key}")
    if not isinstance(npm.get("version"), str) or not npm["version"]:
        raise ExactReleaseStaticAdapterError("exact-release static build receipt npm toolchain is invalid")
    sandbox = toolchain.get("sandbox")
    if not isinstance(sandbox, Mapping) or set(sandbox) != {"python", "unshare", "setpriv", "mount", "policy_sha256"}:
        raise ExactReleaseStaticAdapterError("exact-release static build receipt sandbox toolchain is invalid")
    _require_sha256(sandbox.get("policy_sha256"), field="exact-release sandbox policy checksum")
    for label in ("python", "unshare", "setpriv", "mount"):
        descriptor = sandbox.get(label)
        if not isinstance(descriptor, Mapping) or set(descriptor) != {"path_sha256", "sha256", "version"}:
            raise ExactReleaseStaticAdapterError("exact-release static build receipt sandbox toolchain is invalid")
        _require_sha256(descriptor.get("path_sha256"), field=f"exact-release sandbox {label} path checksum")
        _require_sha256(descriptor.get("sha256"), field=f"exact-release sandbox {label} checksum")
        if not isinstance(descriptor.get("version"), str) or not descriptor["version"]:
            raise ExactReleaseStaticAdapterError("exact-release static build receipt sandbox toolchain is invalid")
    runtime_closure = value.get("runtime_closure")
    if not isinstance(runtime_closure, Mapping) or set(runtime_closure) != {
        "manifest_sha256",
        "setpriv_sha256",
        "sh_sha256",
        "env_sha256",
    }:
        raise ExactReleaseStaticAdapterError("exact-release static build receipt runtime closure is invalid")
    for key in ("manifest_sha256", "setpriv_sha256", "sh_sha256", "env_sha256"):
        _require_sha256(runtime_closure.get(key), field=f"exact-release runtime closure {key}")
    build = value.get("build")
    expected_build = {
        "environment_sha256",
        "lifecycle_scripts_enabled",
        "mount_namespace_required",
        "network_namespace_required",
        "pid_namespace_required",
        "privilege_drop_required",
        "rlimit_nproc",
        "rlimit_as_bytes",
        "rlimit_cpu_seconds",
        "rlimit_fsize_bytes",
    }
    if not isinstance(build, Mapping) or set(build) != expected_build:
        raise ExactReleaseStaticAdapterError("exact-release static build receipt build record is invalid")
    _require_sha256(build.get("environment_sha256"), field="exact-release build environment checksum")
    for key in (
        "lifecycle_scripts_enabled",
        "mount_namespace_required",
        "network_namespace_required",
        "pid_namespace_required",
        "privilege_drop_required",
    ):
        expected_value = key != "lifecycle_scripts_enabled"
        if build.get(key) is not expected_value:
            raise ExactReleaseStaticAdapterError("exact-release static build receipt build record is invalid")
    for key in ("rlimit_nproc", "rlimit_as_bytes", "rlimit_cpu_seconds", "rlimit_fsize_bytes"):
        _require_nonnegative_int(build.get(key), field=f"exact-release build {key}", minimum=1)
    output = _validate_output_descriptor(value.get("output"))
    return {
        "release_sha": release_sha,
        "release_tree": release_tree,
        "output": output,
        "receipt_sha256": value["receipt_sha256"],
    }


def _load_exact_sibling(filename: str, module_name: str) -> Any:
    if not isinstance(filename, str) or Path(filename).name != filename or filename in {"", ".", ".."}:
        raise ExactReleaseStaticAdapterError("required sibling filename is unsafe")
    source = Path(__file__).resolve(strict=True)
    _require_safe_ancestors(source.parent, field="adapter source")
    try:
        state = source.lstat()
        sibling = source.with_name(filename)
        sibling_state = sibling.lstat()
        resolved = sibling.resolve(strict=True)
    except OSError as exc:
        raise ExactReleaseStaticAdapterError("cannot inspect required campaign binding helper") from exc
    if (
        stat.S_ISLNK(state.st_mode)
        or not stat.S_ISREG(state.st_mode)
        or state.st_uid != 0
        or stat.S_IMODE(state.st_mode) & 0o022
        or resolved != sibling
        or stat.S_ISLNK(sibling_state.st_mode)
        or not stat.S_ISREG(sibling_state.st_mode)
        or sibling_state.st_uid != 0
        or stat.S_IMODE(sibling_state.st_mode) & 0o022
    ):
        raise ExactReleaseStaticAdapterError("required campaign binding helper is unsafe")
    spec = importlib.util.spec_from_file_location(module_name, sibling)
    if spec is None or spec.loader is None:  # pragma: no cover - repository invariant.
        raise ExactReleaseStaticAdapterError("cannot load required campaign binding helper")
    module = importlib.util.module_from_spec(spec)
    previous = sys.modules.get(module_name)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        if previous is None:
            sys.modules.pop(module_name, None)
        else:
            sys.modules[module_name] = previous
        raise
    loaded = getattr(module, "__file__", None)
    if not isinstance(loaded, str) or Path(loaded).resolve(strict=True) != sibling:
        raise ExactReleaseStaticAdapterError("campaign binding helper loaded from an unexpected path")
    return module


def _load_campaign_binding(path: Path) -> Any:
    binding = _load_exact_sibling(
        "webapp_fi_source_campaign_binding.py", "_exact_release_static_adapter_campaign_binding"
    )
    try:
        return binding.load_campaign_binding(Path(path))
    except Exception as exc:
        raise ExactReleaseStaticAdapterError("canonical campaign binding is invalid") from exc


def _write_new_private_file(path: Path, payload: bytes, *, field: str) -> None:
    path = _require_absolute(path, field=field)
    parent = _require_private_directory(path.parent, field=f"{field} parent")
    if path.parent != parent or path.exists() or path.is_symlink():
        raise ExactReleaseStaticAdapterError(f"refusing to overwrite {field}")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as exc:
        raise ExactReleaseStaticAdapterError(f"cannot create {field}") from exc
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        directory_fd = os.open(parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except OSError as exc:
        raise ExactReleaseStaticAdapterError(f"cannot write {field}") from exc


def derive_expected_static_assets_manifest(
    *,
    exact_build_candidate: Path,
    exact_build_receipt: Path,
    campaign_binding_path: Path,
    destination: Path,
    apply: bool,
) -> dict[str, Any]:
    """Verify one local exact-build candidate and derive a v2 FI manifest."""

    _require_root_execution()
    candidate = _require_private_directory(Path(exact_build_candidate), field="exact-release build candidate")
    expected_receipt_path = candidate / EXACT_BUILD_RECEIPT_NAME
    receipt_path = _require_absolute(Path(exact_build_receipt), field="exact-release build receipt")
    if receipt_path != expected_receipt_path:
        raise ExactReleaseStaticAdapterError("exact-release build receipt is not at the fixed candidate path")
    receipt_payload = _read_private_file(
        receipt_path,
        field="exact-release static build receipt",
        maximum_bytes=MAX_EXACT_BUILD_RECEIPT_BYTES,
    )
    evidence = _validate_exact_build_receipt(receipt_payload)
    binding = _load_campaign_binding(Path(campaign_binding_path))
    if (
        evidence["release_sha"] != binding.application_release_sha
        or evidence["release_tree"] != binding.application_release_tree
    ):
        raise ExactReleaseStaticAdapterError("exact-release static build receipt is not bound to the canonical campaign")
    output_root = candidate / EXACT_BUILD_OUTPUT_DIRECTORY_NAME
    observed_files = _scan_static_output(output_root)
    output = evidence["output"]
    if (
        observed_files != output["files"]
        or sha256_bytes(canonical_json_bytes(observed_files)) != output["files_sha256"]
        or len(observed_files) != output["file_count"]
        or sum(item["bytes"] for item in observed_files) != output["bytes"]
    ):
        raise ExactReleaseStaticAdapterError("exact-release static build output differs from its receipt")
    manifest: dict[str, Any] = {
        "schema": EXPECTED_STATIC_ASSETS_SCHEMA,
        "status": "prepared",
        "campaign_id": binding.campaign_id,
        "application": {
            "release_sha": binding.application_release_sha,
            "release_tree": binding.application_release_tree,
            "expected_alembic_revision": binding.expected_alembic_revision,
        },
        "tooling": {
            "control_commit": binding.control_commit,
            "control_tree": binding.control_tree,
        },
        "static_root": EXPECTED_STATIC_ASSETS_ROOT,
        "files": observed_files,
        "files_sha256": sha256_bytes(canonical_json_bytes(observed_files)),
    }
    payload = canonical_json_bytes(manifest) + b"\n"
    if len(payload) > MAX_EXPECTED_STATIC_ASSETS_MANIFEST_BYTES:
        raise ExactReleaseStaticAdapterError("expected static assets manifest exceeds its size bound")
    _reject_secret_or_url(manifest, field="expected static assets manifest")
    destination = _require_absolute(Path(destination), field="expected static assets manifest destination")
    destination_parent = _require_private_directory(
        destination.parent, field="expected static assets manifest destination parent"
    )
    if (
        destination.parent != destination_parent
        or destination.exists()
        or destination.is_symlink()
    ):
        raise ExactReleaseStaticAdapterError("expected static assets manifest destination must be a new child of a root-only directory")
    if not apply:
        return {
            "schema": EXPECTED_STATIC_ASSETS_SCHEMA,
            "status": "planned",
            "campaign_id": binding.campaign_id,
            "destination": str(destination),
            "manifest_sha256": sha256_bytes(payload),
            "exact_build_receipt_sha256": evidence["receipt_sha256"],
            "file_count": len(observed_files),
            "files_sha256": manifest["files_sha256"],
            "network_action": False,
            "object_storage_action": False,
            "ssh_action": False,
            "docker_action": False,
            "service_changed": False,
            "current_changed": False,
        }
    # Re-read every mutable local input immediately before create-only output.
    final_receipt = _validate_exact_build_receipt(
        _read_private_file(
            receipt_path,
            field="exact-release static build receipt",
            maximum_bytes=MAX_EXACT_BUILD_RECEIPT_BYTES,
        )
    )
    final_binding = _load_campaign_binding(Path(campaign_binding_path))
    final_files = _scan_static_output(output_root)
    if (
        final_receipt != evidence
        or (
            final_binding.campaign_id,
            final_binding.application_release_sha,
            final_binding.application_release_tree,
            final_binding.expected_alembic_revision,
            final_binding.control_commit,
            final_binding.control_tree,
        )
        != (
            binding.campaign_id,
            binding.application_release_sha,
            binding.application_release_tree,
            binding.expected_alembic_revision,
            binding.control_commit,
            binding.control_tree,
        )
        or final_files != observed_files
    ):
        raise ExactReleaseStaticAdapterError("exact-release static build inputs changed before manifest creation")
    _write_new_private_file(destination, payload, field="expected static assets manifest")
    return {
        "schema": EXPECTED_STATIC_ASSETS_SCHEMA,
        "status": "prepared",
        "campaign_id": binding.campaign_id,
        "destination": str(destination),
        "manifest_sha256": sha256_bytes(payload),
        "exact_build_receipt_sha256": evidence["receipt_sha256"],
        "file_count": len(observed_files),
        "files_sha256": manifest["files_sha256"],
        "network_action": False,
        "object_storage_action": False,
        "ssh_action": False,
        "docker_action": False,
        "service_changed": False,
        "current_changed": False,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exact-build-candidate", type=Path, required=True)
    parser.add_argument("--exact-build-receipt", type=Path, required=True)
    parser.add_argument("--campaign-binding", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = derive_expected_static_assets_manifest(
            exact_build_candidate=args.exact_build_candidate,
            exact_build_receipt=args.exact_build_receipt,
            campaign_binding_path=args.campaign_binding,
            destination=args.destination,
            apply=args.apply,
        )
    except ExactReleaseStaticAdapterError as exc:
        print(
            json.dumps({"status": "blocked", "error": str(exc), "error_class": type(exc).__name__}, sort_keys=True),
            file=sys.stderr,
        )
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point.
    raise SystemExit(main())
