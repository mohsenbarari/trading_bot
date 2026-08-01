#!/usr/bin/env python3
"""Prepare one deterministic, local-only WebApp-FI static-assets archive.

This helper is deliberately the *producer* half of the WebApp-FI static
asset hand-off.  It reads only a root-only detached runtime source and creates
one fresh root-only candidate containing a USTAR archive of ``mini_app_dist``,
its canonical file manifest, and a URL-free preparation receipt.  It does not
contact Object Storage, invoke age, SSH, Docker, or a service, and it neither
loads images nor changes a release, ``current``, volume, data, or runtime
configuration.

The archive intentionally contains only sorted relative regular-file members
below ``mini_app_dist``.  It has no wrapper directory, PAX/GNU extension,
directory entry, symlink, special file, or embedded mutable manifest.  Its
members use exactly the metadata accepted by
``adopt_webapp_fi_static_assets.py``.  A separately authorised publication
step must bind this archive to one immutable Object Storage version before the
controller adoption helper can use it.

Both planning and preparation re-hash the root-controlled source.  Before an
``--apply`` candidate is created, the command reserves enough free space for
the exact USTAR upper bound, canonical manifest, receipt reserve, and margin.
If a source drift or another failure occurs after candidate creation, that
fresh root-only candidate is intentionally retained for inspection and is
never overwritten or cleaned up by a retry.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tarfile
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence


STATIC_ASSET_FILE_MANIFEST_SCHEMA = "gold-trade-webapp-fi-static-assets-file-manifest-v1"
STATIC_ASSET_PREPARATION_RECEIPT_SCHEMA = "gold-trade-webapp-fi-static-assets-preparation-v1"
STATIC_ARCHIVE_NAME = "mini_app_dist.tar"
STATIC_FILE_MANIFEST_NAME = "mini-app-dist-files.json"
STATIC_PREPARATION_RECEIPT_NAME = "mini-app-dist-preparation-receipt.json"
RUNTIME_STATIC_ASSET_RELATIVE = "mini_app_dist"
FI_RUNTIME_SOURCE_ROOT = PurePosixPath("/srv/trading-bot/current")
STATIC_PREPARER_MEMBER = PurePosixPath("scripts/prepare_webapp_fi_static_assets.py")
EXPECTED_STATIC_ASSETS_MEMBER = PurePosixPath("config/expected-static-assets.json")
EXPECTED_STATIC_ASSETS_SCHEMA = "gold-trade-webapp-fi-expected-static-assets-v2"

# These limits and the tar member contract intentionally match the controller
# adoption primitive.  Keeping the producer conservative lets the later
# controller verifier reject nothing that this helper emits.
MAX_STATIC_ARCHIVE_BYTES = 100 * 1024 * 1024
MAX_STATIC_FILE_BYTES = 100 * 1024 * 1024
MAX_STATIC_FILES = 100_000
MAX_STATIC_PATH_BYTES = 512
MAX_FILE_MANIFEST_BYTES = 64 * 1024 * 1024
MAX_EXPECTED_STATIC_ASSETS_MANIFEST_BYTES = 8 * 1024 * 1024
MAX_INSTALLED_HELPER_BYTES = 8 * 1024 * 1024
RECEIPT_RESERVE_BYTES = 1024 * 1024
CAPACITY_MARGIN_BYTES = 4 * 1024 * 1024

CAMPAIGN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,127}$")
RELEASE_RE = re.compile(r"^[0-9a-f]{40}$")
ALEMBIC_RE = re.compile(r"^[0-9a-f]{12}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class StaticAssetPreparationError(RuntimeError):
    """The detached WebApp-FI static-assets source cannot be prepared safely."""


@dataclass(frozen=True)
class SourceFile:
    """One source file, including enough state to detect a concurrent change."""

    path: str
    sha256: str
    bytes: int
    device: int
    inode: int
    mode: int
    mtime_ns: int
    ctime_ns: int

    def public(self) -> dict[str, Any]:
        return {"path": self.path, "sha256": self.sha256, "bytes": self.bytes}


DiskUsage = Callable[[Path], Any]


class _StaticTarInfo(tarfile.TarInfo):
    """Reject non-USTAR extension headers before tarfile reads their payload."""

    def _proc_member(self, archive: tarfile.TarFile) -> tarfile.TarInfo:
        if self.type in {
            tarfile.GNUTYPE_LONGNAME,
            tarfile.GNUTYPE_LONGLINK,
            tarfile.GNUTYPE_SPARSE,
            tarfile.XHDTYPE,
            tarfile.XGLTYPE,
            tarfile.SOLARIS_XHDTYPE,
        }:
            raise tarfile.ReadError("static asset archives must not contain extended tar headers")
        return super()._proc_member(archive)


class _HashingReader:
    """Small ``TarFile.addfile`` reader which proves exactly what was copied."""

    def __init__(self, handle: Any, *, maximum_bytes: int) -> None:
        self._handle = handle
        self._maximum_bytes = maximum_bytes
        self._digest = hashlib.sha256()
        self.bytes = 0

    @property
    def sha256(self) -> str:
        return self._digest.hexdigest()

    def read(self, size: int = -1) -> bytes:
        value = self._handle.read(size)
        self.bytes += len(value)
        if self.bytes > self._maximum_bytes:
            raise StaticAssetPreparationError("static asset source file exceeds its size bound while copying")
        self._digest.update(value)
        return value


def canonical_json_bytes(value: Mapping[str, Any] | Sequence[Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _require_root_execution() -> None:
    if os.geteuid() != 0:
        raise StaticAssetPreparationError("static asset preparation must run as root")


def _require_absolute(path: Path, *, field: str) -> Path:
    if not isinstance(path, Path) or not path.is_absolute():
        raise StaticAssetPreparationError(f"{field} must be an absolute path")
    return path


def _require_safe_directory_ancestors(path: Path, *, field: str) -> None:
    """Require root-controlled, non-symlink ancestors.

    A root-owned sticky directory such as ``/tmp`` is safe for a root-owned
    child: another user cannot replace that child.  Other writable ancestors
    are rejected because they could swap a path between validation and use.
    """

    path = _require_absolute(path, field=field)
    current = Path(path.anchor)
    for component in path.parts[1:]:
        current = current / component
        try:
            metadata = current.lstat()
        except OSError as exc:
            raise StaticAssetPreparationError(f"{field} ancestor does not exist") from exc
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != 0
        ):
            raise StaticAssetPreparationError(f"{field} has an unsafe ancestor")
        if stat.S_IMODE(metadata.st_mode) & 0o022 and not metadata.st_mode & stat.S_ISVTX:
            raise StaticAssetPreparationError(f"{field} has a writable non-sticky ancestor")


def _require_root_only_directory(path: Path, *, field: str) -> Path:
    path = _require_absolute(path, field=field)
    _require_safe_directory_ancestors(path, field=field)
    try:
        metadata = path.lstat()
        resolved = path.resolve(strict=True)
        resolved_metadata = resolved.lstat()
    except OSError as exc:
        raise StaticAssetPreparationError(f"cannot inspect {field}") from exc
    if (
        resolved != path
        or stat.S_ISLNK(metadata.st_mode)
        or stat.S_ISLNK(resolved_metadata.st_mode)
        or not stat.S_ISDIR(resolved_metadata.st_mode)
        or resolved_metadata.st_uid != 0
        or stat.S_IMODE(resolved_metadata.st_mode) & 0o077
    ):
        raise StaticAssetPreparationError(f"{field} must be one root-only non-symlink directory")
    return resolved


def _require_root_controlled_directory(path: Path, *, field: str) -> Path:
    """Accept a static directory that is root-owned but need not be private."""

    path = _require_absolute(path, field=field)
    _require_safe_directory_ancestors(path.parent, field=field)
    try:
        metadata = path.lstat()
        resolved = path.resolve(strict=True)
        resolved_metadata = resolved.lstat()
    except OSError as exc:
        raise StaticAssetPreparationError(f"cannot inspect {field}") from exc
    if (
        resolved != path
        or stat.S_ISLNK(metadata.st_mode)
        or stat.S_ISLNK(resolved_metadata.st_mode)
        or not stat.S_ISDIR(resolved_metadata.st_mode)
        or resolved_metadata.st_uid != 0
        or stat.S_IMODE(resolved_metadata.st_mode) & (0o022 | 0o7000)
    ):
        raise StaticAssetPreparationError(f"{field} must be root-owned and not group/other writable")
    return resolved


def _require_private_file(path: Path, *, field: str, maximum_bytes: int) -> Path:
    path = _require_absolute(path, field=field)
    _require_root_only_directory(path.parent, field=f"{field} parent")
    try:
        metadata = path.lstat()
        resolved = path.resolve(strict=True)
        resolved_metadata = resolved.lstat()
    except OSError as exc:
        raise StaticAssetPreparationError(f"cannot inspect {field}") from exc
    if (
        resolved != path
        or stat.S_ISLNK(metadata.st_mode)
        or stat.S_ISLNK(resolved_metadata.st_mode)
        or not stat.S_ISREG(resolved_metadata.st_mode)
        or resolved_metadata.st_uid != 0
        or stat.S_IMODE(resolved_metadata.st_mode) & 0o077
        or resolved_metadata.st_nlink != 1
        or not 1 <= resolved_metadata.st_size <= maximum_bytes
    ):
        raise StaticAssetPreparationError(f"{field} must be a bounded root-only non-symlink file")
    return resolved


def _safe_asset_path(value: object) -> str:
    if not isinstance(value, str) or not value or len(value.encode("utf-8")) > MAX_STATIC_PATH_BYTES:
        raise StaticAssetPreparationError("static asset path is invalid")
    if any(ord(character) < 0x20 or ord(character) > 0x7E for character in value):
        raise StaticAssetPreparationError("static asset path must be printable ASCII")
    pure = PurePosixPath(value)
    if (
        pure.as_posix() != value
        or pure.is_absolute()
        or "\\" in value
        or any(part in {"", ".", ".."} for part in pure.parts)
    ):
        raise StaticAssetPreparationError("static asset path is invalid")
    return value


def _require_ustar_member_path(path: str) -> None:
    """Prove a path fits USTAR before creating the candidate."""

    try:
        info = tarfile.TarInfo(path)
        info.size = 0
        info.mode = 0o644
        info.uid = 0
        info.gid = 0
        info.uname = ""
        info.gname = ""
        info.mtime = 0
        info.tobuf(format=tarfile.USTAR_FORMAT, encoding="ascii", errors="strict")
    except (UnicodeError, ValueError) as exc:
        raise StaticAssetPreparationError("static asset path cannot be represented in USTAR") from exc


def _validate_source_file_state(metadata: os.stat_result, *, field: str) -> None:
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) & (0o022 | 0o7000)
        or metadata.st_nlink != 1
        or metadata.st_size < 0
        or metadata.st_size > MAX_STATIC_FILE_BYTES
    ):
        raise StaticAssetPreparationError(f"{field} is not a bounded root-controlled regular file")


def _hash_source_file(path: Path, *, relative: str) -> SourceFile:
    """Hash one source file via ``O_NOFOLLOW`` and detect an in-read change."""

    try:
        before = path.lstat()
    except OSError as exc:
        raise StaticAssetPreparationError("cannot inspect static asset source file") from exc
    _validate_source_file_state(before, field="static asset source file")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise StaticAssetPreparationError("cannot securely open static asset source file") from exc
    try:
        opened = os.fstat(descriptor)
        _validate_source_file_state(opened, field="static asset source file")
        if (
            opened.st_dev != before.st_dev
            or opened.st_ino != before.st_ino
            or opened.st_size != before.st_size
            or opened.st_mtime_ns != before.st_mtime_ns
            or opened.st_ctime_ns != before.st_ctime_ns
        ):
            raise StaticAssetPreparationError("static asset source file changed while being opened")
        digest = hashlib.sha256()
        bytes_value = 0
        while chunk := os.read(descriptor, 1024 * 1024):
            bytes_value += len(chunk)
            if bytes_value > MAX_STATIC_FILE_BYTES:
                raise StaticAssetPreparationError("static asset source file exceeds its size bound while hashing")
            digest.update(chunk)
        after = os.fstat(descriptor)
        if (
            after.st_dev != opened.st_dev
            or after.st_ino != opened.st_ino
            or after.st_size != opened.st_size
            or after.st_mtime_ns != opened.st_mtime_ns
            or after.st_ctime_ns != opened.st_ctime_ns
            or bytes_value != opened.st_size
        ):
            raise StaticAssetPreparationError("static asset source file changed while being hashed")
    except OSError as exc:
        raise StaticAssetPreparationError("cannot read static asset source file") from exc
    finally:
        os.close(descriptor)
    return SourceFile(
        path=relative,
        sha256=digest.hexdigest(),
        bytes=bytes_value,
        device=opened.st_dev,
        inode=opened.st_ino,
        mode=stat.S_IMODE(opened.st_mode),
        mtime_ns=opened.st_mtime_ns,
        ctime_ns=opened.st_ctime_ns,
    )


def _scan_static_source(static_root: Path) -> tuple[SourceFile, ...]:
    """Read a sorted snapshot of a root-controlled static tree only."""

    static_root = _require_root_controlled_directory(static_root, field="mini_app_dist source")
    files: list[SourceFile] = []

    def visit(directory: Path, prefix: str) -> None:
        try:
            state = directory.lstat()
        except OSError as exc:
            raise StaticAssetPreparationError("cannot inspect static asset source directory") from exc
        if (
            stat.S_ISLNK(state.st_mode)
            or not stat.S_ISDIR(state.st_mode)
            or state.st_uid != 0
            or stat.S_IMODE(state.st_mode) & (0o022 | 0o7000)
        ):
            raise StaticAssetPreparationError("static asset source directory is not root-controlled")
        try:
            with os.scandir(directory) as scanner:
                entries = sorted(scanner, key=lambda item: item.name)
        except OSError as exc:
            raise StaticAssetPreparationError("cannot enumerate static asset source directory") from exc
        for entry in entries:
            relative = _safe_asset_path(entry.name if not prefix else prefix + "/" + entry.name)
            path = directory / entry.name
            try:
                metadata = path.lstat()
            except OSError as exc:
                raise StaticAssetPreparationError("cannot inspect static asset source entry") from exc
            if stat.S_ISDIR(metadata.st_mode):
                visit(path, relative)
            elif stat.S_ISREG(metadata.st_mode):
                _require_ustar_member_path(relative)
                files.append(_hash_source_file(path, relative=relative))
            else:
                raise StaticAssetPreparationError("static asset source may contain only directories and regular files")

    visit(static_root, "")
    if not files:
        raise StaticAssetPreparationError("mini_app_dist source must contain at least one file")
    if len(files) > MAX_STATIC_FILES:
        raise StaticAssetPreparationError("mini_app_dist source has too many files")
    ordered = tuple(sorted(files, key=lambda item: item.path))
    if tuple(item.path for item in ordered) != tuple(item.path for item in files):
        raise StaticAssetPreparationError("static asset source enumeration is not deterministic")
    return ordered


def _public_files(files: Sequence[SourceFile]) -> list[dict[str, Any]]:
    return [item.public() for item in files]


def _files_sha256(files: Sequence[Mapping[str, Any]]) -> str:
    return sha256_bytes(canonical_json_bytes(list(files)))


def _validate_identity(*, campaign_id: str, application: Mapping[str, str]) -> tuple[str, dict[str, str]]:
    if not isinstance(campaign_id, str) or not CAMPAIGN_ID_RE.fullmatch(campaign_id):
        raise StaticAssetPreparationError("campaign_id is invalid")
    if not isinstance(application, Mapping) or set(application) != {"release_sha", "expected_alembic_revision"}:
        raise StaticAssetPreparationError("application is invalid")
    release_sha = application.get("release_sha")
    revision = application.get("expected_alembic_revision")
    if not isinstance(release_sha, str) or not RELEASE_RE.fullmatch(release_sha):
        raise StaticAssetPreparationError("application release_sha is invalid")
    if not isinstance(revision, str) or not ALEMBIC_RE.fullmatch(revision):
        raise StaticAssetPreparationError("application expected_alembic_revision is invalid")
    return campaign_id, {"release_sha": release_sha, "expected_alembic_revision": revision}


def _file_manifest(
    *,
    campaign_id: str,
    application: Mapping[str, str],
    files: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    file_values = list(files)
    return {
        "schema": STATIC_ASSET_FILE_MANIFEST_SCHEMA,
        "status": "prepared",
        "campaign_id": campaign_id,
        "application": dict(application),
        "source_site": "webapp_fi",
        "artifact": STATIC_ARCHIVE_NAME,
        "files": file_values,
        "files_sha256": _files_sha256(file_values),
    }


def _archive_upper_bound(files: Sequence[SourceFile]) -> int:
    """Exact output bound for Python's uncompressed USTAR writer."""

    raw_bytes = 0
    for item in files:
        raw_bytes += tarfile.BLOCKSIZE + ((item.bytes + tarfile.BLOCKSIZE - 1) // tarfile.BLOCKSIZE) * tarfile.BLOCKSIZE
    raw_bytes += tarfile.BLOCKSIZE * 2
    # The standard writer pads the final two zero blocks only to the next tar
    # record boundary; an already aligned archive needs no extra record.
    return raw_bytes + (-raw_bytes % tarfile.RECORDSIZE)


def _capacity_preflight(
    *,
    output_parent: Path,
    archive_upper_bound_bytes: int,
    file_manifest_bytes: int,
    source_bytes: int,
    file_count: int,
    disk_usage: DiskUsage,
) -> dict[str, int]:
    if archive_upper_bound_bytes < 1 or archive_upper_bound_bytes > MAX_STATIC_ARCHIVE_BYTES:
        raise StaticAssetPreparationError("deterministic static archive exceeds its configured size bound")
    if file_manifest_bytes < 1 or file_manifest_bytes > MAX_FILE_MANIFEST_BYTES:
        raise StaticAssetPreparationError("static asset file manifest exceeds its configured size bound")
    try:
        free_bytes = disk_usage(output_parent).free
    except Exception as exc:
        raise StaticAssetPreparationError("cannot inspect static asset output capacity") from exc
    if isinstance(free_bytes, bool) or not isinstance(free_bytes, int) or free_bytes < 0:
        raise StaticAssetPreparationError("static asset output capacity is invalid")
    required_free_bytes = (
        archive_upper_bound_bytes + file_manifest_bytes + RECEIPT_RESERVE_BYTES + CAPACITY_MARGIN_BYTES
    )
    if free_bytes < required_free_bytes:
        raise StaticAssetPreparationError("insufficient free space for a new static asset candidate")
    return {
        "archive_upper_bound_bytes": archive_upper_bound_bytes,
        "file_manifest_bytes": file_manifest_bytes,
        "source_bytes": source_bytes,
        "file_count": file_count,
        "receipt_reserve_bytes": RECEIPT_RESERVE_BYTES,
        "margin_bytes": CAPACITY_MARGIN_BYTES,
        "required_free_bytes": required_free_bytes,
        "available_free_bytes": free_bytes,
    }


def _new_private_file(path: Path, *, field: str) -> Any:
    if path.exists() or path.is_symlink():
        raise StaticAssetPreparationError(f"refusing to overwrite {field}")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
        os.fchmod(descriptor, 0o600)
        return os.fdopen(descriptor, "wb")
    except OSError as exc:
        raise StaticAssetPreparationError(f"cannot create {field}") from exc


def _write_new_private_json(path: Path, value: Mapping[str, Any], *, field: str) -> bytes:
    payload = canonical_json_bytes(value) + b"\n"
    _reject_persisted_url(payload, field=field)
    try:
        with _new_private_file(path, field=field) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        raise StaticAssetPreparationError(f"cannot write {field}") from exc
    return payload


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0))
    except OSError as exc:
        raise StaticAssetPreparationError("cannot synchronize static asset candidate directory") from exc
    try:
        os.fsync(descriptor)
    except OSError as exc:
        raise StaticAssetPreparationError("cannot synchronize static asset candidate directory") from exc
    finally:
        os.close(descriptor)


