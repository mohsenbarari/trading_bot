import copy
import errno
import os
import pathlib
from unittest.mock import patch
import pytest

_TEST_FILE = pathlib.Path(__file__).resolve()
_SRC_FILE = _TEST_FILE.parent.parent / "scripts" / "two_site_protected_main_path_guard.py"

_SRC_STAT_SIZE = _SRC_FILE.stat().st_size
_TEST_STAT_SIZE = _TEST_FILE.stat().st_size
_TOTAL_STAT_SIZE = _SRC_STAT_SIZE + _TEST_STAT_SIZE

if _SRC_STAT_SIZE > 8000 or _TEST_STAT_SIZE > 16000 or _TOTAL_STAT_SIZE > 24000:
    raise AssertionError(
        f"Byte cap exceeded: src={_SRC_STAT_SIZE} (max 8000), "
        f"test={_TEST_STAT_SIZE} (max 16000), total={_TOTAL_STAT_SIZE} (max 24000)"
    )

with open(_SRC_FILE, "rb") as _f_src:
    _SRC_BYTES = _f_src.read(8001)
if len(_SRC_BYTES) > 8000 or len(_SRC_BYTES) != _SRC_STAT_SIZE:
    raise AssertionError("Invalid src bytes")

with open(_TEST_FILE, "rb") as _f_test:
    _TEST_BYTES = _f_test.read(16001)
if len(_TEST_BYTES) > 16000 or len(_TEST_BYTES) != _TEST_STAT_SIZE:
    raise AssertionError("Invalid test bytes")

from scripts.two_site_protected_main_path_guard import (
    ProtectedMainPathGuardError,
    pinned_protected_main_parent,
)


def _setup_tree(tmp_path: pathlib.Path, root_mode: int = 0o755, root_name: str = "project") -> pathlib.Path:
    outer = tmp_path / "outer"
    outer.mkdir(mode=0o755, parents=True, exist_ok=True)
    os.chmod(outer, 0o755)
    project = outer / root_name
    project.mkdir(mode=root_mode, exist_ok=True)
    os.chmod(project, root_mode)
    local = project / ".local"
    local.mkdir(mode=0o700, exist_ok=True)
    os.chmod(local, 0o700)
    refactor = local / "two-site-refactor"
    refactor.mkdir(mode=0o755, exist_ok=True)
    os.chmod(refactor, 0o755)
    autopilot = refactor / "autopilot"
    autopilot.mkdir(mode=0o700, exist_ok=True)
    os.chmod(autopilot, 0o700)
    return project


def _snapshot_tree(base: pathlib.Path) -> dict[str, tuple[int, int, int, int]]:
    snap = {}
    for p in sorted(base.rglob("*")):
        st = p.stat(follow_symlinks=False)
        snap[str(p.relative_to(base))] = (st.st_dev, st.st_ino, st.st_mode, st.st_uid)
    return copy.deepcopy(snap)


def _assert_good(project: pathlib.Path, expected_ino: int) -> int:
    with pinned_protected_main_parent(project) as fd:
        assert isinstance(fd, int) and not isinstance(fd, bool)
        fst = os.fstat(fd)
        assert fst.st_ino == expected_ino
        return fd


def _with_uid(st: os.stat_result, uid: int) -> os.stat_result:
    return os.stat_result(
        (st.st_mode, st.st_ino, st.st_dev, st.st_nlink, uid, st.st_gid, st.st_size, st.st_atime, st.st_mtime, st.st_ctime),
        {"st_atime_ns": st.st_atime_ns, "st_mtime_ns": st.st_mtime_ns, "st_ctime_ns": st.st_ctime_ns},
    )


def _run_fault(project: pathlib.Path, fault_fn, restore_fn):
    outer = project.parent
    auto_ino = (project / ".local" / "two-site-refactor" / "autopilot").stat().st_ino
    _assert_good(project, auto_ino)
    before_snap = _snapshot_tree(outer)
    try:
        fault_fn()
        with pytest.raises(ProtectedMainPathGuardError, match=r"^invalid protected main path$"):
            with pinned_protected_main_parent(project):
                pass
    finally:
        restore_fn()
    assert _snapshot_tree(outer) == before_snap
    _assert_good(project, auto_ino)


