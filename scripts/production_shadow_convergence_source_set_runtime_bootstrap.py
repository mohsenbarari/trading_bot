#!/usr/bin/env python3
"""Held-FD bootstrap primitives for the controller convergence source set.

This module deliberately imports only the Python standard library.  It holds
future parsing and descriptor-attestation primitives for a separately
root-installed bootstrap.  The v3 held plan binds only the small pre-runtime
closure needed to prove and materialize the controller runtime.  The producer
and convergence gate are explicitly post-runtime sources and remain
unavailable until a separately reviewed FD-pinned loader/map binds them.

The copy in a release is *not* an executable trust anchor: this checkpoint
deliberately keeps its CLI unavailable, because a launcher cannot execute
release-controlled bootstrap bytes before proving them.  It does not import
the producer, alter ``sys.path``, materialize a runtime, or contact any host.
"""

from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
import hashlib
import json
import os
import platform
from pathlib import PurePosixPath
import re
import stat
import subprocess
import sys
from types import MappingProxyType
from typing import Any, Mapping, Sequence
from uuid import UUID


HELD_PLAN_SCHEMA = "production-shadow-controller-runtime-held-plan-v3"
PRE_RUNTIME_CLOSURE_SCOPE = "pre-runtime-controller-closure"
MAX_PLAN_BYTES = 4 * 1024 * 1024
MAX_SOURCE_BYTES = 16 * 1024 * 1024
MAX_SOURCE_FILES = 4096
SHA256_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
SHA40_RE = re.compile(r"^[0-9a-f]{40}$", re.ASCII)
SAFE_COMPONENT_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._+\-]*", re.ASCII)
PLAN_FIELDS = frozenset(
    {
        "schema",
        "campaign_id",
        "release",
        "source_policy_sha256",
        "controller_wheelhouse_sha256",
        "wheel_input_receipt_sha256",
        "closure_scope",
        "bootstrap_path",
        "required_blobs",
    }
)
RELEASE_FIELDS = frozenset({"commit_sha", "tree_sha"})
BOOTSTRAP_SOURCE = "scripts/production_shadow_convergence_source_set_runtime_bootstrap.py"
VERIFIER_SOURCE = "scripts/verify_production_shadow_controller_runtime_closure.py"
BUILDER_SOURCE = "scripts/build_production_shadow_controller_runtime_closure.py"
PRODUCER_SOURCE = "scripts/produce_production_shadow_convergence_source_set.py"
LAUNCHER_SOURCE = "scripts/production_shadow_convergence_source_set_launcher"
GATE_SOURCE = "scripts/orchestrate_production_shadow_convergence_gate.py"
CUTOVER_CONTROLLER_SOURCE = "scripts/production_shadow_cutover_controller.py"
PHASE_VERIFIER_SOURCE = "scripts/verify_production_shadow_phase_evidence.py"
SCRIPTS_INIT_SOURCE = "scripts/__init__.py"
POLICY_SOURCE = "deploy/production-shadow-controller-runtime/runtime-closure-policy.json"
REQUIREMENTS_SOURCE = "deploy/production-shadow-controller-runtime/requirements.lock"
WHEELHOUSE_SOURCE = "deploy/production-shadow-controller-runtime/controller-wheelhouse.sha256"
# These files deliberately have no v3 blob binding.  They can become
# executable only under a separately reviewed post-runtime FD-pinned loader
# with its own exact map; this bootstrap never imports or dispatches them.
POST_RUNTIME_UNAVAILABLE_SOURCES = frozenset(
    {
        PRODUCER_SOURCE,
        GATE_SOURCE,
        CUTOVER_CONTROLLER_SOURCE,
        PHASE_VERIFIER_SOURCE,
    }
)
STATIC_REQUIRED_BLOBS = frozenset(
    {
        BOOTSTRAP_SOURCE,
        VERIFIER_SOURCE,
        BUILDER_SOURCE,
        SCRIPTS_INIT_SOURCE,
        POLICY_SOURCE,
        REQUIREMENTS_SOURCE,
        WHEELHOUSE_SOURCE,
    }
)
SYSTEM_PYTHON = "/usr/bin/python3.12"
EXPECTED_PYTHON_VERSION = (3, 12, 3)
EXPECTED_STDLIB_PATHS = (
    "/usr/lib/python312.zip",
    "/usr/lib/python3.12",
    "/usr/lib/python3.12/lib-dynload",
)
SAFE_GIT_ENV = {
    "PATH": "/usr/bin:/bin",
    "HOME": "/nonexistent",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_OPTIONAL_LOCKS": "0",
    "GIT_NO_REPLACE_OBJECTS": "1",
    "GIT_PAGER": "cat",
}


class SourceSetRuntimeBootstrapError(RuntimeError):
    """A held controller bootstrap input is malformed or changed."""


class _ReleaseBlobAbsent(SourceSetRuntimeBootstrapError):
    """A tracked source candidate is absent without treating it as unsafe."""


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SourceSetRuntimeBootstrapError("held runtime plan has duplicate fields")
        result[key] = value
    return result


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
        raise SourceSetRuntimeBootstrapError("held runtime plan is not canonical JSON") from exc


