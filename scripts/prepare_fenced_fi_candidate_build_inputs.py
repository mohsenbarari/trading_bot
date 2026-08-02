#!/usr/bin/env python3
"""Seal non-authorizing local inputs for one Fenced-FI image build.

The term-fenced writer admission path intentionally requires an application
checkout that stays clean.  A frontend build therefore cannot be copied into
that checkout merely to satisfy ``Dockerfile``'s ``mini_app_dist`` input.  This
tool closes that narrow assembly gap without invoking Docker, npm, a registry,
Object Storage, a peer, or a service.

``snapshot-static`` creates one root-only, create-only manifest for a
caller-supplied ``mini_app_dist`` tree.  ``bind`` verifies that manifest again
and binds it to a clean, non-legacy Git source/tree plus the existing
term-fenced source evidence.  Both outputs are non-authorizing metadata: an
actual image build, private-registry digest, signed release descriptor and
host preflight remain separate gates.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
from typing import Any, Mapping, Sequence
from uuid import uuid4


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core import term_fenced_application_capability as capability  # noqa: E402
from scripts import verify_term_fenced_application_source as source_verifier  # noqa: E402


STATIC_MANIFEST_SCHEMA = "gold-trade-release0-mini-app-dist-manifest-v1"
BUILD_INPUT_MANIFEST_SCHEMA = "gold-trade-release0-fenced-fi-build-input-manifest-v1"
STATIC_MANIFEST_STATUS = "static-snapshot-non-authorizing"
BUILD_INPUT_MANIFEST_STATUS = "verified-build-inputs-non-authorizing"
LEGACY_UNFENCED_APPLICATION_RELEASE_SHA = (
    "2c08da14bfa0ef94d9c788e478d30ddc3f31a3c5"
)
FRONTEND_DIST_DIRECTORY = "mini_app_dist"

MAX_MANIFEST_BYTES = 8 * 1024 * 1024
MAX_GIT_OUTPUT_BYTES = 8 * 1024 * 1024
MAX_STATIC_FILES = 100_000
MAX_STATIC_FILE_BYTES = 100 * 1024 * 1024
MAX_STATIC_TOTAL_BYTES = 200 * 1024 * 1024
SHA1_RE = re.compile(r"^[0-9a-f]{40}$", re.ASCII)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)

_STATIC_MANIFEST_FIELDS = frozenset(
    {
        "schema",
        "status",
        "mini_app_dist_root",
        "files",
        "files_sha256",
        "file_count",
        "total_bytes",
    }
)
_BUILD_INPUT_FIELDS = frozenset(
    {
        "schema",
        "status",
        "application",
        "term_fenced_application_evidence_sha256",
        "mini_app_dist",
        "writer_authorized",
        "promotion_authorized",
        "deployment_authorized",
        "execution_authorized",
        "full_matrix_authorized",
        "full_matrix_executed",
    }
)
_APPLICATION_FIELDS = frozenset(
    {
        "source_root",
        "release_sha",
        "release_tree_sha",
        "dockerfile_sha256",
        "dockerignore_sha256",
    }
)
_BUILD_STATIC_FIELDS = frozenset(
    {
        "root",
        "manifest_sha256",
        "files_sha256",
        "file_count",
        "total_bytes",
    }
)
_STATIC_FILE_FIELDS = frozenset({"path", "sha256", "bytes"})
_FORBIDDEN_STATIC_COMPONENTS = frozenset(
    {
        ".git",
        ".env",
        "__pycache__",
        "node_modules",
        "pip_packages",
        "venv",
        ".venv",
        "secrets",
        "secret",
        "credentials",
        "credential",
        "private",
    }
)
_FORBIDDEN_STATIC_SUFFIXES = (".key", ".pem", ".p12", ".pfx", ".env")
# These are deliberately high-confidence secret encodings rather than generic
# words such as "token" or "secret", which occur legitimately in bundled
# application code.  Generated browser assets must never contain a private
# key or a live credential literal.  The manifest records only hashes and
# sizes, but refusing these values at the input boundary makes an accidental
# inclusion fail before any candidate context is assembled.
_STATIC_SECRET_PATTERNS = (
    re.compile(rb"-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----"),
    re.compile(rb"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(rb"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    re.compile(rb"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b"),
)
_STATIC_SECRET_SCAN_OVERLAP = 256
_REQUIRED_DOCKERFILE_BYTES = (
    b"ARG FRONTEND_DIST_DIR=mini_app_dist",
    b"COPY ${FRONTEND_DIST_DIR}/ /app/mini_app_dist/",
)


class FencedFiCandidateBuildInputError(RuntimeError):
    """One local source/static build input is unsafe or inconsistent."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class StaticFile:
    path: str
    sha256: str
    bytes: int
    device: int
    inode: int
    mode: int
    mtime_ns: int
    ctime_ns: int

    def public(self) -> dict[str, object]:
        return {"path": self.path, "sha256": self.sha256, "bytes": self.bytes}


@dataclass(frozen=True)
class StaticDirectory:
    """Private scan identity used to detect a directory swap between scans."""

    path: str
    device: int
    inode: int
    mode: int
    uid: int
    gid: int
    mtime_ns: int
    ctime_ns: int


@dataclass(frozen=True)
class StaticSnapshot:
    root: Path
    directories: tuple[StaticDirectory, ...]
    files: tuple[StaticFile, ...]

    @property
    def public_files(self) -> list[dict[str, object]]:
        return [item.public() for item in self.files]

    @property
    def total_bytes(self) -> int:
        return sum(item.bytes for item in self.files)


