import copy
import errno
import os
import pathlib
import stat
import traceback
from typing import Optional
from unittest.mock import patch
import pytest

_TEST_FILE = pathlib.Path(__file__).resolve()
_SRC_FILE = _TEST_FILE.parent.parent / "scripts" / "two_site_protected_main_approval_leaf.py"

_SRC_STAT_SIZE = _SRC_FILE.stat().st_size
_TEST_STAT_SIZE = _TEST_FILE.stat().st_size
_TOTAL_STAT_SIZE = _SRC_STAT_SIZE + _TEST_STAT_SIZE

if _SRC_STAT_SIZE > 8000 or _TEST_STAT_SIZE > 24000 or _TOTAL_STAT_SIZE > 32000:
    raise AssertionError(
        f"Byte cap exceeded: src={_SRC_STAT_SIZE} (max 8000), "
        f"test={_TEST_STAT_SIZE} (max 24000), total={_TOTAL_STAT_SIZE} (max 32000)"
    )

with open(_SRC_FILE, "rb") as _f_src:
    _SRC_BYTES = _f_src.read(8001)
if len(_SRC_BYTES) > 8000 or len(_SRC_BYTES) != _SRC_STAT_SIZE:
    raise AssertionError("Invalid src bytes")

with open(_TEST_FILE, "rb") as _f_test:
    _TEST_BYTES = _f_test.read(24001)
if len(_TEST_BYTES) > 24000 or len(_TEST_BYTES) != _TEST_STAT_SIZE:
    raise AssertionError("Invalid test bytes")

from scripts.two_site_protected_main_approval_leaf import (
    MAX_PROTECTED_MAIN_APPROVAL_LEAF_BYTES,
    ProtectedMainApprovalLeafError,
    read_protected_main_approval_leaf_locked,
)

_REAL_STAT = os.stat
_REAL_FSTAT = os.fstat
_REAL_OPEN = os.open
_REAL_READ = os.read
_REAL_CLOSE = os.close

FROZEN_GOLDEN_BYTES: bytes = b'{"schema_version":"1.0","test_data":"SYNTHETIC-LEAF-PAYLOAD"}\n'


def _setup_tree(
    tmp_path: pathlib.Path,
    leaf_content: bytes = FROZEN_GOLDEN_BYTES,
    leaf_mode: int = 0o600,
) -> pathlib.Path:
    outer = tmp_path / "outer"
    outer.mkdir(mode=0o755, parents=True, exist_ok=True)
    os.chmod(outer, 0o755)
    project = outer / "project"
    project.mkdir(mode=0o755, exist_ok=True)
    os.chmod(project, 0o755)
    local = project / ".local"
    local.mkdir(mode=0o700, exist_ok=True)
    os.chmod(local, 0o700)
    refactor = local / "two-site-refactor"
    refactor.mkdir(mode=0o755, exist_ok=True)
    os.chmod(refactor, 0o755)
    autopilot = refactor / "autopilot"
    autopilot.mkdir(mode=0o700, exist_ok=True)
    os.chmod(autopilot, 0o700)
    leaf = autopilot / "protected-main-approval.json"
    leaf.write_bytes(leaf_content)
    os.chmod(leaf, leaf_mode)
    return project


def _snapshot_tree(base: pathlib.Path) -> dict[str, tuple[str, int, int, int, Optional[str], Optional[bytes]]]:
    snap = {}
    for p in sorted(base.rglob("*")):
        st = _REAL_STAT(p, follow_symlinks=False)
        m = st.st_mode
        if stat.S_ISLNK(m):
            ftype = "lnk"
            target = os.readlink(p)
            content = None
        elif stat.S_ISDIR(m):
            ftype = "dir"
            target = None
            content = None
        elif stat.S_ISFIFO(m):
            ftype = "fifo"
            target = None
            content = None
        elif stat.S_ISREG(m):
            ftype = "reg"
            target = None
            with open(p, "rb") as f:
                content = f.read()
        else:
            ftype = "other"
            target = None
            content = None
        rel = str(p.relative_to(base))
        snap[rel] = (ftype, st.st_uid, stat.S_IMODE(m), st.st_nlink, target, content)
    return copy.deepcopy(snap)


