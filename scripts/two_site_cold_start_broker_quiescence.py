#!/usr/bin/env python3
"""Dormant read-only cold-adoption Broker quiescence context manager under AE03.

Precondition: Caller MUST already hold the exclusive Supervisor lock.
This module does NOT verify the Supervisor lock and grants NO adoption
publication, startup, Provider, replay, or retry authority.
"""

from __future__ import annotations

from contextlib import contextmanager
import math
import os
from pathlib import Path
import stat
import sys
import time
from typing import Iterator

try:
    from scripts import two_site_provider_broker as broker
except ImportError:
    try:
        import two_site_provider_broker as broker  # type: ignore[no-redef]
    except ImportError as exc:
        raise ImportError("scripts.two_site_provider_broker unavailable") from exc

REQUESTS_DIRNAME: str = "requests"
CLAIMED_DIRNAME: str = "claimed"
CLIENT_LOCK_FILENAME: str = "client.lock"
STATUS_FILENAME: str = "status.json"


class ColdBrokerQuiescenceError(Exception):
    """Base exception for cold-start broker quiescence validation failures."""


class ColdBrokerQuiescencePostBodyError(ColdBrokerQuiescenceError):
    """Raised when broker quiescence validation fails after caller body execution.

    Caller must treat the adoption record state as ambiguous and retain it.
    """


__all__ = [
    "CLAIMED_DIRNAME",
    "CLIENT_LOCK_FILENAME",
    "ColdBrokerQuiescenceError",
    "ColdBrokerQuiescencePostBodyError",
    "REQUESTS_DIRNAME",
    "STATUS_FILENAME",
    "hold_idle_broker_client_lock_for_cold_adoption",
]


def _stat_fingerprint(info: os.stat_result) -> tuple[int, int, int, int, int, int, int, int]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_uid,
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _state_root_fingerprint(info: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_uid,
        info.st_nlink,
    )


def _validate_state_root_path(state_root: Path, error_cls: type[ColdBrokerQuiescenceError]) -> None:
    if not isinstance(state_root, Path):
        raise error_cls("state_root must be a pathlib.Path instance")
    if not state_root.is_absolute():
        raise error_cls("state_root must be an absolute path")
    state_root_str = str(state_root)
    if state_root_str != os.path.normpath(state_root_str):
        raise error_cls("state_root must be canonical without redundant segments")

    try:
        st = os.lstat(state_root)
    except FileNotFoundError:
        raise error_cls("state_root directory does not exist") from None
    except OSError as exc:
        raise error_cls("failed to stat state_root") from exc

    if stat.S_ISLNK(st.st_mode):
        raise error_cls("state_root must not be a symlink")
    if not stat.S_ISDIR(st.st_mode):
        raise error_cls("state_root must be a directory")
    if st.st_uid != os.geteuid():
        raise error_cls("state_root owner mismatch")
    if (st.st_mode & 0o777) != 0o700 or (st.st_mode & 0o7000) != 0:
        raise error_cls("state_root mode must be 0700")

    try:
        real_root = state_root.resolve(strict=True)
    except OSError as exc:
        raise error_cls("failed to resolve state_root") from exc
    if real_root != state_root:
        raise error_cls("state_root contains symlinks")


def _validate_client_lock_path(
    lock_path: Path, error_cls: type[ColdBrokerQuiescenceError]
) -> tuple[int, int, int, int, int]:
    try:
        st = os.lstat(lock_path)
    except FileNotFoundError:
        raise error_cls("client.lock does not exist") from None
    except OSError as exc:
        raise error_cls("failed to stat client.lock") from exc

    if stat.S_ISLNK(st.st_mode):
        raise error_cls("client.lock must not be a symlink")
    if not stat.S_ISREG(st.st_mode):
        raise error_cls("client.lock must be a regular file")
    if st.st_uid != os.geteuid():
        raise error_cls("client.lock owner mismatch")
    if (st.st_mode & 0o777) != 0o600 or (st.st_mode & 0o7000) != 0:
        raise error_cls("client.lock mode must be 0600")
    if st.st_nlink != 1:
        raise error_cls("client.lock must be single-link file")

    try:
        real_lock = lock_path.resolve(strict=True)
    except OSError as exc:
        raise error_cls("failed to resolve client.lock") from exc
    if real_lock != lock_path:
        raise error_cls("client.lock contains symlinks")
    return _state_root_fingerprint(st)


