"""Hermetic fault matrix tests for terminal continuation storage absence."""

from __future__ import annotations

import os

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

import errno
from pathlib import Path
import shutil
import stat
from typing import Any, Callable, Iterator
import pytest

import scripts.two_site_provider_broker as broker
from scripts.two_site_provider_broker import STATE_REL
import scripts.two_site_quota_continuation_terminal_storage as terminal_storage
from scripts.two_site_quota_continuation_terminal_storage import (
    TERMINAL_DIR_NAME,
    TerminalContinuationRecordAbsentError,
    TerminalContinuationStorageError,
    read_terminal_continuation_record,
)

REQUEST_ID = "0123456789abcdef0123456789abcdef"
SENTINEL_NAME = "custody_sentinel.bin"
SENTINEL_BYTES = b"REAL_CUSTODY_SENTINEL_BYTES_0123456789"


class BoundaryTrap:
    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.monkeypatch = monkeypatch
        self.calls: dict[str, int] = {
            "submit": 0,
            "submit_maintenance": 0,
            "execute_pipeline": 0,
            "publish": 0,
        }
        self.traps: dict[str, Any] = {}
        self.originals: dict[str, Any] = {}
        self._install()

    def _trap(self, name: str) -> Callable[..., Any]:
        def _guarded(*args: Any, **kwargs: Any) -> Any:
            self.calls[name] += 1
            raise AssertionError(f"Boundary trap tripped: {name} was called")

        return _guarded

    def _install(self) -> None:
        for name in ("submit", "submit_maintenance", "execute_pipeline"):
            self.originals[name] = getattr(broker, name)
            fn = self._trap(name)
            self.traps[name] = fn
            self.monkeypatch.setattr(broker, name, fn)
        self.originals["publish"] = getattr(
            terminal_storage, "publish_terminal_continuation_record"
        )
        pub_fn = self._trap("publish")
        self.traps["publish"] = pub_fn
        self.monkeypatch.setattr(
            terminal_storage, "publish_terminal_continuation_record", pub_fn
        )

    def assert_active(self) -> None:
        for name in ("submit", "submit_maintenance", "execute_pipeline"):
            current = getattr(broker, name)
            assert current is self.traps[name], f"Trap {name} identity mismatch"
            assert self.calls[name] == 0, f"Trap {name} was called {self.calls[name]} times"
        current_pub = getattr(terminal_storage, "publish_terminal_continuation_record")
        assert current_pub is self.traps["publish"], "Trap publish identity mismatch"
        assert self.calls["publish"] == 0, f"Trap publish was called {self.calls['publish']} times"


@pytest.fixture
def boundary_guards() -> Iterator[BoundaryTrap]:
    owner_mp = pytest.MonkeyPatch()
    trap = BoundaryTrap(owner_mp)
    trap.assert_active()
    try:
        yield trap
    finally:
        try:
            trap.assert_active()
        finally:
            owner_mp.undo()


def _setup_tree(tmp_path: Path, *, with_terminal_dir: bool = False) -> tuple[Path, Path, Path]:
    root = tmp_path / "repo_root"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(root, 0o700)

    sentinel = root / SENTINEL_NAME
    sentinel.write_bytes(SENTINEL_BYTES)
    os.chmod(sentinel, 0o600)

    cur = root
    for part in STATE_REL.parts:
        cur = cur / part
        cur.mkdir(mode=0o700, exist_ok=True)
        os.chmod(cur, 0o700)
    broker_dir = cur

    term_dir = broker_dir / TERMINAL_DIR_NAME
    if with_terminal_dir:
        term_dir.mkdir(mode=0o700, exist_ok=True)
        os.chmod(term_dir, 0o700)

    return root, broker_dir, term_dir


def _snapshot_tree(root: Path) -> dict[str, tuple[int, int, bytes | None]]:
    snap: dict[str, tuple[int, int, bytes | None]] = {}
    for p in sorted(root.rglob("*")):
        rel = str(p.relative_to(root))
        st = p.lstat()
        content = p.read_bytes() if stat.S_ISREG(st.st_mode) else None
        snap[rel] = (stat.S_IMODE(st.st_mode), st.st_size, content)
    return snap