def _assert_good(project: pathlib.Path, expected: bytes = FROZEN_GOLDEN_BYTES) -> bytes:
    res = read_protected_main_approval_leaf_locked(project)
    assert res == expected
    assert isinstance(res, bytes)
    return res


def _with_uid(st: os.stat_result, uid: int) -> os.stat_result:
    return os.stat_result(
        (
            st.st_mode,
            st.st_ino,
            st.st_dev,
            st.st_nlink,
            uid,
            st.st_gid,
            st.st_size,
            st.st_atime,
            st.st_mtime,
            st.st_ctime,
        ),
        {
            "st_atime_ns": st.st_atime_ns,
            "st_mtime_ns": st.st_mtime_ns,
            "st_ctime_ns": st.st_ctime_ns,
        },
    )


def _run_fault(project: pathlib.Path, fault_fn, restore_fn, expected_bytes: bytes = FROZEN_GOLDEN_BYTES):
    outer = project.parent
    _assert_good(project, expected_bytes)
    before_snap = _snapshot_tree(outer)
    try:
        fault_fn()
        with pytest.raises(ProtectedMainApprovalLeafError) as exc_info:
            read_protected_main_approval_leaf_locked(project)
        err = exc_info.value
        assert str(err) == "invalid owner approval leaf"
        assert err.__cause__ is None
        assert err.__suppress_context__ is True
    finally:
        restore_fn()
    assert _snapshot_tree(outer) == before_snap
    _assert_good(project, expected_bytes)


def test_matrix_1_positives_and_metadata_prechecks(tmp_path: pathlib.Path):
    project = _setup_tree(tmp_path)
    leaf = project / ".local" / "two-site-refactor" / "autopilot" / "protected-main-approval.json"

    # Positive standard synthetic
    res = _assert_good(project)
    assert len(res) == len(FROZEN_GOLDEN_BYTES)

    # Positive 4096 arbitrary bytes
    raw4096 = bytes((i * 37 + 13) % 256 for i in range(4096))
    leaf.write_bytes(raw4096)
    os.chmod(leaf, 0o600)
    res4096 = _assert_good(project, expected=raw4096)
    assert len(res4096) == 4096
    leaf.write_bytes(FROZEN_GOLDEN_BYTES)
    os.chmod(leaf, 0o600)

    # Empty metadata pre-read rejection (no leaf read called)
    read_called = False

    def spy_read(fd, count):
        nonlocal read_called
        read_called = True
        return _REAL_READ(fd, count)

    p_read = patch("os.read", spy_read)

    def do_empty():
        nonlocal read_called
        read_called = False
        p_read.start()
        leaf.write_bytes(b"")
        os.chmod(leaf, 0o600)

    def undo_empty():
        p_read.stop()
        leaf.write_bytes(FROZEN_GOLDEN_BYTES)
        os.chmod(leaf, 0o600)

    _run_fault(project, do_empty, undo_empty)
    assert not read_called

    # Missing leaf pre-read rejection
    bak_missing = leaf.parent / "leaf_bak_missing"

    def do_missing():
        nonlocal read_called
        read_called = False
        p_read.start()
        leaf.rename(bak_missing)

    def undo_missing():
        p_read.stop()
        if bak_missing.exists():
            bak_missing.rename(leaf)

    _run_fault(project, do_missing, undo_missing)
    assert not read_called

    # 4097 metadata pre-read rejection
    def do_4097():
        nonlocal read_called
        read_called = False
        p_read.start()
        leaf.write_bytes(b"X" * 4097)
        os.chmod(leaf, 0o600)

    def undo_4097():
        p_read.stop()
        leaf.write_bytes(FROZEN_GOLDEN_BYTES)
        os.chmod(leaf, 0o600)

    _run_fault(project, do_4097, undo_4097)
    assert not read_called

    # Independent read calls prove remaining request limit
    leaf.write_bytes(raw4096)
    os.chmod(leaf, 0o600)
    requested_limits = []

    def spy_limits(fd, count):
        requested_limits.append(count)
        return _REAL_READ(fd, count)

    with patch("os.read", spy_limits):
        res_lim = _assert_good(project, expected=raw4096)
        assert len(res_lim) == 4096
    assert len(requested_limits) >= 1
    assert all(lim <= 4097 for lim in requested_limits)
    leaf.write_bytes(FROZEN_GOLDEN_BYTES)
    os.chmod(leaf, 0o600)