def test_positive_and_idempotence(tmp_path: pathlib.Path):
    project = _setup_tree(tmp_path, root_mode=0o755)
    autopilot = project / ".local" / "two-site-refactor" / "autopilot"
    st = autopilot.stat()
    oracle = copy.deepcopy({"dev": st.st_dev, "ino": st.st_ino, "mode": st.st_mode, "uid": st.st_uid})

    for _ in range(2):
        with pinned_protected_main_parent(project) as fd:
            assert isinstance(fd, int) and not isinstance(fd, bool)
            fst = os.fstat(fd)
            assert fst.st_dev == oracle["dev"]
            assert fst.st_ino == oracle["ino"]
            assert fst.st_mode == oracle["mode"]
            assert fst.st_uid == oracle["uid"]
            assert len(os.listdir(fd)) == 0
        with pytest.raises(OSError) as err:
            os.fstat(fd)
        assert err.value.errno == errno.EBADF

    special = _setup_tree(tmp_path / "spec", root_mode=0o755, root_name="autopilot")
    with pinned_protected_main_parent(special) as sfd:
        assert isinstance(sfd, int) and not isinstance(sfd, bool)
    with pytest.raises(OSError) as err:
        os.fstat(sfd)
    assert err.value.errno == errno.EBADF


def test_matrix_1_invalid_roots(tmp_path: pathlib.Path):
    project = _setup_tree(tmp_path)
    outer = project.parent
    auto_ino = (project / ".local" / "two-site-refactor" / "autopilot").stat().st_ino
    invalid_cases = [
        None,
        12345,
        str(project),
        pathlib.Path("relative/path/autopilot"),
        pathlib.Path(f"{project}/../escape"),
        pathlib.Path("//tmp/escape"),
    ]
    for case in invalid_cases:
        _assert_good(project, auto_ino)
        before_snap = _snapshot_tree(outer)
        opened, stated = [], []
        real_open, real_stat, real_fstat = os.open, os.stat, os.fstat

        def spy_open(*a, **k):
            opened.append(a)
            return real_open(*a, **k)

        def spy_stat(*a, **k):
            stated.append(a)
            return real_stat(*a, **k)

        def spy_fstat(*a, **k):
            stated.append(a)
            return real_fstat(*a, **k)

        with patch("os.open", spy_open), patch("os.stat", spy_stat), patch("os.fstat", spy_fstat):
            with pytest.raises(ProtectedMainPathGuardError, match=r"^invalid protected main path$"):
                with pinned_protected_main_parent(case):  # type: ignore
                    pass
        assert len(opened) == 0 and len(stated) == 0
        assert _snapshot_tree(outer) == before_snap
        _assert_good(project, auto_ino)