def test_boundary_guards_removal_sensitivity(boundary_guards: BoundaryTrap) -> None:
    boundary_guards.assert_active()
    original_submit = boundary_guards.originals["submit"]
    with pytest.MonkeyPatch.context() as nested_mp:
        nested_mp.setattr(broker, "submit", original_submit)
        with pytest.raises(AssertionError, match="Trap submit identity mismatch"):
            boundary_guards.assert_active()
    boundary_guards.assert_active()
    assert boundary_guards.calls["submit"] == 0


def test_fault_matrix_1_genuine_absence(tmp_path: Path, boundary_guards: BoundaryTrap) -> None:
    root, broker_dir, term_dir = _setup_tree(tmp_path, with_terminal_dir=False)
    snap_before = _snapshot_tree(root)
    with pytest.raises(TerminalContinuationRecordAbsentError, match="cannot lstat terminal-continuation"):
        read_terminal_continuation_record(root, REQUEST_ID)
    assert not term_dir.exists()
    assert _snapshot_tree(root) == snap_before
    boundary_guards.assert_active()

    term_dir.mkdir(mode=0o700, exist_ok=True)
    os.chmod(term_dir, 0o700)
    snap_before2 = _snapshot_tree(root)
    with pytest.raises(TerminalContinuationRecordAbsentError, match="cannot stat receipt"):
        read_terminal_continuation_record(root, REQUEST_ID)
    receipt_path = term_dir / f"{REQUEST_ID}.json"
    assert not receipt_path.exists()
    assert _snapshot_tree(root) == snap_before2
    boundary_guards.assert_active()


@pytest.mark.parametrize(
    "missing_part",
    ["root", ".local", "two-site-refactor", "provider-broker"],
)
def test_fault_matrix_2_earlier_ancestors_missing(
    tmp_path: Path, boundary_guards: BoundaryTrap, missing_part: str
) -> None:
    root, broker_dir, _ = _setup_tree(tmp_path, with_terminal_dir=False)
    if missing_part == "root":
        shutil.rmtree(root)
        target_root = root
    elif missing_part == ".local":
        shutil.rmtree(root / ".local")
        target_root = root
    elif missing_part == "two-site-refactor":
        shutil.rmtree(root / ".local" / "two-site-refactor")
        target_root = root
    else:
        shutil.rmtree(broker_dir)
        target_root = root

    snap_before = _snapshot_tree(root) if root.exists() else {}
    with pytest.raises(TerminalContinuationStorageError) as exc_info:
        read_terminal_continuation_record(target_root, REQUEST_ID)
    assert not isinstance(exc_info.value, TerminalContinuationRecordAbsentError)
    assert type(exc_info.value) is TerminalContinuationStorageError

    if missing_part == "root":
        assert not root.exists()
    elif missing_part == ".local":
        assert not (root / ".local").exists()
        assert _snapshot_tree(root) == snap_before
    elif missing_part == "two-site-refactor":
        assert not (root / ".local" / "two-site-refactor").exists()
        assert _snapshot_tree(root) == snap_before
    else:
        assert not broker_dir.exists()
        assert _snapshot_tree(root) == snap_before
    boundary_guards.assert_active()


