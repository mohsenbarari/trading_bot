#!/usr/bin/env python3
"""Assemble one exact, non-Docker Release-0 build context.

The Fenced-FI candidate build-input manifest deliberately leaves the clean
application checkout untouched.  This helper is the next local-only boundary:
it re-verifies that immutable manifest, its static manifest, the named clean
Git checkout, and the named static tree, then copies only those verified bytes
into one fresh root-only context directory.  It never invokes Docker, npm, a
registry, Object Storage, SSH, a peer, or a service.

The context receipt is intentionally separate from the Docker context.  It is
canonical, create-only, and non-authorizing; a future signed candidate
identity can bind its hash without changing either existing input schema.

The build-input schema records a source pathname and Git identities, not a
portable physical-clone attestation.  Accordingly, ``independent`` here means
an exact clean checkout at a distinct, non-overlapping safe pathname.  The
receipt intentionally makes no claim about physical, host, or Git-common-dir
independence.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
from typing import Any, Mapping, Sequence
from uuid import uuid4


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from scripts import prepare_fenced_fi_candidate_build_inputs as build_inputs  # noqa: E402


BUILD_CONTEXT_RECEIPT_SCHEMA = "gold-trade-release0-fenced-fi-build-context-receipt-v1"
BUILD_CONTEXT_RECEIPT_STATUS = "prepared-non-authorizing"
FRONTEND_DIST_DIRECTORY = build_inputs.FRONTEND_DIST_DIRECTORY
LEGACY_UNFENCED_APPLICATION_RELEASE_SHA = build_inputs.LEGACY_UNFENCED_APPLICATION_RELEASE_SHA

MAX_MANIFEST_BYTES = build_inputs.MAX_MANIFEST_BYTES
MAX_GIT_INDEX_BYTES = 32 * 1024 * 1024
MAX_GIT_CONFIG_BYTES = 1024 * 1024
MAX_SOURCE_FILES = 100_000
MAX_SOURCE_FILE_BYTES = 128 * 1024 * 1024
MAX_SOURCE_TOTAL_BYTES = 512 * 1024 * 1024
MAX_CONTEXT_TOTAL_BYTES = MAX_SOURCE_TOTAL_BYTES + build_inputs.MAX_STATIC_TOTAL_BYTES
RECEIPT_RESERVE_BYTES = 64 * 1024
CONTEXT_MARGIN_BYTES = 16 * 1024 * 1024
COPY_CHUNK_BYTES = 1024 * 1024

SHA1_RE = re.compile(r"^[0-9a-f]{40}$", re.ASCII)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)

_RECEIPT_FIELDS = frozenset(
    {
        "schema",
        "status",
        "application",
        "inputs",
        "context",
        "writer_authorized",
        "promotion_authorized",
        "deployment_authorized",
        "execution_authorized",
        "full_matrix_authorized",
        "full_matrix_executed",
    }
)
_RECEIPT_APPLICATION_FIELDS = frozenset(
    {
        "release_sha",
        "release_tree_sha",
        "dockerfile_sha256",
        "dockerignore_sha256",
        "files_sha256",
        "file_count",
        "total_bytes",
    }
)
_RECEIPT_INPUT_FIELDS = frozenset(
    {
        "build_input_manifest_sha256",
        "term_fenced_application_evidence_sha256",
        "manifest_sha256",
        "files_sha256",
        "file_count",
        "total_bytes",
    }
)
_RECEIPT_CONTEXT_FIELDS = frozenset(
    {
        "files_sha256",
        "file_count",
        "total_bytes",
        "directories_sha256",
        "directory_count",
    }
)


class FencedFiCandidateBuildContextError(RuntimeError):
    """A Release-0 build-context input or output is unsafe."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class FileSnapshot:
    relative: str
    sha256: str
    bytes: int
    mode: int
    device: int
    inode: int
    uid: int
    gid: int | None
    nlink: int
    mtime_ns: int
    ctime_ns: int
    git_blob_sha1: str | None = None

    def public(self) -> dict[str, object]:
        value: dict[str, object] = {
            "path": self.relative,
            "sha256": self.sha256,
            "bytes": self.bytes,
            "mode": format(self.mode, "04o"),
        }
        if self.git_blob_sha1 is not None:
            value["git_blob_sha1"] = self.git_blob_sha1
        return value


@dataclass(frozen=True)
class SourceSnapshot:
    root: Path
    release_sha: str
    release_tree_sha: str
    files: tuple[FileSnapshot, ...]

    @property
    def total_bytes(self) -> int:
        return sum(item.bytes for item in self.files)

    @property
    def files_sha256(self) -> str:
        return _files_sha256([item.public() for item in self.files])


@dataclass(frozen=True)
class DirectorySnapshot:
    relative: str
    mode: int
    device: int
    inode: int
    uid: int
    gid: int
    mtime_ns: int
    ctime_ns: int

    def public(self) -> dict[str, object]:
        return {"path": self.relative, "mode": format(self.mode, "04o")}


@dataclass(frozen=True)
class ContextSnapshot:
    root: Path
    directories: tuple[DirectorySnapshot, ...]
    files: tuple[FileSnapshot, ...]

    @property
    def total_bytes(self) -> int:
        return sum(item.bytes for item in self.files)

    @property
    def files_sha256(self) -> str:
        return _files_sha256([item.public() for item in self.files])

    @property
    def directories_sha256(self) -> str:
        return _directories_sha256([item.public() for item in self.directories])


def _fail(code: str) -> None:
    raise FencedFiCandidateBuildContextError(code)


def _require_root() -> None:
    if os.geteuid() != 0:
        _fail("FENCED_FI_BUILD_CONTEXT_ROOT_REQUIRED")


def _canonical_json_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError, RecursionError) as exc:
        raise FencedFiCandidateBuildContextError(
            "FENCED_FI_BUILD_CONTEXT_JSON_INVALID"
        ) from exc


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("FENCED_FI_BUILD_CONTEXT_JSON_DUPLICATE_FIELD")
        result[key] = value
    return result


def _reject_json_constant(_: str) -> None:
    _fail("FENCED_FI_BUILD_CONTEXT_JSON_INVALID")


def _canonical_absolute_path(value: Path | str, *, label: str) -> Path:
    if not isinstance(value, (Path, str)):
        _fail(f"FENCED_FI_BUILD_CONTEXT_{label}_PATH_INVALID")
    raw = str(value)
    path = Path(raw)
    if (
        not raw
        or "\x00" in raw
        or not path.is_absolute()
        or raw.startswith("//")
        or any(part in {"", ".", ".."} for part in path.parts[1:])
        or raw != os.path.normpath(raw)
    ):
        _fail(f"FENCED_FI_BUILD_CONTEXT_{label}_PATH_INVALID")
    return path


def _require_safe_directory_chain(path: Path, *, label: str) -> None:
    path = _canonical_absolute_path(path, label=label)
    current = Path(path.anchor)
    for component in path.parts[1:]:
        current /= component
        try:
            metadata = current.lstat()
        except OSError as exc:
            raise FencedFiCandidateBuildContextError(
                f"FENCED_FI_BUILD_CONTEXT_{label}_ANCESTOR_UNAVAILABLE"
            ) from exc
        mode = stat.S_IMODE(metadata.st_mode)
        sticky_root_directory = (
            stat.S_ISDIR(metadata.st_mode)
            and metadata.st_uid == os.geteuid()
            and bool(mode & stat.S_ISVTX)
        )
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or ((mode & 0o022) and not sticky_root_directory)
        ):
            _fail(f"FENCED_FI_BUILD_CONTEXT_{label}_ANCESTOR_UNSAFE")


