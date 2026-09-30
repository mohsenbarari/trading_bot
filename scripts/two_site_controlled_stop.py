"""Controlled STOP publication helper for two-site architecture.

Provides durable, crash-resilient publication of the STOP marker
under .local/two-site-refactor/autopilot/STOP while holding the
exclusive proposal runner lock.
"""

from __future__ import annotations

import argparse
import errno
import fcntl
import math
import os
from pathlib import Path
import stat
import sys
import time
from typing import Union

DEFAULT_LOCK_WAIT_SECONDS: float = 1200.0
STOP_MARKER: bytes = b"STOP\n"
GENERIC_CLI_ERROR_MESSAGE: str = "ERROR: controlled stop failed\n"

_OPEN_DIR_FLAGS: int = (
    os.O_RDONLY
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_NOFOLLOW", 0)
)


class ControlledStopError(Exception):
    """Raised when controlled stop publication, lock acquisition, or validation fails."""


def _validate_and_canonicalize_root(root: Union[str, Path]) -> Path:
    if isinstance(root, bool) or not isinstance(root, (str, Path)):
        raise ControlledStopError("worktree root must be a string or Path")
    root_path = Path(root)
    if not root_path.is_absolute():
        raise ControlledStopError(f"worktree root must be an absolute path: {root_path}")

    for ancestor in root_path.parents:
        try:
            anc_stat = os.lstat(ancestor)
        except OSError as exc:
            raise ControlledStopError(f"cannot inspect ancestor {ancestor}: {exc}") from exc
        if stat.S_ISLNK(anc_stat.st_mode):
            raise ControlledStopError(f"root ancestor {ancestor} is a symlink")

    try:
        root_stat = os.lstat(root_path)
    except OSError as exc:
        raise ControlledStopError(f"cannot inspect worktree root {root_path}: {exc}") from exc

    if stat.S_ISLNK(root_stat.st_mode):
        raise ControlledStopError(f"worktree root {root_path} is a symlink")
    if not stat.S_ISDIR(root_stat.st_mode):
        raise ControlledStopError(f"worktree root {root_path} is not a directory")
    if root_stat.st_uid != os.geteuid():
        raise ControlledStopError(
            f"worktree root {root_path} owner {root_stat.st_uid} does not match current euid {os.geteuid()}"
        )
    if root_stat.st_mode & 0o022 != 0:
        raise ControlledStopError(
            f"worktree root {root_path} has group or world write permissions: {oct(root_stat.st_mode)}"
        )

    resolved = root_path.resolve()
    if resolved != root_path:
        raise ControlledStopError(f"worktree root {root_path} is not canonical (resolves to {resolved})")

    return root_path


def _validate_lock_wait(lock_wait_seconds: float) -> float:
    if isinstance(lock_wait_seconds, bool) or not isinstance(lock_wait_seconds, (int, float)):
        raise ControlledStopError("lock_wait_seconds must be a finite positive nonboolean number")
    if math.isnan(lock_wait_seconds) or math.isinf(lock_wait_seconds) or lock_wait_seconds <= 0:
        raise ControlledStopError("lock_wait_seconds must be a finite positive nonboolean number")
    return float(lock_wait_seconds)