def test_matrix_2_structural_and_security_faults(tmp_path: pathlib.Path):
    project = _setup_tree(tmp_path)
    outer = project.parent
    local = project / ".local"
    refactor = local / "two-site-refactor"
    autopilot = refactor / "autopilot"

    # Missing components
    for target in (local, refactor, autopilot):
        bak = target.parent / f"{target.name}_bak"
        _run_fault(project, lambda t=target, b=bak: t.rename(b), lambda t=target, b=bak: b.rename(t))

    # Symlinks
    for target in (outer, project, local, refactor, autopilot):
        bak = target.parent / f"{target.name}_symbak"
        def do_sym(t=target, b=bak):
            t.rename(b)
            t.symlink_to(b)
        def undo_sym(t=target, b=bak):
            t.unlink()
            b.rename(t)
        _run_fault(project, do_sym, undo_sym)

    # Non-directory files
    for target in (local, refactor, autopilot):
        bak = target.parent / f"{target.name}_fbak"
        def do_file(t=target, b=bak):
            t.rename(b)
            t.write_text("not_dir")
        def undo_file(t=target, b=bak):
            t.unlink()
            b.rename(t)
        _run_fault(project, do_file, undo_file)

    # Permissions
    for path, bad_mode, good_mode in [
        (project, 0o770, 0o755), (project, 0o757, 0o755),
        (refactor, 0o775, 0o755), (refactor, 0o757, 0o755),
        (local, 0o755, 0o700), (local, 0o4700, 0o700),
        (autopilot, 0o755, 0o700), (autopilot, 0o2700, 0o700),
    ]:
        _run_fault(project, lambda p=path, m=bad_mode: os.chmod(p, m), lambda p=path, m=good_mode: os.chmod(p, m))

    # Valid intermediate 0700 and 0755
    auto_ino = autopilot.stat().st_ino
    os.chmod(refactor, 0o700)
    _assert_good(project, auto_ino)
    os.chmod(refactor, 0o755)
    _assert_good(project, auto_ino)

    # Wrong project directory UID
    proj_st = project.stat()
    real_fstat, real_stat = os.fstat, os.stat
    fake_proj_uid = lambda fd: _with_uid(real_fstat(fd), 99999) if real_fstat(fd).st_ino == proj_st.st_ino else real_fstat(fd)
    p_uid = patch("os.fstat", fake_proj_uid)
    _run_fault(project, p_uid.start, p_uid.stop)

    # Foreign ancestor UID allowed
    outer_st = outer.stat()
    fake_anc_stat = lambda p, *a, **k: _with_uid(real_stat(p, *a, **k), 99999) if real_stat(p, *a, **k).st_ino == outer_st.st_ino else real_stat(p, *a, **k)
    fake_anc_fstat = lambda fd: _with_uid(real_fstat(fd), 99999) if real_fstat(fd).st_ino == outer_st.st_ino else real_fstat(fd)

    _assert_good(project, auto_ino)
    before_snap = _snapshot_tree(outer)
    with patch("os.stat", fake_anc_stat), patch("os.fstat", fake_anc_fstat):
        _assert_good(project, auto_ino)
    assert _snapshot_tree(outer) == before_snap
    _assert_good(project, auto_ino)


def test_matrix_3_replacement_and_drift_during_body(tmp_path: pathlib.Path):
    project = _setup_tree(tmp_path)
    outer = project.parent
    autopilot = project / ".local" / "two-site-refactor" / "autopilot"
    auto_ino = autopilot.stat().st_ino

    for target, mode in ((autopilot, 0o700), (project, 0o755), (outer, 0o755)):
        _assert_good(project, auto_ino)
        before_snap = _snapshot_tree(outer)
        bak = target.parent / f"{target.name}_drift_bak"
        try:
            with pytest.raises(ProtectedMainPathGuardError, match=r"^invalid protected main path$"):
                with pinned_protected_main_parent(project):
                    target.rename(bak)
                    target.mkdir(mode=mode)
                    os.chmod(target, mode)
        finally:
            if target.exists() and not target.samefile(bak):
                target.rmdir()
            if bak.exists():
                bak.rename(target)
        assert _snapshot_tree(outer) == before_snap
        _assert_good(project, auto_ino)

    # Mode drift during body
    _assert_good(project, auto_ino)
    before_snap = _snapshot_tree(outer)
    try:
        with pytest.raises(ProtectedMainPathGuardError, match=r"^invalid protected main path$"):
            with pinned_protected_main_parent(project):
                os.chmod(autopilot, 0o755)
    finally:
        os.chmod(autopilot, 0o700)
    assert _snapshot_tree(outer) == before_snap
    _assert_good(project, auto_ino)

    # UID drift during body
    real_fstat = os.fstat
    drift_active = False
    fake_drift_fstat = lambda fd: _with_uid(real_fstat(fd), 99999) if drift_active and real_fstat(fd).st_ino == auto_ino else real_fstat(fd)

    _assert_good(project, auto_ino)
    before_snap = _snapshot_tree(outer)
    with patch("os.fstat", fake_drift_fstat):
        try:
            with pytest.raises(ProtectedMainPathGuardError, match=r"^invalid protected main path$"):
                with pinned_protected_main_parent(project):
                    drift_active = True
        finally:
            drift_active = False
    assert _snapshot_tree(outer) == before_snap
    _assert_good(project, auto_ino)