def _open_pinned_dir(
    dir_path: Path,
    error_cls: type[ColdBrokerQuiescenceError],
    dir_fd: int | None = None,
) -> int:
    try:
        st_path = os.lstat(dir_path)
    except FileNotFoundError:
        raise error_cls("directory does not exist") from None
    except OSError as exc:
        raise error_cls("failed to stat directory") from exc

    if stat.S_ISLNK(st_path.st_mode):
        raise error_cls("directory must not be a symlink")
    if not stat.S_ISDIR(st_path.st_mode):
        raise error_cls("directory must be a directory")
    if st_path.st_uid != os.geteuid():
        raise error_cls("directory owner mismatch")
    if (st_path.st_mode & 0o777) != 0o700 or (st_path.st_mode & 0o7000) != 0:
        raise error_cls("directory mode must be 0700")

    try:
        real_path = dir_path.resolve(strict=True)
    except OSError as exc:
        raise error_cls("failed to resolve directory") from exc
    if real_path != dir_path:
        raise error_cls("directory contains symlinks")

    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        if dir_fd is not None:
            fd = os.open(dir_path.name, flags, dir_fd=dir_fd)
        else:
            fd = os.open(str(dir_path), flags)
    except OSError as exc:
        raise error_cls("failed to open directory descriptor") from exc

    try:
        st_fd = os.fstat(fd)
        if (st_fd.st_dev, st_fd.st_ino) != (st_path.st_dev, st_path.st_ino):
            raise error_cls("directory descriptor identity mismatch")
        if (st_fd.st_mode & 0o777) != 0o700 or (st_fd.st_mode & 0o7000) != 0:
            raise error_cls("directory descriptor metadata invalid")
        if not stat.S_ISDIR(st_fd.st_mode) or st_fd.st_uid != os.geteuid():
            raise error_cls("directory descriptor metadata invalid")
    except Exception:
        os.close(fd)
        raise

    return fd


def _assert_dir_empty_streaming(
    dir_fd: int,
    error_cls: type[ColdBrokerQuiescenceError],
) -> None:
    try:
        os.lseek(dir_fd, 0, os.SEEK_SET)
    except (KeyboardInterrupt, SystemExit):
        raise
    except OSError as exc:
        raise error_cls("queue rewind failed") from exc

    it = None
    try:
        try:
            it = os.scandir(dir_fd)
        except (KeyboardInterrupt, SystemExit):
            raise
        except OSError as exc:
            raise error_cls("failed to open directory iterator") from exc

        try:
            for _ in it:
                raise error_cls("queue directory is not empty")
        except (KeyboardInterrupt, SystemExit):
            raise
        except ColdBrokerQuiescenceError:
            raise
        except OSError as exc:
            raise error_cls("failed to stream directory entries") from exc
    finally:
        if it is not None:
            pending_error = sys.exc_info()[1]
            try:
                it.close()
            except (KeyboardInterrupt, SystemExit):
                raise
            except Exception as exc:
                if pending_error is None:
                    raise error_cls("failed to close directory iterator") from exc


