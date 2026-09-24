"""Hermetic unit tests for terminal quota record byte reader in provider broker."""
from __future__ import annotations

import errno
import os
from pathlib import Path
import stat
from unittest import mock
import pytest

from scripts import two_site_provider_broker as broker


def _setup_terminal_quota_env(
    tmp_path: Path, request_id: str, content: bytes = b'{"receipt":"ok"}'
) -> tuple[Path, Path, Path]:
    root = tmp_path.resolve()
    state_root, _status, _reqs, _claimed, _resps = broker.broker_paths(root)
    directory = state_root / "terminal-quota"
    directory.mkdir(mode=0o700, exist_ok=True)
    os.chmod(directory, 0o700)

    target_file = directory / f"{request_id}.json"
    target_file.write_bytes(content)
    os.chmod(target_file, 0o600)
    return root, directory, target_file


def _clone_stat_with_time_ns(
    st: os.stat_result, *, mtime_ns: int | None = None, ctime_ns: int | None = None
) -> os.stat_result:
    return os.stat_result(
        (
            st.st_mode,
            st.st_ino,
            st.st_dev,
            st.st_nlink,
            st.st_uid,
            st.st_gid,
            st.st_size,
            int(st.st_atime),
            int(st.st_mtime if mtime_ns is None else mtime_ns // 1_000_000_000),
            int(st.st_ctime if ctime_ns is None else ctime_ns // 1_000_000_000),
        ),
        {
            "st_atime_ns": st.st_atime_ns,
            "st_mtime_ns": st.st_mtime_ns if mtime_ns is None else mtime_ns,
            "st_ctime_ns": st.st_ctime_ns if ctime_ns is None else ctime_ns,
        },
    )


def test_read_terminal_quota_bytes_success(tmp_path: Path) -> None:
    req_id = "a" * 32
    expected = b'{"schema_version":"1.0","test":"data"}'
    root, _dir, _target = _setup_terminal_quota_env(tmp_path, req_id, expected)

    data = broker.read_terminal_quota_bytes(root, req_id)
    assert data == expected


def test_counterexample_a_post_scan_pre_open_replacement(tmp_path: Path) -> None:
    req_id = "b" * 32
    root, _dir_path, target_path = _setup_terminal_quota_env(
        tmp_path, req_id, b'{"target":"initial"}'
    )

    replacement_path = tmp_path / "replacement.json"
    replacement_path.write_bytes(b'{"target":"replaced"}')
    os.chmod(replacement_path, 0o600)

    real_scan = broker.scan_private_record_directory

    def hooked_scan(directory: Path) -> list[str]:
        names = real_scan(directory)
        os.replace(replacement_path, target_path)
        return names

    with mock.patch(
        "scripts.two_site_provider_broker.scan_private_record_directory",
        side_effect=hooked_scan,
    ):
        with pytest.raises(broker.BrokerError, match="changed between scan and open"):
            broker.read_terminal_quota_bytes(root, req_id)


def test_counterexample_b_file_close_failure_fails_closed(tmp_path: Path) -> None:
    req_id = "c" * 32
    root, _dir, _target = _setup_terminal_quota_env(
        tmp_path, req_id, b'{"data":"close_fail"}'
    )

    real_open = os.open
    real_close = os.close
    opened_fds: list[int] = []
    closed_fds: list[int] = []
    file_fd_to_fault: int | None = None

    def spy_open(path, flags, *args, **kwargs) -> int:
        fd = real_open(path, flags, *args, **kwargs)
        opened_fds.append(fd)
        nonlocal file_fd_to_fault
        if kwargs.get("dir_fd") is not None or str(path).endswith(".json"):
            file_fd_to_fault = fd
        return fd

    def faulted_close(fd: int) -> None:
        if file_fd_to_fault is not None and fd == file_fd_to_fault:
            raise OSError(errno.EIO, "Injected I/O error on file close")
        closed_fds.append(fd)
        real_close(fd)

    with mock.patch("os.open", side_effect=spy_open), mock.patch(
        "os.close", side_effect=faulted_close
    ):
        with pytest.raises(broker.BrokerError, match="cannot safely close descriptor"):
            broker.read_terminal_quota_bytes(root, req_id)

    assert file_fd_to_fault is not None
    try:
        real_close(file_fd_to_fault)
    except OSError:
        pass

    dir_fds = [fd for fd in opened_fds if fd != file_fd_to_fault]
    assert len(dir_fds) >= 1
    for dfd in dir_fds:
        assert dfd in closed_fds
        with pytest.raises(OSError):
            os.fstat(dfd)


def test_counterexample_c_post_read_stat_mtime_ns_mismatch(tmp_path: Path) -> None:
    req_id = "7" * 32
    root, _dir, _target = _setup_terminal_quota_env(tmp_path, req_id, b'{"data":"mtime_drift"}')

    real_stat = os.stat
    stat_target_calls = 0

    def hooked_stat(path, *args, **kwargs):
        st = real_stat(path, *args, **kwargs)
        if kwargs.get("dir_fd") is not None and str(path) == f"{req_id}.json":
            nonlocal stat_target_calls
            stat_target_calls += 1
            if stat_target_calls == 2:
                return _clone_stat_with_time_ns(st, mtime_ns=st.st_mtime_ns + 10_000_000)
        return st

    with mock.patch("os.stat", side_effect=hooked_stat):
        with pytest.raises(broker.BrokerError, match="terminal quota record path metadata mismatch after read"):
            broker.read_terminal_quota_bytes(root, req_id)

    assert stat_target_calls == 2


def test_counterexample_c_post_read_stat_ctime_ns_mismatch(tmp_path: Path) -> None:
    req_id = "8" * 32
    root, _dir, _target = _setup_terminal_quota_env(tmp_path, req_id, b'{"data":"ctime_drift"}')

    real_stat = os.stat
    stat_target_calls = 0

    def hooked_stat(path, *args, **kwargs):
        st = real_stat(path, *args, **kwargs)
        if kwargs.get("dir_fd") is not None and str(path) == f"{req_id}.json":
            nonlocal stat_target_calls
            stat_target_calls += 1
            if stat_target_calls == 2:
                return _clone_stat_with_time_ns(st, ctime_ns=st.st_ctime_ns + 10_000_000)
        return st

    with mock.patch("os.stat", side_effect=hooked_stat):
        with pytest.raises(broker.BrokerError, match="terminal quota record path metadata mismatch after read"):
            broker.read_terminal_quota_bytes(root, req_id)

    assert stat_target_calls == 2


def test_read_terminal_quota_bytes_invalid_request_id(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    for bad_id in (
        "A" * 32,
        "1" * 31,
        "1" * 33,
        "g" * 32,
        "../invalid",
        "",
        12345,
        None,
    ):
        with pytest.raises(broker.BrokerError, match="request ID is invalid"):
            broker.read_terminal_quota_bytes(root, bad_id)  # type: ignore[arg-type]


def test_read_terminal_quota_bytes_missing_directory(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    broker.broker_paths(root)
    req_id = "d" * 32
    with pytest.raises(broker.BrokerError, match="terminal quota directory is unavailable"):
        broker.read_terminal_quota_bytes(root, req_id)


def test_read_terminal_quota_bytes_directory_bad_mode_and_symlink(tmp_path: Path) -> None:
    req_id = "e" * 32
    root, directory, _target = _setup_terminal_quota_env(tmp_path, req_id)

    os.chmod(directory, 0o755)
    with pytest.raises(broker.BrokerError, match="mode must be 0700"):
        broker.read_terminal_quota_bytes(root, req_id)
    os.chmod(directory, 0o700)

    state_root, _status, _reqs, _claimed, _resps = broker.broker_paths(root)
    real_dir = state_root / "real-terminal-quota"
    directory.rename(real_dir)
    directory.symlink_to(real_dir)
    with pytest.raises(broker.BrokerError, match="must be a directory and not a symlink"):
        broker.read_terminal_quota_bytes(root, req_id)


def test_read_terminal_quota_bytes_target_missing_or_symlink(tmp_path: Path) -> None:
    req_id = "f" * 32
    root, _directory, target = _setup_terminal_quota_env(tmp_path, req_id)
    target.unlink()
    with pytest.raises(broker.BrokerError, match="is unavailable"):
        broker.read_terminal_quota_bytes(root, req_id)

    outside = tmp_path / "outside.json"
    outside.write_bytes(b"outside")
    target.symlink_to(outside)
    with pytest.raises(broker.BrokerError, match="is not a regular file"):
        broker.read_terminal_quota_bytes(root, req_id)


def test_read_terminal_quota_bytes_target_mode_mismatch(tmp_path: Path) -> None:
    req_id = "1" * 32
    root, _directory, target = _setup_terminal_quota_env(tmp_path, req_id)
    os.chmod(target, 0o644)
    with pytest.raises(broker.BrokerError, match="mode must be 0600"):
        broker.read_terminal_quota_bytes(root, req_id)


def test_read_terminal_quota_bytes_external_hardlink(tmp_path: Path) -> None:
    req_id = "2" * 32
    root, _directory, target = _setup_terminal_quota_env(tmp_path, req_id)
    outside = tmp_path / "outside_hardlink.json"
    os.link(target, outside)
    with pytest.raises(broker.BrokerError, match="invalid link count"):
        broker.read_terminal_quota_bytes(root, req_id)
    outside.unlink()


def test_read_terminal_quota_bytes_oversize(tmp_path: Path) -> None:
    req_id = "3" * 32
    root, _directory, _target = _setup_terminal_quota_env(
        tmp_path, req_id, b"x" * (broker.MAX_RECORD_BYTES + 1)
    )
    with pytest.raises(broker.BrokerError, match="exceeds byte limit"):
        broker.read_terminal_quota_bytes(root, req_id)


def test_read_terminal_quota_bytes_short_read(tmp_path: Path) -> None:
    req_id = "4" * 32
    root, _directory, _target = _setup_terminal_quota_env(tmp_path, req_id, b"1234567890")

    calls = 0

    def short_read(fd: int, n: int) -> bytes:
        nonlocal calls
        calls += 1
        if calls == 1:
            return b"123"
        return b""

    with mock.patch("os.read", side_effect=short_read):
        with pytest.raises(broker.BrokerError, match="read size mismatch"):
            broker.read_terminal_quota_bytes(root, req_id)


def test_read_terminal_quota_bytes_post_read_metadata_drift(tmp_path: Path) -> None:
    req_id = "5" * 32
    root, _directory, target = _setup_terminal_quota_env(tmp_path, req_id, b"test-data")

    real_read = os.read

    def drift_hook(fd: int, n: int) -> bytes:
        chunk = real_read(fd, n)
        st = os.stat(target)
        os.utime(target, ns=(st.st_atime_ns, st.st_mtime_ns + 10_000_000))
        return chunk

    with mock.patch("os.read", side_effect=drift_hook):
        with pytest.raises(broker.BrokerError, match="metadata drift"):
            broker.read_terminal_quota_bytes(root, req_id)