@dataclass(frozen=True)
class StaticManifest:
    root: Path
    files: tuple[dict[str, object], ...]
    files_sha256: str
    file_count: int
    total_bytes: int
    manifest_sha256: str


@dataclass(frozen=True)
class ApplicationSnapshot:
    root: Path
    release_sha: str
    release_tree_sha: str
    evidence_sha256: str
    dockerfile_sha256: str
    dockerignore_sha256: str


def _fail(code: str) -> None:
    raise FencedFiCandidateBuildInputError(code)


def _require_root() -> None:
    if os.geteuid() != 0:
        _fail("FENCED_FI_BUILD_INPUT_ROOT_REQUIRED")


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("FENCED_FI_BUILD_INPUT_JSON_DUPLICATE_FIELD")
        result[key] = value
    return result


def _reject_json_constant(_: str) -> None:
    _fail("FENCED_FI_BUILD_INPUT_JSON_INVALID")


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
        raise FencedFiCandidateBuildInputError(
            "FENCED_FI_BUILD_INPUT_JSON_INVALID"
        ) from exc


def _canonical_absolute_path(value: Path | str, *, label: str) -> Path:
    if not isinstance(value, (Path, str)):
        _fail(f"FENCED_FI_BUILD_INPUT_{label}_PATH_INVALID")
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
        _fail(f"FENCED_FI_BUILD_INPUT_{label}_PATH_INVALID")
    return path


def _require_safe_directory_chain(path: Path, *, label: str) -> None:
    """Require root-owned non-symlink ancestors; root-owned sticky is safe."""

    path = _canonical_absolute_path(path, label=label)
    current = Path(path.anchor)
    for component in path.parts[1:]:
        current /= component
        try:
            metadata = current.lstat()
        except OSError as exc:
            raise FencedFiCandidateBuildInputError(
                f"FENCED_FI_BUILD_INPUT_{label}_ANCESTOR_UNAVAILABLE"
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
            _fail(f"FENCED_FI_BUILD_INPUT_{label}_ANCESTOR_UNSAFE")


def _require_root_directory(path: Path, *, label: str, private: bool) -> Path:
    path = _canonical_absolute_path(path, label=label)
    _require_safe_directory_chain(path.parent, label=label)
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise FencedFiCandidateBuildInputError(
            f"FENCED_FI_BUILD_INPUT_{label}_UNAVAILABLE"
        ) from exc
    mode = stat.S_IMODE(metadata.st_mode)
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or (mode != 0o700 if private else bool(mode & (0o022 | 0o7000)))
    ):
        _fail(f"FENCED_FI_BUILD_INPUT_{label}_UNSAFE")
    return path


