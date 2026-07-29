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
from types import MappingProxyType
from typing import Any, Mapping, Sequence
from uuid import UUID


RUNTIME_CLOSURE_SCHEMA = "production-shadow-controller-runtime-closure-v1"
RUNTIME_NAMESPACE = "production-shadow-controller-only"
RUNTIME_MANIFEST_FILENAME = "runtime-closure-manifest.json"
WHEEL_RECEIPT_FILENAME = "controller-wheel-installation-receipt.json"
WHEEL_RECEIPT_SCHEMA = "production-shadow-controller-wheel-installation-receipt-v1"
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
    "deploy/production-shadow-controller-runtime/controller-wheelhouse.sha256"
)
HELD_RUNTIME_PLAN_ROOT = Path("/etc/trading-bot-three-site/campaigns")
HELD_RUNTIME_PLAN_FILENAME = "controller-runtime-closure-plan.json"
HELD_RUNTIME_PLAN_SCHEMA = "production-shadow-controller-runtime-held-plan-v2"
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
        "scripts/build_production_shadow_controller_runtime_closure.py",
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
        "campaign_id",
        "release",
        "python",
        "source_policy_sha256",
        "wheelhouse_manifest_sha256",
        "held_plan_sha256",
        "wheel_input_receipt_sha256",
        "packages",
        "site_packages",
        "project_sources",
        "control_sources",
        "wheel_installation_receipt_sha256",
        "runtime_binding_sha256",
    }
)
RELEASE_FIELDS = frozenset({"commit_sha", "tree_sha"})
PYTHON_FIELDS = frozenset({"implementation", "major", "minor", "architecture"})
SITE_FIELDS = frozenset({"path", "files", "files_sha256", "import_origins"})
WHEEL_RECEIPT_FIELDS = frozenset(
    {
        "schema",
        "namespace",
        "campaign_id",
        "release",
        "source_policy_sha256",
        "controller_wheelhouse_sha256",
        "held_plan_sha256",
        "wheel_input_receipt_sha256",
        "wheels",
        "installed_files",
        "receipt_sha256",
    }
)
WHEEL_RECEIPT_WHEEL_FIELDS = frozenset(
    {
        "wheel",
        "archive_sha256",
        "record_sha256",
        "members_sha256",
        "installed_files_sha256",
    }
)
INSTALLED_FILE_FIELDS = frozenset(
    {"path", "size", "sha256", "source_wheel", "source_member"}
)
HELD_RUNTIME_PLAN_FIELDS = frozenset(
    {
        "schema",
        "campaign_id",
        "release",
        "source_policy_sha256",
        "controller_wheelhouse_sha256",
        "wheel_input_receipt_sha256",
        "bootstrap_path",
        "required_blobs",
    }
)
HELD_RUNTIME_BOOTSTRAP_SOURCE = "scripts/production_shadow_convergence_source_set_runtime_bootstrap.py"
HELD_RUNTIME_STATIC_BLOBS = frozenset(
    {
        HELD_RUNTIME_BOOTSTRAP_SOURCE,
        "scripts/verify_production_shadow_controller_runtime_closure.py",
        "scripts/build_production_shadow_controller_runtime_closure.py",
        "scripts/produce_production_shadow_convergence_source_set.py",
        "scripts/production_shadow_convergence_source_set_launcher",
        SOURCE_POLICY_RELATIVE,
        "deploy/production-shadow-controller-runtime/requirements.lock",
        WHEELHOUSE_MANIFEST_RELATIVE,
    }
)

# These exact class identities are installed only by a bootstrap that has
# already proved its held descriptors.  This is deliberately *not* a bearer
# credential or a security boundary against arbitrary Python code already
# executing in this interpreter: such code can alter module state.  It is an
# in-process integration contract for a separately root-installed bootstrap;
# the process/launcher boundary must establish trust before registration.
#
# A verifier must not import a release-sourced bootstrap merely to discover
# its types.  The trusted bootstrap instead registers the concrete classes
# from its own module instance after proof.  Exact identity, rather than a
# structural/duck check, rejects ordinary look-alike objects at the operational
# API boundary.
_REGISTERED_HELD_BOOTSTRAP_CAPABILITY_TYPE: type[object] | None = None
_REGISTERED_HELD_BOOTSTRAP_LEASE_TYPE: type[object] | None = None


