"""Dormant private maintenance-transition record file operations under AE03.

Provides secure descriptor-relative reading and non-replacing publishing of
reviewed maintenance-transition record files.
"""

from __future__ import annotations

import errno
import os
import re
import secrets
import stat
from typing import Any

try:
    from scripts.two_site_maintenance_transition_dir import (
        LEAF_NAME_PATTERN,
        MaintenanceTransitionDirError,
    )
    from scripts.two_site_maintenance_transition_record import (
        MAX_MAINTENANCE_TRANSITION_RECORD_BYTES,
        MaintenanceTransitionRecordError,
        parse_maintenance_transition_record,
    )
except ImportError:
    from two_site_maintenance_transition_dir import (  # type: ignore[no-redef]
        LEAF_NAME_PATTERN,
        MaintenanceTransitionDirError,
    )
    from two_site_maintenance_transition_record import (  # type: ignore[no-redef]
        MAX_MAINTENANCE_TRANSITION_RECORD_BYTES,
        MaintenanceTransitionRecordError,
        parse_maintenance_transition_record,
    )

_LEAF_NAME_RE = re.compile(LEAF_NAME_PATTERN)


class MaintenanceTransitionFileError(ValueError):
    """Raised when maintenance-transition file operations fail."""


class MaintenanceTransitionDuplicateError(MaintenanceTransitionFileError):
    """Raised when attempting to publish an existing maintenance-transition leaf."""


class MaintenanceTransitionAmbiguousError(MaintenanceTransitionFileError):
    """Raised when publication outcome is ambiguous and final state cannot be proven."""


__all__ = [
    "MaintenanceTransitionAmbiguousError",
    "MaintenanceTransitionDuplicateError",
    "MaintenanceTransitionFileError",
    "publish_maintenance_transition_bytes_noreplace",
    "read_maintenance_transition_bytes",
]


def _validate_transitions_fd(transitions_fd: int) -> os.stat_result:
    if type(transitions_fd) is not int or transitions_fd < 0:
        raise MaintenanceTransitionFileError("Invalid transitions descriptor")

    try:
        st = os.fstat(transitions_fd)
    except OSError:
        raise MaintenanceTransitionFileError(
            "Failed to stat transitions descriptor"
        ) from None

    if stat.S_ISLNK(st.st_mode):
        raise MaintenanceTransitionFileError(
            "Transitions descriptor must not be a symlink"
        )
    if not stat.S_ISDIR(st.st_mode):
        raise MaintenanceTransitionFileError(
            "Transitions descriptor must be a directory"
        )
    if (st.st_mode & 0o777) != 0o700:
        raise MaintenanceTransitionFileError(
            "Transitions directory mode must be 0700"
        )
    if st.st_uid != os.geteuid():
        raise MaintenanceTransitionFileError(
            "Transitions directory owner mismatch"
        )
    return st


def _validate_leaf_name(leaf: str) -> str:
    if type(leaf) is not str:
        raise MaintenanceTransitionFileError("Leaf name must be a string")
    if not _LEAF_NAME_RE.fullmatch(leaf):
        raise MaintenanceTransitionFileError("Invalid leaf name format")
    return leaf


def _stat_fingerprint(
    st: os.stat_result,
) -> tuple[int, int, int, int, int, int, int, int]:
    return (
        st.st_dev,
        st.st_ino,
        st.st_mode,
        st.st_uid,
        st.st_nlink,
        st.st_size,
        st.st_mtime_ns,
        st.st_ctime_ns,
    )


def _validate_file_stat(st: os.stat_result) -> None:
    if stat.S_ISLNK(st.st_mode):
        raise MaintenanceTransitionFileError("Leaf file must not be a symlink")
    if not stat.S_ISREG(st.st_mode):
        raise MaintenanceTransitionFileError("Leaf file must be a regular file")
    if (st.st_mode & 0o777) != 0o600:
        raise MaintenanceTransitionFileError("Leaf file mode must be 0600")
    if st.st_uid != os.geteuid():
        raise MaintenanceTransitionFileError("Leaf file owner mismatch")
    if st.st_nlink != 1:
        raise MaintenanceTransitionFileError("Leaf file link count must be 1")
    if st.st_size <= 0:
        raise MaintenanceTransitionFileError("Leaf file cannot be empty")
    if st.st_size > MAX_MAINTENANCE_TRANSITION_RECORD_BYTES:
        raise MaintenanceTransitionFileError("Leaf file exceeds maximum size")