def _copy_source_file_to_tar(archive: tarfile.TarFile, *, static_root: Path, item: SourceFile) -> None:
    path = static_root / item.path
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        before = path.lstat()
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise StaticAssetPreparationError("cannot securely open static asset source while archiving") from exc
    try:
        opened = os.fstat(descriptor)
        _validate_source_file_state(opened, field="static asset source file")
        if (
            opened.st_dev != item.device
            or opened.st_ino != item.inode
            or opened.st_size != item.bytes
            or stat.S_IMODE(opened.st_mode) != item.mode
            or opened.st_mtime_ns != item.mtime_ns
            or opened.st_ctime_ns != item.ctime_ns
            or before.st_dev != item.device
            or before.st_ino != item.inode
        ):
            raise StaticAssetPreparationError("static asset source file changed before archiving")
        info = tarfile.TarInfo(item.path)
        info.size = item.bytes
        info.mode = 0o644
        info.uid = 0
        info.gid = 0
        info.uname = ""
        info.gname = ""
        info.mtime = 0
        with os.fdopen(descriptor, "rb", closefd=False) as source:
            reader = _HashingReader(source, maximum_bytes=item.bytes)
            archive.addfile(info, reader)
        after = os.fstat(descriptor)
        if (
            after.st_dev != opened.st_dev
            or after.st_ino != opened.st_ino
            or after.st_size != opened.st_size
            or after.st_mtime_ns != opened.st_mtime_ns
            or after.st_ctime_ns != opened.st_ctime_ns
            or reader.bytes != item.bytes
            or reader.sha256 != item.sha256
        ):
            raise StaticAssetPreparationError("static asset source file changed while being archived")
    except (OSError, tarfile.TarError) as exc:
        raise StaticAssetPreparationError("cannot archive static asset source file") from exc
    finally:
        os.close(descriptor)