def test_matrix_2_leaf_file_types_modes_and_uid_regression(tmp_path: pathlib.Path):
    project = _setup_tree(tmp_path)
    outer = project.parent
    leaf = project / ".local" / "two-site-refactor" / "autopilot" / "protected-main-approval.json"

    # Symlink
    bak_sym = leaf.parent / "leaf_sym_bak"

    def do_sym():
        leaf.rename(bak_sym)
        leaf.symlink_to(bak_sym)

    def undo_sym():
        leaf.unlink()
        bak_sym.rename(leaf)

    _run_fault(project, do_sym, undo_sym)

    # External hardlink
    ext_link = outer / "ext_link_target"

    def do_link():
        os.link(leaf, ext_link)

    def undo_link():
        ext_link.unlink()

    _run_fault(project, do_link, undo_link)

    # Directory
    bak_dir = leaf.parent / "leaf_dir_bak"

    def do_dir():
        leaf.rename(bak_dir)
        leaf.mkdir(mode=0o600)

    def undo_dir():
        leaf.rmdir()
        bak_dir.rename(leaf)

    _run_fault(project, do_dir, undo_dir)

    # FIFO
    bak_fifo = leaf.parent / "leaf_fifo_bak"

    def do_fifo():
        leaf.rename(bak_fifo)
        os.mkfifo(str(leaf), 0o600)

    def undo_fifo():
        leaf.unlink()
        bak_fifo.rename(leaf)

    _run_fault(project, do_fifo, undo_fifo)

    # Wrong modes (standard + special bits)
    for bad_mode in (0o644, 0o666, 0o700, 0o4600, 0o2600, 0o1600):
        _run_fault(
            project,
            lambda m=bad_mode: os.chmod(leaf, m),
            lambda: os.chmod(leaf, 0o600),
        )

    # Wrong leaf UID regression: must consistently affect leaf named stat and fstat
    leaf_st = _REAL_STAT(leaf, follow_symlinks=False)
    leaf_name_seen = []

    def spy_stat_uid(path, *args, **kwargs):
        st = _REAL_STAT(path, *args, **kwargs)
        if str(path) == "protected-main-approval.json" or str(path).endswith("protected-main-approval.json"):
            leaf_name_seen.append(str(path))
            return _with_uid(st, 99999)
        return st

    def spy_fstat_uid(fd):
        fst = _REAL_FSTAT(fd)
        if fst.st_ino == leaf_st.st_ino:
            return _with_uid(fst, 99999)
        return fst

    p_stat = patch("os.stat", spy_stat_uid)
    p_fstat = patch("os.fstat", spy_fstat_uid)

    def do_uid():
        leaf_name_seen.clear()
        p_stat.start()
        p_fstat.start()

    def undo_uid():
        p_fstat.stop()
        p_stat.stop()

    _run_fault(project, do_uid, undo_uid)
    assert len(leaf_name_seen) > 0
    assert "protected-main-approval.json" in leaf_name_seen[0]


