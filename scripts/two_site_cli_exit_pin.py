"""Safe disposable CLI exit pin capture and verification module."""

from __future__ import annotations

import collections.abc
import dataclasses
import hashlib
import os
import pathlib
import stat
from typing import Callable, Mapping

__all__ = [
    "CliExitPin",
    "CliExitPinError",
    "capture_cli_exit_pin",
    "verify_cli_exit_pin",
    "LABEL_CLOSE_FAILED",
    "LABEL_COPY_MISMATCH",
    "LABEL_HASH_MISMATCH",
    "LABEL_INVALID_ARGUMENT",
    "LABEL_OPEN_FAILED",
    "LABEL_PARENT_MISMATCH",
    "LABEL_PATH_NOT_FOUND",
    "LABEL_PROBE_FAILED",
    "LABEL_PROBE_INVALID",
    "LABEL_PROBE_MISMATCH",
    "LABEL_PROBE_RESULT_INVALID",
    "LABEL_READ_FAILED",
    "LABEL_SIZE_MISMATCH",
    "LABEL_STAT_FAILED",
]

LABEL_INVALID_ARGUMENT = "Invalid argument provided."
LABEL_PATH_NOT_FOUND = "Path does not exist or cannot be resolved."
LABEL_PARENT_MISMATCH = "Parent directory metadata mismatch."
LABEL_COPY_MISMATCH = "Disposable copy metadata mismatch."
LABEL_OPEN_FAILED = "Failed to open descriptor."
LABEL_STAT_FAILED = "Failed to inspect descriptor or path."
LABEL_READ_FAILED = "Failed to read disposable copy."
LABEL_CLOSE_FAILED = "Failed to close descriptor."
LABEL_HASH_MISMATCH = "Disposable copy SHA-256 mismatch."
LABEL_SIZE_MISMATCH = "Disposable copy size mismatch."
LABEL_PROBE_FAILED = "Version probe execution failed."
LABEL_PROBE_INVALID = "Version probe argument or configuration invalid."
LABEL_PROBE_RESULT_INVALID = "Version probe result structure invalid."
LABEL_PROBE_MISMATCH = "Version probe metadata mismatch."


class CliExitPinError(ValueError):
    """Raised when a CLI exit pin cannot be captured or verified safely."""


@dataclasses.dataclass(frozen=True, slots=True)
class CliExitPin:
    """Immutable baseline metadata for a verified disposable CLI executable copy."""

    disposable: pathlib.Path
    expected_sha256: str
    expected_version: str | None
    size: int
    mtime_ns: int
    ctime_ns: int
    mode: int
    nlink: int
    uid: int
    dev: int
    ino: int
    parent_path: pathlib.Path
    parent_dev: int
    parent_ino: int
    parent_uid: int
    parent_mode: int


def _is_cancel(exc: BaseException) -> bool:
    return isinstance(exc, (KeyboardInterrupt, SystemExit, GeneratorExit))


def _check_parent_named(pin: CliExitPin) -> None:
    try:
        st = os.lstat(pin.parent_path)
    except BaseException as exc:
        if _is_cancel(exc):
            raise
        raise CliExitPinError(LABEL_STAT_FAILED) from exc

    if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
        raise CliExitPinError(LABEL_PARENT_MISMATCH)
    if stat.S_IMODE(st.st_mode) != pin.parent_mode:
        raise CliExitPinError(LABEL_PARENT_MISMATCH)
    if st.st_uid != pin.parent_uid:
        raise CliExitPinError(LABEL_PARENT_MISMATCH)
    if st.st_dev != pin.parent_dev or st.st_ino != pin.parent_ino:
        raise CliExitPinError(LABEL_PARENT_MISMATCH)

    try:
        resolved = pin.parent_path.resolve(strict=True)
    except BaseException as exc:
        if _is_cancel(exc):
            raise
        raise CliExitPinError(LABEL_PATH_NOT_FOUND) from exc
    if resolved != pin.parent_path:
        raise CliExitPinError(LABEL_PARENT_MISMATCH)