def _strict_json(raw: bytes) -> dict[str, Any]:
    if not raw:
        raise SourceSetRuntimeBootstrapError("held runtime plan is empty")
    try:
        value = json.loads(
            raw.decode("ascii"),
            object_pairs_hook=_strict_object,
            parse_constant=lambda item: (_ for _ in ()).throw(
                ValueError(f"invalid JSON constant: {item}")),
        )
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise SourceSetRuntimeBootstrapError("held runtime plan is not strict ASCII JSON") from exc
    if not isinstance(value, dict) or canonical_json_bytes(value) != raw:
        raise SourceSetRuntimeBootstrapError("held runtime plan is not canonical JSON")
    return value


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _require_sha256(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None or value == "0" * 64:
        raise SourceSetRuntimeBootstrapError(f"{label} is not a nonzero SHA-256")
    return value


def _require_sha40(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or SHA40_RE.fullmatch(value) is None:
        raise SourceSetRuntimeBootstrapError(f"{label} is not a Git SHA-1")
    return value


def _require_campaign_id(value: Any) -> str:
    if not isinstance(value, str):
        raise SourceSetRuntimeBootstrapError("held runtime plan campaign is invalid")
    try:
        parsed = UUID(value)
    except (TypeError, ValueError, AttributeError) as exc:
        raise SourceSetRuntimeBootstrapError("held runtime plan campaign is invalid") from exc
    if str(parsed) != value or parsed.int == 0:
        raise SourceSetRuntimeBootstrapError("held runtime plan campaign is invalid")
    return value


def _safe_relative_path(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value or value.startswith("/") or "\\" in value:
        raise SourceSetRuntimeBootstrapError(f"{label} path is invalid")
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or any(
        part in {"", ".", ".."} or part.startswith(".") or SAFE_COMPONENT_RE.fullmatch(part) is None
        for part in path.parts
    ):
        raise SourceSetRuntimeBootstrapError(f"{label} path is invalid")
    if path.parts[0] not in {"core", "scripts", "deploy"}:
        raise SourceSetRuntimeBootstrapError(f"{label} path is outside the controller release")
    return path.as_posix()


@dataclass(frozen=True)
class HeldDescriptorIdentity:
    descriptor: int
    device: int
    inode: int
    mode: int
    links: int
    uid: int
    gid: int
    size: int
    modified_ns: int
    changed_ns: int


@dataclass(frozen=True)
class HeldRuntimePlan:
    campaign_id: str
    release_sha: str
    release_tree_sha: str
    source_policy_sha256: str
    wheelhouse_manifest_sha256: str
    wheel_input_receipt_sha256: str
    closure_scope: str
    bootstrap_path: str
    required_blobs: Mapping[str, str]
    sha256: str


@dataclass(frozen=True)
class VerifiedHeldBootstrapInputs:
    """Inputs proven from held descriptors before project code may load."""

    release_identity: HeldDescriptorIdentity
    plan_identity: HeldDescriptorIdentity
    bootstrap_identity: HeldDescriptorIdentity
    plan: HeldRuntimePlan
    reachable_blobs: tuple[str, ...]
    source_graph_sha256: str


@dataclass(frozen=True)
class _SourceGraph:
    paths: tuple[str, ...]
    edges: tuple[tuple[str, str], ...]
    sha256: str


@dataclass(frozen=True)
class HeldBootstrapBinding:
    """Non-secret binding carried by a process-local held-FD capability."""

    campaign_id: str
    release_sha: str
    release_tree_sha: str
    held_plan_sha256: str
    source_graph_sha256: str
    source_policy_sha256: str
    wheelhouse_manifest_sha256: str
    wheel_input_receipt_sha256: str
    required_blobs: Mapping[str, str]


def _identity(metadata: os.stat_result, *, descriptor: int) -> HeldDescriptorIdentity:
    return HeldDescriptorIdentity(
        descriptor=descriptor,
        device=metadata.st_dev,
        inode=metadata.st_ino,
        mode=metadata.st_mode,
        links=metadata.st_nlink,
        uid=metadata.st_uid,
        gid=metadata.st_gid,
        size=metadata.st_size,
        modified_ns=metadata.st_mtime_ns,
        changed_ns=metadata.st_ctime_ns,
    )


def _assert_identity_current(identity: HeldDescriptorIdentity, *, label: str) -> os.stat_result:
    if not isinstance(identity, HeldDescriptorIdentity) or identity.descriptor < 3:
        raise SourceSetRuntimeBootstrapError(f"{label} descriptor identity is invalid")
    try:
        metadata = os.fstat(identity.descriptor)
    except OSError as exc:
        raise SourceSetRuntimeBootstrapError(f"{label} descriptor is unavailable") from exc
    if _identity(metadata, descriptor=identity.descriptor) != identity:
        raise SourceSetRuntimeBootstrapError(f"{label} descriptor changed")
    return metadata


def capture_held_directory(
    descriptor: int,
    *,
    label: str,
    expected_uid: int = 0,
) -> HeldDescriptorIdentity:
    if type(descriptor) is not int or descriptor < 3:
        raise SourceSetRuntimeBootstrapError(f"{label} descriptor is invalid")
    try:
        metadata = os.fstat(descriptor)
    except OSError as exc:
        raise SourceSetRuntimeBootstrapError(f"{label} descriptor is unavailable") from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != expected_uid
        or metadata.st_gid != expected_uid
        or metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
    ):
        raise SourceSetRuntimeBootstrapError(f"{label} is not a root-controlled directory")
    return _identity(metadata, descriptor=descriptor)


def capture_held_regular_file(
    descriptor: int,
    *,
    label: str,
    expected_uid: int = 0,
    exact_mode: int | None = None,
    minimum: int = 1,
    maximum: int = MAX_PLAN_BYTES,
) -> HeldDescriptorIdentity:
    if type(descriptor) is not int or descriptor < 3 or minimum < 0 or maximum < minimum:
        raise SourceSetRuntimeBootstrapError(f"{label} descriptor is invalid")
    try:
        metadata = os.fstat(descriptor)
    except OSError as exc:
        raise SourceSetRuntimeBootstrapError(f"{label} descriptor is unavailable") from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != expected_uid
        or metadata.st_gid != expected_uid
        or metadata.st_nlink != 1
        or metadata.st_size < minimum
        or metadata.st_size > maximum
        or metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
        or (exact_mode is not None and stat.S_IMODE(metadata.st_mode) != exact_mode)
    ):
        raise SourceSetRuntimeBootstrapError(f"{label} is not a root-controlled regular file")
    return _identity(metadata, descriptor=descriptor)


def read_held_bytes(
    identity: HeldDescriptorIdentity,
    *,
    label: str,
    maximum: int,
) -> bytes:
    before = _assert_identity_current(identity, label=label)
    if before.st_size > maximum:
        raise SourceSetRuntimeBootstrapError(f"{label} exceeds its safe size limit")
    try:
        os.lseek(identity.descriptor, 0, os.SEEK_SET)
        chunks: list[bytes] = []
        remaining = maximum + 1
        while remaining:
            part = os.read(identity.descriptor, min(1024 * 1024, remaining))
            if not part:
                break
            chunks.append(part)
            remaining -= len(part)
        raw = b"".join(chunks)
    except OSError as exc:
        raise SourceSetRuntimeBootstrapError(f"{label} cannot be read") from exc
    if len(raw) > maximum:
        raise SourceSetRuntimeBootstrapError(f"{label} exceeds its safe size limit")
    _assert_identity_current(identity, label=label)
    return raw


def parse_held_runtime_plan(raw: bytes) -> HeldRuntimePlan:
    document = _strict_json(raw)
    if set(document) != PLAN_FIELDS or document.get("schema") != HELD_PLAN_SCHEMA:
        raise SourceSetRuntimeBootstrapError("held runtime plan schema or fields differ")
    if document.get("closure_scope") != PRE_RUNTIME_CLOSURE_SCOPE:
        raise SourceSetRuntimeBootstrapError("held runtime plan closure scope differs")
    release = document.get("release")
    if not isinstance(release, dict) or set(release) != RELEASE_FIELDS:
        raise SourceSetRuntimeBootstrapError("held runtime plan release differs")
    blobs = document.get("required_blobs")
    if not isinstance(blobs, dict) or not blobs:
        raise SourceSetRuntimeBootstrapError("held runtime plan blob map differs")
    normalized_blobs: dict[str, str] = {}
    for path, digest in blobs.items():
        normalized = _safe_relative_path(path, label="held runtime plan blob")
        if normalized in normalized_blobs:
            raise SourceSetRuntimeBootstrapError("held runtime plan blob path is duplicated")
        normalized_blobs[normalized] = _require_sha256(digest, label="held runtime plan blob")
    if set(normalized_blobs) & POST_RUNTIME_UNAVAILABLE_SOURCES:
        raise SourceSetRuntimeBootstrapError(
            "held runtime plan binds an unavailable post-runtime source"
        )
    bootstrap_path = _safe_relative_path(
        document.get("bootstrap_path"),
        label="held runtime plan bootstrap",
    )
    if bootstrap_path != BOOTSTRAP_SOURCE or bootstrap_path not in normalized_blobs:
        raise SourceSetRuntimeBootstrapError("held runtime plan bootstrap binding differs")
    if set(normalized_blobs) != STATIC_REQUIRED_BLOBS:
        raise SourceSetRuntimeBootstrapError(
            "held runtime plan pre-runtime blob map differs"
        )
    return HeldRuntimePlan(
        campaign_id=_require_campaign_id(document.get("campaign_id")),
        release_sha=_require_sha40(release.get("commit_sha"), label="held runtime plan release commit"),
        release_tree_sha=_require_sha40(release.get("tree_sha"), label="held runtime plan release tree"),
        source_policy_sha256=_require_sha256(
            document.get("source_policy_sha256"),
            label="held runtime plan source policy",
        ),
        wheelhouse_manifest_sha256=_require_sha256(
            document.get("controller_wheelhouse_sha256"),
            label="held runtime plan wheelhouse manifest",
        ),
        wheel_input_receipt_sha256=_require_sha256(
            document.get("wheel_input_receipt_sha256"),
            label="held runtime plan wheel input receipt",
        ),
        closure_scope=PRE_RUNTIME_CLOSURE_SCOPE,
        bootstrap_path=bootstrap_path,
        required_blobs=MappingProxyType(
            {path: normalized_blobs[path] for path in sorted(normalized_blobs)}
        ),
        sha256=_sha256(raw),
    )


def read_held_runtime_plan_fd(plan_descriptor: int, *, expected_uid: int = 0) -> HeldRuntimePlan:
    identity = capture_held_regular_file(
        plan_descriptor,
        label="held runtime plan",
        expected_uid=expected_uid,
        exact_mode=0o600,
        maximum=MAX_PLAN_BYTES,
    )
    return parse_held_runtime_plan(
        read_held_bytes(identity, label="held runtime plan", maximum=MAX_PLAN_BYTES)
    )


def require_exact_isolated_root_startup() -> None:
    """Reject any process that was not launched as the fixed bootstrap shell."""

    forbidden_environment = {
        key
        for key in os.environ
        if key in {"PYTHONPATH", "PYTHONHOME", "PYTHONUSERBASE"} or key.startswith("LD_")
    }
    if forbidden_environment:
        raise SourceSetRuntimeBootstrapError("bootstrap inherited loader or Python environment")
    flags = sys.flags
    if (
        os.geteuid() != 0
        or os.getegid() != 0
        or not flags.isolated
        or not flags.ignore_environment
        or not flags.no_site
        or not flags.dont_write_bytecode
        or flags.utf8_mode != 1
        or sys.pycache_prefix != "/dev/null"
        or os.path.realpath(sys.executable) != SYSTEM_PYTHON
        or sys.implementation.name != "cpython"
        or tuple(sys.version_info[:3]) != EXPECTED_PYTHON_VERSION
        or platform.machine() != "x86_64"
        or platform.libc_ver() != ("glibc", "2.39")
        or tuple(sys.path) != EXPECTED_STDLIB_PATHS
    ):
        raise SourceSetRuntimeBootstrapError("bootstrap requires root exact isolated Python startup")
    blocked_prefixes = ("core", "scripts", "cryptography", "cffi", "_cffi_backend", "pycparser")
    if any(
        name == prefix or name.startswith(f"{prefix}.")
        for name in sys.modules
        for prefix in blocked_prefixes
    ):
        raise SourceSetRuntimeBootstrapError("bootstrap imported project or runtime package before proof")


def _safe_parts(relative: str, *, label: str) -> tuple[str, ...]:
    return tuple(_safe_relative_path(relative, label=label).split("/"))


def _assert_release_directory(metadata: os.stat_result, *, label: str, expected_uid: int) -> None:
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != expected_uid
        or metadata.st_gid != expected_uid
        or metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
    ):
        raise SourceSetRuntimeBootstrapError(f"{label} is not a root-controlled release directory")


