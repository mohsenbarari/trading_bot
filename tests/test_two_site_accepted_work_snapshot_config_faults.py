"""Actual disposable Git configuration and external-resolution regressions."""
from __future__ import annotations

import os
os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"
from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence
install_p1_00_network_fence()

import copy
import errno
import hashlib
import shlex
import signal
import subprocess
import sys
import time

import pytest

from scripts import two_site_accepted_work_snapshot as subject
from tests.test_two_site_accepted_work_snapshot import (
    GIT_ENV, PRIVATE, REFS, Repository, fixed_error, oid,
)


def missing_blob_commit(repo, path, object_id):
    assert "/" not in path
    tree = repo.run("mktree", "--missing", input=f"100644 blob {object_id}\t{path}\n".encode()).strip().decode()
    return repo.run("commit-tree", tree, "-p", repo.base, input=b"missing fixture blob\n").strip().decode()


def poisoned_lookup(repo, commit, path, code="GIT_FAILURE"):
    def normal_exit():
        with subject._proof_snapshot(repo.root) as snapshot:
            fixed_error(code, lambda: snapshot._git_blob(commit, path))
            fixed_error("CLOSED_SNAPSHOT", snapshot._metadata)
    fixed_error(code, normal_exit)


def test_actual_promisor_helper_would_execute_without_no_lazy_fetch_but_never_in_snapshot(tmp_path):
    repo = Repository(tmp_path / "promisor")
    marker, helper = tmp_path / "helper-called", tmp_path / "local-helper.sh"
    # Controlled ext helper has no network operation, no user configuration,
    # and writes only this temporary marker before returning a fixed failure.
    helper.write_text("#!/bin/sh\nprintf called > " + shlex.quote(str(marker)) + "\nexit 1\n")
    helper.chmod(0o700)
    assert not any(char.isspace() for char in str(helper))
    repo.run("config", "core.repositoryformatversion", "1")
    repo.run("config", "extensions.partialClone", "origin")
    repo.run("config", "remote.origin.promisor", "true")
    repo.run("config", "remote.origin.url", "ext::/bin/sh " + str(helper))
    # A per-protocol local allow would outrank protocol.allow=never; the
    # explicit no-lazy-fetch guard itself must prevent this local execution.
    repo.run("config", "protocol.ext.allow", "always")
    missing = oid("blob", b"fixture blob deliberately absent")
    commit = missing_blob_commit(repo, "missing.py", missing)
    control_env = {key: value for key, value in GIT_ENV.items() if key != "GIT_NO_LAZY_FETCH"}
    control = subprocess.run(
        ["/usr/bin/git", "--no-pager", "--no-replace-objects", "--literal-pathspecs",
         "-C", str(repo.root), "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false",
         "-c", "core.pager=cat", "-c", "protocol.allow=never", "cat-file", "-t", missing],
        env=control_env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        timeout=10, check=False,
    )
    assert control.returncode != 0 and marker.read_bytes() == b"called"
    marker.unlink()
    poisoned_lookup(repo, commit, "missing.py")
    assert not marker.exists()


def test_actual_graft_advice_control_and_fixed_suppression_leave_exact_blob_and_stderr_gate(tmp_path):
    repo = Repository(tmp_path / "graft-advice")
    prefix = ["/usr/bin/git", "--no-pager", "--no-replace-objects", "--literal-pathspecs",
              "-C", str(repo.root), "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false",
              "-c", "core.pager=cat", "-c", "protocol.allow=never"]
    query = ["ls-tree", "-z", repo.child, "--", "src/offer.py"]
    expected = b"100644 blob " + oid("blob", b"offer = 2\n").encode() + b"\tsrc/offer.py\0"
    results = []
    for extra in ([], ["-c", "advice.graftFileDeprecated=false"]):
        results.append(subprocess.run(prefix + extra + query, env=dict(GIT_ENV),
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=10, check=False))
    control, fixed = results
    assert control.returncode == fixed.returncode == 0
    assert control.stdout == fixed.stdout == expected
    assert b"advice.graftFileDeprecated" in control.stderr and fixed.stderr == b""
    assert GIT_ENV["GIT_GRAFT_FILE"] == "/dev/null"
    with subject._proof_snapshot(repo.root) as snapshot:
        assert snapshot._git_blob(repo.child, "src/offer.py")["data"] == b"offer = 2\n"
    # The pump's unchanged failed-stdout/stderr oracle separately rejects ANY
    # stderr. Only the fixed known Git advice is disabled, never accepted output.


