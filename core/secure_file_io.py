"""Small fail-closed primitives for local secret and append-only audit files.

The helpers deliberately operate on file descriptors.  Path based ``stat``
followed by ``read_text`` leaves a swap/symlink race that is unacceptable for
provider credentials and activation manifests.
"""

from __future__ import annotations

import fcntl
import hashlib
import hmac
import json
import os
import secrets
import stat
from pathlib import Path
from typing import Any


class SecureFileError(RuntimeError):
    """Raised when a security-sensitive file cannot be proven safe."""


def write_secure_new_bytes(
    path: Path,
    payload: bytes,
    *,
    label: str = "secure file",
    mode: int = 0o600,
    max_size: int = 1024 * 1024,
) -> None:
    """Publish a complete owner-only file without replacing any existing path."""

    if not isinstance(payload, bytes) or len(payload) > max_size:
        raise SecureFileError(f"{label} payload is invalid or oversized")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
    directory_flags |= getattr(os, "O_NOFOLLOW", 0)
    directory_fd = os.open(path.parent, directory_flags)
    temporary_name = f".{path.name}.{os.getpid()}.{secrets.token_hex(8)}.tmp"
    temporary_fd = -1
    published = False
    try:
        directory_metadata = os.fstat(directory_fd)
        if (
            not stat.S_ISDIR(directory_metadata.st_mode)
            or directory_metadata.st_uid != os.geteuid()
            or stat.S_IMODE(directory_metadata.st_mode) & 0o022
        ):
            raise SecureFileError(f"{label} directory is not owner-controlled")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        temporary_fd = os.open(temporary_name, flags, mode, dir_fd=directory_fd)
        view = memoryview(payload)
        written = 0
        while written < len(view):
            count = os.write(temporary_fd, view[written:])
            if count <= 0:
                raise SecureFileError(f"{label} write made no progress")
            written += count
        os.fchmod(temporary_fd, mode)
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
            raise SecureFileError(f"{label} already exists") from exc
        published = True
        os.unlink(temporary_name, dir_fd=directory_fd)
        os.fsync(directory_fd)
    finally:
        if temporary_fd >= 0:
            os.close(temporary_fd)
        try:
            os.unlink(temporary_name, dir_fd=directory_fd)
        except FileNotFoundError:
            pass
        os.close(directory_fd)
    if not published:
        raise SecureFileError(f"{label} was not published")


def write_secure_atomic_bytes(
    path: Path,
    payload: bytes,
    *,
    label: str = "secure file",
    mode: int = 0o600,
    max_size: int = 1024 * 1024,
) -> None:
    """Atomically replace one owner-only file through a no-follow dirfd."""

    if not isinstance(payload, bytes) or len(payload) > max_size:
        raise SecureFileError(f"{label} payload is invalid or oversized")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
    directory_flags |= getattr(os, "O_NOFOLLOW", 0)
    directory_fd = os.open(path.parent, directory_flags)
    temporary_name = f".{path.name}.{os.getpid()}.{secrets.token_hex(8)}.tmp"
    temporary_fd = -1
    try:
        directory_metadata = os.fstat(directory_fd)
        if (
            not stat.S_ISDIR(directory_metadata.st_mode)
            or directory_metadata.st_uid != os.geteuid()
            or stat.S_IMODE(directory_metadata.st_mode) & 0o022
        ):
            raise SecureFileError(f"{label} directory is not owner-controlled")
        try:
            existing = os.stat(path.name, dir_fd=directory_fd, follow_symlinks=False)
        except FileNotFoundError:
            existing = None
        if existing is not None:
            _assert_regular_owner_file(
                existing,
                label=label,
                owner_uid=os.geteuid(),
                max_size=max_size,
            )
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        temporary_fd = os.open(temporary_name, flags, mode, dir_fd=directory_fd)
        view = memoryview(payload)
        written = 0
        while written < len(view):
            count = os.write(temporary_fd, view[written:])
            if count <= 0:
                raise SecureFileError(f"{label} atomic write made no progress")
            written += count
        os.fchmod(temporary_fd, mode)
        os.fsync(temporary_fd)
        os.close(temporary_fd)
        temporary_fd = -1
        os.replace(
            temporary_name,
            path.name,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
        )
        os.fsync(directory_fd)
    finally:
        if temporary_fd >= 0:
            os.close(temporary_fd)
        try:
            os.unlink(temporary_name, dir_fd=directory_fd)
        except FileNotFoundError:
            pass
        os.close(directory_fd)