def _cleanup_temp_file(
    transitions_fd: int,
    temp_name: str,
    expected_dev: int,
    expected_ino: int,
) -> None:
    """Verify temporary file identity and safely unlink it, followed by directory fsync."""
    try:
        st = os.stat(temp_name, dir_fd=transitions_fd, follow_symlinks=False)
    except FileNotFoundError:
        try:
            os.fsync(transitions_fd)
        except OSError:
            pass
        raise MaintenanceTransitionAmbiguousError(
            "Temporary file disappeared unexpectedly; publication is ambiguous"
        ) from None
    except OSError:
        raise MaintenanceTransitionAmbiguousError(
            "Failed to stat temporary file during cleanup; publication is ambiguous"
        ) from None

    if (
        stat.S_ISLNK(st.st_mode)
        or not stat.S_ISREG(st.st_mode)
        or (st.st_dev, st.st_ino) != (expected_dev, expected_ino)
    ):
        raise MaintenanceTransitionAmbiguousError(
            "Temporary file identity mismatch during cleanup; publication is ambiguous"
        )

    try:
        os.unlink(temp_name, dir_fd=transitions_fd)
    except FileNotFoundError:
        try:
            os.fsync(transitions_fd)
        except OSError:
            pass
        raise MaintenanceTransitionAmbiguousError(
            "Temporary file disappeared before unlink; publication is ambiguous"
        ) from None
    except OSError:
        raise MaintenanceTransitionAmbiguousError(
            "Failed to unlink temporary file during cleanup; publication is ambiguous"
        ) from None

    try:
        os.fsync(transitions_fd)
    except OSError:
        raise MaintenanceTransitionAmbiguousError(
            "Failed to fsync directory after temporary file cleanup; publication is ambiguous"
        ) from None