def test_replace_refs_grafts_inherited_config_and_hooks_cannot_change_facts(tmp_path, monkeypatch):
    repo = Repository(tmp_path / "overrides")
    marker = tmp_path / "hook-called"
    helper = tmp_path / "forbidden-hook"
    helper.write_text("#!/bin/sh\nprintf called > " + shlex.quote(str(marker)) + "\nexit 1\n")
    helper.chmod(0o700)
    repo.run("replace", repo.base, repo.child)
    grafts = repo.root / ".git/info/grafts"
    grafts.parent.mkdir(exist_ok=True)
    grafts.write_text(repo.base + " " + repo.child + "\n")
    repo.run("config", "core.fsmonitor", str(helper))
    external_config = tmp_path / "global-config"
    external_config.write_text("[core]\n\thooksPath = " + str(helper) + "\n\tfsmonitor = " + str(helper) + "\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(external_config))
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", str(external_config))
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.fsmonitor")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", str(helper))
    with subject._proof_snapshot(repo.root) as snapshot:
        assert snapshot._git_commit(repo.base) == {"sha": repo.base, "parents": ()}
        assert snapshot._git_commit(repo.child) == {"sha": repo.child, "parents": (repo.base,)}
        assert snapshot._is_ancestor(repo.base, repo.child) is True
        assert snapshot._git_blob(repo.base, "src/offer.py")["data"] == b"offer = 1\n"
    assert not marker.exists()


def test_every_cached_structured_return_is_detached_from_private_storage(tmp_path):
    repo = Repository(tmp_path / "detached")
    with subject._proof_snapshot(repo.root) as snapshot:
        expected_commit = {"sha": repo.child, "parents": (repo.base,)}
        data = b"offer = 2\n"
        expected_blob = {"commit": repo.child, "path": "src/offer.py", "mode": "100644",
                         "git_oid": oid("blob", data), "sha256": hashlib.sha256(data).hexdigest(),
                         "data": data}
        commit = snapshot._git_commit(repo.child)
        blob = snapshot._git_blob(repo.child, "src/offer.py")
        changes = snapshot._git_changes(repo.child)
        expected_changes = copy.deepcopy(changes)
        commit.clear()
        blob.update({"data": PRIVATE.encode(), "path": PRIVATE})
        changes[0]["path"] = PRIVATE
        assert snapshot._git_commit(repo.child) == expected_commit
        assert snapshot._git_blob(repo.child, "src/offer.py") == expected_blob
        assert snapshot._git_changes(repo.child) == expected_changes


def test_actual_nonregular_type_changes_are_unknown_not_absence_or_fatal_git_failure(tmp_path):
    repo = Repository(tmp_path / "type-change")
    for mode in ("120000", "160000"):
        commit = repo.special(mode, "src/offer.py")
        with subject._proof_snapshot(repo.root) as snapshot:
            fixed_error("UNSUPPORTED_SOURCE", lambda: snapshot._git_blob(commit, "src/offer.py"))
            # Git reports actual regular→nonregular transitions as statusT,
            # not just an A/M/D row with a changed mode. Never silently filter.
            fixed_error("UNSUPPORTED_SOURCE", lambda: snapshot._git_changes(commit))
            assert snapshot._git_blob(repo.child, "src/offer.py")["data"] == b"offer = 2\n"


