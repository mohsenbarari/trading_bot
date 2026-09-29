"""Adversarial fault suite for scripts/two_site_quota_codex_result_custody.py."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
from pathlib import Path
import stat
from typing import Any
from unittest import mock
import pytest

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"
try:
    from tests.test_p1_00_characterization_network_isolation import (
        install_p1_00_network_fence,
    )
except ImportError:
    from test_p1_00_characterization_network_isolation import (
        install_p1_00_network_fence,
    )

install_p1_00_network_fence()

from scripts.two_site_provider_broker import BrokerError
import scripts.two_site_quota_codex_result_custody as custody_mod
from scripts.two_site_quota_codex_result_custody import (
    CODEX_RESULT_FILENAME,
    MAX_CODEX_RESULT_BYTES,
    CodexResultCustodyError,
    ResultCustodyError,
    read_untrusted_codex_result_candidate,
)


class ConcreteStat:
    """Concrete stat object with strict integer fields avoiding MagicMock stat TypeError."""

    def __init__(
        self,
        st_mode: int = 0o100644,
        st_uid: int | None = None,
        st_nlink: int = 1,
        st_size: int = 100,
        st_dev: int = 1,
        st_ino: int = 1,
        st_mtime_ns: int = 1000,
        st_ctime_ns: int = 1000,
    ):
        self.st_mode = int(st_mode)
        self.st_uid = os.geteuid() if st_uid is None else int(st_uid)
        self.st_nlink = int(st_nlink)
        self.st_size = int(st_size)
        self.st_dev = int(st_dev)
        self.st_ino = int(st_ino)
        self.st_mtime_ns = int(st_mtime_ns)
        self.st_ctime_ns = int(st_ctime_ns)


@contextlib.contextmanager
def _mock_open_cycle_dir(run_path: Path, verify_callback: Any = None):
    fd = os.open(
        str(run_path),
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0),
    )
    try:
        def verify():
            if verify_callback is not None:
                verify_callback()

        yield run_path, fd, verify
    finally:
        os.close(fd)


def _setup_run_dir(
    tmp_path: Path,
    filename: str = CODEX_RESULT_FILENAME,
    content: bytes = b'{"status": "PROPOSAL_READY"}',
) -> tuple[Path, Path]:
    cid = "cycle-0000-20260901T000000Z-999"
    run_dir = tmp_path / cid
    run_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
    target = run_dir / filename
    target.write_bytes(content)
    os.chmod(target, 0o644)
    return run_dir, target


def _snapshot_file(p: Path) -> tuple[bytes, int, int, int]:
    raw = p.read_bytes()
    st = os.stat(p)
    return raw, stat.S_IMODE(st.st_mode), st.st_ino, st.st_mtime_ns


def _assert_file_unchanged(p: Path, snap: tuple[bytes, int, int, int]) -> None:
    raw, mode, ino, mtime = snap
    assert p.read_bytes() == raw
    st = os.stat(p)
    assert stat.S_IMODE(st.st_mode) == mode
    assert st.st_ino == ino
    assert st.st_mtime_ns == mtime


def test_descriptor_close_both_paths(tmp_path: Path) -> None:
    cid = "cycle-0000-20260901T000000Z-999"
    run_dir, target = _setup_run_dir(tmp_path)
    snap = _snapshot_file(target)
    real_close = os.close

    # Path A: Clean read, os.close raises OSError -> fails closed with CodexResultCustodyError
    closed_fds_a: list[int] = []

    def fake_close_clean(fd: int) -> None:
        st = os.fstat(fd)
        if stat.S_ISDIR(st.st_mode):
            real_close(fd)
            return
        closed_fds_a.append(fd)
        real_close(fd)
        raise OSError("descriptor close flush failure")

    with mock.patch.object(
        custody_mod,
        "_open_existing_cycle_run_directory",
        side_effect=lambda r, c: _mock_open_cycle_dir(run_dir),
    ):
        with mock.patch("os.close", side_effect=fake_close_clean):
            with pytest.raises(
                CodexResultCustodyError,
                match="cannot safely close result.json descriptor: descriptor close flush failure",
            ):
                read_untrusted_codex_result_candidate(tmp_path, cid)

    assert len(closed_fds_a) == 1
    _assert_file_unchanged(target, snap)

    # Path B: Prior read error, os.close raises OSError -> preserves original read error
    closed_fds_b: list[int] = []

    def fake_close_err(fd: int) -> None:
        st = os.fstat(fd)
        if stat.S_ISDIR(st.st_mode):
            real_close(fd)
            return
        closed_fds_b.append(fd)
        real_close(fd)
        raise OSError("descriptor close cleanup failure")

    def fail_read(fd: int, n: int) -> bytes:
        raise OSError("underlying disk read failure")

    with mock.patch.object(
        custody_mod,
        "_open_existing_cycle_run_directory",
        side_effect=lambda r, c: _mock_open_cycle_dir(run_dir),
    ):
        with mock.patch("os.read", side_effect=fail_read):
            with mock.patch("os.close", side_effect=fake_close_err):
                with pytest.raises(CodexResultCustodyError, match="cannot read result.json") as exc_info:
                    read_untrusted_codex_result_candidate(tmp_path, cid)
                assert "cannot safely close" not in str(exc_info.value)

    assert len(closed_fds_b) == 1
    _assert_file_unchanged(target, snap)

    # Path C: Prior validation error (invalid JSON), os.close raises OSError -> preserves validation error
    target.write_bytes(b"invalid json")
    snap_invalid = _snapshot_file(target)
    closed_fds_c: list[int] = []

    def fake_close_val(fd: int) -> None:
        st = os.fstat(fd)
        if stat.S_ISDIR(st.st_mode):
            real_close(fd)
            return
        closed_fds_c.append(fd)
        real_close(fd)
        raise OSError("descriptor close validation cleanup failure")

    with mock.patch.object(
        custody_mod,
        "_open_existing_cycle_run_directory",
        side_effect=lambda r, c: _mock_open_cycle_dir(run_dir),
    ):
        with mock.patch("os.close", side_effect=fake_close_val):
            with pytest.raises(CodexResultCustodyError, match="is not valid JSON") as exc_info:
                read_untrusted_codex_result_candidate(tmp_path, cid)
            assert "cannot safely close" not in str(exc_info.value)

    assert len(closed_fds_c) == 1
    _assert_file_unchanged(target, snap_invalid)


def test_file_existence_and_link_faults(tmp_path: Path) -> None:
    cid = "cycle-0000-20260901T000000Z-999"
    run_dir = tmp_path / cid
    run_dir.mkdir(parents=True, mode=0o700, exist_ok=True)

    # Missing file
    with mock.patch.object(
        custody_mod,
        "_open_existing_cycle_run_directory",
        side_effect=lambda r, c: _mock_open_cycle_dir(run_dir),
    ):
        with pytest.raises(CodexResultCustodyError, match="cannot lstat result.json"):
            read_untrusted_codex_result_candidate(tmp_path, cid)

    # Setup real file
    target = run_dir / CODEX_RESULT_FILENAME
    target.write_bytes(b'{"status": "ok"}')
    os.chmod(target, 0o644)
    snap = _snapshot_file(target)

    # Hardlink fault (nlink > 1)
    hardlink = run_dir / "hardlink_alias.json"
    os.link(target, hardlink)
    try:
        with mock.patch.object(
            custody_mod,
            "_open_existing_cycle_run_directory",
            side_effect=lambda r, c: _mock_open_cycle_dir(run_dir),
        ):
            with pytest.raises(CodexResultCustodyError, match="invalid link count"):
                read_untrusted_codex_result_candidate(tmp_path, cid)
    finally:
        hardlink.unlink()
    _assert_file_unchanged(target, snap)

    # Symlink fault
    real_file = run_dir / "target_store.json"
    target.rename(real_file)
    target.symlink_to(real_file)
    try:
        with mock.patch.object(
            custody_mod,
            "_open_existing_cycle_run_directory",
            side_effect=lambda r, c: _mock_open_cycle_dir(run_dir),
        ):
            with pytest.raises(CodexResultCustodyError, match="is not a regular file"):
                read_untrusted_codex_result_candidate(tmp_path, cid)
    finally:
        target.unlink()
        real_file.rename(target)
    _assert_file_unchanged(target, snap)

    # Forbidden mode bits
    os.chmod(target, 0o755)
    with mock.patch.object(
        custody_mod,
        "_open_existing_cycle_run_directory",
        side_effect=lambda r, c: _mock_open_cycle_dir(run_dir),
    ):
        with pytest.raises(CodexResultCustodyError, match="forbidden permission bits"):
            read_untrusted_codex_result_candidate(tmp_path, cid)
    os.chmod(target, 0o644)
    _assert_file_unchanged(target, snap)


def test_empty_and_oversized_faults(tmp_path: Path) -> None:
    cid = "cycle-0000-20260901T000000Z-999"
    run_dir = tmp_path / cid
    run_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
    target = run_dir / CODEX_RESULT_FILENAME

    # Empty
    target.write_bytes(b"")
    os.chmod(target, 0o644)
    snap_empty = _snapshot_file(target)
    with mock.patch.object(
        custody_mod,
        "_open_existing_cycle_run_directory",
        side_effect=lambda r, c: _mock_open_cycle_dir(run_dir),
    ):
        with pytest.raises(CodexResultCustodyError, match="is empty"):
            read_untrusted_codex_result_candidate(tmp_path, cid)
    _assert_file_unchanged(target, snap_empty)

    # Oversized
    target.write_bytes(b"x" * (MAX_CODEX_RESULT_BYTES + 1))
    os.chmod(target, 0o644)
    snap_over = _snapshot_file(target)
    with mock.patch.object(
        custody_mod,
        "_open_existing_cycle_run_directory",
        side_effect=lambda r, c: _mock_open_cycle_dir(run_dir),
    ):
        with pytest.raises(CodexResultCustodyError, match="exceeds byte limit"):
            read_untrusted_codex_result_candidate(tmp_path, cid)
    _assert_file_unchanged(target, snap_over)


def test_duplicate_key_and_nonobject_json_faults(tmp_path: Path) -> None:
    cid = "cycle-0000-20260901T000000Z-999"
    run_dir = tmp_path / cid
    run_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
    target = run_dir / CODEX_RESULT_FILENAME

    fault_cases = [
        (b'{"key": 1, "key": 2}', "is not valid JSON"),
        (b'["array", "item"]', "must be a JSON object"),
        (b'"standalone string"', "must be a JSON object"),
        (b"12345", "must be a JSON object"),
        (b"true", "must be a JSON object"),
        (b'{"key": invalid_json_syntax}', "is not valid JSON"),
        (b"\xff\xfe\x00\x00corrupt_utf8", "is not valid UTF-8"),
    ]
    for payload_bytes, expected_match in fault_cases:
        target.write_bytes(payload_bytes)
        os.chmod(target, 0o644)
        snap = _snapshot_file(target)
        with mock.patch.object(
            custody_mod,
            "_open_existing_cycle_run_directory",
            side_effect=lambda r, c: _mock_open_cycle_dir(run_dir),
        ):
            with pytest.raises(CodexResultCustodyError, match=expected_match):
                read_untrusted_codex_result_candidate(tmp_path, cid)
        _assert_file_unchanged(target, snap)


def test_metadata_divergence_faults(tmp_path: Path) -> None:
    cid = "cycle-0000-20260901T000000Z-999"
    run_dir, target = _setup_run_dir(tmp_path)
    snap = _snapshot_file(target)
    real_stat = os.stat
    real_fstat = os.fstat
    real_lstat = os.lstat

    # 1. dirfd_stat_before mismatch
    def fake_stat_dirfd_mismatch(path, *args, **kwargs):
        if kwargs.get("dir_fd") is not None:
            st = real_stat(path, *args, **kwargs)
            return ConcreteStat(st_mode=st.st_mode, st_ino=st.st_ino + 999, st_size=st.st_size)
        return real_stat(path, *args, **kwargs)

    with mock.patch.object(
        custody_mod,
        "_open_existing_cycle_run_directory",
        side_effect=lambda r, c: _mock_open_cycle_dir(run_dir),
    ):
        with mock.patch("os.stat", side_effect=fake_stat_dirfd_mismatch):
            with pytest.raises(CodexResultCustodyError, match="dir_fd stat mismatch before open"):
                read_untrusted_codex_result_candidate(tmp_path, cid)
    _assert_file_unchanged(target, snap)

    # 2. descriptor metadata mismatch before read
    def fake_fstat_before_mismatch(fd: int):
        st = real_fstat(fd)
        if stat.S_ISREG(st.st_mode):
            return ConcreteStat(st_mode=st.st_mode, st_ino=st.st_ino + 888, st_size=st.st_size)
        return st

    with mock.patch.object(
        custody_mod,
        "_open_existing_cycle_run_directory",
        side_effect=lambda r, c: _mock_open_cycle_dir(run_dir),
    ):
        with mock.patch("os.fstat", side_effect=fake_fstat_before_mismatch):
            with pytest.raises(CodexResultCustodyError, match="descriptor metadata mismatch before read"):
                read_untrusted_codex_result_candidate(tmp_path, cid)
    _assert_file_unchanged(target, snap)

    # 3. descriptor metadata changed after read
    fstat_calls = 0

    def fake_fstat_after_mismatch(fd: int):
        nonlocal fstat_calls
        st = real_fstat(fd)
        if stat.S_ISREG(st.st_mode):
            fstat_calls += 1
            if fstat_calls > 1:
                return ConcreteStat(st_mode=st.st_mode, st_ino=st.st_ino + 777, st_size=st.st_size)
        return st

    with mock.patch.object(
        custody_mod,
        "_open_existing_cycle_run_directory",
        side_effect=lambda r, c: _mock_open_cycle_dir(run_dir),
    ):
        with mock.patch("os.fstat", side_effect=fake_fstat_after_mismatch):
            with pytest.raises(CodexResultCustodyError, match="descriptor metadata changed after read"):
                read_untrusted_codex_result_candidate(tmp_path, cid)
    _assert_file_unchanged(target, snap)

    # 4. dir_fd metadata changed after read
    dirfd_stat_calls = 0

    def fake_dirfd_stat_after(path, *args, **kwargs):
        nonlocal dirfd_stat_calls
        st = real_stat(path, *args, **kwargs)
        if kwargs.get("dir_fd") is not None:
            dirfd_stat_calls += 1
            if dirfd_stat_calls > 1:
                return ConcreteStat(st_mode=st.st_mode, st_ino=st.st_ino + 666, st_size=st.st_size)
        return st

    with mock.patch.object(
        custody_mod,
        "_open_existing_cycle_run_directory",
        side_effect=lambda r, c: _mock_open_cycle_dir(run_dir),
    ):
        with mock.patch("os.stat", side_effect=fake_dirfd_stat_after):
            with pytest.raises(CodexResultCustodyError, match="dir_fd metadata changed after read"):
                read_untrusted_codex_result_candidate(tmp_path, cid)
    _assert_file_unchanged(target, snap)

    # 5. path metadata changed after read
    lstat_calls = 0

    def fake_lstat_after(path, *args, **kwargs):
        nonlocal lstat_calls
        st = real_lstat(path, *args, **kwargs)
        lstat_calls += 1
        if lstat_calls > 1:
            return ConcreteStat(st_mode=st.st_mode, st_ino=st.st_ino + 555, st_size=st.st_size)
        return st

    with mock.patch.object(
        custody_mod,
        "_open_existing_cycle_run_directory",
        side_effect=lambda r, c: _mock_open_cycle_dir(run_dir),
    ):
        with mock.patch("os.lstat", side_effect=fake_lstat_after):
            with pytest.raises(CodexResultCustodyError, match="path metadata changed after read"):
                read_untrusted_codex_result_candidate(tmp_path, cid)
    _assert_file_unchanged(target, snap)


def test_short_mutated_and_read_error_faults(tmp_path: Path) -> None:
    cid = "cycle-0000-20260901T000000Z-999"
    content = b'{"status": "PROPOSAL_READY", "key": "value"}'
    run_dir, target = _setup_run_dir(tmp_path, content=content)
    snap = _snapshot_file(target)

    # Read error
    def fail_read(fd: int, n: int) -> bytes:
        raise OSError("underlying read fault")

    with mock.patch.object(
        custody_mod,
        "_open_existing_cycle_run_directory",
        side_effect=lambda r, c: _mock_open_cycle_dir(run_dir),
    ):
        with mock.patch("os.read", side_effect=fail_read):
            with pytest.raises(CodexResultCustodyError, match="cannot read result.json"):
                read_untrusted_codex_result_candidate(tmp_path, cid)
    _assert_file_unchanged(target, snap)

    # Short read
    short_read_done = False

    def short_read(fd: int, n: int) -> bytes:
        nonlocal short_read_done
        if not short_read_done:
            short_read_done = True
            return b'{"truncated": true}'
        return b""

    with mock.patch.object(
        custody_mod,
        "_open_existing_cycle_run_directory",
        side_effect=lambda r, c: _mock_open_cycle_dir(run_dir),
    ):
        with mock.patch("os.read", side_effect=short_read):
            with pytest.raises(CodexResultCustodyError, match="read size mismatch"):
                read_untrusted_codex_result_candidate(tmp_path, cid)
    _assert_file_unchanged(target, snap)

    # Mutated caller copy isolation
    with mock.patch.object(
        custody_mod,
        "_open_existing_cycle_run_directory",
        side_effect=lambda r, c: _mock_open_cycle_dir(run_dir),
    ):
        raw1, sha1, payload1 = read_untrusted_codex_result_candidate(tmp_path, cid)
        assert raw1 == content
        payload1["injected"] = "mutation"

        raw2, sha2, payload2 = read_untrusted_codex_result_candidate(tmp_path, cid)
        assert raw2 == content
        assert sha2 == sha1
        assert "injected" not in payload2
    _assert_file_unchanged(target, snap)


def test_verify_identity_faults(tmp_path: Path) -> None:
    cid = "cycle-0000-20260901T000000Z-999"
    run_dir, target = _setup_run_dir(tmp_path)
    snap = _snapshot_file(target)

    # Identity verification fails before read
    def fail_verify_before():
        raise BrokerError("broker directory compromised before read")

    with mock.patch.object(
        custody_mod,
        "_open_existing_cycle_run_directory",
        side_effect=lambda r, c: _mock_open_cycle_dir(run_dir, verify_callback=fail_verify_before),
    ):
        with pytest.raises(
            CodexResultCustodyError, match="directory identity verification failed before read"
        ):
            read_untrusted_codex_result_candidate(tmp_path, cid)
    _assert_file_unchanged(target, snap)

    # Identity verification fails after read
    verify_calls = 0

    def fail_verify_after():
        nonlocal verify_calls
        verify_calls += 1
        if verify_calls > 1:
            raise BrokerError("broker directory compromised after read")

    with mock.patch.object(
        custody_mod,
        "_open_existing_cycle_run_directory",
        side_effect=lambda r, c: _mock_open_cycle_dir(run_dir, verify_callback=fail_verify_after),
    ):
        with pytest.raises(
            CodexResultCustodyError, match="directory identity verification failed after read"
        ):
            read_untrusted_codex_result_candidate(tmp_path, cid)
    _assert_file_unchanged(target, snap)


def test_no_live_authority_invariants() -> None:
    assert ResultCustodyError is CodexResultCustodyError
    forbidden_attrs = [
        "write",
        "unlink",
        "lock",
        "wake",
        "execute",
        "replay",
        "ack",
        "acquire_lock",
        "acquire_provider",
    ]
    for attr in forbidden_attrs:
        assert not hasattr(custody_mod, attr), f"custody module must not expose authority {attr!r}"

    assert set(custody_mod.__all__) == {
        "CODEX_RESULT_FILENAME",
        "MAX_CODEX_RESULT_BYTES",
        "CodexResultCustodyError",
        "ResultCustodyError",
        "read_untrusted_codex_result_candidate",
    }