def _secure_read(
    path: Path,
    *,
    label: str,
    maximum_bytes: int,
    private: bool,
) -> bytes:
    path = _canonical_absolute_path(path, label=label)
    _require_safe_directory_chain(path.parent, label=label)
    no_follow = getattr(os, "O_NOFOLLOW", None)
    if type(no_follow) is not int:
        _fail("FENCED_FI_BUILD_INPUT_O_NOFOLLOW_REQUIRED")
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | no_follow,
        )
    except OSError as exc:
        raise FencedFiCandidateBuildInputError(
            f"FENCED_FI_BUILD_INPUT_{label}_UNAVAILABLE"
        ) from exc
    try:
        before = os.fstat(descriptor)
        mode = stat.S_IMODE(before.st_mode)
        unsafe_permissions = 0o077 if private else 0o022
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.geteuid()
            or mode & unsafe_permissions
            or before.st_nlink != 1
            or not 1 <= before.st_size <= maximum_bytes
        ):
            _fail(f"FENCED_FI_BUILD_INPUT_{label}_UNSAFE")
        chunks: list[bytes] = []
        remaining = before.st_size
        while remaining:
            chunk = os.read(descriptor, min(65536, remaining))
            if not chunk:
                _fail(f"FENCED_FI_BUILD_INPUT_{label}_SHORT_READ")
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        after = os.fstat(descriptor)
        identity_fields = (
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
        if any(getattr(before, field) != getattr(after, field) for field in identity_fields):
            _fail(f"FENCED_FI_BUILD_INPUT_{label}_CHANGED")
        return payload
    finally:
        os.close(descriptor)


def _write_new_private(path: Path, *, payload: bytes, label: str) -> None:
    path = _canonical_absolute_path(path, label=label)
    if not 1 <= len(payload) <= MAX_MANIFEST_BYTES or path.name in {"", ".", ".."}:
        _fail(f"FENCED_FI_BUILD_INPUT_{label}_OUTPUT_INVALID")
    parent = _require_root_directory(path.parent, label=f"{label}_OUTPUT_PARENT", private=True)
    no_follow = getattr(os, "O_NOFOLLOW", None)
    if type(no_follow) is not int:
        _fail("FENCED_FI_BUILD_INPUT_O_NOFOLLOW_REQUIRED")
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
            | no_follow,
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
                    | no_follow,
                    0o600,
                    dir_fd=directory_fd,
                )
            except FileExistsError:
                continue
            temporary_name = candidate
            break
        if temporary_fd < 0 or temporary_name is None:  # pragma: no cover - UUID collision.
            _fail(f"FENCED_FI_BUILD_INPUT_{label}_OUTPUT_UNAVAILABLE")
        offset = 0
        while offset < len(payload):
            written = os.write(temporary_fd, payload[offset:])
            if written <= 0:
                _fail(f"FENCED_FI_BUILD_INPUT_{label}_OUTPUT_WRITE_FAILED")
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
            raise FencedFiCandidateBuildInputError(
                f"FENCED_FI_BUILD_INPUT_{label}_OUTPUT_EXISTS"
            ) from exc
        published = True
        os.fsync(directory_fd)
        os.unlink(temporary_name, dir_fd=directory_fd)
        temporary_name = None
        os.fsync(directory_fd)
    except FencedFiCandidateBuildInputError:
        raise
    except OSError as exc:
        raise FencedFiCandidateBuildInputError(
            f"FENCED_FI_BUILD_INPUT_{label}_OUTPUT_UNAVAILABLE"
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


def _safe_static_path(value: object) -> str:
    if not isinstance(value, str) or not value or "\x00" in value or value.startswith("/"):
        _fail("FENCED_FI_BUILD_INPUT_STATIC_PATH_INVALID")
    path = PurePosixPath(value)
    try:
        encoded_length = len(value.encode("utf-8"))
    except UnicodeEncodeError:
        _fail("FENCED_FI_BUILD_INPUT_STATIC_PATH_INVALID")
    if (
        path.as_posix() != value
        or any(part in {"", ".", ".."} for part in path.parts)
        or any(ord(character) < 0x20 for character in value)
        or encoded_length > 512
    ):
        _fail("FENCED_FI_BUILD_INPUT_STATIC_PATH_INVALID")
    for part in path.parts:
        lowered = part.lower()
        if (
            part.startswith(".")
            or lowered in _FORBIDDEN_STATIC_COMPONENTS
            or lowered.endswith(_FORBIDDEN_STATIC_SUFFIXES)
        ):
            _fail("FENCED_FI_BUILD_INPUT_STATIC_POLLUTION")
    return value


def _safe_static_directory(metadata: os.stat_result) -> bool:
    return (
        stat.S_ISDIR(metadata.st_mode)
        and metadata.st_uid == os.geteuid()
        and not (stat.S_IMODE(metadata.st_mode) & (0o022 | 0o7000))
    )


def _static_directory_identity(path: str, metadata: os.stat_result) -> StaticDirectory:
    if not _safe_static_directory(metadata):
        _fail("FENCED_FI_BUILD_INPUT_STATIC_DIRECTORY_UNSAFE")
    return StaticDirectory(
        path=path,
        device=metadata.st_dev,
        inode=metadata.st_ino,
        mode=metadata.st_mode,
        uid=metadata.st_uid,
        gid=metadata.st_gid,
        mtime_ns=metadata.st_mtime_ns,
        ctime_ns=metadata.st_ctime_ns,
    )


def _contains_static_secret(payload: bytes) -> bool:
    return any(pattern.search(payload) is not None for pattern in _STATIC_SECRET_PATTERNS)


def _hash_static_file(path: Path, *, relative: str) -> StaticFile:
    try:
        before = path.lstat()
    except OSError as exc:
        raise FencedFiCandidateBuildInputError(
            "FENCED_FI_BUILD_INPUT_STATIC_ENTRY_UNAVAILABLE"
        ) from exc
    mode = stat.S_IMODE(before.st_mode)
    if (
        stat.S_ISLNK(before.st_mode)
        or not stat.S_ISREG(before.st_mode)
        or before.st_uid != os.geteuid()
        or mode & (0o022 | 0o111 | 0o7000)
        or before.st_nlink != 1
        or not 1 <= before.st_size <= MAX_STATIC_FILE_BYTES
    ):
        _fail("FENCED_FI_BUILD_INPUT_STATIC_ENTRY_UNSAFE")
    no_follow = getattr(os, "O_NOFOLLOW", None)
    if type(no_follow) is not int:
        _fail("FENCED_FI_BUILD_INPUT_O_NOFOLLOW_REQUIRED")
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | no_follow)
    except OSError as exc:
        raise FencedFiCandidateBuildInputError(
            "FENCED_FI_BUILD_INPUT_STATIC_ENTRY_UNAVAILABLE"
        ) from exc
    try:
        opened = os.fstat(descriptor)
        if (
            opened.st_dev != before.st_dev
            or opened.st_ino != before.st_ino
            or not stat.S_ISREG(opened.st_mode)
            or opened.st_uid != os.geteuid()
            or stat.S_IMODE(opened.st_mode) != mode
            or opened.st_nlink != 1
            or opened.st_size != before.st_size
        ):
            _fail("FENCED_FI_BUILD_INPUT_STATIC_ENTRY_CHANGED")
        digest = hashlib.sha256()
        total = 0
        tail = b""
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            if _contains_static_secret(tail + chunk):
                _fail("FENCED_FI_BUILD_INPUT_STATIC_SECRET_CONTENT")
            tail = (tail + chunk)[-_STATIC_SECRET_SCAN_OVERLAP:]
            digest.update(chunk)
            total += len(chunk)
            if total > MAX_STATIC_FILE_BYTES:
                _fail("FENCED_FI_BUILD_INPUT_STATIC_ENTRY_UNSAFE")
        after = os.fstat(descriptor)
        if (
            total != before.st_size
            or (
                before.st_dev,
                before.st_ino,
                before.st_mode,
                before.st_uid,
                before.st_gid,
                before.st_nlink,
                before.st_size,
                before.st_mtime_ns,
                before.st_ctime_ns,
            )
            != (
                after.st_dev,
                after.st_ino,
                after.st_mode,
                after.st_uid,
                after.st_gid,
                after.st_nlink,
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            )
        ):
            _fail("FENCED_FI_BUILD_INPUT_STATIC_ENTRY_CHANGED")
    finally:
        os.close(descriptor)
    return StaticFile(
        path=relative,
        sha256=digest.hexdigest(),
        bytes=total,
        device=before.st_dev,
        inode=before.st_ino,
        mode=before.st_mode,
        mtime_ns=before.st_mtime_ns,
        ctime_ns=before.st_ctime_ns,
    )