def _require_root_directory(path: Path, *, label: str, private: bool) -> Path:
    path = _canonical_absolute_path(path, label=label)
    _require_safe_directory_chain(path.parent, label=label)
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise FencedFiCandidateBuildContextError(
            f"FENCED_FI_BUILD_CONTEXT_{label}_UNAVAILABLE"
        ) from exc
    mode = stat.S_IMODE(metadata.st_mode)
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or (mode != 0o700 if private else bool(mode & (0o022 | 0o7000)))
    ):
        _fail(f"FENCED_FI_BUILD_CONTEXT_{label}_UNSAFE")
    return path


def _no_follow() -> int:
    value = getattr(os, "O_NOFOLLOW", None)
    if type(value) is not int:
        _fail("FENCED_FI_BUILD_CONTEXT_O_NOFOLLOW_REQUIRED")
    return value


def _read_private_regular(path: Path, *, label: str, maximum_bytes: int) -> bytes:
    path = _canonical_absolute_path(path, label=label)
    _require_safe_directory_chain(path.parent, label=label)
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | _no_follow())
    except OSError as exc:
        raise FencedFiCandidateBuildContextError(
            f"FENCED_FI_BUILD_CONTEXT_{label}_UNAVAILABLE"
        ) from exc
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.geteuid()
            or stat.S_IMODE(before.st_mode) & 0o077
            or before.st_nlink != 1
            or not 1 <= before.st_size <= maximum_bytes
        ):
            _fail(f"FENCED_FI_BUILD_CONTEXT_{label}_UNSAFE")
        chunks: list[bytes] = []
        remaining = before.st_size
        while remaining:
            chunk = os.read(descriptor, min(COPY_CHUNK_BYTES, remaining))
            if not chunk:
                _fail(f"FENCED_FI_BUILD_CONTEXT_{label}_SHORT_READ")
            chunks.append(chunk)
            remaining -= len(chunk)
        after = os.fstat(descriptor)
        if _stat_identity(before) != _stat_identity(after):
            _fail(f"FENCED_FI_BUILD_CONTEXT_{label}_CHANGED")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _stat_identity(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_uid,
        value.st_gid,
        value.st_nlink,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _safe_source_relative(value: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value or value.startswith("/"):
        _fail("FENCED_FI_BUILD_CONTEXT_SOURCE_PATH_INVALID")
    path = PurePosixPath(value)
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError:
        _fail("FENCED_FI_BUILD_CONTEXT_SOURCE_PATH_INVALID")
    if (
        path.as_posix() != value
        or any(part in {"", ".", ".."} for part in path.parts)
        or any(ord(character) < 0x20 for character in value)
        or len(encoded) > 1024
        or ".git" in path.parts
    ):
        _fail("FENCED_FI_BUILD_CONTEXT_SOURCE_PATH_INVALID")
    if path.parts[0] == FRONTEND_DIST_DIRECTORY:
        _fail("FENCED_FI_BUILD_CONTEXT_SOURCE_STATIC_PATH_OVERLAP")
    return value


def _safe_context_relative(value: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value or value.startswith("/"):
        _fail("FENCED_FI_BUILD_CONTEXT_OUTPUT_PATH_INVALID")
    path = PurePosixPath(value)
    if (
        path.as_posix() != value
        or any(part in {"", ".", ".."} for part in path.parts)
        or any(ord(character) < 0x20 for character in value)
        or ".git" in path.parts
    ):
        _fail("FENCED_FI_BUILD_CONTEXT_OUTPUT_PATH_INVALID")
    return value


def _run_git(root: Path, *arguments: str, maximum_bytes: int = MAX_GIT_INDEX_BYTES) -> bytes:
    """Run a local-only Git query with lazy network fetches prohibited.

    The source tree is an input, not an authority to contact a remote.  The
    environment is deliberately complete rather than inherited so an
    operator's ``GIT_*`` values cannot turn a verification query into a
    transport action.  ``_require_local_git_object_store`` additionally
    rejects partial/promisor and alternate object stores before any command
    requiring tree/blob objects is issued.
    """

    if isinstance(maximum_bytes, bool) or not isinstance(maximum_bytes, int) or maximum_bytes < 1:
        _fail("FENCED_FI_BUILD_CONTEXT_GIT_REJECTED")
    try:
        trusted_git = build_inputs._trusted_git()
        result = subprocess.run(
            [
                str(trusted_git),
                "--no-optional-locks",
                "-c",
                "core.fsmonitor=false",
                "-c",
                "core.hooksPath=/dev/null",
                "-c",
                "core.pager=cat",
                "-C",
                str(root),
                *arguments,
            ],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=20,
            env={
                "PATH": "/usr/bin:/bin",
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": "/dev/null",
                "GIT_CONFIG_SYSTEM": "/dev/null",
                "GIT_OPTIONAL_LOCKS": "0",
                "GIT_EXTERNAL_DIFF": "",
                "GIT_PAGER": "cat",
                "GIT_TERMINAL_PROMPT": "0",
                "GIT_NO_LAZY_FETCH": "1",
                "GIT_ALLOW_PROTOCOL": "none",
                "GIT_NO_REPLACE_OBJECTS": "1",
            },
        )
    except (build_inputs.FencedFiCandidateBuildInputError, OSError, subprocess.TimeoutExpired) as exc:
        raise FencedFiCandidateBuildContextError(
            "FENCED_FI_BUILD_CONTEXT_GIT_UNAVAILABLE"
        ) from exc
    if result.returncode != 0 or len(result.stdout) > maximum_bytes:
        _fail("FENCED_FI_BUILD_CONTEXT_GIT_REJECTED")
    return result.stdout


def _git_one_line(root: Path, *arguments: str) -> str:
    try:
        value = _run_git(root, *arguments, maximum_bytes=4096).decode("ascii").strip()
    except UnicodeDecodeError as exc:
        raise FencedFiCandidateBuildContextError(
            "FENCED_FI_BUILD_CONTEXT_GIT_REJECTED"
        ) from exc
    if not value or "\n" in value:
        _fail("FENCED_FI_BUILD_CONTEXT_GIT_REJECTED")
    return value


def _git_path(root: Path, *, argument: str, label: str) -> Path:
    raw = _git_one_line(root, "rev-parse", argument)
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = root / candidate
    return _canonical_absolute_path(os.path.normpath(str(candidate)), label=label)


def _reject_existing_path(path: Path, *, code: str) -> None:
    try:
        path.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise FencedFiCandidateBuildContextError(code) from exc
    _fail(code)


def _git_local_config_entries(root: Path) -> tuple[tuple[str, str], ...]:
    raw = _run_git(
        root,
        "config",
        "--local",
        "--no-includes",
        "--null",
        "--list",
        maximum_bytes=MAX_GIT_CONFIG_BYTES,
    )
    entries: list[tuple[str, str]] = []
    for record in raw.split(b"\0"):
        if not record:
            continue
        key, separator, value = record.partition(b"\n")
        try:
            normalized_key = key.decode("ascii").lower()
            normalized_value = value.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise FencedFiCandidateBuildContextError(
                "FENCED_FI_BUILD_CONTEXT_SOURCE_GIT_CONFIG_REJECTED"
            ) from exc
        if not separator or not normalized_key or "\x00" in normalized_value:
            _fail("FENCED_FI_BUILD_CONTEXT_SOURCE_GIT_CONFIG_REJECTED")
        entries.append((normalized_key, normalized_value))
    return tuple(entries)


def _require_local_git_object_store(root: Path) -> None:
    """Reject Git layouts that could retrieve unreviewed remote objects."""

    git_directory = _git_path(root, argument="--git-dir", label="SOURCE_GIT_DIRECTORY")
    common_directory = _git_path(
        root,
        argument="--git-common-dir",
        label="SOURCE_GIT_COMMON_DIRECTORY",
    )
    _require_root_directory(git_directory, label="SOURCE_GIT_DIRECTORY", private=False)
    _require_root_directory(
        common_directory,
        label="SOURCE_GIT_COMMON_DIRECTORY",
        private=False,
    )
    _reject_existing_path(
        git_directory / "config.worktree",
        code="FENCED_FI_BUILD_CONTEXT_SOURCE_GIT_CONFIG_REJECTED",
    )
    for key, _value in _git_local_config_entries(root):
        if (
            key == "include.path"
            or (key.startswith("includeif.") and key.endswith(".path"))
            or key == "extensions.worktreeconfig"
            or key == "core.worktree"
            or "partialclone" in key
            or (key.startswith("remote.") and key.endswith(".promisor"))
        ):
            _fail("FENCED_FI_BUILD_CONTEXT_SOURCE_PARTIAL_OR_ALTERNATE_REJECTED")
    objects = _require_root_directory(
        common_directory / "objects",
        label="SOURCE_GIT_OBJECTS_DIRECTORY",
        private=False,
    )
    info = _require_root_directory(
        objects / "info",
        label="SOURCE_GIT_OBJECTS_INFO_DIRECTORY",
        private=False,
    )
    pack = _require_root_directory(
        objects / "pack",
        label="SOURCE_GIT_OBJECTS_PACK_DIRECTORY",
        private=False,
    )
    _reject_existing_path(
        info / "alternates",
        code="FENCED_FI_BUILD_CONTEXT_SOURCE_PARTIAL_OR_ALTERNATE_REJECTED",
    )
    try:
        with os.scandir(pack) as scan:
            entries = tuple(scan)
    except OSError as exc:
        raise FencedFiCandidateBuildContextError(
            "FENCED_FI_BUILD_CONTEXT_SOURCE_PARTIAL_OR_ALTERNATE_REJECTED"
        ) from exc
    for entry in entries:
        if entry.name.endswith(".promisor"):
            _fail("FENCED_FI_BUILD_CONTEXT_SOURCE_PARTIAL_OR_ALTERNATE_REJECTED")


def _source_file_path(root: Path, relative: str) -> Path:
    relative = _safe_source_relative(relative)
    current = root
    for component in PurePosixPath(relative).parts[:-1]:
        current /= component
        try:
            metadata = current.lstat()
        except OSError as exc:
            raise FencedFiCandidateBuildContextError(
                "FENCED_FI_BUILD_CONTEXT_SOURCE_ENTRY_UNAVAILABLE"
            ) from exc
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) & (0o022 | 0o7000)
        ):
            _fail("FENCED_FI_BUILD_CONTEXT_SOURCE_ENTRY_UNSAFE")
    return root.joinpath(*PurePosixPath(relative).parts)


def _snapshot_source_file(root: Path, *, relative: str, git_blob_sha1: str, git_mode: int) -> FileSnapshot:
    path = _source_file_path(root, relative)
    try:
        before = path.lstat()
    except OSError as exc:
        raise FencedFiCandidateBuildContextError(
            "FENCED_FI_BUILD_CONTEXT_SOURCE_ENTRY_UNAVAILABLE"
        ) from exc
    if (
        stat.S_ISLNK(before.st_mode)
        or not stat.S_ISREG(before.st_mode)
        or before.st_uid != os.geteuid()
        or before.st_nlink != 1
        or stat.S_IMODE(before.st_mode) != git_mode
        or before.st_size < 0
        or before.st_size > MAX_SOURCE_FILE_BYTES
    ):
        _fail("FENCED_FI_BUILD_CONTEXT_SOURCE_ENTRY_UNSAFE")
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | _no_follow())
    except OSError as exc:
        raise FencedFiCandidateBuildContextError(
            "FENCED_FI_BUILD_CONTEXT_SOURCE_ENTRY_UNAVAILABLE"
        ) from exc
    try:
        opened = os.fstat(descriptor)
        if _stat_identity(opened) != _stat_identity(before):
            _fail("FENCED_FI_BUILD_CONTEXT_SOURCE_ENTRY_CHANGED")
        git_digest = hashlib.sha1()
        git_digest.update(f"blob {before.st_size}\0".encode("ascii"))
        digest = hashlib.sha256()
        total = 0
        while True:
            chunk = os.read(descriptor, COPY_CHUNK_BYTES)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_SOURCE_FILE_BYTES:
                _fail("FENCED_FI_BUILD_CONTEXT_SOURCE_ENTRY_UNSAFE")
            git_digest.update(chunk)
            digest.update(chunk)
        after = os.fstat(descriptor)
        if total != before.st_size or _stat_identity(after) != _stat_identity(before):
            _fail("FENCED_FI_BUILD_CONTEXT_SOURCE_ENTRY_CHANGED")
    finally:
        os.close(descriptor)
    if git_digest.hexdigest() != git_blob_sha1:
        _fail("FENCED_FI_BUILD_CONTEXT_SOURCE_GIT_BLOB_MISMATCH")
    return FileSnapshot(
        relative=relative,
        sha256=digest.hexdigest(),
        bytes=total,
        mode=git_mode,
        device=before.st_dev,
        inode=before.st_ino,
        uid=before.st_uid,
        gid=before.st_gid,
        nlink=before.st_nlink,
        mtime_ns=before.st_mtime_ns,
        ctime_ns=before.st_ctime_ns,
        git_blob_sha1=git_blob_sha1,
    )


