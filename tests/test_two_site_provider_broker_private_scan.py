"""Tests for private record directory scanner in two_site_provider_broker."""
from __future__ import annotations

import os
from pathlib import Path
import stat
import sys
from unittest import mock
import pytest

from scripts import two_site_provider_broker as broker


def _make_dir(tmp_path: Path, name: str = "records") -> Path:
    directory = (tmp_path / name).resolve()
    directory.mkdir(parents=True, mode=0o700, exist_ok=True)
    os.chmod(directory, 0o700)
    return directory


def _make_record(directory: Path, hex_id: str, size: int = 16, mode: int = 0o600) -> Path:
    file_path = directory / f"{hex_id}.json"
    file_path.write_bytes(b"x" * size)
    os.chmod(file_path, mode)
    return file_path


def test_scan_empty_directory(tmp_path: Path) -> None:
    directory = _make_dir(tmp_path)
    assert broker.scan_private_record_directory(directory) == []


def test_scan_64_valid_entries(tmp_path: Path) -> None:
    directory = _make_dir(tmp_path)
    expected = []
    for i in range(broker.MAX_RECORD_SCAN_ENTRIES):
        hex_id = f"{i:032x}"
        _make_record(directory, hex_id)
        expected.append(f"{hex_id}.json")
    expected.sort()
    result = broker.scan_private_record_directory(directory)
    assert result == expected
    assert len(result) == 64


def test_scan_rejects_65th_entry(tmp_path: Path) -> None:
    directory = _make_dir(tmp_path)
    for i in range(65):
        _make_record(directory, f"{i:032x}")
    with pytest.raises(broker.BrokerError, match="exceeds scan entry limit"):
        broker.scan_private_record_directory(directory)


def test_scan_rejects_tmp_entry(tmp_path: Path) -> None:
    directory = _make_dir(tmp_path)
    _make_record(directory, f"{1:032x}")
    tmp_file = directory / f".{1:032x}.json.tmp"
    tmp_file.write_bytes(b"temp-data")
    os.chmod(tmp_file, 0o600)
    with pytest.raises(broker.BrokerError, match="invalid entry name"):
        broker.scan_private_record_directory(directory)


def test_scan_rejects_wrong_basename(tmp_path: Path) -> None:
    directory = _make_dir(tmp_path)
    bad_case = directory / ("A" * 32 + ".json")
    bad_case.write_bytes(b"data")
    os.chmod(bad_case, 0o600)
    with pytest.raises(broker.BrokerError, match="invalid entry name"):
        broker.scan_private_record_directory(directory)

    bad_case.unlink()
    bad_ext = directory / ("a" * 32 + ".txt")
    bad_ext.write_bytes(b"data")
    os.chmod(bad_ext, 0o600)
    with pytest.raises(broker.BrokerError, match="invalid entry name"):
        broker.scan_private_record_directory(directory)


def test_scan_rejects_mode_0755_entry(tmp_path: Path) -> None:
    directory = _make_dir(tmp_path)
    _make_record(directory, f"{1:032x}", mode=0o755)
    with pytest.raises(broker.BrokerError, match="mode must be 0600"):
        broker.scan_private_record_directory(directory)


def test_scan_rejects_symlink_entry_and_directory(tmp_path: Path) -> None:
    directory = _make_dir(tmp_path)
    outside_target = (tmp_path / "outside.json").resolve()
    outside_target.write_bytes(b"target")
    symlink_file = directory / f"{1:032x}.json"
    symlink_file.symlink_to(outside_target)
    with pytest.raises(broker.BrokerError, match="cannot be a symlink"):
        broker.scan_private_record_directory(directory)

    symlink_file.unlink()
    symlink_dir = (tmp_path / "symlink_dir").resolve()
    symlink_dir.symlink_to(directory)
    with pytest.raises(broker.BrokerError, match="symlink"):
        broker.scan_private_record_directory(symlink_dir)


def test_scan_rejects_oversized_entry(tmp_path: Path) -> None:
    directory = _make_dir(tmp_path)
    _make_record(directory, f"{1:032x}", size=broker.MAX_RECORD_BYTES + 1)
    with pytest.raises(broker.BrokerError, match="exceeds byte limit"):
        broker.scan_private_record_directory(directory)


