"""Adversarial custody/lifecycle cases with real temporary artifacts."""
from __future__ import annotations

import os
os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"
from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence
install_p1_00_network_fence()

from contextlib import contextmanager
import errno
import multiprocessing
from pathlib import Path
import socket
import stat
from types import SimpleNamespace

import pytest

from scripts import two_site_accepted_work_evidence as evidence
from tests.test_two_site_accepted_work_evidence import (
    ArtifactTree, FIXED_CODES, METRICS, PRIVATE_SENTINEL, STAT_FIELDS, TASKS,
    fixed_error, metadata_oracle, read_once,
)


def stat_proxy(original, field, changed):
    """Copy ALL nine real fields (including ns), then change exactly one."""
    values = {name: getattr(original, name) for name in STAT_FIELDS}
    assert len(values) == 9
    values[field] = changed
    proxy = SimpleNamespace(**values)
    assert all(getattr(proxy, name) == getattr(original, name)
               for name in STAT_FIELDS if name != field)
    assert getattr(proxy, field) != getattr(original, field)
    return proxy


def task_witness(root, body=b"abcd"):
    with evidence._evidence_files(root) as snapshot:
        assert snapshot._read("task", "probe") == body
        assert snapshot._metadata() == metadata_oracle({f"{TASKS}/probe.json": body})
        assert snapshot._verify() is None


@contextmanager
def descriptors(patcher):
    real_open, real_close, real_fstat = os.open, os.close, os.fstat
    opened = []
    def tracked_open(*args, **kwargs):
        flags = args[1]
        assert flags & (os.O_NOFOLLOW | os.O_CLOEXEC) == os.O_NOFOLLOW | os.O_CLOEXEC
        if not flags & os.O_DIRECTORY:
            assert flags & os.O_NONBLOCK
        descriptor = real_open(*args, **kwargs)
        opened.append(descriptor)
        return descriptor
    patcher.setattr(evidence.os, "open", tracked_open)
    try:
        yield opened, real_open, real_close, real_fstat
    finally:
        # No /proc scan: every descriptor we observed must actually be closed.
        for descriptor in opened:
            with pytest.raises(OSError):
                real_fstat(descriptor)


def fifo_child(root, sender):
    try:
        read_once(Path(root), "task", "probe")
    except evidence._EvidenceFilesError as error:
        sender.send((error.code, str(error)))
    finally:
        sender.close()