def _check_parent_fd(pin: CliExitPin, parent_fd: int) -> None:
    try:
        st = os.fstat(parent_fd)
    except BaseException as exc:
        if _is_cancel(exc):
            raise
        raise CliExitPinError(LABEL_STAT_FAILED) from exc

    if not stat.S_ISDIR(st.st_mode):
        raise CliExitPinError(LABEL_PARENT_MISMATCH)
    if stat.S_IMODE(st.st_mode) != pin.parent_mode:
        raise CliExitPinError(LABEL_PARENT_MISMATCH)
    if st.st_uid != pin.parent_uid:
        raise CliExitPinError(LABEL_PARENT_MISMATCH)
    if st.st_dev != pin.parent_dev or st.st_ino != pin.parent_ino:
        raise CliExitPinError(LABEL_PARENT_MISMATCH)


def _check_copy_parent_relative(pin: CliExitPin, parent_fd: int) -> None:
    try:
        st = os.stat(pin.disposable.name, dir_fd=parent_fd, follow_symlinks=False)
    except BaseException as exc:
        if _is_cancel(exc):
            raise
        raise CliExitPinError(LABEL_STAT_FAILED) from exc

    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        raise CliExitPinError(LABEL_COPY_MISMATCH)
    if stat.S_IMODE(st.st_mode) != pin.mode:
        raise CliExitPinError(LABEL_COPY_MISMATCH)
    if st.st_nlink != pin.nlink:
        raise CliExitPinError(LABEL_COPY_MISMATCH)
    if st.st_uid != pin.uid:
        raise CliExitPinError(LABEL_COPY_MISMATCH)
    if st.st_dev != pin.dev or st.st_ino != pin.ino:
        raise CliExitPinError(LABEL_COPY_MISMATCH)
    if st.st_size != pin.size:
        raise CliExitPinError(LABEL_COPY_MISMATCH)
    if st.st_mtime_ns != pin.mtime_ns:
        raise CliExitPinError(LABEL_COPY_MISMATCH)
    if st.st_ctime_ns != pin.ctime_ns:
        raise CliExitPinError(LABEL_COPY_MISMATCH)


def _check_copy_named(pin: CliExitPin) -> None:
    try:
        st = os.lstat(pin.disposable)
    except BaseException as exc:
        if _is_cancel(exc):
            raise
        raise CliExitPinError(LABEL_STAT_FAILED) from exc

    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        raise CliExitPinError(LABEL_COPY_MISMATCH)
    if stat.S_IMODE(st.st_mode) != pin.mode:
        raise CliExitPinError(LABEL_COPY_MISMATCH)
    if st.st_nlink != pin.nlink:
        raise CliExitPinError(LABEL_COPY_MISMATCH)
    if st.st_uid != pin.uid:
        raise CliExitPinError(LABEL_COPY_MISMATCH)
    if st.st_dev != pin.dev or st.st_ino != pin.ino:
        raise CliExitPinError(LABEL_COPY_MISMATCH)
    if st.st_size != pin.size:
        raise CliExitPinError(LABEL_COPY_MISMATCH)
    if st.st_mtime_ns != pin.mtime_ns:
        raise CliExitPinError(LABEL_COPY_MISMATCH)
    if st.st_ctime_ns != pin.ctime_ns:
        raise CliExitPinError(LABEL_COPY_MISMATCH)

    try:
        resolved = pin.disposable.resolve(strict=True)
    except BaseException as exc:
        if _is_cancel(exc):
            raise
        raise CliExitPinError(LABEL_PATH_NOT_FOUND) from exc
    if resolved != pin.disposable:
        raise CliExitPinError(LABEL_COPY_MISMATCH)


