#!/usr/bin/env python3
"""Hold the native apt/dpkg POSIX locks for one Witness activation.

The parent provisioner keeps this process's stdin open for the complete
activation.  EOF, a signal, or parent death closes every descriptor and
releases all locks.  ``fcntl.lockf`` deliberately matches apt/dpkg's native
locking protocol; BSD ``flock`` on the same paths would not exclude them.
"""

from __future__ import annotations

import fcntl
import os
from pathlib import Path
import stat
import sys
from typing import BinaryIO, Iterable, TextIO


PACKAGE_LOCK_PATHS = (
    Path("/var/lib/dpkg/lock-frontend"),
    Path("/var/lib/dpkg/lock"),
    Path("/var/lib/apt/lists/lock"),
    Path("/var/cache/apt/archives/lock"),
)


class PackageLockError(RuntimeError):
    """The package-manager exclusion boundary could not be established."""


def _open_lock(path: Path, *, expected_uid: int, expected_gid: int) -> int:
    flags = os.O_RDWR | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise PackageLockError(f"package lock cannot be securely opened: {path}") from exc
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != expected_uid
            or metadata.st_gid != expected_gid
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) & 0o022
        ):
            raise PackageLockError(f"package lock metadata is unsafe: {path}")
        fcntl.lockf(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


def hold_package_locks(
    paths: Iterable[Path],
    *,
    control: BinaryIO,
    ready: TextIO,
    expected_uid: int,
    expected_gid: int,
) -> None:
    descriptors: list[int] = []
    try:
        for path in paths:
            descriptors.append(
                _open_lock(
                    path,
                    expected_uid=expected_uid,
                    expected_gid=expected_gid,
                )
            )
        ready.write("package_manager_locks_held=yes\n")
        ready.flush()
        while control.read(64 * 1024):
            pass
    finally:
        for descriptor in reversed(descriptors):
            try:
                fcntl.lockf(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)


def main() -> int:
    if os.geteuid() != 0 or os.getegid() != 0:
        raise SystemExit("Writer Witness package lock holder must run as root")
    try:
        hold_package_locks(
            PACKAGE_LOCK_PATHS,
            control=sys.stdin.buffer,
            ready=sys.stdout,
            expected_uid=0,
            expected_gid=0,
        )
    except (OSError, PackageLockError) as exc:
        raise SystemExit(f"Writer Witness package lock acquisition failed: {exc}") from exc
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
