#!/usr/bin/env python3
"""Attest one dedicated, offline controller runtime closure.

This verifier deliberately accepts neither the Writer-Witness runtime nor an
ambient Python/pip cache.  The only accepted closure is a controller-only,
root-controlled directory containing the three packages needed to verify the
production-shadow convergence source set.  It is stdlib-only so the launcher
can invoke it before any project or third-party import.

Operational prerequisite: the controller must run the fixed
``/usr/bin/python3.12`` CPython 3.12.3 x86_64/glibc 2.39 interpreter.  A
different interpreter is a fail-closed condition, not a compatibility mode.

The committed policy is a release input.  A future offline builder derives the
installed ``runtime-closure-manifest.json`` from that policy, one exact Git
release, and an independently trusted wheel input.  This module verifies the
derived manifest and the materialized files; it never downloads, installs, or
contacts a host.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import importlib
import importlib.machinery
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import stat
import sys
from typing import Any, Mapping, Sequence


RUNTIME_CLOSURE_SCHEMA = "production-shadow-controller-runtime-closure-v1"
RUNTIME_NAMESPACE = "production-shadow-controller-only"
RUNTIME_MANIFEST_FILENAME = "runtime-closure-manifest.json"
SITE_PACKAGES_DIRECTORY = "site-packages"
SYSTEM_PYTHON = "/usr/bin/python3.12"
EXPECTED_PYTHON_VERSION = (3, 12, 3)
EXPECTED_STDLIB_PATHS = (
    "/usr/lib/python312.zip",
    "/usr/lib/python3.12",
    "/usr/lib/python3.12/lib-dynload",
)
SOURCE_POLICY_RELATIVE = (
    "deploy/production-shadow-controller-runtime/runtime-closure-policy.json"
)
WHEELHOUSE_MANIFEST_RELATIVE = (
    "deploy/production-shadow-controller-runtime/wheelhouse.sha256"
)
MAX_MANIFEST_BYTES = 4 * 1024 * 1024
MAX_RUNTIME_FILE_BYTES = 64 * 1024 * 1024
SHA256_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
SHA40_RE = re.compile(r"^[0-9a-f]{40}$", re.ASCII)
SAFE_RELATIVE_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._+\-]*", re.ASCII)

# This contract is intentionally duplicated here instead of imported from a
# Writer-Witness file.  Its wheel identities are controller-specific inputs.
REQUIRED_PACKAGES: tuple[dict[str, str], ...] = (
    {
        "name": "cffi",
        "version": "2.1.0",
        "wheel": "cffi-2.1.0-cp312-cp312-manylinux2014_x86_64.manylinux_2_17_x86_64.whl",
        "sha256": "1e9f50d192a3e525b15a75ab5114e442d83d657b7ec29182a991bc9a88fd3a66",
    },
    {
        "name": "cryptography",
        "version": "41.0.7",
        "wheel": "cryptography-41.0.7-cp37-abi3-manylinux_2_17_x86_64.manylinux2014_x86_64.whl",
        "sha256": "841df4caa01008bad253bce2a6f7b47f86dc9f08df4b433c404def869f590a15",
    },
    {
        "name": "pycparser",
        "version": "3.0",
        "wheel": "pycparser-3.0-py3-none-any.whl",
        "sha256": "b727414169a36b7d524c1c3e31839a521725078d7b2ff038656844266160a992",
    },
)
REQUIRED_IMPORT_ORIGINS = {
    "_cffi_backend": "_cffi_backend.cpython-312-x86_64-linux-gnu.so",
    "cffi": "cffi/__init__.py",
    "cryptography": "cryptography/__init__.py",
    "cryptography.hazmat.bindings._rust": "cryptography/hazmat/bindings/_rust.abi3.so",
    "pycparser": "pycparser/__init__.py",
}
CONTROL_SOURCE_PATHS = frozenset(
    {
        "scripts/produce_production_shadow_convergence_source_set.py",
        "scripts/production_shadow_convergence_source_set_launcher",
        "scripts/production_shadow_convergence_source_set_runtime_bootstrap.py",
        "scripts/verify_production_shadow_controller_runtime_closure.py",
    }
)
MANIFEST_FIELDS = frozenset(
    {
        "schema",
        "namespace",
        "release",
        "python",
        "source_policy_sha256",
        "wheelhouse_manifest_sha256",
        "packages",
        "site_packages",
        "project_sources",
        "control_sources",
        "runtime_binding_sha256",
    }
)
RELEASE_FIELDS = frozenset({"commit_sha", "tree_sha"})
PYTHON_FIELDS = frozenset({"implementation", "major", "minor", "architecture"})
SITE_FIELDS = frozenset({"path", "files", "files_sha256", "import_origins"})


class RuntimeClosureError(RuntimeError):
    """The controller runtime closure cannot be proven safe and exact."""


@dataclass
class RuntimeClosureAttestation:
    manifest: Mapping[str, Any]
    manifest_sha256: str
    release_sha: str
    release_tree_sha: str
    site_packages_root: str
    site_packages_descriptor: int
    site_file_count: int
    project_source_count: int

    def close(self) -> None:
        """Release the held site-packages capability after the producer exits."""

        if self.site_packages_descriptor >= 0:
            try:
                os.close(self.site_packages_descriptor)
            except OSError:
                pass
            self.site_packages_descriptor = -1


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    document: dict[str, Any] = {}
    for key, value in pairs:
        if key in document:
            raise RuntimeClosureError("runtime closure JSON contains duplicate fields")
        document[key] = value
    return document


def canonical_json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise RuntimeClosureError("runtime closure value is not canonical JSON") from exc


def _sha256(value: bytes | Mapping[str, Any]) -> str:
    payload = value if isinstance(value, bytes) else canonical_json_bytes(value)
    return hashlib.sha256(payload).hexdigest()


def _metadata_signature(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_nlink,
        metadata.st_uid,
        metadata.st_gid,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _assert_stable(before: os.stat_result, after: os.stat_result, *, label: str) -> None:
    if _metadata_signature(before) != _metadata_signature(after):
        raise RuntimeClosureError(f"{label} changed during verification")


def _assert_owner(metadata: os.stat_result, *, label: str, expected_uid: int | None) -> None:
    if expected_uid is not None and metadata.st_uid != expected_uid:
        raise RuntimeClosureError(f"{label} has an unexpected owner")
    if metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise RuntimeClosureError(f"{label} is group/world writable")


def _assert_secure_directory(
    descriptor: int,
    *,
    label: str,
    expected_uid: int | None,
) -> os.stat_result:
    try:
        metadata = os.fstat(descriptor)
    except OSError as exc:
        raise RuntimeClosureError(f"cannot inspect {label}") from exc
    if not stat.S_ISDIR(metadata.st_mode):
        raise RuntimeClosureError(f"{label} is not a directory")
    _assert_owner(metadata, label=label, expected_uid=expected_uid)
    return metadata


def _assert_secure_regular(
    descriptor: int,
    *,
    label: str,
    expected_uid: int | None,
    maximum: int = MAX_RUNTIME_FILE_BYTES,
) -> os.stat_result:
    try:
        metadata = os.fstat(descriptor)
    except OSError as exc:
        raise RuntimeClosureError(f"cannot inspect {label}") from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or metadata.st_size < 0
        or metadata.st_size > maximum
    ):
        raise RuntimeClosureError(f"{label} is not a bounded regular file")
    _assert_owner(metadata, label=label, expected_uid=expected_uid)
    return metadata


def _safe_relative(value: str, *, label: str) -> tuple[str, ...]:
    if not isinstance(value, str) or not value or value.startswith("/"):
        raise RuntimeClosureError(f"{label} path is invalid")
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or any(
        part in {"", ".", ".."} or SAFE_RELATIVE_RE.fullmatch(part) is None
        for part in path.parts
    ):
        raise RuntimeClosureError(f"{label} path is invalid")
    return tuple(path.parts)


def _open_child_directory(
    parent: int,
    name: str,
    *,
    label: str,
    expected_uid: int | None,
) -> int:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(name, flags, dir_fd=parent)
    except OSError as exc:
        raise RuntimeClosureError(f"cannot safely open {label}") from exc
    try:
        _assert_secure_directory(descriptor, label=label, expected_uid=expected_uid)
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _open_relative_regular(
    root_descriptor: int,
    relative: str,
    *,
    label: str,
    expected_uid: int | None,
    maximum: int = MAX_RUNTIME_FILE_BYTES,
) -> tuple[int, os.stat_result]:
    parts = _safe_relative(relative, label=label)
    directory = os.dup(root_descriptor)
    try:
        _assert_secure_directory(directory, label=f"{label} root", expected_uid=expected_uid)
        for part in parts[:-1]:
            child = _open_child_directory(
                directory,
                part,
                label=f"{label} directory",
                expected_uid=expected_uid,
            )
            os.close(directory)
            directory = child
        flags = (
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
        )
        try:
            descriptor = os.open(parts[-1], flags, dir_fd=directory)
        except OSError as exc:
            raise RuntimeClosureError(f"cannot safely open {label}") from exc
        try:
            metadata = _assert_secure_regular(
                descriptor,
                label=label,
                expected_uid=expected_uid,
                maximum=maximum,
            )
            return descriptor, metadata
        except Exception:
            os.close(descriptor)
            raise
    finally:
        os.close(directory)


def _read_descriptor(
    descriptor: int,
    before: os.stat_result,
    *,
    label: str,
    maximum: int,
) -> bytes:
    if before.st_size > maximum:
        raise RuntimeClosureError(f"{label} exceeds its safe size limit")
    try:
        os.lseek(descriptor, 0, os.SEEK_SET)
        chunks: list[bytes] = []
        remaining = maximum + 1
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        if len(payload) > maximum:
            raise RuntimeClosureError(f"{label} exceeds its safe size limit")
        _assert_stable(before, os.fstat(descriptor), label=label)
        return payload
    except RuntimeClosureError:
        raise
    except OSError as exc:
        raise RuntimeClosureError(f"cannot safely read {label}") from exc


def _hash_descriptor(
    descriptor: int,
    before: os.stat_result,
    *,
    label: str,
) -> str:
    try:
        os.lseek(descriptor, 0, os.SEEK_SET)
        digest = hashlib.sha256()
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
        _assert_stable(before, os.fstat(descriptor), label=label)
        return digest.hexdigest()
    except RuntimeClosureError:
        raise
    except OSError as exc:
        raise RuntimeClosureError(f"cannot safely hash {label}") from exc


def _strict_json(raw: bytes, *, label: str) -> dict[str, Any]:
    if not raw:
        raise RuntimeClosureError(f"{label} is empty")
    try:
        document = json.loads(
            raw.decode("ascii"),
            object_pairs_hook=_strict_object,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"invalid JSON constant: {value}")
            ),
        )
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeClosureError(f"{label} is not strict ASCII JSON") from exc
    if not isinstance(document, dict) or raw != canonical_json_bytes(document):
        raise RuntimeClosureError(f"{label} is not canonical JSON")
    return document


def _require_sha256(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise RuntimeClosureError(f"{label} is not a SHA-256")
    return value


def _require_sha40(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or SHA40_RE.fullmatch(value) is None:
        raise RuntimeClosureError(f"{label} is not a Git SHA-1")
    return value


def _hash_mapping(value: Mapping[str, str]) -> str:
    return _sha256({key: value[key] for key in sorted(value)})


def _validate_source_mapping(value: Any, *, label: str) -> dict[str, str]:
    if not isinstance(value, dict) or not value:
        raise RuntimeClosureError(f"{label} must be a non-empty source mapping")
    result: dict[str, str] = {}
    for path, digest in value.items():
        if not isinstance(path, str):
            raise RuntimeClosureError(f"{label} path is invalid")
        parts = _safe_relative(path, label=label)
        if parts[0] not in {"core", "scripts"} or not path.endswith(".py") and path not in CONTROL_SOURCE_PATHS:
            raise RuntimeClosureError(f"{label} path is outside the release source allowlist")
        result[path] = _require_sha256(digest, label=f"{label} digest")
    return result


def _validate_manifest(document: Mapping[str, Any]) -> dict[str, Any]:
    if set(document) != MANIFEST_FIELDS:
        raise RuntimeClosureError("runtime closure manifest fields differ")
    if document.get("schema") != RUNTIME_CLOSURE_SCHEMA or document.get("namespace") != RUNTIME_NAMESPACE:
        raise RuntimeClosureError("runtime closure manifest schema or namespace differs")
    release = document.get("release")
    if not isinstance(release, dict) or set(release) != RELEASE_FIELDS:
        raise RuntimeClosureError("runtime closure release binding differs")
    release_sha = _require_sha40(release.get("commit_sha"), label="runtime closure release commit")
    release_tree_sha = _require_sha40(release.get("tree_sha"), label="runtime closure release tree")
    python = document.get("python")
    if (
        not isinstance(python, dict)
        or set(python) != PYTHON_FIELDS
        or python.get("implementation") != "cpython"
        or python.get("major") != 3
        or python.get("minor") != 12
        or python.get("architecture") != "x86_64"
    ):
        raise RuntimeClosureError("runtime closure Python binding differs")
    _require_sha256(document.get("source_policy_sha256"), label="runtime closure source policy")
    _require_sha256(document.get("wheelhouse_manifest_sha256"), label="runtime closure wheelhouse manifest")

    packages = document.get("packages")
    if not isinstance(packages, list) or packages != list(REQUIRED_PACKAGES):
        raise RuntimeClosureError("runtime closure package set differs")

    site = document.get("site_packages")
    if not isinstance(site, dict) or set(site) != SITE_FIELDS or site.get("path") != SITE_PACKAGES_DIRECTORY:
        raise RuntimeClosureError("runtime closure site-packages binding differs")
    files = site.get("files")
    if not isinstance(files, dict) or not files:
        raise RuntimeClosureError("runtime closure file inventory is empty")
    normalized_files: dict[str, str] = {}
    for path, digest in files.items():
        if not isinstance(path, str):
            raise RuntimeClosureError("runtime closure file inventory path is invalid")
        parts = _safe_relative(path, label="runtime closure file inventory")
        if any(part.startswith(".") for part in parts):
            raise RuntimeClosureError("runtime closure file inventory contains hidden path")
        basename = parts[-1]
        if basename.endswith((".pth", ".egg-link")) or basename in {
            "sitecustomize.py",
            "usercustomize.py",
        }:
            raise RuntimeClosureError("runtime closure file inventory contains startup hook")
        normalized_files[path] = _require_sha256(digest, label="runtime closure file digest")
    if site.get("files_sha256") != _hash_mapping(normalized_files):
        raise RuntimeClosureError("runtime closure file inventory digest differs")
    origins = site.get("import_origins")
    if origins != REQUIRED_IMPORT_ORIGINS:
        raise RuntimeClosureError("runtime closure import origins differ")
    if not set(REQUIRED_IMPORT_ORIGINS.values()) <= set(normalized_files):
        raise RuntimeClosureError("runtime closure import origin is not inventoried")

    project_sources = _validate_source_mapping(document.get("project_sources"), label="runtime closure project sources")
    control_sources = _validate_source_mapping(document.get("control_sources"), label="runtime closure control sources")
    if set(control_sources) != CONTROL_SOURCE_PATHS:
        raise RuntimeClosureError("runtime closure control source set differs")
    if not CONTROL_SOURCE_PATHS <= set(project_sources):
        raise RuntimeClosureError("runtime closure project source set omits control source")
    if any(project_sources[path] != control_sources[path] for path in CONTROL_SOURCE_PATHS):
        raise RuntimeClosureError("runtime closure control source digest differs")

    unsigned = {key: document[key] for key in document if key != "runtime_binding_sha256"}
    if document.get("runtime_binding_sha256") != _sha256(unsigned):
        raise RuntimeClosureError("runtime closure binding digest differs")
    return {
        "release_sha": release_sha,
        "release_tree_sha": release_tree_sha,
        "site_files": normalized_files,
        "project_sources": project_sources,
    }


def _scan_site_directory(
    directory_descriptor: int,
    *,
    expected_uid: int | None,
    prefix: str = "",
) -> dict[str, str]:
    _assert_secure_directory(
        directory_descriptor,
        label="runtime closure site-packages directory",
        expected_uid=expected_uid,
    )
    try:
        names = os.listdir(directory_descriptor)
    except OSError as exc:
        raise RuntimeClosureError("cannot list runtime closure site-packages directory") from exc
    result: dict[str, str] = {}
    for name in sorted(names):
        if (
            not isinstance(name, str)
            or SAFE_RELATIVE_RE.fullmatch(name) is None
            or name.startswith(".")
        ):
            raise RuntimeClosureError("runtime closure contains an unsafe path")
        relative = f"{prefix}{name}"
        try:
            metadata = os.stat(name, dir_fd=directory_descriptor, follow_symlinks=False)
        except OSError as exc:
            raise RuntimeClosureError("cannot inspect runtime closure entry") from exc
        if stat.S_ISDIR(metadata.st_mode):
            child = _open_child_directory(
                directory_descriptor,
                name,
                label="runtime closure site-packages child",
                expected_uid=expected_uid,
            )
            try:
                result.update(
                    _scan_site_directory(
                        child,
                        expected_uid=expected_uid,
                        prefix=f"{relative}/",
                    )
                )
            finally:
                os.close(child)
            continue
        if not stat.S_ISREG(metadata.st_mode):
            raise RuntimeClosureError("runtime closure contains a non-regular file")
        if name.endswith((".pth", ".egg-link")) or name in {"sitecustomize.py", "usercustomize.py"}:
            raise RuntimeClosureError("runtime closure contains a Python startup hook")
        descriptor, before = _open_relative_regular(
            directory_descriptor,
            name,
            label="runtime closure site-packages file",
            expected_uid=expected_uid,
        )
        try:
            result[relative] = _hash_descriptor(
                descriptor,
                before,
                label="runtime closure site-packages file",
            )
        finally:
            os.close(descriptor)
    return result


def _read_manifest_from_root(
    runtime_root_descriptor: int,
    *,
    expected_uid: int | None,
) -> tuple[dict[str, Any], bytes]:
    descriptor, before = _open_relative_regular(
        runtime_root_descriptor,
        RUNTIME_MANIFEST_FILENAME,
        label="runtime closure manifest",
        expected_uid=expected_uid,
        maximum=MAX_MANIFEST_BYTES,
    )
    try:
        raw = _read_descriptor(
            descriptor,
            before,
            label="runtime closure manifest",
            maximum=MAX_MANIFEST_BYTES,
        )
    finally:
        os.close(descriptor)
    return _strict_json(raw, label="runtime closure manifest"), raw


def _verify_runtime_root_layout(
    root_descriptor: int,
    *,
    expected_uid: int | None,
) -> int:
    _assert_secure_directory(
        root_descriptor,
        label="runtime closure root",
        expected_uid=expected_uid,
    )
    try:
        names = set(os.listdir(root_descriptor))
    except OSError as exc:
        raise RuntimeClosureError("cannot list runtime closure root") from exc
    if names != {RUNTIME_MANIFEST_FILENAME, SITE_PACKAGES_DIRECTORY}:
        raise RuntimeClosureError("runtime closure root contains unexpected entries")
    return _open_child_directory(
        root_descriptor,
        SITE_PACKAGES_DIRECTORY,
        label="runtime closure site-packages root",
        expected_uid=expected_uid,
    )


def _verify_project_sources(
    release_root_descriptor: int,
    sources: Mapping[str, str],
    *,
    expected_uid: int | None,
) -> None:
    _assert_secure_directory(
        release_root_descriptor,
        label="runtime closure release root",
        expected_uid=expected_uid,
    )
    for relative, expected in sorted(sources.items()):
        descriptor, before = _open_relative_regular(
            release_root_descriptor,
            relative,
            label="runtime closure release source",
            expected_uid=expected_uid,
        )
        try:
            if _hash_descriptor(descriptor, before, label="runtime closure release source") != expected:
                raise RuntimeClosureError("runtime closure release source digest differs")
        finally:
            os.close(descriptor)


def _verify_release_bound_input(
    release_root_descriptor: int,
    *,
    relative: str,
    expected_sha256: str,
    expected_uid: int | None,
    label: str,
) -> None:
    descriptor, before = _open_relative_regular(
        release_root_descriptor,
        relative,
        label=label,
        expected_uid=expected_uid,
        maximum=MAX_MANIFEST_BYTES,
    )
    try:
        if _hash_descriptor(descriptor, before, label=label) != expected_sha256:
            raise RuntimeClosureError(f"{label} digest differs")
    finally:
        os.close(descriptor)


def attest_held_runtime_closure(
    *,
    runtime_root_descriptor: int,
    release_root_descriptor: int,
    expected_uid: int | None = 0,
    expected_release_sha: str | None = None,
    expected_release_tree_sha: str | None = None,
) -> RuntimeClosureAttestation:
    """Attest a closure via already-held no-follow directory descriptors."""

    if type(runtime_root_descriptor) is not int or runtime_root_descriptor < 3:
        raise RuntimeClosureError("runtime closure root descriptor is invalid")
    if type(release_root_descriptor) is not int or release_root_descriptor < 3:
        raise RuntimeClosureError("runtime closure release descriptor is invalid")
    document, raw = _read_manifest_from_root(
        runtime_root_descriptor,
        expected_uid=expected_uid,
    )
    parsed = _validate_manifest(document)
    if expected_release_sha is not None and parsed["release_sha"] != expected_release_sha:
        raise RuntimeClosureError("runtime closure release commit differs")
    if expected_release_tree_sha is not None and parsed["release_tree_sha"] != expected_release_tree_sha:
        raise RuntimeClosureError("runtime closure release tree differs")
    _verify_release_bound_input(
        release_root_descriptor,
        relative=SOURCE_POLICY_RELATIVE,
        expected_sha256=str(document["source_policy_sha256"]),
        expected_uid=expected_uid,
        label="runtime closure source policy",
    )
    _verify_release_bound_input(
        release_root_descriptor,
        relative=WHEELHOUSE_MANIFEST_RELATIVE,
        expected_sha256=str(document["wheelhouse_manifest_sha256"]),
        expected_uid=expected_uid,
        label="runtime closure wheelhouse manifest",
    )
    site_descriptor = _verify_runtime_root_layout(
        runtime_root_descriptor,
        expected_uid=expected_uid,
    )
    try:
        actual_files = _scan_site_directory(site_descriptor, expected_uid=expected_uid)
        if actual_files != parsed["site_files"]:
            raise RuntimeClosureError("runtime closure site-packages inventory differs")
        _verify_project_sources(
            release_root_descriptor,
            parsed["project_sources"],
            expected_uid=expected_uid,
        )
        return RuntimeClosureAttestation(
            manifest=document,
            manifest_sha256=_sha256(raw),
            release_sha=parsed["release_sha"],
            release_tree_sha=parsed["release_tree_sha"],
            site_packages_root=f"/proc/self/fd/{site_descriptor}",
            site_packages_descriptor=site_descriptor,
            site_file_count=len(actual_files),
            project_source_count=len(parsed["project_sources"]),
        )
    except Exception:
        os.close(site_descriptor)
        raise


def verify_import_origins(attestation: RuntimeClosureAttestation) -> None:
    """Import the fixed dependency roots and require their exact closure origin."""

    site_root = Path(attestation.site_packages_root)
    try:
        resolved_root = site_root.resolve(strict=True)
    except OSError as exc:
        raise RuntimeClosureError("verified runtime site-packages root is unavailable") from exc
    for module_name, relative in REQUIRED_IMPORT_ORIGINS.items():
        try:
            module = importlib.import_module(module_name)
            origin = Path(str(module.__file__)).resolve(strict=True)
        except (ImportError, AttributeError, OSError) as exc:
            raise RuntimeClosureError("runtime closure package import is unavailable") from exc
        expected = (resolved_root / relative).resolve(strict=True)
        try:
            origin.relative_to(resolved_root)
        except ValueError as exc:
            raise RuntimeClosureError("runtime closure package origin escaped site-packages") from exc
        if origin != expected:
            raise RuntimeClosureError("runtime closure package origin differs")
    for name in ("sitecustomize", "usercustomize"):
        if name in sys.modules:
            raise RuntimeClosureError("runtime closure loaded a Python startup hook")


def require_clean_preimport_state() -> None:
    """Reject ambient import state before a runtime site root is inserted.

    The launcher calls this while Python still has only its ``-I -S`` standard
    library search roots.  It does not try to repair an inherited hook or
    cache: any such state is a failed runtime boundary.
    """

    forbidden_environment = {
        key
        for key in os.environ
        if key in {"PYTHONPATH", "PYTHONHOME", "PYTHONUSERBASE"}
        or key.startswith("LD_")
    }
    if forbidden_environment:
        raise RuntimeClosureError("runtime closure bootstrap inherited loader or Python environment")
    if (
        Path(sys.executable).resolve(strict=True) != Path(SYSTEM_PYTHON)
        or sys.implementation.name != "cpython"
        or tuple(sys.version_info[:3]) != EXPECTED_PYTHON_VERSION
        or platform.machine() != "x86_64"
        or platform.libc_ver() != ("glibc", "2.39")
    ):
        raise RuntimeClosureError("runtime closure bootstrap interpreter differs")
    blocked_module_prefixes = {
        "core",
        "scripts",
        "cryptography",
        "cffi",
        "_cffi_backend",
        "pycparser",
        "site",
        "sitecustomize",
        "usercustomize",
    }
    if any(
        name == prefix or name.startswith(f"{prefix}.")
        for name in sys.modules
        for prefix in blocked_module_prefixes
    ):
        raise RuntimeClosureError("runtime closure bootstrap imported an ambient package")
    if not sys.flags.isolated or not sys.flags.no_site or not sys.flags.ignore_environment:
        raise RuntimeClosureError("runtime closure bootstrap is not isolated")
    if tuple(sys.path) != EXPECTED_STDLIB_PATHS:
        raise RuntimeClosureError("runtime closure bootstrap interpreter path is unsafe")
    expected_meta_path = {
        importlib.machinery.BuiltinImporter,
        importlib.machinery.FrozenImporter,
        importlib.machinery.PathFinder,
    }
    if set(sys.meta_path) != expected_meta_path:
        raise RuntimeClosureError("runtime closure bootstrap import hook differs")
    allowed_hook_modules = {"zipimport", "_frozen_importlib_external"}
    if any(getattr(hook, "__module__", None) not in allowed_hook_modules for hook in sys.path_hooks):
        raise RuntimeClosureError("runtime closure bootstrap path hook differs")
    standard_roots = tuple(Path(entry).resolve() for entry in EXPECTED_STDLIB_PATHS)
    for cached_path, finder in sys.path_importer_cache.items():
        if not isinstance(cached_path, str) or not cached_path.startswith("/"):
            raise RuntimeClosureError("runtime closure bootstrap import cache path is unsafe")
        try:
            resolved = Path(cached_path).resolve(strict=False)
        except OSError as exc:
            raise RuntimeClosureError("runtime closure bootstrap import cache path is unsafe") from exc
        try:
            permitted = any(
                resolved.is_relative_to(root)
                for root in standard_roots
            )
        except ValueError:
            permitted = False
        if not permitted or (
            finder is not None
            and (
                type(finder).__name__ != "FileFinder"
                or type(finder).__module__ != "_frozen_importlib_external"
            )
        ):
            raise RuntimeClosureError("runtime closure bootstrap import cache differs")


def _open_root(path: Path, *, label: str, expected_uid: int | None) -> int:
    if not path.is_absolute():
        raise RuntimeClosureError(f"{label} path must be absolute")
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise RuntimeClosureError(f"cannot safely open {label}") from exc
    try:
        _assert_secure_directory(descriptor, label=label, expected_uid=expected_uid)
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def attest_runtime_closure(
    runtime_root: Path,
    release_root: Path,
    *,
    expected_uid: int | None = 0,
    expected_release_sha: str | None = None,
    expected_release_tree_sha: str | None = None,
) -> RuntimeClosureAttestation:
    runtime_descriptor = _open_root(
        runtime_root,
        label="runtime closure root",
        expected_uid=expected_uid,
    )
    release_descriptor = _open_root(
        release_root,
        label="runtime closure release root",
        expected_uid=expected_uid,
    )
    try:
        return attest_held_runtime_closure(
            runtime_root_descriptor=runtime_descriptor,
            release_root_descriptor=release_descriptor,
            expected_uid=expected_uid,
            expected_release_sha=expected_release_sha,
            expected_release_tree_sha=expected_release_tree_sha,
        )
    finally:
        os.close(release_descriptor)
        os.close(runtime_descriptor)


def _require_isolated_startup() -> None:
    flags = sys.flags
    if not (
        flags.isolated
        and flags.ignore_environment
        and flags.no_site
        and flags.dont_write_bytecode
        and flags.utf8_mode == 1
        and sys.pycache_prefix == "/dev/null"
        and Path(sys.executable).resolve(strict=True) == Path(SYSTEM_PYTHON)
        and sys.implementation.name == "cpython"
        and tuple(sys.version_info[:3]) == EXPECTED_PYTHON_VERSION
        and platform.machine() == "x86_64"
        and platform.libc_ver() == ("glibc", "2.39")
    ):
        raise RuntimeClosureError("runtime closure verifier requires isolated clean Python startup")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--release-root", type=Path, required=True)
    parser.add_argument("--expected-uid", type=int, default=0)
    parser.add_argument("--release-sha", required=True)
    parser.add_argument("--release-tree-sha", required=True)
    parser.add_argument("--verify-import-origins", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        _require_isolated_startup()
        args = _parser().parse_args(argv)
        if args.expected_uid < 0:
            raise RuntimeClosureError("runtime closure expected uid is invalid")
        attestation = attest_runtime_closure(
            args.runtime_root,
            args.release_root,
            expected_uid=args.expected_uid,
            expected_release_sha=args.release_sha,
            expected_release_tree_sha=args.release_tree_sha,
        )
        if args.verify_import_origins:
            base_path = list(sys.path)
            sys.path[:] = [attestation.site_packages_root, *base_path]
            verify_import_origins(attestation)
        attestation.close()
        print(
            json.dumps(
                {
                    "runtime_closure_attested": "yes",
                    "manifest_sha256": attestation.manifest_sha256,
                    "release_sha": attestation.release_sha,
                    "release_tree_sha": attestation.release_tree_sha,
                    "site_file_count": attestation.site_file_count,
                    "project_source_count": attestation.project_source_count,
                },
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 0
    except (RuntimeClosureError, OSError, ValueError) as exc:
        print(f"blocked: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
