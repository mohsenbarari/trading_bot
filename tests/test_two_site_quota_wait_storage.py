from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

try:
    from scripts.two_site_quota_wait_storage import (
        MAX_WAIT_RECORD_BYTES,
        QUOTA_WAIT_FILENAME,
        WaitStorageError,
        read_initial_wait_record_bytes,
    )
except ImportError:
    from two_site_quota_wait_storage import (  # type: ignore
        MAX_WAIT_RECORD_BYTES,
        QUOTA_WAIT_FILENAME,
        WaitStorageError,
        read_initial_wait_record_bytes,
    )

try:
    from tests.test_two_site_provider_broker_cycle_reservation_read import (
        CYCLE_ID,
        _setup_cycle_root,
    )
except ImportError:
    try:
        from test_two_site_provider_broker_cycle_reservation_read import (  # type: ignore
            CYCLE_ID,
            _setup_cycle_root,
        )
    except ImportError:
        CYCLE_ID = "cycle-20260925T000000Z"

        def _setup_cycle_root(tmp_dir: str | Path, cycle_id: str = CYCLE_ID) -> tuple[Path, Path]:
            root = Path(tmp_dir).resolve(strict=True)
            os.chmod(root, 0o700)
            runs_dir = root / ".local" / "two-site-refactor" / "autopilot" / "runs" / cycle_id
            runs_dir.mkdir(parents=True, mode=0o700)
            curr = root
            for comp in (".local", "two-site-refactor", "autopilot", "runs", cycle_id):
                curr = curr / comp
                os.chmod(curr, 0o700)
            return root, runs_dir


def _write_wait_file(run_dir: Path, content: bytes, mode: int = 0o600) -> Path:
    file_path = run_dir / QUOTA_WAIT_FILENAME
    file_path.write_bytes(content)
    os.chmod(file_path, mode)
    return file_path


def test_read_initial_wait_record_bytes_success(tmp_path: Path) -> None:
    root, run_dir = _setup_cycle_root(str(tmp_path))
    payload = {"quota_wait": 120, "reason": "rate_limited"}
    canonical_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    _write_wait_file(run_dir, canonical_bytes)

    result = read_initial_wait_record_bytes(root, CYCLE_ID)
    assert result == canonical_bytes