class RuntimeClosureError(RuntimeError):
    """The controller runtime closure cannot be proven safe and exact."""


@dataclass(frozen=True)
class HeldRuntimePlan:
    """One root-only external trust input for a specific controller campaign."""

    campaign_id: str
    release_sha: str
    release_tree_sha: str
    source_policy_sha256: str
    wheelhouse_manifest_sha256: str
    wheel_input_receipt_sha256: str
    bootstrap_path: str
    required_blobs: Mapping[str, str]
    sha256: str


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


def _require_campaign_id(value: Any, *, label: str) -> str:
    if not isinstance(value, str):
        raise RuntimeClosureError(f"{label} is not a canonical UUID")
    try:
        parsed = UUID(value)
    except (TypeError, ValueError, AttributeError) as exc:
        raise RuntimeClosureError(f"{label} is not a canonical UUID") from exc
    if str(parsed) != value or parsed.int == 0:
        raise RuntimeClosureError(f"{label} is not a canonical UUID")
    return value


def _assert_root_only_mode(metadata: os.stat_result, *, label: str, file: bool) -> None:
    expected_mode = 0o600 if file else 0o700
    if stat.S_IMODE(metadata.st_mode) != expected_mode:
        raise RuntimeClosureError(f"{label} is not root-only mode {expected_mode:04o}")