def _scan_mini_app_dist(root: Path) -> StaticSnapshot:
    root = _require_root_directory(root, label="MINI_APP_DIST_ROOT", private=False)
    directories: list[StaticDirectory] = []
    files: list[StaticFile] = []

    def visit(directory: Path, prefix: str) -> None:
        try:
            directory_state = directory.lstat()
        except OSError as exc:
            raise FencedFiCandidateBuildInputError(
                "FENCED_FI_BUILD_INPUT_STATIC_DIRECTORY_UNAVAILABLE"
            ) from exc
        if stat.S_ISLNK(directory_state.st_mode):
            _fail("FENCED_FI_BUILD_INPUT_STATIC_DIRECTORY_UNSAFE")
        directories.append(_static_directory_identity(prefix, directory_state))
        try:
            entries = sorted(os.scandir(directory), key=lambda entry: entry.name)
        except OSError as exc:
            raise FencedFiCandidateBuildInputError(
                "FENCED_FI_BUILD_INPUT_STATIC_DIRECTORY_UNAVAILABLE"
            ) from exc
        for entry in entries:
            relative = _safe_static_path(entry.name if not prefix else prefix + "/" + entry.name)
            path = directory / entry.name
            try:
                metadata = path.lstat()
            except OSError as exc:
                raise FencedFiCandidateBuildInputError(
                    "FENCED_FI_BUILD_INPUT_STATIC_ENTRY_UNAVAILABLE"
                ) from exc
            if stat.S_ISDIR(metadata.st_mode):
                visit(path, relative)
            elif stat.S_ISREG(metadata.st_mode):
                files.append(_hash_static_file(path, relative=relative))
            else:
                _fail("FENCED_FI_BUILD_INPUT_STATIC_ENTRY_UNSAFE")

    visit(root, "")
    if not files or len(files) > MAX_STATIC_FILES:
        _fail("FENCED_FI_BUILD_INPUT_STATIC_FILE_SET_INVALID")
    files = sorted(files, key=lambda item: item.path)
    if len({item.path for item in files}) != len(files) or files[0].path > files[-1].path:
        _fail("FENCED_FI_BUILD_INPUT_STATIC_FILE_SET_INVALID")
    if not any(item.path == "index.html" for item in files):
        _fail("FENCED_FI_BUILD_INPUT_STATIC_INDEX_MISSING")
    if sum(item.bytes for item in files) > MAX_STATIC_TOTAL_BYTES:
        _fail("FENCED_FI_BUILD_INPUT_STATIC_FILE_SET_INVALID")
    if len({item.path for item in directories}) != len(directories):
        _fail("FENCED_FI_BUILD_INPUT_STATIC_DIRECTORY_UNSAFE")
    return StaticSnapshot(
        root=root,
        directories=tuple(sorted(directories, key=lambda item: item.path)),
        files=tuple(files),
    )


def _files_sha256(files: Sequence[Mapping[str, object]]) -> str:
    return hashlib.sha256(_canonical_json_bytes(list(files))).hexdigest()


def _static_manifest_document(snapshot: StaticSnapshot) -> bytes:
    files = snapshot.public_files
    value = {
        "schema": STATIC_MANIFEST_SCHEMA,
        "status": STATIC_MANIFEST_STATUS,
        "mini_app_dist_root": str(snapshot.root),
        "files": files,
        "files_sha256": _files_sha256(files),
        "file_count": len(files),
        "total_bytes": snapshot.total_bytes,
    }
    return _canonical_json_bytes(value)