def test_matrix_3_replacement_and_drift_during_body(tmp_path: pathlib.Path):
    project = _setup_tree(tmp_path)
    leaf = project / ".local" / "two-site-refactor" / "autopilot" / "protected-main-approval.json"

    # Replacement between stat and open
    bak_rep1 = leaf.parent / "leaf_bak_rep1"
    rep1_done = False

    def spy_open_rep1(path, flags, *args, **kwargs):
        nonlocal rep1_done
        if str(path) == "protected-main-approval.json" and not rep1_done:
            rep1_done = True
            leaf.rename(bak_rep1)
            leaf.write_bytes(b"REPLACED_BEFORE_OPEN\n")
            os.chmod(leaf, 0o600)
        return _REAL_OPEN(path, flags, *args, **kwargs)

    p_open1 = patch("os.open", spy_open_rep1)

    def do_rep1():
        nonlocal rep1_done
        rep1_done = False
        p_open1.start()

    def undo_rep1():
        p_open1.stop()
        if leaf.exists() and not leaf.samefile(bak_rep1):
            leaf.unlink()
        if bak_rep1.exists():
            bak_rep1.rename(leaf)

    _run_fault(project, do_rep1, undo_rep1)

    # Replacement during read
    bak_rep2 = leaf.parent / "leaf_bak_rep2"
    rep2_done = False

    def spy_read_rep2(fd, count):
        nonlocal rep2_done
        if not rep2_done:
            rep2_done = True
            leaf.rename(bak_rep2)
            leaf.write_bytes(b"REPLACED_DURING_READ\n")
            os.chmod(leaf, 0o600)
        return _REAL_READ(fd, count)

    p_read2 = patch("os.read", spy_read_rep2)

    def do_rep2():
        nonlocal rep2_done
        rep2_done = False
        p_read2.start()

    def undo_rep2():
        p_read2.stop()
        if leaf.exists() and not leaf.samefile(bak_rep2):
            leaf.unlink()
        if bak_rep2.exists():
            bak_rep2.rename(leaf)

    _run_fault(project, do_rep2, undo_rep2)

    # In-place byte drift during read
    drift_done = False

    def spy_read_drift(fd, count):
        nonlocal drift_done
        if not drift_done:
            drift_done = True
            with open(leaf, "r+b") as f:
                f.seek(0)
                f.write(b"IN_PLACE_MUTATION_DRIFT")
        return _REAL_READ(fd, count)

    p_read_drift = patch("os.read", spy_read_drift)

    def do_drift():
        nonlocal drift_done
        drift_done = False
        p_read_drift.start()

    def undo_drift():
        p_read_drift.stop()
        leaf.write_bytes(FROZEN_GOLDEN_BYTES)
        os.chmod(leaf, 0o600)

    _run_fault(project, do_drift, undo_drift)

    # FIFO swap between stat and open (asserts O_NONBLOCK)
    bak_fifo_swap = leaf.parent / "leaf_bak_fifo_swap"
    fifo_swap_done = False
    open_flags_seen = []

    def spy_open_fifo(path, flags, *args, **kwargs):
        nonlocal fifo_swap_done
        if str(path) == "protected-main-approval.json" and not fifo_swap_done:
            fifo_swap_done = True
            open_flags_seen.append(flags)
            leaf.rename(bak_fifo_swap)
            os.mkfifo(str(leaf), 0o600)
        return _REAL_OPEN(path, flags, *args, **kwargs)

    p_open_fifo = patch("os.open", spy_open_fifo)

    def do_fifo_swap():
        nonlocal fifo_swap_done
        fifo_swap_done = False
        open_flags_seen.clear()
        p_open_fifo.start()

    def undo_fifo_swap():
        p_open_fifo.stop()
        if leaf.exists():
            leaf.unlink()
        if bak_fifo_swap.exists():
            bak_fifo_swap.rename(leaf)

    _run_fault(project, do_fifo_swap, undo_fifo_swap)
    assert len(open_flags_seen) > 0
    assert (open_flags_seen[0] & os.O_NONBLOCK) != 0