def _assert_release_regular(
    metadata: os.stat_result,
    *,
    label: str,
    expected_uid: int,
    maximum: int,
    minimum: int = 0,
) -> None:
    if (
        minimum < 0
        or maximum < minimum
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != expected_uid
        or metadata.st_gid != expected_uid
        or metadata.st_nlink != 1
        or metadata.st_size < minimum
        or metadata.st_size > maximum
        or metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
    ):
        raise SourceSetRuntimeBootstrapError(f"{label} is not a root-controlled release file")


def _open_held_child_directory(parent: int, name: str, *, expected_uid: int, label: str) -> int:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(name, flags, dir_fd=parent)
    except FileNotFoundError as exc:
        raise _ReleaseBlobAbsent(f"{label} is absent") from exc
    except OSError as exc:
        raise SourceSetRuntimeBootstrapError(f"cannot safely open {label}") from exc
    try:
        _assert_release_directory(os.fstat(descriptor), label=label, expected_uid=expected_uid)
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _read_release_blob(
    release_identity: HeldDescriptorIdentity,
    relative: str,
    *,
    expected_uid: int,
    maximum: int = MAX_SOURCE_BYTES,
) -> bytes:
    _assert_identity_current(release_identity, label="held release")
    parts = _safe_parts(relative, label="controller release blob")
    directory = os.dup(release_identity.descriptor)
    try:
        _assert_release_directory(
            os.fstat(directory),
            label="held release",
            expected_uid=expected_uid,
        )
        for part in parts[:-1]:
            child = _open_held_child_directory(
                directory,
                part,
                expected_uid=expected_uid,
                label="controller release blob directory",
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
        except FileNotFoundError as exc:
            raise _ReleaseBlobAbsent("controller release blob is absent") from exc
        except OSError as exc:
            raise SourceSetRuntimeBootstrapError("cannot safely open controller release blob") from exc
        try:
            before = os.fstat(descriptor)
            _assert_release_regular(
                before,
                label="controller release blob",
                expected_uid=expected_uid,
                maximum=maximum,
            )
            raw = read_held_bytes(
                _identity(before, descriptor=descriptor),
                label="controller release blob",
                maximum=maximum,
            )
            return raw
        finally:
            os.close(descriptor)
    finally:
        os.close(directory)
        _assert_identity_current(release_identity, label="held release")


def _module_name_for_relative(relative: str) -> str | None:
    if not relative.endswith(".py"):
        return None
    path = PurePosixPath(relative)
    if path.parts[0] not in {"core", "scripts"}:
        return None
    if path.name == "__init__.py":
        return ".".join(path.parts[:-1])
    return ".".join((*path.parts[:-1], path.stem))


def _module_candidate(
    tracked_blobs: Mapping[str, str],
    module: str,
    *,
    required: bool,
) -> str | None:
    parts = module.split(".")
    if (
        not module
        or parts[0] not in {"core", "scripts"}
        or any(SAFE_COMPONENT_RE.fullmatch(part) is None for part in parts)
    ):
        if required:
            raise SourceSetRuntimeBootstrapError("controller source imports an invalid local module")
        return None
    stem = "/".join(parts)
    candidates = tuple(path for path in (f"{stem}.py", f"{stem}/__init__.py") if path in tracked_blobs)
    if len(candidates) > 1:
        raise SourceSetRuntimeBootstrapError("controller source local module resolution is ambiguous")
    if not candidates:
        if required:
            raise SourceSetRuntimeBootstrapError("controller source imports a missing local module")
        return None
    return candidates[0]


def _relative_module(current_relative: str, node: ast.ImportFrom) -> str | None:
    current = _module_name_for_relative(current_relative)
    if current is None:
        raise SourceSetRuntimeBootstrapError("controller source module identity is invalid")
    package = current.split(".") if current_relative.endswith("/__init__.py") else current.split(".")[:-1]
    if node.level > len(package):
        raise SourceSetRuntimeBootstrapError("controller source relative import escapes its package")
    prefix = package[: len(package) - node.level + 1]
    if node.module:
        prefix.extend(node.module.split("."))
    return ".".join(prefix) or None


def _dotted_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _dotted_name(node.value)
        return f"{parent}.{node.attr}" if parent else None
    return None


def _is_project_module(module: str | None) -> bool:
    return bool(module) and module.split(".")[0] in {"core", "scripts"}


def _reject_dynamic_loader_or_path_mutation(relative: str, tree: ast.AST) -> None:
    """Reject non-static loading before a trusted runtime is available.

    The verifier has one policy-bound third-party import path after its
    closure has been attested.  It is not a project-module loader, so retain
    that narrow exception here while refusing every other dynamic loader.
    """

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            dotted = _dotted_name(node.func)
            if dotted in {
                "__import__",
                "eval",
                "exec",
                "compile",
                "runpy.run_path",
                "runpy.run_module",
                "importlib.util.spec_from_file_location",
                "importlib.machinery.SourceFileLoader",
            }:
                raise SourceSetRuntimeBootstrapError("controller source uses a dynamic loader")
            if dotted == "importlib.import_module" and relative != VERIFIER_SOURCE:
                raise SourceSetRuntimeBootstrapError("controller source uses a dynamic import")
            if dotted in {
                "sys.path.append",
                "sys.path.extend",
                "sys.path.insert",
                "sys.path.remove",
                "sys.path.pop",
                "sys.path.clear",
            }:
                raise SourceSetRuntimeBootstrapError("controller source mutates sys.path before trusted runtime")
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else (node.target,)
            if any(_dotted_name(target) == "sys.path" for target in targets):
                raise SourceSetRuntimeBootstrapError("controller source mutates sys.path before trusted runtime")


def _project_import_references(current_relative: str, tree: ast.Module) -> tuple[tuple[str, bool], ...]:
    """Return direct import-time local module references only.

    A project import under a function/class/conditional body is deliberately
    rejected rather than guessed into the startup closure.  That forces a
    source split before a deferred operation can join the held runtime.
    """

    direct = {id(node) for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom))}
    references: set[tuple[str, bool]] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        if isinstance(node, ast.Import):
            local = tuple(alias.name for alias in node.names if _is_project_module(alias.name))
        else:
            base = _relative_module(current_relative, node) if node.level else node.module
            local = tuple(value for value in (base,) if _is_project_module(value))
            if local and any(alias.name == "*" for alias in node.names):
                raise SourceSetRuntimeBootstrapError("controller source uses a star import")
        if id(node) not in direct:
            if node.level or local:
                raise SourceSetRuntimeBootstrapError("controller source has a deferred local import")
            continue
        if isinstance(node, ast.Import):
            references.update((module, True) for module in local)
            continue
        for base in local:
            references.add((base, True))
            references.update((f"{base}.{alias.name}", False) for alias in node.names)
    return tuple(sorted(references))