def _parse_static_manifest(document: bytes) -> StaticManifest:
    if not 1 <= len(document) <= MAX_MANIFEST_BYTES:
        _fail("FENCED_FI_BUILD_INPUT_STATIC_MANIFEST_INVALID")
    try:
        value = json.loads(
            document.decode("utf-8"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except FencedFiCandidateBuildInputError:
        raise
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError, RecursionError) as exc:
        raise FencedFiCandidateBuildInputError(
            "FENCED_FI_BUILD_INPUT_STATIC_MANIFEST_INVALID"
        ) from exc
    if (
        not isinstance(value, dict)
        or set(value) != _STATIC_MANIFEST_FIELDS
        or value.get("schema") != STATIC_MANIFEST_SCHEMA
        or value.get("status") != STATIC_MANIFEST_STATUS
        or _canonical_json_bytes(value) != document
    ):
        _fail("FENCED_FI_BUILD_INPUT_STATIC_MANIFEST_INVALID")
    root = _canonical_absolute_path(
        value.get("mini_app_dist_root", ""),
        label="STATIC_MANIFEST_ROOT",
    )
    raw_files = value.get("files")
    if not isinstance(raw_files, list) or not 1 <= len(raw_files) <= MAX_STATIC_FILES:
        _fail("FENCED_FI_BUILD_INPUT_STATIC_MANIFEST_INVALID")
    normalized: list[dict[str, object]] = []
    previous = ""
    total = 0
    for raw in raw_files:
        if not isinstance(raw, dict) or set(raw) != _STATIC_FILE_FIELDS:
            _fail("FENCED_FI_BUILD_INPUT_STATIC_MANIFEST_INVALID")
        path = _safe_static_path(raw.get("path"))
        digest = raw.get("sha256")
        size = raw.get("bytes")
        if (
            path <= previous
            or not isinstance(digest, str)
            or SHA256_RE.fullmatch(digest) is None
            or isinstance(size, bool)
            or not isinstance(size, int)
            or not 1 <= size <= MAX_STATIC_FILE_BYTES
        ):
            _fail("FENCED_FI_BUILD_INPUT_STATIC_MANIFEST_INVALID")
        previous = path
        total += size
        normalized.append({"path": path, "sha256": digest, "bytes": size})
    if (
        total > MAX_STATIC_TOTAL_BYTES
        or value.get("file_count") != len(normalized)
        or value.get("total_bytes") != total
        or value.get("files_sha256") != _files_sha256(normalized)
        or not any(item["path"] == "index.html" for item in normalized)
    ):
        _fail("FENCED_FI_BUILD_INPUT_STATIC_MANIFEST_INVALID")
    return StaticManifest(
        root=root,
        files=tuple(normalized),
        files_sha256=str(value["files_sha256"]),
        file_count=len(normalized),
        total_bytes=total,
        manifest_sha256=hashlib.sha256(document).hexdigest(),
    )


def create_mini_app_dist_manifest(
    *,
    mini_app_dist_root: Path,
    output: Path,
) -> dict[str, object]:
    """Create a stable root-only static manifest without changing the tree."""

    _require_root()
    first = _scan_mini_app_dist(mini_app_dist_root)
    second = _scan_mini_app_dist(mini_app_dist_root)
    if first != second:
        _fail("FENCED_FI_BUILD_INPUT_STATIC_SOURCE_CHANGED")
    payload = _static_manifest_document(first)
    parsed = _parse_static_manifest(payload)
    _write_new_private(Path(output), payload=payload, label="STATIC_MANIFEST")
    return {
        "status": "created-non-authorizing",
        "schema": STATIC_MANIFEST_SCHEMA,
        "manifest_sha256": parsed.manifest_sha256,
        "files_sha256": parsed.files_sha256,
        "file_count": parsed.file_count,
        "total_bytes": parsed.total_bytes,
        "docker_action": False,
        "network_action": False,
        "service_changed": False,
    }


def _trusted_git() -> Path:
    git = Path("/usr/bin/git")
    try:
        metadata = git.stat()
    except OSError as exc:
        raise FencedFiCandidateBuildInputError(
            "FENCED_FI_BUILD_INPUT_GIT_UNAVAILABLE"
        ) from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) & 0o022
        or not os.access(git, os.X_OK)
    ):
        _fail("FENCED_FI_BUILD_INPUT_GIT_UNSAFE")
    return git


def _run_git(
    root: Path,
    *arguments: str,
    maximum_bytes: int = MAX_GIT_OUTPUT_BYTES,
) -> bytes:
    try:
        result = subprocess.run(
            [
                str(_trusted_git()),
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
            },
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise FencedFiCandidateBuildInputError(
            "FENCED_FI_BUILD_INPUT_GIT_UNAVAILABLE"
        ) from exc
    if result.returncode != 0 or len(result.stdout) > maximum_bytes:
        _fail("FENCED_FI_BUILD_INPUT_GIT_REJECTED")
    return result.stdout


def _git_one_line(root: Path, *arguments: str) -> str:
    try:
        value = _run_git(root, *arguments, maximum_bytes=4096).decode("ascii").strip()
    except UnicodeDecodeError as exc:
        raise FencedFiCandidateBuildInputError(
            "FENCED_FI_BUILD_INPUT_GIT_REJECTED"
        ) from exc
    if not value or "\n" in value:
        _fail("FENCED_FI_BUILD_INPUT_GIT_REJECTED")
    return value


def _git_blob(root: Path, relative: str) -> bytes:
    return _run_git(root, "show", f"HEAD:{relative}", maximum_bytes=MAX_MANIFEST_BYTES)


def _require_root_controlled_source_entry(
    root: Path,
    *,
    relative: str,
    git_mode: bytes,
) -> None:
    """Refuse a clean-looking checkout whose build files are not root controlled."""

    path = PurePosixPath(relative)
    if (
        path.as_posix() != relative
        or any(part in {"", ".", ".."} for part in path.parts)
        or any(ord(character) < 0x20 for character in relative)
    ):
        _fail("FENCED_FI_BUILD_INPUT_SOURCE_GIT_REJECTED")
    current = root
    for component in path.parts[:-1]:
        current /= component
        try:
            directory = current.lstat()
        except OSError as exc:
            raise FencedFiCandidateBuildInputError(
                "FENCED_FI_BUILD_INPUT_SOURCE_ENTRY_UNAVAILABLE"
            ) from exc
        if (
            stat.S_ISLNK(directory.st_mode)
            or not stat.S_ISDIR(directory.st_mode)
            or directory.st_uid != os.geteuid()
            or stat.S_IMODE(directory.st_mode) & (0o022 | 0o7000)
        ):
            _fail("FENCED_FI_BUILD_INPUT_SOURCE_ENTRY_UNSAFE")
    target = root.joinpath(*path.parts)
    try:
        metadata = target.lstat()
    except OSError as exc:
        raise FencedFiCandidateBuildInputError(
            "FENCED_FI_BUILD_INPUT_SOURCE_ENTRY_UNAVAILABLE"
        ) from exc
    expected_permissions = 0o755 if git_mode == b"100755" else 0o644
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) != expected_permissions
    ):
        _fail("FENCED_FI_BUILD_INPUT_SOURCE_ENTRY_UNSAFE")