def test_16kib_boundary_success_and_16385_rejection(tmp_path: Path) -> None:
    root, run_dir = _setup_cycle_root(str(tmp_path))
    prefix_len = len('{"p":"')
    suffix_len = len('"}')
    target_padding_len = MAX_WAIT_RECORD_BYTES - prefix_len - suffix_len
    assert target_padding_len > 0

    valid_payload = {"p": "a" * target_padding_len}
    valid_bytes = json.dumps(valid_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    assert len(valid_bytes) == MAX_WAIT_RECORD_BYTES

    _write_wait_file(run_dir, valid_bytes)
    result = read_initial_wait_record_bytes(root, CYCLE_ID)
    assert result == valid_bytes

    oversized_payload = {"p": "a" * (target_padding_len + 1)}
    oversized_bytes = json.dumps(oversized_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    assert len(oversized_bytes) == MAX_WAIT_RECORD_BYTES + 1

    _write_wait_file(run_dir, oversized_bytes)
    with pytest.raises(WaitStorageError, match="exceeds byte limit"):
        read_initial_wait_record_bytes(root, CYCLE_ID)


def test_reject_missing_wait_file(tmp_path: Path) -> None:
    root, _ = _setup_cycle_root(str(tmp_path))
    with pytest.raises(WaitStorageError, match=f"cannot lstat {QUOTA_WAIT_FILENAME}"):
        read_initial_wait_record_bytes(root, CYCLE_ID)


def test_reject_invalid_controller_id(tmp_path: Path) -> None:
    root, _ = _setup_cycle_root(str(tmp_path))
    with pytest.raises(WaitStorageError, match="controller_run_id must be a non-empty string"):
        read_initial_wait_record_bytes(root, "")
    with pytest.raises(WaitStorageError, match="controller_run_id must be a non-empty string"):
        read_initial_wait_record_bytes(root, None)  # type: ignore[arg-type]
    with pytest.raises(WaitStorageError, match="controller run ID must be a cycle run ID"):
        read_initial_wait_record_bytes(root, "invalid-cycle-id")
    with pytest.raises(WaitStorageError, match="cannot safely open cycle run directory"):
        read_initial_wait_record_bytes(root, "../escaped_cycle")


def test_reject_invalid_root() -> None:
    with pytest.raises(WaitStorageError, match="root must be a Path"):
        read_initial_wait_record_bytes("/not/a/path", CYCLE_ID)  # type: ignore[arg-type]


def test_reject_mode_not_0600(tmp_path: Path) -> None:
    root, run_dir = _setup_cycle_root(str(tmp_path))
    _write_wait_file(run_dir, b'{"quota_wait":1}', mode=0o644)
    with pytest.raises(WaitStorageError, match="mode must be 0600"):
        read_initial_wait_record_bytes(root, CYCLE_ID)


def test_reject_symlink_wait_file(tmp_path: Path) -> None:
    root, run_dir = _setup_cycle_root(str(tmp_path))
    target_file = tmp_path / "other.json"
    target_file.write_bytes(b'{"quota_wait":1}')
    os.chmod(target_file, 0o600)

    symlink_file = run_dir / QUOTA_WAIT_FILENAME
    symlink_file.symlink_to(target_file)

    with pytest.raises(WaitStorageError, match="is not a regular file"):
        read_initial_wait_record_bytes(root, CYCLE_ID)


def test_reject_hardlink_wait_file(tmp_path: Path) -> None:
    root, run_dir = _setup_cycle_root(str(tmp_path))
    wait_file = _write_wait_file(run_dir, b'{"quota_wait":1}')
    hardlink = run_dir / "extra-hardlink.json"
    os.link(wait_file, hardlink)

    with pytest.raises(WaitStorageError, match="has invalid link count"):
        read_initial_wait_record_bytes(root, CYCLE_ID)


def test_reject_duplicate_keys(tmp_path: Path) -> None:
    root, run_dir = _setup_cycle_root(str(tmp_path))
    _write_wait_file(run_dir, b'{"quota_wait":1,"quota_wait":2}')
    with pytest.raises(WaitStorageError, match="is not valid JSON"):
        read_initial_wait_record_bytes(root, CYCLE_ID)


def test_reject_whitespace_and_noncanonical_order(tmp_path: Path) -> None:
    root, run_dir = _setup_cycle_root(str(tmp_path))
    _write_wait_file(run_dir, b'{"quota_wait": 10}')
    with pytest.raises(WaitStorageError, match="is not canonical JSON bytes"):
        read_initial_wait_record_bytes(root, CYCLE_ID)

    _write_wait_file(run_dir, b'{"b":2,"a":1}')
    with pytest.raises(WaitStorageError, match="is not canonical JSON bytes"):
        read_initial_wait_record_bytes(root, CYCLE_ID)


def test_reject_json_scalar(tmp_path: Path) -> None:
    root, run_dir = _setup_cycle_root(str(tmp_path))
    for scalar in (b'"a_string"', b"12345", b"[1,2,3]", b"true"):
        _write_wait_file(run_dir, scalar)
        with pytest.raises(WaitStorageError, match="must be a JSON object"):
            read_initial_wait_record_bytes(root, CYCLE_ID)


def test_reject_invalid_utf8(tmp_path: Path) -> None:
    root, run_dir = _setup_cycle_root(str(tmp_path))
    _write_wait_file(run_dir, b'{"key":"\xff\xfe\xfd"}')
    with pytest.raises(WaitStorageError, match="is not valid UTF-8"):
        read_initial_wait_record_bytes(root, CYCLE_ID)


def test_reject_symlink_cycle_run_dir(tmp_path: Path) -> None:
    root = Path(tmp_path).resolve(strict=True)
    os.chmod(root, 0o700)
    real_dir = root / "somewhere_else"
    real_dir.mkdir(parents=True, mode=0o700)
    _write_wait_file(real_dir, b'{"quota_wait":1}')

    runs_parent = root / ".local" / "two-site-refactor" / "autopilot" / "runs"
    runs_parent.mkdir(parents=True, mode=0o700)
    curr = root
    for comp in (".local", "two-site-refactor", "autopilot", "runs"):
        curr = curr / comp
        os.chmod(curr, 0o700)

    symlink_run = runs_parent / CYCLE_ID
    symlink_run.symlink_to(real_dir)

    with pytest.raises(WaitStorageError, match="cannot safely open cycle run directory"):
        read_initial_wait_record_bytes(root, CYCLE_ID)


def test_reject_symlink_parent_dir(tmp_path: Path) -> None:
    root = Path(tmp_path).resolve(strict=True)
    os.chmod(root, 0o700)
    real_autopilot = root / "real_autopilot"
    real_run = real_autopilot / "runs" / CYCLE_ID
    real_run.mkdir(parents=True, mode=0o700)
    _write_wait_file(real_run, b'{"quota_wait":1}')

    curr = real_autopilot
    os.chmod(curr, 0o700)
    os.chmod(curr / "runs", 0o700)
    os.chmod(real_run, 0o700)

    base = root / ".local" / "two-site-refactor"
    base.mkdir(parents=True, mode=0o700)
    curr = root
    for comp in (".local", "two-site-refactor"):
        curr = curr / comp
        os.chmod(curr, 0o700)

    symlink_autopilot = base / "autopilot"
    symlink_autopilot.symlink_to(real_autopilot)

    with pytest.raises(WaitStorageError, match="cannot safely open cycle run directory"):
        read_initial_wait_record_bytes(root, CYCLE_ID)


def test_read_fault_handling_and_no_fd_leak(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, run_dir = _setup_cycle_root(str(tmp_path))
    canonical = b'{"quota_wait":30}'
    _write_wait_file(run_dir, canonical)

    opened_fds: list[int] = []
    closed_fds: list[int] = []
    real_open = os.open
    real_close = os.close

    def tracking_open(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        fd = real_open(path, flags, *args, **kwargs)
        if isinstance(path, (str, bytes, Path)) and QUOTA_WAIT_FILENAME in str(path):
            opened_fds.append(fd)
        return fd

    def tracking_close(fd: int) -> None:
        closed_fds.append(fd)
        real_close(fd)

    def failing_read(fd: int, n: int) -> bytes:
        raise OSError("simulated disk read failure")

    monkeypatch.setattr(os, "open", tracking_open)
    monkeypatch.setattr(os, "close", tracking_close)
    monkeypatch.setattr(os, "read", failing_read)

    with pytest.raises(WaitStorageError, match=f"cannot read {QUOTA_WAIT_FILENAME}"):
        read_initial_wait_record_bytes(root, CYCLE_ID)

    assert len(opened_fds) == 1
    target_fd = opened_fds[0]
    assert target_fd in closed_fds
    with pytest.raises(OSError):
        os.fstat(target_fd)


def test_inode_replacement_between_stat_and_open_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, run_dir = _setup_cycle_root(str(tmp_path))
    canonical = b'{"quota_wait":15}'
    file_path = _write_wait_file(run_dir, canonical)

    opened_fds: list[int] = []
    closed_fds: list[int] = []
    real_open = os.open
    real_close = os.close

    def replacing_open(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        if isinstance(path, (str, bytes, Path)) and QUOTA_WAIT_FILENAME in str(path):
            temp_keep = run_dir / "old_keep.json"
            os.link(file_path, temp_keep)
            os.unlink(file_path)
            new_path = run_dir / QUOTA_WAIT_FILENAME
            new_path.write_bytes(canonical)
            os.chmod(new_path, 0o600)
            fd = real_open(path, flags, *args, **kwargs)
            os.unlink(temp_keep)
            opened_fds.append(fd)
            return fd
        return real_open(path, flags, *args, **kwargs)

    def tracking_close(fd: int) -> None:
        closed_fds.append(fd)
        real_close(fd)

    monkeypatch.setattr(os, "open", replacing_open)
    monkeypatch.setattr(os, "close", tracking_close)

    with pytest.raises(WaitStorageError, match="descriptor metadata mismatch before read"):
        read_initial_wait_record_bytes(root, CYCLE_ID)

    assert len(opened_fds) == 1
    target_fd = opened_fds[0]
    assert target_fd in closed_fds
    with pytest.raises(OSError):
        os.fstat(target_fd)