def _ensure_pinned_child_dir(
    parent_fd: int, parent_path: Path, comp: str, open_fds: list[int]
) -> tuple[int, Path]:
    child_path = parent_path / comp
    created = False
    try:
        st_pre = os.stat(comp, dir_fd=parent_fd, follow_symlinks=False)
        if stat.S_ISLNK(st_pre.st_mode):
            raise ControlledStopError(f"storage component {comp} is a symlink")
    except FileNotFoundError:
        try:
            os.mkdir(comp, 0o700, dir_fd=parent_fd)
            created = True
        except OSError as exc:
            raise ControlledStopError(f"cannot create directory {comp}: {exc}") from exc
    except OSError as exc:
        if isinstance(exc, ControlledStopError):
            raise
        if exc.errno == errno.ELOOP:
            raise ControlledStopError(f"storage component {comp} is a symlink: {exc}") from exc
        raise ControlledStopError(f"cannot inspect directory component {comp}: {exc}") from exc

    try:
        child_fd = os.open(comp, _OPEN_DIR_FLAGS, dir_fd=parent_fd)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise ControlledStopError(f"storage component {comp} is a symlink: {exc}") from exc
        raise ControlledStopError(f"cannot open directory {comp}: {exc}") from exc
    open_fds.append(child_fd)

    try:
        f_stat = os.fstat(child_fd)
        p_stat = os.stat(comp, dir_fd=parent_fd, follow_symlinks=False)
        l_stat = os.lstat(child_path)
    except OSError as exc:
        raise ControlledStopError(f"cannot stat {child_path}: {exc}") from exc

    if stat.S_ISLNK(f_stat.st_mode) or stat.S_ISLNK(p_stat.st_mode) or stat.S_ISLNK(l_stat.st_mode):
        raise ControlledStopError(f"storage component {comp} is a symlink")
    if not stat.S_ISDIR(f_stat.st_mode) or not stat.S_ISDIR(p_stat.st_mode):
        raise ControlledStopError(f"storage component {comp} is not a directory")
    if f_stat.st_uid != os.geteuid() or p_stat.st_uid != os.geteuid() or l_stat.st_uid != os.geteuid():
        raise ControlledStopError(f"storage component {comp} owner mismatch")
    if (f_stat.st_mode & 0o022) != 0 or (p_stat.st_mode & 0o022) != 0 or (l_stat.st_mode & 0o022) != 0:
        raise ControlledStopError(f"storage component {comp} has group or world write permission")
    if (f_stat.st_dev, f_stat.st_ino, f_stat.st_uid, f_stat.st_mode) != (
        p_stat.st_dev, p_stat.st_ino, p_stat.st_uid, p_stat.st_mode
    ) or (f_stat.st_dev, f_stat.st_ino) != (l_stat.st_dev, l_stat.st_ino):
        raise ControlledStopError(f"storage component {comp} descriptor and path identity mismatch")

    if created:
        try:
            os.fsync(child_fd)
            os.fsync(parent_fd)
        except OSError as exc:
            raise ControlledStopError(
                f"cannot fsync newly created directory {comp} or parent: {exc}"
            ) from exc

    return child_fd, child_path