def test_matrix_4_cleanup_and_exception_propagation(tmp_path: pathlib.Path):
    project = _setup_tree(tmp_path)
    captured_fds, real_open, call_count = [], os.open, 0

    def fail_after_open(path, flags, *args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 3:
            raise OSError("injected setup failure")
        fd = real_open(path, flags, *args, **kwargs)
        captured_fds.append(fd)
        return fd

    with patch("os.open", fail_after_open):
        with pytest.raises(ProtectedMainPathGuardError, match=r"^invalid protected main path$"):
            with pinned_protected_main_parent(project):
                pass
    assert len(captured_fds) >= 2
    for fd in captured_fds:
        with pytest.raises(OSError) as exc_info:
            os.fstat(fd)
        assert exc_info.value.errno == errno.EBADF

    sentinel = ValueError("sentinel_body_exc")
    with pytest.raises(ValueError) as exc_info:
        with pinned_protected_main_parent(project) as fd:
            yielded_fd = fd
            raise sentinel
    assert exc_info.value is sentinel
    with pytest.raises(OSError) as err:
        os.fstat(yielded_fd)
    assert err.value.errno == errno.EBADF

    with pytest.raises(KeyboardInterrupt):
        with pinned_protected_main_parent(project) as fd:
            yielded_fd = fd
            raise KeyboardInterrupt()
    with pytest.raises(OSError) as err:
        os.fstat(yielded_fd)
    assert err.value.errno == errno.EBADF

    with pytest.raises(SystemExit):
        with pinned_protected_main_parent(project) as fd:
            yielded_fd = fd
            raise SystemExit(42)
    with pytest.raises(OSError) as err:
        os.fstat(yielded_fd)
    assert err.value.errno == errno.EBADF


def test_matrix_5_close_error_wrapper(tmp_path: pathlib.Path):
    project = _setup_tree(tmp_path)
    captured_fds, real_open, real_close, close_called = [], os.open, os.close, 0

    def spy_open(path, flags, *args, **kwargs):
        fd = real_open(path, flags, *args, **kwargs)
        captured_fds.append(fd)
        return fd

    def fail_once_close(fd):
        nonlocal close_called
        close_called += 1
        real_close(fd)
        if close_called == 1:
            raise OSError("injected close error")

    with patch("os.open", spy_open), patch("os.close", fail_once_close):
        with pytest.raises(ProtectedMainPathGuardError, match=r"^invalid protected main path$"):
            with pinned_protected_main_parent(project):
                pass
    assert len(captured_fds) > 0
    for fd in captured_fds:
        with pytest.raises(OSError) as exc_info:
            os.fstat(fd)
        assert exc_info.value.errno == errno.EBADF

    captured_fds.clear()
    close_called = 0
    with patch("os.open", spy_open), patch("os.close", fail_once_close):
        with pytest.raises(KeyboardInterrupt):
            with pinned_protected_main_parent(project):
                raise KeyboardInterrupt()
    assert len(captured_fds) > 0
    for fd in captured_fds:
        with pytest.raises(OSError) as exc_info:
            os.fstat(fd)
        assert exc_info.value.errno == errno.EBADF


def test_matrix_6_directory_content_mutation(tmp_path: pathlib.Path):
    project = _setup_tree(tmp_path)
    outer = project.parent
    autopilot = project / ".local" / "two-site-refactor" / "autopilot"
    before_snap = _snapshot_tree(outer)

    with pinned_protected_main_parent(project) as fd:
        assert isinstance(fd, int)
        child = autopilot / "synthetic_unrelated_child.txt"
        child.write_text("test_payload")
        assert child.read_text() == "test_payload"
        child.unlink()

    assert _snapshot_tree(outer) == before_snap