def _add_parent_package_initializers(
    module: str,
    tracked_blobs: Mapping[str, str],
    discovered: set[str],
) -> tuple[str, ...]:
    parts = module.split(".")
    initializers: list[str] = []
    for depth in range(1, len(parts)):
        initializer = "/".join(parts[:depth]) + "/__init__.py"
        if initializer not in tracked_blobs:
            raise SourceSetRuntimeBootstrapError("controller package initializer is not bound in the Git tree")
        discovered.add(initializer)
        initializers.append(initializer)
    return tuple(initializers)


def discover_reachable_controller_sources(
    release_identity: HeldDescriptorIdentity,
    *,
    tracked_blobs: Mapping[str, str],
    expected_uid: int = 0,
) -> _SourceGraph:
    """Return the exact static controller graph without importing any project code."""

    if not isinstance(tracked_blobs, Mapping) or not tracked_blobs:
        raise SourceSetRuntimeBootstrapError("held release tracked Git tree is unavailable")
    if not STATIC_REQUIRED_BLOBS <= set(tracked_blobs):
        raise SourceSetRuntimeBootstrapError("held release Git tree omits a static controller blob")
    pending = [path for path in STATIC_REQUIRED_BLOBS if path.endswith(".py")]
    discovered: set[str] = set(STATIC_REQUIRED_BLOBS)
    visited: set[str] = set()
    edges: set[tuple[str, str]] = set()
    while pending:
        relative = pending.pop()
        if relative in visited:
            continue
        visited.add(relative)
        if relative not in tracked_blobs:
            raise SourceSetRuntimeBootstrapError("controller source is absent from the held Git tree")
        raw = _read_release_blob(release_identity, relative, expected_uid=expected_uid)
        try:
            tree = ast.parse(raw.decode("utf-8"), filename=relative)
        except (UnicodeDecodeError, SyntaxError) as exc:
            raise SourceSetRuntimeBootstrapError("controller source is not valid UTF-8 Python") from exc
        _reject_dynamic_loader_or_path_mutation(relative, tree)
        module_name = _module_name_for_relative(relative)
        if module_name:
            _add_parent_package_initializers(module_name, tracked_blobs, discovered)
        for module, required in _project_import_references(relative, tree):
            candidate = _module_candidate(tracked_blobs, module, required=required)
            if candidate is None:
                continue
            edges.add((relative, candidate))
            _add_parent_package_initializers(module, tracked_blobs, discovered)
            if candidate not in discovered:
                if len(discovered) >= MAX_SOURCE_FILES:
                    raise SourceSetRuntimeBootstrapError("controller source graph exceeds its safe limit")
                discovered.add(candidate)
                if candidate.endswith(".py"):
                    pending.append(candidate)
    paths = tuple(sorted(discovered))
    canonical_edges = tuple(sorted(edges))
    return _SourceGraph(
        paths=paths,
        edges=canonical_edges,
        sha256=_sha256(canonical_json_bytes({"edges": canonical_edges, "paths": paths})),
    )