def read_held_runtime_plan(
    campaign_id: str,
    *,
    expected_uid: int | None = 0,
    plan_root: Path = HELD_RUNTIME_PLAN_ROOT,
) -> HeldRuntimePlan:
    """Read the fixed, root-only plan that supplies runtime trust expectations.

    The plan root is deliberately not a command-line input in the public
    verifier.  Tests may pass a temporary root explicitly, while production
    uses the fixed campaign directory below ``/etc``.
    """

    campaign = _require_campaign_id(campaign_id, label="controller runtime campaign")
    if not isinstance(plan_root, Path) or not plan_root.is_absolute():
        raise RuntimeClosureError("controller runtime held plan root is invalid")
    try:
        canonical_root = plan_root.resolve(strict=True)
    except OSError as exc:
        raise RuntimeClosureError("controller runtime held plan root is unavailable") from exc
    if canonical_root != plan_root:
        raise RuntimeClosureError("controller runtime held plan root must be canonical")
    root_descriptor = _open_root(
        canonical_root,
        label="controller runtime held plan root",
        expected_uid=expected_uid,
    )
    try:
        root_metadata = os.fstat(root_descriptor)
        _assert_root_only_mode(
            root_metadata,
            label="controller runtime held plan root",
            file=False,
        )
        campaign_descriptor = _open_child_directory(
            root_descriptor,
            campaign,
            label="controller runtime held campaign plan directory",
            expected_uid=expected_uid,
        )
        try:
            campaign_metadata = os.fstat(campaign_descriptor)
            _assert_root_only_mode(
                campaign_metadata,
                label="controller runtime held campaign plan directory",
                file=False,
            )
            descriptor, before = _open_relative_regular(
                campaign_descriptor,
                HELD_RUNTIME_PLAN_FILENAME,
                label="controller runtime held plan",
                expected_uid=expected_uid,
                maximum=MAX_MANIFEST_BYTES,
            )
            try:
                _assert_root_only_mode(before, label="controller runtime held plan", file=True)
                raw = _read_descriptor(
                    descriptor,
                    before,
                    label="controller runtime held plan",
                    maximum=MAX_MANIFEST_BYTES,
                )
            finally:
                os.close(descriptor)
        finally:
            os.close(campaign_descriptor)
    finally:
        os.close(root_descriptor)
    document = _strict_json(raw, label="controller runtime held plan")
    if set(document) != HELD_RUNTIME_PLAN_FIELDS or document.get("schema") != HELD_RUNTIME_PLAN_SCHEMA:
        raise RuntimeClosureError("controller runtime held plan fields differ")
    if document.get("campaign_id") != campaign:
        raise RuntimeClosureError("controller runtime held plan campaign differs")
    release = document.get("release")
    if not isinstance(release, dict) or set(release) != RELEASE_FIELDS:
        raise RuntimeClosureError("controller runtime held plan release differs")
    bootstrap_path = document.get("bootstrap_path")
    if bootstrap_path != HELD_RUNTIME_BOOTSTRAP_SOURCE:
        raise RuntimeClosureError("controller runtime held plan bootstrap differs")
    blobs = document.get("required_blobs")
    if not isinstance(blobs, dict) or not blobs:
        raise RuntimeClosureError("controller runtime held plan blob map differs")
    normalized_blobs: dict[str, str] = {}
    for path, digest in blobs.items():
        if not isinstance(path, str):
            raise RuntimeClosureError("controller runtime held plan blob path differs")
        parts = _safe_relative(path, label="controller runtime held plan blob")
        if parts[0] not in {"core", "scripts", "deploy"} or path in normalized_blobs:
            raise RuntimeClosureError("controller runtime held plan blob path differs")
        normalized_blobs[path] = _require_sha256(
            digest,
            label="controller runtime held plan blob",
        )
    if not HELD_RUNTIME_STATIC_BLOBS <= set(normalized_blobs):
        raise RuntimeClosureError("controller runtime held plan omits static bootstrap blob")
    return HeldRuntimePlan(
        campaign_id=campaign,
        release_sha=_require_sha40(release.get("commit_sha"), label="controller runtime held plan release commit"),
        release_tree_sha=_require_sha40(release.get("tree_sha"), label="controller runtime held plan release tree"),
        source_policy_sha256=_require_sha256(
            document.get("source_policy_sha256"),
            label="controller runtime held plan source policy",
        ),
        wheelhouse_manifest_sha256=_require_sha256(
            document.get("controller_wheelhouse_sha256"),
            label="controller runtime held plan wheelhouse manifest",
        ),
        wheel_input_receipt_sha256=_require_sha256(
            document.get("wheel_input_receipt_sha256"),
            label="controller runtime held plan wheel input receipt",
        ),
        bootstrap_path=bootstrap_path,
        required_blobs=MappingProxyType(
            {path: normalized_blobs[path] for path in sorted(normalized_blobs)}
        ),
        sha256=_sha256(raw),
    )


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
    campaign_id = _require_campaign_id(
        document.get("campaign_id"),
        label="runtime closure campaign",
    )
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
    held_plan_sha256 = _require_sha256(
        document.get("held_plan_sha256"),
        label="runtime closure held plan",
    )
    _require_sha256(
        document.get("wheel_input_receipt_sha256"),
        label="runtime closure wheel input receipt",
    )
    _require_sha256(
        document.get("wheel_installation_receipt_sha256"),
        label="runtime closure wheel installation receipt",
    )

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
        "campaign_id": campaign_id,
        "release_sha": release_sha,
        "release_tree_sha": release_tree_sha,
        "held_plan_sha256": held_plan_sha256,
        "site_files": normalized_files,
        "project_sources": project_sources,
    }