def _tracked_source_records(root: Path) -> list[tuple[str, str, int]]:
    raw = _run_git(root, "ls-tree", "-r", "-z", "HEAD")
    records: list[tuple[str, str, int]] = []
    observed: set[str] = set()
    for record in raw.split(b"\0"):
        if not record:
            continue
        metadata, separator, raw_path = record.partition(b"\t")
        fields = metadata.split()
        try:
            relative = raw_path.decode("utf-8")
            blob = fields[2].decode("ascii") if len(fields) == 3 else ""
        except UnicodeDecodeError as exc:
            raise FencedFiCandidateBuildContextError(
                "FENCED_FI_BUILD_CONTEXT_SOURCE_GIT_REJECTED"
            ) from exc
        if (
            not separator
            or len(fields) != 3
            or fields[0] not in {b"100644", b"100755"}
            or fields[1] != b"blob"
            or SHA1_RE.fullmatch(blob) is None
        ):
            _fail("FENCED_FI_BUILD_CONTEXT_SOURCE_SYMLINK_OR_GITLINK")
        relative = _safe_source_relative(relative)
        if relative in observed:
            _fail("FENCED_FI_BUILD_CONTEXT_SOURCE_GIT_REJECTED")
        observed.add(relative)
        records.append((relative, blob, 0o755 if fields[0] == b"100755" else 0o644))
    if not records or len(records) > MAX_SOURCE_FILES:
        _fail("FENCED_FI_BUILD_CONTEXT_SOURCE_FILE_SET_INVALID")
    records.sort(key=lambda item: item[0])
    return records


