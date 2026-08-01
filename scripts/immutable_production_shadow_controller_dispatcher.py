#!/usr/bin/env python3
"""Repository-local D1 prototype for immutable controller admission.

This module is deliberately proof-only.  It uses only the Python standard
library, never imports a release module, never changes ``sys.path``, and never
creates a runtime.  A future production installation must place an audited
copy outside the release tree behind a fixed root-owned launcher.  The public
CLI below is intentionally limited to repository-local test mode; production
invocation remains blocked.

The trust boundary is the held-plan digest map plus descriptor-bound Git/blob
proof.  Static Python analysis is not, and must never become, a sandbox.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import importlib.machinery
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
from types import MappingProxyType
from typing import Any, Mapping, Sequence
from uuid import UUID


PRODUCTION_PLAN_ROOT = Path("/etc/trading-bot-three-site/campaigns")
PRODUCTION_RELEASES_ROOT = Path("/srv/trading-bot-three-site/releases")
HELD_PLAN_FILENAME = "controller-runtime-closure-plan.json"
HELD_PLAN_SCHEMA = "production-shadow-controller-runtime-held-plan-v3"
PRE_RUNTIME_CLOSURE_SCOPE = "pre-runtime-controller-closure"
BOOTSTRAP_SOURCE = "scripts/production_shadow_convergence_source_set_runtime_bootstrap.py"
SOURCE_POLICY_SOURCE = (
    "deploy/production-shadow-controller-runtime/runtime-closure-policy.json"
)
REQUIREMENTS_SOURCE = "deploy/production-shadow-controller-runtime/requirements.lock"
WHEELHOUSE_SOURCE = "deploy/production-shadow-controller-runtime/controller-wheelhouse.sha256"
PRE_RUNTIME_SOURCE_PATHS = frozenset(
    {
        "scripts/__init__.py",
        BOOTSTRAP_SOURCE,
        "scripts/verify_production_shadow_controller_runtime_closure.py",
        "scripts/build_production_shadow_controller_runtime_closure.py",
        SOURCE_POLICY_SOURCE,
        REQUIREMENTS_SOURCE,
        WHEELHOUSE_SOURCE,
    }
)
POST_RUNTIME_SOURCE_PATHS = frozenset(
    {
        "scripts/produce_production_shadow_convergence_source_set.py",
        "scripts/orchestrate_production_shadow_convergence_gate.py",
        "scripts/production_shadow_cutover_controller.py",
        "scripts/verify_production_shadow_phase_evidence.py",
    }
)
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
MAX_PLAN_BYTES = 4 * 1024 * 1024
MAX_SOURCE_BYTES = 16 * 1024 * 1024
SHA256_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
SHA40_RE = re.compile(r"^[0-9a-f]{40}$", re.ASCII)
SAFE_COMPONENT_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._+\-]*$", re.ASCII)
SAFE_ENV = MappingProxyType(
    {
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
)
DISPATCHER_ENV = MappingProxyType(
    {
        "PATH": "/usr/bin:/bin",
        "HOME": "/nonexistent",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
    }
)


class ImmutableDispatcherError(RuntimeError):
    """The pre-runtime admission proof could not be established."""


@dataclass(frozen=True)
class DispatcherConfig:
    """Fixed roots and host TCB inputs for one proof-only invocation."""

    plan_root: Path
    releases_root: Path
    expected_uid: int = 0
    expected_gid: int = 0
    git_binary: Path = Path("/usr/bin/git")
    require_clean_process: bool = True
    repository_local_test_mode: bool = False


@dataclass(frozen=True)
class _DescriptorIdentity:
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
class _HeldPlan:
    campaign_id: str
    release_sha: str
    release_tree_sha: str
    source_policy_sha256: str
    wheelhouse_sha256: str
    wheel_input_receipt_sha256: str
    required_blobs: Mapping[str, str]
    sha256: str


def _identity(metadata: os.stat_result, *, descriptor: int) -> _DescriptorIdentity:
    return _DescriptorIdentity(
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


def _require_flag(name: str) -> int:
    value = getattr(os, name, None)
    if type(value) is not int or value == 0:
        raise ImmutableDispatcherError(f"host lacks required {name} descriptor flag")
    return value


def _directory_flags() -> int:
    return os.O_RDONLY | _require_flag("O_DIRECTORY") | _require_flag("O_CLOEXEC") | _require_flag("O_NOFOLLOW")


def _file_flags() -> int:
    return os.O_RDONLY | _require_flag("O_CLOEXEC") | _require_flag("O_NOFOLLOW") | getattr(os, "O_NONBLOCK", 0)


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ImmutableDispatcherError("held plan contains duplicate JSON fields")
        result[key] = value
    return result


def _canonical_json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise ImmutableDispatcherError("held plan is not canonical JSON") from exc


def _strict_json(raw: bytes) -> dict[str, Any]:
    if not raw:
        raise ImmutableDispatcherError("held plan is empty")
    try:
        value = json.loads(
            raw.decode("ascii"),
            object_pairs_hook=_strict_object,
            parse_constant=lambda item: (_ for _ in ()).throw(ValueError(item)),
        )
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise ImmutableDispatcherError("held plan is not strict ASCII JSON") from exc
    if not isinstance(value, dict) or _canonical_json_bytes(value) != raw:
        raise ImmutableDispatcherError("held plan is not canonical JSON")
    return value


def _require_campaign_id(value: Any) -> str:
    if not isinstance(value, str):
        raise ImmutableDispatcherError("campaign identifier is invalid")
    try:
        parsed = UUID(value)
    except (TypeError, ValueError, AttributeError) as exc:
        raise ImmutableDispatcherError("campaign identifier is invalid") from exc
    if parsed.int == 0 or str(parsed) != value:
        raise ImmutableDispatcherError("campaign identifier is invalid")
    return value


def _require_sha256(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None or value == "0" * 64:
        raise ImmutableDispatcherError(f"{label} is not a nonzero SHA-256")
    return value


def _require_sha40(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or SHA40_RE.fullmatch(value) is None:
        raise ImmutableDispatcherError(f"{label} is not a Git SHA-1")
    return value


def _safe_relative(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value or value.startswith("/") or "\\" in value:
        raise ImmutableDispatcherError(f"{label} path is invalid")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or not path.parts
        or any(
            part in {"", ".", ".."}
            or part.startswith(".")
            or SAFE_COMPONENT_RE.fullmatch(part) is None
            for part in path.parts
        )
        or path.parts[0] not in {"scripts", "deploy"}
    ):
        raise ImmutableDispatcherError(f"{label} path is invalid")
    return path.as_posix()


def _parse_plan(raw: bytes, *, expected_campaign_id: str) -> _HeldPlan:
    document = _strict_json(raw)
    if set(document) != PLAN_FIELDS or document.get("schema") != HELD_PLAN_SCHEMA:
        raise ImmutableDispatcherError("held plan schema or fields differ")
    campaign_id = _require_campaign_id(document.get("campaign_id"))
    if campaign_id != expected_campaign_id:
        raise ImmutableDispatcherError("held plan campaign differs")
    if document.get("closure_scope") != PRE_RUNTIME_CLOSURE_SCOPE:
        raise ImmutableDispatcherError("held plan closure scope differs")
    release = document.get("release")
    if not isinstance(release, dict) or set(release) != RELEASE_FIELDS:
        raise ImmutableDispatcherError("held plan release differs")
    bootstrap_path = _safe_relative(document.get("bootstrap_path"), label="held plan bootstrap")
    if bootstrap_path != BOOTSTRAP_SOURCE:
        raise ImmutableDispatcherError("held plan bootstrap path differs")
    blob_map = document.get("required_blobs")
    if not isinstance(blob_map, dict):
        raise ImmutableDispatcherError("held plan blob map differs")
    normalized: dict[str, str] = {}
    for path, digest in blob_map.items():
        relative = _safe_relative(path, label="held plan blob")
        if relative in normalized:
            raise ImmutableDispatcherError("held plan blob path is duplicated")
        normalized[relative] = _require_sha256(digest, label="held plan blob")
    if set(normalized) & POST_RUNTIME_SOURCE_PATHS:
        raise ImmutableDispatcherError("held plan binds unavailable post-runtime source")
    if set(normalized) != PRE_RUNTIME_SOURCE_PATHS:
        raise ImmutableDispatcherError("held plan does not bind the exact pre-runtime source set")
    if bootstrap_path not in normalized:
        raise ImmutableDispatcherError("held plan does not bind its bootstrap")
    source_policy_sha256 = _require_sha256(
        document.get("source_policy_sha256"), label="held plan source policy"
    )
    wheelhouse_sha256 = _require_sha256(
        document.get("controller_wheelhouse_sha256"), label="held plan wheelhouse"
    )
    if (
        normalized[SOURCE_POLICY_SOURCE] != source_policy_sha256
        or normalized[WHEELHOUSE_SOURCE] != wheelhouse_sha256
    ):
        raise ImmutableDispatcherError("held plan release input binding differs")
    return _HeldPlan(
        campaign_id=campaign_id,
        release_sha=_require_sha40(release.get("commit_sha"), label="held plan release commit"),
        release_tree_sha=_require_sha40(release.get("tree_sha"), label="held plan release tree"),
        source_policy_sha256=source_policy_sha256,
        wheelhouse_sha256=wheelhouse_sha256,
        wheel_input_receipt_sha256=_require_sha256(
            document.get("wheel_input_receipt_sha256"), label="held plan wheel input receipt"
        ),
        required_blobs=MappingProxyType({path: normalized[path] for path in sorted(normalized)}),
        sha256=hashlib.sha256(raw).hexdigest(),
    )


def _assert_secure_directory(
    descriptor: int,
    *,
    label: str,
    config: DispatcherConfig,
    exact_mode: int | None = None,
) -> _DescriptorIdentity:
    try:
        metadata = os.fstat(descriptor)
    except OSError as exc:
        raise ImmutableDispatcherError(f"{label} descriptor is unavailable") from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != config.expected_uid
        or metadata.st_gid != config.expected_gid
        or metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
        or (exact_mode is not None and stat.S_IMODE(metadata.st_mode) != exact_mode)
    ):
        raise ImmutableDispatcherError(f"{label} is not a root-controlled directory")
    return _identity(metadata, descriptor=descriptor)


def _assert_secure_regular(
    descriptor: int,
    *,
    label: str,
    config: DispatcherConfig,
    maximum: int,
    exact_mode: int | None = None,
) -> _DescriptorIdentity:
    try:
        metadata = os.fstat(descriptor)
    except OSError as exc:
        raise ImmutableDispatcherError(f"{label} descriptor is unavailable") from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != config.expected_uid
        or metadata.st_gid != config.expected_gid
        or metadata.st_nlink != 1
        or metadata.st_size < 1
        or metadata.st_size > maximum
        or metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
        or (exact_mode is not None and stat.S_IMODE(metadata.st_mode) != exact_mode)
    ):
        raise ImmutableDispatcherError(f"{label} is not a root-controlled regular file")
    return _identity(metadata, descriptor=descriptor)


def _assert_identity_current(identity: _DescriptorIdentity, *, label: str) -> None:
    try:
        current = _identity(os.fstat(identity.descriptor), descriptor=identity.descriptor)
    except OSError as exc:
        raise ImmutableDispatcherError(f"{label} descriptor is unavailable") from exc
    if current != identity:
        raise ImmutableDispatcherError(f"{label} descriptor changed")


def _validate_root_path(path: Path, *, label: str) -> None:
    if not isinstance(path, Path) or not path.is_absolute() or Path(os.path.normpath(os.fspath(path))) != path:
        raise ImmutableDispatcherError(f"{label} is not a canonical absolute path")


def _open_root_directory(path: Path, *, label: str, config: DispatcherConfig) -> tuple[int, _DescriptorIdentity]:
    _validate_root_path(path, label=label)
    try:
        before = path.lstat()
    except OSError as exc:
        raise ImmutableDispatcherError(f"{label} is unavailable") from exc
    if stat.S_ISLNK(before.st_mode):
        raise ImmutableDispatcherError(f"{label} must not be a symbolic link")
    try:
        descriptor = os.open(path, _directory_flags())
    except OSError as exc:
        raise ImmutableDispatcherError(f"{label} cannot be opened safely") from exc
    try:
        identity = _assert_secure_directory(
            descriptor, label=label, config=config, exact_mode=0o700
        )
        if (before.st_dev, before.st_ino) != (identity.device, identity.inode):
            raise ImmutableDispatcherError(f"{label} changed while being opened")
        return descriptor, identity
    except Exception:
        os.close(descriptor)
        raise


def _open_child_directory(
    parent: int,
    name: str,
    *,
    label: str,
    config: DispatcherConfig,
    exact_mode: int | None = None,
) -> tuple[int, _DescriptorIdentity]:
    if SAFE_COMPONENT_RE.fullmatch(name) is None:
        raise ImmutableDispatcherError(f"{label} name is invalid")
    try:
        descriptor = os.open(name, _directory_flags(), dir_fd=parent)
    except OSError as exc:
        raise ImmutableDispatcherError(f"{label} cannot be opened safely") from exc
    try:
        return descriptor, _assert_secure_directory(
            descriptor, label=label, config=config, exact_mode=exact_mode
        )
    except Exception:
        os.close(descriptor)
        raise


def _open_relative_regular(
    root: int,
    relative: str,
    *,
    label: str,
    config: DispatcherConfig,
    maximum: int,
    exact_mode: int | None = None,
) -> tuple[int, _DescriptorIdentity]:
    path = PurePosixPath(_safe_relative(relative, label=label))
    directory = os.dup(root)
    try:
        _assert_secure_directory(directory, label=f"{label} root", config=config)
        for part in path.parts[:-1]:
            child, _identity_value = _open_child_directory(
                directory, part, label=f"{label} directory", config=config
            )
            os.close(directory)
            directory = child
        try:
            descriptor = os.open(path.parts[-1], _file_flags(), dir_fd=directory)
        except OSError as exc:
            raise ImmutableDispatcherError(f"{label} cannot be opened safely") from exc
        try:
            return descriptor, _assert_secure_regular(
                descriptor,
                label=label,
                config=config,
                maximum=maximum,
                exact_mode=exact_mode,
            )
        except Exception:
            os.close(descriptor)
            raise
    finally:
        os.close(directory)


def _open_child_regular(
    parent: int,
    name: str,
    *,
    label: str,
    config: DispatcherConfig,
    maximum: int,
    exact_mode: int | None = None,
) -> tuple[int, _DescriptorIdentity]:
    """Open one non-path child without applying the release source policy."""

    if SAFE_COMPONENT_RE.fullmatch(name) is None:
        raise ImmutableDispatcherError(f"{label} name is invalid")
    try:
        descriptor = os.open(name, _file_flags(), dir_fd=parent)
    except OSError as exc:
        raise ImmutableDispatcherError(f"{label} cannot be opened safely") from exc
    try:
        return descriptor, _assert_secure_regular(
            descriptor,
            label=label,
            config=config,
            maximum=maximum,
            exact_mode=exact_mode,
        )
    except Exception:
        os.close(descriptor)
        raise


def _read_held_bytes(
    identity: _DescriptorIdentity,
    *,
    label: str,
    maximum: int,
) -> bytes:
    _assert_identity_current(identity, label=label)
    if identity.size > maximum:
        raise ImmutableDispatcherError(f"{label} exceeds its size limit")
    try:
        os.lseek(identity.descriptor, 0, os.SEEK_SET)
        payload = bytearray()
        while len(payload) <= maximum:
            block = os.read(identity.descriptor, min(1024 * 1024, maximum + 1 - len(payload)))
            if not block:
                break
            payload.extend(block)
    except OSError as exc:
        raise ImmutableDispatcherError(f"{label} cannot be read") from exc
    if len(payload) > maximum:
        raise ImmutableDispatcherError(f"{label} exceeds its size limit")
    _assert_identity_current(identity, label=label)
    return bytes(payload)


def _assert_trusted_git(config: DispatcherConfig) -> Path:
    candidate = config.git_binary
    if not isinstance(candidate, Path) or not candidate.is_absolute():
        raise ImmutableDispatcherError("immutable dispatcher Git binary is invalid")
    try:
        metadata = candidate.stat(follow_symlinks=False)
    except OSError as exc:
        raise ImmutableDispatcherError("immutable dispatcher Git binary is unavailable") from exc
    if (
        candidate.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_gid != 0
        or metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
        or not os.access(candidate, os.X_OK)
    ):
        raise ImmutableDispatcherError("immutable dispatcher Git binary is unsafe")
    return candidate


def _run_git(
    release_identity: _DescriptorIdentity,
    *,
    config: DispatcherConfig,
    arguments: tuple[str, ...],
    label: str,
    maximum: int,
) -> bytes:
    _assert_identity_current(release_identity, label="held release")
    git = _assert_trusted_git(config)
    command = (
        os.fspath(git),
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
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd="/",
            env=dict(SAFE_ENV),
            close_fds=True,
            pass_fds=(release_identity.descriptor,),
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ImmutableDispatcherError(f"{label} is unavailable") from exc
    if (
        completed.returncode != 0
        or len(completed.stdout) > maximum
        or len(completed.stderr) > MAX_PLAN_BYTES
    ):
        raise ImmutableDispatcherError(f"{label} failed")
    _assert_identity_current(release_identity, label="held release")
    return completed.stdout


def _git_text(
    release_identity: _DescriptorIdentity,
    *,
    config: DispatcherConfig,
    arguments: tuple[str, ...],
    label: str,
) -> str:
    try:
        return _run_git(
            release_identity,
            config=config,
            arguments=arguments,
            label=label,
            maximum=MAX_PLAN_BYTES,
        ).decode("ascii").strip()
    except UnicodeDecodeError as exc:
        raise ImmutableDispatcherError(f"{label} returned non-ASCII output") from exc


def _verify_git_state(
    release_identity: _DescriptorIdentity,
    plan: _HeldPlan,
    *,
    config: DispatcherConfig,
) -> None:
    observed = {
        "head": _git_text(
            release_identity, config=config, arguments=("rev-parse", "HEAD"), label="held release HEAD"
        ),
        "tree": _git_text(
            release_identity, config=config, arguments=("rev-parse", "HEAD^{tree}"), label="held release tree"
        ),
        "branch": _git_text(
            release_identity,
            config=config,
            arguments=("rev-parse", "--abbrev-ref", "HEAD"),
            label="held release branch",
        ),
        "status": _git_text(
            release_identity,
            config=config,
            arguments=("status", "--porcelain=v1", "--untracked-files=all", "--ignored=matching"),
            label="held release status",
        ),
        "remote": _git_text(
            release_identity, config=config, arguments=("remote",), label="held release remote"
        ),
    }
    if (
        observed["head"] != plan.release_sha
        or observed["tree"] != plan.release_tree_sha
        or observed["branch"] != "HEAD"
        or observed["status"]
        or observed["remote"]
    ):
        raise ImmutableDispatcherError("held release is not exact detached clean and remote-free")


def _git_blob(
    release_identity: _DescriptorIdentity,
    *,
    config: DispatcherConfig,
    release_sha: str,
    relative: str,
) -> bytes:
    return _run_git(
        release_identity,
        config=config,
        arguments=("cat-file", "blob", f"{release_sha}:{relative}"),
        label="held release Git blob",
        maximum=MAX_SOURCE_BYTES,
    )


def _release_identity_sha256(identity: _DescriptorIdentity) -> str:
    return hashlib.sha256(
        _canonical_json_bytes(
            {
                "schema": "production-shadow-immutable-dispatcher-release-identity-v1",
                "device": identity.device,
                "inode": identity.inode,
                "mode": stat.S_IMODE(identity.mode),
                "uid": identity.uid,
                "gid": identity.gid,
            }
        )
    ).hexdigest()


def _open_descriptor_numbers() -> set[int]:
    try:
        entries = os.listdir("/proc/self/fd")
    except OSError as exc:
        raise ImmutableDispatcherError("dispatcher descriptor table is unavailable") from exc
    result: set[int] = set()
    for entry in entries:
        if not entry.isdecimal():
            raise ImmutableDispatcherError("dispatcher descriptor table is invalid")
        descriptor = int(entry)
        try:
            os.fstat(descriptor)
        except OSError as exc:
            if exc.errno == getattr(os, "EBADF", 9):
                continue
            raise ImmutableDispatcherError("dispatcher descriptor table cannot be inspected") from exc
        result.add(descriptor)
    return result


def require_clean_dispatcher_process(*, expected_uid: int, expected_gid: int) -> None:
    """Validate the pre-release interpreter and descriptor boundary.

    This is intentionally executed before opening a plan or release descriptor.
    It is a launcher admission check, not a substitute for descriptor/Git proof.
    """

    if os.geteuid() != expected_uid or os.getegid() != expected_gid:
        raise ImmutableDispatcherError("dispatcher identity differs")
    flags = sys.flags
    if (
        getattr(flags, "isolated", 0) != 1
        or getattr(flags, "ignore_environment", 0) != 1
        or getattr(flags, "no_site", 0) != 1
        or getattr(flags, "dont_write_bytecode", 0) != 1
        or getattr(flags, "utf8_mode", 0) != 1
        or not bool(getattr(flags, "safe_path", False))
    ):
        raise ImmutableDispatcherError("dispatcher requires isolated Python -I -S -B -X utf8")
    if dict(os.environ) != dict(DISPATCHER_ENV):
        raise ImmutableDispatcherError("dispatcher inherited an unexpected environment")
    expected_meta_path = {
        importlib.machinery.BuiltinImporter,
        importlib.machinery.FrozenImporter,
        importlib.machinery.PathFinder,
    }
    if set(sys.meta_path) != expected_meta_path:
        raise ImmutableDispatcherError("dispatcher import hook state differs")
    if any(not isinstance(entry, str) or not entry.startswith("/") for entry in sys.path):
        raise ImmutableDispatcherError("dispatcher import path is unsafe")
    forbidden_prefixes = ("core", "scripts", "site", "sitecustomize", "usercustomize")
    if any(
        name == prefix or name.startswith(f"{prefix}.")
        for name in sys.modules
        for prefix in forbidden_prefixes
    ):
        raise ImmutableDispatcherError("dispatcher preloaded a project or site module")
    if _open_descriptor_numbers() != {0, 1, 2}:
        raise ImmutableDispatcherError("dispatcher inherited an unexpected descriptor")


def _validate_config(config: DispatcherConfig) -> None:
    if not isinstance(config, DispatcherConfig):
        raise ImmutableDispatcherError("dispatcher configuration is invalid")
    _validate_root_path(config.plan_root, label="dispatcher plan root")
    _validate_root_path(config.releases_root, label="dispatcher releases root")
    if config.expected_uid < 0 or config.expected_gid < 0:
        raise ImmutableDispatcherError("dispatcher ownership configuration is invalid")
    if not config.repository_local_test_mode:
        raise ImmutableDispatcherError("immutable dispatcher prototype is limited to local test roots")
    if (
        config.plan_root == PRODUCTION_PLAN_ROOT
        or config.releases_root == PRODUCTION_RELEASES_ROOT
        or config.plan_root.is_relative_to(PRODUCTION_PLAN_ROOT)
        or config.releases_root.is_relative_to(PRODUCTION_RELEASES_ROOT)
    ):
        raise ImmutableDispatcherError("immutable dispatcher prototype refuses production roots")


def prove_pre_runtime(campaign_id: str, *, config: DispatcherConfig) -> dict[str, object]:
    """Prove one exact v3 pre-runtime source set without executing it."""

    _validate_config(config)
    campaign = _require_campaign_id(campaign_id)
    if config.require_clean_process:
        require_clean_dispatcher_process(
            expected_uid=config.expected_uid, expected_gid=config.expected_gid
        )
    descriptors: list[int] = []
    try:
        plan_root_fd, _plan_root_identity = _open_root_directory(
            config.plan_root, label="dispatcher plan root", config=config
        )
        descriptors.append(plan_root_fd)
        campaign_fd, _campaign_identity = _open_child_directory(
            plan_root_fd,
            campaign,
            label="dispatcher campaign directory",
            config=config,
            exact_mode=0o700,
        )
        descriptors.append(campaign_fd)
        plan_fd, plan_identity = _open_child_regular(
            campaign_fd,
            HELD_PLAN_FILENAME,
            label="held controller runtime plan",
            config=config,
            maximum=MAX_PLAN_BYTES,
            exact_mode=0o600,
        )
        descriptors.append(plan_fd)
        plan_raw = _read_held_bytes(
            plan_identity, label="held controller runtime plan", maximum=MAX_PLAN_BYTES
        )
        plan = _parse_plan(plan_raw, expected_campaign_id=campaign)
        releases_root_fd, _releases_root_identity = _open_root_directory(
            config.releases_root, label="dispatcher releases root", config=config
        )
        descriptors.append(releases_root_fd)
        release_fd, release_identity = _open_child_directory(
            releases_root_fd,
            plan.release_sha,
            label="held release",
            config=config,
            exact_mode=0o700,
        )
        descriptors.append(release_fd)
        bootstrap_fd, bootstrap_identity = _open_relative_regular(
            release_fd,
            BOOTSTRAP_SOURCE,
            label="held release bootstrap",
            config=config,
            maximum=MAX_SOURCE_BYTES,
        )
        descriptors.append(bootstrap_fd)
        bootstrap_raw = _read_held_bytes(
            bootstrap_identity, label="held release bootstrap", maximum=MAX_SOURCE_BYTES
        )
        _verify_git_state(release_identity, plan, config=config)
        for relative, expected_sha256 in plan.required_blobs.items():
            if relative == BOOTSTRAP_SOURCE:
                raw = bootstrap_raw
            else:
                descriptor, identity = _open_relative_regular(
                    release_fd,
                    relative,
                    label=f"held release blob {relative}",
                    config=config,
                    maximum=MAX_SOURCE_BYTES,
                )
                try:
                    raw = _read_held_bytes(
                        identity, label=f"held release blob {relative}", maximum=MAX_SOURCE_BYTES
                    )
                finally:
                    os.close(descriptor)
            if hashlib.sha256(raw).hexdigest() != expected_sha256:
                raise ImmutableDispatcherError("held release blob digest differs from plan")
            if _git_blob(
                release_identity,
                config=config,
                release_sha=plan.release_sha,
                relative=relative,
            ) != raw:
                raise ImmutableDispatcherError("held release blob differs from its Git object")
        _assert_identity_current(plan_identity, label="held controller runtime plan")
        _assert_identity_current(bootstrap_identity, label="held release bootstrap")
        _assert_identity_current(release_identity, label="held release")
        return {
            "schema": "production-shadow-immutable-dispatcher-proof-v1",
            "status": "pre_runtime_proven",
            "campaign_id": plan.campaign_id,
            "release_sha": plan.release_sha,
            "release_tree_sha": plan.release_tree_sha,
            "held_plan_sha256": plan.sha256,
            "bootstrap_sha256": hashlib.sha256(bootstrap_raw).hexdigest(),
            "required_blob_count": len(plan.required_blobs),
            "release_identity_sha256": _release_identity_sha256(release_identity),
            "release_python_executed": False,
            "runtime_created": False,
        }
    finally:
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except OSError:
                pass


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-local-test-mode", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--test-plan-root", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--test-releases-root", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--campaign-id")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if (
        not args.repository_local_test_mode
        or args.test_plan_root is None
        or args.test_releases_root is None
        or args.campaign_id is None
    ):
        print(
            "blocked: repository-local immutable dispatcher prototype is not a production installation",
            file=sys.stderr,
        )
        return 69
    config = DispatcherConfig(
        plan_root=args.test_plan_root,
        releases_root=args.test_releases_root,
        expected_uid=os.geteuid(),
        expected_gid=os.getegid(),
        require_clean_process=True,
        repository_local_test_mode=True,
    )
    try:
        result = prove_pre_runtime(args.campaign_id, config=config)
    except ImmutableDispatcherError as exc:
        print(f"blocked: immutable dispatcher pre-runtime proof failed: {exc}", file=sys.stderr)
        return 70
    print(json.dumps(result, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