@pytest.mark.parametrize(
    "scenario",
    [
        "broken_term_symlink",
        "intact_term_symlink",
        "broken_receipt_symlink",
        "intact_receipt_symlink",
        "receipt_hardlink",
        "term_dir_mode_0755",
        "broker_dir_mode_0755",
        "corrupt_receipt_file",
    ],
)
def test_fault_matrix_3_unsafe_and_corrupt(
    tmp_path: Path, boundary_guards: BoundaryTrap, scenario: str
) -> None:
    root, broker_dir, term_dir = _setup_tree(tmp_path, with_terminal_dir=False)
    outside = tmp_path / "outside_dummy"
    outside.mkdir(mode=0o700, exist_ok=True)
    outside_file = outside / "dummy.bin"
    outside_file.write_bytes(b"OUTSIDE_UNTOUCHED_BYTES")

    if scenario == "broken_term_symlink":
        (broker_dir / TERMINAL_DIR_NAME).symlink_to(tmp_path / "nonexistent")
    elif scenario == "intact_term_symlink":
        (broker_dir / TERMINAL_DIR_NAME).symlink_to(outside)
    elif scenario == "broken_receipt_symlink":
        term_dir.mkdir(mode=0o700, exist_ok=True)
        (term_dir / f"{REQUEST_ID}.json").symlink_to(tmp_path / "nonexistent")
    elif scenario == "intact_receipt_symlink":
        term_dir.mkdir(mode=0o700, exist_ok=True)
        (term_dir / f"{REQUEST_ID}.json").symlink_to(outside_file)
    elif scenario == "receipt_hardlink":
        term_dir.mkdir(mode=0o700, exist_ok=True)
        os.link(outside_file, term_dir / f"{REQUEST_ID}.json")
    elif scenario == "term_dir_mode_0755":
        term_dir.mkdir(mode=0o755, exist_ok=True)
        os.chmod(term_dir, 0o755)
    elif scenario == "broker_dir_mode_0755":
        os.chmod(broker_dir, 0o755)
    elif scenario == "corrupt_receipt_file":
        term_dir.mkdir(mode=0o700, exist_ok=True)
        rf = term_dir / f"{REQUEST_ID}.json"
        rf.write_bytes(b"not json at all {corrupt: content}")
        os.chmod(rf, 0o600)

    snap_before = _snapshot_tree(root)
    outside_bytes_before = outside_file.read_bytes()

    with pytest.raises(TerminalContinuationStorageError) as exc_info:
        read_terminal_continuation_record(root, REQUEST_ID)
    assert not isinstance(exc_info.value, TerminalContinuationRecordAbsentError)

    assert outside_file.read_bytes() == outside_bytes_before
    assert _snapshot_tree(root) == snap_before
    boundary_guards.assert_active()


@pytest.mark.parametrize("err_code", [errno.EACCES, errno.EPERM])
def test_fault_matrix_4_permission_denials(
    tmp_path: Path, boundary_guards: BoundaryTrap, err_code: int
) -> None:
    root, broker_dir, term_dir = _setup_tree(tmp_path, with_terminal_dir=True)
    target_path = term_dir / f"{REQUEST_ID}.json"
    reached = {"leaf_stat": 0, "late_ancestor": 0}

    real_lstat = os.lstat

    def guarded_lstat_leaf(path: Any, *args: Any, **kwargs: Any) -> Any:
        if Path(path) == target_path:
            reached["leaf_stat"] += 1
            raise PermissionError(err_code, "denied leaf stat")
        return real_lstat(path, *args, **kwargs)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(os, "lstat", guarded_lstat_leaf)
        snap_before = _snapshot_tree(root)
        with pytest.raises(TerminalContinuationStorageError) as exc_info:
            read_terminal_continuation_record(root, REQUEST_ID)
        assert not isinstance(exc_info.value, TerminalContinuationRecordAbsentError)
        assert type(exc_info.value) is TerminalContinuationStorageError
        assert reached["leaf_stat"] == 1
        assert _snapshot_tree(root) == snap_before
        boundary_guards.assert_active()

    real_lstat2 = os.lstat
    real_stat2 = os.stat
    leaf_enoent_observed = {"lstat": False, "dirfd": False}

    def guarded_stat_leaf_and_ancestor(name: Any, *args: Any, **kwargs: Any) -> Any:
        try:
            return real_stat2(name, *args, **kwargs)
        except FileNotFoundError:
            if name == f"{REQUEST_ID}.json":
                leaf_enoent_observed["dirfd"] = True
            raise

    def guarded_lstat_late(path: Any, *args: Any, **kwargs: Any) -> Any:
        p = Path(path)
        if p == target_path:
            leaf_enoent_observed["lstat"] = True
            return real_lstat2(path, *args, **kwargs)
        if leaf_enoent_observed["lstat"] and leaf_enoent_observed["dirfd"] and p == broker_dir:
            reached["late_ancestor"] += 1
            raise PermissionError(err_code, "denied late ancestor verification")
        return real_lstat2(path, *args, **kwargs)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(os, "stat", guarded_stat_leaf_and_ancestor)
        mp.setattr(os, "lstat", guarded_lstat_late)
        snap_before2 = _snapshot_tree(root)
        with pytest.raises(TerminalContinuationStorageError) as exc_info2:
            read_terminal_continuation_record(root, REQUEST_ID)
        assert not isinstance(exc_info2.value, TerminalContinuationRecordAbsentError)
        assert type(exc_info2.value) is TerminalContinuationStorageError
        assert reached["late_ancestor"] == 1
        assert _snapshot_tree(root) == snap_before2
        boundary_guards.assert_active()