def _files_sha256(files: Sequence[Mapping[str, object]]) -> str:
    return hashlib.sha256(_canonical_json_bytes(list(files))).hexdigest()


def _directories_sha256(directories: Sequence[Mapping[str, object]]) -> str:
    return hashlib.sha256(_canonical_json_bytes(list(directories))).hexdigest()


def _scan_source(root: Path, *, application: Mapping[str, object]) -> SourceSnapshot:
    root = _require_root_directory(root, label="APPLICATION_SOURCE_ROOT", private=False)
    _require_local_git_object_store(root)
    release_sha = _git_one_line(root, "rev-parse", "--verify", "HEAD")
    release_tree_sha = _git_one_line(root, "rev-parse", "--verify", "HEAD^{tree}")
    if SHA1_RE.fullmatch(release_sha) is None or SHA1_RE.fullmatch(release_tree_sha) is None:
        _fail("FENCED_FI_BUILD_CONTEXT_SOURCE_GIT_IDENTITY_INVALID")
    if release_sha == LEGACY_UNFENCED_APPLICATION_RELEASE_SHA:
        _fail("FENCED_FI_BUILD_CONTEXT_LEGACY_2C08_APPLICATION_BLOCKED")
    if root.name != release_sha:
        _fail("FENCED_FI_BUILD_CONTEXT_SOURCE_IMMUTABLE_PATH_REQUIRED")
    if _run_git(
        root,
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
        "--ignored=matching",
        "--ignore-submodules=all",
    ):
        _fail("FENCED_FI_BUILD_CONTEXT_SOURCE_GIT_WORKTREE_POLLUTED")
    if (
        application.get("release_sha") != release_sha
        or application.get("release_tree_sha") != release_tree_sha
    ):
        _fail("FENCED_FI_BUILD_CONTEXT_SOURCE_MANIFEST_MISMATCH")
    files = tuple(
        _snapshot_source_file(root, relative=relative, git_blob_sha1=blob, git_mode=mode)
        for relative, blob, mode in _tracked_source_records(root)
    )
    if sum(item.bytes for item in files) > MAX_SOURCE_TOTAL_BYTES:
        _fail("FENCED_FI_BUILD_CONTEXT_SOURCE_FILE_SET_INVALID")
    by_name = {item.relative: item for item in files}
    for name, manifest_key in (("Dockerfile", "dockerfile_sha256"), (".dockerignore", "dockerignore_sha256")):
        if name not in by_name or by_name[name].sha256 != application.get(manifest_key):
            _fail("FENCED_FI_BUILD_CONTEXT_SOURCE_MANIFEST_MISMATCH")
    return SourceSnapshot(
        root=root,
        release_sha=release_sha,
        release_tree_sha=release_tree_sha,
        files=files,
    )


def _load_verified_inputs(
    *,
    application_release_root: Path,
    build_input_manifest: Path,
    mini_app_dist_manifest: Path,
) -> tuple[Path, Path, Mapping[str, object], bytes, build_inputs.StaticManifest, bytes]:
    application_root = _canonical_absolute_path(application_release_root, label="APPLICATION_SOURCE_ROOT")
    build_document = _read_private_regular(
        build_input_manifest,
        label="BUILD_INPUT_MANIFEST",
        maximum_bytes=MAX_MANIFEST_BYTES,
    )
    static_document = _read_private_regular(
        mini_app_dist_manifest,
        label="STATIC_MANIFEST",
        maximum_bytes=MAX_MANIFEST_BYTES,
    )
    try:
        build_value = build_inputs._verify_build_input_document(build_document)
        static_value = build_inputs._parse_static_manifest(static_document)
    except build_inputs.FencedFiCandidateBuildInputError as exc:
        raise FencedFiCandidateBuildContextError(
            "FENCED_FI_BUILD_CONTEXT_INPUT_MANIFEST_INVALID"
        ) from exc
    application = build_value["application"]
    static = build_value["mini_app_dist"]
    if not isinstance(application, Mapping) or not isinstance(static, Mapping):  # Defensive parser invariant.
        _fail("FENCED_FI_BUILD_CONTEXT_INPUT_MANIFEST_INVALID")
    recorded_source_root = _canonical_absolute_path(
        application.get("source_root", ""),
        label="RECORDED_APPLICATION_SOURCE_ROOT",
    )
    if recorded_source_root == application_root:
        _fail("FENCED_FI_BUILD_CONTEXT_SOURCE_NOT_INDEPENDENT")
    if (
        static_value.root != _canonical_absolute_path(static.get("root", ""), label="STATIC_ROOT")
        or hashlib.sha256(static_document).hexdigest() != static.get("manifest_sha256")
        or static_value.files_sha256 != static.get("files_sha256")
        or static_value.file_count != static.get("file_count")
        or static_value.total_bytes != static.get("total_bytes")
    ):
        _fail("FENCED_FI_BUILD_CONTEXT_STATIC_MANIFEST_MISMATCH")
    return application_root, recorded_source_root, build_value, build_document, static_value, static_document


def _scan_static(static_manifest: build_inputs.StaticManifest) -> tuple[FileSnapshot, ...]:
    try:
        snapshot = build_inputs._scan_mini_app_dist(static_manifest.root)
        build_inputs._snapshot_matches_manifest(snapshot, static_manifest)
    except build_inputs.FencedFiCandidateBuildInputError as exc:
        raise FencedFiCandidateBuildContextError(
            "FENCED_FI_BUILD_CONTEXT_STATIC_MANIFEST_MISMATCH"
        ) from exc
    return tuple(
        FileSnapshot(
            relative=item.path,
            sha256=item.sha256,
            bytes=item.bytes,
            mode=stat.S_IMODE(item.mode),
            device=item.device,
            inode=item.inode,
            uid=os.geteuid(),
            gid=None,
            nlink=1,
            mtime_ns=item.mtime_ns,
            ctime_ns=item.ctime_ns,
        )
        for item in snapshot.files
    )


def _paths_overlap(left: Path, right: Path) -> bool:
    try:
        right.relative_to(left)
        return True
    except ValueError:
        try:
            left.relative_to(right)
            return True
        except ValueError:
            return False


