#!/usr/bin/env python3
"""Adversarial and contract tests for _open_existing_cycle_run_directory pathwalk."""
from __future__ import annotations

import os
import stat
from pathlib import Path
import pytest

from scripts.two_site_provider_broker import (
    BrokerError,
    _open_existing_cycle_run_directory,
    _verify_pinned_directory,
)

VALID_CYCLE_ID = "cycle-2026-20260925T120000Z-1"
ANCESTOR_COMPONENTS = [".local", "two-site-refactor", "autopilot", "runs"]


def _setup_canonical_tree(root: Path, cycle_id: str = VALID_CYCLE_ID) -> Path:
    run_dir = root / ".local" / "two-site-refactor" / "autopilot" / "runs" / cycle_id
    run_dir.mkdir(parents=True, mode=0o700)
    curr = root
    os.chmod(curr, 0o700)
    for part in (*ANCESTOR_COMPONENTS, cycle_id):
        curr = curr / part
        os.chmod(curr, 0o700)
    return run_dir


def _snapshot_tree(root: Path) -> dict[str, tuple[int, int, int]]:
    snapshot: dict[str, tuple[int, int, int]] = {}
    for p in root.rglob("*"):
        st = p.lstat()
        rel = p.relative_to(root).as_posix()
        snapshot[rel] = (stat.S_IMODE(st.st_mode), st.st_ino, st.st_size)
    return snapshot


def _fd_count() -> int | None:
    fd_dir = Path("/proc/self/fd")
    if fd_dir.is_dir():
        try:
            return len(os.listdir(fd_dir))
        except OSError:
            return None
    return None