def _validate_wheel_receipt(
    document: Mapping[str, Any],
    *,
    manifest: Mapping[str, Any],
    site_files: Mapping[str, str],
    site_file_sizes: Mapping[str, int],
) -> None:
    if set(document) != WHEEL_RECEIPT_FIELDS:
        raise RuntimeClosureError("controller wheel receipt fields differ")
    if document.get("schema") != WHEEL_RECEIPT_SCHEMA or document.get("namespace") != RUNTIME_NAMESPACE:
        raise RuntimeClosureError("controller wheel receipt schema or namespace differs")
    if (
        document.get("campaign_id") != manifest.get("campaign_id")
        or document.get("release") != manifest.get("release")
    ):
        raise RuntimeClosureError("controller wheel receipt release binding differs")
    if (
        document.get("source_policy_sha256") != manifest.get("source_policy_sha256")
        or document.get("controller_wheelhouse_sha256") != manifest.get("wheelhouse_manifest_sha256")
        or document.get("held_plan_sha256") != manifest.get("held_plan_sha256")
        or document.get("wheel_input_receipt_sha256") != manifest.get("wheel_input_receipt_sha256")
    ):
        raise RuntimeClosureError("controller wheel receipt input binding differs")
    wheels = document.get("wheels")
    if not isinstance(wheels, list) or len(wheels) != len(REQUIRED_PACKAGES):
        raise RuntimeClosureError("controller wheel receipt wheel set differs")
    expected_by_wheel = {record["wheel"]: record for record in REQUIRED_PACKAGES}
    wheel_rows: dict[str, Mapping[str, Any]] = {}
    for row in wheels:
        if not isinstance(row, dict) or set(row) != WHEEL_RECEIPT_WHEEL_FIELDS:
            raise RuntimeClosureError("controller wheel receipt wheel fields differ")
        wheel = row.get("wheel")
        if not isinstance(wheel, str) or wheel not in expected_by_wheel or wheel in wheel_rows:
            raise RuntimeClosureError("controller wheel receipt wheel identity differs")
        if row.get("archive_sha256") != expected_by_wheel[wheel]["sha256"]:
            raise RuntimeClosureError("controller wheel receipt archive digest differs")
        for key in ("record_sha256", "members_sha256", "installed_files_sha256"):
            _require_sha256(row.get(key), label=f"controller wheel receipt {key}")
        wheel_rows[wheel] = row
    if [row.get("wheel") for row in wheels] != sorted(expected_by_wheel):
        raise RuntimeClosureError("controller wheel receipt wheels are not sorted")

    installed = document.get("installed_files")
    if not isinstance(installed, list) or not installed:
        raise RuntimeClosureError("controller wheel receipt installed file set differs")
    installed_by_wheel: dict[str, list[dict[str, Any]]] = {wheel: [] for wheel in expected_by_wheel}
    installed_hashes: dict[str, str] = {}
    ordered_paths: list[str] = []
    for row in installed:
        if not isinstance(row, dict) or set(row) != INSTALLED_FILE_FIELDS:
            raise RuntimeClosureError("controller wheel receipt installed file fields differ")
        path = row.get("path")
        member = row.get("source_member")
        wheel = row.get("source_wheel")
        if not isinstance(path, str) or not isinstance(member, str):
            raise RuntimeClosureError("controller wheel receipt installed file path differs")
        _safe_relative(path, label="controller wheel receipt installed file")
        _safe_relative(member, label="controller wheel receipt source member")
        if any(part.startswith(".") for part in PurePosixPath(path).parts):
            raise RuntimeClosureError("controller wheel receipt installed file path is hidden")
        if wheel not in expected_by_wheel or path in installed_hashes:
            raise RuntimeClosureError("controller wheel receipt installed file attribution differs")
        if member != path:
            raise RuntimeClosureError("controller wheel receipt source member attribution differs")
        if type(row.get("size")) is not int or row["size"] < 0:
            raise RuntimeClosureError("controller wheel receipt installed file size differs")
        if site_file_sizes.get(path) != row["size"]:
            raise RuntimeClosureError("controller wheel receipt installed file size differs")
        digest = _require_sha256(row.get("sha256"), label="controller wheel receipt installed file digest")
        installed_hashes[path] = digest
        installed_by_wheel[str(wheel)].append(dict(row))
        ordered_paths.append(path)
    if ordered_paths != sorted(ordered_paths) or installed_hashes != dict(site_files):
        raise RuntimeClosureError("controller wheel receipt installed file inventory differs")
    for wheel, wheel_row in wheel_rows.items():
        rows = installed_by_wheel[wheel]
        if not rows or wheel_row["installed_files_sha256"] != _sha256(rows):
            raise RuntimeClosureError("controller wheel receipt wheel attribution digest differs")
    unsigned = {key: document[key] for key in document if key != "receipt_sha256"}
    if document.get("receipt_sha256") != _sha256(unsigned):
        raise RuntimeClosureError("controller wheel receipt digest differs")