def _validate_layout(
    *,
    application_root: Path,
    recorded_application_root: Path,
    static_root: Path,
    build_input_manifest: Path,
    mini_app_dist_manifest: Path,
    context_output: Path,
    receipt_output: Path,
) -> tuple[Path, Path]:
    paths = {
        "application": _canonical_absolute_path(application_root, label="APPLICATION_SOURCE_ROOT"),
        "recorded_application": _canonical_absolute_path(
            recorded_application_root,
            label="RECORDED_APPLICATION_SOURCE_ROOT",
        ),
        "static": _canonical_absolute_path(static_root, label="STATIC_ROOT"),
        "build_manifest": _canonical_absolute_path(build_input_manifest, label="BUILD_INPUT_MANIFEST"),
        "static_manifest": _canonical_absolute_path(mini_app_dist_manifest, label="STATIC_MANIFEST"),
        "context": _canonical_absolute_path(context_output, label="CONTEXT_OUTPUT"),
        "receipt": _canonical_absolute_path(receipt_output, label="RECEIPT_OUTPUT"),
    }
    if _paths_overlap(paths["application"], paths["recorded_application"]):
        # The existing input manifest binds a source pathname, not a portable
        # clone attestation.  A non-overlapping checkout is therefore the
        # strongest local independence condition this helper can enforce
        # without misrepresenting it in the receipt.
        _fail("FENCED_FI_BUILD_CONTEXT_SOURCE_NOT_INDEPENDENT")
    input_names = (
        "application",
        "recorded_application",
        "static",
        "build_manifest",
        "static_manifest",
    )
    for index, left_name in enumerate(input_names):
        for right_name in input_names[index + 1 :]:
            if _paths_overlap(paths[left_name], paths[right_name]):
                _fail("FENCED_FI_BUILD_CONTEXT_PATH_OVERLAP")
    for output_name in ("context", "receipt"):
        for input_name in input_names:
            if _paths_overlap(paths[output_name], paths[input_name]):
                _fail("FENCED_FI_BUILD_CONTEXT_PATH_OVERLAP")
    if _paths_overlap(paths["context"], paths["receipt"]):
        _fail("FENCED_FI_BUILD_CONTEXT_PATH_OVERLAP")
    context_parent = _require_root_directory(paths["context"].parent, label="CONTEXT_OUTPUT_PARENT", private=True)
    receipt_parent = _require_root_directory(paths["receipt"].parent, label="RECEIPT_OUTPUT_PARENT", private=True)
    for path, label in ((paths["context"], "CONTEXT_OUTPUT"), (paths["receipt"], "RECEIPT_OUTPUT")):
        try:
            path.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise FencedFiCandidateBuildContextError(
                f"FENCED_FI_BUILD_CONTEXT_{label}_UNAVAILABLE"
            ) from exc
        _fail(f"FENCED_FI_BUILD_CONTEXT_{label}_EXISTS")
    return context_parent, receipt_parent


def _ensure_context_parent(root: Path, parts: Sequence[str]) -> Path:
    current = root
    for component in parts:
        current /= component
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            try:
                current.mkdir(mode=0o700)
            except OSError as exc:
                raise FencedFiCandidateBuildContextError(
                    "FENCED_FI_BUILD_CONTEXT_OUTPUT_UNAVAILABLE"
                ) from exc
            try:
                metadata = current.lstat()
            except OSError as exc:  # pragma: no cover - mkdir/lstat race defense.
                raise FencedFiCandidateBuildContextError(
                    "FENCED_FI_BUILD_CONTEXT_OUTPUT_UNAVAILABLE"
                ) from exc
        except OSError as exc:
            raise FencedFiCandidateBuildContextError(
                "FENCED_FI_BUILD_CONTEXT_OUTPUT_UNAVAILABLE"
            ) from exc
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) != 0o700
        ):
            _fail("FENCED_FI_BUILD_CONTEXT_OUTPUT_UNSAFE")
    return current


def _assert_snapshot_state(path: Path, expected: FileSnapshot, *, label: str) -> os.stat_result:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise FencedFiCandidateBuildContextError(
            f"FENCED_FI_BUILD_CONTEXT_{label}_UNAVAILABLE"
        ) from exc
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_dev != expected.device
        or metadata.st_ino != expected.inode
        or stat.S_IMODE(metadata.st_mode) != expected.mode
        or metadata.st_uid != expected.uid
        or (expected.gid is not None and metadata.st_gid != expected.gid)
        or metadata.st_nlink != expected.nlink
        or metadata.st_size != expected.bytes
        or metadata.st_mtime_ns != expected.mtime_ns
        or metadata.st_ctime_ns != expected.ctime_ns
    ):
        _fail(f"FENCED_FI_BUILD_CONTEXT_{label}_CHANGED")
    return metadata


def _copy_snapshot_file(
    *,
    source: Path,
    expected: FileSnapshot,
    context_root: Path,
    destination_relative: str,
    destination_mode: int,
    label: str,
) -> FileSnapshot:
    destination_relative = _safe_context_relative(destination_relative)
    parts = PurePosixPath(destination_relative).parts
    destination_parent = _ensure_context_parent(context_root, parts[:-1])
    destination = destination_parent / parts[-1]
    before = _assert_snapshot_state(source, expected, label=label)
    try:
        source_fd = os.open(source, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | _no_follow())
    except OSError as exc:
        raise FencedFiCandidateBuildContextError(
            f"FENCED_FI_BUILD_CONTEXT_{label}_UNAVAILABLE"
        ) from exc
    destination_fd = -1
    try:
        if _stat_identity(os.fstat(source_fd)) != _stat_identity(before):
            _fail(f"FENCED_FI_BUILD_CONTEXT_{label}_CHANGED")
        try:
            destination_fd = os.open(
                destination,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | _no_follow(),
                destination_mode,
            )
        except FileExistsError as exc:
            raise FencedFiCandidateBuildContextError(
                "FENCED_FI_BUILD_CONTEXT_OUTPUT_EXISTS"
            ) from exc
        except OSError as exc:
            raise FencedFiCandidateBuildContextError(
                "FENCED_FI_BUILD_CONTEXT_OUTPUT_UNAVAILABLE"
            ) from exc
        source_git = hashlib.sha1() if expected.git_blob_sha1 is not None else None
        if source_git is not None:
            source_git.update(f"blob {expected.bytes}\0".encode("ascii"))
        digest = hashlib.sha256()
        total = 0
        while True:
            chunk = os.read(source_fd, COPY_CHUNK_BYTES)
            if not chunk:
                break
            total += len(chunk)
            if total > expected.bytes:
                _fail(f"FENCED_FI_BUILD_CONTEXT_{label}_CHANGED")
            digest.update(chunk)
            if source_git is not None:
                source_git.update(chunk)
            view = memoryview(chunk)
            while view:
                written = os.write(destination_fd, view)
                if written <= 0:
                    _fail("FENCED_FI_BUILD_CONTEXT_OUTPUT_WRITE_FAILED")
                view = view[written:]
        if total != expected.bytes or digest.hexdigest() != expected.sha256:
            _fail(f"FENCED_FI_BUILD_CONTEXT_{label}_CHANGED")
        if source_git is not None and source_git.hexdigest() != expected.git_blob_sha1:
            _fail("FENCED_FI_BUILD_CONTEXT_SOURCE_GIT_BLOB_MISMATCH")
        if _stat_identity(os.fstat(source_fd)) != _stat_identity(before):
            _fail(f"FENCED_FI_BUILD_CONTEXT_{label}_CHANGED")
        os.fchmod(destination_fd, destination_mode)
        os.fsync(destination_fd)
    finally:
        if destination_fd >= 0:
            os.close(destination_fd)
        os.close(source_fd)
    try:
        written = destination.lstat()
    except OSError as exc:  # pragma: no cover - post-write filesystem fault.
        raise FencedFiCandidateBuildContextError(
            "FENCED_FI_BUILD_CONTEXT_OUTPUT_UNAVAILABLE"
        ) from exc
    if (
        stat.S_ISLNK(written.st_mode)
        or not stat.S_ISREG(written.st_mode)
        or written.st_uid != os.geteuid()
        or written.st_nlink != 1
        or stat.S_IMODE(written.st_mode) != destination_mode
        or written.st_size != expected.bytes
    ):
        _fail("FENCED_FI_BUILD_CONTEXT_OUTPUT_UNSAFE")
    return FileSnapshot(
        relative=destination_relative,
        sha256=expected.sha256,
        bytes=expected.bytes,
        mode=destination_mode,
        device=written.st_dev,
        inode=written.st_ino,
        uid=written.st_uid,
        gid=written.st_gid,
        nlink=written.st_nlink,
        mtime_ns=written.st_mtime_ns,
        ctime_ns=written.st_ctime_ns,
    )