def test_requirement1_valid_open_zero_mutation_and_descriptor_lifecycle(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    expected_run = _setup_canonical_tree(root, VALID_CYCLE_ID)
    snapshot_before = _snapshot_tree(root)

    # 1. Valid open, correct path, active fd, and verify twice.
    captured_fd: int | None = None
    with _open_existing_cycle_run_directory(root, VALID_CYCLE_ID) as (run_path, run_fd, verify_identity):
        captured_fd = run_fd
        assert run_path == expected_run
        assert isinstance(run_fd, int) and run_fd >= 0

        # Assert active fd matches run path
        fd_stat = os.fstat(run_fd)
        assert stat.S_ISDIR(fd_stat.st_mode)
        path_stat = os.lstat(run_path)
        assert (fd_stat.st_dev, fd_stat.st_ino) == (path_stat.st_dev, path_stat.st_ino)

        # Assert verify twice succeeds
        verify_identity()
        verify_identity()

        # Zero mutation during open
        assert _snapshot_tree(root) == snapshot_before

    # Zero mutation after exit
    assert _snapshot_tree(root) == snapshot_before

    # Descriptors closed after normal exit
    assert captured_fd is not None
    with pytest.raises(OSError):
        os.fstat(captured_fd)

    # verify_identity after exit raises BrokerError
    with pytest.raises(BrokerError, match="cycle run directory context is closed"):
        verify_identity()

    # Descriptors closed after raised body exception
    exception_fd: int | None = None
    with pytest.raises(RuntimeError, match="deliberate body error"):
        with _open_existing_cycle_run_directory(root, VALID_CYCLE_ID) as (run_path, run_fd, verify_identity):
            exception_fd = run_fd
            raise RuntimeError("deliberate body error")
    assert exception_fd is not None
    with pytest.raises(OSError):
        os.fstat(exception_fd)

    # Bounded fd count: no fd leak on failed open
    initial_fds = _fd_count()
    if initial_fds is not None:
        missing_cycle = "cycle-2026-20260925T120000Z-9"
        for _ in range(5):
            with pytest.raises(BrokerError, match="cannot safely open directory"):
                with _open_existing_cycle_run_directory(root, missing_cycle):
                    pass
        assert _fd_count() == initial_fds


def test_requirement2_missing_symlink_unsafe_mode_and_malformed_ids_fail_closed(tmp_path: Path) -> None:
    root = tmp_path.resolve()

    # 1. Missing component fails without creating any path
    assert not (root / ".local").exists()
    with pytest.raises(BrokerError, match="cannot safely open directory"):
        with _open_existing_cycle_run_directory(root, VALID_CYCLE_ID):
            pass
    assert not (root / ".local").exists()

    # Intermediate missing component
    runs_dir = root / ".local" / "two-site-refactor" / "autopilot" / "runs"
    runs_dir.mkdir(parents=True, mode=0o700)
    for p in [root, root / ".local", root / ".local" / "two-site-refactor", root / ".local" / "two-site-refactor" / "autopilot", runs_dir]:
        os.chmod(p, 0o700)

    target_run = runs_dir / VALID_CYCLE_ID
    assert not target_run.exists()
    with pytest.raises(BrokerError, match="cannot safely open directory"):
        with _open_existing_cycle_run_directory(root, VALID_CYCLE_ID):
            pass
    assert not target_run.exists()

    # Non-directory / missing root
    non_dir_root = root / "file_root"
    non_dir_root.write_text("not_a_dir")
    with pytest.raises(BrokerError, match="broker root must be a canonical directory"):
        with _open_existing_cycle_run_directory(non_dir_root, VALID_CYCLE_ID):
            pass

    # Setup valid tree for subsequent checks
    _setup_canonical_tree(root, VALID_CYCLE_ID)

    # 2. Symlinks: root, .local, intermediate, final must fail
    sym_root = root.parent / "sym_root"
    sym_root.symlink_to(root)
    try:
        with pytest.raises(BrokerError, match="broker root must be a canonical directory"):
            with _open_existing_cycle_run_directory(sym_root, VALID_CYCLE_ID):
                pass
    finally:
        sym_root.unlink(missing_ok=True)

    # Symlinked .local
    local_dir = root / ".local"
    local_bak = root / ".local_bak"
    local_dir.rename(local_bak)
    local_dir.symlink_to(local_bak)
    try:
        with pytest.raises(BrokerError):
            with _open_existing_cycle_run_directory(root, VALID_CYCLE_ID):
                pass
    finally:
        local_dir.unlink()
        local_bak.rename(local_dir)

    # Symlinked intermediate (runs)
    runs_bak = runs_dir.with_name("runs_bak")
    runs_dir.rename(runs_bak)
    runs_dir.symlink_to(runs_bak)
    try:
        with pytest.raises(BrokerError):
            with _open_existing_cycle_run_directory(root, VALID_CYCLE_ID):
                pass
    finally:
        runs_dir.unlink()
        runs_bak.rename(runs_dir)

    # Symlinked final run dir
    run_bak = target_run.with_name("run_bak")
    target_run.rename(run_bak)
    target_run.symlink_to(run_bak)
    try:
        with pytest.raises(BrokerError):
            with _open_existing_cycle_run_directory(root, VALID_CYCLE_ID):
                pass
    finally:
        target_run.unlink()
        run_bak.rename(target_run)

    # 3. Unsafe permissions on initial open fail; restore modes in cleanup
    # Group/world-writable root
    for bad_mode in (0o775, 0o777):
        os.chmod(root, bad_mode)
        try:
            with pytest.raises(BrokerError, match="has group or world write bits"):
                with _open_existing_cycle_run_directory(root, VALID_CYCLE_ID):
                    pass
        finally:
            os.chmod(root, 0o700)

    # Every ancestor component with group or world write
    curr = root
    for comp in ANCESTOR_COMPONENTS:
        curr = curr / comp
        for bad_mode in (0o775, 0o777):
            os.chmod(curr, bad_mode)
            try:
                with pytest.raises(BrokerError, match="has group or world write bits"):
                    with _open_existing_cycle_run_directory(root, VALID_CYCLE_ID):
                        pass
            finally:
                os.chmod(curr, 0o700)

    # Final run directory mode != 0700 (e.g. 0755, 0750)
    for bad_mode in (0o755, 0o750):
        os.chmod(target_run, bad_mode)
        try:
            with pytest.raises(BrokerError, match="mode must be 0700"):
                with _open_existing_cycle_run_directory(root, VALID_CYCLE_ID):
                    pass
        finally:
            os.chmod(target_run, 0o700)

    # 4. Maintenance IDs and malformed cycle IDs fail without creating paths
    maint_id = "maint-20260925T120000Z-0123456789abcdef"
    maint_target = runs_dir / maint_id
    assert not maint_target.exists()
    with pytest.raises(BrokerError, match="controller run ID must be a cycle run ID"):
        with _open_existing_cycle_run_directory(root, maint_id):
            pass
    assert not maint_target.exists()

    malformed_ids = [
        "",
        "cycle-",
        "cycle-2026-bad",
        "cycle-2026-20260925T120000Z-1/../../escape",
        "cycle-2026-20260925T120000Z-1\x00extra",
        12345,
        None,
    ]
    for bad_id in malformed_ids:
        with pytest.raises(BrokerError, match="controller run ID must be a cycle run ID"):
            with _open_existing_cycle_run_directory(root, bad_id):  # type: ignore[arg-type]
                pass


def test_requirement3_physical_replacement_and_permission_drift_fail_verify(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    target_run = _setup_canonical_tree(root, VALID_CYCLE_ID)

    # 1. Post-open permission drift fails verify_identity; restore allows subsequent verify
    with _open_existing_cycle_run_directory(root, VALID_CYCLE_ID) as (run_path, run_fd, verify_identity):
        verify_identity()

        # Root permission drift
        os.chmod(root, 0o775)
        try:
            with pytest.raises(BrokerError, match="has group or world write bits"):
                verify_identity()
        finally:
            os.chmod(root, 0o700)
        verify_identity()

        # Ancestor permission drift (.local, two-site-refactor, autopilot, runs)
        curr = root
        for comp in ANCESTOR_COMPONENTS:
            curr = curr / comp
            os.chmod(curr, 0o775)
            try:
                with pytest.raises(BrokerError, match="has group or world write bits"):
                    verify_identity()
            finally:
                os.chmod(curr, 0o700)
            verify_identity()

        # Final run directory permission drift
        os.chmod(run_path, 0o755)
        try:
            with pytest.raises(BrokerError, match="mode must be 0700"):
                verify_identity()
        finally:
            os.chmod(run_path, 0o700)
        verify_identity()

    # 2. Physical ancestor replacement while pinned fails verify even with same permissions
    runs_dir = root / ".local" / "two-site-refactor" / "autopilot" / "runs"
    runs_bak = runs_dir.with_name("runs_bak")

    with _open_existing_cycle_run_directory(root, VALID_CYCLE_ID) as (run_path, run_fd, verify_identity):
        verify_identity()
        # Rename pinned runs ancestor and replace with same mode 0700
        runs_dir.rename(runs_bak)
        runs_dir.mkdir(mode=0o700)
        try:
            with pytest.raises(BrokerError, match="identity changed|does not match path"):
                verify_identity()
        finally:
            runs_dir.rmdir()
            runs_bak.rename(runs_dir)
        verify_identity()

    # Earlier ancestor replacement (autopilot)
    autopilot_dir = root / ".local" / "two-site-refactor" / "autopilot"
    autopilot_bak = autopilot_dir.with_name("autopilot_bak")

    with _open_existing_cycle_run_directory(root, VALID_CYCLE_ID) as (run_path, run_fd, verify_identity):
        verify_identity()
        autopilot_dir.rename(autopilot_bak)
        autopilot_dir.mkdir(mode=0o700)
        try:
            with pytest.raises(BrokerError, match="identity changed|does not match path"):
                verify_identity()
        finally:
            autopilot_dir.rmdir()
            autopilot_bak.rename(autopilot_dir)
        verify_identity()

    # 3. Final run directory replacement while pinned fails verify even with same 0700 permissions
    run_bak = target_run.with_name("run_bak")

    with _open_existing_cycle_run_directory(root, VALID_CYCLE_ID) as (run_path, run_fd, verify_identity):
        verify_identity()
        target_run.rename(run_bak)
        target_run.mkdir(mode=0o700)
        try:
            with pytest.raises(BrokerError, match="identity changed|does not match path"):
                verify_identity()
        finally:
            target_run.rmdir()
            run_bak.rename(target_run)
        verify_identity()

    # 4. Deleting/unlinking final run directory while pinned fails verify
    with _open_existing_cycle_run_directory(root, VALID_CYCLE_ID) as (run_path, run_fd, verify_identity):
        verify_identity()
        target_run.rename(run_bak)
        try:
            with pytest.raises(BrokerError, match="cannot lstat directory"):
                verify_identity()
        finally:
            run_bak.rename(target_run)
        verify_identity()