def test_fault_matrix_5_dual_view_disagreement(
    tmp_path: Path, boundary_guards: BoundaryTrap
) -> None:
    root, broker_dir, term_dir = _setup_tree(tmp_path, with_terminal_dir=True)
    real_lstat = os.lstat
    real_stat = os.stat

    reached_5a = 0
    def hook_lstat_term_enoent(path: Any, *args: Any, **kwargs: Any) -> Any:
        nonlocal reached_5a
        if Path(path) == term_dir:
            reached_5a += 1
            raise FileNotFoundError(errno.ENOENT, "hooked enoent")
        return real_lstat(path, *args, **kwargs)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(os, "lstat", hook_lstat_term_enoent)
        with pytest.raises(TerminalContinuationStorageError, match="disagreement on presence of terminal-continuation") as exc:
            read_terminal_continuation_record(root, REQUEST_ID)
        assert not isinstance(exc.value, TerminalContinuationRecordAbsentError)
        assert reached_5a == 1
        boundary_guards.assert_active()

    reached_5b = 0
    def hook_dirfd_term_enoent(name: Any, *args: Any, **kwargs: Any) -> Any:
        nonlocal reached_5b
        if name == TERMINAL_DIR_NAME and kwargs.get("dir_fd") is not None:
            reached_5b += 1
            raise FileNotFoundError(errno.ENOENT, "hooked enoent")
        return real_stat(name, *args, **kwargs)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(os, "stat", hook_dirfd_term_enoent)
        with pytest.raises(TerminalContinuationStorageError, match="disagreement on presence of terminal-continuation") as exc:
            read_terminal_continuation_record(root, REQUEST_ID)
        assert not isinstance(exc.value, TerminalContinuationRecordAbsentError)
        assert reached_5b == 1
        boundary_guards.assert_active()

    receipt_path = term_dir / f"{REQUEST_ID}.json"
    receipt_path.write_bytes(b"{}")
    os.chmod(receipt_path, 0o600)

    reached_5c = 0
    def hook_lstat_receipt_enoent(path: Any, *args: Any, **kwargs: Any) -> Any:
        nonlocal reached_5c
        if Path(path) == receipt_path:
            reached_5c += 1
            raise FileNotFoundError(errno.ENOENT, "hooked enoent")
        return real_lstat(path, *args, **kwargs)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(os, "lstat", hook_lstat_receipt_enoent)
        with pytest.raises(TerminalContinuationStorageError, match="disagreement on presence of receipt") as exc:
            read_terminal_continuation_record(root, REQUEST_ID)
        assert not isinstance(exc.value, TerminalContinuationRecordAbsentError)
        assert reached_5c == 1
        boundary_guards.assert_active()

    reached_5d = 0
    def hook_dirfd_receipt_enoent(name: Any, *args: Any, **kwargs: Any) -> Any:
        nonlocal reached_5d
        if name == f"{REQUEST_ID}.json" and kwargs.get("dir_fd") is not None:
            reached_5d += 1
            raise FileNotFoundError(errno.ENOENT, "hooked enoent")
        return real_stat(name, *args, **kwargs)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(os, "stat", hook_dirfd_receipt_enoent)
        with pytest.raises(TerminalContinuationStorageError, match="disagreement on presence of receipt") as exc:
            read_terminal_continuation_record(root, REQUEST_ID)
        assert not isinstance(exc.value, TerminalContinuationRecordAbsentError)
        assert reached_5d == 1
        boundary_guards.assert_active()


