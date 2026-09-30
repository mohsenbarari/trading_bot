r"""Tests for descriptor-relative maintenance-transitions directory helper."""

from __future__ import annotations

import os
import stat
import warnings
from unittest.mock import patch

import pytest

from scripts.two_site_maintenance_transition_dir import (
    LEAF_NAME_PATTERN,
    MAINTENANCE_TRANSITIONS_DIRNAME,
    MAX_MAINTENANCE_TRANSITION_LEAVES,
    MaintenanceTransitionDirError,
    list_maintenance_transition_leaves,
    open_maintenance_transitions_dir,
)


def test_no_syntax_warnings() -> None:
    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always")
        import scripts.two_site_maintenance_transition_dir  # noqa: F401
    syntax_warnings = [w for w in recorded if issubclass(w.category, SyntaxWarning)]
    assert not syntax_warnings


def test_parent_initial_invalid_conditions(tmp_path) -> None:
    root_dir = tmp_path / "root_bad_mode"
    root_dir.mkdir(mode=0o700)
    os.chmod(str(root_dir), 0o755)
    rfd = os.open(str(root_dir), os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(MaintenanceTransitionDirError, match="Parent directory mode must be 0700"):
            open_maintenance_transitions_dir(rfd)
    finally:
        os.close(rfd)

    parent_file = tmp_path / "parent_file"
    parent_file.write_text("not a directory")
    pfd = os.open(str(parent_file), os.O_RDONLY)
    try:
        with pytest.raises(MaintenanceTransitionDirError, match="Parent must be a directory"):
            open_maintenance_transitions_dir(pfd)
    finally:
        os.close(pfd)


def test_parent_mode_mutated_before_baseline_closes_child_fd(tmp_path) -> None:
    root_dir = tmp_path / "state_root"
    root_dir.mkdir(mode=0o700)
    os.chmod(str(root_dir), 0o700)
    root_fd = os.open(str(root_dir), os.O_RDONLY | os.O_DIRECTORY)
    try:
        # Scenario 1: create=False with existing directory
        child_dir = root_dir / MAINTENANCE_TRANSITIONS_DIRNAME
        child_dir.mkdir(mode=0o700)
        os.chmod(str(child_dir), 0o700)

        real_open = os.open
        captured_fds: list[int] = []

        def mock_open(path, flags, *args, **kwargs):
            fd = real_open(path, flags, *args, **kwargs)
            if path == MAINTENANCE_TRANSITIONS_DIRNAME:
                captured_fds.append(fd)
                os.chmod(str(root_dir), 0o755)
            return fd

        with patch("os.open", side_effect=mock_open):
            with pytest.raises(MaintenanceTransitionDirError, match="Parent directory mode must be 0700"):
                open_maintenance_transitions_dir(root_fd, create=False)

        assert len(captured_fds) == 1
        with pytest.raises(OSError):
            os.fstat(captured_fds[0])

        # Restore parent mode to 0700
        os.chmod(str(root_dir), 0o700)
        child_dir.rmdir()
        captured_fds.clear()

        # Scenario 2: create=True where directory is created then opened
        with patch("os.open", side_effect=mock_open):
            with pytest.raises(MaintenanceTransitionDirError, match="Parent directory mode must be 0700"):
                open_maintenance_transitions_dir(root_fd, create=True)

        assert len(captured_fds) == 1
        with pytest.raises(OSError):
            os.fstat(captured_fds[0])
    finally:
        os.close(root_fd)


def test_parent_mode_mutated_after_scan_closes_child_fd(tmp_path) -> None:
    root_dir = tmp_path / "state_root"
    root_dir.mkdir(mode=0o700)
    os.chmod(str(root_dir), 0o700)
    root_fd = os.open(str(root_dir), os.O_RDONLY | os.O_DIRECTORY)
    try:
        child_dir = root_dir / MAINTENANCE_TRANSITIONS_DIRNAME
        child_dir.mkdir(mode=0o700)
        os.chmod(str(child_dir), 0o700)

        captured_fds: list[int] = []
        real_list = list_maintenance_transition_leaves

        def mock_list(cfd):
            captured_fds.append(cfd)
            res = real_list(cfd)
            os.chmod(str(root_dir), 0o755)
            return res

        with patch(
            "scripts.two_site_maintenance_transition_dir.list_maintenance_transition_leaves",
            side_effect=mock_list,
        ):
            with pytest.raises(MaintenanceTransitionDirError, match="Parent directory mode must be 0700"):
                open_maintenance_transitions_dir(root_fd, create=False)

        assert len(captured_fds) == 1
        with pytest.raises(OSError):
            os.fstat(captured_fds[0])
    finally:
        os.close(root_fd)


def test_create_true_fsync_ordering_child_then_parent(tmp_path) -> None:
    root_dir = tmp_path / "state_root"
    root_dir.mkdir(mode=0o700)
    os.chmod(str(root_dir), 0o700)
    root_fd = os.open(str(root_dir), os.O_RDONLY | os.O_DIRECTORY)
    try:
        real_fsync = os.fsync
        fsync_calls: list[int] = []

        def mock_fsync(fd):
            fsync_calls.append(fd)
            return real_fsync(fd)

        with patch("os.fsync", side_effect=mock_fsync):
            child_fd = open_maintenance_transitions_dir(root_fd, create=True)

        assert child_fd is not None
        try:
            assert len(fsync_calls) == 2
            assert fsync_calls[0] == child_fd
            assert fsync_calls[1] == root_fd
            st = os.fstat(child_fd)
            assert stat.S_ISDIR(st.st_mode)
            assert (st.st_mode & 0o777) == 0o700
            assert st.st_uid == os.geteuid()
        finally:
            os.close(child_fd)
    finally:
        os.close(root_fd)


def test_existing_unsafe_child_rejected_without_repair(tmp_path) -> None:
    root_dir = tmp_path / "state_root"
    root_dir.mkdir(mode=0o700)
    os.chmod(str(root_dir), 0o700)
    root_fd = os.open(str(root_dir), os.O_RDONLY | os.O_DIRECTORY)
    try:
        child_dir = root_dir / MAINTENANCE_TRANSITIONS_DIRNAME
        child_dir.mkdir(mode=0o700)
        os.chmod(str(child_dir), 0o755)

        with pytest.raises(
            MaintenanceTransitionDirError,
            match="maintenance-transitions directory mode must be 0700",
        ):
            open_maintenance_transitions_dir(root_fd, create=False)

        assert (child_dir.stat().st_mode & 0o777) == 0o755

        with pytest.raises(
            MaintenanceTransitionDirError,
            match="maintenance-transitions directory mode must be 0700",
        ):
            open_maintenance_transitions_dir(root_fd, create=True)

        assert (child_dir.stat().st_mode & 0o777) == 0o755
    finally:
        os.close(root_fd)


def test_existing_child_symlink_or_file_rejected(tmp_path) -> None:
    root_dir = tmp_path / "state_root"
    root_dir.mkdir(mode=0o700)
    os.chmod(str(root_dir), 0o700)
    root_fd = os.open(str(root_dir), os.O_RDONLY | os.O_DIRECTORY)
    try:
        target_dir = tmp_path / "target"
        target_dir.mkdir(mode=0o700)
        os.chmod(str(target_dir), 0o700)
        symlink_path = root_dir / MAINTENANCE_TRANSITIONS_DIRNAME
        symlink_path.symlink_to(target_dir)

        with pytest.raises(MaintenanceTransitionDirError, match="must not be a symlink"):
            open_maintenance_transitions_dir(root_fd, create=False)

        symlink_path.unlink()
        file_path = root_dir / MAINTENANCE_TRANSITIONS_DIRNAME
        file_path.write_text("regular file")
        with pytest.raises(MaintenanceTransitionDirError, match="must be a directory"):
            open_maintenance_transitions_dir(root_fd, create=False)
    finally:
        os.close(root_fd)


def test_child_path_replacement_drift_detected(tmp_path) -> None:
    root_dir = tmp_path / "state_root"
    root_dir.mkdir(mode=0o700)
    os.chmod(str(root_dir), 0o700)
    root_fd = os.open(str(root_dir), os.O_RDONLY | os.O_DIRECTORY)
    try:
        child_dir = root_dir / MAINTENANCE_TRANSITIONS_DIRNAME
        child_dir.mkdir(mode=0o700)
        os.chmod(str(child_dir), 0o700)

        captured_fds: list[int] = []
        real_list = list_maintenance_transition_leaves

        def replace_child_during_scan(cfd):
            captured_fds.append(cfd)
            res = real_list(cfd)
            child_dir.rmdir()
            child_dir.mkdir(mode=0o700)
            os.chmod(str(child_dir), 0o700)
            return res

        with patch(
            "scripts.two_site_maintenance_transition_dir.list_maintenance_transition_leaves",
            side_effect=replace_child_during_scan,
        ):
            with pytest.raises(
                MaintenanceTransitionDirError,
                match="Child directory path replaced or drift detected",
            ):
                open_maintenance_transitions_dir(root_fd, create=False)

        assert len(captured_fds) == 1
        with pytest.raises(OSError):
            os.fstat(captured_fds[0])
    finally:
        os.close(root_fd)


def test_leaf_name_grammar_and_negative_literals(tmp_path) -> None:
    child_dir = tmp_path / MAINTENANCE_TRANSITIONS_DIRNAME
    child_dir.mkdir(mode=0o700)
    os.chmod(str(child_dir), 0o700)
    cfd = os.open(str(child_dir), os.O_RDONLY | os.O_DIRECTORY)
    try:
        valid_hash = "a" * 64
        valid_name = f"rescope-{valid_hash}.json"
        leaf_file = child_dir / valid_name
        leaf_file.write_text("{}")
        os.chmod(str(leaf_file), 0o600)

        leaves = list_maintenance_transition_leaves(cfd)
        assert leaves == [valid_name]

        negatives = [
            "leaf.json",
            "001_review.json",
            f"rescope-{'A' * 64}.json",
            f"rescope-{'a' * 63}.json",
            f"rescope-{'a' * 65}.json",
            f"rescope-{valid_hash}.json.tmp",
        ]
        for bad_name in negatives:
            bad_file = child_dir / bad_name
            bad_file.write_text("{}")
            os.chmod(str(bad_file), 0o600)
            try:
                with pytest.raises(MaintenanceTransitionDirError, match="Invalid leaf entry name"):
                    list_maintenance_transition_leaves(cfd)
            finally:
                bad_file.unlink()
    finally:
        os.close(cfd)


def test_leaf_metadata_validation(tmp_path) -> None:
    child_dir = tmp_path / MAINTENANCE_TRANSITIONS_DIRNAME
    child_dir.mkdir(mode=0o700)
    os.chmod(str(child_dir), 0o700)
    cfd = os.open(str(child_dir), os.O_RDONLY | os.O_DIRECTORY)
    try:
        h1 = "1" * 64
        name1 = f"rescope-{h1}.json"
        f1 = child_dir / name1
        f1.write_text("{}")

        # Wrong mode
        os.chmod(str(f1), 0o644)
        with pytest.raises(MaintenanceTransitionDirError, match="Leaf entry mode must be 0600"):
            list_maintenance_transition_leaves(cfd)

        # Correct mode
        os.chmod(str(f1), 0o600)

        # Link count > 1
        link_target = tmp_path / "hardlink_target"
        os.link(str(f1), str(link_target))
        try:
            with pytest.raises(MaintenanceTransitionDirError, match="Leaf entry link count must be 1"):
                list_maintenance_transition_leaves(cfd)
        finally:
            link_target.unlink()

        # Symlink leaf
        f1.unlink()
        target = tmp_path / "real_file"
        target.write_text("{}")
        os.chmod(str(target), 0o600)
        f1.symlink_to(target)
        with pytest.raises(MaintenanceTransitionDirError, match="Leaf entry must not be a symlink"):
            list_maintenance_transition_leaves(cfd)
        f1.unlink()

        # Directory leaf
        f1.mkdir(mode=0o700)
        os.chmod(str(f1), 0o700)
        with pytest.raises(MaintenanceTransitionDirError, match="Leaf entry must be a regular file"):
            list_maintenance_transition_leaves(cfd)
        f1.rmdir()
    finally:
        os.close(cfd)


def test_leaf_cap_streaming_256(tmp_path) -> None:
    child_dir = tmp_path / MAINTENANCE_TRANSITIONS_DIRNAME
    child_dir.mkdir(mode=0o700)
    os.chmod(str(child_dir), 0o700)
    cfd = os.open(str(child_dir), os.O_RDONLY | os.O_DIRECTORY)
    try:
        for i in range(MAX_MAINTENANCE_TRANSITION_LEAVES):
            name = f"rescope-{i:064x}.json"
            f = child_dir / name
            f.write_text("{}")
            os.chmod(str(f), 0o600)

        leaves = list_maintenance_transition_leaves(cfd)
        assert len(leaves) == 256
        assert leaves == sorted(leaves)

        extra_name = f"rescope-{'f' * 64}.json"
        extra = child_dir / extra_name
        extra.write_text("{}")
        os.chmod(str(extra), 0o600)
        try:
            with pytest.raises(MaintenanceTransitionDirError, match="Directory entry count exceeds limit"):
                list_maintenance_transition_leaves(cfd)
        finally:
            extra.unlink()
    finally:
        os.close(cfd)


def test_argument_validation(tmp_path) -> None:
    with pytest.raises(MaintenanceTransitionDirError, match="Invalid state_root_fd"):
        open_maintenance_transitions_dir("not_int")  # type: ignore[arg-type]
    with pytest.raises(MaintenanceTransitionDirError, match="Invalid state_root_fd"):
        open_maintenance_transitions_dir(-1)
    with pytest.raises(MaintenanceTransitionDirError, match="Invalid create argument"):
        open_maintenance_transitions_dir(0, create="yes")  # type: ignore[arg-type]

    with pytest.raises(MaintenanceTransitionDirError, match="Invalid transitions descriptor"):
        list_maintenance_transition_leaves("not_int")  # type: ignore[arg-type]
    with pytest.raises(MaintenanceTransitionDirError, match="Invalid transitions descriptor"):
        list_maintenance_transition_leaves(-1)

    root_dir = tmp_path / "root"
    root_dir.mkdir(mode=0o700)
    os.chmod(str(root_dir), 0o700)
    rfd = os.open(str(root_dir), os.O_RDONLY | os.O_DIRECTORY)
    try:
        assert open_maintenance_transitions_dir(rfd, create=False) is None
    finally:
        os.close(rfd)