def _create_context(path: Path, *, parent: Path) -> Path:
    try:
        path.mkdir(mode=0o700)
    except FileExistsError as exc:
        raise FencedFiCandidateBuildContextError(
            "FENCED_FI_BUILD_CONTEXT_CONTEXT_OUTPUT_EXISTS"
        ) from exc
    except OSError as exc:
        raise FencedFiCandidateBuildContextError(
            "FENCED_FI_BUILD_CONTEXT_OUTPUT_UNAVAILABLE"
        ) from exc
    context = _require_root_directory(path, label="CONTEXT_OUTPUT", private=True)
    try:
        directory_fd = os.open(parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0) | _no_follow())
    except OSError as exc:
        raise FencedFiCandidateBuildContextError(
            "FENCED_FI_BUILD_CONTEXT_OUTPUT_UNAVAILABLE"
        ) from exc
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    return context


def _snapshot_context_file(path: Path, *, relative: str) -> FileSnapshot:
    try:
        before = path.lstat()
    except OSError as exc:
        raise FencedFiCandidateBuildContextError(
            "FENCED_FI_BUILD_CONTEXT_OUTPUT_UNAVAILABLE"
        ) from exc
    if (
        stat.S_ISLNK(before.st_mode)
        or not stat.S_ISREG(before.st_mode)
        or before.st_uid != os.geteuid()
        or before.st_nlink != 1
        or stat.S_IMODE(before.st_mode) not in {0o644, 0o755}
        or before.st_size < 0
        or before.st_size > MAX_SOURCE_FILE_BYTES
    ):
        _fail("FENCED_FI_BUILD_CONTEXT_OUTPUT_UNSAFE")
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | _no_follow())
    except OSError as exc:
        raise FencedFiCandidateBuildContextError(
            "FENCED_FI_BUILD_CONTEXT_OUTPUT_UNAVAILABLE"
        ) from exc
    try:
        if _stat_identity(os.fstat(descriptor)) != _stat_identity(before):
            _fail("FENCED_FI_BUILD_CONTEXT_OUTPUT_CHANGED")
        digest = hashlib.sha256()
        total = 0
        while True:
            chunk = os.read(descriptor, COPY_CHUNK_BYTES)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_SOURCE_FILE_BYTES:
                _fail("FENCED_FI_BUILD_CONTEXT_OUTPUT_UNSAFE")
            digest.update(chunk)
        if total != before.st_size or _stat_identity(os.fstat(descriptor)) != _stat_identity(before):
            _fail("FENCED_FI_BUILD_CONTEXT_OUTPUT_CHANGED")
    finally:
        os.close(descriptor)
    return FileSnapshot(
        relative=relative,
        sha256=digest.hexdigest(),
        bytes=total,
        mode=stat.S_IMODE(before.st_mode),
        device=before.st_dev,
        inode=before.st_ino,
        uid=before.st_uid,
        gid=before.st_gid,
        nlink=before.st_nlink,
        mtime_ns=before.st_mtime_ns,
        ctime_ns=before.st_ctime_ns,
    )


def _snapshot_context_directory(path: Path, *, relative: str) -> DirectorySnapshot:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise FencedFiCandidateBuildContextError(
            "FENCED_FI_BUILD_CONTEXT_OUTPUT_UNAVAILABLE"
        ) from exc
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        _fail("FENCED_FI_BUILD_CONTEXT_OUTPUT_UNSAFE")
    return DirectorySnapshot(
        relative=relative,
        mode=stat.S_IMODE(metadata.st_mode),
        device=metadata.st_dev,
        inode=metadata.st_ino,
        uid=metadata.st_uid,
        gid=metadata.st_gid,
        mtime_ns=metadata.st_mtime_ns,
        ctime_ns=metadata.st_ctime_ns,
    )


def _scan_context(root: Path) -> ContextSnapshot:
    root = _require_root_directory(root, label="CONTEXT_OUTPUT", private=True)
    files: list[FileSnapshot] = []
    directories: list[DirectorySnapshot] = []

    def visit(directory: Path, prefix: str) -> None:
        try:
            entries = sorted(os.scandir(directory), key=lambda entry: entry.name)
        except OSError as exc:
            raise FencedFiCandidateBuildContextError(
                "FENCED_FI_BUILD_CONTEXT_OUTPUT_UNAVAILABLE"
            ) from exc
        for entry in entries:
            relative = _safe_context_relative(entry.name if not prefix else prefix + "/" + entry.name)
            path = directory / entry.name
            try:
                metadata = path.lstat()
            except OSError as exc:
                raise FencedFiCandidateBuildContextError(
                    "FENCED_FI_BUILD_CONTEXT_OUTPUT_UNAVAILABLE"
                ) from exc
            if stat.S_ISDIR(metadata.st_mode):
                directories.append(_snapshot_context_directory(path, relative=relative))
                visit(path, relative)
            elif stat.S_ISREG(metadata.st_mode):
                files.append(_snapshot_context_file(path, relative=relative))
            else:
                _fail("FENCED_FI_BUILD_CONTEXT_OUTPUT_UNSAFE")

    visit(root, "")
    files.sort(key=lambda item: item.relative)
    directories.sort(key=lambda item: item.relative)
    if not files or len(files) > MAX_SOURCE_FILES + build_inputs.MAX_STATIC_FILES:
        _fail("FENCED_FI_BUILD_CONTEXT_OUTPUT_UNSAFE")
    if len(directories) > MAX_SOURCE_FILES + build_inputs.MAX_STATIC_FILES:
        _fail("FENCED_FI_BUILD_CONTEXT_OUTPUT_UNSAFE")
    if sum(item.bytes for item in files) > MAX_CONTEXT_TOTAL_BYTES:
        _fail("FENCED_FI_BUILD_CONTEXT_OUTPUT_UNSAFE")
    return ContextSnapshot(
        root=root,
        directories=tuple(directories),
        files=tuple(files),
    )


