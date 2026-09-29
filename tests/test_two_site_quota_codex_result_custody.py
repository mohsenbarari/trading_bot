"""Hermetic tests for scripts/two_site_quota_codex_result_custody.py."""

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

from scripts.two_site_provider_broker import BrokerError, CYCLE_RUN_RE
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


def test_positive_real_cycle_dir_and_whitespace_0644_proof(tmp_path: Path) -> None:
    cid = "cycle-0000-20260901T000000Z-999"
    root = tmp_path.resolve()
    cur = root
    os.chmod(cur, 0o700)
    for comp in [".local", "two-site-refactor", "autopilot", "runs", cid]:
        cur = cur / comp
        cur.mkdir(mode=0o700, exist_ok=True)
        os.chmod(cur, 0o700)
    run_dir = cur

    raw_content = (
        b'{\n'
        b'  "status": "PROPOSAL_READY",\n'
        b'  "summary": "deliberate whitespace noncanonical",\n'
        b'  "covered_requirements": [ 1,  2,   3 ],\n'
        b'  "findings": [   ],\n'
        b'  "changes": [  ]\n'
        b'}\n'
    )
    target_file = run_dir / CODEX_RESULT_FILENAME
    target_file.write_bytes(raw_content)
    os.chmod(target_file, 0o644)

    stat_before = os.stat(target_file)
    mtime_before = stat_before.st_mtime_ns
    ino_before = stat_before.st_ino

    raw_bytes, sha256_hex, payload = read_untrusted_codex_result_candidate(root, cid)

    assert raw_bytes == raw_content
    expected_sha = hashlib.sha256(raw_content).hexdigest()
    assert sha256_hex == expected_sha

    canonical_bytes = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    assert canonical_bytes != raw_content
    assert hashlib.sha256(canonical_bytes).hexdigest() != sha256_hex

    raw_bytes2, sha256_hex2, payload2 = read_untrusted_codex_result_candidate(root, cid)
    assert raw_bytes2 == raw_content
    assert sha256_hex2 == expected_sha
    assert payload2 == payload
    payload["mutated"] = True
    assert "mutated" not in payload2

    assert target_file.read_bytes() == raw_content
    stat_after = os.stat(target_file)
    assert stat.S_IMODE(stat_after.st_mode) == 0o644
    assert stat_after.st_mtime_ns == mtime_before
    assert stat_after.st_ino == ino_before


def test_invalid_arguments_fail_before_io(tmp_path: Path) -> None:
    valid_cid = "cycle-0000-20260901T000000Z-999"

    for bad_root, match_str in [\
        ("not_a_path", "root must be a Path"),\
        (123, "root must be a Path"),\
        (None, "root must be a Path"),\
    ]:
        with pytest.raises(CodexResultCustodyError, match=match_str):
            read_untrusted_codex_result_candidate(bad_root, valid_cid)  # type: ignore

    for bad_cid, match_str in [\
        (123, "continuation_controller_run_id must be exact str"),\
        (None, "continuation_controller_run_id must be exact str"),\
        ([], "continuation_controller_run_id must be exact str"),\
        ("", "continuation_controller_run_id must be a non-empty string"),\
        ("invalid-cid", "is not a valid cycle run ID"),\
        ("../../escape", "is not a valid cycle run ID"),\
    ]:
        with pytest.raises(CodexResultCustodyError, match=match_str):
            read_untrusted_codex_result_candidate(tmp_path, bad_cid)  # type: ignore

    def raise_on_open(*args, **kwargs):
        raise AssertionError("I/O attempted on invalid arguments")

    with mock.patch("os.open", side_effect=raise_on_open):
        with pytest.raises(CodexResultCustodyError, match="root must be a Path"):
            read_untrusted_codex_result_candidate("invalid", valid_cid)  # type: ignore
        with pytest.raises(CodexResultCustodyError, match="is not a valid cycle run ID"):
            read_untrusted_codex_result_candidate(tmp_path, "invalid-cid")


