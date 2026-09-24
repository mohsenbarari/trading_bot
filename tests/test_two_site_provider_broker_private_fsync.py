from __future__ import annotations

import os
from pathlib import Path
import stat
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.two_site_provider_broker import (
    BrokerError,
    fsync_private_record_directory,
)


def _clone_stat_with_ino(st: os.stat_result, new_ino: int) -> os.stat_result:
    return os.stat_result((
        st.st_mode,
        new_ino,
        st.st_dev,
        st.st_nlink,
        st.st_uid,
        st.st_gid,
        st.st_size,
        int(st.st_atime),
        int(st.st_mtime),
        int(st.st_ctime),
    ))


def test_fsync_private_record_directory_positive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "target_dir"
    target.mkdir(mode=0o700)
    target.chmod(0o700)

    fsync_fds: list[int] = []
    orig_fsync = os.fsync

    def fsync_spy(fd: int) -> None:
        st = os.fstat(fd)
        assert stat.S_ISDIR(st.st_mode)
        fsync_fds.append(fd)
        orig_fsync(fd)

    monkeypatch.setattr(os, "fsync", fsync_spy)

    fsync_private_record_directory(target)

    assert len(fsync_fds) == 1
    with pytest.raises(OSError):
        os.fstat(fsync_fds[0])


def test_fsync_private_record_directory_invalid_path_types_and_canonical(tmp_path: Path) -> None:
    real_dir = tmp_path / "real_dir"
    real_dir.mkdir(mode=0o700)
    real_dir.chmod(0o700)

    with pytest.raises(BrokerError, match="record directory path must be a Path"):
        fsync_private_record_directory(str(real_dir))  # type: ignore[arg-type]

    symlink_dir = tmp_path / "symlink_dir"
    symlink_dir.symlink_to(real_dir)
    with pytest.raises(BrokerError, match="record directory must be a canonical path without symlinks"):
        fsync_private_record_directory(symlink_dir)

    non_canonical = real_dir / ".." / real_dir.name
    with pytest.raises(BrokerError, match="record directory must be a canonical path without symlinks"):
        fsync_private_record_directory(non_canonical)

    non_existent = tmp_path / "missing_dir"
    with pytest.raises(BrokerError, match="record directory is unavailable"):
        fsync_private_record_directory(non_existent)


def test_fsync_private_record_directory_non_directory(tmp_path: Path) -> None:
    regular_file = tmp_path / "file.txt"
    regular_file.write_text("content")
    regular_file.chmod(0o700)
    with pytest.raises(BrokerError, match="record directory must be a directory and not a symlink"):
        fsync_private_record_directory(regular_file)


def test_fsync_private_record_directory_mode_mismatch(tmp_path: Path) -> None:
    bad_mode_dir = tmp_path / "bad_mode"
    bad_mode_dir.mkdir(mode=0o755)
    bad_mode_dir.chmod(0o755)
    with pytest.raises(BrokerError, match="record directory mode must be 0700"):
        fsync_private_record_directory(bad_mode_dir)


def test_fsync_private_record_directory_owner_mismatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "target_dir"
    target.mkdir(mode=0o700)
    target.chmod(0o700)
    real_euid = os.geteuid()
    monkeypatch.setattr(os, "geteuid", lambda: real_euid + 9999)
    with pytest.raises(BrokerError, match="record directory is not owned by current euid"):
        fsync_private_record_directory(target)


def test_fsync_private_record_directory_pre_open_inode_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "target_dir"
    target.mkdir(mode=0o700)
    target.chmod(0o700)

    opened_fds: list[int] = []
    orig_open = os.open

    def open_spy(*args, **kwargs) -> int:
        fd = orig_open(*args, **kwargs)
        opened_fds.append(fd)
        return fd

    monkeypatch.setattr(os, "open", open_spy)

    orig_lstat = os.lstat

    def scoped_lstat(path, *args, **kwargs):
        st = orig_lstat(path, *args, **kwargs)
        if Path(path) == target:
            return _clone_stat_with_ino(st, st.st_ino + 9999)
        return st

    monkeypatch.setattr(os, "lstat", scoped_lstat)

    with pytest.raises(BrokerError, match="record directory descriptor does not match path before fsync"):
        fsync_private_record_directory(target)

    assert len(opened_fds) == 1
    with pytest.raises(OSError):
        os.fstat(opened_fds[0])