def _expected_context_directory_public(files: Sequence[FileSnapshot]) -> list[dict[str, object]]:
    directories: set[str] = set()
    for file in files:
        parts = PurePosixPath(file.relative).parts[:-1]
        for index in range(1, len(parts) + 1):
            directories.add("/".join(parts[:index]))
    return [
        {"path": relative, "mode": "0700"}
        for relative in sorted(directories)
    ]


def _verify_context(root: Path, *, expected: Sequence[FileSnapshot]) -> ContextSnapshot:
    observed = _scan_context(root)
    if (
        [item.public() for item in observed.files] != [item.public() for item in expected]
        or [item.public() for item in observed.directories]
        != _expected_context_directory_public(expected)
    ):
        _fail("FENCED_FI_BUILD_CONTEXT_OUTPUT_MISMATCH")
    return observed


def _write_new_receipt(path: Path, *, payload: bytes, parent: Path) -> None:
    if not 1 <= len(payload) <= RECEIPT_RESERVE_BYTES:
        _fail("FENCED_FI_BUILD_CONTEXT_RECEIPT_OUTPUT_INVALID")
    directory_fd = -1
    temporary_fd = -1
    temporary_name: str | None = None
    published = False
    try:
        directory_fd = os.open(
            parent,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | _no_follow(),
        )
        for _attempt in range(8):
            candidate = f".{path.name}.tmp-{uuid4().hex}"
            try:
                temporary_fd = os.open(
                    candidate,
                    os.O_WRONLY
                    | os.O_CREAT
                    | os.O_EXCL
                    | getattr(os, "O_CLOEXEC", 0)
                    | _no_follow(),
                    0o600,
                    dir_fd=directory_fd,
                )
            except FileExistsError:
                continue
            temporary_name = candidate
            break
        if temporary_fd < 0 or temporary_name is None:  # pragma: no cover - UUID collision defense.
            _fail("FENCED_FI_BUILD_CONTEXT_RECEIPT_OUTPUT_UNAVAILABLE")
        offset = 0
        while offset < len(payload):
            written = os.write(temporary_fd, payload[offset:])
            if written <= 0:
                _fail("FENCED_FI_BUILD_CONTEXT_RECEIPT_OUTPUT_WRITE_FAILED")
            offset += written
        os.fsync(temporary_fd)
        os.close(temporary_fd)
        temporary_fd = -1
        try:
            os.link(
                temporary_name,
                path.name,
                src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd,
                follow_symlinks=False,
            )
        except FileExistsError as exc:
            raise FencedFiCandidateBuildContextError(
                "FENCED_FI_BUILD_CONTEXT_RECEIPT_OUTPUT_EXISTS"
            ) from exc
        published = True
        os.fsync(directory_fd)
        os.unlink(temporary_name, dir_fd=directory_fd)
        temporary_name = None
        os.fsync(directory_fd)
    except FencedFiCandidateBuildContextError:
        raise
    except OSError as exc:
        raise FencedFiCandidateBuildContextError(
            "FENCED_FI_BUILD_CONTEXT_RECEIPT_OUTPUT_UNAVAILABLE"
        ) from exc
    finally:
        if temporary_fd >= 0:
            os.close(temporary_fd)
        if temporary_name is not None and not published and directory_fd >= 0:
            try:
                os.unlink(temporary_name, dir_fd=directory_fd)
            except OSError:
                pass
        if directory_fd >= 0:
            os.close(directory_fd)
    try:
        metadata = path.lstat()
    except OSError as exc:  # pragma: no cover - post-write filesystem fault.
        raise FencedFiCandidateBuildContextError(
            "FENCED_FI_BUILD_CONTEXT_RECEIPT_OUTPUT_UNAVAILABLE"
        ) from exc
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_size != len(payload)
    ):
        _fail("FENCED_FI_BUILD_CONTEXT_RECEIPT_OUTPUT_UNSAFE")


def _receipt_document(
    *,
    build_input_manifest: bytes,
    build_value: Mapping[str, object],
    source: SourceSnapshot,
    static_manifest: build_inputs.StaticManifest,
    context: ContextSnapshot,
) -> bytes:
    value = {
        "schema": BUILD_CONTEXT_RECEIPT_SCHEMA,
        "status": BUILD_CONTEXT_RECEIPT_STATUS,
        "application": {
            "release_sha": source.release_sha,
            "release_tree_sha": source.release_tree_sha,
            "dockerfile_sha256": next(item.sha256 for item in source.files if item.relative == "Dockerfile"),
            "dockerignore_sha256": next(item.sha256 for item in source.files if item.relative == ".dockerignore"),
            "files_sha256": source.files_sha256,
            "file_count": len(source.files),
            "total_bytes": source.total_bytes,
        },
        "inputs": {
            "build_input_manifest_sha256": hashlib.sha256(build_input_manifest).hexdigest(),
            "term_fenced_application_evidence_sha256": build_value["term_fenced_application_evidence_sha256"],
            "manifest_sha256": static_manifest.manifest_sha256,
            "files_sha256": static_manifest.files_sha256,
            "file_count": static_manifest.file_count,
            "total_bytes": static_manifest.total_bytes,
        },
        "context": {
            "files_sha256": context.files_sha256,
            "file_count": len(context.files),
            "total_bytes": context.total_bytes,
            "directories_sha256": context.directories_sha256,
            "directory_count": len(context.directories),
        },
        "writer_authorized": False,
        "promotion_authorized": False,
        "deployment_authorized": False,
        "execution_authorized": False,
        "full_matrix_authorized": False,
        "full_matrix_executed": False,
    }
    return _canonical_json_bytes(value)


