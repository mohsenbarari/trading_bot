"""Bounded process, shared accounting and fail-closed snapshot fault oracles."""
from __future__ import annotations

import os
os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"
from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence
install_p1_00_network_fence()

from contextlib import contextmanager
import errno
import json
from pathlib import Path
import select
import signal
import subprocess
import sys
import time

import pytest

from scripts import two_site_accepted_work_snapshot as subject
from tests.test_two_site_accepted_work_snapshot import (
    GIT_ENV, METRICS, PRIVATE, REFS, TASKS, Repository, fixed_error, oid, sha256,
)


def poisoned(root, operation, code):
    """Caught inner failures remain failed and are re-raised by normal exit."""
    def body():
        with subject._proof_snapshot(root) as snapshot:
            fixed_error(code, lambda: operation(snapshot))
            fixed_error("CLOSED_SNAPSHOT", snapshot._metadata)
            fixed_error("CLOSED_SNAPSHOT", lambda: snapshot._read("metrics"))
    fixed_error(code, body)


def capture(snapshot):
    # Exercise the real pump under the SAME production public-operation error
    # boundary; the internal pump alone is deliberately not a second API.
    return snapshot._invoke(snapshot._capture, ("rev-parse", "--show-object-format"))


@contextmanager
def scripted_child(patcher, script):
    """Replace only Git's child process, not the production pump or cleanup."""
    actual = subprocess.Popen
    children = []
    def start(argv, **options):
        assert argv[0] == "/usr/bin/git"
        assert options["env"] == GIT_ENV and options["start_new_session"] is True
        child = actual([sys.executable, "-I", "-c", script], **options)
        children.append(child)
        return child
    with patcher.context() as child_patch:
        child_patch.setattr(subject.subprocess, "Popen", start)
        try:
            yield children
        finally:
            for child in children:
                assert child.returncode is not None
                assert child.stdout.closed and child.stderr.closed
    assert subprocess.Popen is actual


def test_real_failed_stdout_stderr_nonzero_and_spawn_failures_charge_and_poison(tmp_path, monkeypatch):
    repo = Repository(tmp_path / "failed")
    cases = [
        ("import os; os.write(1,b'visible'); os.write(2,b'private');", 14, "GIT_FAILURE"),
        ("import os; os.write(1,b'partial'); os._exit(7)", 7, "GIT_FAILURE"),
        ("import os; os.write(2,b'" + PRIVATE + "'); os._exit(1)", len(PRIVATE), "GIT_FAILURE"),
    ]
    for script, charged, code in cases:
        def operation(snapshot):
            before = snapshot._files._bytes_read
            with monkeypatch.context() as patcher:
                with scripted_child(patcher, script) as children:
                    error = fixed_error(code, lambda: capture(snapshot))
                assert len(children) == 1
            assert snapshot._files._bytes_read == before + charged
            fixed_error("CLOSED_SNAPSHOT", snapshot._metadata)
            raise error
        poisoned(repo.root, operation, code)
    def cannot_start(snapshot):
        before = snapshot._files._bytes_read
        def denied(*args, **kwargs):
            raise OSError(errno.EACCES, PRIVATE)
        with monkeypatch.context() as patcher:
            patcher.setattr(subject.subprocess, "Popen", denied)
            error = fixed_error("GIT_FAILURE", lambda: capture(snapshot))
        assert snapshot._files._bytes_read == before
        raise error
    poisoned(repo.root, cannot_start, "GIT_FAILURE")