def test_actual_external_alternates_are_usable_without_guard_but_rejected_by_snapshot(tmp_path):
    repo = Repository(tmp_path / "owned")
    external = Repository(tmp_path / "outside")
    foreign_data = b"fixture raw blob belongs only to outside storage\n"
    foreign_oid = external.run("hash-object", "-w", "--stdin", input=foreign_data).strip().decode()
    assert foreign_oid == oid("blob", foreign_data)
    commit = missing_blob_commit(repo, "outside.py", foreign_oid)
    alternates = repo.root / ".git/objects/info/alternates"
    alternates.parent.mkdir(mode=0o700, exist_ok=True)
    external_path = str(external.root / ".git/objects").encode() + b"\n"
    alternates.write_bytes(external_path)
    alternates.chmod(0o600)
    # This is a real object-resolution fixture, not a fake stdout substitute.
    assert repo.run("cat-file", "blob", foreign_oid) == foreign_data
    for contents in (external_path, b""):
        alternates.write_bytes(contents)
        fixed_error("GIT_FAILURE", lambda: subject._proof_snapshot(repo.root).__enter__())
    alternates.unlink()
    def alternate_appears():
        with subject._proof_snapshot(repo.root) as snapshot:
            alternates.write_bytes(external_path)
            alternates.chmod(0o600)
            fixed_error("SNAPSHOT_DRIFT", snapshot._verify)
            fixed_error("CLOSED_SNAPSHOT", snapshot._metadata)
    try:
        fixed_error("SNAPSHOT_DRIFT", alternate_appears)
    finally:
        alternates.unlink()
    poisoned_lookup(repo, commit, "outside.py")


def test_repository_auxiliary_symlinks_cannot_redirect_alternate_custody(tmp_path):
    repo = Repository(tmp_path / "aux-links")
    alternates = repo.root / ".git/objects/info/alternates"
    outside = tmp_path / "outside-alternates"
    outside.write_text("/not-a-fixture-object-store\n")
    outside.chmod(0o600)
    alternates.symlink_to(outside)
    try:
        fixed_error("UNSAFE_FILE", lambda: subject._proof_snapshot(repo.root).__enter__())
    finally:
        alternates.unlink()
    info = alternates.parent
    assert not any(info.iterdir())
    info.rmdir()
    redirected = tmp_path / "redirected-info"
    redirected.mkdir(mode=0o700)
    (redirected / "alternates").write_bytes(b"")
    info.symlink_to(redirected, target_is_directory=True)
    try:
        fixed_error("UNSAFE_PATH", lambda: subject._proof_snapshot(repo.root).__enter__())
    finally:
        info.unlink()
        info.mkdir(mode=0o700)


def test_actual_named_root_rename_is_snapshot_drift_and_restoration_is_usable(tmp_path):
    repo = Repository(tmp_path / "named-root")
    moved = tmp_path / "renamed-root"
    def changed_name():
        with subject._proof_snapshot(repo.root) as snapshot:
            assert snapshot._git_commit(repo.child) == {"sha": repo.child, "parents": (repo.base,)}
            repo.root.rename(moved)
            fixed_error("SNAPSHOT_DRIFT", snapshot._verify)
            fixed_error("CLOSED_SNAPSHOT", snapshot._metadata)
    try:
        fixed_error("SNAPSHOT_DRIFT", changed_name)
    finally:
        if moved.exists():
            moved.rename(repo.root)
    with subject._proof_snapshot(repo.root) as snapshot:
        assert snapshot._git_commit(repo.child) == {"sha": repo.child, "parents": (repo.base,)}


def test_snapshot_body_exception_and_auxiliary_close_failure_preserve_body_and_release_every_fd(tmp_path, monkeypatch):
    repo = Repository(tmp_path / "body-and-close")
    original = RuntimeError(PRIVATE)
    actual_close, actual_fstat = os.close, os.fstat
    attempted, handles = [], set()
    with pytest.raises(RuntimeError) as caught:
        with monkeypatch.context() as patcher:
            with subject._proof_snapshot(repo.root) as snapshot:
                assert snapshot._read("task", "probe") == b"private-artifact"
                snapshot._git_commit(repo.child)
                handles = set(snapshot._files._handles)
                auxiliary = {record["fd"] for record in snapshot._files._aux_records.values()
                             if record["fd"] is not None}
                assert auxiliary and auxiliary <= handles
                failed_descriptor = min(auxiliary)
                def fail_one_auxiliary_close(descriptor):
                    attempted.append(descriptor)
                    actual_close(descriptor)
                    if descriptor == failed_descriptor:
                        raise OSError(errno.EIO, PRIVATE)
                patcher.setattr(subject.os, "close", fail_one_auxiliary_close)
                raise original
    assert caught.value is original and str(caught.value) == PRIVATE
    assert caught.value.__notes__ == ["UNSAFE_FILE"]
    assert set(attempted) == handles and len(attempted) == len(handles)
    for descriptor in handles:
        with pytest.raises(OSError):
            actual_fstat(descriptor)
    assert snapshot._state == "CLOSED" and snapshot._files._state == "CLOSED"
    assert not any((snapshot._objects, snapshot._lookups, snapshot._commits,
                    snapshot._files._aux_records, snapshot._files._handles, snapshot._files._dirs))