def test_fifo_symlink_hardlink_mode_owner_and_type_rejection(tmp_path, monkeypatch):
    fifo_tree = ArtifactTree(tmp_path / "fifo")
    os.mkfifo(fifo_tree.root / TASKS / "probe.json", mode=0o600)
    # A bad blocking implementation cannot hang the test process indefinitely.
    context = multiprocessing.get_context("fork")
    receiver, sender = context.Pipe(duplex=False)
    child = context.Process(target=fifo_child, args=(str(fifo_tree.root), sender))
    child.start()
    sender.close()
    try:
        child.join(2)
        if child.is_alive():
            child.terminate()
            child.join(2)
            pytest.fail("FIFO leaf read blocked")
        assert child.exitcode == 0
        assert receiver.poll(0.5)
        assert receiver.recv() == ("UNSAFE_FILE", "UNSAFE_FILE")
    finally:
        if child.is_alive():
            child.terminate()
            child.join(2)
            if child.is_alive():
                child.kill()
                child.join(2)
        assert not child.is_alive()
        receiver.close()
        child.close()

    outside = tmp_path / "outside"
    outside.write_bytes(b"outside-byte-custody")
    outside.chmod(0o600)
    for label in ("symlink", "hardlink", "directory", "group-write", "world-write", "socket"):
        tree = ArtifactTree(tmp_path / label)
        leaf = tree.root / TASKS / "probe.json"
        sock = None
        if label == "symlink":
            leaf.symlink_to(outside)
        elif label == "hardlink":
            os.link(outside, leaf)
        elif label == "directory":
            leaf.mkdir()
        elif label == "socket":
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            with monkeypatch.context() as patcher:
                patcher.chdir(leaf.parent)
                sock.bind("probe.json")
        else:
            tree.put(f"{TASKS}/probe.json", b"abcd")
            leaf.chmod(0o620 if label == "group-write" else 0o602)
        before = (outside.read_bytes(), tuple(getattr(outside.stat(), name) for name in STAT_FIELDS))
        try:
            fixed_error("UNSAFE_FILE", lambda: read_once(tree.root, "task", "probe"))
        finally:
            if sock is not None:
                sock.close()
        assert (outside.read_bytes(), tuple(getattr(outside.stat(), name) for name in STAT_FIELDS)) == before

    tree = ArtifactTree(tmp_path / "root-and-parent")
    tree.put(f"{TASKS}/probe.json", b"abcd")
    alias = tmp_path / "root-alias"
    alias.symlink_to(tree.root, target_is_directory=True)
    fixed_error("INVALID_ROOT", lambda: read_once(alias, "task", "probe"))
    fixed_error("INVALID_ROOT", lambda: read_once(tmp_path / "absent-root", "task", "probe"))
    fixed_error("INVALID_ROOT", lambda: read_once(outside, "task", "probe"))
    parent = tree.root / TASKS
    moved = parent.with_name("tasks-original")
    parent.rename(moved)
    parent.symlink_to(moved, target_is_directory=True)
    fixed_error("UNSAFE_PATH", lambda: read_once(tree.root, "task", "probe"))
    parent.unlink()
    moved.rename(parent)
    task_witness(tree.root)

    leaf = tree.root / TASKS / "probe.json"
    foreign_owner_created = False
    if os.geteuid() == 0:
        try:
            os.chown(leaf, 65534, -1)
            foreign_owner_created = True
        except OSError as error:
            if error.errno not in (errno.EPERM, errno.EACCES, errno.EINVAL):
                raise
    if foreign_owner_created:
        try:
            fixed_error("UNSAFE_FILE", lambda: read_once(tree.root, "task", "probe"))
        finally:
            os.chown(leaf, os.geteuid(), -1)
    else:
        real_fstat = os.fstat
        def foreign_owner(descriptor):
            info = real_fstat(descriptor)
            if stat.S_ISREG(info.st_mode):
                return stat_proxy(info, "st_uid", info.st_uid + 1)
            return info
        with monkeypatch.context() as patcher:
            patcher.setattr(evidence.os, "fstat", foreign_owner)
            fixed_error("UNSAFE_FILE", lambda: read_once(tree.root, "task", "probe"))
    # Use a real character device if permitted, otherwise the exact metadata proxy.
    device_tree = ArtifactTree(tmp_path / "device")
    device_leaf = device_tree.root / TASKS / "probe.json"
    simulated_device = False
    try:
        os.mknod(device_leaf, stat.S_IFCHR | 0o600, os.makedev(1, 3))
    except OSError as error:
        if error.errno not in (errno.EPERM, errno.EACCES, errno.ENOSYS):
            raise
        simulated_device = True
    if not simulated_device:
        fixed_error("UNSAFE_FILE", lambda: read_once(device_tree.root, "task", "probe"))
    real_fstat = os.fstat
    def device_type(descriptor):
        info = real_fstat(descriptor)
        if stat.S_ISREG(info.st_mode):
            return stat_proxy(info, "st_mode", stat.S_IFCHR | stat.S_IMODE(info.st_mode))
        return info
    if simulated_device:
        with monkeypatch.context() as patcher:
            patcher.setattr(evidence.os, "fstat", device_type)
            fixed_error("UNSAFE_FILE", lambda: read_once(tree.root, "task", "probe"))
    task_witness(tree.root)


