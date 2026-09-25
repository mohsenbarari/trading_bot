from __future__ import annotations

import json
import os
from pathlib import Path
import stat
from typing import Any

import pytest

try:
    from scripts import two_site_quota_wait_storage as storage_mod
    from scripts.two_site_quota_wait_storage import (
        MAX_WAIT_RECORD_BYTES,
        QUOTA_WAIT_FILENAME,
        WaitStorageError,
        publish_initial_wait_record_bytes,
        read_initial_wait_record_bytes,
    )
except ImportError:
    import two_site_quota_wait_storage as storage_mod  # type: ignore
    from two_site_quota_wait_storage import (  # type: ignore
        MAX_WAIT_RECORD_BYTES,
        QUOTA_WAIT_FILENAME,
        WaitStorageError,
        publish_initial_wait_record_bytes,
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


def test_publish_initial_wait_record_bytes_success_and_no_overwrite(tmp_path: Path) -> None:
    root, run_dir = _setup_cycle_root(str(tmp_path))
    payload = {"quota_wait": 60, "reason": "rate_limited"}
    canonical_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")

    returned_bytes = publish_initial_wait_record_bytes(root, CYCLE_ID, canonical_bytes)
    assert returned_bytes == canonical_bytes

    target_file = run_dir / QUOTA_WAIT_FILENAME
    assert target_file.is_file()
    assert not target_file.is_symlink()
    assert target_file.read_bytes() == canonical_bytes

    target_stat = os.stat(target_file)
    assert stat.S_ISREG(target_stat.st_mode)
    assert not stat.S_ISLNK(target_stat.st_mode)
    assert stat.S_IMODE(target_stat.st_mode) == 0o600
    assert target_stat.st_nlink == 1
    assert target_stat.st_uid == os.geteuid()

    entries = [p.name for p in run_dir.iterdir()]
    assert entries == [QUOTA_WAIT_FILENAME]

    with pytest.raises(WaitStorageError, match=f"{QUOTA_WAIT_FILENAME} already exists"):
        publish_initial_wait_record_bytes(root, CYCLE_ID, canonical_bytes)

    different_payload = {"quota_wait": 120, "reason": "different"}
    different_bytes = json.dumps(different_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    with pytest.raises(WaitStorageError, match=f"{QUOTA_WAIT_FILENAME} already exists"):
        publish_initial_wait_record_bytes(root, CYCLE_ID, different_bytes)

    assert target_file.read_bytes() == canonical_bytes


def test_publish_rejection_before_mutation(tmp_path: Path) -> None:
    root, run_dir = _setup_cycle_root(str(tmp_path))

    for invalid_data in ("string_payload", 12345, ["a", "b"], None):
        with pytest.raises(WaitStorageError, match="data must be bytes"):
            publish_initial_wait_record_bytes(root, CYCLE_ID, invalid_data)  # type: ignore[arg-type]

    for noncanonical in (b'{"quota_wait": 10}', b'{"b":2,"a":1}'):
        with pytest.raises(WaitStorageError, match="data is not canonical JSON bytes"):
            publish_initial_wait_record_bytes(root, CYCLE_ID, noncanonical)

    with pytest.raises(WaitStorageError, match="data is not valid JSON"):
        publish_initial_wait_record_bytes(root, CYCLE_ID, b'{"a":1,"a":2}')

    for scalar in (b'"a_string"', b"12345", b"[1,2,3]", b"true"):
        with pytest.raises(WaitStorageError, match="data must be a JSON object"):
            publish_initial_wait_record_bytes(root, CYCLE_ID, scalar)

    prefix_len = len('{"p":"')
    suffix_len = len('"}')
    target_padding_len = MAX_WAIT_RECORD_BYTES - prefix_len - suffix_len
    oversized_payload = {"p": "a" * (target_padding_len + 1)}
    oversized_bytes = json.dumps(oversized_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    assert len(oversized_bytes) == MAX_WAIT_RECORD_BYTES + 1
    with pytest.raises(WaitStorageError, match="data exceeds byte limit"):
        publish_initial_wait_record_bytes(root, CYCLE_ID, oversized_bytes)

    external_file = tmp_path / "other_target.json"
    external_file.write_bytes(b'{"quota_wait":5}')
    os.chmod(external_file, 0o600)

    symlink_file = run_dir / QUOTA_WAIT_FILENAME
    symlink_file.symlink_to(external_file)

    valid_bytes = b'{"quota_wait":50}'
    with pytest.raises(WaitStorageError, match=f"{QUOTA_WAIT_FILENAME} already exists"):
        publish_initial_wait_record_bytes(root, CYCLE_ID, valid_bytes)

    assert symlink_file.is_symlink()
    assert symlink_file.resolve() == external_file.resolve()
    assert [p for p in run_dir.iterdir() if p.name.startswith(".")] == []


def test_publish_fault_before_link_fsync_cleans_temp_and_no_fd_leak(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, run_dir = _setup_cycle_root(str(tmp_path))
    valid_bytes = b'{"quota_wait":50}'

    opened_fds: list[int] = []
    closed_fds: list[int] = []
    real_open = os.open
    real_close = os.close
    real_fsync = os.fsync

    def tracking_open(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        fd = real_open(path, flags, *args, **kwargs)
        if isinstance(path, (str, bytes, Path)) and f".{QUOTA_WAIT_FILENAME}.tmp." in str(path):
            opened_fds.append(fd)
        return fd

    def tracking_close(fd: int) -> None:
        closed_fds.append(fd)
        real_close(fd)

    def failing_fsync(fd: int) -> None:
        if fd in opened_fds:
            raise OSError("simulated temp fsync failure")
        real_fsync(fd)

    monkeypatch.setattr(os, "open", tracking_open)
    monkeypatch.setattr(os, "close", tracking_close)
    monkeypatch.setattr(os, "fsync", failing_fsync)

    with pytest.raises(WaitStorageError, match=f"cannot fsync \\.{QUOTA_WAIT_FILENAME}\\.tmp\\."):
        publish_initial_wait_record_bytes(root, CYCLE_ID, valid_bytes)

    assert not (run_dir / QUOTA_WAIT_FILENAME).exists()
    assert [p for p in run_dir.iterdir() if p.name.startswith(".")] == []
    assert len(opened_fds) == 1
    temp_fd = opened_fds[0]
    assert temp_fd in closed_fds
    with pytest.raises(OSError):
        os.fstat(temp_fd)


def test_publish_fault_before_link_os_link_cleans_temp_and_no_fd_leak(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, run_dir = _setup_cycle_root(str(tmp_path))
    valid_bytes = b'{"quota_wait":50}'

    opened_fds: list[int] = []
    closed_fds: list[int] = []
    real_open = os.open
    real_close = os.close

    def tracking_open(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        fd = real_open(path, flags, *args, **kwargs)
        if isinstance(path, (str, bytes, Path)) and f".{QUOTA_WAIT_FILENAME}.tmp." in str(path):
            opened_fds.append(fd)
        return fd

    def tracking_close(fd: int) -> None:
        closed_fds.append(fd)
        real_close(fd)

    def failing_link(src: Any, dst: Any, *args: Any, **kwargs: Any) -> None:
        raise OSError("simulated link failure")

    monkeypatch.setattr(os, "open", tracking_open)
    monkeypatch.setattr(os, "close", tracking_close)
    monkeypatch.setattr(os, "link", failing_link)

    with pytest.raises(WaitStorageError, match=f"cannot link {QUOTA_WAIT_FILENAME}"):
        publish_initial_wait_record_bytes(root, CYCLE_ID, valid_bytes)

    assert not (run_dir / QUOTA_WAIT_FILENAME).exists()
    assert [p for p in run_dir.iterdir() if p.name.startswith(".")] == []
    assert len(opened_fds) == 1
    temp_fd = opened_fds[0]
    assert temp_fd in closed_fds
    with pytest.raises(OSError):
        os.fstat(temp_fd)


def test_publish_collision_after_prestat_before_link_preserves_competing_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, run_dir = _setup_cycle_root(str(tmp_path))
    valid_bytes = b'{"quota_wait":50}'
    competing_bytes = b'{"competing":true}'

    real_link = os.link

    def colliding_link(src: Any, dst: Any, *args: Any, **kwargs: Any) -> None:
        competing_target = run_dir / QUOTA_WAIT_FILENAME
        competing_target.write_bytes(competing_bytes)
        os.chmod(competing_target, 0o600)
        real_link(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "link", colliding_link)

    with pytest.raises(WaitStorageError, match="target already exists"):
        publish_initial_wait_record_bytes(root, CYCLE_ID, valid_bytes)

    target_file = run_dir / QUOTA_WAIT_FILENAME
    assert target_file.is_file()
    assert target_file.read_bytes() == competing_bytes
    assert [p for p in run_dir.iterdir() if p.name.startswith(".")] == []


def test_publish_fault_after_link_unlink_temp_leaves_target_and_blocks_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, run_dir = _setup_cycle_root(str(tmp_path))
    valid_bytes = b'{"quota_wait":50}'

    real_unlink = os.unlink

    def failing_unlink(path: Any, *args: Any, **kwargs: Any) -> None:
        if isinstance(path, (str, bytes, Path)) and f".{QUOTA_WAIT_FILENAME}.tmp." in str(path):
            raise OSError("simulated temp unlink failure")
        real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(os, "unlink", failing_unlink)

    with pytest.raises(WaitStorageError, match="ambiguous post-link publication failure: cannot unlink"):
        publish_initial_wait_record_bytes(root, CYCLE_ID, valid_bytes)

    target_file = run_dir / QUOTA_WAIT_FILENAME
    assert target_file.is_file()
    assert target_file.read_bytes() == valid_bytes
    assert os.stat(target_file).st_nlink == 2

    with pytest.raises(WaitStorageError, match="has invalid link count"):
        read_initial_wait_record_bytes(root, CYCLE_ID)

    with pytest.raises(WaitStorageError, match=f"{QUOTA_WAIT_FILENAME} already exists"):
        publish_initial_wait_record_bytes(root, CYCLE_ID, valid_bytes)

    assert target_file.exists()


def test_publish_fault_after_link_directory_fsync_leaves_target_and_blocks_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, run_dir = _setup_cycle_root(str(tmp_path))
    valid_bytes = b'{"quota_wait":50}'

    real_fsync = os.fsync

    def failing_dir_fsync(fd: int) -> None:
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError("simulated directory fsync failure")
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", failing_dir_fsync)

    with pytest.raises(WaitStorageError, match="ambiguous post-link publication failure: cannot fsync directory"):
        publish_initial_wait_record_bytes(root, CYCLE_ID, valid_bytes)

    target_file = run_dir / QUOTA_WAIT_FILENAME
    assert target_file.is_file()
    assert target_file.read_bytes() == valid_bytes
    target_stat = os.stat(target_file)
    assert stat.S_IMODE(target_stat.st_mode) == 0o600
    assert target_stat.st_nlink == 1
    assert [p for p in run_dir.iterdir() if p.name.startswith(".")] == []

    readback = read_initial_wait_record_bytes(root, CYCLE_ID)
    assert readback == valid_bytes

    with pytest.raises(WaitStorageError, match=f"{QUOTA_WAIT_FILENAME} already exists"):
        publish_initial_wait_record_bytes(root, CYCLE_ID, valid_bytes)

    assert target_file.exists()


def test_publish_fault_after_link_readback_failure_leaves_target_and_blocks_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, run_dir = _setup_cycle_root(str(tmp_path))
    valid_bytes = b'{"quota_wait":50}'

    def failing_readback(root_arg: Any, run_id_arg: Any) -> bytes:
        raise WaitStorageError("simulated readback corruption")

    monkeypatch.setattr(storage_mod, "read_initial_wait_record_bytes", failing_readback)

    with pytest.raises(
        WaitStorageError,
        match="ambiguous post-link publication failure: readback failed: simulated readback corruption",
    ):
        publish_initial_wait_record_bytes(root, CYCLE_ID, valid_bytes)

    target_file = run_dir / QUOTA_WAIT_FILENAME
    assert target_file.is_file()
    assert target_file.read_bytes() == valid_bytes

    with pytest.raises(WaitStorageError, match=f"{QUOTA_WAIT_FILENAME} already exists"):
        publish_initial_wait_record_bytes(root, CYCLE_ID, valid_bytes)

    assert target_file.exists()