def test_query_exact_cap_hup_eof_and_overflow_without_read_plus_one(tmp_path, monkeypatch):
    repo = Repository(tmp_path / "query-cap")
    exact = "import os; data=b'x'*65536; [os.write(1,data) for _ in range(30)]; os.write(1,b'x'*33920)"
    # 30*65536 +33920 ==2,000,000; the cap is never weakened in the Subject.
    assert 30 * 65536 + 33920 == 2_000_000
    for overflow in (False, True):
        def operation(snapshot):
            before = snapshot._files._bytes_read
            calls = []
            data_fds, poll_events = set(), []
            actual_read = os.read
            actual_poll = select.poll
            def counted(fd, count):
                assert 0 < count <= 65536
                result = actual_read(fd, count)
                calls.append((count, len(result)))
                if result:
                    data_fds.add(fd)
                return result
            class PollProbe:
                def __init__(self):
                    self.actual = actual_poll()
                def register(self, *args):
                    return self.actual.register(*args)
                def unregister(self, *args):
                    return self.actual.unregister(*args)
                def poll(self, milliseconds):
                    events = self.actual.poll(milliseconds)
                    poll_events.append((sum(size for _, size in calls), events))
                    return events
            with monkeypatch.context() as patcher:
                patcher.setattr(subject.os, "read", counted)
                patcher.setattr(subject.select, "poll", PollProbe)
                script = exact + ("; os.write(1,b'y')" if overflow else "")
                with scripted_child(patcher, script):
                    if overflow:
                        error = fixed_error("BYTE_LIMIT", lambda: capture(snapshot))
                    else:
                        rc, output = capture(snapshot)
                        assert rc == 0 and output == b"x" * 2_000_000
            assert sum(size for _, size in calls) == 2_000_000
            assert snapshot._files._bytes_read == before + 2_000_000
            # An extra one-byte EOF probe would be visible both here and in the total.
            assert all(requested <= 2_000_000 - sum(size for _, size in calls[:index])
                       for index, (requested, _) in enumerate(calls))
            boundary_events = [flags for charged, events in poll_events if charged == 2_000_000
                               for fd, flags in events if fd in data_fds]
            if overflow:
                assert any(flags & select.POLLIN for flags in boundary_events)
            else:
                assert any(flags & select.POLLHUP and not flags & select.POLLIN for flags in boundary_events)
            if overflow:
                raise error
        if overflow:
            poisoned(repo.root, operation, "BYTE_LIMIT")
        else:
            with subject._proof_snapshot(repo.root) as snapshot:
                operation(snapshot)


@pytest.mark.parametrize("overflow", [False, True])
def test_same_owner_file_plus_failed_git_bytes_and_shared_global_limit(tmp_path, monkeypatch, overflow):
    repo = Repository(tmp_path / "global-cap")
    repo.put(METRICS, b"a" * 2_000_000)
    def operation(snapshot):
        assert snapshot._read("metrics") == b"a" * 2_000_000
        initial = snapshot._files._bytes_read
        assert initial >= 2_000_000
        # Use the complete real remaining budget, not another cap or a reset.
        remaining = 8_000_000 - initial
        actual_read = os.read
        reads = []
        def counted(fd, count):
            result = actual_read(fd, count)
            reads.append((count, len(result)))
            return result
        with monkeypatch.context() as patcher:
            patcher.setattr(subject.os, "read", counted)
            # Each query still has its own2M cap. Three independently drained
            # queries reach the shared budget; the last adds readable overflow.
            for amount in (2_000_000, 2_000_000):
                child = f"import os; left={amount}\nwhile left:\n n=min(left,65536); os.write(1,b'x'*n); left-=n"
                with scripted_child(patcher, child):
                    assert capture(snapshot)[1] == b"x" * amount
            final = 8_000_000 - snapshot._files._bytes_read
            script = f"import os; left={final + int(overflow)}\nwhile left:\n n=min(left,65536); os.write(1,b'x'*n); left-=n"
            with scripted_child(patcher, script):
                if overflow:
                    error = fixed_error("BYTE_LIMIT", lambda: capture(snapshot))
                else:
                    assert capture(snapshot) == (0, b"x" * final)
            if not overflow:
                # ExactEOF is valid, but mandatory final identity verification
                # still needs this SAME budget; exhaustion is not waived.
                error = fixed_error("BYTE_LIMIT", snapshot._verify)
        assert snapshot._files._bytes_read == 8_000_000
        assert sum(count for _, count in reads) == remaining
        fixed_error("CLOSED_SNAPSHOT", lambda: snapshot._git_commit(repo.child))
        raise error
    poisoned(repo.root, operation, "BYTE_LIMIT")


