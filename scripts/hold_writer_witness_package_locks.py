#!/usr/bin/env python3
"""Acquire native apt/dpkg locks and become the authorized mutation actor.

The production mode never leaves a separate asynchronous lock-holder process.
It acquires apt/dpkg's POSIX record locks and then ``execve`` replaces itself
with the provisioner or watchdog.  Lock ownership and the actor PID are thus
one kernel lifetime.  The surrounding systemd service uses
``KillMode=control-group`` so a fatal actor exit also kills any in-flight child
command before package exclusion can be reused by another process.
"""

from __future__ import annotations

import argparse
import fcntl
import os
from pathlib import Path
import stat
import sys
from typing import Iterable, Sequence


PACKAGE_LOCK_PATHS = (
    Path("/var/lib/dpkg/lock-frontend"),
    Path("/var/lib/dpkg/lock"),
    Path("/var/lib/apt/lists/lock"),
    Path("/var/cache/apt/archives/lock"),
)
TRUSTED_PATH = "/usr/sbin:/usr/bin:/sbin:/bin"
PRESERVED_ENVIRONMENT = (
    "INVOCATION_ID",
    "SYSTEMD_EXEC_PID",
    "WRITER_WITNESS_EXPECTED_HOST_TOOLCHAIN_INVENTORY_SHA256",
    "WRITER_WITNESS_EXPECTED_MANIFEST_SHA256",
    "WRITER_WITNESS_HARDEN_SSH",
    "WRITER_WITNESS_ALLOW_LEGACY_ACTIVATION_RECOVERY",
    "WRITER_WITNESS_PROVISION_TRANSACTION_UNIT",
    "WRITER_WITNESS_CONTROLLER_TRANSACTION_UNIT",
    "WRITER_WITNESS_REAL_HOST_MATRIX_CONFIRM",
    "WRITER_WITNESS_REAL_HOST_MATRIX_OBSERVER_CONFIRM",
    "WRITER_WITNESS_REAL_HOST_MATRIX_SCENARIO",
    "WRITER_WITNESS_PUBLIC_IP",
    "WRITER_WITNESS_RELEASE_ID",
    "WRITER_WITNESS_ROTATE_TLS",
    "WRITER_WITNESS_SOURCE_DIR",
    "WRITER_WITNESS_SSH_KEY_SOURCE_USER",
    "WRITER_WITNESS_SSH_SOURCE_IP",
    "WRITER_WITNESS_WEBAPP_FI_SOURCE_IP",
    "WRITER_WITNESS_WEBAPP_IR_SOURCE_IP",
    "WRITER_WITNESS_WHEELHOUSE",
)


class PackageLockError(RuntimeError):
    """The package-manager exclusion boundary could not be established."""


def _open_lock(
    path: Path,
    *,
    expected_uid: int,
    expected_gid: int,
    inheritable: bool = False,
) -> int:
    flags = os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    if not inheritable:
        flags |= getattr(os, "O_CLOEXEC", 0)
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
        os.set_inheritable(descriptor, inheritable)
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


def acquire_package_locks(
    paths: Iterable[Path],
    *,
    expected_uid: int,
    expected_gid: int,
    inheritable: bool,
) -> list[int]:
    descriptors: list[int] = []
    try:
        for path in paths:
            descriptors.append(
                _open_lock(
                    path,
                    expected_uid=expected_uid,
                    expected_gid=expected_gid,
                    inheritable=inheritable,
                )
            )
        return descriptors
    except BaseException:
        for descriptor in reversed(descriptors):
            try:
                fcntl.lockf(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)
        raise


def _lock_identity(path: Path) -> tuple[int, int, int]:
    metadata = path.stat(follow_symlinks=False)
    return os.major(metadata.st_dev), os.minor(metadata.st_dev), metadata.st_ino


def _observed_posix_write_locks() -> set[tuple[int, int, int, int]]:
    observed: set[tuple[int, int, int, int]] = set()
    try:
        lines = Path("/proc/locks").read_text(encoding="ascii").splitlines()
    except (OSError, UnicodeError) as exc:
        raise PackageLockError("cannot inspect kernel package-lock ownership") from exc
    for line in lines:
        fields = line.split()
        if len(fields) < 6 or fields[1:4] != ["POSIX", "ADVISORY", "WRITE"]:
            continue
        try:
            pid = int(fields[4])
            major_raw, minor_raw, inode_raw = fields[5].split(":", 2)
            observed.add(
                (pid, int(major_raw, 16), int(minor_raw, 16), int(inode_raw))
            )
        except (TypeError, ValueError):
            continue
    return observed


def assert_package_locks_owned_by(
    paths: Iterable[Path],
    *,
    owner_pid: int,
) -> None:
    if owner_pid <= 1:
        raise PackageLockError("package lock owner PID is unsafe")
    observed = _observed_posix_write_locks()
    for path in paths:
        major, minor, inode = _lock_identity(path)
        if (owner_pid, major, minor, inode) not in observed:
            raise PackageLockError(
                f"package lock is not owned by the mutation actor: {path}"
            )


def exec_with_package_locks(
    paths: Sequence[Path],
    command: Sequence[str],
    *,
    expected_uid: int,
    expected_gid: int,
) -> None:
    if not command or not Path(command[0]).is_absolute():
        raise PackageLockError("package-locked command must use an absolute executable")
    descriptors = acquire_package_locks(
        paths,
        expected_uid=expected_uid,
        expected_gid=expected_gid,
        inheritable=True,
    )
    environment = {
        "HOME": "/root",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "LOGNAME": "root",
        "PATH": TRUSTED_PATH,
        "USER": "root",
        "WRITER_WITNESS_PACKAGE_LOCK_OWNER_PID": str(os.getpid()),
    }
    for name in PRESERVED_ENVIRONMENT:
        if name in os.environ:
            environment[name] = os.environ[name]
    # The descriptors intentionally stay open over exec.  If exec fails, close
    # them before reporting the error so no stale exclusion survives.
    try:
        os.execve(command[0], list(command), environment)
    finally:
        for descriptor in reversed(descriptors):
            try:
                fcntl.lockf(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--exec", dest="exec_command", nargs=argparse.REMAINDER)
    mode.add_argument("--assert-parent-locks", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    if os.geteuid() != 0 or os.getegid() != 0:
        raise PackageLockError("Writer Witness package-lock actor must run as root")
    args = parse_args(argv)
    if args.assert_parent_locks:
        expected_text = os.environ.get("WRITER_WITNESS_PACKAGE_LOCK_OWNER_PID", "")
        if not expected_text.isdigit() or int(expected_text) != os.getppid():
            raise PackageLockError("package-lock owner environment does not match parent")
        assert_package_locks_owned_by(PACKAGE_LOCK_PATHS, owner_pid=os.getppid())
        print("package_manager_locks_held_by_actor=yes")
        return 0
    command = list(args.exec_command or ())
    if command and command[0] == "--":
        command.pop(0)
    exec_with_package_locks(
        PACKAGE_LOCK_PATHS,
        command,
        expected_uid=0,
        expected_gid=0,
    )
    raise AssertionError("execve unexpectedly returned")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, PackageLockError) as exc:
        raise SystemExit(f"Writer Witness package-lock transaction failed: {exc}") from exc