def _require_clean_source_tree(root: Path) -> tuple[str, str]:
    root = _require_root_directory(root, label="APPLICATION_SOURCE_ROOT", private=False)
    if root.name == LEGACY_UNFENCED_APPLICATION_RELEASE_SHA:
        _fail("FENCED_FI_BUILD_INPUT_LEGACY_2C08_APPLICATION_BLOCKED")
    release_sha = _git_one_line(root, "rev-parse", "--verify", "HEAD")
    release_tree_sha = _git_one_line(root, "rev-parse", "--verify", "HEAD^{tree}")
    if SHA1_RE.fullmatch(release_sha) is None or SHA1_RE.fullmatch(release_tree_sha) is None:
        _fail("FENCED_FI_BUILD_INPUT_SOURCE_GIT_IDENTITY_INVALID")
    if release_sha == LEGACY_UNFENCED_APPLICATION_RELEASE_SHA:
        _fail("FENCED_FI_BUILD_INPUT_LEGACY_2C08_APPLICATION_BLOCKED")
    if root.name != release_sha:
        _fail("FENCED_FI_BUILD_INPUT_SOURCE_IMMUTABLE_PATH_REQUIRED")
    if _run_git(
        root,
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
        "--ignored=matching",
        "--ignore-submodules=all",
    ):
        _fail("FENCED_FI_BUILD_INPUT_SOURCE_GIT_WORKTREE_POLLUTED")
    tracked = _run_git(root, "ls-files", "-s", "-z")
    paths: set[str] = set()
    for record in tracked.split(b"\x00"):
        if not record:
            continue
        metadata, separator, path = record.partition(b"\t")
        fields = metadata.split()
        try:
            decoded_path = path.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise FencedFiCandidateBuildInputError(
                "FENCED_FI_BUILD_INPUT_SOURCE_GIT_REJECTED"
            ) from exc
        if (
            not separator
            or len(fields) != 3
            or fields[0] not in {b"100644", b"100755"}
            or fields[2] != b"0"
            or not decoded_path
            or decoded_path.startswith("/")
            or "\x00" in decoded_path
        ):
            _fail("FENCED_FI_BUILD_INPUT_SOURCE_SYMLINK_OR_GITLINK")
        _require_root_controlled_source_entry(
            root,
            relative=decoded_path,
            git_mode=fields[0],
        )
        paths.add(decoded_path)
    if {"Dockerfile", ".dockerignore"} - paths:
        _fail("FENCED_FI_BUILD_INPUT_SOURCE_DOCKER_SURFACE_MISSING")
    if any(
        path == FRONTEND_DIST_DIRECTORY
        or path.startswith(FRONTEND_DIST_DIRECTORY + "/")
        for path in paths
    ):
        _fail("FENCED_FI_BUILD_INPUT_SOURCE_STATIC_POLLUTION")
    try:
        (root / FRONTEND_DIST_DIRECTORY).lstat()
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise FencedFiCandidateBuildInputError(
            "FENCED_FI_BUILD_INPUT_SOURCE_STATIC_POLLUTION"
        ) from exc
    else:
        _fail("FENCED_FI_BUILD_INPUT_SOURCE_STATIC_POLLUTION")
    return release_sha, release_tree_sha


def _source_file_sha256(root: Path, relative: str) -> tuple[str, bytes]:
    git_blob = _git_blob(root, relative)
    if not git_blob:
        _fail("FENCED_FI_BUILD_INPUT_SOURCE_DOCKER_SURFACE_MISSING")
    local = _secure_read(
        root / relative,
        label="SOURCE_DOCKERFILE" if relative == "Dockerfile" else "SOURCE_DOCKERIGNORE",
        maximum_bytes=MAX_MANIFEST_BYTES,
        private=False,
    )
    if local != git_blob:
        _fail("FENCED_FI_BUILD_INPUT_SOURCE_DOCKER_SURFACE_CHANGED")
    return hashlib.sha256(git_blob).hexdigest(), git_blob