def _check_copy_fd(pin: CliExitPin, copy_fd: int) -> None:
    try:
        st = os.fstat(copy_fd)
    except BaseException as exc:
        if _is_cancel(exc):
            raise
        raise CliExitPinError(LABEL_STAT_FAILED) from exc

    if not stat.S_ISREG(st.st_mode):
        raise CliExitPinError(LABEL_COPY_MISMATCH)
    if stat.S_IMODE(st.st_mode) != pin.mode:
        raise CliExitPinError(LABEL_COPY_MISMATCH)
    if st.st_nlink != pin.nlink:
        raise CliExitPinError(LABEL_COPY_MISMATCH)
    if st.st_uid != pin.uid:
        raise CliExitPinError(LABEL_COPY_MISMATCH)
    if st.st_dev != pin.dev or st.st_ino != pin.ino:
        raise CliExitPinError(LABEL_COPY_MISMATCH)
    if st.st_size != pin.size:
        raise CliExitPinError(LABEL_COPY_MISMATCH)
    if st.st_mtime_ns != pin.mtime_ns:
        raise CliExitPinError(LABEL_COPY_MISMATCH)
    if st.st_ctime_ns != pin.ctime_ns:
        raise CliExitPinError(LABEL_COPY_MISMATCH)


def _check_all(pin: CliExitPin, parent_fd: int, copy_fd: int) -> None:
    _check_parent_named(pin)
    _check_parent_fd(pin, parent_fd)
    _check_copy_named(pin)
    _check_copy_parent_relative(pin, parent_fd)
    _check_copy_fd(pin, copy_fd)


def _handle_cleanup(
    parent_fd: int,
    copy_fd: int,
    primary_exc: BaseException | None,
) -> None:
    primary_cancel = (
        primary_exc
        if (primary_exc is not None and _is_cancel(primary_exc))
        else None
    )
    cleanup_cancel: BaseException | None = None
    close_err: BaseException | None = None

    if copy_fd >= 0:
        try:
            os.close(copy_fd)
        except BaseException as exc:
            if _is_cancel(exc):
                if primary_cancel is None and cleanup_cancel is None:
                    cleanup_cancel = exc
            elif close_err is None:
                close_err = exc

    if parent_fd >= 0:
        try:
            os.close(parent_fd)
        except BaseException as exc:
            if _is_cancel(exc):
                if primary_cancel is None and cleanup_cancel is None:
                    cleanup_cancel = exc
            elif close_err is None:
                close_err = exc

    if primary_cancel is not None:
        raise primary_cancel
    if cleanup_cancel is not None:
        raise cleanup_cancel
    if primary_exc is None and close_err is not None:
        raise CliExitPinError(LABEL_CLOSE_FAILED) from close_err


def _run_fs_and_hash_proof(pin: CliExitPin) -> None:
    parent_flags = (
        os.O_RDONLY
        | os.O_DIRECTORY
        | os.O_CLOEXEC
        | getattr(os, "O_NOFOLLOW", 0)
    )
    copy_flags = (
        os.O_RDONLY
        | os.O_CLOEXEC
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )

    parent_fd = -1
    copy_fd = -1
    primary_exc: BaseException | None = None

    try:
        try:
            parent_fd = os.open(pin.parent_path, parent_flags)
        except BaseException as exc:
            if _is_cancel(exc):
                raise
            raise CliExitPinError(LABEL_OPEN_FAILED) from exc

        try:
            copy_fd = os.open(pin.disposable.name, copy_flags, dir_fd=parent_fd)
        except BaseException as exc:
            if _is_cancel(exc):
                raise
            raise CliExitPinError(LABEL_OPEN_FAILED) from exc

        _check_all(pin, parent_fd, copy_fd)

        hasher = hashlib.sha256()
        bytes_read = 0
        max_allowed = pin.size + 1

        while True:
            to_read = min(65536, max_allowed - bytes_read)
            if to_read <= 0:
                raise CliExitPinError(LABEL_SIZE_MISMATCH)

            try:
                chunk = os.read(copy_fd, to_read)
            except BaseException as exc:
                if _is_cancel(exc):
                    raise
                raise CliExitPinError(LABEL_READ_FAILED) from exc

            _check_all(pin, parent_fd, copy_fd)

            if not chunk:
                break

            bytes_read += len(chunk)
            if bytes_read > pin.size:
                raise CliExitPinError(LABEL_SIZE_MISMATCH)
            hasher.update(chunk)

        if bytes_read != pin.size:
            raise CliExitPinError(LABEL_SIZE_MISMATCH)

        digest = hasher.hexdigest()
        if digest != pin.expected_sha256:
            raise CliExitPinError(LABEL_HASH_MISMATCH)

        # Post-EOF recheck with all descriptors open:
        _check_all(pin, parent_fd, copy_fd)

        # Close copy descriptor explicitly:
        fd_to_close = copy_fd
        copy_fd = -1
        try:
            os.close(fd_to_close)
        except BaseException as exc:
            if _is_cancel(exc):
                raise
            raise CliExitPinError(LABEL_CLOSE_FAILED) from exc

        # Recheck after closing copy descriptor:
        _check_parent_named(pin)
        _check_parent_fd(pin, parent_fd)
        _check_copy_parent_relative(pin, parent_fd)
        _check_copy_named(pin)

        # Close parent directory descriptor explicitly:
        fd_to_close = parent_fd
        parent_fd = -1
        try:
            os.close(fd_to_close)
        except BaseException as exc:
            if _is_cancel(exc):
                raise
            raise CliExitPinError(LABEL_CLOSE_FAILED) from exc

        # Recheck after closing parent descriptor:
        _check_parent_named(pin)
        _check_copy_named(pin)

    except BaseException as exc:
        primary_exc = exc
        raise
    finally:
        _handle_cleanup(parent_fd, copy_fd, primary_exc)