def _verify_receipt_document(payload: bytes) -> Mapping[str, object]:
    if not 1 <= len(payload) <= RECEIPT_RESERVE_BYTES:
        _fail("FENCED_FI_BUILD_CONTEXT_RECEIPT_INVALID")
    try:
        value = json.loads(
            payload.decode("ascii"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except FencedFiCandidateBuildContextError:
        raise
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError, RecursionError) as exc:
        raise FencedFiCandidateBuildContextError(
            "FENCED_FI_BUILD_CONTEXT_RECEIPT_INVALID"
        ) from exc
    if (
        not isinstance(value, dict)
        or set(value) != _RECEIPT_FIELDS
        or value.get("schema") != BUILD_CONTEXT_RECEIPT_SCHEMA
        or value.get("status") != BUILD_CONTEXT_RECEIPT_STATUS
        or _canonical_json_bytes(value) != payload
    ):
        _fail("FENCED_FI_BUILD_CONTEXT_RECEIPT_INVALID")
    application = value.get("application")
    inputs = value.get("inputs")
    context = value.get("context")
    if (
        not isinstance(application, dict)
        or set(application) != _RECEIPT_APPLICATION_FIELDS
        or not isinstance(inputs, dict)
        or set(inputs) != _RECEIPT_INPUT_FIELDS
        or not isinstance(context, dict)
        or set(context) != _RECEIPT_CONTEXT_FIELDS
    ):
        _fail("FENCED_FI_BUILD_CONTEXT_RECEIPT_INVALID")
    for digest in (
        inputs.get("build_input_manifest_sha256"),
        inputs.get("term_fenced_application_evidence_sha256"),
        application.get("dockerfile_sha256"),
        application.get("dockerignore_sha256"),
        application.get("files_sha256"),
        inputs.get("manifest_sha256"),
        inputs.get("files_sha256"),
        context.get("files_sha256"),
        context.get("directories_sha256"),
    ):
        if not isinstance(digest, str) or SHA256_RE.fullmatch(digest) is None:
            _fail("FENCED_FI_BUILD_CONTEXT_RECEIPT_INVALID")
    for release in (application.get("release_sha"), application.get("release_tree_sha")):
        if not isinstance(release, str) or SHA1_RE.fullmatch(release) is None:
            _fail("FENCED_FI_BUILD_CONTEXT_RECEIPT_INVALID")
    for number in (
        application.get("file_count"),
        application.get("total_bytes"),
        inputs.get("file_count"),
        inputs.get("total_bytes"),
        context.get("file_count"),
        context.get("total_bytes"),
        context.get("directory_count"),
    ):
        if isinstance(number, bool) or not isinstance(number, int) or number < 1:
            _fail("FENCED_FI_BUILD_CONTEXT_RECEIPT_INVALID")
    for name in (
        "writer_authorized",
        "promotion_authorized",
        "deployment_authorized",
        "execution_authorized",
        "full_matrix_authorized",
        "full_matrix_executed",
    ):
        if value.get(name) is not False:
            _fail("FENCED_FI_BUILD_CONTEXT_RECEIPT_AUTHORIZATION_FORBIDDEN")
    return value


def _capacity_preflight(*, context_parent: Path, receipt_parent: Path, source: SourceSnapshot, static_files: Sequence[FileSnapshot]) -> None:
    required_context = source.total_bytes + sum(item.bytes for item in static_files) + CONTEXT_MARGIN_BYTES
    try:
        context_free = shutil.disk_usage(context_parent).free
        receipt_free = shutil.disk_usage(receipt_parent).free
    except OSError as exc:
        raise FencedFiCandidateBuildContextError(
            "FENCED_FI_BUILD_CONTEXT_CAPACITY_UNAVAILABLE"
        ) from exc
    if required_context > MAX_CONTEXT_TOTAL_BYTES + CONTEXT_MARGIN_BYTES or context_free < required_context or receipt_free < RECEIPT_RESERVE_BYTES:
        _fail("FENCED_FI_BUILD_CONTEXT_INSUFFICIENT_SPACE")


def prepare_fenced_fi_candidate_build_context(
    *,
    independent_application_release_root: Path,
    build_input_manifest: Path,
    mini_app_dist_manifest: Path,
    context_output: Path,
    receipt_output: Path,
) -> dict[str, object]:
    """Create one exact fresh context and one separate non-authorizing receipt."""

    _require_root()
    application_root, recorded_application_root, build_value, build_document, static_manifest, _static_document = _load_verified_inputs(
        application_release_root=Path(independent_application_release_root),
        build_input_manifest=Path(build_input_manifest),
        mini_app_dist_manifest=Path(mini_app_dist_manifest),
    )
    context_output = _canonical_absolute_path(context_output, label="CONTEXT_OUTPUT")
    receipt_output = _canonical_absolute_path(receipt_output, label="RECEIPT_OUTPUT")
    context_parent, receipt_parent = _validate_layout(
        application_root=application_root,
        recorded_application_root=recorded_application_root,
        static_root=static_manifest.root,
        build_input_manifest=Path(build_input_manifest),
        mini_app_dist_manifest=Path(mini_app_dist_manifest),
        context_output=context_output,
        receipt_output=receipt_output,
    )
    application = build_value["application"]
    if not isinstance(application, Mapping):  # Defensive parser invariant.
        _fail("FENCED_FI_BUILD_CONTEXT_INPUT_MANIFEST_INVALID")
    first_source = _scan_source(application_root, application=application)
    first_static = _scan_static(static_manifest)
    _capacity_preflight(
        context_parent=context_parent,
        receipt_parent=receipt_parent,
        source=first_source,
        static_files=first_static,
    )
    context_root = _create_context(context_output, parent=context_parent)
    expected_context: list[FileSnapshot] = []
    for source_file in first_source.files:
        expected_context.append(
            _copy_snapshot_file(
                source=_source_file_path(application_root, source_file.relative),
                expected=source_file,
                context_root=context_root,
                destination_relative=source_file.relative,
                destination_mode=source_file.mode,
                label="SOURCE_ENTRY",
            )
        )
    for static_file in first_static:
        expected_context.append(
            _copy_snapshot_file(
                source=static_manifest.root.joinpath(*PurePosixPath(static_file.relative).parts),
                expected=static_file,
                context_root=context_root,
                destination_relative=FRONTEND_DIST_DIRECTORY + "/" + static_file.relative,
                destination_mode=0o644,
                label="STATIC_ENTRY",
            )
        )
    expected_context.sort(key=lambda item: item.relative)
    # A changed input invalidates this fresh context.  It remains as forensic
    # evidence but receives no success receipt and is never reused by this tool.
    if _scan_source(application_root, application=application) != first_source:
        _fail("FENCED_FI_BUILD_CONTEXT_SOURCE_CHANGED")
    if _scan_static(static_manifest) != first_static:
        _fail("FENCED_FI_BUILD_CONTEXT_STATIC_SOURCE_CHANGED")
    observed_context = _verify_context(context_root, expected=expected_context)
    receipt = _receipt_document(
        build_input_manifest=build_document,
        build_value=build_value,
        source=first_source,
        static_manifest=static_manifest,
        context=observed_context,
    )
    verified_receipt = _verify_receipt_document(receipt)
    _write_new_receipt(receipt_output, payload=receipt, parent=receipt_parent)
    return {
        "status": BUILD_CONTEXT_RECEIPT_STATUS,
        "schema": BUILD_CONTEXT_RECEIPT_SCHEMA,
        "context_root": str(context_root),
        "receipt_output": str(receipt_output),
        "receipt_sha256": hashlib.sha256(receipt).hexdigest(),
        "build_input_manifest_sha256": hashlib.sha256(build_document).hexdigest(),
        "release_sha": first_source.release_sha,
        "release_tree_sha": first_source.release_tree_sha,
        "source_file_count": len(first_source.files),
        "static_file_count": static_manifest.file_count,
        "context_file_count": len(observed_context.files),
        "context_directory_count": len(observed_context.directories),
        "writer_authorized": verified_receipt["writer_authorized"],
        "docker_action": False,
        "npm_action": False,
        "network_action": False,
        "service_changed": False,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--independent-application-release-root", required=True, type=Path)
    parser.add_argument("--build-input-manifest", required=True, type=Path)
    parser.add_argument("--mini-app-dist-manifest", required=True, type=Path)
    parser.add_argument("--context-output", required=True, type=Path)
    parser.add_argument("--receipt-output", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        result = prepare_fenced_fi_candidate_build_context(
            independent_application_release_root=arguments.independent_application_release_root,
            build_input_manifest=arguments.build_input_manifest,
            mini_app_dist_manifest=arguments.mini_app_dist_manifest,
            context_output=arguments.context_output,
            receipt_output=arguments.receipt_output,
        )
    except FencedFiCandidateBuildContextError as exc:
        print(json.dumps({"status": "blocked", "error_class": type(exc).__name__, "error": exc.code}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