def _git(release_identity: HeldDescriptorIdentity, arguments: tuple[str, ...]) -> bytes:
    _assert_identity_current(release_identity, label="held release")
    command = (
        "/usr/bin/git",
        "-C",
        f"/proc/self/fd/{release_identity.descriptor}",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "core.pager=cat",
        "-c",
        "protocol.file.allow=never",
        *arguments,
    )
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            env=SAFE_GIT_ENV,
            pass_fds=(release_identity.descriptor,),
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SourceSetRuntimeBootstrapError("held release Git proof is unavailable") from exc
    if completed.returncode != 0 or len(completed.stdout) > MAX_SOURCE_BYTES or len(completed.stderr) > MAX_PLAN_BYTES:
        raise SourceSetRuntimeBootstrapError("held release Git proof failed")
    _assert_identity_current(release_identity, label="held release")
    return completed.stdout


def _git_text(release_identity: HeldDescriptorIdentity, *arguments: str) -> str:
    try:
        return _git(release_identity, tuple(arguments)).decode("ascii").strip()
    except UnicodeDecodeError as exc:
        raise SourceSetRuntimeBootstrapError("held release Git output is not ASCII") from exc


def _tracked_git_blobs(
    release_identity: HeldDescriptorIdentity,
    *,
    release_sha: str,
) -> dict[str, str]:
    raw = _git(
        release_identity,
        ("ls-tree", "-r", "-z", "--full-tree", release_sha),
    )
    result: dict[str, str] = {}
    for entry in raw.split(b"\0"):
        if not entry:
            continue
        try:
            metadata, raw_path = entry.split(b"\t", 1)
            mode, kind, object_sha = metadata.split(b" ", 2)
            relative = raw_path.decode("ascii")
        except (UnicodeDecodeError, ValueError) as exc:
            raise SourceSetRuntimeBootstrapError("held release Git tree record is invalid") from exc
        if not relative.startswith(("core/", "scripts/", "deploy/")):
            continue
        normalized = _safe_relative_path(relative, label="held release Git tree")
        if normalized != relative or kind != b"blob" or mode not in {b"100644", b"100755"}:
            raise SourceSetRuntimeBootstrapError("held release Git tree controller entry is invalid")
        try:
            blob_sha = object_sha.decode("ascii")
        except UnicodeDecodeError as exc:
            raise SourceSetRuntimeBootstrapError("held release Git tree object is invalid") from exc
        _require_sha40(blob_sha, label="held release Git tree object")
        if relative in result:
            raise SourceSetRuntimeBootstrapError("held release Git tree contains duplicate controller path")
        result[relative] = blob_sha
    return result


