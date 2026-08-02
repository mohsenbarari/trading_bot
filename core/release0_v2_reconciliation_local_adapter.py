"""Hardened local-only adapter for the additive Release-0 V2 guard.

This module is deliberately *not* a command-line tool.  It provides concrete
filesystem adapters for :mod:`core.release0_v2_reconciliation_inventory` and
:mod:`core.release0_v2_reconciliation_materializer`, but never constructs a
materialization configuration, enables one, commits Git, builds an image, or
contacts a host, registry, or object store.

The adapter accepts only standalone, root-controlled Git worktrees below
``/srv``.  It treats both the checkpoint and the Release-0 target as hostile
until their Git identity, complete on-disk tree, ownership, modes, paths and
content hashes have been independently checked.  Every pathname operation is
anchored to a directory descriptor and uses ``O_NOFOLLOW``.

There is no portable POSIX primitive that atomically commits additions across
the three pre-existing directory trees used by this overlay.  Accordingly,
each final pathname is committed atomically and create-only: content is
fsync'ed in an ``O_EXCL`` temporary file and then hard-linked into a previously
absent final name.  A failure never returns a success observation or receipt;
already committed final paths remain as fail-closed forensic evidence and only
the private temporary files created by this invocation are removed.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
from typing import Iterator, Sequence

from core.release0_v2_reconciliation_inventory import (
    ADDITIVE_V2_CLOSURE_PATHS,
    ADDITIVE_V2_CLOSURE_PATH_SET,
    RECONCILIATION_CHECKPOINT_SHA,
    RECONCILIATION_CHECKPOINT_TREE,
    RELEASE0_RECONCILIATION_BASELINE_SHA,
    RELEASE0_RECONCILIATION_BASELINE_TREE,
    Release0ReconciliationError,
    Release0ReconciliationFileObservation,
    Release0ReconciliationInventoryEntry,
    Release0ReconciliationSourceInspection,
    Release0ReconciliationSourceObject,
)
from core.release0_v2_reconciliation_materializer import (
    RELEASE0_V2_RECONCILIATION_OVERLAY_SCHEMA,
    RELEASE0_V2_RECONCILIATION_TRANSFER_SCHEMA,
    Release0V2ReconciliationMaterializationRequest,
    Release0V2ReconciliationTargetOverlayInspection,
    Release0V2ReconciliationTransferObservation,
)


__all__ = (
    "LOCAL_RECONCILIATION_SERVICES_ROOT",
    "Release0V2ReconciliationLocalAdapter",
    "Release0V2ReconciliationLocalAdapterError",
)


# This is deliberately a module constant, not an environment variable or a
# caller-supplied configuration value.  Test code may patch it only inside its
# own process; production callers have no path escape hatch.
LOCAL_RECONCILIATION_SERVICES_ROOT = Path("/srv")
# A fixed operating-system binary is used instead of caller ``PATH``.  The
# adapter is intentionally Linux/Ubuntu-oriented (it already requires dirfd,
# O_NOFOLLOW and fcntl semantics), so failing closed on another layout is
# safer than discovering an executable dynamically.
_LOCAL_RECONCILIATION_GIT_BINARY = Path("/usr/bin/git")

_SAFE_COMPONENT_RE = re.compile(r"^[A-Za-z0-9._-]{1,120}$", re.ASCII)
_HEX_BY_OBJECT_FORMAT = {"sha1": 40, "sha256": 64}
_GIT_MODE_TO_FILESYSTEM_MODE = {"100644": 0o644, "100755": 0o755}
_MAX_GIT_OUTPUT_BYTES = 16 * 1024 * 1024
_MAX_TREE_ENTRIES = 10_000
_MAX_TREE_BYTES = 64 * 1024 * 1024
_MAX_FILE_BYTES = 8 * 1024 * 1024
_READ_CHUNK_BYTES = 1024 * 1024
_TEMP_ATTEMPTS = 32


class Release0V2ReconciliationLocalAdapterError(Release0ReconciliationError):
    """A fail-closed refusal from the concrete local adapter."""


def _fail(code: str) -> None:
    raise Release0V2ReconciliationLocalAdapterError(code)


def _canonical(value: object, *, code: str) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeEncodeError):
        _fail(code)


def _sha256_evidence(value: object, *, code: str) -> str:
    return hashlib.sha256(_canonical(value, code=code)).hexdigest()


def _require_root_runtime() -> None:
    if os.geteuid() != 0:
        _fail("RELEASE0_V2_LOCAL_ADAPTER_ROOT_RUNTIME_REQUIRED")


def _require_nofollow_flags() -> tuple[int, int, int]:
    nofollow = getattr(os, "O_NOFOLLOW", None)
    directory = getattr(os, "O_DIRECTORY", None)
    cloexec = getattr(os, "O_CLOEXEC", 0)
    if type(nofollow) is not int or nofollow == 0:
        _fail("RELEASE0_V2_LOCAL_ADAPTER_O_NOFOLLOW_REQUIRED")
    if type(directory) is not int or directory == 0:
        _fail("RELEASE0_V2_LOCAL_ADAPTER_O_DIRECTORY_REQUIRED")
    if type(cloexec) is not int:
        _fail("RELEASE0_V2_LOCAL_ADAPTER_O_CLOEXEC_INVALID")
    return nofollow, directory, cloexec


def _secure_metadata(
    value: os.stat_result,
    *,
    directory: bool,
    regular: bool,
    code: str,
) -> None:
    if value.st_uid != 0 or stat.S_IMODE(value.st_mode) & 0o022:
        _fail(code)
    if directory and not stat.S_ISDIR(value.st_mode):
        _fail(code)
    if regular and not stat.S_ISREG(value.st_mode):
        _fail(code)
    if not directory and not regular:
        _fail(code)


def _path_components(value: Path, *, code: str) -> tuple[str, ...]:
    if not isinstance(value, Path) or not value.is_absolute() or ".." in value.parts:
        _fail(code)
    parts = value.parts
    if not parts or parts[0] != os.sep:
        _fail(code)
    components = tuple(parts[1:])
    if any(_SAFE_COMPONENT_RE.fullmatch(part) is None for part in components):
        _fail(code)
    return components


def _relative_components(value: str, *, code: str) -> tuple[str, ...]:
    if not isinstance(value, str) or not value or value.startswith("/") or value.endswith("/"):
        _fail(code)
    parts = tuple(value.split("/"))
    if (
        not parts
        or any(part in {"", ".", ".."} for part in parts)
        or any(_SAFE_COMPONENT_RE.fullmatch(part) is None for part in parts)
    ):
        _fail(code)
    return parts


def _relative_path(value: str, *, code: str) -> str:
    return "/".join(_relative_components(value, code=code))


def _fd_fingerprint(value: os.stat_result) -> tuple[int, int, int, int, int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_uid,
        value.st_gid,
        stat.S_IMODE(value.st_mode),
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _open_secure_absolute_directory(path: Path) -> int:
    """Open one root-owned, no-symlink absolute directory path by descriptor."""

    components = _path_components(
        path, code="RELEASE0_V2_LOCAL_ADAPTER_ABSOLUTE_ROOT_INVALID"
    )
    nofollow, directory, cloexec = _require_nofollow_flags()
    flags = os.O_RDONLY | nofollow | directory | cloexec
    descriptor = -1
    try:
        descriptor = os.open(os.sep, flags)
        _secure_metadata(
            os.fstat(descriptor),
            directory=True,
            regular=False,
            code="RELEASE0_V2_LOCAL_ADAPTER_ANCESTOR_UNSAFE",
        )
        for component in components:
            child = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
            _secure_metadata(
                os.fstat(descriptor),
                directory=True,
                regular=False,
                code="RELEASE0_V2_LOCAL_ADAPTER_ANCESTOR_UNSAFE",
            )
        return descriptor
    except Release0V2ReconciliationLocalAdapterError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError:
        if descriptor >= 0:
            os.close(descriptor)
        _fail("RELEASE0_V2_LOCAL_ADAPTER_ANCESTOR_OPEN_FAILED")


def _open_child_directory(parent_descriptor: int, name: str, *, code: str) -> int:
    if _SAFE_COMPONENT_RE.fullmatch(name) is None:
        _fail(code)
    nofollow, directory, cloexec = _require_nofollow_flags()
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY | nofollow | directory | cloexec,
            dir_fd=parent_descriptor,
        )
    except OSError:
        _fail(code)
    try:
        _secure_metadata(
            os.fstat(descriptor), directory=True, regular=False, code=code
        )
        return descriptor
    except Release0V2ReconciliationLocalAdapterError:
        os.close(descriptor)
        raise


@contextmanager
def _open_trusted_services_root() -> Iterator[int]:
    descriptor = _open_secure_absolute_directory(LOCAL_RECONCILIATION_SERVICES_ROOT)
    try:
        yield descriptor
    finally:
        os.close(descriptor)


def _local_root_components(root: Path) -> tuple[str, ...]:
    if not isinstance(root, Path) or not root.is_absolute() or ".." in root.parts:
        _fail("RELEASE0_V2_LOCAL_ADAPTER_ROOT_INVALID")
    services_root = LOCAL_RECONCILIATION_SERVICES_ROOT
    if not isinstance(services_root, Path) or not services_root.is_absolute():
        _fail("RELEASE0_V2_LOCAL_ADAPTER_SERVICES_ROOT_INVALID")
    try:
        relative = root.relative_to(services_root)
    except ValueError:
        _fail("RELEASE0_V2_LOCAL_ADAPTER_ROOT_OUTSIDE_SERVICES")
    if relative == Path("."):
        _fail("RELEASE0_V2_LOCAL_ADAPTER_ROOT_IS_SERVICES_ROOT")
    parts = tuple(relative.parts)
    if not parts or any(_SAFE_COMPONENT_RE.fullmatch(part) is None for part in parts):
        _fail("RELEASE0_V2_LOCAL_ADAPTER_ROOT_INVALID")
    return parts


@contextmanager
def _open_local_root(root: Path) -> Iterator[int]:
    """Open a root-controlled non-symlink directory strictly below ``/srv``."""

    components = _local_root_components(root)
    with _open_trusted_services_root() as services_descriptor:
        descriptor = os.dup(services_descriptor)
        try:
            for component in components:
                child = _open_child_directory(
                    descriptor,
                    component,
                    code="RELEASE0_V2_LOCAL_ADAPTER_ROOT_OPEN_FAILED",
                )
                os.close(descriptor)
                descriptor = child
            yield descriptor
        finally:
            os.close(descriptor)


def _open_parent_directory(root_descriptor: int, parts: Sequence[str], *, code: str) -> int:
    descriptor = os.dup(root_descriptor)
    try:
        for component in parts:
            child = _open_child_directory(descriptor, component, code=code)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _assert_secure_git_directory(root_descriptor: int) -> None:
    """Require a standalone root-owned ``.git`` directory, never a worktree file."""

    git_descriptor = _open_child_directory(
        root_descriptor, ".git", code="RELEASE0_V2_LOCAL_ADAPTER_GIT_DIR_INVALID"
    )
    try:
        for directory_name in ("objects", "refs"):
            child = _open_child_directory(
                git_descriptor,
                directory_name,
                code="RELEASE0_V2_LOCAL_ADAPTER_GIT_DIR_INVALID",
            )
            os.close(child)
        nofollow, _directory, cloexec = _require_nofollow_flags()
        for file_name in ("HEAD", "config", "index"):
            try:
                descriptor = os.open(
                    file_name,
                    os.O_RDONLY | nofollow | cloexec,
                    dir_fd=git_descriptor,
                )
            except OSError:
                _fail("RELEASE0_V2_LOCAL_ADAPTER_GIT_METADATA_INVALID")
            try:
                _secure_metadata(
                    os.fstat(descriptor),
                    directory=False,
                    regular=True,
                    code="RELEASE0_V2_LOCAL_ADAPTER_GIT_METADATA_INVALID",
                )
            finally:
                os.close(descriptor)
    finally:
        os.close(git_descriptor)


def _secure_git_binary() -> str:
    path = _LOCAL_RECONCILIATION_GIT_BINARY
    if not isinstance(path, Path) or not path.is_absolute() or ".." in path.parts:
        _fail("RELEASE0_V2_LOCAL_ADAPTER_GIT_UNSAFE")
    if _SAFE_COMPONENT_RE.fullmatch(path.name) is None:
        _fail("RELEASE0_V2_LOCAL_ADAPTER_GIT_UNSAFE")
    parent = -1
    descriptor = -1
    try:
        parent = _open_secure_absolute_directory(path.parent)
        nofollow, _directory, cloexec = _require_nofollow_flags()
        descriptor = os.open(
            path.name,
            os.O_RDONLY | nofollow | cloexec,
            dir_fd=parent,
        )
        metadata = os.fstat(descriptor)
    except (OSError, Release0V2ReconciliationLocalAdapterError):
        _fail("RELEASE0_V2_LOCAL_ADAPTER_GIT_UNSAFE")
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if parent >= 0:
            os.close(parent)
    _secure_metadata(
        metadata,
        directory=False,
        regular=True,
        code="RELEASE0_V2_LOCAL_ADAPTER_GIT_UNSAFE",
    )
    if stat.S_IMODE(metadata.st_mode) & 0o111 == 0:
        _fail("RELEASE0_V2_LOCAL_ADAPTER_GIT_UNSAFE")
    return str(path)


def _git_environment() -> dict[str, str]:
    # Do not inherit *any* caller environment.  In particular, a merely
    # stripped ``GIT_*`` environment would still leave dynamic-loader and
    # helper-process variables such as ``LD_PRELOAD`` or ``SSH_ASKPASS`` in a
    # root-owned Git subprocess.  The plumbing commands below need none.
    return {
        "PATH": os.defpath,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_PAGER": "cat",
        "GIT_OPTIONAL_LOCKS": "0",
        "HOME": os.devnull,
        "LC_ALL": "C",
        "LANG": "C",
    }


def _git(root: Path, *arguments: str) -> bytes:
    command = [
        _secure_git_binary(),
        "--no-optional-locks",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.untrackedCache=false",
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "core.pager=cat",
        "-C",
        str(root),
        *arguments,
    ]
    try:
        completed = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            # Diagnostics from a hostile local Git configuration are neither
            # needed for this fail-closed boundary nor safe to retain in
            # memory or logs.
            stderr=subprocess.DEVNULL,
            env=_git_environment(),
            check=False,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired):
        _fail("RELEASE0_V2_LOCAL_ADAPTER_GIT_COMMAND_FAILED")
    if completed.returncode != 0 or len(completed.stdout) > _MAX_GIT_OUTPUT_BYTES:
        _fail("RELEASE0_V2_LOCAL_ADAPTER_GIT_COMMAND_FAILED")
    return completed.stdout


def _git_single_ascii(root: Path, *arguments: str) -> str:
    value = _git(root, *arguments)
    if not value.endswith(b"\n") or value.count(b"\n") != 1:
        _fail("RELEASE0_V2_LOCAL_ADAPTER_GIT_OUTPUT_INVALID")
    try:
        result = value[:-1].decode("ascii")
    except UnicodeDecodeError:
        _fail("RELEASE0_V2_LOCAL_ADAPTER_GIT_OUTPUT_INVALID")
    if not result:
        _fail("RELEASE0_V2_LOCAL_ADAPTER_GIT_OUTPUT_INVALID")
    return result


def _require_object_id(value: str, object_format: str, *, code: str) -> str:
    expected_length = _HEX_BY_OBJECT_FORMAT.get(object_format)
    if (
        expected_length is None
        or not isinstance(value, str)
        or len(value) != expected_length
        or any(character not in "0123456789abcdef" for character in value)
    ):
        _fail(code)
    return value


@dataclass(frozen=True)
class _GitState:
    head: str
    tree: str
    object_format: str


def _git_state(root: Path) -> _GitState:
    object_format = _git_single_ascii(root, "rev-parse", "--show-object-format=storage")
    if object_format not in _HEX_BY_OBJECT_FORMAT:
        _fail("RELEASE0_V2_LOCAL_ADAPTER_GIT_OBJECT_FORMAT_INVALID")
    head = _require_object_id(
        _git_single_ascii(root, "rev-parse", "--verify", "HEAD^{commit}"),
        object_format,
        code="RELEASE0_V2_LOCAL_ADAPTER_GIT_HEAD_INVALID",
    )
    tree = _require_object_id(
        _git_single_ascii(root, "rev-parse", "--verify", "HEAD^{tree}"),
        object_format,
        code="RELEASE0_V2_LOCAL_ADAPTER_GIT_TREE_INVALID",
    )
    # A direct descriptor-anchored tree scan below is the clean verification.
    # It is stronger than ``git status`` (which may honor ignore rules or
    # assume-unchanged index bits) and avoids running a config-defined filter
    # process while this adapter is supposed to stay local-only.
    return _GitState(head=head, tree=tree, object_format=object_format)


@dataclass(frozen=True)
class _GitTreeEntry:
    relative_path: str
    mode: int
    object_id: str


def _git_tree_entries(root: Path, state: _GitState) -> tuple[_GitTreeEntry, ...]:
    raw = _git(root, "ls-tree", "-r", "-z", state.tree)
    if raw and not raw.endswith(b"\0"):
        _fail("RELEASE0_V2_LOCAL_ADAPTER_GIT_TREE_OUTPUT_INVALID")
    entries: list[_GitTreeEntry] = []
    seen: set[str] = set()
    for record in raw.split(b"\0"):
        if not record:
            continue
        try:
            metadata, encoded_path = record.split(b"\t", 1)
            encoded_mode, encoded_type, encoded_object = metadata.split(b" ", 2)
            mode_name = encoded_mode.decode("ascii")
            object_type = encoded_type.decode("ascii")
            object_id = encoded_object.decode("ascii")
            relative_path = encoded_path.decode("ascii")
        except (UnicodeDecodeError, ValueError):
            _fail("RELEASE0_V2_LOCAL_ADAPTER_GIT_TREE_OUTPUT_INVALID")
        mode = _GIT_MODE_TO_FILESYSTEM_MODE.get(mode_name)
        if mode is None or object_type != "blob":
            _fail("RELEASE0_V2_LOCAL_ADAPTER_GIT_TREE_ENTRY_INVALID")
        _require_object_id(
            object_id,
            state.object_format,
            code="RELEASE0_V2_LOCAL_ADAPTER_GIT_TREE_ENTRY_INVALID",
        )
        _relative_path(
            relative_path, code="RELEASE0_V2_LOCAL_ADAPTER_GIT_TREE_ENTRY_INVALID"
        )
        if relative_path in seen:
            _fail("RELEASE0_V2_LOCAL_ADAPTER_GIT_TREE_ENTRY_INVALID")
        seen.add(relative_path)
        entries.append(
            _GitTreeEntry(
                relative_path=relative_path, mode=mode, object_id=object_id
            )
        )
        if len(entries) > _MAX_TREE_ENTRIES:
            _fail("RELEASE0_V2_LOCAL_ADAPTER_TREE_ENTRY_LIMIT_EXCEEDED")
    if tuple(entry.relative_path for entry in entries) != tuple(
        sorted(entry.relative_path for entry in entries)
    ):
        _fail("RELEASE0_V2_LOCAL_ADAPTER_GIT_TREE_OUTPUT_INVALID")
    return tuple(entries)


@dataclass(frozen=True)
class _ReadFile:
    content: bytes
    owner_uid: int
    mode: int
    stable: bool


def _read_relative_file(root_descriptor: int, relative_path: str) -> _ReadFile:
    parts = _relative_components(
        relative_path, code="RELEASE0_V2_LOCAL_ADAPTER_FILE_PATH_INVALID"
    )
    parent = _open_parent_directory(
        root_descriptor,
        parts[:-1],
        code="RELEASE0_V2_LOCAL_ADAPTER_FILE_PARENT_UNSAFE",
    )
    try:
        nofollow, _directory, cloexec = _require_nofollow_flags()
        try:
            descriptor = os.open(
                parts[-1],
                os.O_RDONLY | nofollow | cloexec,
                dir_fd=parent,
            )
        except OSError:
            _fail("RELEASE0_V2_LOCAL_ADAPTER_FILE_OPEN_FAILED")
        try:
            before = os.fstat(descriptor)
            _secure_metadata(
                before,
                directory=False,
                regular=True,
                code="RELEASE0_V2_LOCAL_ADAPTER_FILE_UNSAFE",
            )
            if before.st_nlink != 1 or before.st_size < 0 or before.st_size > _MAX_FILE_BYTES:
                _fail("RELEASE0_V2_LOCAL_ADAPTER_FILE_UNSAFE")
            chunks: list[bytes] = []
            remaining = before.st_size
            while remaining:
                chunk = os.read(descriptor, min(_READ_CHUNK_BYTES, remaining))
                if not chunk:
                    _fail("RELEASE0_V2_LOCAL_ADAPTER_FILE_CHANGED")
                chunks.append(chunk)
                remaining -= len(chunk)
            if os.read(descriptor, 1):
                _fail("RELEASE0_V2_LOCAL_ADAPTER_FILE_CHANGED")
            after = os.fstat(descriptor)
            stable = _fd_fingerprint(before) == _fd_fingerprint(after)
            if not stable:
                _fail("RELEASE0_V2_LOCAL_ADAPTER_FILE_CHANGED")
            content = b"".join(chunks)
            if len(content) != before.st_size:
                _fail("RELEASE0_V2_LOCAL_ADAPTER_FILE_CHANGED")
            return _ReadFile(
                content=content,
                owner_uid=before.st_uid,
                mode=stat.S_IMODE(before.st_mode),
                stable=True,
            )
        finally:
            os.close(descriptor)
    finally:
        os.close(parent)


@dataclass(frozen=True)
class _WorktreeScan:
    files: dict[str, os.stat_result]
    directories: frozenset[str]


def _scan_worktree(root_descriptor: int) -> _WorktreeScan:
    files: dict[str, os.stat_result] = {}
    directories: set[str] = set()
    total_bytes = 0

    def walk(descriptor: int, prefix: tuple[str, ...]) -> None:
        nonlocal total_bytes
        try:
            names = os.listdir(descriptor)
        except OSError:
            _fail("RELEASE0_V2_LOCAL_ADAPTER_TREE_SCAN_FAILED")
        for name in sorted(names):
            if not prefix and name == ".git":
                continue
            if _SAFE_COMPONENT_RE.fullmatch(name) is None:
                _fail("RELEASE0_V2_LOCAL_ADAPTER_TREE_PATH_INVALID")
            try:
                metadata = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            except OSError:
                _fail("RELEASE0_V2_LOCAL_ADAPTER_TREE_SCAN_FAILED")
            relative_path = "/".join((*prefix, name))
            if stat.S_ISLNK(metadata.st_mode):
                _fail("RELEASE0_V2_LOCAL_ADAPTER_TREE_SYMLINK_REJECTED")
            if stat.S_ISDIR(metadata.st_mode):
                _secure_metadata(
                    metadata,
                    directory=True,
                    regular=False,
                    code="RELEASE0_V2_LOCAL_ADAPTER_TREE_DIRECTORY_UNSAFE",
                )
                directories.add(relative_path)
                if len(directories) + len(files) > _MAX_TREE_ENTRIES:
                    _fail("RELEASE0_V2_LOCAL_ADAPTER_TREE_ENTRY_LIMIT_EXCEEDED")
                child = _open_child_directory(
                    descriptor,
                    name,
                    code="RELEASE0_V2_LOCAL_ADAPTER_TREE_DIRECTORY_UNSAFE",
                )
                try:
                    walk(child, (*prefix, name))
                finally:
                    os.close(child)
                continue
            _secure_metadata(
                metadata,
                directory=False,
                regular=True,
                code="RELEASE0_V2_LOCAL_ADAPTER_TREE_FILE_UNSAFE",
            )
            if metadata.st_nlink != 1 or metadata.st_size < 0 or metadata.st_size > _MAX_FILE_BYTES:
                _fail("RELEASE0_V2_LOCAL_ADAPTER_TREE_FILE_UNSAFE")
            files[relative_path] = metadata
            total_bytes += metadata.st_size
            if len(directories) + len(files) > _MAX_TREE_ENTRIES:
                _fail("RELEASE0_V2_LOCAL_ADAPTER_TREE_ENTRY_LIMIT_EXCEEDED")
            if total_bytes > _MAX_TREE_BYTES:
                _fail("RELEASE0_V2_LOCAL_ADAPTER_TREE_BYTES_EXCEEDED")

    walk(root_descriptor, ())
    return _WorktreeScan(files=files, directories=frozenset(directories))


def _expected_directories(paths: Sequence[str]) -> frozenset[str]:
    result: set[str] = set()
    for relative_path in paths:
        parts = _relative_components(
            relative_path, code="RELEASE0_V2_LOCAL_ADAPTER_TREE_PATH_INVALID"
        )
        for length in range(1, len(parts)):
            result.add("/".join(parts[:length]))
    return frozenset(result)


def _git_blob_hash(content: bytes, object_format: str) -> str:
    try:
        digest = hashlib.new(object_format)
    except ValueError:
        _fail("RELEASE0_V2_LOCAL_ADAPTER_GIT_OBJECT_FORMAT_INVALID")
    digest.update(f"blob {len(content)}\0".encode("ascii"))
    digest.update(content)
    return digest.hexdigest()


def _verify_exact_tree(
    *,
    root_descriptor: int,
    entries: Sequence[_GitTreeEntry],
    object_format: str,
    additive_paths: Sequence[str] = (),
) -> str:
    """Directly prove every tracked baseline byte and every allowed extra path."""

    baseline_paths = tuple(entry.relative_path for entry in entries)
    if len(baseline_paths) != len(set(baseline_paths)):
        _fail("RELEASE0_V2_LOCAL_ADAPTER_GIT_TREE_ENTRY_INVALID")
    extra_paths = tuple(additive_paths)
    if len(extra_paths) != len(set(extra_paths)):
        _fail("RELEASE0_V2_LOCAL_ADAPTER_OVERLAY_LAYOUT_INVALID")
    if set(baseline_paths) & set(extra_paths):
        _fail("RELEASE0_V2_LOCAL_ADAPTER_OVERLAY_LAYOUT_INVALID")
    expected_paths = set(baseline_paths) | set(extra_paths)
    scan = _scan_worktree(root_descriptor)
    if set(scan.files) != expected_paths or scan.directories != _expected_directories(
        tuple(expected_paths)
    ):
        _fail("RELEASE0_V2_LOCAL_ADAPTER_TREE_LAYOUT_MISMATCH")
    evidence: list[dict[str, object]] = []
    for entry in entries:
        observed = _read_relative_file(root_descriptor, entry.relative_path)
        if observed.mode != entry.mode:
            _fail("RELEASE0_V2_LOCAL_ADAPTER_TREE_MODE_MISMATCH")
        if _git_blob_hash(observed.content, object_format) != entry.object_id:
            _fail("RELEASE0_V2_LOCAL_ADAPTER_TREE_HASH_MISMATCH")
        evidence.append(
            {
                "path": entry.relative_path,
                "mode": entry.mode,
                "object_id": entry.object_id,
            }
        )
    for path in sorted(extra_paths):
        observed = _read_relative_file(root_descriptor, path)
        if observed.mode not in _GIT_MODE_TO_FILESYSTEM_MODE.values():
            _fail("RELEASE0_V2_LOCAL_ADAPTER_OVERLAY_MODE_INVALID")
        evidence.append(
            {
                "path": path,
                "mode": observed.mode,
                "sha256": hashlib.sha256(observed.content).hexdigest(),
            }
        )
    return _sha256_evidence(
        evidence, code="RELEASE0_V2_LOCAL_ADAPTER_TREE_EVIDENCE_INVALID"
    )


def _assert_disjoint_roots(
    *,
    source_root: Path,
    target_root: Path,
    source_descriptor: int,
    target_descriptor: int,
) -> None:
    if source_root == target_root:
        _fail("RELEASE0_V2_LOCAL_ADAPTER_SOURCE_TARGET_CONFLATED")
    try:
        common = Path(os.path.commonpath((str(source_root), str(target_root))))
    except ValueError:
        _fail("RELEASE0_V2_LOCAL_ADAPTER_SOURCE_TARGET_CONFLATED")
    if common == source_root or common == target_root:
        _fail("RELEASE0_V2_LOCAL_ADAPTER_SOURCE_TARGET_CONFLATED")
    source_stat = os.fstat(source_descriptor)
    target_stat = os.fstat(target_descriptor)
    if (source_stat.st_dev, source_stat.st_ino) == (
        target_stat.st_dev,
        target_stat.st_ino,
    ):
        _fail("RELEASE0_V2_LOCAL_ADAPTER_SOURCE_TARGET_CONFLATED")


@contextmanager
def _exclusive_target_lock(target_descriptor: int) -> Iterator[None]:
    try:
        fcntl.flock(target_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        _fail("RELEASE0_V2_LOCAL_ADAPTER_TARGET_LOCK_UNAVAILABLE")
    try:
        yield
    finally:
        try:
            fcntl.flock(target_descriptor, fcntl.LOCK_UN)
        except OSError:
            _fail("RELEASE0_V2_LOCAL_ADAPTER_TARGET_LOCK_RELEASE_FAILED")


def _validate_request(
    request: object,
) -> Release0V2ReconciliationMaterializationRequest:
    if type(request) is not Release0V2ReconciliationMaterializationRequest:
        _fail("RELEASE0_V2_LOCAL_ADAPTER_REQUEST_INVALID")
    value = request
    if (
        tuple(entry.relative_path for entry in value.entries)
        != ADDITIVE_V2_CLOSURE_PATHS
        or len(value.entries) != len(ADDITIVE_V2_CLOSURE_PATHS)
    ):
        _fail("RELEASE0_V2_LOCAL_ADAPTER_REQUEST_PATHS_INVALID")
    total = 0
    for entry in value.entries:
        if type(entry) is not Release0ReconciliationInventoryEntry:
            _fail("RELEASE0_V2_LOCAL_ADAPTER_REQUEST_ENTRY_INVALID")
        if entry.relative_path not in ADDITIVE_V2_CLOSURE_PATH_SET:
            _fail("RELEASE0_V2_LOCAL_ADAPTER_REQUEST_PATHS_INVALID")
        if entry.mode not in {"0644", "0755"}:
            _fail("RELEASE0_V2_LOCAL_ADAPTER_REQUEST_ENTRY_INVALID")
        if type(entry.size_bytes) is not int or not 0 <= entry.size_bytes <= _MAX_FILE_BYTES:
            _fail("RELEASE0_V2_LOCAL_ADAPTER_REQUEST_ENTRY_INVALID")
        if (
            not isinstance(entry.sha256, str)
            or len(entry.sha256) != 64
            or any(character not in "0123456789abcdef" for character in entry.sha256)
        ):
            _fail("RELEASE0_V2_LOCAL_ADAPTER_REQUEST_ENTRY_INVALID")
        total += entry.size_bytes
    if total > _MAX_TREE_BYTES:
        _fail("RELEASE0_V2_LOCAL_ADAPTER_REQUEST_TOTAL_BYTES_EXCEEDED")
    return value


@dataclass(frozen=True)
class _PreparedPayload:
    entry: Release0ReconciliationInventoryEntry
    content: bytes


def _prepare_source_payloads(
    *,
    source_descriptor: int,
    source_entries: dict[str, _GitTreeEntry],
    request_entries: Sequence[Release0ReconciliationInventoryEntry],
    object_format: str,
) -> tuple[_PreparedPayload, ...]:
    prepared: list[_PreparedPayload] = []
    for entry in request_entries:
        source_entry = source_entries.get(entry.relative_path)
        if source_entry is None:
            _fail("RELEASE0_V2_LOCAL_ADAPTER_SOURCE_PATH_MISSING")
        observed = _read_relative_file(source_descriptor, entry.relative_path)
        if (
            observed.mode != int(entry.mode, 8)
            or len(observed.content) != entry.size_bytes
            or hashlib.sha256(observed.content).hexdigest() != entry.sha256
            or _git_blob_hash(observed.content, object_format) != source_entry.object_id
        ):
            _fail("RELEASE0_V2_LOCAL_ADAPTER_SOURCE_REHASH_MISMATCH")
        prepared.append(_PreparedPayload(entry=entry, content=observed.content))
    return tuple(prepared)


@dataclass
class _StagedTargetFile:
    payload: _PreparedPayload
    parent_descriptor: int
    final_name: str
    temporary_name: str
    temporary_inode: int
    committed: bool = False


def _create_private_temporary(
    parent_descriptor: int,
    *,
    content: bytes,
    mode: int,
) -> tuple[str, int]:
    nofollow, _directory, cloexec = _require_nofollow_flags()
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | nofollow | cloexec
    for _attempt in range(_TEMP_ATTEMPTS):
        name = ".release0-v2-" + secrets.token_hex(16) + ".tmp"
        try:
            descriptor = os.open(name, flags, mode, dir_fd=parent_descriptor)
        except FileExistsError:
            continue
        except OSError:
            _fail("RELEASE0_V2_LOCAL_ADAPTER_TEMP_CREATE_FAILED")
        try:
            written = 0
            while written < len(content):
                amount = os.write(descriptor, content[written:])
                if amount <= 0:
                    _fail("RELEASE0_V2_LOCAL_ADAPTER_TEMP_WRITE_FAILED")
                written += amount
            os.fchmod(descriptor, mode)
            os.fsync(descriptor)
            metadata = os.fstat(descriptor)
            _secure_metadata(
                metadata,
                directory=False,
                regular=True,
                code="RELEASE0_V2_LOCAL_ADAPTER_TEMP_UNSAFE",
            )
            if (
                metadata.st_nlink != 1
                or stat.S_IMODE(metadata.st_mode) != mode
                or metadata.st_size != len(content)
            ):
                _fail("RELEASE0_V2_LOCAL_ADAPTER_TEMP_UNSAFE")
            return name, metadata.st_ino
        except Release0V2ReconciliationLocalAdapterError:
            try:
                os.unlink(name, dir_fd=parent_descriptor)
            except OSError:
                pass
            raise
        except OSError:
            try:
                os.unlink(name, dir_fd=parent_descriptor)
            except OSError:
                pass
            _fail("RELEASE0_V2_LOCAL_ADAPTER_TEMP_WRITE_FAILED")
        finally:
            os.close(descriptor)
    _fail("RELEASE0_V2_LOCAL_ADAPTER_TEMP_NAME_EXHAUSTED")


def _unlink_private_temporary(staged: _StagedTargetFile) -> None:
    try:
        metadata = os.stat(
            staged.temporary_name,
            dir_fd=staged.parent_descriptor,
            follow_symlinks=False,
        )
    except FileNotFoundError:
        return
    except OSError:
        _fail("RELEASE0_V2_LOCAL_ADAPTER_TEMP_CLEANUP_FAILED")
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_ino != staged.temporary_inode
        or metadata.st_nlink != (2 if staged.committed else 1)
    ):
        _fail("RELEASE0_V2_LOCAL_ADAPTER_TEMP_CLEANUP_UNSAFE")
    try:
        os.unlink(staged.temporary_name, dir_fd=staged.parent_descriptor)
        os.fsync(staged.parent_descriptor)
    except OSError:
        _fail("RELEASE0_V2_LOCAL_ADAPTER_TEMP_CLEANUP_FAILED")


def _materialize_create_only(
    *,
    target_descriptor: int,
    payloads: Sequence[_PreparedPayload],
) -> None:
    staged: list[_StagedTargetFile] = []
    try:
        for payload in payloads:
            parts = _relative_components(
                payload.entry.relative_path,
                code="RELEASE0_V2_LOCAL_ADAPTER_REQUEST_PATHS_INVALID",
            )
            parent = _open_parent_directory(
                target_descriptor,
                parts[:-1],
                code="RELEASE0_V2_LOCAL_ADAPTER_TARGET_PARENT_UNSAFE",
            )
            name, inode = _create_private_temporary(
                parent,
                content=payload.content,
                mode=int(payload.entry.mode, 8),
            )
            staged.append(
                _StagedTargetFile(
                    payload=payload,
                    parent_descriptor=parent,
                    final_name=parts[-1],
                    temporary_name=name,
                    temporary_inode=inode,
                )
            )
        for item in staged:
            try:
                os.link(
                    item.temporary_name,
                    item.final_name,
                    src_dir_fd=item.parent_descriptor,
                    dst_dir_fd=item.parent_descriptor,
                    follow_symlinks=False,
                )
                item.committed = True
                metadata = os.stat(
                    item.final_name,
                    dir_fd=item.parent_descriptor,
                    follow_symlinks=False,
                )
                if (
                    not stat.S_ISREG(metadata.st_mode)
                    or metadata.st_ino != item.temporary_inode
                    or metadata.st_nlink != 2
                    or metadata.st_uid != 0
                    or stat.S_IMODE(metadata.st_mode) != int(item.payload.entry.mode, 8)
                    or metadata.st_size != item.payload.entry.size_bytes
                ):
                    _fail("RELEASE0_V2_LOCAL_ADAPTER_TARGET_COMMIT_UNSAFE")
                os.fsync(item.parent_descriptor)
            except FileExistsError:
                _fail("RELEASE0_V2_LOCAL_ADAPTER_TARGET_PATH_ALREADY_EXISTS")
            except Release0V2ReconciliationLocalAdapterError:
                raise
            except OSError:
                _fail("RELEASE0_V2_LOCAL_ADAPTER_TARGET_COMMIT_FAILED")
        for item in staged:
            _unlink_private_temporary(item)
    except BaseException:
        for item in reversed(staged):
            try:
                _unlink_private_temporary(item)
            except Release0V2ReconciliationLocalAdapterError:
                # A failed cleanup is intentionally not hidden by a later
                # cleanup.  The original failure has already prevented a
                # success observation.
                pass
        raise
    finally:
        for item in reversed(staged):
            try:
                os.close(item.parent_descriptor)
            except OSError:
                pass


def _assert_expected_state(
    state: _GitState,
    *,
    expected_head: str,
    expected_tree: str,
    code: str,
) -> None:
    if state.head != expected_head or state.tree != expected_tree:
        _fail(code)


def _overlay_evidence(
    *,
    root_descriptor: int,
    target_root: Path,
    state: _GitState,
    expected_tree: str,
) -> str:
    entries = _git_tree_entries(target_root, state)
    baseline_evidence = _verify_exact_tree(
        root_descriptor=root_descriptor,
        entries=entries,
        object_format=state.object_format,
        additive_paths=ADDITIVE_V2_CLOSURE_PATHS,
    )
    return _sha256_evidence(
        {
            "baseline_tree": expected_tree,
            "baseline_evidence": baseline_evidence,
            "overlay_paths": ADDITIVE_V2_CLOSURE_PATHS,
        },
        code="RELEASE0_V2_LOCAL_ADAPTER_OVERLAY_EVIDENCE_INVALID",
    )


class Release0V2ReconciliationLocalAdapter:
    """Concrete, local-only implementation of all reconciliation protocols.

    The object has no configuration or activation switch.  The existing
    materializer remains default-off and must explicitly be given this object
    by a future local-only caller after separate authorization.
    """

    def inspect_source(
        self, *, source_root: Path
    ) -> Release0ReconciliationSourceInspection:
        _require_root_runtime()
        with _open_local_root(source_root) as root_descriptor:
            before = _fd_fingerprint(os.fstat(root_descriptor))
            _assert_secure_git_directory(root_descriptor)
            state = _git_state(source_root)
            entries = _git_tree_entries(source_root, state)
            _verify_exact_tree(
                root_descriptor=root_descriptor,
                entries=entries,
                object_format=state.object_format,
            )
            after = _fd_fingerprint(os.fstat(root_descriptor))
            metadata = os.fstat(root_descriptor)
            return Release0ReconciliationSourceInspection(
                source_root=Release0ReconciliationSourceObject(
                    path=source_root,
                    owner_uid=metadata.st_uid,
                    mode=stat.S_IMODE(metadata.st_mode),
                    directory=True,
                    symlink=False,
                    ancestors_root_controlled=True,
                ),
                release_sha=state.head,
                git_tree_id=state.tree,
                clean=True,
                stable=before == after,
            )

    def read_file(
        self, *, source_root: Path, relative_path: str
    ) -> Release0ReconciliationFileObservation:
        _require_root_runtime()
        if relative_path not in ADDITIVE_V2_CLOSURE_PATH_SET:
            _fail("RELEASE0_V2_LOCAL_ADAPTER_FILE_PATH_NOT_ALLOWED")
        with _open_local_root(source_root) as root_descriptor:
            _assert_secure_git_directory(root_descriptor)
            state = _git_state(source_root)
            entries = {
                entry.relative_path: entry
                for entry in _git_tree_entries(source_root, state)
            }
            observed = _read_relative_file(root_descriptor, relative_path)
            source_entry = entries.get(relative_path)
            if source_entry is not None and (
                observed.mode != source_entry.mode
                or _git_blob_hash(observed.content, state.object_format)
                != source_entry.object_id
            ):
                _fail("RELEASE0_V2_LOCAL_ADAPTER_FILE_GIT_HASH_MISMATCH")
            return Release0ReconciliationFileObservation(
                relative_path=relative_path,
                owner_uid=observed.owner_uid,
                mode=observed.mode,
                regular_file=True,
                symlink=False,
                stable=observed.stable,
                content=observed.content,
            )

    def materialize_additive_overlay(
        self, *, request: Release0V2ReconciliationMaterializationRequest
    ) -> Release0V2ReconciliationTransferObservation:
        """Create the exact overlay through atomic, never-replacing file links."""

        _require_root_runtime()
        value = _validate_request(request)
        with _open_local_root(value.source_root) as source_descriptor, _open_local_root(
            value.target_root
        ) as target_descriptor:
            _assert_disjoint_roots(
                source_root=value.source_root,
                target_root=value.target_root,
                source_descriptor=source_descriptor,
                target_descriptor=target_descriptor,
            )
            _assert_secure_git_directory(source_descriptor)
            _assert_secure_git_directory(target_descriptor)
            with _exclusive_target_lock(target_descriptor):
                source_state = _git_state(value.source_root)
                target_state = _git_state(value.target_root)
                _assert_expected_state(
                    source_state,
                    expected_head=RECONCILIATION_CHECKPOINT_SHA,
                    expected_tree=RECONCILIATION_CHECKPOINT_TREE,
                    code="RELEASE0_V2_LOCAL_ADAPTER_SOURCE_CHECKPOINT_REJECTED",
                )
                _assert_expected_state(
                    target_state,
                    expected_head=RELEASE0_RECONCILIATION_BASELINE_SHA,
                    expected_tree=RELEASE0_RECONCILIATION_BASELINE_TREE,
                    code="RELEASE0_V2_LOCAL_ADAPTER_TARGET_BASELINE_REJECTED",
                )
                source_entries = _git_tree_entries(value.source_root, source_state)
                target_entries = _git_tree_entries(value.target_root, target_state)
                _verify_exact_tree(
                    root_descriptor=source_descriptor,
                    entries=source_entries,
                    object_format=source_state.object_format,
                )
                _verify_exact_tree(
                    root_descriptor=target_descriptor,
                    entries=target_entries,
                    object_format=target_state.object_format,
                )
                prepared = _prepare_source_payloads(
                    source_descriptor=source_descriptor,
                    source_entries={
                        entry.relative_path: entry for entry in source_entries
                    },
                    request_entries=value.entries,
                    object_format=source_state.object_format,
                )
                _materialize_create_only(
                    target_descriptor=target_descriptor, payloads=prepared
                )
                post_state = _git_state(value.target_root)
                _assert_expected_state(
                    post_state,
                    expected_head=RELEASE0_RECONCILIATION_BASELINE_SHA,
                    expected_tree=RELEASE0_RECONCILIATION_BASELINE_TREE,
                    code="RELEASE0_V2_LOCAL_ADAPTER_TARGET_POSTSTATE_REJECTED",
                )
                overlay_evidence = _overlay_evidence(
                    root_descriptor=target_descriptor,
                    target_root=value.target_root,
                    state=post_state,
                    expected_tree=RELEASE0_RECONCILIATION_BASELINE_TREE,
                )
                transfer_evidence = _sha256_evidence(
                    {
                        "source_head": source_state.head,
                        "source_tree": source_state.tree,
                        "target_head": target_state.head,
                        "target_tree": target_state.tree,
                        "inventory_manifest_sha256": value.inventory_manifest_sha256,
                        "entries": [
                            {
                                "path": item.entry.relative_path,
                                "mode": item.entry.mode,
                                "size_bytes": item.entry.size_bytes,
                                "sha256": item.entry.sha256,
                            }
                            for item in prepared
                        ],
                        "overlay_evidence": overlay_evidence,
                        # This means each final pathname was committed by an
                        # atomic create-only hard-link, not an impossible
                        # cross-directory transaction.
                        "per_path_atomic_create_only": True,
                    },
                    code="RELEASE0_V2_LOCAL_ADAPTER_TRANSFER_EVIDENCE_INVALID",
                )
                return Release0V2ReconciliationTransferObservation(
                    schema=RELEASE0_V2_RECONCILIATION_TRANSFER_SCHEMA,
                    status="transferred",
                    inventory_manifest_sha256=value.inventory_manifest_sha256,
                    materialized_paths=ADDITIVE_V2_CLOSURE_PATHS,
                    unexpected_paths=(),
                    replaced_release0_paths=(),
                    source_read_no_follow=True,
                    target_write_no_follow=True,
                    atomically_committed=True,
                    transfer_evidence_sha256=transfer_evidence,
                )

    def inspect_additive_overlay(
        self,
        *,
        target_root: Path,
        expected_release0_sha: str,
        expected_release0_tree: str,
    ) -> Release0V2ReconciliationTargetOverlayInspection:
        _require_root_runtime()
        if (
            expected_release0_sha != RELEASE0_RECONCILIATION_BASELINE_SHA
            or expected_release0_tree != RELEASE0_RECONCILIATION_BASELINE_TREE
        ):
            _fail("RELEASE0_V2_LOCAL_ADAPTER_TARGET_PIN_INVALID")
        with _open_local_root(target_root) as target_descriptor:
            before = _fd_fingerprint(os.fstat(target_descriptor))
            _assert_secure_git_directory(target_descriptor)
            state = _git_state(target_root)
            _assert_expected_state(
                state,
                expected_head=RELEASE0_RECONCILIATION_BASELINE_SHA,
                expected_tree=RELEASE0_RECONCILIATION_BASELINE_TREE,
                code="RELEASE0_V2_LOCAL_ADAPTER_TARGET_POSTSTATE_REJECTED",
            )
            evidence = _overlay_evidence(
                root_descriptor=target_descriptor,
                target_root=target_root,
                state=state,
                expected_tree=RELEASE0_RECONCILIATION_BASELINE_TREE,
            )
            after = _fd_fingerprint(os.fstat(target_descriptor))
            return Release0V2ReconciliationTargetOverlayInspection(
                schema=RELEASE0_V2_RECONCILIATION_OVERLAY_SCHEMA,
                status="target-observed",
                target_root=target_root,
                release0_baseline_sha=RELEASE0_RECONCILIATION_BASELINE_SHA,
                release0_baseline_tree=RELEASE0_RECONCILIATION_BASELINE_TREE,
                stable=before == after,
                changed_paths=ADDITIVE_V2_CLOSURE_PATHS,
                unexpected_paths=(),
                replaced_release0_paths=(),
                no_symlink_paths=True,
                release0_bytes_rehashed=True,
                release0_content_tree=RELEASE0_RECONCILIATION_BASELINE_TREE,
                target_git_commit_created=False,
                release_seal_created=False,
                evidence_sha256=evidence,
            )