def test_matrix_4_growth_during_read_overflow(tmp_path: pathlib.Path):
    init_bytes = b"A" * 2000
    project = _setup_tree(tmp_path, leaf_content=init_bytes)
    leaf = project / ".local" / "two-site-refactor" / "autopilot" / "protected-main-approval.json"
    growth_done = False
    requested_lengths = []

    def spy_read_growth(fd, count):
        nonlocal growth_done
        requested_lengths.append(count)
        if not growth_done:
            growth_done = True
            with open(leaf, "ab") as f:
                f.write(b"B" * (4097 - 2000))
        return _REAL_READ(fd, count)

    p_growth = patch("os.read", spy_read_growth)

    def do_growth():
        nonlocal growth_done
        growth_done = False
        requested_lengths.clear()
        p_growth.start()

    def undo_growth():
        p_growth.stop()
        leaf.write_bytes(init_bytes)
        os.chmod(leaf, 0o600)

    _run_fault(project, do_growth, undo_growth, expected_bytes=init_bytes)
    assert len(requested_lengths) > 0
    assert all(n <= 4097 for n in requested_lengths)


def test_matrix_5_fd_custody_and_cleanup(tmp_path: pathlib.Path):
    project = _setup_tree(tmp_path)
    leaf = project / ".local" / "two-site-refactor" / "autopilot" / "protected-main-approval.json"
    leaf_st = _REAL_STAT(leaf, follow_symlinks=False)
    captured_fds = []

    def spy_open(path, flags, *args, **kwargs):
        fd = _REAL_OPEN(path, flags, *args, **kwargs)
        captured_fds.append(fd)
        return fd

    # 1. Success path closes all FDs
    with patch("os.open", spy_open):
        res = read_protected_main_approval_leaf_locked(project)
        assert res == FROZEN_GOLDEN_BYTES
    assert len(captured_fds) >= 2
    for fd in captured_fds:
        with pytest.raises(OSError) as exc_info:
            _REAL_FSTAT(fd)
        assert exc_info.value.errno == errno.EBADF

    # 2. Acquired leaf fstat error
    captured_fds.clear()
    fstat_call_count = 0

    def spy_fstat_fail(fd):
        nonlocal fstat_call_count
        fst = _REAL_FSTAT(fd)
        if fst.st_ino == leaf_st.st_ino:
            fstat_call_count += 1
            if fstat_call_count == 1:
                raise OSError("injected leaf fstat error")
        return fst

    with patch("os.open", spy_open), patch("os.fstat", spy_fstat_fail):
        with pytest.raises(ProtectedMainApprovalLeafError) as exc_info:
            read_protected_main_approval_leaf_locked(project)
        assert str(exc_info.value) == "invalid owner approval leaf"
    assert fstat_call_count == 1
    assert len(captured_fds) >= 2
    for fd in captured_fds:
        with pytest.raises(OSError) as exc_info:
            _REAL_FSTAT(fd)
        assert exc_info.value.errno == errno.EBADF

    # 3. Final named stat error
    captured_fds.clear()
    leaf_stat_count = 0

    def spy_stat_post_fail(path, *args, **kwargs):
        nonlocal leaf_stat_count
        st = _REAL_STAT(path, *args, **kwargs)
        if str(path) == "protected-main-approval.json":
            leaf_stat_count += 1
            if leaf_stat_count == 2:
                raise OSError("injected post stat error")
        return st

    with patch("os.open", spy_open), patch("os.stat", spy_stat_post_fail):
        with pytest.raises(ProtectedMainApprovalLeafError) as exc_info:
            read_protected_main_approval_leaf_locked(project)
        assert str(exc_info.value) == "invalid owner approval leaf"
    assert leaf_stat_count == 2
    assert len(captured_fds) >= 2
    for fd in captured_fds:
        with pytest.raises(OSError) as exc_info:
            _REAL_FSTAT(fd)
        assert exc_info.value.errno == errno.EBADF

    # 4. Actual close wrapper failure
    captured_fds.clear()
    close_call_count = 0

    def spy_close_fail(fd):
        nonlocal close_call_count
        _REAL_CLOSE(fd)
        close_call_count += 1
        if close_call_count == 1:
            raise OSError("injected close error")

    with patch("os.open", spy_open), patch("os.close", spy_close_fail):
        with pytest.raises(ProtectedMainApprovalLeafError) as exc_info:
            read_protected_main_approval_leaf_locked(project)
        assert str(exc_info.value) == "invalid owner approval leaf"
    assert close_call_count > 0
    assert len(captured_fds) >= 2
    for fd in captured_fds:
        with pytest.raises(OSError) as exc_info:
            _REAL_FSTAT(fd)
        assert exc_info.value.errno == errno.EBADF

    # 5. Body KeyboardInterrupt + close error: BaseException preserved
    captured_fds.clear()
    sentinel_ki = KeyboardInterrupt()

    def spy_read_ki(fd, count):
        raise sentinel_ki

    close_call_count = 0
    with patch("os.open", spy_open), patch("os.read", spy_read_ki), patch("os.close", spy_close_fail):
        with pytest.raises(KeyboardInterrupt) as exc_info:
            read_protected_main_approval_leaf_locked(project)
        assert exc_info.value is sentinel_ki
    assert len(captured_fds) >= 2
    for fd in captured_fds:
        with pytest.raises(OSError) as exc_info:
            _REAL_FSTAT(fd)
        assert exc_info.value.errno == errno.EBADF

    # 6. Body SystemExit + close error: BaseException preserved
    captured_fds.clear()
    sentinel_se = SystemExit(42)

    def spy_read_se(fd, count):
        raise sentinel_se

    close_call_count = 0
    with patch("os.open", spy_open), patch("os.read", spy_read_se), patch("os.close", spy_close_fail):
        with pytest.raises(SystemExit) as exc_info:
            read_protected_main_approval_leaf_locked(project)
        assert exc_info.value is sentinel_se
        assert exc_info.value.code == 42
    assert len(captured_fds) >= 2
    for fd in captured_fds:
        with pytest.raises(OSError) as exc_info:
            _REAL_FSTAT(fd)
        assert exc_info.value.errno == errno.EBADF