def _verify_exact_git_state(
    release_identity: HeldDescriptorIdentity,
    plan: HeldRuntimePlan,
) -> dict[str, str]:
    observed = {
        "head": _git_text(release_identity, "rev-parse", "HEAD"),
        "tree": _git_text(release_identity, "rev-parse", "HEAD^{tree}"),
        "branch": _git_text(release_identity, "rev-parse", "--abbrev-ref", "HEAD"),
        "status": _git_text(
            release_identity,
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
            "--ignored=matching",
        ),
        "remote": _git_text(release_identity, "remote"),
    }
    if (
        observed["head"] != plan.release_sha
        or observed["tree"] != plan.release_tree_sha
        or observed["branch"] != "HEAD"
        or observed["status"]
        or observed["remote"]
    ):
        raise SourceSetRuntimeBootstrapError("held release is not exact detached clean and remote-free")
    return _tracked_git_blobs(release_identity, release_sha=plan.release_sha)


def _verify_exact_git_release(
    release_identity: HeldDescriptorIdentity,
    plan: HeldRuntimePlan,
    *,
    expected_uid: int,
    graph: _SourceGraph,
    tracked_blobs: Mapping[str, str],
) -> None:
    if set(plan.required_blobs) != set(graph.paths):
        raise SourceSetRuntimeBootstrapError("held runtime plan blob map is not the exact reachable controller graph")
    if (
        plan.required_blobs.get(POLICY_SOURCE) != plan.source_policy_sha256
        or plan.required_blobs.get(WHEELHOUSE_SOURCE) != plan.wheelhouse_manifest_sha256
    ):
        raise SourceSetRuntimeBootstrapError("held runtime plan release input binding differs")
    for relative in graph.paths:
        if relative not in tracked_blobs:
            raise SourceSetRuntimeBootstrapError("held runtime plan binds an untracked controller blob")
        raw = _read_release_blob(release_identity, relative, expected_uid=expected_uid)
        if _sha256(raw) != plan.required_blobs[relative]:
            raise SourceSetRuntimeBootstrapError("held runtime plan blob digest differs")
        blob = _git(release_identity, ("cat-file", "blob", f"{plan.release_sha}:{relative}"))
        if blob != raw:
            raise SourceSetRuntimeBootstrapError("held release blob differs from its Git object")


def verify_held_bootstrap_inputs(
    *,
    release_descriptor: int,
    plan_descriptor: int,
    bootstrap_descriptor: int,
    expected_uid: int = 0,
) -> VerifiedHeldBootstrapInputs:
    """Prove held release, plan, and bootstrap bytes before project imports."""

    release_identity = capture_held_directory(
        release_descriptor,
        label="held release",
        expected_uid=expected_uid,
    )
    plan_identity = capture_held_regular_file(
        plan_descriptor,
        label="held runtime plan",
        expected_uid=expected_uid,
        exact_mode=0o600,
        maximum=MAX_PLAN_BYTES,
    )
    bootstrap_identity = capture_held_regular_file(
        bootstrap_descriptor,
        label="held bootstrap",
        expected_uid=expected_uid,
        maximum=MAX_SOURCE_BYTES,
    )
    plan = parse_held_runtime_plan(
        read_held_bytes(plan_identity, label="held runtime plan", maximum=MAX_PLAN_BYTES)
    )
    tracked_blobs = _verify_exact_git_state(release_identity, plan)
    graph = discover_reachable_controller_sources(
        release_identity,
        tracked_blobs=tracked_blobs,
        expected_uid=expected_uid,
    )
    _verify_exact_git_release(
        release_identity,
        plan,
        expected_uid=expected_uid,
        graph=graph,
        tracked_blobs=tracked_blobs,
    )
    held_bootstrap = read_held_bytes(
        bootstrap_identity,
        label="held bootstrap",
        maximum=MAX_SOURCE_BYTES,
    )
    release_bootstrap = _read_release_blob(
        release_identity,
        plan.bootstrap_path,
        expected_uid=expected_uid,
    )
    if held_bootstrap != release_bootstrap:
        raise SourceSetRuntimeBootstrapError("held bootstrap does not match the verified release blob")
    return VerifiedHeldBootstrapInputs(
        release_identity=release_identity,
        plan_identity=plan_identity,
        bootstrap_identity=bootstrap_identity,
        plan=plan,
        reachable_blobs=graph.paths,
        source_graph_sha256=graph.sha256,
    )


def _same_held_object(left: HeldDescriptorIdentity, right: HeldDescriptorIdentity) -> bool:
    return (
        left.device,
        left.inode,
        left.mode,
        left.links,
        left.uid,
        left.gid,
        left.size,
        left.modified_ns,
        left.changed_ns,
    ) == (
        right.device,
        right.inode,
        right.mode,
        right.links,
        right.uid,
        right.gid,
        right.size,
        right.modified_ns,
        right.changed_ns,
    )


def _duplicate_descriptor(descriptor: int, *, label: str) -> int:
    try:
        duplicate = os.dup(descriptor)
        os.set_inheritable(duplicate, False)
        return duplicate
    except OSError as exc:
        raise SourceSetRuntimeBootstrapError(f"cannot retain {label} descriptor") from exc