def test_cleanup_keeps_leader_unreaped_until_group_kill_and_preserves_body(tmp_path, monkeypatch):
    repo = Repository(tmp_path / "session")
    script = ("import os,time\nchild=os.fork()\nif child==0:\n"
              " os.close(1); os.close(2); time.sleep(60); os._exit(0)\n"
              "os.write(1,str(child).encode()); os._exit(0)")
    real_waitid, real_killpg = os.waitid, os.killpg
    events = []
    def observe(which, pid, flags):
        assert flags & os.WNOWAIT
        result = real_waitid(which, pid, flags)
        events.append(("observe", pid, result))
        return result
    def kill_group(pid, number):
        # WNOWAIT proves the child leader's PID/PGID cannot yet have been reused.
        result = real_waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
        assert result is not None and result.si_pid == pid
        events.append(("kill", pid, number))
        return real_killpg(pid, number)
    with subject._proof_snapshot(repo.root) as snapshot:
        with monkeypatch.context() as patcher:
            patcher.setattr(subject.os, "waitid", observe)
            patcher.setattr(subject.os, "killpg", kill_group)
            def forbidden_poll(*args, **kwargs):
                raise AssertionError("leader was polled/reaped before group cleanup")
            patcher.setattr(subprocess.Popen, "poll", forbidden_poll)
            with scripted_child(patcher, script) as children:
                rc, output = capture(snapshot)
                descendant = int(output)
                assert rc == 0 and len(children) == 1
            assert any(event[0] == "observe" and event[2] is not None for event in events)
            assert [event[2] for event in events if event[0] == "kill"] == [signal.SIGTERM, signal.SIGKILL]
            # A killed orphan may await init's reaper, but it cannot still execute.
            status = Path(f"/proc/{descendant}/stat")
            until = time.monotonic() + 1
            while status.exists() and status.read_text().split(") ", 1)[1].split()[0] != "Z" and time.monotonic() < until:
                time.sleep(0.005)
            if status.exists():
                assert status.read_text().split(") ", 1)[1].split()[0] == "Z"
    sentinel = ValueError(PRIVATE)
    with pytest.raises(ValueError) as caught:
        with subject._proof_snapshot(repo.root) as snapshot:
            with monkeypatch.context() as patcher:
                with scripted_child(patcher, "import os; os.write(2,b'bad')"):
                    fixed_error("GIT_FAILURE", lambda: capture(snapshot))
            raise sentinel
    assert caught.value is sentinel and str(caught.value) == PRIVATE


def test_real_command_512_boundary_and_independent_object_256_unit_guard(tmp_path, monkeypatch):
    repo = Repository(tmp_path / "counts")
    def command_boundary(snapshot):
        assert snapshot._commands == 6
        for _ in range(506):
            assert capture(snapshot) == (0, b"sha1\n")
        assert snapshot._commands == 512
        error = fixed_error("COUNT_LIMIT", lambda: capture(snapshot))
        assert snapshot._commands == 512
        raise error
    poisoned(repo.root, command_boundary, "COUNT_LIMIT")

    # 256objects need >512 real queries. This explicitly isolates the object
    # guard using valid independently hashed raw commits; it is NOT a claim
    # that the production pump can qualify256 new objects within512 commands.
    original = subject._ProofSnapshot._capture
    raw_objects = {}
    for index in range(257):
        raw = repo.commits[repo.base] + f"unit-{index}\n".encode()
        raw_objects[oid("commit", raw)] = raw
    assert len(raw_objects) == 257
    queried = []
    def object_capture(instance, arguments, allowed_returns=(0,)):
        if arguments[0] != "cat-file":
            return original(instance, arguments, allowed_returns)
        command, kind, key = arguments
        assert command == "cat-file" and key in raw_objects
        data = raw_objects[key]
        assert oid("commit", data) == key
        queried.append((kind, key))
        if kind == "-t":
            return 0, b"commit\n"
        if kind == "-s":
            return 0, str(len(data)).encode() + b"\n"
        assert kind == "commit"
        return 0, data
    def object_boundary(snapshot):
        with monkeypatch.context() as patcher:
            patcher.setattr(subject._ProofSnapshot, "_capture", object_capture)
            for key in list(raw_objects)[:256]:
                assert snapshot._git_commit(key) == {"sha": key, "parents": ()}
            assert len(snapshot._objects) == 256
            calls = len(queried)
            assert snapshot._git_commit(next(iter(raw_objects))) == {"sha": next(iter(raw_objects)), "parents": ()}
            assert len(queried) == calls
            error = fixed_error("COUNT_LIMIT", lambda: snapshot._git_commit(list(raw_objects)[256]))
            assert len(snapshot._objects) == 256
        raise error
    poisoned(repo.root, object_boundary, "COUNT_LIMIT")