def _write_ustar_archive(*, archive_path: Path, static_root: Path, files: Sequence[SourceFile]) -> tuple[str, int]:
    """Create the exact archive accepted by the controller adopter."""

    try:
        with _new_private_file(archive_path, field="static asset USTAR archive") as output:
            with tarfile.open(
                fileobj=output,
                mode="w",
                format=tarfile.USTAR_FORMAT,
                encoding="ascii",
                errors="strict",
            ) as archive:
                for item in files:
                    _copy_source_file_to_tar(archive, static_root=static_root, item=item)
            output.flush()
            os.fsync(output.fileno())
    except (OSError, tarfile.TarError) as exc:
        raise StaticAssetPreparationError("cannot create deterministic static asset USTAR archive") from exc
    archive_path = _require_private_file(
        archive_path,
        field="prepared static asset USTAR archive",
        maximum_bytes=MAX_STATIC_ARCHIVE_BYTES,
    )
    return sha256_file(archive_path)


def _member_payload(archive: tarfile.TarFile, member: tarfile.TarInfo) -> tuple[str, int]:
    if (
        not member.isreg()
        or member.issparse()
        or member.size < 0
        or member.size > MAX_STATIC_FILE_BYTES
        or member.mode != 0o644
        or member.uid != 0
        or member.gid != 0
        or member.uname
        or member.gname
        or member.mtime != 0
        or member.pax_headers
        or member.linkname
    ):
        raise StaticAssetPreparationError("prepared static asset archive member is not deterministic")
    payload = archive.extractfile(member)
    if payload is None:
        raise StaticAssetPreparationError("prepared static asset archive member cannot be read")
    digest = hashlib.sha256()
    bytes_value = 0
    try:
        while chunk := payload.read(1024 * 1024):
            bytes_value += len(chunk)
            if bytes_value > member.size or bytes_value > MAX_STATIC_FILE_BYTES:
                raise StaticAssetPreparationError("prepared static asset archive member exceeds its bound")
            digest.update(chunk)
    finally:
        payload.close()
    if bytes_value != member.size:
        raise StaticAssetPreparationError("prepared static asset archive member is truncated")
    return digest.hexdigest(), bytes_value