def _assert_regular_owner_file(
    metadata: os.stat_result,
    *,
    label: str,
    owner_uid: int,
    max_size: int,
    require_single_link: bool = True,
) -> None:
    if not stat.S_ISREG(metadata.st_mode):
        raise SecureFileError(f"{label} must be a regular file")
    if metadata.st_uid != owner_uid:
        raise SecureFileError(f"{label} must be owned by uid {owner_uid}")
    if stat.S_IMODE(metadata.st_mode) & 0o077:
        raise SecureFileError(f"{label} must not be group/world accessible")
    if require_single_link and metadata.st_nlink != 1:
        raise SecureFileError(f"{label} must have exactly one hard link")
    if metadata.st_size < 0 or metadata.st_size > max_size:
        raise SecureFileError(f"{label} exceeds the maximum size of {max_size} bytes")


def read_secure_bytes(
    path: Path,
    *,
    label: str = "secure file",
    owner_uid: int | None = None,
    max_size: int = 1024 * 1024,
) -> bytes:
    """Read one stable, owner-only regular file without following symlinks."""

    expected_uid = os.geteuid() if owner_uid is None else int(owner_uid)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise SecureFileError(f"cannot securely open {label}: {path}") from exc
    try:
        before = os.fstat(descriptor)
        _assert_regular_owner_file(
            before,
            label=label,
            owner_uid=expected_uid,
            max_size=max_size,
        )
        chunks: list[bytes] = []
        remaining = max_size + 1
        while remaining > 0:
            chunk = os.read(descriptor, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        if len(payload) > max_size:
            raise SecureFileError(f"{label} exceeds the maximum size of {max_size} bytes")
        after = os.fstat(descriptor)
        stable_fields = ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_nlink", "st_size")
        if any(getattr(before, field) != getattr(after, field) for field in stable_fields):
            raise SecureFileError(f"{label} changed while it was being read")
        return payload
    finally:
        os.close(descriptor)


def read_secure_text(
    path: Path,
    *,
    label: str = "secure file",
    owner_uid: int | None = None,
    max_size: int = 1024 * 1024,
) -> str:
    try:
        return read_secure_bytes(
            path,
            label=label,
            owner_uid=owner_uid,
            max_size=max_size,
        ).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SecureFileError(f"{label} must contain UTF-8 text") from exc


def sha256_secure_file(
    path: Path,
    *,
    label: str = "secure artifact",
    owner_uid: int | None = None,
    max_size: int = 4 * 1024 * 1024 * 1024,
) -> tuple[str, int]:
    """Hash a stable owner-only regular file without buffering it in memory."""

    expected_uid = os.geteuid() if owner_uid is None else int(owner_uid)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise SecureFileError(f"cannot securely open {label}: {path}") from exc
    try:
        before = os.fstat(descriptor)
        _assert_regular_owner_file(
            before,
            label=label,
            owner_uid=expected_uid,
            max_size=max_size,
        )
        digest = hashlib.sha256()
        size = 0
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > max_size:
                raise SecureFileError(f"{label} exceeds the maximum size of {max_size} bytes")
            digest.update(chunk)
        after = os.fstat(descriptor)
        stable_fields = ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_nlink", "st_size")
        if any(getattr(before, field) != getattr(after, field) for field in stable_fields):
            raise SecureFileError(f"{label} changed while it was being hashed")
        return digest.hexdigest(), size
    finally:
        os.close(descriptor)


def _last_hash_from_locked_audit(descriptor: int, *, max_size: int) -> str:
    metadata = os.fstat(descriptor)
    if metadata.st_size == 0:
        return "0" * 64
    if metadata.st_size > max_size:
        raise SecureFileError("audit log exceeds its configured maximum size")
    os.lseek(descriptor, 0, os.SEEK_SET)
    payload = b""
    remaining = metadata.st_size
    while remaining:
        chunk = os.read(descriptor, min(65536, remaining))
        if not chunk:
            break
        payload += chunk
        remaining -= len(chunk)
    lines = payload.splitlines()
    if not lines:
        return "0" * 64
    try:
        previous = json.loads(lines[-1].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SecureFileError("audit log tail is not valid JSON") from exc
    event_hash = previous.get("event_hash") if isinstance(previous, dict) else None
    if not isinstance(event_hash, str) or len(event_hash) != 64:
        raise SecureFileError("audit log tail has no valid event hash")
    return event_hash


def append_hash_chained_jsonl(
    path: Path,
    event: dict[str, Any],
    *,
    max_size: int = 64 * 1024 * 1024,
) -> dict[str, Any]:
    """Atomically append one fsync'd hash-chained JSON record.

    The containing directory and file must be owned by the current effective
    uid.  The file is opened relative to a no-follow directory descriptor and
    locked across tail verification plus append.
    """

    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
    directory_flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        directory_fd = os.open(path.parent, directory_flags)
    except OSError as exc:
        raise SecureFileError(f"cannot securely open audit directory: {path.parent}") from exc
    descriptor = -1
    try:
        directory_metadata = os.fstat(directory_fd)
        if not stat.S_ISDIR(directory_metadata.st_mode) or directory_metadata.st_uid != os.geteuid():
            raise SecureFileError("audit directory must be owned by the current uid")
        if stat.S_IMODE(directory_metadata.st_mode) & 0o022:
            raise SecureFileError("audit directory must not be group/world writable")
        flags = os.O_RDWR | os.O_APPEND | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path.name, flags, 0o600, dir_fd=directory_fd)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        metadata = os.fstat(descriptor)
        _assert_regular_owner_file(
            metadata,
            label="audit log",
            owner_uid=os.geteuid(),
            max_size=max_size,
        )
        previous_hash = _last_hash_from_locked_audit(descriptor, max_size=max_size)
        unsigned = {**event, "previous_hash": previous_hash}
        encoded_unsigned = json.dumps(
            unsigned,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        record = {
            **unsigned,
            "event_hash": hashlib.sha256(encoded_unsigned).hexdigest(),
        }
        encoded = (
            json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        if metadata.st_size + len(encoded) > max_size:
            raise SecureFileError("audit append would exceed its configured maximum size")
        view = memoryview(encoded)
        written = 0
        while written < len(view):
            count = os.write(descriptor, view[written:])
            if count <= 0:
                raise SecureFileError("audit append made no progress")
            written += count
        os.fsync(descriptor)
        os.fsync(directory_fd)
        return record
    finally:
        if descriptor >= 0:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)
        os.close(directory_fd)


def verify_hash_chained_jsonl(
    path: Path,
    *,
    label: str = "audit log",
    max_size: int = 64 * 1024 * 1024,
) -> list[dict[str, Any]]:
    """Read a secure JSONL log and verify every hash-chain link."""

    raw = read_secure_bytes(path, label=label, max_size=max_size)
    previous_hash = "0" * 64
    records: list[dict[str, Any]] = []
    for line_number, raw_line in enumerate(raw.splitlines(), start=1):
        try:
            record = json.loads(raw_line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SecureFileError(f"{label} line {line_number} is not valid JSON") from exc
        if not isinstance(record, dict):
            raise SecureFileError(f"{label} line {line_number} is not an object")
        event_hash = record.get("event_hash")
        if record.get("previous_hash") != previous_hash or not isinstance(event_hash, str):
            raise SecureFileError(f"{label} hash chain breaks at line {line_number}")
        unsigned = {key: value for key, value in record.items() if key != "event_hash"}
        expected = hashlib.sha256(
            json.dumps(
                unsigned,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        if not hmac.compare_digest(event_hash, expected):
            raise SecureFileError(f"{label} event hash is invalid at line {line_number}")
        previous_hash = event_hash
        records.append(record)
    return records