def test_shared_metadata_65536_exact_guard_after_real_file_and_git_charges(tmp_path):
    repo = Repository(tmp_path / "metadata")
    def boundary(snapshot):
        owner = snapshot._files
        before = owner._metadata_bytes
        assert snapshot._read("task", "probe") == b"private-artifact"
        path = f"{TASKS}/probe.json"
        file_charge = len(path.encode()) + len(json.dumps("probe", separators=(",", ":")).encode())
        assert owner._metadata_bytes == before + file_charge
        before_git = owner._metadata_bytes
        assert snapshot._git_blob(repo.child, "src/offer.py")["data"] == b"offer = 2\n"
        # Factory already observed commit/parentSHA; this lookup observes its
        # treeSHA, blobOID and (commit,path), all in ONE64K allowance.
        assert owner._metadata_bytes == before_git + 40 + 40 + 40 + len("src/offer.py")
        cached = owner._metadata_bytes
        snapshot._git_blob(repo.child, "src/offer.py")
        snapshot._read("task", "probe")
        assert owner._metadata_bytes == cached
        # Isolate the exact integer boundary only after demonstrating both real
        # consumers charge this SAME owner. No cap is altered or waived.
        owner._charge_metadata(("unit-exact-boundary",), 65536 - cached)
        assert owner._metadata_bytes == 65536
        owner._charge_metadata(("unit-exact-boundary",), 1)
        assert owner._metadata_bytes == 65536
        error = fixed_error("COUNT_LIMIT", lambda: snapshot._git_blob(repo.child, "src/next.py"))
        assert owner._metadata_bytes == 65536
        raise error
    poisoned(repo.root, boundary, "COUNT_LIMIT")


def test_query_timeout_and_read_then_deadline_failure_still_charge_chunk(tmp_path, monkeypatch):
    repo = Repository(tmp_path / "clock")
    def timeout(snapshot):
        clock = [time.monotonic()]
        class TimeoutPoll:
            def register(self, *args):
                pass
            def unregister(self, *args):
                pass
            def poll(self, milliseconds):
                assert 0 <= milliseconds <= 30000
                clock[0] += 29
                return []
        with monkeypatch.context() as patcher:
            patcher.setattr(subject.time, "monotonic", lambda: clock[0])
            patcher.setattr(subject.select, "poll", TimeoutPoll)
            with scripted_child(patcher, "import time; time.sleep(60)"):
                error = fixed_error("TIME_LIMIT", lambda: capture(snapshot))
        raise error
    poisoned(repo.root, timeout, "TIME_LIMIT")
    def late_chunk(snapshot):
        before = snapshot._files._bytes_read
        clock = [time.monotonic()]
        actual_read = os.read
        returned = []
        def after_read(fd, amount):
            data = actual_read(fd, amount)
            if data:
                returned.append(data)
                clock[0] = snapshot._files._deadline
            return data
        with monkeypatch.context() as patcher:
            patcher.setattr(subject.time, "monotonic", lambda: clock[0])
            patcher.setattr(subject.os, "read", after_read)
            with scripted_child(patcher, "import os; os.write(1,b'late-chunk')"):
                error = fixed_error("TIME_LIMIT", lambda: capture(snapshot))
        assert returned == [b"late-chunk"]
        assert snapshot._files._bytes_read == before + len(b"late-chunk")
        raise error
    poisoned(repo.root, late_chunk, "TIME_LIMIT")