def test_mode_and_ownership_and_link_checks(tmp_path: Path) -> None:
    cid = "cycle-0000-20260901T000000Z-999"
    run_dir = tmp_path / cid
    run_dir.mkdir(parents=True, mode=0o700)
    target = run_dir / CODEX_RESULT_FILENAME
    raw_json = b'{"key":"value"}'
    target.write_bytes(raw_json)

    os.chmod(target, 0o600)
    with mock.patch.object(\
        custody_mod,\
        "_open_existing_cycle_run_directory",\
        side_effect=lambda r, c: _mock_open_cycle_dir(run_dir),\
    ):
        raw, sha, d = read_untrusted_codex_result_candidate(tmp_path, cid)
        assert d == {"key": "value"}
        assert raw == raw_json

    for bad_mode in [0o664, 0o646, 0o755, 0o700, 0o645, 0o654]:
        os.chmod(target, bad_mode)
        with mock.patch.object(\
            custody_mod,\
            "_open_existing_cycle_run_directory",\
            side_effect=lambda r, c: _mock_open_cycle_dir(run_dir),\
        ):
            with pytest.raises(CodexResultCustodyError, match="forbidden permission bits"):
                read_untrusted_codex_result_candidate(tmp_path, cid)
    os.chmod(target, 0o644)

    with mock.patch.object(\
        custody_mod,\
        "_open_existing_cycle_run_directory",\
        side_effect=lambda r, c: _mock_open_cycle_dir(run_dir),\
    ):
        with mock.patch(\
            "os.lstat",\
            return_value=ConcreteStat(\
                st_uid=os.geteuid() + 1000, st_size=len(raw_json)\
            ),\
        ):
            with pytest.raises(CodexResultCustodyError, match="owner mismatch"):
                read_untrusted_codex_result_candidate(tmp_path, cid)

    hardlink = run_dir / "hardlink.json"
    os.link(target, hardlink)
    with mock.patch.object(\
        custody_mod,\
        "_open_existing_cycle_run_directory",\
        side_effect=lambda r, c: _mock_open_cycle_dir(run_dir),\
    ):
        with pytest.raises(CodexResultCustodyError, match="invalid link count"):
            read_untrusted_codex_result_candidate(tmp_path, cid)
    hardlink.unlink()

    target.unlink()
    real_file = tmp_path / "real.json"
    real_file.write_bytes(raw_json)
    target.symlink_to(real_file)
    with mock.patch.object(\
        custody_mod,\
        "_open_existing_cycle_run_directory",\
        side_effect=lambda r, c: _mock_open_cycle_dir(run_dir),\
    ):
        with pytest.raises(CodexResultCustodyError, match="is not a regular file"):
            read_untrusted_codex_result_candidate(tmp_path, cid)


def test_content_and_size_and_json_safety(tmp_path: Path) -> None:
    cid = "cycle-0000-20260901T000000Z-999"
    run_dir = tmp_path / cid
    run_dir.mkdir(parents=True, mode=0o700)
    target = run_dir / CODEX_RESULT_FILENAME

    with mock.patch.object(\
        custody_mod,\
        "_open_existing_cycle_run_directory",\
        side_effect=lambda r, c: _mock_open_cycle_dir(run_dir),\
    ):
        with pytest.raises(CodexResultCustodyError, match="cannot lstat result.json"):
            read_untrusted_codex_result_candidate(tmp_path, cid)

    cases = [\
        (b"", "is empty"),\
        (b"x" * (MAX_CODEX_RESULT_BYTES + 1), "exceeds byte limit"),\
        (b"\xff\xfe\xfd", "is not valid UTF-8"),\
        (b"not json content", "is not valid JSON"),\
        (b'{"key":"a", "key":"b"}', "is not valid JSON"),\
        (b'["array", "not", "object"]', "must be a JSON object"),\
        (b'"just a string"', "must be a JSON object"),\
    ]

    for raw_bytes, match_str in cases:
        target.write_bytes(raw_bytes)
        os.chmod(target, 0o644)
        with mock.patch.object(\
            custody_mod,\
            "_open_existing_cycle_run_directory",\
            side_effect=lambda r, c: _mock_open_cycle_dir(run_dir),\
        ):
            with pytest.raises(CodexResultCustodyError, match=match_str):
                read_untrusted_codex_result_candidate(tmp_path, cid)