def _load_application_snapshot(
    *,
    application_release_root: Path,
    term_fenced_application_evidence: Path,
) -> ApplicationSnapshot:
    root = _require_root_directory(
        application_release_root,
        label="APPLICATION_SOURCE_ROOT",
        private=False,
    )
    release_sha, release_tree_sha = _require_clean_source_tree(root)
    dockerfile_sha256, dockerfile = _source_file_sha256(root, "Dockerfile")
    dockerignore_sha256, _dockerignore = _source_file_sha256(root, ".dockerignore")
    if any(required not in dockerfile for required in _REQUIRED_DOCKERFILE_BYTES):
        _fail("FENCED_FI_BUILD_INPUT_SOURCE_DOCKER_SURFACE_INVALID")
    evidence_document = _secure_read(
        term_fenced_application_evidence,
        label="TERM_FENCED_APPLICATION_EVIDENCE",
        maximum_bytes=MAX_MANIFEST_BYTES,
        private=True,
    )
    try:
        verified_evidence = capability.verify_term_fenced_application_capability(
            evidence_document
        )
        source_tree = source_verifier.load_clean_source_tree(
            root,
            expected_release_sha=release_sha,
            expected_release_tree_sha=release_tree_sha,
        )
        source_verifier.verify_evidence_for_source(source_tree, evidence_document)
    except (
        capability.TermFencedApplicationCapabilityError,
        source_verifier.TermFencedApplicationSourceError,
    ) as exc:
        raise FencedFiCandidateBuildInputError(
            "FENCED_FI_BUILD_INPUT_TERM_FENCED_EVIDENCE_INVALID"
        ) from exc
    if (
        verified_evidence.release_sha != release_sha
        or verified_evidence.release_tree_sha != release_tree_sha
    ):
        _fail("FENCED_FI_BUILD_INPUT_TERM_FENCED_EVIDENCE_INVALID")
    return ApplicationSnapshot(
        root=root,
        release_sha=release_sha,
        release_tree_sha=release_tree_sha,
        evidence_sha256=verified_evidence.evidence_sha256,
        dockerfile_sha256=dockerfile_sha256,
        dockerignore_sha256=dockerignore_sha256,
    )


def _roots_do_not_overlap(*paths: Path) -> None:
    canonical = [_canonical_absolute_path(path, label="BUILD_INPUT") for path in paths]
    for index, left in enumerate(canonical):
        for right in canonical[index + 1 :]:
            try:
                right.relative_to(left)
            except ValueError:
                try:
                    left.relative_to(right)
                except ValueError:
                    continue
            _fail("FENCED_FI_BUILD_INPUT_PATH_OVERLAP")


def _snapshot_matches_manifest(snapshot: StaticSnapshot, manifest: StaticManifest) -> None:
    if snapshot.root != manifest.root:
        _fail("FENCED_FI_BUILD_INPUT_STATIC_MANIFEST_ROOT_MISMATCH")
    if (
        tuple(snapshot.public_files) != manifest.files
        or _files_sha256(snapshot.public_files) != manifest.files_sha256
        or len(snapshot.files) != manifest.file_count
        or snapshot.total_bytes != manifest.total_bytes
    ):
        _fail("FENCED_FI_BUILD_INPUT_STATIC_MANIFEST_MISMATCH")


def _build_input_document(
    *,
    application: ApplicationSnapshot,
    static_manifest: StaticManifest,
) -> bytes:
    value = {
        "schema": BUILD_INPUT_MANIFEST_SCHEMA,
        "status": BUILD_INPUT_MANIFEST_STATUS,
        "application": {
            "source_root": str(application.root),
            "release_sha": application.release_sha,
            "release_tree_sha": application.release_tree_sha,
            "dockerfile_sha256": application.dockerfile_sha256,
            "dockerignore_sha256": application.dockerignore_sha256,
        },
        "term_fenced_application_evidence_sha256": application.evidence_sha256,
        "mini_app_dist": {
            "root": str(static_manifest.root),
            "manifest_sha256": static_manifest.manifest_sha256,
            "files_sha256": static_manifest.files_sha256,
            "file_count": static_manifest.file_count,
            "total_bytes": static_manifest.total_bytes,
        },
        "writer_authorized": False,
        "promotion_authorized": False,
        "deployment_authorized": False,
        "execution_authorized": False,
        "full_matrix_authorized": False,
        "full_matrix_executed": False,
    }
    return _canonical_json_bytes(value)