def test_fault_matrix_6_visible_parent_swap(
    tmp_path: Path, boundary_guards: BoundaryTrap
) -> None:
    root, broker_dir, term_dir = _setup_tree(tmp_path, with_terminal_dir=True)
    target_path = term_dir / f"{REQUEST_ID}.json"
    saved_parent = tmp_path / "saved_provider_broker"
    swap_info = {"count": 0, "old_ino": 0, "new_ino": 0, "held_descriptor_ino": 0}

    real_stat = os.stat
    real_lstat = os.lstat
    leaf_stat_done = {"path": False, "dirfd": False}

    def wrapped_lstat(path: Any, *args: Any, **kwargs: Any) -> Any:
        p = Path(path)
        if p == target_path:
            leaf_stat_done["path"] = True
            return real_lstat(path, *args, **kwargs)
        return real_lstat(path, *args, **kwargs)

    def wrapped_stat(name: Any, *args: Any, **kwargs: Any) -> Any:
        if name == f"{REQUEST_ID}.json":
            leaf_stat_done["dirfd"] = True
            try:
                return real_stat(name, *args, **kwargs)
            except FileNotFoundError:
                if swap_info["count"] == 0 and leaf_stat_done["path"]:
                    swap_info["count"] += 1
                    term_fd = kwargs.get("dir_fd")
                    assert term_fd is not None
                    term_fst = os.fstat(term_fd)
                    swap_info["held_descriptor_ino"] = term_fst.st_ino
                    st_old = broker_dir.stat()
                    swap_info["old_ino"] = st_old.st_ino
                    os.rename(broker_dir, saved_parent)
                    broker_dir.mkdir(mode=0o700)
                    os.chmod(broker_dir, 0o700)
                    swap_info["new_ino"] = broker_dir.stat().st_ino
                    assert swap_info["new_ino"] != swap_info["old_ino"]
                raise
        return real_stat(name, *args, **kwargs)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(os, "lstat", wrapped_lstat)
        mp.setattr(os, "stat", wrapped_stat)

        with pytest.raises(TerminalContinuationStorageError, match="directory path identity changed") as exc:
            read_terminal_continuation_record(root, REQUEST_ID)
        assert not isinstance(exc.value, TerminalContinuationRecordAbsentError)
        assert swap_info["count"] == 1
        assert swap_info["held_descriptor_ino"] == (saved_parent / TERMINAL_DIR_NAME).stat().st_ino
        assert saved_parent.stat().st_ino == swap_info["old_ino"]
        assert broker_dir.stat().st_ino == swap_info["new_ino"]
        assert (root / SENTINEL_NAME).read_bytes() == SENTINEL_BYTES
        boundary_guards.assert_active()


def test_fault_matrix_7_late_disappearance_and_denial(
    tmp_path: Path, boundary_guards: BoundaryTrap
) -> None:
    root, broker_dir, term_dir = _setup_tree(tmp_path, with_terminal_dir=True)
    target_path = term_dir / f"{REQUEST_ID}.json"

    real_lstat = os.lstat
    real_stat = os.stat
    leaf_done = {"path": False, "dirfd": False}
    reached = {"disappeared": 0, "denied": 0}

    def track_stat(name: Any, *args: Any, **kwargs: Any) -> Any:
        try:
            return real_stat(name, *args, **kwargs)
        except FileNotFoundError:
            if name == f"{REQUEST_ID}.json":
                leaf_done["dirfd"] = True
            raise

    def late_disappear_lstat(path: Any, *args: Any, **kwargs: Any) -> Any:
        p = Path(path)
        if p == target_path:
            leaf_done["path"] = True
            return real_lstat(path, *args, **kwargs)
        if leaf_done["path"] and leaf_done["dirfd"] and p == broker_dir:
            reached["disappeared"] += 1
            raise FileNotFoundError(errno.ENOENT, "ancestor disappeared")
        return real_lstat(path, *args, **kwargs)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(os, "stat", track_stat)
        mp.setattr(os, "lstat", late_disappear_lstat)
        with pytest.raises(TerminalContinuationStorageError, match="directory verification failed for provider-broker: errno=2") as exc:
            read_terminal_continuation_record(root, REQUEST_ID)
        assert not isinstance(exc.value, TerminalContinuationRecordAbsentError)
        assert reached["disappeared"] == 1
        boundary_guards.assert_active()

    leaf_done["path"] = False
    leaf_done["dirfd"] = False

    def late_denied_lstat(path: Any, *args: Any, **kwargs: Any) -> Any:
        p = Path(path)
        if p == target_path:
            leaf_done["path"] = True
            return real_lstat(path, *args, **kwargs)
        if leaf_done["path"] and leaf_done["dirfd"] and p == broker_dir:
            reached["denied"] += 1
            raise PermissionError(errno.EACCES, "ancestor permission revoked")
        return real_lstat(path, *args, **kwargs)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(os, "stat", track_stat)
        mp.setattr(os, "lstat", late_denied_lstat)
        with pytest.raises(TerminalContinuationStorageError, match="directory verification failed for provider-broker: errno=13") as exc2:
            read_terminal_continuation_record(root, REQUEST_ID)
        assert not isinstance(exc2.value, TerminalContinuationRecordAbsentError)
        assert reached["denied"] == 1
        boundary_guards.assert_active()