def test_nine_field_file_and_named_directory_custody(tmp_path, monkeypatch):
    tree = ArtifactTree(tmp_path / "custody")
    leaf = tree.put(f"{TASKS}/probe.json", b"abcd")
    task_witness(tree.root)
    for field in STAT_FIELDS:
        real_fstat, real_read = os.fstat, os.read
        did_read = [False]
        def read_then_arm(descriptor, amount):
            assert 0 < amount <= 65536
            body = real_read(descriptor, amount)
            if body:
                did_read[0] = True
            return body
        def changed_after_read(descriptor):
            info = real_fstat(descriptor)
            if did_read[0] and stat.S_ISREG(info.st_mode):
                value = getattr(info, field)
                changed = value ^ 0o100 if field == "st_mode" else value + 1
                return stat_proxy(info, field, changed)
            return info
        with monkeypatch.context() as patcher:
            patcher.setattr(evidence.os, "read", read_then_arm)
            patcher.setattr(evidence.os, "fstat", changed_after_read)
            fixed_error("CUSTODY_DRIFT", lambda: task_witness(tree.root))
            assert did_read[0], field
        task_witness(tree.root)

    for action in ("replace", "unlink", "parent-replace"):
        def drift():
            with evidence._evidence_files(tree.root) as snapshot:
                assert snapshot._read("task", "probe") == b"abcd"
                if action == "parent-replace":
                    parent = leaf.parent
                    parent.rename(parent.with_name("tasks-held"))
                    parent.mkdir(mode=0o700)
                    (parent / "probe.json").write_bytes(b"abcd")
                else:
                    leaf.rename(leaf.with_name("probe-held.json"))
                    if action == "replace":
                        leaf.write_bytes(b"abcd")
                if action == "replace":
                    snapshot._read("task", "probe")  # Cached bytes still require named custody.
                else:
                    snapshot._verify()
        try:
            fixed_error("CUSTODY_DRIFT", drift)
        finally:
            if action == "parent-replace":
                leaf.unlink()
                leaf.parent.rmdir()
                leaf.parent.with_name("tasks-held").rename(leaf.parent)
            else:
                if leaf.exists():
                    leaf.unlink()
                leaf.with_name("probe-held.json").rename(leaf)
        task_witness(tree.root)

    # Independent directory identity includes six fields, never directory timestamps.
    for factor in ("st_dev", "st_ino", "type", "mode", "st_uid", "st_gid"):
        real_fstat, real_read = os.fstat, os.read
        did_read = [False]
        parent_identity = leaf.parent.stat().st_ino
        def arm_directory(descriptor, amount):
            result = real_read(descriptor, amount)
            did_read[0] = did_read[0] or bool(result)
            return result
        def directory_drift(descriptor):
            info = real_fstat(descriptor)
            if did_read[0] and stat.S_ISDIR(info.st_mode) and info.st_ino == parent_identity:
                field = "st_mode" if factor in ("type", "mode") else factor
                changed = (stat.S_IFREG | stat.S_IMODE(info.st_mode) if factor == "type"
                           else info.st_mode ^ 0o100 if factor == "mode"
                           else getattr(info, field) + 1)
                return stat_proxy(info, field, changed)
            return info
        with monkeypatch.context() as patcher:
            patcher.setattr(evidence.os, "read", arm_directory)
            patcher.setattr(evidence.os, "fstat", directory_drift)
            fixed_error("CUSTODY_DRIFT", lambda: task_witness(tree.root))
        task_witness(tree.root)

    # Directory timestamps, size and link counts are deliberately NOT identity.
    with evidence._evidence_files(tree.root) as snapshot:
        assert snapshot._read("task", "probe") == b"abcd"
        (leaf.parent / "unrelated-directory").mkdir()
        (leaf.parent / "unrelated-file").write_bytes(b"not-read")
        assert snapshot._verify() is None
        assert snapshot._metadata() == metadata_oracle({f"{TASKS}/probe.json": b"abcd"})
    real_stat = os.stat
    def wrong_named_leaf(path, *args, **kwargs):
        info = real_stat(path, *args, **kwargs)
        if str(path) == "probe.json" and kwargs.get("follow_symlinks") is False:
            return stat_proxy(info, "st_ino", info.st_ino + 1)
        return info
    with monkeypatch.context() as patcher:
        patcher.setattr(evidence.os, "stat", wrong_named_leaf)
        fixed_error("CUSTODY_DRIFT", lambda: task_witness(tree.root))
    task_witness(tree.root)

    missing_tree = ArtifactTree(tmp_path / "negative")
    with evidence._evidence_files(missing_tree.root) as snapshot:
        for _ in range(2):
            fixed_error("MISSING_FILE", lambda: snapshot._read("task", "probe"))
        assert snapshot._verify() is None
        assert snapshot._metadata() == {"schema_version": 1, "files": [], "bytes_read": 0}
    def name_appears():
        with evidence._evidence_files(missing_tree.root) as snapshot:
            fixed_error("MISSING_FILE", lambda: snapshot._read("task", "probe"))
            missing_tree.put(f"{TASKS}/probe.json", b"abcd")
            snapshot._verify()
    fixed_error("CUSTODY_DRIFT", name_appears)
    task_witness(missing_tree.root)
    absent_parent = ArtifactTree(tmp_path / "missing-parent")
    (absent_parent.root / TASKS).rmdir()
    fixed_error("UNSAFE_PATH", lambda: read_once(absent_parent.root, "task", "probe"))
    unsafe_parent = ArtifactTree(tmp_path / "unsafe-parent")
    (unsafe_parent.root / TASKS).chmod(0o722)
    fixed_error("UNSAFE_PATH", lambda: read_once(unsafe_parent.root, "task", "probe"))
    real_open = os.open
    def false_missing(path, flags, *args, **kwargs):
        if str(path) == "probe.json":
            raise FileNotFoundError(PRIVATE_SENTINEL)
        return real_open(path, flags, *args, **kwargs)
    with monkeypatch.context() as patcher:
        patcher.setattr(evidence.os, "open", false_missing)
        fixed_error("CUSTODY_DRIFT", lambda: read_once(tree.root, "task", "probe"))
    denied_negative = ArtifactTree(tmp_path / "denied-negative")
    real_stat = os.stat
    def denied_negative_stat(path, *args, **kwargs):
        if str(path) == "probe.json":
            raise PermissionError(PRIVATE_SENTINEL)
        return real_stat(path, *args, **kwargs)
    with monkeypatch.context() as patcher:
        patcher.setattr(evidence.os, "stat", denied_negative_stat)
        fixed_error("UNSAFE_FILE", lambda: read_once(denied_negative.root, "task", "probe"))
    def negative_permission_drift():
        with evidence._evidence_files(denied_negative.root) as snapshot:
            fixed_error("MISSING_FILE", lambda: snapshot._read("task", "probe"))
            with monkeypatch.context() as patcher:
                patcher.setattr(evidence.os, "stat", denied_negative_stat)
                snapshot._verify()
    fixed_error("CUSTODY_DRIFT", negative_permission_drift)




