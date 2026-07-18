#!/usr/bin/env python3
"""Attest the exact host and venv Python runtime for Writer Witness.

The release-bound system manifest closes CPython, active stdlib/lib-dynload,
ELF/shared-library dependencies, loader state and Ubuntu package identity. The
lock is authoritative for the venv. Package names and versions alone are
insufficient: all active host bytes, every RECORD-listed file, and every venv
node feed deterministic digests. Production invocation is accepted only from
an empty environment with ``-I -S -B -X utf8 -X pycache_prefix=/dev/null``.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
from importlib import metadata as importlib_metadata
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import subprocess
import stat
import struct
import sys
import sysconfig
import zipfile
from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence


EXPECTED_IMPLEMENTATION = "cpython"
EXPECTED_PYTHON_MAJOR_MINOR = (3, 12)
BOOTSTRAP_EXTRAS = frozenset({"pip", "setuptools", "wheel"})
MAXIMUM_LOCK_BYTES = 1024 * 1024
MAXIMUM_SYSTEM_MANIFEST_BYTES = 4 * 1024 * 1024
MAXIMUM_DPKG_STATUS_BYTES = 64 * 1024 * 1024
SYSTEM_RUNTIME_SCHEMA_VERSION = "writer_witness_system_runtime_v2"
TRUSTED_SYSTEM_PATH = "/usr/sbin:/usr/bin:/sbin:/bin"
SYSTEM_MANIFEST_FIELDS = frozenset(
    {
        "architecture",
        "elf_objects",
        "executable_path",
        "executable_sha256",
        "implementation",
        "loader",
        "os_release",
        "packages",
        "python_version",
        "schema_version",
        "stdlib",
        "venv_elf_system_roots",
    }
)
SYSTEM_STDLIB_FIELDS = frozenset(
    {
        "entry_count",
        "external_files",
        "file_count",
        "inactive_import_paths",
        "packages",
        "path",
        "python_path",
        "symlink_count",
        "tree_sha256",
    }
)
SYSTEM_EXTERNAL_FILE_FIELDS = frozenset({"path", "sha256"})
SYSTEM_ELF_FIELDS = frozenset(
    {"interpreter", "needed", "package", "path", "sha256", "soname"}
)
SYSTEM_PACKAGE_FIELDS = frozenset(
    {
        "architecture",
        "info_stem",
        "list_sha256",
        "md5sums_sha256",
        "md5_verified_file_count",
        "md5_verified_paths_sha256",
        "name",
        "status",
        "version",
    }
)
SYSTEM_OS_RELEASE_FIELDS = frozenset(
    {"alias_target", "id", "path", "sha256", "version_id"}
)
SYSTEM_LOADER_FIELDS = frozenset(
    {
        "cache_path",
        "cache_sha256",
        "configuration_entry_count",
        "configuration_sha256",
        "preload_path",
        "preload_sha256",
    }
)
SYSTEM_ATTESTATION_FIELDS = frozenset(
    {
        "system_elf_closure_sha256",
        "system_elf_object_count",
        "system_os_release_sha256",
        "system_package_count",
        "system_package_set_sha256",
        "system_runtime_attested",
        "system_runtime_manifest_sha256",
        "system_runtime_sha256",
        "system_stdlib_entry_count",
        "system_stdlib_tree_sha256",
    }
)
PACKAGE_NAME_PATTERN = re.compile(
    r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?\Z"
)
LOCK_ENTRY_PATTERN = re.compile(
    r"(?P<name>[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)"
    r"==(?P<version>[^\s;#]+)\Z"
)
PYTHON_VERSION_PATTERN = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\Z")
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
RECORD_SHA256_PATTERN = re.compile(r"[A-Za-z0-9_-]{43}\Z")
NORMALIZE_NAME_PATTERN = re.compile(r"[-_.]+")
# Standard-library-only copy of the public PEP 440 grammar. Importing packaging
# or pip before their own files are attested would make the verifier bootstrap
# trust the exact runtime it is supposed to verify.
PEP440_PATTERN = re.compile(
    r"""
    v?
    (?:
        (?:(?P<epoch>[0-9]+)!)?
        (?P<release>[0-9]+(?:\.[0-9]+)*)
        (?P<pre>
            [-_.]?
            (?P<pre_l>alpha|a|beta|b|preview|pre|c|rc)
            [-_.]?
            (?P<pre_n>[0-9]+)?
        )?
        (?P<post>
            (?:-(?P<post_n1>[0-9]+))
            |(?:
                [-_.]?
                (?P<post_l>post|rev|r)
                [-_.]?
                (?P<post_n2>[0-9]+)?
            )
        )?
        (?P<dev>
            [-_.]?
            (?P<dev_l>dev)
            [-_.]?
            (?P<dev_n>[0-9]+)?
        )?
    )
    (?:\+(?P<local>[a-z0-9]+(?:[-_.][a-z0-9]+)*))?
    \Z
    """,
    re.IGNORECASE | re.VERBOSE,
)
UNSAFE_WRITE_BITS = stat.S_IWGRP | stat.S_IWOTH
UNSAFE_EXECUTABLE_BITS = UNSAFE_WRITE_BITS | stat.S_ISUID | stat.S_ISGID


class RuntimeAttestationError(RuntimeError):
    """The active Python runtime cannot be proven to match its exact lock."""


@dataclass(frozen=True)
class RuntimeIdentity:
    implementation: str
    major: int
    minor: int
    micro: int
    is_virtual_environment: bool = False

    @property
    def public_implementation(self) -> str:
        return "CPython" if self.implementation == "cpython" else self.implementation

    @property
    def version(self) -> str:
        return f"{self.major}.{self.minor}.{self.micro}"


@dataclass(frozen=True)
class LockInventory:
    packages: Mapping[str, str]
    sha256: str


@dataclass(frozen=True)
class RuntimePrefix:
    alias: Path
    resolved: Path
    alias_metadata: os.stat_result


@dataclass(frozen=True)
class AttestedFile:
    path: str
    sha256: str
    size: int


@dataclass(frozen=True)
class InstalledInventory:
    packages: Mapping[str, str]
    files: Mapping[str, tuple[AttestedFile, ...]]

    @property
    def file_count(self) -> int:
        return sum(len(files) for files in self.files.values())


@dataclass(frozen=True)
class InterpreterAttestation:
    sha256: str
    target: Path


@dataclass(frozen=True)
class ScannedNode:
    path: str
    kind: str
    mode: int
    uid: int
    gid: int
    device: int
    inode: int
    links: int
    size: int
    mtime_ns: int
    ctime_ns: int
    link_target: str | None = None


@dataclass(frozen=True)
class StructuralEntry:
    path: str
    kind: str
    sha256: str
    size: int
    link_target: str | None = None


@dataclass(frozen=True)
class SystemTreeInventory:
    document: tuple[Mapping[str, object], ...]
    external_files: tuple[Mapping[str, str], ...]
    elf_paths: tuple[Path, ...]
    entry_count: int
    file_count: int
    symlink_count: int
    sha256: str


@dataclass(frozen=True)
class ElfIdentity:
    interpreter: str | None
    needed: tuple[str, ...]
    soname: str | None


def _require_exact_fields(
    value: Mapping[str, object], expected: frozenset[str], *, subject: str
) -> None:
    observed = set(value)
    if observed != expected:
        raise RuntimeAttestationError(f"{subject} has an unsupported schema")


def _unique_json_object(pairs: Iterable[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise RuntimeAttestationError("system runtime manifest contains a duplicate key")
        result[key] = value
    return result


def _reject_non_finite_json(value: str) -> object:
    raise RuntimeAttestationError("system runtime manifest contains a non-finite number")


def _parse_system_manifest(value: bytes) -> Mapping[str, object]:
    try:
        decoded = value.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RuntimeAttestationError("system runtime manifest is not valid UTF-8") from exc
    if not decoded or decoded.startswith("\ufeff") or "\r" in decoded:
        raise RuntimeAttestationError("system runtime manifest is not canonical UTF-8 JSON")
    try:
        document = json.loads(
            decoded,
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_non_finite_json,
        )
    except RuntimeAttestationError:
        raise
    except (ValueError, RecursionError) as exc:
        raise RuntimeAttestationError("system runtime manifest is not valid bounded JSON") from exc
    if not isinstance(document, dict):
        raise RuntimeAttestationError("system runtime manifest root must be an object")
    _require_exact_fields(document, SYSTEM_MANIFEST_FIELDS, subject="system runtime manifest")
    if document.get("schema_version") != SYSTEM_RUNTIME_SCHEMA_VERSION:
        raise RuntimeAttestationError("system runtime manifest schema version is unsupported")
    return document


def _read_secure_system_manifest(
    path: Path, *, expected_uid: int | None
) -> tuple[bytes, str]:
    try:
        descriptor = os.open(path, _safe_open_flags())
    except OSError as exc:
        raise RuntimeAttestationError("cannot safely open system runtime manifest") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise RuntimeAttestationError("system runtime manifest is not a regular file")
        if before.st_nlink != 1:
            raise RuntimeAttestationError("system runtime manifest must have one hard link")
        if expected_uid is not None and before.st_uid != expected_uid:
            raise RuntimeAttestationError("system runtime manifest has an unexpected owner")
        if stat.S_IMODE(before.st_mode) != 0o644:
            raise RuntimeAttestationError("system runtime manifest mode must be exactly 0644")
        if before.st_size <= 0 or before.st_size > MAXIMUM_SYSTEM_MANIFEST_BYTES:
            raise RuntimeAttestationError("system runtime manifest has an unsafe size")
        chunks: list[bytes] = []
        remaining = MAXIMUM_SYSTEM_MANIFEST_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        value = b"".join(chunks)
        if len(value) != before.st_size or len(value) > MAXIMUM_SYSTEM_MANIFEST_BYTES:
            raise RuntimeAttestationError("system runtime manifest changed during attestation")
        _assert_stable_metadata(
            before, os.fstat(descriptor), subject="system runtime manifest"
        )
        return value, hashlib.sha256(value).hexdigest()
    except RuntimeAttestationError:
        raise
    except OSError as exc:
        raise RuntimeAttestationError("cannot safely read system runtime manifest") from exc
    finally:
        os.close(descriptor)


def _canonical_absolute_path(path: Path, *, subject: str) -> Path:
    if not path.is_absolute():
        raise RuntimeAttestationError(f"{subject} is not absolute")
    normalized = Path(os.path.normpath(os.fspath(path)))
    try:
        resolved = normalized.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise RuntimeAttestationError(f"cannot safely resolve {subject}") from exc
    if resolved != normalized:
        raise RuntimeAttestationError(f"{subject} is not a canonical path")
    return resolved


def _require_clean_system_startup() -> None:
    if not (
        sys.flags.isolated
        and sys.flags.no_site
        and sys.flags.ignore_environment
        and sys.flags.dont_write_bytecode
        and getattr(sys.flags, "safe_path", False)
        and sys.flags.utf8_mode == 1
        and sys.pycache_prefix == "/dev/null"
    ):
        raise RuntimeAttestationError(
            "runtime verifier requires -I -S -B -X utf8 -X pycache_prefix=/dev/null"
        )
    environment = dict(os.environ)
    if environment.get("PATH") != TRUSTED_SYSTEM_PATH:
        raise RuntimeAttestationError("runtime verifier PATH is not the trusted clean value")
    allowed = {"PATH": TRUSTED_SYSTEM_PATH}
    if environment.get("LC_CTYPE") == "C.UTF-8":
        allowed["LC_CTYPE"] = "C.UTF-8"
    if environment != allowed:
        raise RuntimeAttestationError("runtime verifier did not start with a clean environment")
    identity = _runtime_identity()
    stdlib = Path(sysconfig.get_path("stdlib")).resolve(strict=True)
    inactive_zip = Path(f"/usr/lib/python{identity.major}{identity.minor}.zip")
    expected_python_path = [
        inactive_zip.as_posix(),
        stdlib.as_posix(),
        (stdlib / "lib-dynload").as_posix(),
    ]
    if list(sys.path) != expected_python_path:
        raise RuntimeAttestationError("isolated system Python import path is unexpected")
    if inactive_zip.exists() or inactive_zip.is_symlink():
        raise RuntimeAttestationError("inactive system zip import path unexpectedly exists")


def _validate_inactive_pycache(directory: Path, *, expected_uid: int | None) -> None:
    try:
        with os.scandir(directory) as iterator:
            entries = sorted(iterator, key=lambda item: item.name)
    except OSError as exc:
        raise RuntimeAttestationError("cannot safely enumerate inactive system bytecode") from exc
    for entry in entries:
        path = Path(entry.path)
        try:
            metadata = entry.stat(follow_symlinks=False)
        except OSError as exc:
            raise RuntimeAttestationError("cannot safely inspect inactive system bytecode") from exc
        if stat.S_ISDIR(metadata.st_mode):
            _safe_metadata(
                metadata, expected_uid=expected_uid, subject="inactive bytecode directory"
            )
            _validate_inactive_pycache(path, expected_uid=expected_uid)
        elif stat.S_ISREG(metadata.st_mode):
            if path.suffix != ".pyc":
                raise RuntimeAttestationError(
                    "inactive __pycache__ contains a non-bytecode file"
                )
            _safe_metadata(
                metadata, expected_uid=expected_uid, subject="inactive bytecode file"
            )
            if metadata.st_mode & (stat.S_ISUID | stat.S_ISGID):
                raise RuntimeAttestationError("inactive bytecode has unsafe privilege bits")
        else:
            raise RuntimeAttestationError("inactive __pycache__ contains an unsafe file type")


def _looks_like_elf(path: Path) -> bool:
    try:
        descriptor = os.open(path, _safe_open_flags())
    except OSError as exc:
        raise RuntimeAttestationError("cannot safely open a possible ELF object") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            return False
        header = os.read(descriptor, 18)
        if len(header) < 18 or header[:4] != b"\x7fELF" or header[5] != 1:
            return False
        return struct.unpack_from("<H", header, 16)[0] in {2, 3}
    finally:
        os.close(descriptor)


def _scan_system_tree_once(
    root: Path, *, expected_uid: int | None
) -> SystemTreeInventory:
    try:
        root_before = root.lstat()
    except OSError as exc:
        raise RuntimeAttestationError("cannot inspect the system standard-library root") from exc
    if not stat.S_ISDIR(root_before.st_mode) or stat.S_ISLNK(root_before.st_mode):
        raise RuntimeAttestationError("system standard-library root is not a real directory")
    _safe_metadata(
        root_before, expected_uid=expected_uid, subject="system standard-library root"
    )
    records: list[Mapping[str, object]] = [
        {
            "gid": root_before.st_gid,
            "kind": "directory",
            "link_target": None,
            "mode": stat.S_IMODE(root_before.st_mode),
            "path": ".",
            "sha256": None,
            "size": 0,
            "uid": root_before.st_uid,
        }
    ]
    external: dict[str, Mapping[str, str]] = {}
    elf_paths: set[Path] = set()
    file_count = 0
    symlink_count = 0

    def walk(directory: Path) -> None:
        nonlocal file_count, symlink_count
        try:
            with os.scandir(directory) as iterator:
                entries = sorted(iterator, key=lambda item: item.name)
        except OSError as exc:
            raise RuntimeAttestationError("cannot enumerate the system standard library") from exc
        for entry in entries:
            path = Path(entry.path)
            try:
                metadata = entry.stat(follow_symlinks=False)
                relative = path.relative_to(root).as_posix()
            except (OSError, ValueError) as exc:
                raise RuntimeAttestationError("cannot inspect a system standard-library entry") from exc
            if stat.S_ISDIR(metadata.st_mode):
                _safe_metadata(
                    metadata,
                    expected_uid=expected_uid,
                    subject="system standard-library directory",
                )
                if metadata.st_mode & (stat.S_ISUID | stat.S_ISGID):
                    raise RuntimeAttestationError(
                        "system standard-library directory has unsafe privilege bits"
                    )
                if path.name == "__pycache__":
                    _validate_inactive_pycache(path, expected_uid=expected_uid)
                    continue
                records.append(
                    {
                        "gid": metadata.st_gid,
                        "kind": "directory",
                        "link_target": None,
                        "mode": stat.S_IMODE(metadata.st_mode),
                        "path": relative,
                        "sha256": None,
                        "size": 0,
                        "uid": metadata.st_uid,
                    }
                )
                walk(path)
                continue
            if stat.S_ISREG(metadata.st_mode):
                digest, size = _stable_hash_file(
                    path,
                    expected_uid=expected_uid,
                    require_single_link=False,
                    subject="system standard-library file",
                )
                records.append(
                    {
                        "gid": metadata.st_gid,
                        "kind": "file",
                        "link_target": None,
                        "mode": stat.S_IMODE(metadata.st_mode),
                        "path": relative,
                        "sha256": digest,
                        "size": size,
                        "uid": metadata.st_uid,
                    }
                )
                file_count += 1
                if _looks_like_elf(path):
                    elf_paths.add(path)
                continue
            if stat.S_ISLNK(metadata.st_mode):
                if expected_uid is not None and metadata.st_uid != expected_uid:
                    raise RuntimeAttestationError(
                        "system standard-library symlink has an unexpected owner"
                    )
                try:
                    target_text = os.readlink(path)
                    resolved = path.resolve(strict=True)
                    after = path.lstat()
                except (OSError, RuntimeError) as exc:
                    raise RuntimeAttestationError(
                        "cannot safely resolve a system standard-library symlink"
                    ) from exc
                _assert_stable_metadata(
                    metadata, after, subject="system standard-library symlink"
                )
                if not target_text or "\x00" in target_text:
                    raise RuntimeAttestationError(
                        "system standard-library symlink has an unsafe target"
                    )
                records.append(
                    {
                        "gid": metadata.st_gid,
                        "kind": "symlink",
                        "link_target": target_text,
                        "mode": stat.S_IMODE(metadata.st_mode),
                        "path": relative,
                        "sha256": None,
                        "size": metadata.st_size,
                        "uid": metadata.st_uid,
                    }
                )
                symlink_count += 1
                try:
                    resolved.relative_to(root)
                except ValueError:
                    digest, _ = _stable_hash_file(
                        resolved,
                        expected_uid=expected_uid,
                        require_single_link=False,
                        subject="external system standard-library symlink target",
                    )
                    external[resolved.as_posix()] = {
                        "path": resolved.as_posix(),
                        "sha256": digest,
                    }
                    if _looks_like_elf(resolved):
                        elf_paths.add(resolved)
                continue
            raise RuntimeAttestationError(
                "system standard-library tree contains a forbidden special file"
            )

    walk(root)
    _assert_stable_metadata(
        root_before, root.lstat(), subject="system standard-library root"
    )
    ordered_records = tuple(sorted(records, key=lambda item: str(item["path"])))
    return SystemTreeInventory(
        document=ordered_records,
        external_files=tuple(external[key] for key in sorted(external)),
        elf_paths=tuple(sorted(elf_paths, key=os.fspath)),
        entry_count=len(ordered_records),
        file_count=file_count,
        symlink_count=symlink_count,
        sha256=_canonical_json_sha256(ordered_records),
    )


def _scan_system_tree(
    root: Path, *, expected_uid: int | None
) -> SystemTreeInventory:
    first = _scan_system_tree_once(root, expected_uid=expected_uid)
    second = _scan_system_tree_once(root, expected_uid=expected_uid)
    if first != second:
        raise RuntimeAttestationError("system standard-library tree changed during attestation")
    return first


def _read_elf_bytes(path: Path, *, expected_uid: int | None) -> tuple[bytes, str]:
    maximum = 512 * 1024 * 1024
    try:
        descriptor = os.open(path, _safe_open_flags())
    except OSError as exc:
        raise RuntimeAttestationError("cannot safely open an ELF runtime object") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size <= 0 or before.st_size > maximum:
            raise RuntimeAttestationError("ELF runtime object has an unsafe file type or size")
        _safe_metadata(before, expected_uid=expected_uid, subject="ELF runtime object")
        if before.st_mode & (stat.S_ISUID | stat.S_ISGID):
            raise RuntimeAttestationError("ELF runtime object has unsafe privilege bits")
        chunks: list[bytes] = []
        remaining = maximum + 1
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        value = b"".join(chunks)
        if len(value) != before.st_size:
            raise RuntimeAttestationError("ELF runtime object changed during attestation")
        _assert_stable_metadata(before, os.fstat(descriptor), subject="ELF runtime object")
        return value, hashlib.sha256(value).hexdigest()
    except RuntimeAttestationError:
        raise
    except OSError as exc:
        raise RuntimeAttestationError("cannot safely read an ELF runtime object") from exc
    finally:
        os.close(descriptor)


def _parse_elf_identity(value: bytes) -> ElfIdentity:
    if len(value) < 64 or value[:4] != b"\x7fELF":
        raise RuntimeAttestationError("runtime dependency is not an ELF object")
    if value[4] != 2 or value[5] != 1 or value[6] != 1:
        raise RuntimeAttestationError("runtime dependency is not ELF64 little-endian")
    try:
        header = struct.unpack_from("<HHIQQQIHHHHHH", value, 16)
    except struct.error as exc:
        raise RuntimeAttestationError("ELF header is truncated") from exc
    machine = header[1]
    phoff = header[4]
    phentsize = header[8]
    phnum = header[9]
    if machine != 62 or phentsize < 56 or phnum <= 0 or phnum >= 4096:
        raise RuntimeAttestationError("ELF runtime object has unsupported machine metadata")
    if phoff > len(value) or phnum * phentsize > len(value) - phoff:
        raise RuntimeAttestationError("ELF program-header table is out of bounds")
    loads: list[tuple[int, int, int]] = []
    dynamic: tuple[int, int] | None = None
    interpreter: str | None = None
    for index in range(phnum):
        offset = phoff + index * phentsize
        try:
            p_type, _, p_offset, p_vaddr, _, p_filesz, _, _ = struct.unpack_from(
                "<IIQQQQQQ", value, offset
            )
        except struct.error as exc:
            raise RuntimeAttestationError("ELF program header is truncated") from exc
        if p_offset > len(value) or p_filesz > len(value) - p_offset:
            raise RuntimeAttestationError("ELF segment is out of bounds")
        if p_type == 1:
            loads.append((p_vaddr, p_filesz, p_offset))
        elif p_type == 2:
            dynamic = (p_offset, p_filesz)
        elif p_type == 3:
            raw = value[p_offset : p_offset + p_filesz]
            if not raw.endswith(b"\x00") or b"\x00" in raw[:-1]:
                raise RuntimeAttestationError("ELF interpreter path is malformed")
            try:
                interpreter = raw[:-1].decode("ascii")
            except UnicodeDecodeError as exc:
                raise RuntimeAttestationError("ELF interpreter path is not ASCII") from exc
            if not interpreter.startswith("/"):
                raise RuntimeAttestationError("ELF interpreter path is not absolute")
    if dynamic is None:
        return ElfIdentity(interpreter=interpreter, needed=(), soname=None)
    tags: list[tuple[int, int]] = []
    dynamic_offset, dynamic_size = dynamic
    for offset in range(dynamic_offset, dynamic_offset + dynamic_size, 16):
        if offset + 16 > len(value):
            raise RuntimeAttestationError("ELF dynamic table is truncated")
        tag, item = struct.unpack_from("<qQ", value, offset)
        if tag == 0:
            break
        tags.append((tag, item))
    strtab_addresses = [item for tag, item in tags if tag == 5]
    strtab_sizes = [item for tag, item in tags if tag == 10]
    if len(strtab_addresses) != 1 or len(strtab_sizes) != 1:
        raise RuntimeAttestationError("ELF dynamic string table is ambiguous")
    if any(tag in {15, 29} for tag, _ in tags):
        raise RuntimeAttestationError("ELF runtime object contains RPATH or RUNPATH")
    address = strtab_addresses[0]
    size = strtab_sizes[0]
    strtab_offset: int | None = None
    for vaddr, filesz, file_offset in loads:
        if vaddr <= address and address - vaddr < filesz:
            strtab_offset = file_offset + (address - vaddr)
            break
    if strtab_offset is None or size <= 0 or strtab_offset + size > len(value):
        raise RuntimeAttestationError("ELF dynamic string table is out of bounds")

    def dynamic_string(item: int) -> str:
        if item >= size:
            raise RuntimeAttestationError("ELF dynamic string offset is out of bounds")
        start = strtab_offset + item
        end = value.find(b"\x00", start, strtab_offset + size)
        if end < 0:
            raise RuntimeAttestationError("ELF dynamic string is unterminated")
        try:
            result = value[start:end].decode("ascii")
        except UnicodeDecodeError as exc:
            raise RuntimeAttestationError("ELF dynamic string is not ASCII") from exc
        if not result or "/" in result or "\x00" in result:
            raise RuntimeAttestationError("ELF dynamic dependency name is unsafe")
        return result

    needed = tuple(dynamic_string(item) for tag, item in tags if tag == 1)
    if len(set(needed)) != len(needed):
        raise RuntimeAttestationError("ELF dynamic dependency list contains duplicates")
    sonames = [dynamic_string(item) for tag, item in tags if tag == 14]
    if len(sonames) > 1:
        raise RuntimeAttestationError("ELF SONAME is ambiguous")
    return ElfIdentity(
        interpreter=interpreter,
        needed=needed,
        soname=sonames[0] if sonames else None,
    )


def _library_search_directories(architecture: str) -> tuple[Path, ...]:
    if architecture != "x86_64":
        raise RuntimeAttestationError("system runtime architecture is unsupported")
    candidates = (
        Path("/usr/lib/x86_64-linux-gnu"),
        Path("/usr/lib64"),
        Path("/usr/lib"),
    )
    result: list[Path] = []
    for candidate in candidates:
        try:
            resolved = candidate.resolve(strict=True)
        except (OSError, RuntimeError):
            continue
        if resolved not in result:
            result.append(resolved)
    return tuple(result)


def _resolve_elf_dependency(name: str, requester: Path, architecture: str) -> Path:
    if not name or "/" in name or "\x00" in name:
        raise RuntimeAttestationError("ELF dependency name is unsafe")
    for directory in (requester.parent, *_library_search_directories(architecture)):
        candidate = directory / name
        try:
            resolved = candidate.resolve(strict=True)
            metadata = resolved.lstat()
        except (OSError, RuntimeError):
            continue
        if stat.S_ISREG(metadata.st_mode):
            return resolved
    raise RuntimeAttestationError("cannot close the ELF dependency inventory")


def _actual_elf_dependencies(
    path: Path, identity: ElfIdentity, architecture: str
) -> tuple[Path, ...]:
    """Ask the pinned glibc loader which exact files it would map."""

    if architecture != "x86_64":
        raise RuntimeAttestationError("system runtime architecture is unsupported")
    loader = Path("/lib64/ld-linux-x86-64.so.2").resolve(strict=True)
    try:
        completed = subprocess.run(
            [loader.as_posix(), "--list", path.as_posix()],
            env={"PATH": TRUSTED_SYSTEM_PATH},
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeAttestationError("cannot reproduce dynamic-loader resolution") from exc
    if completed.returncode != 0 or completed.stderr:
        raise RuntimeAttestationError("dynamic-loader resolution failed closed")
    resolved_by_name: dict[str, Path] = {}
    for raw_line in completed.stdout.splitlines():
        line = raw_line.strip()
        if not line or line == "statically linked" or line.startswith("linux-vdso.so.1 "):
            continue
        if " => " in line:
            name, remainder = line.split(" => ", 1)
            if "/" in name:
                name = Path(name).name
            raw_path = remainder.rsplit(" (0x", 1)[0]
            if raw_path == "not found":
                raise RuntimeAttestationError("dynamic-loader dependency is missing")
        else:
            raw_path = line.rsplit(" (0x", 1)[0]
            name = Path(raw_path).name
        if not raw_path.startswith("/"):
            raise RuntimeAttestationError("dynamic-loader output is unsafe")
        try:
            resolved = Path(raw_path).resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise RuntimeAttestationError("dynamic-loader output cannot be resolved") from exc
        if name in resolved_by_name and resolved_by_name[name] != resolved:
            raise RuntimeAttestationError("dynamic-loader output is ambiguous")
        resolved_by_name[name] = resolved
    missing = set(identity.needed).difference(resolved_by_name)
    if missing:
        raise RuntimeAttestationError("dynamic-loader output is incomplete")
    return tuple(resolved_by_name[name] for name in identity.needed)


def _wheelhouse_system_elf_roots(
    wheelhouse: Path, *, architecture: str
) -> tuple[Path, ...]:
    try:
        root = wheelhouse.resolve(strict=True)
        root_metadata = root.lstat()
    except (OSError, RuntimeError) as exc:
        raise RuntimeAttestationError("cannot resolve the runtime wheelhouse") from exc
    if not stat.S_ISDIR(root_metadata.st_mode) or stat.S_ISLNK(root_metadata.st_mode):
        raise RuntimeAttestationError("runtime wheelhouse is not a real directory")
    identities: list[tuple[str, ElfIdentity]] = []
    total_uncompressed = 0
    try:
        wheels = sorted(root.glob("*.whl"), key=os.fspath)
    except OSError as exc:
        raise RuntimeAttestationError("cannot enumerate runtime wheels") from exc
    if not wheels:
        raise RuntimeAttestationError("runtime wheelhouse contains no wheels")
    for wheel in wheels:
        try:
            metadata = wheel.lstat()
        except OSError as exc:
            raise RuntimeAttestationError("cannot inspect a runtime wheel") from exc
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or metadata.st_size <= 0
            or metadata.st_size > 512 * 1024 * 1024
        ):
            raise RuntimeAttestationError("runtime wheel has unsafe metadata")
        try:
            with zipfile.ZipFile(wheel) as archive:
                names: set[str] = set()
                for info in sorted(archive.infolist(), key=lambda item: item.filename):
                    member = PurePosixPath(info.filename)
                    if (
                        info.filename in names
                        or member.is_absolute()
                        or not member.parts
                        or any(part in {"", ".", ".."} for part in member.parts)
                    ):
                        raise RuntimeAttestationError("runtime wheel has an unsafe member path")
                    names.add(info.filename)
                    member_mode = info.external_attr >> 16
                    if member_mode and stat.S_ISLNK(member_mode):
                        raise RuntimeAttestationError("runtime wheel contains a symlink")
                    if info.is_dir():
                        continue
                    if info.file_size < 0 or info.file_size > 512 * 1024 * 1024:
                        raise RuntimeAttestationError("runtime wheel member is oversized")
                    total_uncompressed += info.file_size
                    if total_uncompressed > 2 * 1024 * 1024 * 1024:
                        raise RuntimeAttestationError("runtime wheelhouse expands beyond its limit")
                    with archive.open(info, "r") as source:
                        prefix = source.read(18)
                        if (
                            len(prefix) < 18
                            or prefix[:4] != b"\x7fELF"
                            or prefix[5] != 1
                            or struct.unpack_from("<H", prefix, 16)[0] not in {2, 3}
                        ):
                            continue
                        value = prefix + source.read()
                    identities.append((member.as_posix(), _parse_elf_identity(value)))
        except RuntimeAttestationError:
            raise
        except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
            raise RuntimeAttestationError("cannot safely inspect runtime wheel ELF files") from exc
    provided: dict[str, str] = {}
    for member, identity in identities:
        name = identity.soname or PurePosixPath(member).name
        if name in provided and provided[name] != member:
            raise RuntimeAttestationError("runtime wheels provide an ambiguous ELF SONAME")
        provided[name] = member
    roots: set[Path] = set()
    requester = Path("/usr/bin/python3.12")
    for _, identity in identities:
        for needed in identity.needed:
            if needed not in provided:
                roots.add(_resolve_elf_dependency(needed, requester, architecture))
    return tuple(sorted(roots, key=os.fspath))


def _read_dpkg_owner_inventory() -> Mapping[str, str]:
    info_root = Path("/var/lib/dpkg/info")
    owners: dict[str, str] = {}
    try:
        candidates = sorted(info_root.glob("*.list"), key=os.fspath)
    except OSError as exc:
        raise RuntimeAttestationError("cannot enumerate dpkg ownership metadata") from exc
    for path in candidates:
        stem = path.name[:-5]
        try:
            value = path.read_bytes()
        except OSError as exc:
            raise RuntimeAttestationError("cannot read dpkg ownership metadata") from exc
        if len(value) > 64 * 1024 * 1024:
            raise RuntimeAttestationError("dpkg ownership metadata is oversized")
        try:
            lines = value.decode("utf-8").splitlines()
        except UnicodeDecodeError as exc:
            raise RuntimeAttestationError("dpkg ownership metadata is not UTF-8") from exc
        for line in lines:
            if not line.startswith("/") or "\x00" in line:
                continue
            normalized = os.path.normpath(line)
            # Runtime paths are canonical /usr paths on the approved usrmerge
            # baseline.  Do not resolve every package-list entry: doing so is
            # both needlessly expensive and lets an alias entry mask an exact
            # owner (for example python3 -> python3.12).
            owners[normalized] = stem
    return owners


def _package_owner(path: Path, owners: Mapping[str, str]) -> str:
    owner = owners.get(path.as_posix())
    if owner is None:
        raise RuntimeAttestationError("system runtime file has no dpkg package owner")
    return owner


def _read_dpkg_status() -> Mapping[tuple[str, str], Mapping[str, str]]:
    path = Path("/var/lib/dpkg/status")
    value, _ = _stable_read_runtime_file(
        path,
        expected_uid=0,
        maximum_bytes=MAXIMUM_DPKG_STATUS_BYTES,
        subject="dpkg status database",
    )
    try:
        decoded = value.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RuntimeAttestationError("dpkg status database is not UTF-8") from exc
    result: dict[tuple[str, str], Mapping[str, str]] = {}
    for paragraph in decoded.split("\n\n"):
        fields: dict[str, str] = {}
        for line in paragraph.splitlines():
            if not line or line[0].isspace() or ": " not in line:
                continue
            key, item = line.split(": ", 1)
            fields[key] = item
        if not fields:
            continue
        package = fields.get("Package")
        architecture = fields.get("Architecture")
        if package and architecture:
            key = (package, architecture)
            if key in result:
                raise RuntimeAttestationError("dpkg status contains a duplicate package record")
            result[key] = fields
    return result


def _package_document(
    info_stems: Iterable[str], *, expected_uid: int | None
) -> list[Mapping[str, object]]:
    status = _read_dpkg_status()
    result: list[Mapping[str, object]] = []
    for info_stem in sorted(set(info_stems)):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9+.-]*(?::[A-Za-z0-9][A-Za-z0-9_-]*)?", info_stem):
            raise RuntimeAttestationError("dpkg info stem is unsafe")
        if ":" in info_stem:
            package_name, architecture = info_stem.rsplit(":", 1)
            fields = status.get((package_name, architecture))
        else:
            package_name = info_stem
            matches = [
                fields
                for (name, _), fields in status.items()
                if name == package_name and fields.get("Status") == "install ok installed"
            ]
            if len(matches) != 1:
                raise RuntimeAttestationError("dpkg package identity is ambiguous")
            fields = matches[0]
            architecture = fields["Architecture"]
        if fields is None or fields.get("Status") != "install ok installed":
            raise RuntimeAttestationError("system runtime dpkg package is not installed")
        version = fields.get("Version")
        if not version or "\x00" in version:
            raise RuntimeAttestationError("system runtime dpkg package version is unsafe")
        list_path = Path("/var/lib/dpkg/info") / f"{info_stem}.list"
        list_sha256, _ = _stable_hash_file(
            list_path,
            expected_uid=0 if expected_uid is not None else None,
            require_single_link=True,
            subject="dpkg package ownership list",
        )
        md5_path = Path("/var/lib/dpkg/info") / f"{info_stem}.md5sums"
        if md5_path.exists():
            md5_raw, md5_sha256 = _stable_read_runtime_file(
                md5_path,
                expected_uid=0 if expected_uid is not None else None,
                maximum_bytes=64 * 1024 * 1024,
                subject="dpkg package checksum list",
            )
            verified_paths: list[str] = []
            for raw_line in md5_raw.decode("utf-8").splitlines():
                match = re.fullmatch(r"([0-9a-f]{32})  ([^\x00\r\n]+)", raw_line)
                if match is None:
                    raise RuntimeAttestationError("dpkg package checksum entry is invalid")
                relative = PurePosixPath(match.group(2))
                if (
                    relative.is_absolute()
                    or not relative.parts
                    or any(part in {"", ".", ".."} for part in relative.parts)
                ):
                    raise RuntimeAttestationError("dpkg package checksum path is unsafe")
                target = Path("/").joinpath(*relative.parts)
                try:
                    descriptor = os.open(target, _safe_open_flags())
                except OSError as exc:
                    raise RuntimeAttestationError("dpkg-owned file is missing") from exc
                digest = hashlib.md5(usedforsecurity=False)
                try:
                    before = os.fstat(descriptor)
                    if not stat.S_ISREG(before.st_mode):
                        raise RuntimeAttestationError("dpkg-owned path is not a regular file")
                    _safe_metadata(
                        before,
                        expected_uid=0 if expected_uid is not None else None,
                        subject="dpkg-owned file",
                    )
                    while True:
                        chunk = os.read(descriptor, 1024 * 1024)
                        if not chunk:
                            break
                        digest.update(chunk)
                    _assert_stable_metadata(
                        before, os.fstat(descriptor), subject="dpkg-owned file"
                    )
                finally:
                    os.close(descriptor)
                if digest.hexdigest() != match.group(1):
                    raise RuntimeAttestationError("dpkg-owned file checksum mismatch")
                verified_paths.append(relative.as_posix())
            md5_verified_file_count = len(verified_paths)
            md5_verified_paths_sha256 = _canonical_json_sha256(verified_paths)
        else:
            md5_sha256 = None
            md5_verified_file_count = 0
            md5_verified_paths_sha256 = _canonical_json_sha256([])
        result.append(
            {
                "architecture": architecture,
                "info_stem": info_stem,
                "list_sha256": list_sha256,
                "md5sums_sha256": md5_sha256,
                "md5_verified_file_count": md5_verified_file_count,
                "md5_verified_paths_sha256": md5_verified_paths_sha256,
                "name": package_name,
                "status": fields["Status"],
                "version": version,
            }
        )
    return result


def _elf_closure_document(
    initial_paths: Iterable[Path],
    *,
    architecture: str,
    owners: Mapping[str, str],
    expected_uid: int | None,
) -> tuple[list[Mapping[str, object]], set[str]]:
    pending = [path.resolve(strict=True) for path in initial_paths]
    observed: dict[Path, Mapping[str, object]] = {}
    packages: set[str] = set()
    while pending:
        path = pending.pop()
        if path in observed:
            continue
        value, digest = _read_elf_bytes(path, expected_uid=expected_uid)
        identity = _parse_elf_identity(value)
        owner = _package_owner(path, owners)
        packages.add(owner)
        item: Mapping[str, object] = {
            "interpreter": identity.interpreter,
            "needed": list(identity.needed),
            "package": owner,
            "path": path.as_posix(),
            "sha256": digest,
            "soname": identity.soname,
        }
        observed[path] = item
        if identity.interpreter is not None:
            try:
                loader = Path(identity.interpreter).resolve(strict=True)
            except (OSError, RuntimeError) as exc:
                raise RuntimeAttestationError("cannot resolve the ELF interpreter") from exc
            pending.append(loader)
        pending.extend(_actual_elf_dependencies(path, identity, architecture))
    documents = [observed[path] for path in sorted(observed, key=os.fspath)]
    sonames: dict[str, str] = {}
    for item in documents:
        identity_name = item["soname"] or Path(str(item["path"])).name
        if identity_name in sonames and sonames[identity_name] != item["path"]:
            raise RuntimeAttestationError("ELF closure contains an ambiguous SONAME")
        sonames[str(identity_name)] = str(item["path"])
    for item in documents:
        for needed in item["needed"]:
            if needed not in sonames:
                raise RuntimeAttestationError("ELF dependency closure is incomplete")
    return documents, packages


def _parse_os_release(value: bytes) -> Mapping[str, str]:
    try:
        decoded = value.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RuntimeAttestationError("os-release is not UTF-8") from exc
    fields: dict[str, str] = {}
    for line in decoded.splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, raw = line.split("=", 1)
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key) or key in fields:
            raise RuntimeAttestationError("os-release contains unsafe metadata")
        try:
            parsed = shlex.split(raw, posix=True)
        except ValueError as exc:
            raise RuntimeAttestationError("os-release contains malformed quoting") from exc
        if len(parsed) != 1:
            raise RuntimeAttestationError("os-release contains ambiguous metadata")
        fields[key] = parsed[0]
    return fields


def _loader_configuration_identity(*, expected_uid: int | None) -> tuple[str, int]:
    paths = [Path("/etc/ld.so.conf")]
    directory = Path("/etc/ld.so.conf.d")
    try:
        paths.extend(sorted(directory.glob("*"), key=os.fspath))
    except OSError as exc:
        raise RuntimeAttestationError("cannot enumerate dynamic-loader configuration") from exc
    records: list[Mapping[str, object]] = []
    for path in paths:
        try:
            metadata = path.lstat()
        except OSError as exc:
            raise RuntimeAttestationError("cannot inspect dynamic-loader configuration") from exc
        if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            raise RuntimeAttestationError("dynamic-loader configuration has an unsafe file type")
        digest, size = _stable_hash_file(
            path,
            expected_uid=expected_uid,
            require_single_link=True,
            subject="dynamic-loader configuration",
        )
        records.append(
            {
                "mode": stat.S_IMODE(metadata.st_mode),
                "path": path.as_posix(),
                "sha256": digest,
                "size": size,
                "uid": metadata.st_uid,
            }
        )
    return _canonical_json_sha256(records), len(records)


def observe_system_runtime_manifest(
    *,
    stdlib_path: Path | None = None,
    expected_uid: int | None = 0,
    require_stdlib_package_ownership: bool = True,
    wheelhouse: Path | None = None,
    additional_elf_paths: Iterable[Path] | None = None,
) -> dict[str, object]:
    identity = _runtime_identity()
    if identity.implementation != EXPECTED_IMPLEMENTATION:
        raise RuntimeAttestationError("system runtime implementation is not CPython")
    if (identity.major, identity.minor) != EXPECTED_PYTHON_MAJOR_MINOR:
        raise RuntimeAttestationError("system runtime must be CPython 3.12")
    architecture = os.uname().machine
    if architecture != "x86_64":
        raise RuntimeAttestationError("system runtime architecture is unsupported")
    executable = Path(getattr(sys, "_base_executable", sys.executable)).resolve(strict=True)
    executable = _canonical_absolute_path(executable, subject="system CPython executable")
    executable_sha256, _ = _stable_hash_file(
        executable,
        expected_uid=expected_uid,
        require_single_link=False,
        require_executable=True,
        subject="system CPython executable",
    )
    expected_stdlib = Path(sysconfig.get_path("stdlib")).resolve(strict=True)
    inactive_zip = Path(f"/usr/lib/python{identity.major}{identity.minor}.zip")
    expected_python_path = [
        inactive_zip.as_posix(),
        expected_stdlib.as_posix(),
        (expected_stdlib / "lib-dynload").as_posix(),
    ]
    stdlib = (stdlib_path or expected_stdlib).resolve(strict=True)
    if stdlib_path is None and stdlib != expected_stdlib:
        raise RuntimeAttestationError("system standard-library path is unexpected")
    stdlib = _canonical_absolute_path(stdlib, subject="system standard-library path")
    tree = _scan_system_tree(stdlib, expected_uid=expected_uid)

    if wheelhouse is not None and additional_elf_paths is not None:
        raise RuntimeAttestationError(
            "wheelhouse and explicit venv ELF system roots are mutually exclusive"
        )
    if wheelhouse is not None:
        venv_elf_system_roots = _wheelhouse_system_elf_roots(
            wheelhouse, architecture=architecture
        )
    else:
        venv_elf_system_roots = tuple(
            sorted(
                {
                    _canonical_absolute_path(
                        Path(path), subject="venv ELF system dependency"
                    )
                    for path in (additional_elf_paths or ())
                },
                key=os.fspath,
            )
        )
    owners = _read_dpkg_owner_inventory()
    initial_elf = {executable, *tree.elf_paths, *venv_elf_system_roots}
    elf_objects, elf_packages = _elf_closure_document(
        initial_elf,
        architecture=architecture,
        owners=owners,
        expected_uid=expected_uid,
    )
    stdlib_packages: set[str] = set()
    if require_stdlib_package_ownership:
        for item in tree.document:
            if item["kind"] == "directory" or item["path"] == ".":
                continue
            stdlib_packages.add(
                _package_owner(stdlib / str(item["path"]), owners)
            )
        for item in tree.external_files:
            stdlib_packages.add(_package_owner(Path(item["path"]), owners))
    package_stems = elf_packages | stdlib_packages
    packages = _package_document(package_stems, expected_uid=expected_uid)

    os_alias = Path("/etc/os-release")
    try:
        alias_metadata = os_alias.lstat()
        alias_target = os.readlink(os_alias)
        os_release_path = os_alias.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise RuntimeAttestationError("cannot resolve os-release identity") from exc
    if not stat.S_ISLNK(alias_metadata.st_mode) or alias_metadata.st_uid != 0:
        raise RuntimeAttestationError("os-release alias is not a root-owned symlink")
    os_release, os_release_sha256 = _stable_read_runtime_file(
        os_release_path,
        expected_uid=expected_uid,
        maximum_bytes=1024 * 1024,
        subject="os-release identity",
    )
    os_fields = _parse_os_release(os_release)
    if os_fields.get("ID") != "ubuntu" or os_fields.get("VERSION_ID") != "24.04":
        raise RuntimeAttestationError("system runtime requires Ubuntu 24.04")

    cache_path = Path("/etc/ld.so.cache")
    cache_sha256, _ = _stable_hash_file(
        cache_path,
        expected_uid=expected_uid,
        require_single_link=True,
        subject="dynamic-loader cache",
    )
    configuration_sha256, configuration_count = _loader_configuration_identity(
        expected_uid=expected_uid
    )
    preload_path = Path("/etc/ld.so.preload")
    if preload_path.exists() or preload_path.is_symlink():
        raise RuntimeAttestationError("dynamic-loader preload configuration is forbidden")
    preload_sha256 = None

    return {
        "architecture": architecture,
        "elf_objects": elf_objects,
        "executable_path": executable.as_posix(),
        "executable_sha256": executable_sha256,
        "implementation": identity.public_implementation,
        "loader": {
            "cache_path": cache_path.as_posix(),
            "cache_sha256": cache_sha256,
            "configuration_entry_count": configuration_count,
            "configuration_sha256": configuration_sha256,
            "preload_path": preload_path.as_posix(),
            "preload_sha256": preload_sha256,
        },
        "os_release": {
            "alias_target": alias_target,
            "id": os_fields["ID"],
            "path": os_release_path.as_posix(),
            "sha256": os_release_sha256,
            "version_id": os_fields["VERSION_ID"],
        },
        "packages": packages,
        "python_version": identity.version,
        "schema_version": SYSTEM_RUNTIME_SCHEMA_VERSION,
        "stdlib": {
            "entry_count": tree.entry_count,
            "external_files": list(tree.external_files),
            "file_count": tree.file_count,
            "inactive_import_paths": [inactive_zip.as_posix()],
            "packages": sorted(stdlib_packages),
            "path": stdlib.as_posix(),
            "python_path": expected_python_path,
            "symlink_count": tree.symlink_count,
            "tree_sha256": tree.sha256,
        },
        "venv_elf_system_roots": [
            path.as_posix() for path in venv_elf_system_roots
        ],
    }


def _validate_observed_system_schema(document: Mapping[str, object]) -> None:
    _require_exact_fields(document, SYSTEM_MANIFEST_FIELDS, subject="system runtime manifest")
    for field, expected in (
        ("stdlib", SYSTEM_STDLIB_FIELDS),
        ("os_release", SYSTEM_OS_RELEASE_FIELDS),
        ("loader", SYSTEM_LOADER_FIELDS),
    ):
        value = document.get(field)
        if not isinstance(value, dict):
            raise RuntimeAttestationError(f"system runtime {field} must be an object")
        _require_exact_fields(value, expected, subject=f"system runtime {field}")
    external = document["stdlib"].get("external_files")
    elf_objects = document.get("elf_objects")
    packages = document.get("packages")
    venv_elf_system_roots = document.get("venv_elf_system_roots")
    if (
        not isinstance(external, list)
        or not isinstance(elf_objects, list)
        or not isinstance(packages, list)
        or not isinstance(venv_elf_system_roots, list)
        or any(not isinstance(path, str) for path in venv_elf_system_roots)
    ):
        raise RuntimeAttestationError("system runtime inventory fields must be arrays")
    for item in external:
        if not isinstance(item, dict):
            raise RuntimeAttestationError("system external file inventory is malformed")
        _require_exact_fields(item, SYSTEM_EXTERNAL_FILE_FIELDS, subject="system external file")
    for item in elf_objects:
        if not isinstance(item, dict):
            raise RuntimeAttestationError("system ELF inventory is malformed")
        _require_exact_fields(item, SYSTEM_ELF_FIELDS, subject="system ELF object")
    for item in packages:
        if not isinstance(item, dict):
            raise RuntimeAttestationError("system package inventory is malformed")
        _require_exact_fields(item, SYSTEM_PACKAGE_FIELDS, subject="system package")


def attest_system_runtime(
    manifest_path: Path,
    *,
    expected_manifest_sha256: str,
    expected_manifest_uid: int | None = None,
    expected_system_uid: int | None = 0,
    require_clean_startup: bool = False,
    stdlib_path: Path | None = None,
    require_stdlib_package_ownership: bool = True,
) -> dict[str, object]:
    if not SHA256_PATTERN.fullmatch(expected_manifest_sha256):
        raise RuntimeAttestationError(
            "expected system runtime manifest SHA-256 is malformed"
        )
    if require_clean_startup:
        _require_clean_system_startup()
    raw, manifest_sha256 = _read_secure_system_manifest(
        manifest_path, expected_uid=expected_manifest_uid
    )
    if not hmac.compare_digest(manifest_sha256, expected_manifest_sha256):
        raise RuntimeAttestationError(
            "system runtime manifest SHA-256 does not match its release binding"
        )
    document = _parse_system_manifest(raw)
    _validate_observed_system_schema(document)
    observed = observe_system_runtime_manifest(
        stdlib_path=stdlib_path,
        expected_uid=expected_system_uid,
        require_stdlib_package_ownership=require_stdlib_package_ownership,
        additional_elf_paths=(Path(path) for path in document["venv_elf_system_roots"]),
    )
    if document != observed:
        raise RuntimeAttestationError(
            "host CPython, stdlib, ELF closure, loader, or package identity drifted"
        )
    stdlib = observed["stdlib"]
    elf_objects = observed["elf_objects"]
    packages = observed["packages"]
    system_document = {
        "architecture": observed["architecture"],
        "elf_objects": elf_objects,
        "executable_sha256": observed["executable_sha256"],
        "loader": observed["loader"],
        "os_release": observed["os_release"],
        "packages": packages,
        "python_version": observed["python_version"],
        "stdlib": stdlib,
    }
    return {
        "system_elf_closure_sha256": _canonical_json_sha256(elf_objects),
        "system_elf_object_count": len(elf_objects),
        "system_os_release_sha256": observed["os_release"]["sha256"],
        "system_package_count": len(packages),
        "system_package_set_sha256": _canonical_json_sha256(packages),
        "system_runtime_attested": "yes",
        "system_runtime_manifest_sha256": manifest_sha256,
        "system_runtime_sha256": _canonical_json_sha256(system_document),
        "system_stdlib_entry_count": stdlib["entry_count"],
        "system_stdlib_tree_sha256": stdlib["tree_sha256"],
    }


def _validate_system_attestation_result(
    value: Mapping[str, object], *, allow_test_injection: bool
) -> dict[str, object]:
    _require_exact_fields(value, SYSTEM_ATTESTATION_FIELDS, subject="system attestation")
    if value.get("system_runtime_attested") != "yes":
        raise RuntimeAttestationError("system runtime was not attested")
    for field in (
        "system_elf_closure_sha256",
        "system_os_release_sha256",
        "system_package_set_sha256",
        "system_runtime_manifest_sha256",
        "system_runtime_sha256",
        "system_stdlib_tree_sha256",
    ):
        observed = value.get(field)
        if not isinstance(observed, str) or not SHA256_PATTERN.fullmatch(observed):
            raise RuntimeAttestationError(f"{field} is not a lowercase SHA-256")
    for field in (
        "system_elf_object_count",
        "system_package_count",
        "system_stdlib_entry_count",
    ):
        observed = value.get(field)
        if isinstance(observed, bool) or not isinstance(observed, int) or observed <= 0:
            raise RuntimeAttestationError(f"{field} is not a positive integer")
    if not allow_test_injection:
        raise RuntimeAttestationError("unverified system attestation injection is forbidden")
    return dict(value)


def _runtime_identity() -> RuntimeIdentity:
    return RuntimeIdentity(
        implementation=sys.implementation.name.lower(),
        major=sys.version_info.major,
        minor=sys.version_info.minor,
        micro=sys.version_info.micro,
        is_virtual_environment=sys.prefix != getattr(sys, "base_prefix", sys.prefix),
    )


def _normalize_package_name(value: str) -> str:
    if not PACKAGE_NAME_PATTERN.fullmatch(value):
        raise RuntimeAttestationError("package metadata contains an invalid package name")
    return NORMALIZE_NAME_PATTERN.sub("-", value).lower()


def _normalize_version(value: str, *, source: str) -> str:
    match = PEP440_PATTERN.fullmatch(value)
    if match is None:
        raise RuntimeAttestationError(f"{source} contains an invalid PEP 440 version")
    epoch = int(match.group("epoch") or "0")
    release = ".".join(str(int(part)) for part in match.group("release").split("."))
    normalized = f"{epoch}!{release}" if epoch else release

    if match.group("pre") is not None:
        raw_label = (match.group("pre_l") or "").lower()
        label = {
            "alpha": "a",
            "a": "a",
            "beta": "b",
            "b": "b",
            "preview": "rc",
            "pre": "rc",
            "c": "rc",
            "rc": "rc",
        }[raw_label]
        normalized += f"{label}{int(match.group('pre_n') or '0')}"
    if match.group("post") is not None:
        post_number = match.group("post_n1") or match.group("post_n2") or "0"
        normalized += f".post{int(post_number)}"
    if match.group("dev") is not None:
        normalized += f".dev{int(match.group('dev_n') or '0')}"
    if match.group("local") is not None:
        local_parts = re.split(r"[-_.]", match.group("local").lower())
        normalized_local = ".".join(
            str(int(part)) if part.isdigit() else part for part in local_parts
        )
        normalized += f"+{normalized_local}"
    return normalized


def _assert_stable_metadata(
    before: os.stat_result, after: os.stat_result, *, subject: str
) -> None:
    stable_before = (
        before.st_dev,
        before.st_ino,
        before.st_mode,
        before.st_nlink,
        before.st_uid,
        before.st_gid,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    )
    stable_after = (
        after.st_dev,
        after.st_ino,
        after.st_mode,
        after.st_nlink,
        after.st_uid,
        after.st_gid,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    )
    if stable_before != stable_after:
        raise RuntimeAttestationError(f"{subject} changed during attestation")


def _safe_open_flags() -> int:
    flags = os.O_RDONLY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    if hasattr(os, "O_NONBLOCK"):
        flags |= os.O_NONBLOCK
    return flags


def _read_lock_file(path: Path, *, expected_uid: int | None) -> bytes:
    try:
        descriptor = os.open(path, _safe_open_flags())
    except OSError as exc:
        raise RuntimeAttestationError(
            f"cannot safely open requirements lock: {path.name}"
        ) from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise RuntimeAttestationError(
                f"requirements lock is not a regular file: {path.name}"
            )
        if before.st_nlink != 1:
            raise RuntimeAttestationError(
                f"requirements lock must have exactly one hard link: {path.name}"
            )
        if expected_uid is not None and before.st_uid != expected_uid:
            raise RuntimeAttestationError(
                f"requirements lock has an unexpected owner: {path.name}"
            )
        if before.st_size > MAXIMUM_LOCK_BYTES:
            raise RuntimeAttestationError(
                f"requirements lock exceeds its safe size limit: {path.name}"
            )
        chunks: list[bytes] = []
        remaining = MAXIMUM_LOCK_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        value = b"".join(chunks)
        if len(value) > MAXIMUM_LOCK_BYTES:
            raise RuntimeAttestationError(
                f"requirements lock exceeds its safe size limit: {path.name}"
            )
        _assert_stable_metadata(
            before,
            os.fstat(descriptor),
            subject=f"requirements lock {path.name}",
        )
        return value
    except RuntimeAttestationError:
        raise
    except OSError as exc:
        raise RuntimeAttestationError(
            f"cannot safely read requirements lock: {path.name}"
        ) from exc
    finally:
        os.close(descriptor)


def _parse_lock(value: bytes) -> Mapping[str, str]:
    try:
        decoded = value.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RuntimeAttestationError("requirements lock is not valid UTF-8") from exc
    if "\r" in decoded or decoded.startswith("\ufeff"):
        raise RuntimeAttestationError("requirements lock is not canonical UTF-8 text")

    packages: dict[str, str] = {}
    for line_number, line in enumerate(decoded.split("\n"), start=1):
        if not line or line.startswith("#"):
            continue
        match = LOCK_ENTRY_PATTERN.fullmatch(line)
        if match is None:
            raise RuntimeAttestationError(
                f"requirements lock line {line_number} is not an exact name==version entry"
            )
        normalized_name = _normalize_package_name(match.group("name"))
        normalized_version = _normalize_version(
            match.group("version"), source=f"requirements lock line {line_number}"
        )
        if normalized_name in packages:
            raise RuntimeAttestationError(
                f"requirements lock contains a duplicate normalized package on line {line_number}"
            )
        packages[normalized_name] = normalized_version
    if not packages:
        raise RuntimeAttestationError("requirements lock contains no package entries")
    return packages


def load_lock(path: Path, *, expected_uid: int | None = None) -> LockInventory:
    if expected_uid is not None and expected_uid < 0:
        raise RuntimeAttestationError("expected lock owner uid must be non-negative")
    value = _read_lock_file(path, expected_uid=expected_uid)
    return LockInventory(
        packages=_parse_lock(value),
        sha256=hashlib.sha256(value).hexdigest(),
    )


def _safe_metadata(metadata: os.stat_result, *, expected_uid: int | None, subject: str) -> None:
    if expected_uid is not None and metadata.st_uid != expected_uid:
        raise RuntimeAttestationError(f"{subject} is not owned by the expected runtime owner")
    if metadata.st_mode & UNSAFE_WRITE_BITS:
        raise RuntimeAttestationError(f"{subject} is group- or world-writable")


def _resolve_runtime_prefix(path: Path, *, expected_uid: int | None) -> RuntimePrefix:
    alias = Path(os.path.abspath(os.fspath(path)))
    try:
        alias_metadata = alias.lstat()
        if not (stat.S_ISDIR(alias_metadata.st_mode) or stat.S_ISLNK(alias_metadata.st_mode)):
            raise RuntimeAttestationError("active runtime prefix is not a directory or directory alias")
        if expected_uid is not None and alias_metadata.st_uid != expected_uid:
            raise RuntimeAttestationError("active runtime prefix alias has an unexpected owner")
        resolved = alias.resolve(strict=True)
        resolved_metadata = resolved.lstat()
    except RuntimeAttestationError:
        raise
    except (OSError, RuntimeError) as exc:
        raise RuntimeAttestationError("cannot safely resolve the active runtime prefix") from exc
    if not stat.S_ISDIR(resolved_metadata.st_mode) or stat.S_ISLNK(resolved_metadata.st_mode):
        raise RuntimeAttestationError("resolved active runtime prefix is not a real directory")
    _safe_metadata(
        resolved_metadata,
        expected_uid=expected_uid,
        subject="resolved active runtime prefix",
    )
    return RuntimePrefix(alias=alias, resolved=resolved, alias_metadata=alias_metadata)


def _assert_runtime_prefix_stable(prefix: RuntimePrefix) -> None:
    try:
        after = prefix.alias.lstat()
        resolved_after = prefix.alias.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise RuntimeAttestationError("active runtime prefix changed during attestation") from exc
    _assert_stable_metadata(
        prefix.alias_metadata,
        after,
        subject="active runtime prefix alias",
    )
    if resolved_after != prefix.resolved:
        raise RuntimeAttestationError("active runtime prefix target changed during attestation")


def _relative_to_runtime_prefix(path: Path, prefix: RuntimePrefix) -> tuple[Path, str]:
    if not path.is_absolute():
        raise RuntimeAttestationError("installed runtime contains a non-absolute located path")
    normalized = Path(os.path.normpath(os.fspath(path)))
    relative: Path | None = None
    for base in (prefix.alias, prefix.resolved):
        try:
            relative = normalized.relative_to(base)
            break
        except ValueError:
            continue
    if relative is None or relative == Path("."):
        raise RuntimeAttestationError("installed runtime contains a path outside its active prefix")
    expected = prefix.resolved / relative
    try:
        observed = normalized.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise RuntimeAttestationError("cannot safely resolve an installed runtime path") from exc
    if observed != expected:
        raise RuntimeAttestationError(
            "installed runtime path uses a symlink or non-canonical in-prefix target"
        )
    try:
        canonical_relative = expected.relative_to(prefix.resolved).as_posix()
    except ValueError as exc:
        raise RuntimeAttestationError(
            "installed runtime contains a path outside its active prefix"
        ) from exc
    return expected, canonical_relative


def _assert_safe_directory_tree(
    directory: Path,
    prefix: RuntimePrefix,
    *,
    expected_uid: int | None,
    cache: set[Path],
) -> None:
    try:
        relative = directory.relative_to(prefix.resolved)
    except ValueError as exc:
        raise RuntimeAttestationError("installed runtime directory escaped its active prefix") from exc
    current = prefix.resolved
    candidates = [current]
    for part in relative.parts:
        current /= part
        candidates.append(current)
    for candidate in candidates:
        if candidate in cache:
            continue
        try:
            metadata = candidate.lstat()
        except OSError as exc:
            raise RuntimeAttestationError("cannot safely inspect an installed runtime directory") from exc
        if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            raise RuntimeAttestationError(
                "installed runtime path has a non-directory or symlinked parent"
            )
        _safe_metadata(
            metadata,
            expected_uid=expected_uid,
            subject="installed runtime directory",
        )
        cache.add(candidate)


def _stable_hash_file(
    path: Path,
    *,
    expected_uid: int | None,
    require_single_link: bool,
    require_executable: bool = False,
    subject: str,
) -> tuple[str, int]:
    try:
        descriptor = os.open(path, _safe_open_flags())
    except OSError as exc:
        raise RuntimeAttestationError(f"cannot safely open {subject}") from exc
    digest = hashlib.sha256()
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise RuntimeAttestationError(f"{subject} is not a regular file")
        if require_single_link and before.st_nlink != 1:
            raise RuntimeAttestationError(f"{subject} has more than one hard link")
        _safe_metadata(before, expected_uid=expected_uid, subject=subject)
        if before.st_mode & (stat.S_ISUID | stat.S_ISGID):
            raise RuntimeAttestationError(f"{subject} has unsafe privilege mode bits")
        if require_executable and not before.st_mode & (
            stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
        ):
            raise RuntimeAttestationError(f"{subject} is not executable")
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
        _assert_stable_metadata(before, os.fstat(descriptor), subject=subject)
        return digest.hexdigest(), before.st_size
    except RuntimeAttestationError:
        raise
    except OSError as exc:
        raise RuntimeAttestationError(f"cannot safely hash {subject}") from exc
    finally:
        os.close(descriptor)


def _attest_interpreter(
    path: Path,
    prefix: RuntimePrefix,
    *,
    expected_sha256: str | None,
    expected_uid: int | None,
    safe_directories: set[Path],
) -> InterpreterAttestation:
    alias = Path(os.path.normpath(os.path.abspath(os.fspath(path))))
    relative: Path | None = None
    for base in (prefix.alias, prefix.resolved):
        try:
            relative = alias.relative_to(base)
            break
        except ValueError:
            continue
    if relative is None or relative == Path("."):
        raise RuntimeAttestationError("active interpreter is outside the active runtime prefix")
    if relative.as_posix() != "bin/python":
        raise RuntimeAttestationError("runtime verifier must execute through venv bin/python")
    interpreter_alias = prefix.resolved / relative
    _assert_safe_directory_tree(
        interpreter_alias.parent,
        prefix,
        expected_uid=expected_uid,
        cache=safe_directories,
    )
    try:
        alias_before = alias.lstat()
        if not (stat.S_ISREG(alias_before.st_mode) or stat.S_ISLNK(alias_before.st_mode)):
            raise RuntimeAttestationError("active interpreter alias has an unsafe file type")
        if expected_uid is not None and alias_before.st_uid != expected_uid:
            raise RuntimeAttestationError("active interpreter alias has an unexpected owner")
        if stat.S_ISREG(alias_before.st_mode) and alias_before.st_mode & UNSAFE_EXECUTABLE_BITS:
            raise RuntimeAttestationError("active interpreter alias has unsafe mode bits")
        resolved = alias.resolve(strict=True)
    except RuntimeAttestationError:
        raise
    except (OSError, RuntimeError) as exc:
        raise RuntimeAttestationError("cannot safely resolve the active interpreter") from exc
    observed_sha256, _ = _stable_hash_file(
        resolved,
        expected_uid=expected_uid,
        require_single_link=False,
        require_executable=True,
        subject="resolved active interpreter",
    )
    try:
        alias_after = alias.lstat()
        resolved_after = alias.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise RuntimeAttestationError("active interpreter changed during attestation") from exc
    _assert_stable_metadata(
        alias_before,
        alias_after,
        subject="active interpreter alias",
    )
    if resolved_after != resolved:
        raise RuntimeAttestationError("active interpreter target changed during attestation")
    if expected_sha256 is not None and not hmac.compare_digest(
        observed_sha256, expected_sha256
    ):
        raise RuntimeAttestationError("active interpreter SHA-256 does not match its binding")
    return InterpreterAttestation(sha256=observed_sha256, target=resolved)


def _scan_runtime_tree(
    prefix: RuntimePrefix, *, expected_uid: int | None
) -> Mapping[str, ScannedNode]:
    nodes: dict[str, ScannedNode] = {}

    def walk(directory: Path) -> None:
        try:
            with os.scandir(directory) as iterator:
                entries = sorted(iterator, key=lambda item: item.name)
        except OSError as exc:
            raise RuntimeAttestationError("cannot safely enumerate the runtime tree") from exc
        for entry in entries:
            path = Path(entry.path)
            try:
                before = entry.stat(follow_symlinks=False)
            except OSError as exc:
                raise RuntimeAttestationError("cannot safely inspect a runtime tree entry") from exc
            try:
                relative = path.relative_to(prefix.resolved).as_posix()
            except ValueError as exc:
                raise RuntimeAttestationError("runtime tree enumeration escaped its prefix") from exc
            if not relative or relative in nodes:
                raise RuntimeAttestationError("runtime tree contains a duplicate or empty path")

            link_target: str | None = None
            if stat.S_ISDIR(before.st_mode):
                if path.name == "__pycache__":
                    raise RuntimeAttestationError("runtime tree contains a forbidden __pycache__ directory")
                _safe_metadata(
                    before,
                    expected_uid=expected_uid,
                    subject="runtime tree directory",
                )
                if before.st_mode & (stat.S_ISUID | stat.S_ISGID):
                    raise RuntimeAttestationError("runtime tree directory has unsafe privilege bits")
                kind = "directory"
            elif stat.S_ISREG(before.st_mode):
                if path.suffix.lower() in {".pyc", ".pyo"}:
                    raise RuntimeAttestationError("runtime tree contains forbidden Python bytecode")
                if path.suffix.lower() == ".pth" or path.name.lower() in {
                    "sitecustomize.py",
                    "usercustomize.py",
                }:
                    raise RuntimeAttestationError(
                        "runtime tree contains a forbidden startup customization"
                    )
                if before.st_nlink != 1:
                    raise RuntimeAttestationError("runtime tree file has more than one hard link")
                _safe_metadata(
                    before,
                    expected_uid=expected_uid,
                    subject="runtime tree file",
                )
                if before.st_mode & (stat.S_ISUID | stat.S_ISGID):
                    raise RuntimeAttestationError("runtime tree file has unsafe privilege bits")
                kind = "file"
            elif stat.S_ISLNK(before.st_mode):
                if expected_uid is not None and before.st_uid != expected_uid:
                    raise RuntimeAttestationError("runtime tree symlink has an unexpected owner")
                try:
                    link_target = os.readlink(path)
                    after = path.lstat()
                except OSError as exc:
                    raise RuntimeAttestationError("cannot safely read a runtime tree symlink") from exc
                _assert_stable_metadata(before, after, subject="runtime tree symlink")
                if not link_target or "\x00" in link_target:
                    raise RuntimeAttestationError("runtime tree symlink has an unsafe target")
                kind = "symlink"
            else:
                raise RuntimeAttestationError("runtime tree contains a forbidden special file")

            nodes[relative] = ScannedNode(
                path=relative,
                kind=kind,
                mode=stat.S_IMODE(before.st_mode),
                uid=before.st_uid,
                gid=before.st_gid,
                device=before.st_dev,
                inode=before.st_ino,
                links=before.st_nlink,
                size=before.st_size,
                mtime_ns=before.st_mtime_ns,
                ctime_ns=before.st_ctime_ns,
                link_target=link_target,
            )
            if kind == "directory":
                walk(path)

    walk(prefix.resolved)
    return nodes


def _stable_read_runtime_file(
    path: Path,
    *,
    expected_uid: int | None,
    maximum_bytes: int,
    subject: str,
) -> tuple[bytes, str]:
    try:
        descriptor = os.open(path, _safe_open_flags())
    except OSError as exc:
        raise RuntimeAttestationError(f"cannot safely open {subject}") from exc
    digest = hashlib.sha256()
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise RuntimeAttestationError(f"{subject} is not a regular file")
        if before.st_nlink != 1:
            raise RuntimeAttestationError(f"{subject} has more than one hard link")
        if before.st_size > maximum_bytes:
            raise RuntimeAttestationError(f"{subject} exceeds its safe size limit")
        _safe_metadata(before, expected_uid=expected_uid, subject=subject)
        if before.st_mode & (stat.S_ISUID | stat.S_ISGID):
            raise RuntimeAttestationError(f"{subject} has unsafe privilege mode bits")
        chunks: list[bytes] = []
        remaining = maximum_bytes + 1
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            digest.update(chunk)
            remaining -= len(chunk)
        value = b"".join(chunks)
        if len(value) > maximum_bytes:
            raise RuntimeAttestationError(f"{subject} exceeds its safe size limit")
        _assert_stable_metadata(before, os.fstat(descriptor), subject=subject)
        return value, digest.hexdigest()
    except RuntimeAttestationError:
        raise
    except OSError as exc:
        raise RuntimeAttestationError(f"cannot safely read {subject}") from exc
    finally:
        os.close(descriptor)


def _parse_pyvenv_configuration(value: bytes) -> Mapping[str, str]:
    try:
        decoded = value.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RuntimeAttestationError("pyvenv.cfg is not valid UTF-8") from exc
    if "\r" in decoded or decoded.startswith("\ufeff"):
        raise RuntimeAttestationError("pyvenv.cfg is not canonical UTF-8 text")
    result: dict[str, str] = {}
    for line in decoded.split("\n"):
        if not line:
            continue
        if " = " not in line:
            raise RuntimeAttestationError("pyvenv.cfg contains a malformed entry")
        key, entry_value = line.split(" = ", 1)
        if not re.fullmatch(r"[a-z][a-z0-9-]*", key) or not entry_value:
            raise RuntimeAttestationError("pyvenv.cfg contains a malformed entry")
        if key in result:
            raise RuntimeAttestationError("pyvenv.cfg contains a duplicate entry")
        result[key] = entry_value
    required = {
        "home",
        "include-system-site-packages",
        "version",
        "executable",
        "command",
    }
    if set(result) != required:
        raise RuntimeAttestationError("pyvenv.cfg does not have the exact CPython 3.12 schema")
    return result


def _attest_pyvenv_configuration(
    path: Path,
    prefix: RuntimePrefix,
    identity: RuntimeIdentity,
    interpreter: InterpreterAttestation,
    *,
    expected_uid: int | None,
) -> StructuralEntry:
    value, digest = _stable_read_runtime_file(
        path,
        expected_uid=expected_uid,
        maximum_bytes=64 * 1024,
        subject="pyvenv.cfg",
    )
    configuration = _parse_pyvenv_configuration(value)
    if configuration["include-system-site-packages"].lower() != "false":
        raise RuntimeAttestationError("pyvenv.cfg enables system site packages")
    if configuration["version"] != identity.version:
        raise RuntimeAttestationError("pyvenv.cfg version does not match active CPython")
    try:
        home = Path(configuration["home"])
        executable = Path(configuration["executable"])
        if not home.is_absolute() or not executable.is_absolute():
            raise RuntimeAttestationError("pyvenv.cfg contains a non-absolute runtime binding")
        home_target = home.resolve(strict=True)
        executable_target = executable.resolve(strict=True)
        home_metadata = home_target.lstat()
    except RuntimeAttestationError:
        raise
    except (OSError, RuntimeError) as exc:
        raise RuntimeAttestationError("cannot safely resolve pyvenv.cfg runtime bindings") from exc
    if home_target != interpreter.target.parent or executable_target != interpreter.target:
        raise RuntimeAttestationError("pyvenv.cfg does not bind the attested interpreter target")
    if not stat.S_ISDIR(home_metadata.st_mode) or stat.S_ISLNK(home_metadata.st_mode):
        raise RuntimeAttestationError("pyvenv.cfg home is not a real directory")
    _safe_metadata(
        home_metadata,
        expected_uid=expected_uid,
        subject="pyvenv.cfg home directory",
    )
    try:
        command = shlex.split(configuration["command"], posix=True)
    except ValueError as exc:
        raise RuntimeAttestationError("pyvenv.cfg command is malformed") from exc
    if (
        len(command) < 4
        or "--system-site-packages" in command
        or not any(command[index : index + 2] == ["-m", "venv"] for index in range(len(command) - 1))
    ):
        raise RuntimeAttestationError("pyvenv.cfg command is not a safe venv creation command")
    try:
        command_interpreter = Path(command[0]).resolve(strict=True)
        command_prefix = Path(command[-1]).resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise RuntimeAttestationError("cannot safely resolve pyvenv.cfg command bindings") from exc
    if command_interpreter != interpreter.target or command_prefix != prefix.resolved:
        raise RuntimeAttestationError("pyvenv.cfg command does not bind this runtime")
    return StructuralEntry(
        path="pyvenv.cfg",
        kind="file",
        sha256=digest,
        size=len(value),
    )


def _attest_runtime_symlink(
    path: Path,
    relative: str,
    *,
    expected_target: Path,
    expected_uid: int | None,
) -> StructuralEntry:
    try:
        before = path.lstat()
        if not stat.S_ISLNK(before.st_mode):
            raise RuntimeAttestationError("modeled runtime alias is not a symlink")
        if expected_uid is not None and before.st_uid != expected_uid:
            raise RuntimeAttestationError("modeled runtime alias has an unexpected owner")
        link_target = os.readlink(path)
        resolved = path.resolve(strict=True)
        after = path.lstat()
    except RuntimeAttestationError:
        raise
    except (OSError, RuntimeError) as exc:
        raise RuntimeAttestationError("cannot safely resolve a modeled runtime alias") from exc
    _assert_stable_metadata(before, after, subject="modeled runtime alias")
    if not link_target or resolved != expected_target:
        raise RuntimeAttestationError("modeled runtime alias has an unexpected target")
    encoded = link_target.encode("utf-8", errors="strict")
    return StructuralEntry(
        path=relative,
        kind="symlink",
        sha256=hashlib.sha256(b"symlink\x00" + encoded).hexdigest(),
        size=len(encoded),
        link_target=link_target,
    )


def _attest_runtime_structure(
    tree: Mapping[str, ScannedNode],
    prefix: RuntimePrefix,
    identity: RuntimeIdentity,
    interpreter: InterpreterAttestation,
    *,
    expected_uid: int | None,
) -> tuple[tuple[StructuralEntry, ...], set[str], Path]:
    required_files = {
        "bin/Activate.ps1",
        "bin/activate",
        "bin/activate.csh",
        "bin/activate.fish",
        "pyvenv.cfg",
    }
    launcher_paths = {
        "bin/python",
        "bin/python3",
        f"bin/python{identity.major}.{identity.minor}",
    }
    required_directories = {
        "bin",
        "lib",
        f"lib/python{identity.major}.{identity.minor}",
        f"lib/python{identity.major}.{identity.minor}/site-packages",
    }
    for relative in required_directories:
        node = tree.get(relative)
        if node is None or node.kind != "directory":
            raise RuntimeAttestationError("runtime tree is missing a required venv directory")

    entries: list[StructuralEntry] = []
    allowed = set(required_files) | set(launcher_paths)
    pyvenv_path = prefix.resolved / "pyvenv.cfg"
    if tree.get("pyvenv.cfg") is None or tree["pyvenv.cfg"].kind != "file":
        raise RuntimeAttestationError("runtime tree is missing regular pyvenv.cfg")
    entries.append(
        _attest_pyvenv_configuration(
            pyvenv_path,
            prefix,
            identity,
            interpreter,
            expected_uid=expected_uid,
        )
    )
    for relative in sorted(required_files - {"pyvenv.cfg"}):
        node = tree.get(relative)
        if node is None or node.kind != "file":
            raise RuntimeAttestationError("runtime tree is missing a required activation file")
        digest, size = _stable_hash_file(
            prefix.resolved / relative,
            expected_uid=expected_uid,
            require_single_link=True,
            subject="venv activation file",
        )
        entries.append(
            StructuralEntry(path=relative, kind="file", sha256=digest, size=size)
        )

    for relative in sorted(launcher_paths):
        node = tree.get(relative)
        if node is None or node.kind not in {"file", "symlink"}:
            raise RuntimeAttestationError("runtime tree is missing a required Python launcher")
        path = prefix.resolved / relative
        if node.kind == "symlink":
            entries.append(
                _attest_runtime_symlink(
                    path,
                    relative,
                    expected_target=interpreter.target,
                    expected_uid=expected_uid,
                )
            )
        else:
            digest, size = _stable_hash_file(
                path,
                expected_uid=expected_uid,
                require_single_link=True,
                require_executable=True,
                subject="venv Python launcher",
            )
            if not hmac.compare_digest(digest, interpreter.sha256):
                raise RuntimeAttestationError("venv Python launcher does not match the interpreter")
            entries.append(
                StructuralEntry(path=relative, kind="file", sha256=digest, size=size)
            )

    if "lib64" in tree:
        if tree["lib64"].kind != "symlink":
            raise RuntimeAttestationError("optional lib64 venv alias is not a symlink")
        allowed.add("lib64")
        entries.append(
            _attest_runtime_symlink(
                prefix.resolved / "lib64",
                "lib64",
                expected_target=prefix.resolved / "lib",
                expected_uid=expected_uid,
            )
        )
    site_packages = prefix.resolved / f"lib/python{identity.major}.{identity.minor}/site-packages"
    return tuple(sorted(entries, key=lambda item: item.path)), allowed, site_packages


def _close_runtime_tree(
    tree: Mapping[str, ScannedNode],
    installed: InstalledInventory,
    structural_paths: set[str],
) -> None:
    claimed = {
        item.path
        for package_files in installed.files.values()
        for item in package_files
    }
    overlap = claimed & structural_paths
    if overlap:
        raise RuntimeAttestationError("distribution RECORD claims a venv structural entry")
    non_directories = {
        path for path, node in tree.items() if node.kind != "directory"
    }
    missing = (claimed | structural_paths) - non_directories
    if missing:
        raise RuntimeAttestationError("runtime tree changed or is missing an attested entry")
    unclaimed = non_directories - claimed - structural_paths
    if unclaimed:
        raise RuntimeAttestationError(
            "runtime tree contains an unclaimed or unmodeled non-directory entry"
        )
    for path in claimed:
        if tree[path].kind != "file":
            raise RuntimeAttestationError("distribution RECORD claims a non-regular runtime entry")


def _distribution_name(distribution: object) -> str:
    try:
        metadata = distribution.metadata  # type: ignore[attr-defined]
        name = metadata.get("Name")
    except Exception as exc:
        raise RuntimeAttestationError("cannot safely read installed package metadata") from exc
    if not isinstance(name, str):
        raise RuntimeAttestationError("installed package metadata is missing its package name")
    return _normalize_package_name(name)


def _distribution_version(distribution: object) -> str:
    try:
        value = distribution.version  # type: ignore[attr-defined]
    except Exception as exc:
        raise RuntimeAttestationError("cannot safely read installed package version metadata") from exc
    if not isinstance(value, str) or not value or value != value.strip():
        raise RuntimeAttestationError("installed package metadata contains an invalid version")
    return _normalize_version(value, source="installed package metadata")


def _canonical_record_path(value: object) -> str:
    raw = str(value)
    if not raw or "\\" in raw or "\x00" in raw:
        raise RuntimeAttestationError("installed distribution contains an unsafe RECORD path")
    pure = PurePosixPath(raw)
    if pure.is_absolute() or pure.as_posix() != raw or raw == ".":
        raise RuntimeAttestationError("installed distribution contains a non-canonical RECORD path")
    seen_normal = False
    for part in pure.parts:
        if part == ".":
            raise RuntimeAttestationError(
                "installed distribution contains a non-canonical RECORD path"
            )
        if part == "..":
            if seen_normal:
                raise RuntimeAttestationError(
                    "installed distribution contains a non-canonical RECORD traversal"
                )
        else:
            seen_normal = True
    if not seen_normal:
        raise RuntimeAttestationError("installed distribution contains an unsafe RECORD path")
    return raw


def _distribution_metadata_root(
    distribution: object,
    prefix: RuntimePrefix,
    *,
    expected_uid: int | None,
    safe_directories: set[Path],
) -> Path:
    raw = getattr(distribution, "_path", None)
    if not isinstance(raw, (str, os.PathLike)):
        raise RuntimeAttestationError("installed distribution has no canonical metadata root")
    root, _ = _relative_to_runtime_prefix(Path(raw), prefix)
    if not root.name.endswith(".dist-info"):
        raise RuntimeAttestationError("installed distribution metadata is not wheel dist-info")
    _assert_safe_directory_tree(
        root,
        prefix,
        expected_uid=expected_uid,
        cache=safe_directories,
    )
    return root


def _declared_record_values(entry: object) -> tuple[object | None, int | None]:
    try:
        declared_hash = entry.hash  # type: ignore[attr-defined]
        declared_size = entry.size  # type: ignore[attr-defined]
    except Exception as exc:
        raise RuntimeAttestationError("cannot safely read installed RECORD metadata") from exc
    if declared_size is not None and (
        isinstance(declared_size, bool) or not isinstance(declared_size, int) or declared_size < 0
    ):
        raise RuntimeAttestationError("installed RECORD contains an invalid declared size")
    return declared_hash, declared_size


def _verify_record_hash(declared_hash: object, observed_sha256: str) -> None:
    try:
        mode = declared_hash.mode  # type: ignore[attr-defined]
        value = declared_hash.value  # type: ignore[attr-defined]
    except Exception as exc:
        raise RuntimeAttestationError("installed RECORD contains an invalid declared hash") from exc
    if mode != "sha256" or not isinstance(value, str) or not RECORD_SHA256_PATTERN.fullmatch(value):
        raise RuntimeAttestationError("installed RECORD does not contain a valid SHA-256 hash")
    encoded = base64.urlsafe_b64encode(bytes.fromhex(observed_sha256)).rstrip(b"=").decode("ascii")
    if not hmac.compare_digest(encoded, value):
        raise RuntimeAttestationError("installed file does not match its declared RECORD hash")


def _attest_distribution_files(
    distribution: object,
    package_name: str,
    prefix: RuntimePrefix,
    *,
    expected_uid: int | None,
    safe_directories: set[Path],
    claimed_paths: set[str],
) -> tuple[AttestedFile, ...]:
    metadata_root = _distribution_metadata_root(
        distribution,
        prefix,
        expected_uid=expected_uid,
        safe_directories=safe_directories,
    )
    own_record = metadata_root / "RECORD"
    try:
        raw_files = distribution.files  # type: ignore[attr-defined]
        if raw_files is None:
            raise RuntimeAttestationError(
                "installed distribution has no complete RECORD file inventory"
            )
        entries = list(raw_files)
    except RuntimeAttestationError:
        raise
    except Exception as exc:
        raise RuntimeAttestationError("cannot safely enumerate installed RECORD entries") from exc
    if not entries:
        raise RuntimeAttestationError("installed distribution has an empty RECORD inventory")

    attested: list[AttestedFile] = []
    record_entries = 0
    for entry in entries:
        _canonical_record_path(entry)
        try:
            located = Path(distribution.locate_file(entry))  # type: ignore[attr-defined]
        except Exception as exc:
            raise RuntimeAttestationError("cannot safely locate an installed RECORD entry") from exc
        file_path, relative = _relative_to_runtime_prefix(located, prefix)
        if relative in claimed_paths:
            raise RuntimeAttestationError(
                "multiple installed distributions claim the same runtime file"
            )
        claimed_paths.add(relative)
        _assert_safe_directory_tree(
            file_path.parent,
            prefix,
            expected_uid=expected_uid,
            cache=safe_directories,
        )
        is_own_record = file_path == own_record
        if is_own_record:
            record_entries += 1
        declared_hash, declared_size = _declared_record_values(entry)
        if not is_own_record and (declared_hash is None or declared_size is None):
            raise RuntimeAttestationError(
                "installed RECORD has an unhashed or unsized non-RECORD entry"
            )
        observed_sha256, observed_size = _stable_hash_file(
            file_path,
            expected_uid=expected_uid,
            require_single_link=True,
            subject=f"installed file for {package_name}",
        )
        if declared_size is not None and observed_size != declared_size:
            raise RuntimeAttestationError("installed file does not match its declared RECORD size")
        if declared_hash is not None:
            _verify_record_hash(declared_hash, observed_sha256)
        attested.append(
            AttestedFile(path=relative, sha256=observed_sha256, size=observed_size)
        )
    if record_entries != 1:
        raise RuntimeAttestationError(
            "installed distribution RECORD must declare exactly its own RECORD entry"
        )
    return tuple(sorted(attested, key=lambda item: item.path))


def collect_installed(
    distributions: Iterable[object],
    prefix: RuntimePrefix,
    *,
    expected_uid: int | None,
    safe_directories: set[Path],
) -> InstalledInventory:
    packages: dict[str, str] = {}
    files: dict[str, tuple[AttestedFile, ...]] = {}
    claimed_paths: set[str] = set()
    try:
        for distribution in distributions:
            name = _distribution_name(distribution)
            version = _distribution_version(distribution)
            if name in packages:
                raise RuntimeAttestationError(
                    "installed runtime contains duplicate normalized package metadata"
                )
            packages[name] = version
            files[name] = _attest_distribution_files(
                distribution,
                name,
                prefix,
                expected_uid=expected_uid,
                safe_directories=safe_directories,
                claimed_paths=claimed_paths,
            )
    except RuntimeAttestationError:
        raise
    except Exception as exc:
        raise RuntimeAttestationError(
            "cannot safely enumerate installed package metadata"
        ) from exc
    return InstalledInventory(packages=packages, files=files)


def _short_package_list(values: Iterable[str]) -> str:
    ordered = sorted(values)
    visible = ordered[:5]
    suffix = "" if len(ordered) <= len(visible) else f" (and {len(ordered) - len(visible)} more)"
    return ", ".join(visible) + suffix


def _normalize_bootstrap_bindings(values: Mapping[str, str] | None) -> Mapping[str, str]:
    result: dict[str, str] = {}
    for raw_name, raw_version in (values or {}).items():
        if not isinstance(raw_name, str) or not isinstance(raw_version, str):
            raise RuntimeAttestationError("bootstrap bindings must use package name and version strings")
        name = _normalize_package_name(raw_name)
        if name not in BOOTSTRAP_EXTRAS:
            raise RuntimeAttestationError("bootstrap binding names a package outside the allowlist")
        if name in result:
            raise RuntimeAttestationError("bootstrap bindings contain a duplicate normalized package")
        result[name] = _normalize_version(raw_version, source="bootstrap binding")
    return result


def parse_bootstrap_bindings(values: Iterable[str]) -> Mapping[str, str]:
    result: dict[str, str] = {}
    for value in values:
        match = LOCK_ENTRY_PATTERN.fullmatch(value)
        if match is None:
            raise RuntimeAttestationError(
                "bootstrap binding is not an exact name==version entry"
            )
        name = _normalize_package_name(match.group("name"))
        if name not in BOOTSTRAP_EXTRAS:
            raise RuntimeAttestationError("bootstrap binding names a package outside the allowlist")
        if name in result:
            raise RuntimeAttestationError("bootstrap bindings contain a duplicate normalized package")
        result[name] = _normalize_version(match.group("version"), source="bootstrap binding")
    return result


def _attest_package_inventory(
    expected: Mapping[str, str],
    installed: Mapping[str, str],
    expected_bootstrap: Mapping[str, str],
) -> set[str]:
    expected_names = set(expected)
    installed_names = set(installed)
    overlap = expected_names & set(expected_bootstrap)
    if overlap:
        raise RuntimeAttestationError("a locked package was also bound as a bootstrap extra")
    missing = expected_names - installed_names
    if missing:
        raise RuntimeAttestationError(
            f"installed runtime is missing locked packages: {_short_package_list(missing)}"
        )
    observed_bootstrap = installed_names - expected_names
    disallowed_extra = observed_bootstrap - BOOTSTRAP_EXTRAS
    if disallowed_extra:
        raise RuntimeAttestationError(
            "installed runtime contains packages outside the exact lock: "
            f"{_short_package_list(disallowed_extra)}"
        )
    unbound = observed_bootstrap - set(expected_bootstrap)
    if unbound:
        raise RuntimeAttestationError(
            f"installed runtime contains unbound bootstrap packages: {_short_package_list(unbound)}"
        )
    absent_binding = set(expected_bootstrap) - observed_bootstrap
    if absent_binding:
        raise RuntimeAttestationError(
            f"bound bootstrap packages are not installed: {_short_package_list(absent_binding)}"
        )
    drifted = [
        name for name in sorted(expected_names) if installed[name] != expected[name]
    ]
    bootstrap_drift = [
        name
        for name in sorted(observed_bootstrap)
        if installed[name] != expected_bootstrap[name]
    ]
    if drifted:
        raise RuntimeAttestationError(
            f"installed runtime contains locked version drift: {_short_package_list(drifted)}"
        )
    if bootstrap_drift:
        raise RuntimeAttestationError(
            "installed runtime contains bootstrap version drift: "
            f"{_short_package_list(bootstrap_drift)}"
        )
    return observed_bootstrap


def _files_document(inventory: InstalledInventory) -> list[dict[str, object]]:
    return [
        {
            "files": [
                {"path": item.path, "sha256": item.sha256, "size": item.size}
                for item in inventory.files[name]
            ],
            "name": name,
            "version": inventory.packages[name],
        }
        for name in sorted(inventory.packages)
    ]


def _structure_document(entries: Iterable[StructuralEntry]) -> list[dict[str, object]]:
    return [
        {
            "kind": entry.kind,
            "link_target": entry.link_target,
            "path": entry.path,
            "sha256": entry.sha256,
            "size": entry.size,
        }
        for entry in sorted(entries, key=lambda item: item.path)
    ]


def _tree_document(tree: Mapping[str, ScannedNode]) -> list[dict[str, object]]:
    return [
        {
            "gid": node.gid,
            "kind": node.kind,
            "link_target": node.link_target,
            "mode": node.mode,
            "path": node.path,
            "uid": node.uid,
        }
        for node in sorted(tree.values(), key=lambda item: item.path)
    ]


def _verified_system_elf_objects(
    manifest_path: Path, *, expected_sha256: str, expected_uid: int | None
) -> tuple[Mapping[str, object], ...]:
    raw, observed_sha256 = _read_secure_system_manifest(
        manifest_path, expected_uid=expected_uid
    )
    if not hmac.compare_digest(observed_sha256, expected_sha256):
        raise RuntimeAttestationError(
            "system runtime manifest changed after host attestation"
        )
    document = _parse_system_manifest(raw)
    _validate_observed_system_schema(document)
    return tuple(document["elf_objects"])


def _attest_venv_elf_closure(
    tree: Mapping[str, ScannedNode],
    prefix: RuntimePrefix,
    system_objects: Iterable[Mapping[str, object]],
    *,
    expected_uid: int | None,
) -> tuple[list[Mapping[str, object]], str]:
    system_names: dict[str, str] = {}
    system_paths: set[Path] = set()
    for item in system_objects:
        path = Path(str(item.get("path", "")))
        soname = item.get("soname")
        name = str(soname) if isinstance(soname, str) and soname else path.name
        if not name or name in system_names:
            raise RuntimeAttestationError("system ELF identity map is ambiguous")
        system_names[name] = path.as_posix()
        system_paths.add(path)

    documents: list[Mapping[str, object]] = []
    venv_names: dict[str, str] = {}
    for relative, node in sorted(tree.items()):
        if node.kind != "file":
            continue
        path = prefix.resolved / relative
        if not _looks_like_elf(path):
            continue
        value, digest = _read_elf_bytes(path, expected_uid=expected_uid)
        identity = _parse_elf_identity(value)
        name = identity.soname or path.name
        if name in venv_names or name in system_names:
            raise RuntimeAttestationError("venv ELF closure contains an ambiguous SONAME")
        venv_names[name] = relative
        documents.append(
            {
                "interpreter": identity.interpreter,
                "needed": list(identity.needed),
                "path": relative,
                "sha256": digest,
                "soname": identity.soname,
            }
        )
    provided = set(system_names) | set(venv_names)
    for item in documents:
        interpreter = item["interpreter"]
        if interpreter is not None:
            try:
                resolved_interpreter = Path(str(interpreter)).resolve(strict=True)
            except (OSError, RuntimeError) as exc:
                raise RuntimeAttestationError("venv ELF interpreter cannot be resolved") from exc
            if resolved_interpreter not in system_paths:
                raise RuntimeAttestationError(
                    "venv ELF interpreter is outside the system runtime manifest"
                )
        for needed in item["needed"]:
            if needed not in provided:
                raise RuntimeAttestationError(
                    "venv ELF dependency is outside the closed system/venv inventory"
                )
    ordered = sorted(documents, key=lambda item: str(item["path"]))
    return ordered, _canonical_json_sha256(ordered)


def _canonical_json_sha256(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=True, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validate_python_binding(
    expected_version: str | None, expected_sha256: str | None
) -> None:
    if expected_version is not None and not PYTHON_VERSION_PATTERN.fullmatch(expected_version):
        raise RuntimeAttestationError("expected Python version must be an exact X.Y.Z value")
    if expected_sha256 is not None and not SHA256_PATTERN.fullmatch(expected_sha256):
        raise RuntimeAttestationError(
            "expected interpreter SHA-256 must be 64 lowercase hexadecimal characters"
        )


def _has_isolated_no_site_startup() -> bool:
    return bool(sys.flags.isolated and sys.flags.no_site)


def attest_runtime(
    lock_path: Path,
    *,
    system_runtime_manifest: Path | None = None,
    expected_system_runtime_manifest_sha256: str | None = None,
    system_runtime_attestation: Mapping[str, object] | None = None,
    expected_lock_uid: int | None = None,
    expected_python_version: str | None = None,
    expected_python_sha256: str | None = None,
    expected_bootstrap: Mapping[str, str] | None = None,
    distributions: Iterable[object] | None = None,
    identity: RuntimeIdentity | None = None,
    runtime_prefix: Path | None = None,
    interpreter_path: Path | None = None,
    expected_runtime_uid: int | None = None,
    require_virtual_environment: bool = False,
    require_isolated_startup: bool = False,
) -> dict[str, object]:
    if system_runtime_attestation is None:
        if (
            system_runtime_manifest is None
            or expected_system_runtime_manifest_sha256 is None
        ):
            raise RuntimeAttestationError(
                "release-bound system runtime manifest is required"
            )
        system_attestation = attest_system_runtime(
            system_runtime_manifest,
            expected_manifest_sha256=expected_system_runtime_manifest_sha256,
            expected_manifest_uid=expected_lock_uid,
            expected_system_uid=expected_runtime_uid,
            require_clean_startup=require_isolated_startup,
        )
        system_elf_objects = _verified_system_elf_objects(
            system_runtime_manifest,
            expected_sha256=expected_system_runtime_manifest_sha256,
            expected_uid=expected_lock_uid,
        )
    else:
        # Unit-level inventory fixtures may inject an already-verified system
        # result only while both the runtime identity and distributions are
        # explicitly controlled.  The production CLI has no injection path.
        system_attestation = _validate_system_attestation_result(
            system_runtime_attestation,
            allow_test_injection=identity is not None and distributions is not None,
        )
        system_elf_objects = ()
    observed_identity = identity or _runtime_identity()
    if observed_identity.implementation != EXPECTED_IMPLEMENTATION:
        raise RuntimeAttestationError("Writer Witness requires the CPython implementation")
    if (observed_identity.major, observed_identity.minor) != EXPECTED_PYTHON_MAJOR_MINOR:
        raise RuntimeAttestationError("Writer Witness requires CPython 3.12 exactly")
    _validate_python_binding(expected_python_version, expected_python_sha256)
    if expected_python_version is not None and observed_identity.version != expected_python_version:
        raise RuntimeAttestationError("active CPython full version does not match its binding")
    if require_virtual_environment and not observed_identity.is_virtual_environment:
        raise RuntimeAttestationError("Writer Witness runtime is not an active virtual environment")
    if require_isolated_startup and not _has_isolated_no_site_startup():
        raise RuntimeAttestationError(
            "runtime verifier requires -I -S -B -X utf8 -X pycache_prefix=/dev/null"
        )
    if expected_runtime_uid is not None and expected_runtime_uid < 0:
        raise RuntimeAttestationError("expected runtime owner uid must be non-negative")

    lock = load_lock(lock_path, expected_uid=expected_lock_uid)
    bootstrap_bindings = _normalize_bootstrap_bindings(expected_bootstrap)
    prefix = _resolve_runtime_prefix(
        runtime_prefix or Path(sys.prefix), expected_uid=expected_runtime_uid
    )
    tree_before = _scan_runtime_tree(prefix, expected_uid=expected_runtime_uid)
    venv_elf_document, venv_elf_sha256 = _attest_venv_elf_closure(
        tree_before,
        prefix,
        system_elf_objects,
        expected_uid=expected_runtime_uid,
    )
    safe_directories: set[Path] = set()
    interpreter = _attest_interpreter(
        interpreter_path or Path(sys.executable),
        prefix,
        expected_sha256=expected_python_sha256,
        expected_uid=expected_runtime_uid,
        safe_directories=safe_directories,
    )
    structure, structural_paths, site_packages = _attest_runtime_structure(
        tree_before,
        prefix,
        observed_identity,
        interpreter,
        expected_uid=expected_runtime_uid,
    )
    try:
        observed_distributions = (
            importlib_metadata.distributions(path=[str(site_packages)])
            if distributions is None
            else distributions
        )
    except Exception as exc:
        raise RuntimeAttestationError(
            "cannot safely enumerate installed package metadata"
        ) from exc
    installed = collect_installed(
        observed_distributions,
        prefix,
        expected_uid=expected_runtime_uid,
        safe_directories=safe_directories,
    )
    bootstrap_extras = _attest_package_inventory(
        lock.packages, installed.packages, bootstrap_bindings
    )
    _close_runtime_tree(tree_before, installed, structural_paths)
    tree_after = _scan_runtime_tree(prefix, expected_uid=expected_runtime_uid)
    if tree_after != tree_before:
        raise RuntimeAttestationError("runtime tree changed during attestation")
    _assert_runtime_prefix_stable(prefix)

    packages_document = _files_document(installed)
    structure_document = _structure_document(structure)
    tree_document = _tree_document(tree_before)
    installed_files_sha256 = _canonical_json_sha256(packages_document)
    structure_sha256 = _canonical_json_sha256(structure_document)
    tree_sha256 = _canonical_json_sha256(tree_document)
    runtime_document = {
        "implementation": observed_identity.public_implementation,
        "interpreter_sha256": interpreter.sha256,
        "packages": packages_document,
        "python_version": observed_identity.version,
        "structure": structure_document,
        "system_runtime": system_attestation,
        "tree_sha256": tree_sha256,
        "venv_elf": venv_elf_document,
    }
    return {
        "bootstrap_extra_count": len(bootstrap_extras),
        "implementation": observed_identity.public_implementation,
        "installed_file_count": installed.file_count,
        "installed_files_sha256": installed_files_sha256,
        "installed_package_count": len(installed.packages),
        "lock_package_count": len(lock.packages),
        "python_sha256": interpreter.sha256,
        "python_version": observed_identity.version,
        "requirements_lock_sha256": lock.sha256,
        "runtime_attested": "yes",
        "runtime_structure_count": len(structure),
        "runtime_structure_sha256": structure_sha256,
        "runtime_tree_entry_count": len(tree_before),
        "runtime_tree_sha256": tree_sha256,
        "runtime_sha256": _canonical_json_sha256(runtime_document),
        "venv_elf_closure_sha256": venv_elf_sha256,
        "venv_elf_object_count": len(venv_elf_document),
        **system_attestation,
    }


def _non_negative_uid(value: str) -> int:
    try:
        parsed = int(value, 10)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("uid must be a non-negative integer") from exc
    if parsed < 0:
        raise argparse.ArgumentTypeError("uid must be a non-negative integer")
    return parsed


def _exact_python_version(value: str) -> str:
    if not PYTHON_VERSION_PATTERN.fullmatch(value):
        raise argparse.ArgumentTypeError("version must be an exact X.Y.Z value")
    return value


def _sha256(value: str) -> str:
    if not SHA256_PATTERN.fullmatch(value):
        raise argparse.ArgumentTypeError("SHA-256 must be 64 lowercase hexadecimal characters")
    return value


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--system-only",
        action="store_true",
        help="Attest only the release-bound host CPython/OS runtime before venv bootstrap",
    )
    mode.add_argument(
        "--emit-system-runtime-manifest",
        action="store_true",
        help="Emit a manifest from an approved clean release-build host",
    )
    parser.add_argument("--system-runtime-manifest", type=Path)
    parser.add_argument("--wheelhouse", type=Path)
    parser.add_argument(
        "--expected-system-runtime-manifest-sha256", type=_sha256
    )
    parser.add_argument("--requirements-lock", type=Path)
    parser.add_argument("--runtime-prefix", type=Path)
    parser.add_argument("--expected-python-version", type=_exact_python_version)
    parser.add_argument("--expected-python-sha256", type=_sha256)
    parser.add_argument(
        "--expected-bootstrap",
        action="append",
        default=[],
        metavar="NAME==VERSION",
        help="Repeat for each observed pip/setuptools/wheel bootstrap package",
    )
    parser.add_argument(
        "--expected-lock-uid",
        type=_non_negative_uid,
        default=0,
        help="Require the safely opened lock to be owned by this uid (use 0 in production)",
    )
    args = parser.parse_args(argv)
    if args.emit_system_runtime_manifest:
        if args.wheelhouse is None:
            parser.error("--wheelhouse is required for manifest emission")
        forbidden = (
            args.system_runtime_manifest,
            args.expected_system_runtime_manifest_sha256,
            args.requirements_lock,
            args.runtime_prefix,
            args.expected_python_version,
            args.expected_python_sha256,
            *args.expected_bootstrap,
        )
        if any(value is not None and value != "" for value in forbidden):
            parser.error("manifest emission cannot accept attestation inputs")
        return args
    if args.wheelhouse is not None:
        parser.error("--wheelhouse is accepted only for manifest emission")
    if args.system_runtime_manifest is None:
        parser.error("--system-runtime-manifest is required")
    if args.expected_system_runtime_manifest_sha256 is None:
        parser.error("--expected-system-runtime-manifest-sha256 is required")
    if args.system_only:
        forbidden = (
            args.requirements_lock,
            args.runtime_prefix,
            args.expected_python_version,
            args.expected_python_sha256,
            *args.expected_bootstrap,
        )
        if any(value is not None and value != "" for value in forbidden):
            parser.error("--system-only cannot accept venv attestation inputs")
        return args
    for option, value in (
        ("--requirements-lock", args.requirements_lock),
        ("--runtime-prefix", args.runtime_prefix),
        ("--expected-python-version", args.expected_python_version),
        ("--expected-python-sha256", args.expected_python_sha256),
    ):
        if value is None:
            parser.error(f"{option} is required")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.emit_system_runtime_manifest:
            _require_clean_system_startup()
            result = observe_system_runtime_manifest(wheelhouse=args.wheelhouse)
            print(
                json.dumps(
                    result, ensure_ascii=True, indent=2, sort_keys=True
                )
            )
            return 0
        if args.system_only:
            result = attest_system_runtime(
                args.system_runtime_manifest,
                expected_manifest_sha256=args.expected_system_runtime_manifest_sha256,
                expected_manifest_uid=args.expected_lock_uid,
                expected_system_uid=0,
                require_clean_startup=True,
            )
            print(
                json.dumps(
                    result, ensure_ascii=True, separators=(",", ":"), sort_keys=True
                )
            )
            return 0
        bootstrap_bindings = parse_bootstrap_bindings(args.expected_bootstrap)
        result = attest_runtime(
            args.requirements_lock,
            system_runtime_manifest=args.system_runtime_manifest,
            expected_system_runtime_manifest_sha256=(
                args.expected_system_runtime_manifest_sha256
            ),
            expected_lock_uid=args.expected_lock_uid,
            expected_python_version=args.expected_python_version,
            expected_python_sha256=args.expected_python_sha256,
            expected_bootstrap=bootstrap_bindings,
            runtime_prefix=args.runtime_prefix,
            expected_runtime_uid=0,
            require_isolated_startup=True,
        )
    except RuntimeAttestationError as exc:
        print(f"Writer Witness runtime attestation failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=True, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