def publish_controlled_stop(
    root: Union[str, Path], lock_wait_seconds: float = DEFAULT_LOCK_WAIT_SECONDS
) -> Path:
    """Publish durable STOP marker while holding exclusive proposal runner lock.

    Args:
        root: Canonical absolute path to worktree root.
        lock_wait_seconds: Timeout in seconds for waiting on .runner.lock.
            Must be a finite positive nonboolean number (defaults to 1200.0).

    Returns:
        Path to durable STOP marker.

    Raises:
        ControlledStopError: If validation fails, timeout expires, or an error occurs.
    """
    c_root = _validate_and_canonicalize_root(root)
    wait_sec = _validate_lock_wait(lock_wait_seconds)

    open_fds: list[int] = []
    lock_fd: int = -1
    try:
        try:
            root_fd = os.open(c_root, _OPEN_DIR_FLAGS)
        except OSError as exc:
            raise ControlledStopError(f"cannot open worktree root {c_root}: {exc}") from exc
        open_fds.append(root_fd)

        try:
            r_fstat = os.fstat(root_fd)
            r_lstat = os.lstat(c_root)
        except OSError as exc:
            raise ControlledStopError(f"cannot stat worktree root {c_root}: {exc}") from exc

        if (
            stat.S_ISLNK(r_fstat.st_mode)
            or not stat.S_ISDIR(r_fstat.st_mode)
            or r_fstat.st_uid != os.geteuid()
            or (r_fstat.st_mode & 0o022) != 0
            or (r_fstat.st_dev, r_fstat.st_ino) != (r_lstat.st_dev, r_lstat.st_ino)
        ):
            raise ControlledStopError("worktree root descriptor validation failed")

        fd_local, path_local = _ensure_pinned_child_dir(root_fd, c_root, ".local", open_fds)
        fd_refactor, path_refactor = _ensure_pinned_child_dir(
            fd_local, path_local, "two-site-refactor", open_fds
        )
        fd_proposals, path_proposals = _ensure_pinned_child_dir(
            fd_refactor, path_refactor, "proposals", open_fds
        )

        lock_name = ".runner.lock"
        lock_path = path_proposals / lock_name
        lock_flags = (
            os.O_RDWR
            | os.O_CREAT
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            lock_fd = os.open(lock_name, lock_flags, 0o600, dir_fd=fd_proposals)
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                raise ControlledStopError(f"proposal runner lock is a symlink: {exc}") from exc
            raise ControlledStopError(f"cannot open proposal runner lock: {exc}") from exc
        open_fds.append(lock_fd)

        try:
            f_stat = os.fstat(lock_fd)
            p_stat = os.stat(lock_name, dir_fd=fd_proposals, follow_symlinks=False)
            l_stat = os.lstat(lock_path)
        except OSError as exc:
            raise ControlledStopError(f"cannot stat proposal runner lock: {exc}") from exc

        if stat.S_ISLNK(f_stat.st_mode) or stat.S_ISLNK(p_stat.st_mode) or stat.S_ISLNK(l_stat.st_mode):
            raise ControlledStopError("proposal runner lock is a symlink")
        if not stat.S_ISREG(f_stat.st_mode) or not stat.S_ISREG(p_stat.st_mode):
            raise ControlledStopError("proposal runner lock is not a regular file")
        if f_stat.st_uid != os.geteuid() or p_stat.st_uid != os.geteuid() or l_stat.st_uid != os.geteuid():
            raise ControlledStopError("proposal runner lock owner mismatch")
        if f_stat.st_nlink != 1 or p_stat.st_nlink != 1 or l_stat.st_nlink != 1:
            raise ControlledStopError("proposal runner lock must have single link count")
        if (f_stat.st_mode & 0o077) != 0 or (p_stat.st_mode & 0o077) != 0 or (l_stat.st_mode & 0o077) != 0:
            raise ControlledStopError("proposal runner lock has non-restrictive mode")
        if (
            (f_stat.st_dev, f_stat.st_ino) != (p_stat.st_dev, p_stat.st_ino)
            or (f_stat.st_dev, f_stat.st_ino) != (l_stat.st_dev, l_stat.st_ino)
        ):
            raise ControlledStopError(
                "proposal runner lock descriptor and path identity mismatch"
            )

        start_mono = time.monotonic()
        deadline = start_mono + wait_sec
        while True:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                now = time.monotonic()
                if now >= deadline:
                    raise ControlledStopError(
                        f"timed out waiting for proposal runner lock after {wait_sec} seconds"
                    ) from None
                sleep_dur = min(0.05, max(0.001, deadline - now))
                time.sleep(sleep_dur)

        try:
            post_fstat = os.fstat(lock_fd)
            post_pstat = os.stat(lock_name, dir_fd=fd_proposals, follow_symlinks=False)
            post_lstat = os.lstat(lock_path)
        except OSError as exc:
            raise ControlledStopError(f"cannot verify lock file after acquisition: {exc}") from exc

        if (
            stat.S_ISLNK(post_pstat.st_mode)
            or stat.S_ISLNK(post_lstat.st_mode)
            or not stat.S_ISREG(post_fstat.st_mode)
            or not stat.S_ISREG(post_pstat.st_mode)
            or post_fstat.st_uid != os.geteuid()
            or post_pstat.st_uid != os.geteuid()
            or post_lstat.st_uid != os.geteuid()
            or post_fstat.st_nlink != 1
            or post_pstat.st_nlink != 1
            or post_lstat.st_nlink != 1
            or (post_fstat.st_mode & 0o077) != 0
            or (post_pstat.st_mode & 0o077) != 0
            or (post_lstat.st_mode & 0o077) != 0
            or (post_fstat.st_dev, post_fstat.st_ino) != (f_stat.st_dev, f_stat.st_ino)
            or (post_pstat.st_dev, post_pstat.st_ino) != (f_stat.st_dev, f_stat.st_ino)
            or (post_lstat.st_dev, post_lstat.st_ino) != (f_stat.st_dev, f_stat.st_ino)
        ):
            raise ControlledStopError(
                "lock inode identity changed or invalidated during acquisition"
            )

        fd_autopilot, path_autopilot = _ensure_pinned_child_dir(
            fd_refactor, path_refactor, "autopilot", open_fds
        )

        stop_name = "STOP"
        stop_path = path_autopilot / stop_name
        stop_create_flags = (
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )

        try:
            stop_fd = os.open(stop_name, stop_create_flags, 0o600, dir_fd=fd_autopilot)
            is_new = True
        except FileExistsError:
            is_new = False
            stop_fd = -1
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                raise ControlledStopError(f"STOP file is a symlink: {exc}") from exc
            raise ControlledStopError(f"cannot open STOP file: {exc}") from exc

        if is_new:
            # Pinned autopilot dir_fd, created STOP O_CREAT|O_EXCL|O_NOFOLLOW mode 0600
            # CRITICAL: Once STOP is visible, NEVER unlink it on partial write,
            # file fsync, directory fsync, or verification failure!
            try:
                try:
                    st = os.fstat(stop_fd)
                    pst = os.stat(stop_name, dir_fd=fd_autopilot, follow_symlinks=False)
                    lst = os.lstat(stop_path)
                except OSError as exc:
                    raise ControlledStopError(f"cannot stat STOP file: {exc}") from exc

                if (
                    stat.S_ISLNK(pst.st_mode)
                    or stat.S_ISLNK(lst.st_mode)
                    or not stat.S_ISREG(st.st_mode)
                    or not stat.S_ISREG(pst.st_mode)
                    or st.st_uid != os.geteuid()
                    or pst.st_uid != os.geteuid()
                    or lst.st_uid != os.geteuid()
                    or st.st_nlink != 1
                    or pst.st_nlink != 1
                    or lst.st_nlink != 1
                    or (st.st_mode & 0o077) != 0
                    or (pst.st_mode & 0o077) != 0
                    or (lst.st_mode & 0o077) != 0
                    or (st.st_dev, st.st_ino) != (pst.st_dev, pst.st_ino)
                    or (st.st_dev, st.st_ino) != (lst.st_dev, lst.st_ino)
                ):
                    raise ControlledStopError("newly created STOP inode verification failed")

                try:
                    written = os.write(stop_fd, STOP_MARKER)
                except OSError as exc:
                    raise ControlledStopError(f"cannot write to STOP file: {exc}") from exc

                if written != len(STOP_MARKER):
                    raise ControlledStopError(
                        f"partial write to STOP file: wrote {written} of {len(STOP_MARKER)} bytes"
                    )

                try:
                    os.fsync(stop_fd)
                except OSError as exc:
                    raise ControlledStopError(
                        f"cannot fsync newly created STOP file: {exc}"
                    ) from exc

                try:
                    os.fsync(fd_autopilot)
                except OSError as exc:
                    raise ControlledStopError(
                        f"cannot fsync autopilot directory after creating STOP: {exc}"
                    ) from exc
            finally:
                try:
                    os.close(stop_fd)
                except OSError:
                    pass
        else:
            try:
                pst_pre = os.stat(stop_name, dir_fd=fd_autopilot, follow_symlinks=False)
                if stat.S_ISLNK(pst_pre.st_mode):
                    raise ControlledStopError("existing STOP is a symlink")
            except OSError as exc:
                if isinstance(exc, ControlledStopError):
                    raise
                if exc.errno == errno.ELOOP:
                    raise ControlledStopError(f"existing STOP is a symlink: {exc}") from exc
                raise ControlledStopError(f"cannot inspect existing STOP file: {exc}") from exc

            existing_flags = (
                os.O_RDWR
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0)
            )
            try:
                exist_fd = os.open(stop_name, existing_flags, dir_fd=fd_autopilot)
            except OSError as exc:
                if exc.errno == errno.ELOOP:
                    raise ControlledStopError(f"existing STOP is a symlink: {exc}") from exc
                raise ControlledStopError(f"cannot open existing STOP file: {exc}") from exc
            try:
                try:
                    fst = os.fstat(exist_fd)
                    pst = os.stat(stop_name, dir_fd=fd_autopilot, follow_symlinks=False)
                    lst = os.lstat(stop_path)
                except OSError as exc:
                    raise ControlledStopError(f"cannot stat existing STOP file: {exc}") from exc

                if stat.S_ISLNK(pst.st_mode) or stat.S_ISLNK(fst.st_mode) or stat.S_ISLNK(lst.st_mode):
                    raise ControlledStopError("existing STOP is a symlink")
                if not stat.S_ISREG(fst.st_mode) or not stat.S_ISREG(pst.st_mode):
                    raise ControlledStopError("existing STOP is not a regular file")
                if fst.st_uid != os.geteuid() or pst.st_uid != os.geteuid() or lst.st_uid != os.geteuid():
                    raise ControlledStopError("existing STOP owner mismatch")
                if fst.st_nlink != 1 or pst.st_nlink != 1 or lst.st_nlink != 1:
                    raise ControlledStopError("existing STOP must have single link count")
                if stat.S_IMODE(fst.st_mode) != 0o600 or stat.S_IMODE(pst.st_mode) != 0o600:
                    raise ControlledStopError(
                        f"existing STOP must have mode 0600, got {oct(stat.S_IMODE(fst.st_mode))}"
                    )
                if (
                    (fst.st_dev, fst.st_ino) != (pst.st_dev, pst.st_ino)
                    or (fst.st_dev, fst.st_ino) != (lst.st_dev, lst.st_ino)
                ):
                    raise ControlledStopError(
                        "existing STOP descriptor and path identity mismatch"
                    )

                try:
                    os.fsync(exist_fd)
                    os.fsync(fd_autopilot)
                except OSError as exc:
                    raise ControlledStopError(
                        f"cannot fsync existing STOP or parent: {exc}"
                    ) from exc
            finally:
                try:
                    os.close(exist_fd)
                except OSError:
                    pass

        return stop_path

    finally:
        if lock_fd != -1:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            except OSError:
                pass
        for fd in reversed(open_fds):
            try:
                os.close(fd)
            except OSError:
                pass


