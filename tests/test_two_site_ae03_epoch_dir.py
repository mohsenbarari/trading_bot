from __future__ import annotations

import errno
import os
from pathlib import Path
import tempfile
from unittest import mock

import pytest

from scripts.two_site_codex_autopilot import (
    AutopilotError,
    CONTROL_PATHS,
    PROTECTED_REFS,
    ensure_rescope_epoch_dir,
    rescope_epoch_basename,
)


def make_repo() -> dict[str, object]:
    return {
        "head": "a" * 40,
        "status": "",
        "protected_refs": {ref: "b" * 40 for ref in PROTECTED_REFS},
        "control_blobs": {p.as_posix(): "c" * 40 for p in CONTROL_PATHS},
        "assignment_scope": {
            "assignment_id": "test-assignment",
            "stage_id": "P1-00b",
            "sha256": "d" * 64,
            "expires_at": "2026-09-24T12:00:00Z",
            "allowed_paths": ["scripts/two_site_codex_autopilot.py"],
            "forbidden_paths": [],
        },
    }


@pytest.fixture
def state_env():
    with tempfile.TemporaryDirectory(prefix="ae03-epoch-") as tmp:
        resolved = Path(tmp).resolve(strict=True)
        os.chmod(resolved, 0o700)
        yield resolved, make_repo()


def test_epoch_dir_create_and_reuse(state_env):
    root, repo = state_env
    child = ensure_rescope_epoch_dir(root, repo)
    assert child.is_dir()
    assert (child.stat().st_mode & 0o777) == 0o700
    assert child.stat().st_uid == os.geteuid()
    assert ensure_rescope_epoch_dir(root, repo) == child


def test_epoch_dir_invalid_parent_or_type(state_env):
    root, repo = state_env
    for bad_root in ("not-a-path", 123, None):
        with pytest.raises(AutopilotError) as exc_info:
            ensure_rescope_epoch_dir(bad_root, repo)  # type: ignore[arg-type]
        assert not str(exc_info.value).startswith("ambiguous:")
    with pytest.raises(AutopilotError) as exc_info:
        ensure_rescope_epoch_dir(root, "bad-repo")
    assert not str(exc_info.value).startswith("ambiguous:")
    non_existent = root / "does_not_exist"
    with pytest.raises(AutopilotError) as exc_info:
        ensure_rescope_epoch_dir(non_existent, repo)
    assert not str(exc_info.value).startswith("ambiguous:")


def test_epoch_dir_symlink_file_and_0755_child(state_env):
    root, repo = state_env
    target = root / rescope_epoch_basename(repo)
    target.symlink_to(root)
    with pytest.raises(AutopilotError) as exc_info:
        ensure_rescope_epoch_dir(root, repo)
    assert str(exc_info.value).startswith("ambiguous:") and target.is_symlink()
    target.unlink()
    target.write_bytes(b"data")
    with pytest.raises(AutopilotError) as exc_info:
        ensure_rescope_epoch_dir(root, repo)
    assert str(exc_info.value).startswith("ambiguous:") and target.is_file()
    target.unlink()
    target.mkdir(mode=0o755)
    os.chmod(target, 0o755)
    with pytest.raises(AutopilotError) as exc_info:
        ensure_rescope_epoch_dir(root, repo)
    assert str(exc_info.value).startswith("ambiguous:") and target.is_dir()


def test_epoch_dir_real_mkdir_then_eio(state_env):
    root, repo = state_env
    target = root / rescope_epoch_basename(repo)
    orig_mkdir = os.mkdir

    def faulty_mkdir(path, *args, **kwargs):
        orig_mkdir(path, *args, **kwargs)
        raise OSError(errno.EIO, "simulated EIO after mkdir")

    with mock.patch("os.mkdir", side_effect=faulty_mkdir):
        with pytest.raises(AutopilotError) as exc_info:
            ensure_rescope_epoch_dir(root, repo)
    assert str(exc_info.value).startswith("ambiguous:")
    assert isinstance(exc_info.value.__cause__, OSError)
    assert exc_info.value.__cause__.errno == errno.EIO
    assert target.is_dir()


def test_epoch_dir_real_mkdir_simulated_eexist_with_fsync(state_env):
    root, repo = state_env
    target = root / rescope_epoch_basename(repo)
    orig_mkdir, orig_fsync = os.mkdir, os.fsync
    fsync_count = 0

    def simulated_mkdir(path, *args, **kwargs):
        orig_mkdir(path, *args, **kwargs)
        raise OSError(errno.EEXIST, "simulated EEXIST")

    def counted_fsync(fd):
        nonlocal fsync_count
        fsync_count += 1
        return orig_fsync(fd)

    with mock.patch("os.mkdir", side_effect=simulated_mkdir), mock.patch("os.fsync", side_effect=counted_fsync):
        res = ensure_rescope_epoch_dir(root, repo)
    assert res == target
    assert fsync_count >= 1
    assert target.is_dir()


