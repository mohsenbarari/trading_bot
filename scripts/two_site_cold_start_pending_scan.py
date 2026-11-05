#!/usr/bin/env python3
"""Dormant read-only bounded cold-adoption pending-artifact scanner under AE03.

Precondition: Caller MUST already hold the exclusive Supervisor lock and,
when used for issuance/start, a separate Broker quiescence gate.

Authority constraints:
1. Dormant and read-only: contains NO publication, NO startup hook,
   NO Broker/Provider execution, NO wake, NO retry, and NO replay authority.
2. Does NOT authorize startup; this is a narrow primitive, not the complete
   startup gate.
3. Zero mutation: strictly read-only audit. Does not create, modify, chmod,
   or delete any file or directory.
4. Fail-closed: safely opens canonical absolute euid-owned 0700 state_root
   and its euid-owned 0700 runs directory using O_NOFOLLOW|O_DIRECTORY and pinned
   dir_fd. Missing runs directory is an error, not clean evidence.
5. Rejects any pending cold-adoption marker (quota-wait.json, quota-resume-claim.json,
   quota-resume-lineage-snapshot.json, quota-resume-submission.json, accepted-lineage.json,
   final-ack.json, or temporary/unknown variants) without reading its contents.
"""

from __future__ import annotations

import os
from pathlib import Path
import stat
import sys
from typing import Any

try:
    from scripts.two_site_provider_broker import CYCLE_RUN_RE
except ImportError:
    try:
        from two_site_provider_broker import CYCLE_RUN_RE  # type: ignore[no-redef]
    except ImportError as exc:
        raise ImportError("scripts.two_site_provider_broker.CYCLE_RUN_RE unavailable") from exc

if not hasattr(CYCLE_RUN_RE, "match"):
    raise ImportError("CYCLE_RUN_RE must be a compiled regular expression")

RUNS_DIRNAME: str = "runs"
MAX_RUNS_ENTRIES: int = 512
MAX_RUN_CHILD_ENTRIES: int = 128
OPAQUE_RUN_DIRECTORIES: frozenset[str] = frozenset({
    "coordinator-qoder-state",
    "owned-broker-completion",
    "output-contract-repair",
    "root-packet-cap-correction",
})

PENDING_EXACT_NAMES: frozenset[str] = frozenset({
    "quota-wait.json",
    "quota-resume-claim.json",
    "quota-resume-lineage-snapshot.json",
    "quota-resume-submission.json",
    "accepted-lineage.json",
    "final-ack.json",
})


class ColdStartPendingScanError(Exception):
    """Base exception for cold-start pending artifact scan failures."""


class PendingColdAdoptionError(ColdStartPendingScanError):
    """Raised when pending cold adoption artifacts or unsafe run topologies are detected."""