def test_matrix_6_prior_caller_error_and_composition_postcheck(tmp_path: pathlib.Path):
    project = _setup_tree(tmp_path)
    leaf = project / ".local" / "two-site-refactor" / "autopilot" / "protected-main-approval.json"
    outer = project.parent
    marker = "UNIQUE_PRIOR_CALLER_ERROR_MARKER_998877"

    # Prior caller error + bad leaf mode hygiene
    _assert_good(project)
    before_snap = _snapshot_tree(outer)
    try:
        os.chmod(leaf, 0o644)
        try:
            raise ValueError(marker)
        except ValueError:
            with pytest.raises(ProtectedMainApprovalLeafError) as exc_info:
                read_protected_main_approval_leaf_locked(project)
            err = exc_info.value
            assert str(err) == "invalid owner approval leaf"
            assert err.__cause__ is None
            assert err.__suppress_context__ is True
            formatted = "".join(traceback.format_exception(err))
            assert marker not in formatted
    finally:
        os.chmod(leaf, 0o600)
    assert _snapshot_tree(outer) == before_snap
    _assert_good(project)

    # Bad parent mode mapping (proves pathguard error is mapped to leaf error category)
    def do_bad_parent():
        os.chmod(project, 0o777)

    def undo_bad_parent():
        os.chmod(project, 0o755)

    _run_fault(project, do_bad_parent, undo_bad_parent)

    # Parent replace/mode during leaf read (proves composition postcheck revalidation)
    autopilot = project / ".local" / "two-site-refactor" / "autopilot"
    drift_parent_done = False

    def spy_read_parent_drift(fd, count):
        nonlocal drift_parent_done
        if not drift_parent_done:
            drift_parent_done = True
            os.chmod(autopilot, 0o755)
        return _REAL_READ(fd, count)

    p_parent_drift = patch("os.read", spy_read_parent_drift)

    def do_parent_drift():
        nonlocal drift_parent_done
        drift_parent_done = False
        p_parent_drift.start()

    def undo_parent_drift():
        p_parent_drift.stop()
        os.chmod(autopilot, 0o700)

    _run_fault(project, do_parent_drift, undo_parent_drift)