def test_epoch_dir_child_opener_fault(state_env):
    root, repo = state_env
    target = root / rescope_epoch_basename(repo)
    from scripts import two_site_codex_autopilot as mod
    orig_open = mod._open_private_rescope_state_dir

    def faulty_open(path):
        if path == target:
            raise AutopilotError("simulated child opener failure")
        return orig_open(path)

    with mock.patch.object(mod, "_open_private_rescope_state_dir", side_effect=faulty_open):
        with pytest.raises(AutopilotError) as exc_info:
            ensure_rescope_epoch_dir(root, repo)
    assert str(exc_info.value).startswith("ambiguous:")
    assert isinstance(exc_info.value.__cause__, AutopilotError)
    assert target.is_dir()


def test_epoch_dir_child_close_real_close_then_eio(state_env):
    root, repo = state_env
    target = root / rescope_epoch_basename(repo)
    from scripts import two_site_codex_autopilot as mod
    orig_open, orig_close, child_fds = mod._open_private_rescope_state_dir, os.close, set()

    def track_open(p):
        fd = orig_open(p)
        if p == target:
            child_fds.add(fd)
        return fd

    def faulty_close(fd):
        if fd in child_fds:
            orig_close(fd)
            raise OSError(errno.EIO, "simulated child close error")
        return orig_close(fd)

    with mock.patch.object(mod, "_open_private_rescope_state_dir", side_effect=track_open), mock.patch("os.close", side_effect=faulty_close):
        with pytest.raises(AutopilotError) as exc_info:
            ensure_rescope_epoch_dir(root, repo)
    assert str(exc_info.value).startswith("ambiguous:")
    assert isinstance(exc_info.value.__cause__, OSError)
    assert exc_info.value.__cause__.errno == errno.EIO
    assert target.is_dir()


def test_epoch_dir_parent_fsync_and_close_faults(state_env):
    root, repo = state_env
    target = root / rescope_epoch_basename(repo)
    with mock.patch("os.fsync", side_effect=OSError(errno.EIO, "fsync fail")):
        with pytest.raises(AutopilotError) as exc:
            ensure_rescope_epoch_dir(root, repo)
    assert str(exc.value).startswith("ambiguous:") and target.is_dir()
    assert isinstance(exc.value.__cause__, OSError)

    from scripts import two_site_codex_autopilot as mod
    orig_open, orig_close, p_fds = mod._open_private_rescope_state_dir, os.close, set()

    def track_open(p):
        fd = orig_open(p)
        if p == root:
            p_fds.add(fd)
        return fd

    def bad_close(fd):
        if fd in p_fds:
            orig_close(fd)
            raise OSError(errno.EIO, "close parent fail")
        return orig_close(fd)

    with mock.patch.object(mod, "_open_private_rescope_state_dir", side_effect=track_open), mock.patch("os.close", side_effect=bad_close):
        with pytest.raises(AutopilotError) as exc:
            ensure_rescope_epoch_dir(root, repo)
    assert str(exc.value).startswith("ambiguous:") and target.is_dir()
    assert isinstance(exc.value.__cause__, OSError)

    p_fds.clear()

    def bad_child_open(p):
        if p == target:
            raise AutopilotError("primary child error")
        return track_open(p)

    with mock.patch.object(mod, "_open_private_rescope_state_dir", side_effect=bad_child_open), mock.patch("os.close", side_effect=bad_close):
        with pytest.raises(AutopilotError) as exc:
            ensure_rescope_epoch_dir(root, repo)
    assert str(exc.value).startswith("ambiguous:") and target.is_dir()
    assert isinstance(exc.value.__cause__, AutopilotError)
    assert "primary child error" in str(exc.value.__cause__)


def test_epoch_dir_interrupt_after_real_mkdir(state_env):
    root, repo = state_env
    target = root / rescope_epoch_basename(repo)
    from scripts import two_site_codex_autopilot as mod
    orig_open, orig_close, p_fds = mod._open_private_rescope_state_dir, os.close, set()

    def intr_open(p):
        if p == target:
            raise KeyboardInterrupt("simulated interrupt")
        return orig_open(p)

    with mock.patch.object(mod, "_open_private_rescope_state_dir", side_effect=intr_open):
        with pytest.raises(KeyboardInterrupt):
            ensure_rescope_epoch_dir(root, repo)
    assert target.is_dir()

    def track_open(p):
        fd = orig_open(p)
        if p == root:
            p_fds.add(fd)
        return fd

    def bad_close(fd):
        if fd in p_fds:
            orig_close(fd)
            raise OSError(errno.EIO, "close error during interrupt")
        return orig_close(fd)

    with mock.patch.object(mod, "_open_private_rescope_state_dir", side_effect=track_open), mock.patch("os.close", side_effect=bad_close):
        with mock.patch("os.fsync", side_effect=KeyboardInterrupt("fsync interrupt")):
            with pytest.raises(KeyboardInterrupt):
                ensure_rescope_epoch_dir(root, repo)
    assert target.is_dir()