request_controlled_stop = publish_controlled_stop
write_controlled_stop = publish_controlled_stop


class _ControlledStopArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ControlledStopError(message)

    def exit(self, status: int = 0, message: str | None = None) -> None:
        raise ControlledStopError(message or "argument parsing failed")


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for publishing controlled STOP marker."""
    parser = _ControlledStopArgumentParser(
        prog="two_site_controlled_stop",
        description="Controlled STOP publication helper",
        add_help=False,
    )
    parser.add_argument("--root", required=True)
    parser.add_argument(
        "--lock-wait-seconds",
        type=float,
        default=DEFAULT_LOCK_WAIT_SECONDS,
    )
    try:
        if argv is None:
            argv = sys.argv[1:]
        args = parser.parse_args(argv)
        publish_controlled_stop(args.root, lock_wait_seconds=args.lock_wait_seconds)
        return 0
    except (ControlledStopError, Exception, SystemExit):
        sys.stderr.write(GENERIC_CLI_ERROR_MESSAGE)
        return 1


__all__ = [
    "ControlledStopError",
    "DEFAULT_LOCK_WAIT_SECONDS",
    "GENERIC_CLI_ERROR_MESSAGE",
    "STOP_MARKER",
    "main",
    "publish_controlled_stop",
    "request_controlled_stop",
    "write_controlled_stop",
]

if __name__ == "__main__":
    sys.exit(main())