def test_reached_path_corruption_independent_oracle(tmp_path, monkeypatch):
    tree = ArtifactTree(tmp_path / "oracle")
    tree.put(f"{TASKS}/probe.json", b"abcd")
    task_witness(tree.root)

    identifier = "a" * 128
    maximum_path = f"{TASKS}/{identifier}.json"
    tree.put(maximum_path, b"safe-128")
    with evidence._evidence_files(tree.root) as snapshot:
        assert snapshot._read("task", identifier) == b"safe-128"
        assert snapshot._metadata() == metadata_oracle({maximum_path: b"safe-128"})
    aliases = ".local/two-site-refactor/proposals/alias-small/result.json"
    tree.put(aliases, b"small-alias")
    with evidence._evidence_files(tree.root) as snapshot:
        assert snapshot._read("reviewer_result", "alias-small") == b"small-alias"
        assert snapshot._read("worker_result", "alias-small") == b"small-alias"
        assert snapshot._metadata() == metadata_oracle({aliases: b"small-alias"})
    actual_metadata = evidence._EvidenceFiles._metadata
    def corrupted_metadata(snapshot):
        result = actual_metadata(snapshot)
        if result["files"]:
            result["files"][0]["sha256"] = "0" * 64
        return result
    with monkeypatch.context() as patcher:
        patcher.setattr(evidence._EvidenceFiles, "_metadata", corrupted_metadata)
        with pytest.raises(AssertionError):
            task_witness(tree.root)
    task_witness(tree.root)

    # Genuine cache hits recheck custody but never silently read raw bytes again.
    real_read = os.read
    calls = []
    def counted_read(descriptor, amount):
        assert 0 < amount <= 65536
        result = real_read(descriptor, amount)
        calls.append((amount, len(result)))
        return result
    with monkeypatch.context() as patcher:
        patcher.setattr(evidence.os, "read", counted_read)
        with evidence._evidence_files(tree.root) as snapshot:
            snapshot._read("task", "probe")
            initial_calls = list(calls)
            for _ in range(3):
                assert snapshot._read("task", "probe") == b"abcd"
                assert snapshot._verify() is None
            assert initial_calls and calls == initial_calls
            assert snapshot._metadata() == metadata_oracle({f"{TASKS}/probe.json": b"abcd"})

    # Actual failed read bytes remain charged; a repeat starts at offset0 but not budget0.
    body = b"x" * 160000
    tree.put(f"{TASKS}/probe.json", body)
    with monkeypatch.context() as patcher:
        first = [True]
        def partial_io(descriptor, amount):
            if first[0]:
                first[0] = False
                return real_read(descriptor, amount)
            raise OSError(PRIVATE_SENTINEL)
        with evidence._evidence_files(tree.root) as snapshot:
            patcher.setattr(evidence.os, "read", partial_io)
            fixed_error("UNSAFE_FILE", lambda: snapshot._read("task", "probe"))
            assert snapshot._metadata() == {"schema_version": 1, "files": [], "bytes_read": 65536}
            patcher.setattr(evidence.os, "read", real_read)
            assert snapshot._read("task", "probe") == body
            assert snapshot._metadata() == metadata_oracle({f"{TASKS}/probe.json": body}, bytes_read=225536)

    real_fstat = os.fstat
    armed = [False]
    def arm_after_chunk(descriptor, amount):
        chunk = real_read(descriptor, amount)
        if chunk:
            armed[0] = True
        return chunk
    def postread_drift(descriptor):
        info = real_fstat(descriptor)
        if armed[0] and stat.S_ISREG(info.st_mode):
            return stat_proxy(info, "st_ctime_ns", info.st_ctime_ns + 1)
        return info
    with evidence._evidence_files(tree.root) as snapshot:
        with monkeypatch.context() as patcher:
            patcher.setattr(evidence.os, "read", arm_after_chunk)
            patcher.setattr(evidence.os, "fstat", postread_drift)
            for _ in range(50):
                armed[0] = False
                fixed_error("CUSTODY_DRIFT", lambda: snapshot._read("task", "probe"))
            assert snapshot._bytes_read == 8000000
            armed[0] = False
            fixed_error("BYTE_LIMIT", lambda: snapshot._read("task", "probe"))
        assert snapshot._metadata() == {"schema_version": 1, "files": [], "bytes_read": 8000000}

    # Real growth after a chunk proves limit+1 detection/charging, not only an fstat size gate.
    growth_tree = ArtifactTree(tmp_path / "growth")
    leaf = growth_tree.put(f"{TASKS}/probe.json", body)
    grown = [False]
    def grow_after_first_chunk(descriptor, amount):
        chunk = real_read(descriptor, amount)
        if chunk and not grown[0]:
            grown[0] = True
            with leaf.open("ab") as stream:
                stream.write(b"+")
        return chunk
    def growth_case():
        with evidence._evidence_files(growth_tree.root) as snapshot:
            with monkeypatch.context() as patcher:
                patcher.setattr(evidence.os, "read", grow_after_first_chunk)
                fixed_error("BYTE_LIMIT", lambda: snapshot._read("task", "probe"))
            assert grown[0] and snapshot._bytes_read == 160001
            fixed_error("CUSTODY_DRIFT", lambda: snapshot._read("task", "probe"))
            assert snapshot._bytes_read == 160001
    fixed_error("CUSTODY_DRIFT", growth_case)

    # A failure while constructing a directory plus a close fault preserves the first enum.
    with monkeypatch.context() as patcher:
        with descriptors(patcher) as (_, _, real_close, real_fstat):
            closed_once = [False]
            def failed_initial_stat(descriptor):
                raise OSError(PRIVATE_SENTINEL)
            def failed_initial_close(descriptor):
                real_close(descriptor)
                if not closed_once[0]:
                    closed_once[0] = True
                    raise OSError(PRIVATE_SENTINEL)
            patcher.setattr(evidence.os, "fstat", failed_initial_stat)
            patcher.setattr(evidence.os, "close", failed_initial_close)
            error = fixed_error("UNSAFE_PATH", lambda: read_once(tree.root, "task", "probe"))
            assert error.__notes__ == ["UNSAFE_FILE"]

    # Failure AFTER an actual successful read must close every retained descriptor too.
    with monkeypatch.context() as patcher:
        with descriptors(patcher):
            def failed_successful_exit():
                with evidence._evidence_files(tree.root) as snapshot:
                    assert snapshot._read("task", "probe") == body
                    tree.put(f"{TASKS}/probe.json", body + b"+")
            fixed_error("CUSTODY_DRIFT", failed_successful_exit)

    # Parse-time expiry is independently checked, even though raw byte custody succeeded.
    tree.put(METRICS, b'[1]')
    now = [0.0]
    real_loads = evidence.json.loads
    def expire_after_parse(*args, **kwargs):
        result = real_loads(*args, **kwargs)
        now[0] = 120.0
        return result
    def parse_timeout():
        with evidence._evidence_files(tree.root) as snapshot:
            snapshot._json_list("metrics")
    with monkeypatch.context() as patcher:
        patcher.setattr(evidence.time, "monotonic", lambda: now[0])
        patcher.setattr(evidence.json, "loads", expire_after_parse)
        with descriptors(patcher):
            fixed_error("TIME_LIMIT", parse_timeout)