def test_metadata_divergence_and_identity_and_closure_failures(tmp_path: Path) -> None:
    cid = "cycle-0000-20260901T000000Z-999"
    run_dir = tmp_path / cid
    run_dir.mkdir(parents=True, mode=0o700)
    target = run_dir / CODEX_RESULT_FILENAME
    raw_bytes = b'{"status": "ok"}'
    target.write_bytes(raw_bytes)
    os.chmod(target, 0o644)

    def raise_broker(r, c):
        raise BrokerError("broker open failure")

    with mock.patch.object(\
        custody_mod, "_open_existing_cycle_run_directory", side_effect=raise_broker\
    ):
        with pytest.raises(CodexResultCustodyError, match="cannot safely open cycle run directory"):
            read_untrusted_codex_result_candidate(tmp_path, cid)

    def fail_identity_before():
        raise BrokerError("identity mismatch before")

    with mock.patch.object(\
        custody_mod,\
        "_open_existing_cycle_run_directory",\
        side_effect=lambda r, c: _mock_open_cycle_dir(run_dir, verify_callback=fail_identity_before),\
    ):
        with pytest.raises(CodexResultCustodyError, match="directory identity verification failed before read"):
            read_untrusted_codex_result_candidate(tmp_path, cid)

    ident_count = 0
    def fail_identity_after():
        nonlocal ident_count
        ident_count += 1
        if ident_count > 1:
            raise BrokerError("identity mismatch after")

    with mock.patch.object(\
        custody_mod,\
        "_open_existing_cycle_run_directory",\
        side_effect=lambda r, c: _mock_open_cycle_dir(run_dir, verify_callback=fail_identity_after),\
    ):
        with pytest.raises(CodexResultCustodyError, match="directory identity verification failed after read"):
            read_untrusted_codex_result_candidate(tmp_path, cid)

    real_stat = os.stat
    def fake_stat_dirfd_mismatch(path, *args, **kwargs):
        if kwargs.get("dir_fd") is not None:
            return ConcreteStat(st_ino=999, st_size=len(raw_bytes))
        return real_stat(path, *args, **kwargs)

    with mock.patch.object(\
        custody_mod,\
        "_open_existing_cycle_run_directory",\
        side_effect=lambda r, c: _mock_open_cycle_dir(run_dir),\
    ):
        with mock.patch("os.stat", side_effect=fake_stat_dirfd_mismatch):
            with pytest.raises(CodexResultCustodyError, match="dir_fd stat mismatch before open"):
                read_untrusted_codex_result_candidate(tmp_path, cid)

    real_fstat = os.fstat
    def fake_fstat_before(fd):
        st = real_fstat(fd)
        if stat.S_ISREG(st.st_mode):
            return ConcreteStat(st_ino=888, st_size=len(raw_bytes))
        return st

    with mock.patch.object(\
        custody_mod,\
        "_open_existing_cycle_run_directory",\
        side_effect=lambda r, c: _mock_open_cycle_dir(run_dir),\
    ):
        with mock.patch("os.fstat", side_effect=fake_fstat_before):
            with pytest.raises(CodexResultCustodyError, match="descriptor metadata mismatch before read"):
                read_untrusted_codex_result_candidate(tmp_path, cid)

    fstat_count = 0
    def fake_fstat_after(fd):
        nonlocal fstat_count
        st = real_fstat(fd)
        if stat.S_ISREG(st.st_mode):
            fstat_count += 1
            if fstat_count > 1:
                return ConcreteStat(st_ino=777, st_size=len(raw_bytes))
        return st

    with mock.patch.object(\
        custody_mod,\
        "_open_existing_cycle_run_directory",\
        side_effect=lambda r, c: _mock_open_cycle_dir(run_dir),\
    ):
        with mock.patch("os.fstat", side_effect=fake_fstat_after):
            with pytest.raises(CodexResultCustodyError, match="descriptor metadata changed after read"):
                read_untrusted_codex_result_candidate(tmp_path, cid)

    real_close = os.close
    closed_fds: list[int] = []
    def track_close(fd: int) -> None:
        closed_fds.append(fd)
        real_close(fd)

    def fail_read(fd: int, n: int) -> bytes:
        raise OSError("simulated read error")

    with mock.patch.object(\
        custody_mod,\
        "_open_existing_cycle_run_directory",\
        side_effect=lambda r, c: _mock_open_cycle_dir(run_dir),\
    ):
        with mock.patch("os.read", side_effect=fail_read):
            with mock.patch("os.close", side_effect=track_close):
                with pytest.raises(CodexResultCustodyError, match="cannot read result.json"):
                    read_untrusted_codex_result_candidate(tmp_path, cid)

    assert len(closed_fds) >= 1


def test_no_semantic_acceptance_or_execution_authority() -> None:
    assert ResultCustodyError is CodexResultCustodyError
    assert hasattr(custody_mod, "read_untrusted_codex_result_candidate")
    assert not hasattr(custody_mod, "write")
    assert not hasattr(custody_mod, "unlink")
    assert not hasattr(custody_mod, "lock")
    assert not hasattr(custody_mod, "wake")
    assert not hasattr(custody_mod, "execute")
    assert not hasattr(custody_mod, "replay")
    assert not hasattr(custody_mod, "ack")
    assert not hasattr(custody_mod, "acquire_lock")
    assert not hasattr(custody_mod, "acquire_provider")
    assert set(custody_mod.__all__) == {
        "CODEX_RESULT_FILENAME",
        "MAX_CODEX_RESULT_BYTES",
        "CodexResultCustodyError",
        "ResultCustodyError",
        "read_untrusted_codex_result_candidate",
    }