def _validate_status_path_metadata(
    status_path: Path,
    error_cls: type[ColdBrokerQuiescenceError],
) -> None:
    try:
        st = os.lstat(status_path)
    except FileNotFoundError:
        raise error_cls("status.json does not exist") from None
    except OSError as exc:
        raise error_cls("failed to stat status.json") from exc

    if stat.S_ISLNK(st.st_mode):
        raise error_cls("status.json must not be a symlink")
    if not stat.S_ISREG(st.st_mode):
        raise error_cls("status.json must be a regular file")
    if st.st_uid != os.geteuid():
        raise error_cls("status.json owner mismatch")
    if (st.st_mode & 0o777) != 0o600 or (st.st_mode & 0o7000) != 0:
        raise error_cls("status.json mode must be 0600")
    if st.st_nlink != 1:
        raise error_cls("status.json must be single-link file")

    try:
        real_status = status_path.resolve(strict=True)
    except OSError as exc:
        raise error_cls("failed to resolve status.json") from exc
    if real_status != status_path:
        raise error_cls("status.json contains symlinks")


def _validate_status_file(
    canonical_root: Path,
    status_path: Path,
    error_cls: type[ColdBrokerQuiescenceError],
) -> tuple[int, int | float]:
    _validate_status_path_metadata(status_path, error_cls)

    try:
        status = broker._read_broker_status(canonical_root, status_path)
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        raise error_cls("failed to read broker status") from exc

    _validate_status_path_metadata(status_path, error_cls)

    if not isinstance(status, dict):
        raise error_cls("broker status must be a dict")
    if status.get("schema_version") != "1.0":
        raise error_cls("broker status schema_version must be 1.0")
    if status.get("state") != "IDLE":
        raise error_cls("broker status state must be IDLE")

    pid = status.get("pid")
    if type(pid) is bool or not isinstance(pid, int) or pid <= 0:
        raise error_cls("broker status pid must be positive nonbool int")

    started_at = status.get("started_at")
    if type(started_at) is bool or not isinstance(started_at, (int, float)) or not math.isfinite(started_at):
        raise error_cls("broker status started_at must be finite numeric nonbool")

    updated_at = status.get("updated_at")
    if type(updated_at) is bool or not isinstance(updated_at, (int, float)) or not math.isfinite(updated_at):
        raise error_cls("broker status updated_at must be finite numeric nonbool")

    now_epoch = time.time()
    age = now_epoch - updated_at
    if not (0.0 <= age <= broker.STATUS_MAX_AGE_SECONDS):
        raise error_cls("broker status is not fresh")

    return pid, started_at