def capture_cli_exit_pin(
    disposable: pathlib.Path,
    expected_sha256: str,
    expected_version: str | None = None,
) -> CliExitPin:
    """Capture immutable baseline metadata for an effective-UID-owned 0700 disposable copy."""
    if not isinstance(disposable, pathlib.Path):
        raise CliExitPinError(LABEL_INVALID_ARGUMENT)
    if not disposable.is_absolute():
        raise CliExitPinError(LABEL_INVALID_ARGUMENT)
    if ".." in disposable.parts or "." in disposable.parts:
        raise CliExitPinError(LABEL_INVALID_ARGUMENT)

    try:
        disp_is_symlink = disposable.is_symlink()
    except BaseException as exc:
        if _is_cancel(exc):
            raise
        raise CliExitPinError(LABEL_STAT_FAILED) from exc

    if disp_is_symlink:
        raise CliExitPinError(LABEL_COPY_MISMATCH)

    try:
        resolved_disposable = disposable.resolve(strict=True)
    except BaseException as exc:
        if _is_cancel(exc):
            raise
        raise CliExitPinError(LABEL_PATH_NOT_FOUND) from exc

    if resolved_disposable != disposable:
        raise CliExitPinError(LABEL_COPY_MISMATCH)

    if not isinstance(expected_sha256, str) or not expected_sha256.strip():
        raise CliExitPinError(LABEL_INVALID_ARGUMENT)
    exp_hex = expected_sha256.strip().lower()
    if len(exp_hex) != 64 or any(c not in "0123456789abcdef" for c in exp_hex):
        raise CliExitPinError(LABEL_INVALID_ARGUMENT)

    exp_version: str | None = None
    if expected_version is not None:
        if not isinstance(expected_version, str) or not expected_version.strip():
            raise CliExitPinError(LABEL_INVALID_ARGUMENT)
        exp_version = expected_version.strip()

    parent = disposable.parent
    if not parent.is_absolute():
        raise CliExitPinError(LABEL_INVALID_ARGUMENT)
    if ".." in parent.parts or "." in parent.parts:
        raise CliExitPinError(LABEL_INVALID_ARGUMENT)

    try:
        parent_is_symlink = parent.is_symlink()
    except BaseException as exc:
        if _is_cancel(exc):
            raise
        raise CliExitPinError(LABEL_STAT_FAILED) from exc

    if parent_is_symlink:
        raise CliExitPinError(LABEL_PARENT_MISMATCH)

    try:
        resolved_parent = parent.resolve(strict=True)
    except BaseException as exc:
        if _is_cancel(exc):
            raise
        raise CliExitPinError(LABEL_PATH_NOT_FOUND) from exc

    if resolved_parent != parent:
        raise CliExitPinError(LABEL_PARENT_MISMATCH)

    euid = os.geteuid()
    try:
        parent_lstat = os.lstat(parent)
    except BaseException as exc:
        if _is_cancel(exc):
            raise
        raise CliExitPinError(LABEL_STAT_FAILED) from exc

    if stat.S_ISLNK(parent_lstat.st_mode) or not stat.S_ISDIR(parent_lstat.st_mode):
        raise CliExitPinError(LABEL_PARENT_MISMATCH)
    if parent_lstat.st_uid != euid or stat.S_IMODE(parent_lstat.st_mode) != 0o700:
        raise CliExitPinError(LABEL_PARENT_MISMATCH)

    try:
        file_lstat = os.lstat(disposable)
    except BaseException as exc:
        if _is_cancel(exc):
            raise
        raise CliExitPinError(LABEL_STAT_FAILED) from exc

    if stat.S_ISLNK(file_lstat.st_mode) or not stat.S_ISREG(file_lstat.st_mode):
        raise CliExitPinError(LABEL_COPY_MISMATCH)
    if file_lstat.st_uid != euid:
        raise CliExitPinError(LABEL_COPY_MISMATCH)
    if file_lstat.st_nlink != 1:
        raise CliExitPinError(LABEL_COPY_MISMATCH)
    if stat.S_IMODE(file_lstat.st_mode) != 0o700:
        raise CliExitPinError(LABEL_COPY_MISMATCH)

    pin = CliExitPin(
        disposable=resolved_disposable,
        expected_sha256=exp_hex,
        expected_version=exp_version,
        size=file_lstat.st_size,
        mtime_ns=file_lstat.st_mtime_ns,
        ctime_ns=file_lstat.st_ctime_ns,
        mode=0o700,
        nlink=1,
        uid=euid,
        dev=file_lstat.st_dev,
        ino=file_lstat.st_ino,
        parent_path=resolved_parent,
        parent_dev=parent_lstat.st_dev,
        parent_ino=parent_lstat.st_ino,
        parent_uid=euid,
        parent_mode=0o700,
    )

    _run_fs_and_hash_proof(pin)
    return pin