def test_read_poll_wait_and_stream_cleanup_faults_close_all_owned_resources(tmp_path, monkeypatch):
    repo = Repository(tmp_path / "io-faults")
    real_read = os.read
    for fault in ("read", "poll", "waitid", "stream-close"):
        def operation(snapshot):
            with monkeypatch.context() as patcher:
                with scripted_child(patcher, "import os; os.write(1,b'chunk')") as children:
                    with patcher.context() as fault_patch:
                        if fault == "read":
                            def rejected_read(fd, count):
                                # Do not break Popen's own exec-error pipe read.
                                if children and fd in (children[0].stdout.fileno(), children[0].stderr.fileno()):
                                    raise OSError(errno.EIO, PRIVATE)
                                return real_read(fd, count)
                            fault_patch.setattr(subject.os, "read", rejected_read)
                        elif fault == "poll":
                            class PollFailure:
                                def register(self, *args):
                                    pass
                                def unregister(self, *args):
                                    pass
                                def poll(self, milliseconds):
                                    raise OSError(errno.EIO, PRIVATE)
                            fault_patch.setattr(subject.select, "poll", PollFailure)
                        elif fault == "waitid":
                            def rejected_wait(*args):
                                raise OSError(errno.EIO, PRIVATE)
                            fault_patch.setattr(subject.os, "waitid", rejected_wait)
                        else:
                            actual_start = subject.subprocess.Popen
                            class CloseFailure:
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
                                child = actual_start(argv, **options)
                                child.stdout = CloseFailure(child.stdout)
                                return child
                            fault_patch.setattr(subject.subprocess, "Popen", start)
                        error = fixed_error("GIT_FAILURE", lambda: capture(snapshot))
                assert children
            raise error
        poisoned(repo.root, operation, "GIT_FAILURE")

    # The evidence owner must release repository fds on normal and failed entry.
    opened = []
    actual_open, actual_fstat = os.open, os.fstat
    def record_open(*args, **options):
        descriptor = actual_open(*args, **options)
        opened.append(descriptor)
        return descriptor
    with monkeypatch.context() as patcher:
        patcher.setattr(subject.os, "open", record_open)
        with subject._proof_snapshot(repo.root) as snapshot:
            assert snapshot._read("task", "probe") == b"private-artifact"
        repo.run("update-ref", "-d", REFS[0])
        fixed_error("GIT_FAILURE", lambda: subject._proof_snapshot(repo.root).__enter__())
    assert opened
    for descriptor in set(opened):
        with pytest.raises(OSError):
            actual_fstat(descriptor)
    repo.run("update-ref", REFS[0], repo.base)


def test_disallowed_private_capture_operations_never_spawn_and_do_not_change_caps(tmp_path, monkeypatch):
    repo = Repository(tmp_path / "fixed-table")
    with subject._proof_snapshot(repo.root) as snapshot:
        commands = snapshot._commands
        def no_spawn(*args, **kwargs):
            raise AssertionError("non-fixed command spawned")
        with monkeypatch.context() as patcher:
            patcher.setattr(subject.subprocess, "Popen", no_spawn)
            for args in (("status",), ("show", "HEAD"), ("cat-file", "blob", "HEAD"),
                         ("config", "--list"), ("fetch",), ("rev-parse", "--verify", "refs/heads/other"),
                         ("ls-tree", "-z", repo.child, "--", "../secrets.json")):
                expected = "UNSAFE_PATH" if args[0] == "ls-tree" else "INVALID_KEY"
                fixed_error(expected, lambda a=args: snapshot._capture(a))
            fixed_error("INVALID_KEY", lambda: snapshot._capture(("rev-parse", "--show-object-format"), (0, 1)))
        assert snapshot._commands == commands
        assert subject._MAX_GIT_COMMANDS == 512 and subject._MAX_GIT_OBJECTS == 256
        assert subject._QUERY_BYTES == 2_000_000 and subject._CHILD_SECONDS == 30


def test_actual_git_object_parser_parent32_boundary_and_malformed_headers(tmp_path):
    repo = Repository(tmp_path / "commit-parser")
    tree_line = repo.commits[repo.base].split(b"\n", 1)[0]
    parents = tuple(repo.base if index % 2 == 0 else repo.side for index in range(32))
    raw32 = tree_line + b"\n" + b"".join(b"parent " + parent.encode() + b"\n" for parent in parents)
    raw32 += b"author Fixture <fixture@example.invalid> 1 +0000\ncommitter Fixture <fixture@example.invalid> 1 +0000\n\nmessage\n"
    key32 = repo.run("hash-object", "-t", "commit", "--literally", "-w", "--stdin", input=raw32).strip().decode()
    assert key32 == oid("commit", raw32)
    with subject._proof_snapshot(repo.root) as snapshot:
        assert snapshot._git_commit(key32) == {"sha": key32, "parents": parents}
    invalid = [b"tree not-an-oid\n\nmessage\n", tree_line + b"\nparent invalid\n\nmessage\n",
               raw32.replace(b"author ", b"parent " + repo.base.encode() + b"\nauthor ", 1),
               raw32.replace(b"\n\nmessage\n", b"\nmessage\n")]
    for raw in invalid:
        key = repo.run("hash-object", "-t", "commit", "--literally", "-w", "--stdin", input=raw).strip().decode()
        assert key == oid("commit", raw)
        def invalid_object():
            with subject._proof_snapshot(repo.root) as snapshot:
                fixed_error("GIT_FAILURE", lambda: snapshot._git_commit(key))
                fixed_error("CLOSED_SNAPSHOT", snapshot._metadata)
        fixed_error("GIT_FAILURE", invalid_object)