@contextmanager
def hold_idle_broker_client_lock_for_cold_adoption(root: Path) -> Iterator[None]:
    """Hold client lock across yield with strict pre/post IDLE broker quiescence checks.

    Precondition: Caller holds existing exclusive Supervisor lock.
    Does not verify Supervisor lock and grants no start/publication/replay authority.
    """
    body_finished = False
    lock_entered = False
    root_fd: int | None = None
    requests_fd: int | None = None
    claimed_fd: int | None = None

    if not isinstance(root, Path):
        raise ColdBrokerQuiescenceError("root must be a pathlib.Path instance")

    try:
        canonical_root = broker.canonical_root(root)
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        raise ColdBrokerQuiescenceError("failed to canonicalize root") from exc

    state_rel = broker.STATE_REL
    if isinstance(state_rel, str):
        state_rel = Path(state_rel)
    state_root = canonical_root / state_rel

    _validate_state_root_path(state_root, ColdBrokerQuiescenceError)

    lock_path = state_root / CLIENT_LOCK_FILENAME
    lock_fp_before = _validate_client_lock_path(lock_path, ColdBrokerQuiescenceError)

    try:
        lock_cm = broker.exclusive_broker_lock(lock_path)
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        raise ColdBrokerQuiescenceError("failed to initialize broker lock") from exc

    try:
        try:
            lock_cm.__enter__()
            lock_entered = True
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as exc:
            raise ColdBrokerQuiescenceError("failed to acquire broker client lock") from exc

        if _validate_client_lock_path(lock_path, ColdBrokerQuiescenceError) != lock_fp_before:
            raise ColdBrokerQuiescenceError("broker client lock identity changed during acquisition")

        try:
            root_fd = _open_pinned_dir(state_root, ColdBrokerQuiescenceError)
            requests_path = state_root / REQUESTS_DIRNAME
            claimed_path = state_root / CLAIMED_DIRNAME
            requests_fd = _open_pinned_dir(
                requests_path, ColdBrokerQuiescenceError, dir_fd=root_fd
            )
            claimed_fd = _open_pinned_dir(
                claimed_path, ColdBrokerQuiescenceError, dir_fd=root_fd
            )

            root_fp_before = _state_root_fingerprint(os.fstat(root_fd))
            requests_fp_before = _stat_fingerprint(os.fstat(requests_fd))
            claimed_fp_before = _stat_fingerprint(os.fstat(claimed_fd))

            _assert_dir_empty_streaming(requests_fd, ColdBrokerQuiescenceError)
            _assert_dir_empty_streaming(claimed_fd, ColdBrokerQuiescenceError)

            status_path = state_root / STATUS_FILENAME
            before_pid, before_started_at = _validate_status_file(
                canonical_root, status_path, ColdBrokerQuiescenceError
            )

            try:
                yield
                body_finished = True
            except (KeyboardInterrupt, SystemExit):
                raise

            if _validate_client_lock_path(lock_path, ColdBrokerQuiescencePostBodyError) != lock_fp_before:
                raise ColdBrokerQuiescencePostBodyError("broker client lock identity changed during hold")

            st_root_fd_after = os.fstat(root_fd)
            st_root_path_after = os.lstat(state_root)
            if (
                _state_root_fingerprint(st_root_fd_after) != root_fp_before
                or _state_root_fingerprint(st_root_path_after) != root_fp_before
            ):
                raise ColdBrokerQuiescencePostBodyError("state_root metadata changed during hold")

            st_req_fd_after = os.fstat(requests_fd)
            st_req_path_after = os.lstat(requests_path)
            if (
                _stat_fingerprint(st_req_fd_after) != requests_fp_before
                or _stat_fingerprint(st_req_path_after) != requests_fp_before
            ):
                raise ColdBrokerQuiescencePostBodyError("requests metadata changed during hold")

            st_claim_fd_after = os.fstat(claimed_fd)
            st_claim_path_after = os.lstat(claimed_path)
            if (
                _stat_fingerprint(st_claim_fd_after) != claimed_fp_before
                or _stat_fingerprint(st_claim_path_after) != claimed_fp_before
            ):
                raise ColdBrokerQuiescencePostBodyError("claimed metadata changed during hold")

            _assert_dir_empty_streaming(requests_fd, ColdBrokerQuiescencePostBodyError)
            _assert_dir_empty_streaming(claimed_fd, ColdBrokerQuiescencePostBodyError)

            after_pid, after_started_at = _validate_status_file(
                canonical_root, status_path, ColdBrokerQuiescencePostBodyError
            )

            if after_pid != before_pid or after_started_at != before_started_at:
                raise ColdBrokerQuiescencePostBodyError("broker identity drift")
        finally:
            first_close_exc: Exception | None = None
            for fd_val in (claimed_fd, requests_fd, root_fd):
                if fd_val is not None:
                    try:
                        os.close(fd_val)
                    except (KeyboardInterrupt, SystemExit):
                        raise
                    except Exception as exc:
                        if first_close_exc is None:
                            first_close_exc = exc
            pending_err = sys.exc_info()[1]
            if pending_err is None and first_close_exc is not None:
                err_cls = (
                    ColdBrokerQuiescencePostBodyError
                    if body_finished
                    else ColdBrokerQuiescenceError
                )
                raise err_cls("descriptor close failed") from first_close_exc
    finally:
        if lock_entered:
            pending_err = sys.exc_info()[1]
            try:
                lock_cm.__exit__(*sys.exc_info())
            except (KeyboardInterrupt, SystemExit):
                raise
            except Exception as exc:
                if pending_err is None:
                    err_cls = (
                        ColdBrokerQuiescencePostBodyError
                        if body_finished
                        else ColdBrokerQuiescenceError
                    )
                    raise err_cls("lock release failed") from exc