def read_maintenance_transition_bytes(
    transitions_fd: int,
    leaf: str,
) -> bytes | None:
    """Read and validate a reviewed maintenance-transition record file.

    Returns the exact raw bytes of the record, or None ONLY if the final
    leaf file does not exist on initial stat.
    """
    _validate_transitions_fd(transitions_fd)
    _validate_leaf_name(leaf)

    try:
        st_path_before = os.stat(leaf, dir_fd=transitions_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    except OSError:
        raise MaintenanceTransitionFileError(
            "Failed to stat leaf file"
        ) from None

    _validate_file_stat(st_path_before)

    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        fd = os.open(leaf, flags, dir_fd=transitions_fd)
    except FileNotFoundError:
        raise MaintenanceTransitionFileError(
            "Leaf file disappeared after initial stat"
        ) from None
    except OSError:
        raise MaintenanceTransitionFileError(
            "Failed to open leaf file"
        ) from None

    try:
        try:
            st_fd_before = os.fstat(fd)
        except OSError:
            raise MaintenanceTransitionFileError(
                "Failed to stat leaf descriptor"
            ) from None

        _validate_file_stat(st_fd_before)

        if _stat_fingerprint(st_path_before) != _stat_fingerprint(st_fd_before):
            raise MaintenanceTransitionFileError(
                "Leaf metadata mismatch between path and descriptor"
            )

        expected_size = st_fd_before.st_size
        chunks: list[bytes] = []
        total_read = 0
        max_allowed = MAX_MAINTENANCE_TRANSITION_RECORD_BYTES
        chunk_size = 4096

        while True:
            to_read = min(chunk_size, (max_allowed + 1) - total_read)
            if to_read <= 0:
                raise MaintenanceTransitionFileError("Leaf file exceeds size limit")
            try:
                chunk = os.read(fd, to_read)
            except OSError:
                raise MaintenanceTransitionFileError(
                    "Failed to read leaf content"
                ) from None
            if not chunk:
                break
            total_read += len(chunk)
            if total_read > max_allowed:
                raise MaintenanceTransitionFileError("Leaf file exceeds size limit")
            chunks.append(chunk)

        raw = b"".join(chunks)
        if len(raw) != expected_size:
            raise MaintenanceTransitionFileError(
                "Read bytes do not match expected size"
            )

        try:
            st_fd_after = os.fstat(fd)
        except OSError:
            raise MaintenanceTransitionFileError(
                "Failed to restat leaf descriptor after read"
            ) from None

        if _stat_fingerprint(st_fd_after) != _stat_fingerprint(st_fd_before):
            raise MaintenanceTransitionFileError(
                "Leaf descriptor metadata changed during read"
            )

        try:
            st_path_after = os.stat(
                leaf, dir_fd=transitions_fd, follow_symlinks=False
            )
        except OSError:
            raise MaintenanceTransitionFileError(
                "Failed to restat leaf path after read"
            ) from None

        if _stat_fingerprint(st_path_after) != _stat_fingerprint(st_path_before):
            raise MaintenanceTransitionFileError(
                "Leaf path metadata changed during read"
            )

        try:
            parse_maintenance_transition_record(raw)
        except MaintenanceTransitionRecordError:
            raise MaintenanceTransitionFileError(
                "Invalid canonical transition record content"
            ) from None

        return raw
    finally:
        os.close(fd)


def publish_maintenance_transition_bytes_noreplace(
    transitions_fd: int,
    leaf: str,
    raw: bytes,
) -> None:
    """Publish a reviewed maintenance-transition record file with no-overwrite semantics.

    Writes to a private temporary file, validates metadata and syncs, hardlinks
    to final leaf, cleans own temporary file, and verifies final state via reader.
    """
    if type(raw) is not bytes:
        raise MaintenanceTransitionFileError("raw must be bytes")
    if len(raw) == 0:
        raise MaintenanceTransitionFileError("raw bytes cannot be empty")
    if len(raw) > MAX_MAINTENANCE_TRANSITION_RECORD_BYTES:
        raise MaintenanceTransitionFileError(
            "raw bytes exceed maximum allowed length"
        )

    try:
        parse_maintenance_transition_record(raw)
    except MaintenanceTransitionRecordError:
        raise MaintenanceTransitionFileError(
            "Invalid canonical transition record content"
        ) from None

    _validate_transitions_fd(transitions_fd)
    _validate_leaf_name(leaf)

    try:
        os.stat(leaf, dir_fd=transitions_fd, follow_symlinks=False)
        exists = True
    except FileNotFoundError:
        exists = False
    except OSError:
        raise MaintenanceTransitionFileError(
            "Failed to stat final leaf path"
        ) from None

    if exists:
        raise MaintenanceTransitionDuplicateError(
            "Final transition file already exists"
        )

    temp_flags = (
        os.O_CREAT
        | os.O_EXCL
        | os.O_RDWR
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    temp_name = f".tmp-{os.getpid()}-{secrets.token_hex(16)}"
    temp_fd: int | None = None
    temp_dev: int | None = None
    temp_ino: int | None = None

    try:
        try:
            temp_fd = os.open(
                temp_name, temp_flags, mode=0o600, dir_fd=transitions_fd
            )
        except OSError:
            raise MaintenanceTransitionFileError(
                "Failed to open temporary file"
            ) from None

        try:
            st_temp_init = os.fstat(temp_fd)
        except OSError:
            raise MaintenanceTransitionFileError(
                "Failed to stat temporary descriptor immediately after open"
            ) from None

        temp_dev = st_temp_init.st_dev
        temp_ino = st_temp_init.st_ino

        written = 0
        while written < len(raw):
            try:
                n = os.write(temp_fd, raw[written:])
            except OSError:
                raise MaintenanceTransitionFileError(
                    "Failed to write to temporary file"
                ) from None
            if n <= 0:
                raise MaintenanceTransitionFileError(
                    "Incomplete write to temporary file"
                )
            written += n

        try:
            st_temp = os.fstat(temp_fd)
        except OSError:
            raise MaintenanceTransitionFileError(
                "Failed to stat temporary descriptor"
            ) from None

        if (st_temp.st_mode & 0o777) != 0o600:
            try:
                os.fchmod(temp_fd, 0o600)
            except OSError:
                raise MaintenanceTransitionFileError(
                    "Failed to fchmod temporary file"
                ) from None
            try:
                st_temp = os.fstat(temp_fd)
            except OSError:
                raise MaintenanceTransitionFileError(
                    "Failed to restat temporary descriptor after fchmod"
                ) from None

        try:
            os.fsync(temp_fd)
        except OSError:
            raise MaintenanceTransitionFileError(
                "Failed to fsync temporary file"
            ) from None

        if stat.S_ISLNK(st_temp.st_mode):
            raise MaintenanceTransitionFileError(
                "Temporary file must not be a symlink"
            )
        if not stat.S_ISREG(st_temp.st_mode):
            raise MaintenanceTransitionFileError(
                "Temporary file must be a regular file"
            )
        if (st_temp.st_mode & 0o777) != 0o600:
            raise MaintenanceTransitionFileError(
                "Temporary file mode must be 0600"
            )
        if st_temp.st_uid != os.geteuid():
            raise MaintenanceTransitionFileError(
                "Temporary file owner mismatch"
            )
        if st_temp.st_nlink != 1:
            raise MaintenanceTransitionFileError(
                "Temporary file link count must be 1"
            )
        if st_temp.st_size != len(raw):
            raise MaintenanceTransitionFileError(
                "Temporary file size mismatch"
            )

        if (st_temp.st_dev, st_temp.st_ino) != (temp_dev, temp_ino):
            raise MaintenanceTransitionFileError(
                "Temporary file descriptor identity changed"
            )

        try:
            os.close(temp_fd)
        except OSError:
            raise MaintenanceTransitionFileError(
                "Failed to close temporary file"
            ) from None
        temp_fd = None

        try:
            st_temp_path = os.stat(
                temp_name, dir_fd=transitions_fd, follow_symlinks=False
            )
        except OSError:
            raise MaintenanceTransitionFileError(
                "Failed to stat temporary path before link"
            ) from None

        if (st_temp_path.st_dev, st_temp_path.st_ino) != (temp_dev, temp_ino):
            raise MaintenanceTransitionFileError(
                "Temporary path identity mismatch"
            )
        if (st_temp_path.st_mode & 0o777) != 0o600:
            raise MaintenanceTransitionFileError(
                "Temporary path mode must be 0600"
            )
        if st_temp_path.st_uid != os.geteuid():
            raise MaintenanceTransitionFileError(
                "Temporary path owner mismatch"
            )
        if st_temp_path.st_nlink != 1:
            raise MaintenanceTransitionFileError(
                "Temporary path link count must be 1"
            )
        if st_temp_path.st_size != len(raw):
            raise MaintenanceTransitionFileError(
                "Temporary path size mismatch"
            )

    except Exception as pre_err:
        if temp_fd is not None:
            try:
                os.close(temp_fd)
            except OSError:
                pass
            temp_fd = None
            if temp_dev is None or temp_ino is None:
                raise MaintenanceTransitionAmbiguousError(
                    "Temporary file identity could not be established; publication is ambiguous"
                ) from pre_err
        if temp_dev is not None and temp_ino is not None:
            _cleanup_temp_file(transitions_fd, temp_name, temp_dev, temp_ino)
        raise pre_err

    try:
        os.link(
            temp_name,
            leaf,
            src_dir_fd=transitions_fd,
            dst_dir_fd=transitions_fd,
            follow_symlinks=False,
        )
    except (FileExistsError, OSError) as link_exc:
        is_eexist = (
            isinstance(link_exc, FileExistsError)
            or getattr(link_exc, "errno", None) == errno.EEXIST
        )

        try:
            st_check = os.stat(leaf, dir_fd=transitions_fd, follow_symlinks=False)
            final_stat_state = "exists"
        except FileNotFoundError:
            final_stat_state = "absent"
            st_check = None
        except OSError:
            final_stat_state = "unknown"
            st_check = None

        if is_eexist:
            if (
                final_stat_state == "exists"
                and st_check is not None
                and (st_check.st_dev, st_check.st_ino) != (temp_dev, temp_ino)
            ):
                _cleanup_temp_file(transitions_fd, temp_name, temp_dev, temp_ino)
                raise MaintenanceTransitionDuplicateError(
                    "Final transition file already exists"
                ) from None
            elif (
                final_stat_state == "exists"
                and st_check is not None
                and (st_check.st_dev, st_check.st_ino) == (temp_dev, temp_ino)
            ):
                _cleanup_temp_file(transitions_fd, temp_name, temp_dev, temp_ino)
                raise MaintenanceTransitionAmbiguousError(
                    "Link raised EEXIST but final leaf shares temporary file inode; publication is ambiguous"
                ) from None
            else:
                _cleanup_temp_file(transitions_fd, temp_name, temp_dev, temp_ino)
                raise MaintenanceTransitionAmbiguousError(
                    "Link raised EEXIST but final leaf stat was ambiguous; publication is ambiguous"
                ) from None
        else:
            if final_stat_state == "exists":
                _cleanup_temp_file(transitions_fd, temp_name, temp_dev, temp_ino)
                raise MaintenanceTransitionAmbiguousError(
                    "Link failed but final file exists; publication is ambiguous"
                ) from None
            elif final_stat_state == "unknown":
                _cleanup_temp_file(transitions_fd, temp_name, temp_dev, temp_ino)
                raise MaintenanceTransitionAmbiguousError(
                    "Link failed and post-error stat was ambiguous; publication is ambiguous"
                ) from None
            else:
                _cleanup_temp_file(transitions_fd, temp_name, temp_dev, temp_ino)
                raise MaintenanceTransitionFileError(
                    "Failed to hardlink temporary file to final leaf"
                ) from None

    cleanup_attempted = False
    try:
        try:
            os.fsync(transitions_fd)
        except OSError:
            raise MaintenanceTransitionAmbiguousError(
                "Failed to fsync directory after link; publication is ambiguous"
            ) from None

        cleanup_attempted = True
        _cleanup_temp_file(transitions_fd, temp_name, temp_dev, temp_ino)

        try:
            final_bytes = read_maintenance_transition_bytes(transitions_fd, leaf)
        except Exception:
            raise MaintenanceTransitionAmbiguousError(
                "Failed to read final transition file after link; publication is ambiguous"
            ) from None

        if final_bytes is None or final_bytes != raw:
            raise MaintenanceTransitionAmbiguousError(
                "Final transition bytes mismatch after link; publication is ambiguous"
            )

        try:
            st_final = os.stat(leaf, dir_fd=transitions_fd, follow_symlinks=False)
        except OSError:
            raise MaintenanceTransitionAmbiguousError(
                "Failed to stat final transition file after link; publication is ambiguous"
            ) from None

        if (st_final.st_dev, st_final.st_ino) != (temp_dev, temp_ino):
            raise MaintenanceTransitionAmbiguousError(
                "Final transition file identity mismatch; publication is ambiguous"
            )

        if (
            stat.S_ISLNK(st_final.st_mode)
            or not stat.S_ISREG(st_final.st_mode)
            or (st_final.st_mode & 0o777) != 0o600
            or st_final.st_uid != os.geteuid()
            or st_final.st_nlink != 1
            or st_final.st_size != len(raw)
        ):
            raise MaintenanceTransitionAmbiguousError(
                "Final transition metadata mismatch after link; publication is ambiguous"
            )
    except MaintenanceTransitionAmbiguousError as amb_err:
        if not cleanup_attempted:
            cleanup_attempted = True
            try:
                _cleanup_temp_file(transitions_fd, temp_name, temp_dev, temp_ino)
            except MaintenanceTransitionAmbiguousError:
                pass
            except Exception:
                raise MaintenanceTransitionAmbiguousError(
                    "Failed to clean up temporary file; publication is ambiguous"
                ) from None
        raise amb_err
    except Exception:
        if not cleanup_attempted:
            cleanup_attempted = True
            try:
                _cleanup_temp_file(transitions_fd, temp_name, temp_dev, temp_ino)
            except MaintenanceTransitionAmbiguousError:
                pass
            except Exception:
                raise MaintenanceTransitionAmbiguousError(
                    "Failed to clean up temporary file; publication is ambiguous"
                ) from None
        raise MaintenanceTransitionAmbiguousError(
            "Unexpected post-link error; publication is ambiguous"
        ) from None