def test_snapshot_failed_owner_entry_preserves_primary_fixed_cleanup_note_and_closed_fds(tmp_path, monkeypatch):
    repo = Repository(tmp_path / "failed-owner-entry")
    context = subject._proof_snapshot(repo.root)
    actual_open, actual_close, actual_fstat = os.open, os.close, os.fstat
    opened, attempted = [], []
    def record_open(*args, **options):
        descriptor = actual_open(*args, **options)
        opened.append(descriptor)
        return descriptor
    def failed_initial_stat(descriptor):
        raise OSError(errno.EIO, PRIVATE)
    def failed_initial_close(descriptor):
        attempted.append(descriptor)
        actual_close(descriptor)
        raise OSError(errno.EIO, PRIVATE)
    with monkeypatch.context() as patcher:
        patcher.setattr(subject.os, "open", record_open)
        patcher.setattr(subject.os, "fstat", failed_initial_stat)
        patcher.setattr(subject.os, "close", failed_initial_close)
        error = fixed_error("UNSAFE_PATH", context.__enter__)
    assert error.__notes__ == ["UNSAFE_FILE"]
    assert opened and attempted == opened
    assert context._state == "CLOSED" and context._files._state == "CLOSED"
    assert not context._files._handles
    for descriptor in opened:
        with pytest.raises(OSError):
            actual_fstat(descriptor)