def inspect_prepared_static_archive(archive_path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Independently re-parse an emitted archive using the consumer contract."""

    archive_path = _require_private_file(
        archive_path,
        field="prepared static asset USTAR archive",
        maximum_bytes=MAX_STATIC_ARCHIVE_BYTES,
    )
    before = sha256_file(archive_path)
    files: list[dict[str, Any]] = []
    prior = ""
    try:
        with tarfile.open(archive_path, "r|", tarinfo=_StaticTarInfo) as archive:
            while (member := archive.next()) is not None:
                if len(files) >= MAX_STATIC_FILES:
                    raise StaticAssetPreparationError("prepared static asset archive has too many members")
                path = _safe_asset_path(member.name)
                if prior and path <= prior:
                    raise StaticAssetPreparationError(
                        "prepared static asset archive paths are not deterministically sorted"
                    )
                prior = path
                digest, bytes_value = _member_payload(archive, member)
                files.append({"path": path, "sha256": digest, "bytes": bytes_value})
    except (OSError, tarfile.TarError) as exc:
        raise StaticAssetPreparationError("prepared static asset archive cannot be verified") from exc
    if not files:
        raise StaticAssetPreparationError("prepared static asset archive must contain at least one file")
    if sha256_file(archive_path) != before:
        raise StaticAssetPreparationError("prepared static asset archive changed while being inspected")
    return {"name": STATIC_ARCHIVE_NAME, "sha256": before[0], "bytes": before[1]}, files


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise StaticAssetPreparationError("JSON input contains duplicate keys")
        value[key] = item
    return value


def _strict_json_loads(payload: bytes, *, field: str) -> Any:
    try:
        return json.loads(payload.decode("ascii"), object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError, StaticAssetPreparationError) as exc:
        raise StaticAssetPreparationError(f"{field} is not strict JSON") from exc


def _reject_persisted_url(payload: bytes, *, field: str) -> None:
    lowered = payload.lower()
    if b"://" in lowered or b'"url"' in lowered or b'"presigned"' in lowered:
        raise StaticAssetPreparationError(f"{field} must not persist a URL")


def _read_canonical_private_json(path: Path, *, field: str, maximum_bytes: int) -> tuple[dict[str, Any], bytes]:
    path = _require_private_file(path, field=field, maximum_bytes=maximum_bytes)
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise StaticAssetPreparationError(f"cannot read {field}") from exc
    _reject_persisted_url(payload, field=field)
    value = _strict_json_loads(payload, field=field)
    if not isinstance(value, dict) or payload != canonical_json_bytes(value) + b"\n":
        raise StaticAssetPreparationError(f"{field} must use canonical JSON")
    return value, payload


def _validated_files(value: object, *, field: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value or len(value) > MAX_STATIC_FILES:
        raise StaticAssetPreparationError(f"{field} is invalid")
    result: list[dict[str, Any]] = []
    prior = ""
    for item in value:
        if not isinstance(item, Mapping) or set(item) != {"path", "sha256", "bytes"}:
            raise StaticAssetPreparationError(f"{field} is invalid")
        path = _safe_asset_path(item.get("path"))
        digest = item.get("sha256")
        bytes_value = item.get("bytes")
        if (
            not isinstance(digest, str)
            or not SHA256_RE.fullmatch(digest)
            or isinstance(bytes_value, bool)
            or not isinstance(bytes_value, int)
            or not 0 <= bytes_value <= MAX_STATIC_FILE_BYTES
            or prior and path <= prior
        ):
            raise StaticAssetPreparationError(f"{field} is invalid")
        prior = path
        result.append({"path": path, "sha256": digest, "bytes": bytes_value})
    return result


def _installed_source_adoption_candidate() -> Path:
    """Locate only this packaged helper's root-only source-adoption candidate."""

    helper = _require_private_file(
        Path(__file__),
        field="installed static assets helper",
        maximum_bytes=MAX_INSTALLED_HELPER_BYTES,
    )
    candidate = _require_root_only_directory(helper.parent.parent, field="installed source-adoption candidate")
    if helper != candidate.joinpath(*STATIC_PREPARER_MEMBER.parts):
        raise StaticAssetPreparationError("static assets helper is not at its source-adoption package path")
    return candidate


def _load_controller_bound_expected_static_assets(
    *, campaign_id: str, application: Mapping[str, str]
) -> list[dict[str, Any]]:
    """Read the package-only manifest derived from the clean controller tree."""

    candidate = _installed_source_adoption_candidate()
    value, _payload = _read_canonical_private_json(
        candidate.joinpath(*EXPECTED_STATIC_ASSETS_MEMBER.parts),
        field="controller-bound expected static assets manifest",
        maximum_bytes=MAX_EXPECTED_STATIC_ASSETS_MANIFEST_BYTES,
    )
    expected = {
        "schema",
        "status",
        "campaign_id",
        "application",
        "tooling",
        "static_root",
        "files",
        "files_sha256",
    }
    if (
        set(value) != expected
        or value.get("schema") != EXPECTED_STATIC_ASSETS_SCHEMA
        or value.get("status") != "prepared"
        or value.get("campaign_id") != campaign_id
        or value.get("static_root") != RUNTIME_STATIC_ASSET_RELATIVE
    ):
        raise StaticAssetPreparationError("controller-bound expected static assets manifest is unsupported")
    expected_application = value.get("application")
    if not isinstance(expected_application, Mapping) or set(expected_application) != {
        "release_sha",
        "release_tree",
        "expected_alembic_revision",
    }:
        raise StaticAssetPreparationError("controller-bound expected static assets application is invalid")
    if (
        expected_application.get("release_sha") != application["release_sha"]
        or expected_application.get("expected_alembic_revision") != application["expected_alembic_revision"]
        or not isinstance(expected_application.get("release_tree"), str)
        or not RELEASE_RE.fullmatch(expected_application["release_tree"])
    ):
        raise StaticAssetPreparationError("controller-bound expected static assets application binding is invalid")
    tooling = value.get("tooling")
    if not isinstance(tooling, Mapping) or set(tooling) != {"control_commit", "control_tree"}:
        raise StaticAssetPreparationError("controller-bound expected static assets tooling is invalid")
    for item in tooling.values():
        if not isinstance(item, str) or not RELEASE_RE.fullmatch(item):
            raise StaticAssetPreparationError("controller-bound expected static assets tooling is invalid")
    files = _validated_files(value.get("files"), field="controller-bound expected static assets files")
    if value.get("files_sha256") != _files_sha256(files):
        raise StaticAssetPreparationError("controller-bound expected static assets file hash is invalid")
    return files


def verify_prepared_static_assets(
    *,
    output_directory: Path,
    expected_campaign_id: str,
    expected_application: Mapping[str, str],
    expected_static_source: Path | None = None,
) -> dict[str, Any]:
    """Verify a finished candidate without touching the original source tree."""

    _require_root_execution()
    campaign_id, application = _validate_identity(campaign_id=expected_campaign_id, application=expected_application)
    output_directory = _require_root_only_directory(Path(output_directory), field="static asset candidate")
    expected_names = {STATIC_ARCHIVE_NAME, STATIC_FILE_MANIFEST_NAME, STATIC_PREPARATION_RECEIPT_NAME}
    try:
        names = {entry.name for entry in output_directory.iterdir()}
    except OSError as exc:
        raise StaticAssetPreparationError("cannot enumerate static asset candidate") from exc
    if names != expected_names:
        raise StaticAssetPreparationError("static asset candidate has an unexpected file set")
    archive, archive_files = inspect_prepared_static_archive(output_directory / STATIC_ARCHIVE_NAME)
    manifest, manifest_payload = _read_canonical_private_json(
        output_directory / STATIC_FILE_MANIFEST_NAME,
        field="static asset file manifest",
        maximum_bytes=MAX_FILE_MANIFEST_BYTES,
    )
    expected_manifest_fields = {
        "schema",
        "status",
        "campaign_id",
        "application",
        "source_site",
        "artifact",
        "files",
        "files_sha256",
    }
    if (
        set(manifest) != expected_manifest_fields
        or manifest.get("schema") != STATIC_ASSET_FILE_MANIFEST_SCHEMA
        or manifest.get("status") != "prepared"
        or manifest.get("campaign_id") != campaign_id
        or manifest.get("application") != application
        or manifest.get("source_site") != "webapp_fi"
        or manifest.get("artifact") != STATIC_ARCHIVE_NAME
    ):
        raise StaticAssetPreparationError("static asset file manifest is unsupported")
    manifest_files = _validated_files(manifest.get("files"), field="static asset file manifest files")
    if manifest.get("files_sha256") != _files_sha256(manifest_files) or manifest_files != archive_files:
        raise StaticAssetPreparationError("static asset file manifest does not bind the prepared archive")
    receipt, receipt_payload = _read_canonical_private_json(
        output_directory / STATIC_PREPARATION_RECEIPT_NAME,
        field="static asset preparation receipt",
        maximum_bytes=RECEIPT_RESERVE_BYTES,
    )
    expected_receipt_fields = {
        "schema",
        "status",
        "campaign_id",
        "application",
        "source_site",
        "source_root",
        "output_directory",
        "archive",
        "file_manifest_sha256",
        "files_sha256",
        "capacity_preflight",
        "source_drift_rechecked",
        "receipt_sha256",
    }
    if (
        set(receipt) != expected_receipt_fields
        or receipt.get("schema") != STATIC_ASSET_PREPARATION_RECEIPT_SCHEMA
        or receipt.get("status") != "prepared"
        or receipt.get("campaign_id") != campaign_id
        or receipt.get("application") != application
        or receipt.get("source_site") != "webapp_fi"
        or receipt.get("output_directory") != str(output_directory)
        or receipt.get("archive") != archive
        or receipt.get("file_manifest_sha256") != sha256_bytes(manifest_payload)
        or receipt.get("files_sha256") != _files_sha256(manifest_files)
        or receipt.get("source_drift_rechecked") is not True
    ):
        raise StaticAssetPreparationError("static asset preparation receipt is unsupported")
    if expected_static_source is not None:
        expected_static_source = _require_root_controlled_directory(
            Path(expected_static_source), field="expected mini_app_dist source"
        )
        if receipt.get("source_root") != str(expected_static_source):
            raise StaticAssetPreparationError(
                "static asset preparation receipt source root is not bound to the expected source"
            )
    elif not isinstance(receipt.get("source_root"), str) or not Path(str(receipt["source_root"])).is_absolute():
        raise StaticAssetPreparationError("static asset preparation receipt source root is invalid")
    capacity = receipt.get("capacity_preflight")
    expected_capacity_fields = {
        "archive_upper_bound_bytes",
        "file_manifest_bytes",
        "source_bytes",
        "file_count",
        "receipt_reserve_bytes",
        "margin_bytes",
        "required_free_bytes",
        "available_free_bytes",
    }
    if not isinstance(capacity, Mapping) or set(capacity) != expected_capacity_fields:
        raise StaticAssetPreparationError("static asset preparation capacity record is invalid")
    for value in capacity.values():
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise StaticAssetPreparationError("static asset preparation capacity record is invalid")
    if (
        capacity["archive_upper_bound_bytes"] < archive["bytes"]
        or capacity["file_manifest_bytes"] != len(manifest_payload)
        or capacity["source_bytes"] != sum(item["bytes"] for item in manifest_files)
        or capacity["file_count"] != len(manifest_files)
        or capacity["receipt_reserve_bytes"] != RECEIPT_RESERVE_BYTES
        or capacity["margin_bytes"] != CAPACITY_MARGIN_BYTES
        or capacity["required_free_bytes"]
        != capacity["archive_upper_bound_bytes"]
        + capacity["file_manifest_bytes"]
        + capacity["receipt_reserve_bytes"]
        + capacity["margin_bytes"]
        or capacity["available_free_bytes"] < capacity["required_free_bytes"]
    ):
        raise StaticAssetPreparationError("static asset preparation capacity record is inconsistent")
    receipt_unsigned = {key: value for key, value in receipt.items() if key != "receipt_sha256"}
    if receipt.get("receipt_sha256") != sha256_bytes(canonical_json_bytes(receipt_unsigned)):
        raise StaticAssetPreparationError("static asset preparation receipt hash is invalid")
    return {
        "status": "verified",
        "output_directory": str(output_directory),
        "archive": archive,
        "files_sha256": _files_sha256(manifest_files),
        "file_count": len(manifest_files),
        "file_manifest_sha256": sha256_bytes(manifest_payload),
        "preparation_receipt_sha256": sha256_bytes(receipt_payload),
        "object_storage_action": False,
        "age_action": False,
        "ssh_action": False,
        "docker_action": False,
        "service_changed": False,
    }


def prepare_static_assets(
    *,
    runtime_source_root: Path,
    output_directory: Path,
    expected_campaign_id: str,
    expected_application: Mapping[str, str],
    apply: bool,
    disk_usage: DiskUsage = shutil.disk_usage,
) -> dict[str, Any]:
    """Plan or write one fresh deterministic static-assets candidate."""

    _require_root_execution()
    campaign_id, application = _validate_identity(campaign_id=expected_campaign_id, application=expected_application)
    expected_runtime_source_root = Path(FI_RUNTIME_SOURCE_ROOT)
    runtime_source_root = _require_root_only_directory(Path(runtime_source_root), field="runtime source root")
    if runtime_source_root != expected_runtime_source_root:
        raise StaticAssetPreparationError("runtime source root is not the fixed WebApp-FI current path")
    expected_files = _load_controller_bound_expected_static_assets(
        campaign_id=campaign_id,
        application=application,
    )
    static_root = _require_root_controlled_directory(
        runtime_source_root / RUNTIME_STATIC_ASSET_RELATIVE,
        field="mini_app_dist source",
    )
    output_directory = Path(output_directory)
    if not output_directory.is_absolute():
        raise StaticAssetPreparationError("output_directory must be an absolute path")
    output_parent = _require_root_only_directory(output_directory.parent, field="output_directory parent")
    if output_directory.parent != output_parent or output_directory.exists() or output_directory.is_symlink():
        raise StaticAssetPreparationError("output_directory must be a new child of a root-only directory")
    source_files = _scan_static_source(static_root)
    public_files = _public_files(source_files)
    if public_files != expected_files:
        raise StaticAssetPreparationError("mini_app_dist does not match the controller-bound expected static manifest")
    manifest = _file_manifest(campaign_id=campaign_id, application=application, files=public_files)
    manifest_payload = canonical_json_bytes(manifest) + b"\n"
    _reject_persisted_url(manifest_payload, field="static asset file manifest")
    capacity = _capacity_preflight(
        output_parent=output_parent,
        archive_upper_bound_bytes=_archive_upper_bound(source_files),
        file_manifest_bytes=len(manifest_payload),
        source_bytes=sum(item.bytes for item in source_files),
        file_count=len(source_files),
        disk_usage=disk_usage,
    )
    plan = {
        "status": "prepared" if apply else "planned",
        "campaign_id": campaign_id,
        "application": application,
        "source_site": "webapp_fi",
        "runtime_source_root": str(runtime_source_root),
        "static_source_root": str(static_root),
        "output_directory": str(output_directory),
        "archive_name": STATIC_ARCHIVE_NAME,
        "files_sha256": manifest["files_sha256"],
        "file_count": len(source_files),
        "capacity_preflight": capacity,
        "object_storage_action": False,
        "age_action": False,
        "ssh_action": False,
        "docker_action": False,
        "service_changed": False,
    }
    if not apply:
        return plan
    try:
        output_directory.mkdir(mode=0o700)
    except OSError as exc:
        raise StaticAssetPreparationError("cannot create fresh static asset candidate") from exc
    _require_root_only_directory(output_directory, field="static asset candidate")
    _fsync_directory(output_parent)
    archive_path = output_directory / STATIC_ARCHIVE_NAME
    archive_sha256, archive_bytes = _write_ustar_archive(
        archive_path=archive_path,
        static_root=static_root,
        files=source_files,
    )
    archive, archive_files = inspect_prepared_static_archive(archive_path)
    if (
        archive != {"name": STATIC_ARCHIVE_NAME, "sha256": archive_sha256, "bytes": archive_bytes}
        or archive_files != public_files
        or archive_bytes > capacity["archive_upper_bound_bytes"]
    ):
        raise StaticAssetPreparationError("prepared static asset archive does not match its source snapshot")
    # Re-scan only after copying, before publishing any success receipt.  A
    # failed candidate remains intact but has no receipt that claims success.
    if _scan_static_source(static_root) != source_files:
        raise StaticAssetPreparationError("mini_app_dist source drifted while preparing the archive")
    manifest_path = output_directory / STATIC_FILE_MANIFEST_NAME
    written_manifest = _write_new_private_json(manifest_path, manifest, field="static asset file manifest")
    if written_manifest != manifest_payload:
        raise StaticAssetPreparationError("static asset file manifest serialization changed unexpectedly")
    receipt_unsigned = {
        "schema": STATIC_ASSET_PREPARATION_RECEIPT_SCHEMA,
        "status": "prepared",
        "campaign_id": campaign_id,
        "application": application,
        "source_site": "webapp_fi",
        "source_root": str(static_root),
        "output_directory": str(output_directory),
        "archive": archive,
        "file_manifest_sha256": sha256_bytes(written_manifest),
        "files_sha256": manifest["files_sha256"],
        "capacity_preflight": capacity,
        "source_drift_rechecked": True,
    }
    receipt = {
        **receipt_unsigned,
        "receipt_sha256": sha256_bytes(canonical_json_bytes(receipt_unsigned)),
    }
    receipt_path = output_directory / STATIC_PREPARATION_RECEIPT_NAME
    receipt_payload = _write_new_private_json(receipt_path, receipt, field="static asset preparation receipt")
    _fsync_directory(output_directory)
    verified = verify_prepared_static_assets(
        output_directory=output_directory,
        expected_campaign_id=campaign_id,
        expected_application=application,
        expected_static_source=static_root,
    )
    return {
        **plan,
        "archive": archive,
        "file_manifest_path": str(manifest_path),
        "preparation_receipt_path": str(receipt_path),
        "file_manifest_sha256": sha256_bytes(written_manifest),
        "preparation_receipt_sha256": sha256_bytes(receipt_payload),
        "verification": verified,
    }


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-source-root", type=Path, required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--release-sha", required=True)
    parser.add_argument("--expected-alembic-revision", required=True)
    parser.add_argument("--apply", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _argument_parser().parse_args(argv)
    try:
        result = prepare_static_assets(
            runtime_source_root=args.runtime_source_root,
            output_directory=args.output_directory,
            expected_campaign_id=args.campaign_id,
            expected_application={
                "release_sha": args.release_sha,
                "expected_alembic_revision": args.expected_alembic_revision,
            },
            apply=args.apply,
        )
    except StaticAssetPreparationError as exc:
        print(
            json.dumps(
                {"status": "blocked", "error": str(exc), "error_class": exc.__class__.__name__},
                sort_keys=True,
            )
        )
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI dispatch.
    raise SystemExit(main())