class HeldBootstrapLease:
    """One opaque operation lease emitted by a live held-FD capability."""

    __slots__ = (
        "_binding",
        "_capability",
        "_inputs",
        "_inputs_taken",
        "_marker",
        "_operation",
    )

    _MARKER = object()

    def __init__(
        self,
        binding: HeldBootstrapBinding,
        *,
        capability: "HeldFdBootstrapCapability",
        inputs: VerifiedHeldBootstrapInputs,
        operation: str,
    ) -> None:
        self._binding = binding
        self._capability = capability
        self._inputs = inputs
        self._inputs_taken = False
        self._operation = operation
        self._marker = self._MARKER

    @property
    def campaign_id(self) -> str:
        return self._binding.campaign_id

    @property
    def release_sha(self) -> str:
        return self._binding.release_sha

    @property
    def release_tree_sha(self) -> str:
        return self._binding.release_tree_sha

    @property
    def held_plan_sha256(self) -> str:
        return self._binding.held_plan_sha256

    @property
    def source_graph_sha256(self) -> str:
        return self._binding.source_graph_sha256

    @property
    def source_policy_sha256(self) -> str:
        return self._binding.source_policy_sha256

    @property
    def wheelhouse_manifest_sha256(self) -> str:
        return self._binding.wheelhouse_manifest_sha256

    @property
    def wheel_input_receipt_sha256(self) -> str:
        return self._binding.wheel_input_receipt_sha256

    @property
    def required_blobs(self) -> Mapping[str, str]:
        return self._binding.required_blobs

    def assert_for(
        self,
        *,
        operation: str,
        campaign_id: str,
        release_sha: str,
        release_tree_sha: str,
        held_plan_sha256: str,
    ) -> None:
        if (
            self._marker is not self._MARKER
            or self._operation != operation
            or self.campaign_id != campaign_id
            or self.release_sha != release_sha
            or self.release_tree_sha != release_tree_sha
            or self.held_plan_sha256 != held_plan_sha256
        ):
            raise SourceSetRuntimeBootstrapError("held-FD bootstrap lease binding differs")

    def assert_held_by(self, capability: object) -> None:
        if self._marker is not self._MARKER or capability is not self._capability:
            raise SourceSetRuntimeBootstrapError("held-FD bootstrap lease capability differs")

    def take_reproved_preparation_inputs(self) -> VerifiedHeldBootstrapInputs:
        """Return one fresh proof only to the descriptor-native prepare lease.

        The returned descriptors are the capability's retained duplicates, not
        caller-selected paths.  This is deliberately not a materialization
        capability: a future separately reviewed descriptor-native attester
        must establish that boundary before any runtime output can exist.
        """

        if (
            self._marker is not self._MARKER
            or self._operation != "prepare-runtime-closure"
            or self._inputs_taken
            or type(self._inputs) is not VerifiedHeldBootstrapInputs
        ):
            raise SourceSetRuntimeBootstrapError("held-FD preparation inputs are unavailable")
        self._inputs_taken = True
        return self._inputs

    def __reduce__(self) -> object:
        raise TypeError("held-FD bootstrap lease cannot be serialized")


class HeldFdBootstrapCapability:
    """Opaque, process-bound authority backed by three duplicated held FDs.

    The object intentionally exposes no filesystem path or descriptor.  Its
    only operational surface is ``consume_for``: each permitted operation can
    claim one lease after a fresh held-FD/Git/blob re-proof.  A capability is
    neither serializable nor reusable after close or fork.
    """

    __slots__ = (
        "_bootstrap_identity",
        "_closed",
        "_consumed",
        "_expected_uid",
        "_inputs",
        "_marker",
        "_pid",
        "_plan_identity",
        "_release_identity",
    )

    _MARKER = object()
    _OPERATIONS = frozenset(
        {
            "attest-runtime-closure",
            "materialize-runtime-closure",
            "prepare-runtime-closure",
        }
    )

    def __init__(self, inputs: VerifiedHeldBootstrapInputs, *, expected_uid: int) -> None:
        self._marker = self._MARKER
        self._inputs = inputs
        self._release_identity = inputs.release_identity
        self._plan_identity = inputs.plan_identity
        self._bootstrap_identity = inputs.bootstrap_identity
        self._expected_uid = expected_uid
        self._pid = os.getpid()
        self._consumed: set[str] = set()
        self._closed = False

    @property
    def binding(self) -> HeldBootstrapBinding:
        plan = self._inputs.plan
        return HeldBootstrapBinding(
            campaign_id=plan.campaign_id,
            release_sha=plan.release_sha,
            release_tree_sha=plan.release_tree_sha,
            held_plan_sha256=plan.sha256,
            source_graph_sha256=self._inputs.source_graph_sha256,
            source_policy_sha256=plan.source_policy_sha256,
            wheelhouse_manifest_sha256=plan.wheelhouse_manifest_sha256,
            wheel_input_receipt_sha256=plan.wheel_input_receipt_sha256,
            required_blobs=plan.required_blobs,
        )

    @property
    def closed(self) -> bool:
        return self._closed

    def __repr__(self) -> str:
        return f"{type(self).__name__}(closed={self._closed!r}, pid={self._pid!r})"

    def __reduce__(self) -> object:
        raise TypeError("held-FD bootstrap capability cannot be serialized")

    def __copy__(self) -> object:
        raise TypeError("held-FD bootstrap capability cannot be copied")

    def __deepcopy__(self, memo: object) -> object:
        raise TypeError("held-FD bootstrap capability cannot be copied")

    def __enter__(self) -> "HeldFdBootstrapCapability":
        if self._closed:
            raise SourceSetRuntimeBootstrapError("held-FD bootstrap capability is closed")
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def _require_live(self) -> None:
        if (
            self._marker is not self._MARKER
            or self._closed
            or self._pid != os.getpid()
        ):
            raise SourceSetRuntimeBootstrapError("held-FD bootstrap capability is unavailable")

    def _reverify(self) -> VerifiedHeldBootstrapInputs:
        self._require_live()
        observed = verify_held_bootstrap_inputs(
            release_descriptor=self._release_identity.descriptor,
            plan_descriptor=self._plan_identity.descriptor,
            bootstrap_descriptor=self._bootstrap_identity.descriptor,
            expected_uid=self._expected_uid,
        )
        expected = self.binding
        actual = HeldBootstrapBinding(
            campaign_id=observed.plan.campaign_id,
            release_sha=observed.plan.release_sha,
            release_tree_sha=observed.plan.release_tree_sha,
            held_plan_sha256=observed.plan.sha256,
            source_graph_sha256=observed.source_graph_sha256,
            source_policy_sha256=observed.plan.source_policy_sha256,
            wheelhouse_manifest_sha256=observed.plan.wheelhouse_manifest_sha256,
            wheel_input_receipt_sha256=observed.plan.wheel_input_receipt_sha256,
            required_blobs=observed.plan.required_blobs,
        )
        if actual != expected:
            raise SourceSetRuntimeBootstrapError("held-FD bootstrap capability binding changed")
        if (
            not _same_held_object(observed.release_identity, self._release_identity)
            or not _same_held_object(observed.plan_identity, self._plan_identity)
            or not _same_held_object(observed.bootstrap_identity, self._bootstrap_identity)
        ):
            raise SourceSetRuntimeBootstrapError("held-FD bootstrap descriptor identity changed")
        return observed

    def consume_for(
        self,
        *,
        operation: str,
        campaign_id: str,
        release_sha: str,
        release_tree_sha: str,
        held_plan_sha256: str,
        release_descriptor: int | None = None,
    ) -> HeldBootstrapLease:
        """Consume one operation-specific lease after re-proving all inputs."""

        if operation not in self._OPERATIONS:
            raise SourceSetRuntimeBootstrapError("held-FD bootstrap capability operation is invalid")
        self._require_live()
        binding = self.binding
        if (
            campaign_id != binding.campaign_id
            or release_sha != binding.release_sha
            or release_tree_sha != binding.release_tree_sha
            or held_plan_sha256 != binding.held_plan_sha256
        ):
            raise SourceSetRuntimeBootstrapError("held-FD bootstrap capability binding differs")
        if operation in self._consumed:
            raise SourceSetRuntimeBootstrapError("held-FD bootstrap capability lease was already consumed")
        if release_descriptor is not None:
            candidate = capture_held_directory(
                release_descriptor,
                label="runtime release descriptor",
                expected_uid=self._expected_uid,
            )
            if not _same_held_object(candidate, self._release_identity):
                raise SourceSetRuntimeBootstrapError("runtime release descriptor differs from held bootstrap release")
        observed = self._reverify()
        self._consumed.add(operation)
        return HeldBootstrapLease(
            binding,
            capability=self,
            inputs=observed,
            operation=operation,
        )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for identity in (
            self._bootstrap_identity,
            self._plan_identity,
            self._release_identity,
        ):
            try:
                os.close(identity.descriptor)
            except OSError:
                pass