def _verify_build_input_document(document: bytes) -> dict[str, object]:
    if not 1 <= len(document) <= MAX_MANIFEST_BYTES:
        _fail("FENCED_FI_BUILD_INPUT_MANIFEST_INVALID")
    try:
        value = json.loads(
            document.decode("utf-8"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except FencedFiCandidateBuildInputError:
        raise
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError, RecursionError) as exc:
        raise FencedFiCandidateBuildInputError(
            "FENCED_FI_BUILD_INPUT_MANIFEST_INVALID"
        ) from exc
    if (
        not isinstance(value, dict)
        or set(value) != _BUILD_INPUT_FIELDS
        or value.get("schema") != BUILD_INPUT_MANIFEST_SCHEMA
        or value.get("status") != BUILD_INPUT_MANIFEST_STATUS
        or _canonical_json_bytes(value) != document
    ):
        _fail("FENCED_FI_BUILD_INPUT_MANIFEST_INVALID")
    application = value.get("application")
    static_input = value.get("mini_app_dist")
    if not isinstance(application, dict) or set(application) != _APPLICATION_FIELDS:
        _fail("FENCED_FI_BUILD_INPUT_MANIFEST_INVALID")
    if not isinstance(static_input, dict) or set(static_input) != _BUILD_STATIC_FIELDS:
        _fail("FENCED_FI_BUILD_INPUT_MANIFEST_INVALID")
    _canonical_absolute_path(application.get("source_root", ""), label="BUILD_MANIFEST_SOURCE")
    _canonical_absolute_path(static_input.get("root", ""), label="BUILD_MANIFEST_STATIC")
    for name in ("release_sha", "release_tree_sha"):
        if not isinstance(application.get(name), str) or SHA1_RE.fullmatch(application[name]) is None:
            _fail("FENCED_FI_BUILD_INPUT_MANIFEST_INVALID")
    for name in (
        "dockerfile_sha256",
        "dockerignore_sha256",
        "term_fenced_application_evidence_sha256",
    ):
        target = value.get(name) if name.startswith("term_") else application.get(name)
        if not isinstance(target, str) or SHA256_RE.fullmatch(target) is None:
            _fail("FENCED_FI_BUILD_INPUT_MANIFEST_INVALID")
    for name in ("manifest_sha256", "files_sha256"):
        target = static_input.get(name)
        if not isinstance(target, str) or SHA256_RE.fullmatch(target) is None:
            _fail("FENCED_FI_BUILD_INPUT_MANIFEST_INVALID")
    for name in ("file_count", "total_bytes"):
        target = static_input.get(name)
        if isinstance(target, bool) or not isinstance(target, int) or target < 1:
            _fail("FENCED_FI_BUILD_INPUT_MANIFEST_INVALID")
    for name in (
        "writer_authorized",
        "promotion_authorized",
        "deployment_authorized",
        "execution_authorized",
        "full_matrix_authorized",
        "full_matrix_executed",
    ):
        if value.get(name) is not False:
            _fail("FENCED_FI_BUILD_INPUT_MANIFEST_AUTHORIZATION_FORBIDDEN")
    return value


def bind_fenced_fi_candidate_build_inputs(
    *,
    application_release_root: Path,
    term_fenced_application_evidence: Path,
    mini_app_dist_root: Path,
    mini_app_dist_manifest: Path,
    output: Path,
) -> dict[str, object]:
    """Create a manifest only after two stable source/static observations."""

    _require_root()
    static_root = _require_root_directory(
        mini_app_dist_root,
        label="MINI_APP_DIST_ROOT",
        private=False,
    )
    manifest_document = _secure_read(
        mini_app_dist_manifest,
        label="STATIC_MANIFEST",
        maximum_bytes=MAX_MANIFEST_BYTES,
        private=True,
    )
    static_manifest = _parse_static_manifest(manifest_document)
    _roots_do_not_overlap(
        Path(application_release_root),
        static_root,
        Path(mini_app_dist_manifest).parent,
        Path(output).parent,
    )
    if static_manifest.root != static_root:
        _fail("FENCED_FI_BUILD_INPUT_STATIC_MANIFEST_ROOT_MISMATCH")
    first_application = _load_application_snapshot(
        application_release_root=Path(application_release_root),
        term_fenced_application_evidence=Path(term_fenced_application_evidence),
    )
    first_static = _scan_mini_app_dist(static_root)
    _snapshot_matches_manifest(first_static, static_manifest)
    second_application = _load_application_snapshot(
        application_release_root=Path(application_release_root),
        term_fenced_application_evidence=Path(term_fenced_application_evidence),
    )
    second_static = _scan_mini_app_dist(static_root)
    _snapshot_matches_manifest(second_static, static_manifest)
    if first_application != second_application:
        _fail("FENCED_FI_BUILD_INPUT_SOURCE_CHANGED")
    if first_static != second_static:
        _fail("FENCED_FI_BUILD_INPUT_STATIC_SOURCE_CHANGED")
    payload = _build_input_document(
        application=second_application,
        static_manifest=static_manifest,
    )
    verified = _verify_build_input_document(payload)
    _write_new_private(Path(output), payload=payload, label="BUILD_INPUT_MANIFEST")
    return {
        "status": "created-non-authorizing",
        "schema": BUILD_INPUT_MANIFEST_SCHEMA,
        "manifest_sha256": hashlib.sha256(payload).hexdigest(),
        "release_sha": second_application.release_sha,
        "release_tree_sha": second_application.release_tree_sha,
        "term_fenced_application_evidence_sha256": second_application.evidence_sha256,
        "mini_app_dist_manifest_sha256": static_manifest.manifest_sha256,
        "mini_app_dist_files_sha256": static_manifest.files_sha256,
        "mini_app_dist_file_count": static_manifest.file_count,
        "mini_app_dist_total_bytes": static_manifest.total_bytes,
        "docker_action": False,
        "network_action": False,
        "service_changed": False,
        "writer_authorized": verified["writer_authorized"],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    snapshot = commands.add_parser(
        "snapshot-static",
        help="create one root-only manifest for caller-supplied mini_app_dist",
    )
    snapshot.add_argument("--mini-app-dist-root", required=True, type=Path)
    snapshot.add_argument("--output", required=True, type=Path)
    bind = commands.add_parser(
        "bind",
        help="bind clean source/evidence to a verified mini_app_dist manifest",
    )
    bind.add_argument("--application-release-root", required=True, type=Path)
    bind.add_argument("--term-fenced-application-evidence", required=True, type=Path)
    bind.add_argument("--mini-app-dist-root", required=True, type=Path)
    bind.add_argument("--mini-app-dist-manifest", required=True, type=Path)
    bind.add_argument("--output", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        if arguments.command == "snapshot-static":
            result = create_mini_app_dist_manifest(
                mini_app_dist_root=arguments.mini_app_dist_root,
                output=arguments.output,
            )
        else:
            result = bind_fenced_fi_candidate_build_inputs(
                application_release_root=arguments.application_release_root,
                term_fenced_application_evidence=arguments.term_fenced_application_evidence,
                mini_app_dist_root=arguments.mini_app_dist_root,
                mini_app_dist_manifest=arguments.mini_app_dist_manifest,
                output=arguments.output,
            )
    except FencedFiCandidateBuildInputError as exc:
        print(
            json.dumps(
                {"status": "blocked", "error_class": type(exc).__name__, "error": exc.code},
                ensure_ascii=True,
                sort_keys=True,
            )
        )
        return 2
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