def verify_cli_exit_pin(
    pin: CliExitPin,
    version_probe: Callable[[pathlib.Path], Mapping[str, str]] | None = None,
) -> None:
    """Verify an immutable CLI exit pin before and after an optional single exact version probe."""
    if not isinstance(pin, CliExitPin):
        raise CliExitPinError(LABEL_INVALID_ARGUMENT)

    if version_probe is not None:
        if not callable(version_probe):
            raise CliExitPinError(LABEL_PROBE_INVALID)
        if pin.expected_version is None or not pin.expected_version.strip():
            raise CliExitPinError(LABEL_PROBE_INVALID)

    # Initial proof:
    _run_fs_and_hash_proof(pin)

    if version_probe is not None:
        try:
            probe_result = version_probe(pin.disposable)
        except BaseException as exc:
            if _is_cancel(exc):
                raise
            raise CliExitPinError(LABEL_PROBE_FAILED) from exc

        if not isinstance(probe_result, collections.abc.Mapping):
            raise CliExitPinError(LABEL_PROBE_RESULT_INVALID)

        try:
            keys = set(probe_result.keys())
            if keys != {"path", "version", "sha256"}:
                raise CliExitPinError(LABEL_PROBE_RESULT_INVALID)
            res_path = probe_result["path"]
            res_version = probe_result["version"]
            res_sha256 = probe_result["sha256"]

            if res_path != str(pin.disposable):
                raise CliExitPinError(LABEL_PROBE_MISMATCH)
            if res_version != pin.expected_version:
                raise CliExitPinError(LABEL_PROBE_MISMATCH)
            if res_sha256 != pin.expected_sha256:
                raise CliExitPinError(LABEL_PROBE_MISMATCH)
        except BaseException as exc:
            if _is_cancel(exc):
                raise
            if isinstance(exc, CliExitPinError):
                raise
            raise CliExitPinError(LABEL_PROBE_RESULT_INVALID) from exc

        # Immediate post-probe revalidation:
        _run_fs_and_hash_proof(pin)