def _scan_site_directory(
    directory_descriptor: int,
    *,
    expected_uid: int | None,
    prefix: str = "",
) -> dict[str, tuple[str, int]]:
    _assert_secure_directory(
        directory_descriptor,
        label="runtime closure site-packages directory",
        expected_uid=expected_uid,
    )
    try:
        names = os.listdir(directory_descriptor)
    except OSError as exc:
        raise RuntimeClosureError("cannot list runtime closure site-packages directory") from exc
    result: dict[str, tuple[str, int]] = {}
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
            result[relative] = (
                _hash_descriptor(
                    descriptor,
                    before,
                    label="runtime closure site-packages file",
                ),
                before.st_size,
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


def _read_wheel_receipt_from_root(
    runtime_root_descriptor: int,
    *,
    expected_uid: int | None,
) -> tuple[dict[str, Any], bytes]:
    descriptor, before = _open_relative_regular(
        runtime_root_descriptor,
        WHEEL_RECEIPT_FILENAME,
        label="controller wheel installation receipt",
        expected_uid=expected_uid,
        maximum=MAX_MANIFEST_BYTES,
    )
    try:
        raw = _read_descriptor(
            descriptor,
            before,
            label="controller wheel installation receipt",
            maximum=MAX_MANIFEST_BYTES,
        )
    finally:
        os.close(descriptor)
    return _strict_json(raw, label="controller wheel installation receipt"), raw


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
    if names != {
        RUNTIME_MANIFEST_FILENAME,
        WHEEL_RECEIPT_FILENAME,
        SITE_PACKAGES_DIRECTORY,
    }:
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


def _register_held_bootstrap_types(
    *,
    capability_type: type[object],
    lease_type: type[object],
) -> None:
    """Bind this verifier process to one proved bootstrap's concrete types.

    The caller must be the separately trusted bootstrap, after its held-FD
    proof and before any operational verifier/builder call.  Registration is
    idempotent only for the exact same two class objects; replacement fails
    closed so a later module load cannot swap the protocol implementation.

    This narrows the Python-level protocol to exact class identity but does
    not claim to defend against code that was already able to execute in this
    interpreter before the trusted bootstrap.  That stronger threat requires
    the launcher/process trust boundary, not a Python object check.
    """

    global _REGISTERED_HELD_BOOTSTRAP_CAPABILITY_TYPE
    global _REGISTERED_HELD_BOOTSTRAP_LEASE_TYPE
    if (
        type(capability_type) is not type
        or type(lease_type) is not type
        or capability_type is lease_type
        or not callable(getattr(capability_type, "consume_for", None))
        or not callable(getattr(lease_type, "assert_for", None))
        or not callable(getattr(lease_type, "assert_held_by", None))
    ):
        raise RuntimeClosureError("runtime closure held-FD bootstrap type registration is invalid")
    current_capability = _REGISTERED_HELD_BOOTSTRAP_CAPABILITY_TYPE
    current_lease = _REGISTERED_HELD_BOOTSTRAP_LEASE_TYPE
    if current_capability is None and current_lease is None:
        _REGISTERED_HELD_BOOTSTRAP_CAPABILITY_TYPE = capability_type
        _REGISTERED_HELD_BOOTSTRAP_LEASE_TYPE = lease_type
        return
    if current_capability is capability_type and current_lease is lease_type:
        return
    raise RuntimeClosureError("runtime closure held-FD bootstrap types are already registered")


def _assert_registered_held_bootstrap_pair(capability: object, lease: object) -> None:
    """Require exact registered types and a lease bound to its capability.

    This helper is intentionally private.  It lets the builder validate an
    already-consumed lease without making a second claim, while preserving the
    same concrete-type gate used by the verifier.
    """

    capability_type = _REGISTERED_HELD_BOOTSTRAP_CAPABILITY_TYPE
    lease_type = _REGISTERED_HELD_BOOTSTRAP_LEASE_TYPE
    if capability_type is None or lease_type is None:
        raise RuntimeClosureError("runtime closure held-FD bootstrap types are not registered")
    if type(capability) is not capability_type:
        raise RuntimeClosureError("runtime closure held-FD bootstrap capability type differs")
    if type(lease) is not lease_type:
        raise RuntimeClosureError("runtime closure held-FD bootstrap lease type differs")
    assert_held_by = getattr(lease, "assert_held_by", None)
    if not callable(assert_held_by):
        raise RuntimeClosureError("runtime closure held-FD bootstrap lease is invalid")
    try:
        assert_held_by(capability)
    except Exception as exc:
        raise RuntimeClosureError("runtime closure held-FD bootstrap lease was rejected") from exc


def _claim_held_bootstrap_capability(
    capability: Any,
    *,
    operation: str,
    campaign_id: str,
    release_sha: str,
    release_tree_sha: str,
    held_plan_sha256: str,
    release_root_descriptor: int | None,
) -> Any:
    """Require one registered held-FD bootstrap lease for an operational API.

    This is an in-process protocol only.  The concrete types must already be
    registered by the separately trusted bootstrap; a normal CLI cannot pass
    a serialized capability or make registration happen.  The stdlib-only
    bootstrap owns the concrete capability and re-proves its duplicated
    descriptors on each claim.  The verifier intentionally avoids importing a
    release-sourced bootstrap (or any producer) to discover those types.
    """

    if capability is None:
        raise RuntimeClosureError("runtime closure operation requires a held-FD bootstrap capability")
    capability_type = _REGISTERED_HELD_BOOTSTRAP_CAPABILITY_TYPE
    if capability_type is None:
        raise RuntimeClosureError("runtime closure held-FD bootstrap types are not registered")
    if type(capability) is not capability_type:
        raise RuntimeClosureError("runtime closure held-FD bootstrap capability type differs")
    consume = getattr(capability, "consume_for", None)
    if not callable(consume):
        raise RuntimeClosureError("runtime closure operation requires a held-FD bootstrap capability")
    try:
        binding = consume(
            operation=operation,
            campaign_id=campaign_id,
            release_sha=release_sha,
            release_tree_sha=release_tree_sha,
            held_plan_sha256=held_plan_sha256,
            release_descriptor=release_root_descriptor,
        )
    except Exception as exc:
        raise RuntimeClosureError("runtime closure held-FD bootstrap capability was rejected") from exc
    _assert_registered_held_bootstrap_pair(capability, binding)
    if (
        getattr(binding, "campaign_id", None) != campaign_id
        or getattr(binding, "release_sha", None) != release_sha
        or getattr(binding, "release_tree_sha", None) != release_tree_sha
        or getattr(binding, "held_plan_sha256", None) != held_plan_sha256
    ):
        raise RuntimeClosureError("runtime closure held-FD bootstrap capability binding differs")
    assert_for = getattr(binding, "assert_for", None)
    if not callable(assert_for):
        raise RuntimeClosureError("runtime closure held-FD bootstrap lease is invalid")
    try:
        assert_for(
            operation=operation,
            campaign_id=campaign_id,
            release_sha=release_sha,
            release_tree_sha=release_tree_sha,
            held_plan_sha256=held_plan_sha256,
        )
    except Exception as exc:
        raise RuntimeClosureError("runtime closure held-FD bootstrap lease was rejected") from exc
    return binding


def attest_held_runtime_closure(
    *,
    runtime_root_descriptor: int,
    release_root_descriptor: int,
    expected_uid: int | None = 0,
    expected_campaign_id: str,
    expected_release_sha: str,
    expected_release_tree_sha: str,
    expected_held_plan_sha256: str,
    held_bootstrap_capability: object | None = None,
) -> RuntimeClosureAttestation:
    """Attest a closure via descriptors and a registered in-process lease.

    ``held_bootstrap_capability`` is not a serializable authority token.  It
    must be supplied by the separately root-installed bootstrap in the same
    already-trusted interpreter after exact type registration.
    """

    if type(runtime_root_descriptor) is not int or runtime_root_descriptor < 3:
        raise RuntimeClosureError("runtime closure root descriptor is invalid")
    if type(release_root_descriptor) is not int or release_root_descriptor < 3:
        raise RuntimeClosureError("runtime closure release descriptor is invalid")
    campaign_id = _require_campaign_id(
        expected_campaign_id,
        label="expected controller runtime campaign",
    )
    release_sha = _require_sha40(expected_release_sha, label="expected controller runtime release commit")
    release_tree_sha = _require_sha40(
        expected_release_tree_sha,
        label="expected controller runtime release tree",
    )
    held_plan_sha256 = _require_sha256(
        expected_held_plan_sha256,
        label="expected controller runtime held plan",
    )
    _claim_held_bootstrap_capability(
        held_bootstrap_capability,
        operation="attest-runtime-closure",
        campaign_id=campaign_id,
        release_sha=release_sha,
        release_tree_sha=release_tree_sha,
        held_plan_sha256=held_plan_sha256,
        release_root_descriptor=release_root_descriptor,
    )
    document, raw = _read_manifest_from_root(
        runtime_root_descriptor,
        expected_uid=expected_uid,
    )
    parsed = _validate_manifest(document)
    receipt, receipt_raw = _read_wheel_receipt_from_root(
        runtime_root_descriptor,
        expected_uid=expected_uid,
    )
    if _sha256(receipt_raw) != document["wheel_installation_receipt_sha256"]:
        raise RuntimeClosureError("runtime closure wheel installation receipt digest differs")
    if parsed["campaign_id"] != campaign_id:
        raise RuntimeClosureError("runtime closure campaign differs")
    if parsed["release_sha"] != release_sha:
        raise RuntimeClosureError("runtime closure release commit differs")
    if parsed["release_tree_sha"] != release_tree_sha:
        raise RuntimeClosureError("runtime closure release tree differs")
    if parsed["held_plan_sha256"] != held_plan_sha256:
        raise RuntimeClosureError("runtime closure held plan differs")
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
        actual_file_hashes = {path: value[0] for path, value in actual_files.items()}
        actual_file_sizes = {path: value[1] for path, value in actual_files.items()}
        if actual_file_hashes != parsed["site_files"]:
            raise RuntimeClosureError("runtime closure site-packages inventory differs")
        _validate_wheel_receipt(
            receipt,
            manifest=document,
            site_files=parsed["site_files"],
            site_file_sizes=actual_file_sizes,
        )
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
    expected_campaign_id: str,
    expected_release_sha: str,
    expected_release_tree_sha: str,
    expected_held_plan_sha256: str,
    held_bootstrap_capability: object | None = None,
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
            expected_campaign_id=expected_campaign_id,
            expected_release_sha=expected_release_sha,
            expected_release_tree_sha=expected_release_tree_sha,
            expected_held_plan_sha256=expected_held_plan_sha256,
            held_bootstrap_capability=held_bootstrap_capability,
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


def _require_root_cli() -> None:
    if os.geteuid() != 0 or os.getegid() != 0:
        raise RuntimeClosureError("runtime closure verifier CLI requires root:root")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--release-root", type=Path, required=True)
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--verify-import-origins", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        _require_isolated_startup()
        require_clean_preimport_state()
        _require_root_cli()
        _parser().parse_args(argv)
        raise RuntimeClosureError(
            "controller runtime verifier CLI is unavailable without an in-process held-FD bootstrap capability"
        )
    except (RuntimeClosureError, OSError, ValueError) as exc:
        print(f"blocked: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