def activate_verified_held_bootstrap(
    inputs: VerifiedHeldBootstrapInputs,
    *,
    expected_uid: int = 0,
) -> HeldFdBootstrapCapability:
    """Duplicate a verified input set into an opaque, process-local capability."""

    if not isinstance(inputs, VerifiedHeldBootstrapInputs):
        raise SourceSetRuntimeBootstrapError("held-FD bootstrap proof is invalid")
    duplicates: list[int] = []
    try:
        release_descriptor = _duplicate_descriptor(inputs.release_identity.descriptor, label="held release")
        duplicates.append(release_descriptor)
        plan_descriptor = _duplicate_descriptor(inputs.plan_identity.descriptor, label="held runtime plan")
        duplicates.append(plan_descriptor)
        bootstrap_descriptor = _duplicate_descriptor(inputs.bootstrap_identity.descriptor, label="held bootstrap")
        duplicates.append(bootstrap_descriptor)
        observed = verify_held_bootstrap_inputs(
            release_descriptor=release_descriptor,
            plan_descriptor=plan_descriptor,
            bootstrap_descriptor=bootstrap_descriptor,
            expected_uid=expected_uid,
        )
        if (
            observed.plan != inputs.plan
            or observed.reachable_blobs != inputs.reachable_blobs
            or observed.source_graph_sha256 != inputs.source_graph_sha256
            or not _same_held_object(observed.release_identity, inputs.release_identity)
            or not _same_held_object(observed.plan_identity, inputs.plan_identity)
            or not _same_held_object(observed.bootstrap_identity, inputs.bootstrap_identity)
        ):
            raise SourceSetRuntimeBootstrapError("held-FD bootstrap proof changed before activation")
        duplicates.clear()
        return HeldFdBootstrapCapability(observed, expected_uid=expected_uid)
    except Exception:
        for descriptor in duplicates:
            try:
                os.close(descriptor)
            except OSError:
                pass
        raise


def register_held_bootstrap_types(verifier_module: object) -> None:
    """Register this module instance's concrete types with a trusted verifier.

    A future separately root-installed bootstrap calls this only after it has
    completed held-FD/Git/blob proof and loaded the verifier through its own
    FD-pinned mechanism.  Passing the verifier explicitly avoids importing
    any release-sourced module here and preserves exact class identity when a
    bootstrap was loaded through a descriptor rather than a normal module
    path.  This is an in-process integration step, not a cross-process token
    or authority boundary.
    """

    register = getattr(verifier_module, "_register_held_bootstrap_types", None)
    if not callable(register):
        raise SourceSetRuntimeBootstrapError("trusted runtime verifier registration is unavailable")
    try:
        register(
            capability_type=HeldFdBootstrapCapability,
            lease_type=HeldBootstrapLease,
        )
    except Exception as exc:
        raise SourceSetRuntimeBootstrapError("trusted runtime verifier type registration failed") from exc


def _fd_argument(value: str) -> int:
    try:
        descriptor = int(value, 10)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("descriptor must be a decimal integer") from exc
    if descriptor < 3:
        raise argparse.ArgumentTypeError("descriptor must be at least 3")
    return descriptor


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-fd", type=_fd_argument, required=True)
    parser.add_argument("--plan-fd", type=_fd_argument, required=True)
    parser.add_argument("--bootstrap-fd", type=_fd_argument, required=True)
    parser.add_argument("operation", choices=("prove", "produce"))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    del argv
    print(
        "blocked: release-sourced bootstrap CLI is unavailable pending a separately installed immutable bootstrap",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