def test_fsync_private_record_directory_post_fsync_inode_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "target_dir"
    target.mkdir(mode=0o700)
    target.chmod(0o700)

    fsync_called = False
    orig_fsync = os.fsync

    def fsync_tracker(fd: int) -> None:
        nonlocal fsync_called
        orig_fsync(fd)
        fsync_called = True

    monkeypatch.setattr(os, "fsync", fsync_tracker)

    opened_fds: list[int] = []
    orig_open = os.open

    def open_spy(*args, **kwargs) -> int:
        fd = orig_open(*args, **kwargs)
        opened_fds.append(fd)
        return fd

    monkeypatch.setattr(os, "open", open_spy)

    orig_lstat = os.lstat

    def scoped_lstat(path, *args, **kwargs):
        st = orig_lstat(path, *args, **kwargs)
        if Path(path) == target and fsync_called:
            return _clone_stat_with_ino(st, st.st_ino + 9999)
        return st

    monkeypatch.setattr(os, "lstat", scoped_lstat)

    with pytest.raises(BrokerError, match="record directory descriptor does not match path after fsync"):
        fsync_private_record_directory(target)

    assert len(opened_fds) == 1
    with pytest.raises(OSError):
        os.fstat(opened_fds[0])


def test_fsync_private_record_directory_fsync_oserror_closes_fd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "target_dir"
    target.mkdir(mode=0o700)
    target.chmod(0o700)

    opened_fds: list[int] = []
    orig_open = os.open

    def open_spy(*args, **kwargs) -> int:
        fd = orig_open(*args, **kwargs)
        opened_fds.append(fd)
        return fd

    monkeypatch.setattr(os, "open", open_spy)

    def failing_fsync(fd: int) -> None:
        raise OSError(5, "Input/output error")

    monkeypatch.setattr(os, "fsync", failing_fsync)

    with pytest.raises(BrokerError, match="cannot fsync record directory"):
        fsync_private_record_directory(target)

    assert len(opened_fds) == 1
    with pytest.raises(OSError):
        os.fstat(opened_fds[0])


def test_fsync_private_record_directory_fstat_oserror_closes_fd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "target_dir"
    target.mkdir(mode=0o700)
    target.chmod(0o700)

    opened_fds: list[int] = []
    orig_open = os.open

    def open_spy(*args, **kwargs) -> int:
        fd = orig_open(*args, **kwargs)
        opened_fds.append(fd)
        return fd

    monkeypatch.setattr(os, "open", open_spy)

    def failing_fstat(fd: int) -> os.stat_result:
        raise OSError(5, "Input/output error")

    monkeypatch.setattr(os, "fstat", failing_fstat)

    with pytest.raises(BrokerError, match="cannot fstat record directory"):
        fsync_private_record_directory(target)

    assert len(opened_fds) == 1
    with pytest.raises(OSError):
        os.fstat(opened_fds[0])


def test_fsync_private_record_directory_open_oserror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "target_dir"
    target.mkdir(mode=0o700)
    target.chmod(0o700)

    def failing_open(*args, **kwargs) -> int:
        raise OSError(13, "Permission denied")

    monkeypatch.setattr(os, "open", failing_open)

    with pytest.raises(BrokerError, match="cannot safely open record directory"):
        fsync_private_record_directory(target)


def test_fsync_private_record_directory_after_lstat_oserror_closes_fd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "target_dir"
    target.mkdir(mode=0o700)
    target.chmod(0o700)

    fsync_called = False
    orig_fsync = os.fsync

    def fsync_tracker(fd: int) -> None:
        nonlocal fsync_called
        orig_fsync(fd)
        fsync_called = True

    monkeypatch.setattr(os, "fsync", fsync_tracker)

    opened_fds: list[int] = []
    orig_open = os.open

    def open_spy(*args, **kwargs) -> int:
        fd = orig_open(*args, **kwargs)
        opened_fds.append(fd)
        return fd

    monkeypatch.setattr(os, "open", open_spy)

    orig_lstat = os.lstat

    def scoped_lstat(path, *args, **kwargs):
        if Path(path) == target and fsync_called:
            raise OSError(5, "Input/output error")
        return orig_lstat(path, *args, **kwargs)

    monkeypatch.setattr(os, "lstat", scoped_lstat)

    with pytest.raises(BrokerError, match="cannot lstat record directory after fsync"):
        fsync_private_record_directory(target)

    assert len(opened_fds) == 1
    with pytest.raises(OSError):
        os.fstat(opened_fds[0])