__all__ = [
    "ColdStartPendingScanError",
    "CYCLE_RUN_RE",
    "MAX_RUNS_ENTRIES",
    "MAX_RUN_CHILD_ENTRIES",
    "PENDING_EXACT_NAMES",
    "PendingColdAdoptionError",
    "OPAQUE_RUN_DIRECTORIES",
    "RUNS_DIRNAME",
    "assert_no_pending_cold_adoption_artifacts_locked",
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


def _is_pending_artifact_name(name: str) -> bool:
    if name in PENDING_EXACT_NAMES:
        return True
    clean = name.lstrip(".")
    if clean.startswith("quota-") or "quota-" in clean:
        return True
    if clean.startswith("final-ack") or "final-ack" in clean:
        return True
    if clean.startswith("accepted-lineage") or "accepted-lineage" in clean:
        return True
    return False


def _is_valid_cycle_run_id(name: str) -> bool:
    if not isinstance(name, str):
        return False
    if hasattr(CYCLE_RUN_RE, "fullmatch"):
        return bool(CYCLE_RUN_RE.fullmatch(name))
    match = CYCLE_RUN_RE.match(name)
    return bool(match and match.group(0) == name)


def _collect_bounded_entry_names(dir_fd: int, max_entries: int, cap_error_msg: str) -> list[str]:
    names: list[str] = []
    it = None
    try:
        try:
            it = os.scandir(dir_fd)
        except OSError:
            raise PendingColdAdoptionError("Failed to open directory scan iterator") from None
        try:
            for entry in it:
                if len(names) >= max_entries:
                    raise PendingColdAdoptionError(cap_error_msg)
                names.append(entry.name)
        except OSError:
            raise PendingColdAdoptionError("Failed to iterate directory entries") from None
    finally:
        if it is not None:
            pending_error = sys.exc_info()[1]
            try:
                it.close()
            except (KeyboardInterrupt, SystemExit):
                raise
            except Exception as exc:
                if pending_error is None:
                    raise PendingColdAdoptionError("Failed to close directory scan iterator") from exc
    return sorted(names)


def _open_validated_state_root(state_root: Path) -> int:
    if not isinstance(state_root, Path):
        raise PendingColdAdoptionError("state_root must be a pathlib.Path instance")
    if not state_root.is_absolute():
        raise PendingColdAdoptionError("state_root must be an absolute path")
    state_root_str = str(state_root)
    if state_root_str != os.path.normpath(state_root_str):
        raise PendingColdAdoptionError("state_root must be canonical without redundant segments")
    try:
        st = os.lstat(state_root)
    except FileNotFoundError:
        raise PendingColdAdoptionError("state_root directory does not exist") from None
    except OSError:
        raise PendingColdAdoptionError("Failed to stat state_root") from None

    if stat.S_ISLNK(st.st_mode):
        raise PendingColdAdoptionError("state_root must not be a symlink")
    if not stat.S_ISDIR(st.st_mode):
        raise PendingColdAdoptionError("state_root must be a directory")
    if st.st_uid != os.geteuid():
        raise PendingColdAdoptionError("state_root owner mismatch")
    if (st.st_mode & 0o777) != 0o700:
        raise PendingColdAdoptionError("state_root mode must be 0700")

    try:
        real_root = state_root.resolve(strict=True)
    except OSError:
        raise PendingColdAdoptionError("Failed to resolve state_root") from None
    if real_root != state_root:
        raise PendingColdAdoptionError("state_root contains symlinks")

    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        root_fd = os.open(str(state_root), flags)
    except OSError:
        raise PendingColdAdoptionError("Failed to open state_root descriptor") from None

    try:
        st_fd = os.fstat(root_fd)
        if (st_fd.st_dev, st_fd.st_ino) != (st.st_dev, st.st_ino):
            raise PendingColdAdoptionError("state_root descriptor identity mismatch")
        if (st_fd.st_mode & 0o777) != 0o700 or not stat.S_ISDIR(st_fd.st_mode) or st_fd.st_uid != os.geteuid():
            raise PendingColdAdoptionError("state_root descriptor metadata invalid")
    except Exception:
        os.close(root_fd)
        raise

    return root_fd


def _open_validated_runs_dir(root_fd: int, state_root: Path) -> int:
    runs_path = state_root / RUNS_DIRNAME
    try:
        st_runs = os.stat(RUNS_DIRNAME, dir_fd=root_fd, follow_symlinks=False)
    except FileNotFoundError:
        raise PendingColdAdoptionError("runs directory does not exist") from None
    except OSError:
        raise PendingColdAdoptionError("Failed to stat runs directory via dir_fd") from None

    try:
        st_path = os.lstat(runs_path)
    except FileNotFoundError:
        raise PendingColdAdoptionError("runs directory does not exist") from None
    except OSError:
        raise PendingColdAdoptionError("Failed to lstat runs directory") from None

    if _stat_fingerprint(st_path) != _stat_fingerprint(st_runs):
        raise PendingColdAdoptionError("runs directory path and dir_fd stat mismatch")
    if stat.S_ISLNK(st_runs.st_mode):
        raise PendingColdAdoptionError("runs directory must not be a symlink")
    if not stat.S_ISDIR(st_runs.st_mode):
        raise PendingColdAdoptionError("runs must be a directory")
    if st_runs.st_uid != os.geteuid():
        raise PendingColdAdoptionError("runs directory owner mismatch")
    if (st_runs.st_mode & 0o777) != 0o700:
        raise PendingColdAdoptionError("runs directory mode must be 0700")

    try:
        real_runs = runs_path.resolve(strict=True)
    except OSError:
        raise PendingColdAdoptionError("Failed to resolve runs directory") from None
    if real_runs != runs_path:
        raise PendingColdAdoptionError("runs directory contains symlinks")

    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        runs_fd = os.open(RUNS_DIRNAME, flags, dir_fd=root_fd)
    except OSError:
        raise PendingColdAdoptionError("Failed to open runs directory descriptor") from None

    try:
        st_fd = os.fstat(runs_fd)
        if (st_fd.st_dev, st_fd.st_ino) != (st_runs.st_dev, st_runs.st_ino):
            raise PendingColdAdoptionError("runs directory descriptor identity mismatch")
        if (st_fd.st_mode & 0o777) != 0o700 or not stat.S_ISDIR(st_fd.st_mode) or st_fd.st_uid != os.geteuid():
            raise PendingColdAdoptionError("runs directory descriptor metadata invalid")
    except Exception:
        os.close(runs_fd)
        raise

    return runs_fd


def assert_no_pending_cold_adoption_artifacts_locked(state_root: Path) -> None:
    """Audit runs directory for pending cold-adoption artifacts under exclusive Supervisor lock.

    Precondition: Caller holds the existing exclusive Supervisor lock throughout
    execution and, when used for issuance/start, a separate Broker quiescence gate.
    This function grants NO publication/start/retry/replay authority.
    """
    root_fd = _open_validated_state_root(state_root)
    try:
        st_root_before = os.fstat(root_fd)
        runs_fd = _open_validated_runs_dir(root_fd, state_root)
        try:
            st_runs_before = os.fstat(runs_fd)
            runs_path = state_root / RUNS_DIRNAME

            run_entries = _collect_bounded_entry_names(
                runs_fd,
                MAX_RUNS_ENTRIES,
                "Runs directory entry count exceeds maximum",
            )

            for run_entry in run_entries:
                if not _is_valid_cycle_run_id(run_entry):
                    raise PendingColdAdoptionError("Run entry name is not a valid cycle run ID")

                try:
                    st_entry = os.stat(run_entry, dir_fd=runs_fd, follow_symlinks=False)
                except OSError:
                    raise PendingColdAdoptionError("Failed to stat run entry via dir_fd") from None

                run_path = runs_path / run_entry
                try:
                    st_path = os.lstat(run_path)
                except OSError:
                    raise PendingColdAdoptionError("Failed to lstat run entry path") from None

                if _stat_fingerprint(st_path) != _stat_fingerprint(st_entry):
                    raise PendingColdAdoptionError("Run entry path and dir_fd stat mismatch")
                if stat.S_ISLNK(st_entry.st_mode):
                    raise PendingColdAdoptionError("Run entry must not be a symlink")
                if not stat.S_ISDIR(st_entry.st_mode):
                    raise PendingColdAdoptionError("Run entry must be a directory")
                if st_entry.st_uid != os.geteuid():
                    raise PendingColdAdoptionError("Run entry owner mismatch")

                mode = st_entry.st_mode & 0o777
                if mode not in (0o700, 0o755):
                    raise PendingColdAdoptionError("Run entry mode must be 0700 or 0755")

                try:
                    real_run_path = run_path.resolve(strict=True)
                except OSError:
                    raise PendingColdAdoptionError("Failed to resolve run path") from None
                if real_run_path != run_path:
                    raise PendingColdAdoptionError("Run path contains symlinks")

                flags = (
                    os.O_RDONLY
                    | getattr(os, "O_DIRECTORY", 0)
                    | getattr(os, "O_CLOEXEC", 0)
                    | getattr(os, "O_NOFOLLOW", 0)
                )
                try:
                    run_fd = os.open(run_entry, flags, dir_fd=runs_fd)
                except OSError:
                    raise PendingColdAdoptionError("Failed to open run directory descriptor") from None

                try:
                    st_run_before = os.fstat(run_fd)
                    if (st_run_before.st_dev, st_run_before.st_ino) != (st_entry.st_dev, st_entry.st_ino):
                        raise PendingColdAdoptionError("Run directory descriptor identity mismatch")
                    if (st_run_before.st_mode & 0o777) not in (0o700, 0o755):
                        raise PendingColdAdoptionError("Run directory descriptor mode invalid")
                    if not stat.S_ISDIR(st_run_before.st_mode) or st_run_before.st_uid != os.geteuid():
                        raise PendingColdAdoptionError("Run directory descriptor metadata invalid")

                    child_entries = _collect_bounded_entry_names(
                        run_fd,
                        MAX_RUN_CHILD_ENTRIES,
                        "Run child entry count exceeds maximum",
                    )

                    opaque_directories: list[tuple[Path, tuple[int, ...]]] = []
                    for child_name in child_entries:
                        if _is_pending_artifact_name(child_name):
                            raise PendingColdAdoptionError("Pending cold adoption artifact detected")

                        try:
                            st_child = os.stat(child_name, dir_fd=run_fd, follow_symlinks=False)
                        except OSError:
                            raise PendingColdAdoptionError("Failed to stat run child entry via dir_fd") from None

                        child_path = run_path / child_name
                        try:
                            st_child_path = os.lstat(child_path)
                        except OSError:
                            raise PendingColdAdoptionError("Failed to lstat run child path") from None

                        if _stat_fingerprint(st_child_path) != _stat_fingerprint(st_child):
                            raise PendingColdAdoptionError("Run child path and dir_fd stat mismatch")
                        if stat.S_ISLNK(st_child.st_mode):
                            raise PendingColdAdoptionError("Run child must not be a symlink")
                        if stat.S_ISDIR(st_child.st_mode):
                            # Qoder and its bounded repair/correction routes need
                            # private per-run directories. They are opaque runtime
                            # state, never cold-adoption records. Admit only this
                            # closed name set and bind every directory identity
                            # across the scan; all other nesting remains closed.
                            if child_name not in OPAQUE_RUN_DIRECTORIES:
                                raise PendingColdAdoptionError("Run child must not be a nested directory")
                            if (st_child.st_uid != os.geteuid()
                                    or (st_child.st_mode & 0o777) != 0o700
                                    or (st_child.st_mode & 0o7000) != 0):
                                raise PendingColdAdoptionError("Opaque run directory metadata invalid")
                            try:
                                real_child = child_path.resolve(strict=True)
                            except OSError:
                                raise PendingColdAdoptionError(
                                    "Failed to resolve opaque run directory"
                                ) from None
                            if real_child != child_path:
                                raise PendingColdAdoptionError("Opaque run directory contains symlinks")
                            opaque_directories.append((child_path, _stat_fingerprint(st_child)))
                            continue
                        if not stat.S_ISREG(st_child.st_mode):
                            raise PendingColdAdoptionError("Run child must be a regular file")
                        if st_child.st_uid != os.geteuid():
                            raise PendingColdAdoptionError("Run child owner mismatch")
                        if (st_child.st_mode & 0o7000) != 0:
                            raise PendingColdAdoptionError("Run child must not have special mode bits")
                        if st_child.st_nlink != 1:
                            raise PendingColdAdoptionError("Run child must be single-link file")

                    for opaque_path, opaque_fingerprint in opaque_directories:
                        try:
                            opaque_after = os.lstat(opaque_path)
                        except OSError:
                            raise PendingColdAdoptionError(
                                "Failed to recheck opaque run directory"
                            ) from None
                        if _stat_fingerprint(opaque_after) != opaque_fingerprint:
                            raise PendingColdAdoptionError(
                                "Opaque run directory metadata changed during scan"
                            )

                    st_run_after = os.fstat(run_fd)
                    if _stat_fingerprint(st_run_after) != _stat_fingerprint(st_run_before):
                        raise PendingColdAdoptionError("Run directory descriptor metadata changed during scan")

                    st_run_path_after = os.lstat(run_path)
                    if _stat_fingerprint(st_run_path_after) != _stat_fingerprint(st_run_before):
                        raise PendingColdAdoptionError("Run directory path metadata changed during scan")
                finally:
                    pending_error = sys.exc_info()[1]
                    try:
                        os.close(run_fd)
                    except OSError as exc:
                        if pending_error is None:
                            raise PendingColdAdoptionError("Run descriptor close failed") from exc

            st_runs_after = os.fstat(runs_fd)
            if _stat_fingerprint(st_runs_after) != _stat_fingerprint(st_runs_before):
                raise PendingColdAdoptionError("Runs directory descriptor metadata changed during scan")

            st_runs_path_after = os.lstat(runs_path)
            if _stat_fingerprint(st_runs_path_after) != _stat_fingerprint(st_runs_before):
                raise PendingColdAdoptionError("Runs directory path metadata changed during scan")
        finally:
            pending_error = sys.exc_info()[1]
            try:
                os.close(runs_fd)
            except OSError as exc:
                if pending_error is None:
                    raise PendingColdAdoptionError("Runs descriptor close failed") from exc

        st_root_after = os.fstat(root_fd)
        if _stat_fingerprint(st_root_after) != _stat_fingerprint(st_root_before):
            raise PendingColdAdoptionError("State root descriptor metadata changed during scan")

        st_root_path_after = os.lstat(state_root)
        if _stat_fingerprint(st_root_path_after) != _stat_fingerprint(st_root_before):
            raise PendingColdAdoptionError("State root path metadata changed during scan")
    finally:
        pending_error = sys.exc_info()[1]
        try:
            os.close(root_fd)
        except OSError as exc:
            if pending_error is None:
                raise PendingColdAdoptionError("State root descriptor close failed") from exc