@pytest.mark.parametrize("wait_fault", ["none", "first", "permanent"])
def test_expired_action_uses_one_bounded_cleanup_grace_and_honestly_records_reap(tmp_path, monkeypatch, wait_fault):
    repo = Repository(tmp_path / "cleanup-grace")
    actual_start, actual_wait = subprocess.Popen, subprocess.Popen.wait
    actual_waitid, actual_killpg = os.waitid, os.killpg
    children, waits, outer_errors = [], [], []
    def body():
        with subject._proof_snapshot(repo.root) as snapshot:
            owner_deadline, initial_bytes = snapshot._files._deadline, snapshot._files._bytes_read
            clock = [time.monotonic()]
            class ExpirePoll:
                def register(self, *args):
                    pass
                def unregister(self, *args):
                    pass
                def poll(self, milliseconds):
                    clock[0] = owner_deadline + 1
                    return []
            def start(argv, **options):
                child = actual_start([sys.executable, "-I", "-c", "import time; time.sleep(60)"], **options)
                children.append(child)
                def wait(timeout=None):
                    waits.append(timeout)
                    assert 0.001 < timeout <= 2
                    if wait_fault == "permanent" or wait_fault == "first" and len(waits) == 1:
                        # Faithful fault: do NOT reap and then pretend timeout.
                        clock[0] += 0.25
                        raise subprocess.TimeoutExpired("fixture-local-child", timeout)
                    return actual_wait(child, timeout=timeout)
                child.wait = wait
                return child
            with monkeypatch.context() as patcher:
                patcher.setattr(subject.subprocess, "Popen", start)
                patcher.setattr(subject.select, "poll", ExpirePoll)
                patcher.setattr(subject.time, "monotonic", lambda: clock[0])
                error = fixed_error("TIME_LIMIT", lambda: snapshot._capture(("rev-parse", "--show-object-format")))
                outer_errors.append(error)
            assert snapshot._files._deadline == owner_deadline
            assert snapshot._files._bytes_read == initial_bytes
            assert len(children) == 1 and snapshot._commands == 7
            assert len(waits) == (1 if wait_fault == "none" else 2)
            if len(waits) == 2:
                assert waits[1] < waits[0], "retry must share the SAME cleanup deadline"
            child = children[0]
            assert child.stdout.closed and child.stderr.closed
            if wait_fault == "permanent":
                assert child.returncode is None, "do not claim production reaped after permanent wait fault"
                observation = actual_waitid(os.P_PID, child.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
                until = time.monotonic() + 1
                while observation is None and time.monotonic() < until:
                    time.sleep(0.005)
                    observation = actual_waitid(os.P_PID, child.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
                assert observation is not None and observation.si_pid == child.pid
            else:
                assert child.returncode is not None
            fixed_error("CLOSED_SNAPSHOT", snapshot._metadata)
    try:
        final = fixed_error("TIME_LIMIT", body)
        if wait_fault != "none":
            assert outer_errors[0].__notes__ == ["GIT_FAILURE"]
            assert final.__notes__ == ["GIT_FAILURE"]
    finally:
        # Fixture cleanup is explicitly NOT production-reap evidence.
        for child in children:
            if child.returncode is None:
                try:
                    actual_killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                actual_wait(child, timeout=2)


@pytest.mark.parametrize("primary", ["read", "time"])
def test_nested_translation_and_normal_poison_exit_preserve_fixed_cleanup_notes(tmp_path, monkeypatch, primary):
    repo = Repository(tmp_path / "translated-notes")
    actual_start, actual_read = subprocess.Popen, os.read
    children, initial_errors = [], []
    expected = "GIT_FAILURE" if primary == "read" else "TIME_LIMIT"
    def body():
        with subject._proof_snapshot(repo.root) as snapshot:
            clock = [time.monotonic()]
            class BadClose:
                def __init__(self, stream):
                    self.stream = stream
                def fileno(self):
                    return self.stream.fileno()
                @property
                def closed(self):
                    return self.stream.closed
                def close(self):
                    self.stream.close()
                    raise OSError(errno.EIO, PRIVATE)
            def start(argv, **options):
                child = actual_start([sys.executable, "-I", "-c", "import os; os.write(1,b'chunk')"], **options)
                child.stdout = BadClose(child.stdout)
                children.append(child)
                return child
            def rejected_read(fd, count):
                if children and fd in (children[0].stdout.fileno(), children[0].stderr.fileno()):
                    if primary == "read":
                        raise OSError(errno.EIO, PRIVATE)
                    result = actual_read(fd, count)
                    if result:
                        clock[0] = snapshot._files._deadline
                    return result
                return actual_read(fd, count)
            with monkeypatch.context() as patcher:
                patcher.setattr(subject.subprocess, "Popen", start)
                patcher.setattr(subject.os, "read", rejected_read)
                patcher.setattr(subject.time, "monotonic", lambda: clock[0])
                # This models public Git operation -> capture -> owner/pump,
                # not only a direct invocation of one translating layer.
                error = fixed_error(expected, lambda: snapshot._invoke(
                    snapshot._capture, ("rev-parse", "--show-object-format")))
                assert error.__notes__ == ["GIT_FAILURE"]
                initial_errors.append(error)
            fixed_error("CLOSED_SNAPSHOT", snapshot._metadata)
    final = fixed_error(expected, body)
    assert final.__notes__ == ["GIT_FAILURE"]
    assert final is not initial_errors[0]
    assert children and all(child.returncode is not None and child.stdout.closed and child.stderr.closed
                            for child in children)


def test_metadata_hash_expiry_is_checked_after_computation_before_any_return(tmp_path, monkeypatch):
    repo = Repository(tmp_path / "metadata-expiry")
    real_hash = hashlib.sha256
    computed = []
    def body():
        with subject._proof_snapshot(repo.root) as snapshot:
            snapshot._git_commit(repo.child)
            clock = [time.monotonic()]
            def expire_after_hash(data):
                value = real_hash(data)
                computed.append(bytes(data))
                clock[0] = snapshot._files._deadline
                return value
            with monkeypatch.context() as patcher:
                patcher.setattr(subject.time, "monotonic", lambda: clock[0])
                patcher.setattr(subject.hashlib, "sha256", expire_after_hash)
                fixed_error("TIME_LIMIT", snapshot._metadata)
            fixed_error("CLOSED_SNAPSHOT", snapshot._metadata)
    fixed_error("TIME_LIMIT", body)
    assert computed == [repo.commits[repo.child]]