def test_scan_rejects_hardlink_with_outside_link(tmp_path: Path) -> None:
    directory = _make_dir(tmp_path)
    record = _make_record(directory, f"{1:032x}")
    outside_link = (tmp_path / "outside_hardlink.json").resolve()
    os.link(record, outside_link)
    with pytest.raises(broker.BrokerError, match="has invalid link count"):
        broker.scan_private_record_directory(directory)


def test_scan_rejects_wrong_owner(tmp_path: Path) -> None:
    directory = _make_dir(tmp_path)
    _make_record(directory, f"{1:032x}")
    real_euid = os.geteuid()

    with mock.patch("os.geteuid", return_value=real_euid + 1000):
        with pytest.raises(broker.BrokerError, match=r"record directory is not owned by current euid"):
            broker.scan_private_record_directory(directory)

    call_count = [0]

    def fake_geteuid():
        call_count[0] += 1
        if call_count[0] >= 3:
            return real_euid + 1000
        return real_euid

    with mock.patch("os.geteuid", side_effect=fake_geteuid):
        with pytest.raises(broker.BrokerError, match=r"record entry .* owner mismatch"):
            broker.scan_private_record_directory(directory)


def test_scan_rejects_directory_replacement(tmp_path: Path) -> None:
    directory = _make_dir(tmp_path)
    _make_record(directory, f"{1:032x}")
    real_lstat = os.lstat
    real_scandir = os.scandir

    def fake_lstat_before(p, *args, **kwargs):
        st = real_lstat(p, *args, **kwargs)
        if str(p) == str(directory):
            return mock.MagicMock(
                st_mode=st.st_mode,
                st_uid=st.st_uid,
                st_dev=st.st_dev,
                st_ino=st.st_ino + 100000,
            )
        return st

    with mock.patch("os.lstat", side_effect=fake_lstat_before):
        with pytest.raises(broker.BrokerError, match="descriptor does not match path before scan"):
            broker.scan_private_record_directory(directory)

    mismatch_after = False

    def fake_lstat_after(p, *args, **kwargs):
        st = real_lstat(p, *args, **kwargs)
        if mismatch_after and str(p) == str(directory):
            return mock.MagicMock(
                st_mode=st.st_mode,
                st_uid=st.st_uid,
                st_dev=st.st_dev,
                st_ino=st.st_ino + 100000,
            )
        return st

    def hook_scandir(*args, **kwargs):
        nonlocal mismatch_after
        mismatch_after = True
        return real_scandir(*args, **kwargs)

    with mock.patch("os.lstat", side_effect=fake_lstat_after), mock.patch(
        "os.scandir", side_effect=hook_scandir
    ):
        with pytest.raises(broker.BrokerError, match="descriptor does not match path after scan"):
            broker.scan_private_record_directory(directory)


def test_scan_open_flags_include_nofollow(tmp_path: Path) -> None:
    directory = _make_dir(tmp_path)
    real_open = os.open
    captured_flags: list[int] = []

    def spy_open(path, flags, *args, **kwargs):
        captured_flags.append(flags)
        return real_open(path, flags, *args, **kwargs)

    with mock.patch("os.open", side_effect=spy_open):
        broker.scan_private_record_directory(directory)

    assert len(captured_flags) == 1
    assert (captured_flags[0] & os.O_NOFOLLOW) == os.O_NOFOLLOW
    assert (captured_flags[0] & os.O_DIRECTORY) == os.O_DIRECTORY


def test_scan_rejects_directory_mode_not_0700(tmp_path: Path) -> None:
    directory = _make_dir(tmp_path)
    os.chmod(directory, 0o755)
    with pytest.raises(broker.BrokerError, match="mode must be 0700"):
        broker.scan_private_record_directory(directory)


def test_scan_rejects_non_directory_path(tmp_path: Path) -> None:
    file_path = (tmp_path / "not_a_dir").resolve()
    file_path.write_bytes(b"content")
    os.chmod(file_path, 0o700)
    with pytest.raises(broker.BrokerError, match="must be a directory"):
        broker.scan_private_record_directory(file_path)


def test_scan_rejects_non_path_argument() -> None:
    with pytest.raises(broker.BrokerError, match="must be a Path"):
        broker.scan_private_record_directory("/not/a/path")  # type: ignore[arg-type]
