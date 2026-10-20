"""Hermetic lifecycle tests; synthetic frames are NOT Provider/acceptance proof.

Only the fixed Python children below run, with empty HOME and no inherited auth.
The pure callback deliberately accepts one literal test stream; production frame
schema/CID/model/tool validation is exercised by the Runner integration suites.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
import selectors
import signal
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from scripts import two_site_generation_completion as subject


CID = "00000000-0000-4000-8000-000000000752"
MODEL = "hermetic-model"
INPUT = b"synthetic owned input\n"
INIT = {"event": "init", "conversation_id": CID, "model": MODEL}
FINAL = {
    "event": "result", "conversation_id": CID, "model": MODEL,
    "status": "success", "num_turns": 1, "tool_calls": 0,
    "result": '{"fixture":true}',
}


def _wire(*frames):
    return b"".join(json.dumps(frame, separators=(",", ":")).encode() + b"\n"
                    for frame in frames)


FRAMES = _wire(INIT, FINAL)


def _frame_check(stdout, stderr):
    """A strict literal consumer, not a replacement production frame parser."""
    return stdout == FRAMES and stderr == b""


class CaptureError(ValueError):
    pass


def _terminate_direct(process):
    """Only this test's direct child; group cleanup belongs to the SUT policy."""
    if process.poll() is None:
        process.kill()
    process.wait(timeout=2)


@contextmanager
def _child(tmp_path, body, *, stdout=subprocess.PIPE):
    home = tmp_path / "empty-home"
    home.mkdir(exist_ok=True)
    process = subprocess.Popen(
        [sys.executable, "-I", "-B", "-c", body],
        cwd=tmp_path,
        env={"PATH": os.defpath, "HOME": str(home), "LANG": "C", "LC_ALL": "C"},
        stdin=subprocess.PIPE, stdout=stdout, stderr=subprocess.PIPE,
        start_new_session=True, close_fds=True,
    )
    try:
        yield process
    finally:
        # Even a failed assertion cannot leave one of the fixed children alive.
        _terminate_direct(process)
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None and not stream.closed:
                stream.close()


def _capture(process, tmp_path, policy, *, data=INPUT, timeout=2.0, cap=4096):
    return subject.capture_generation_process(
        process, data, tmp_path / "stdout.bin", tmp_path / "stderr.bin",
        timeout, cap, policy, error_type=CaptureError,
        terminate=_terminate_direct, stream_chunk_size=13,
    )


def _retained(tmp_path):
    return ((tmp_path / "stdout.bin").read_bytes(),
            (tmp_path / "stderr.bin").read_bytes())


def _emit_after_eof(*, exit_code=0, stay_alive=False):
    return (
        "import os,sys,time\n"
        "sys.stdin.buffer.read()\n"
        f"os.write(1, {FRAMES!r})\n"
        + ("time.sleep(60)\n" if stay_alive else f"sys.exit({exit_code})\n")
    )


def test_natural_exit0_retains_bytes_and_has_no_recovery_claim(tmp_path):
    policy = subject.GenerationRecoveryPolicy(_frame_check)
    with _child(tmp_path, _emit_after_eof()) as process:
        assert _capture(process, tmp_path, policy) is None
        assert process.returncode == 0
    stdout, stderr = _retained(tmp_path)
    assert (stdout, stderr) == (FRAMES, b"")
    assert policy.failure_facts is None
    normal = subject.normal_observations(0, INPUT, stdout, stderr, 2.0)
    checked = subject.validate_observations(normal, INPUT, stdout, stderr)
    assert checked == normal and checked is not normal
    assert checked["outcome"] == "NORMAL_EXIT"
    assert checked["normal_exit_observed"] is True
    assert checked["owned_group_absent"] is None
    assert checked["post_final_grace_seconds"] is None


@pytest.fixture(scope="module")
def recovered_capture(tmp_path_factory):
    """One real five-second grace; never monkeypatch the production grace."""
    tmp_path = tmp_path_factory.mktemp("owned-final-frame-recovery")
    policy = subject.GenerationRecoveryPolicy(_frame_check)
    with _child(tmp_path, _emit_after_eof(stay_alive=True)) as process:
        started = time.monotonic()
        result = _capture(process, tmp_path, policy, timeout=10.0)
        assert time.monotonic() - started >= 5.0
        assert process.returncode == -signal.SIGKILL
        assert process.poll() == -signal.SIGKILL
        with pytest.raises(ProcessLookupError):
            os.killpg(process.pid, 0)
    stdout, stderr = _retained(tmp_path)
    assert (stdout, stderr) == (FRAMES, b"")
    assert result["post_final_grace_seconds"] == 5.0
    assert subject.POST_FINAL_GRACE_SECONDS == 5.0
    assert policy._terminal_read_at >= policy._closed_at
    assert policy.failure_facts is None
    return result, stdout, stderr


def test_real_owned_child_recovery_is_not_mislabelled_natural_exit(recovered_capture):
    result, stdout, stderr = recovered_capture
    checked = subject.validate_observations(result, INPUT, stdout, stderr)
    assert checked["outcome"] == "RECOVERED_COMPLETED_RESULT"
    assert checked["normal_exit_observed"] is False
    assert checked["actual_returncode"] == -signal.SIGKILL
    assert checked["timeout_kind"] == "POST_FINAL_SHUTDOWN"
    for field in ("signal_attempted", "shutdown_timeout_observed",
                  "direct_child_reaped", "owned_group_absent", "stdout_eof",
                  "stderr_eof", "full_input_written", "input_writer_closed",
                  "observed_close_before_terminal_read", "lifecycle_within_capture_deadline"):
        assert checked[field] is True


@pytest.mark.parametrize("field,value", [
    ("normal_exit_observed", True), ("actual_returncode", 0),
    ("actual_returncode", False), ("cleanup_signal", True),
    ("post_final_grace_seconds", 5), ("post_final_grace_seconds", 0.0),
    ("owned_group_absent", None), ("observed_close_before_terminal_read", False),
    ("stdout_eof", False), ("full_input_written", 1),
    ("input_sha256", "0" * 64), ("outcome", "UNKNOWN"),
])
def test_retained_recovery_rejects_typed_or_digest_tampering(recovered_capture, field, value):
    result, stdout, stderr = recovered_capture
    bad = {**result, field: value}
    with pytest.raises(subject.GenerationCompletionError):
        subject.validate_observations(bad, INPUT, stdout, stderr)


def test_retained_observations_reject_unknown_keys_and_changed_streams(recovered_capture):
    result, stdout, stderr = recovered_capture
    with pytest.raises(subject.GenerationCompletionError, match="unexpected keys"):
        subject.validate_observations({**result, "untrusted": True}, INPUT, stdout, stderr)
    for data, out, err in ((INPUT + b"x", stdout, stderr),
                           (INPUT, stdout + b"x", stderr), (INPUT, stdout, b"x")):
        with pytest.raises(subject.GenerationCompletionError, match="retained bytes mismatch"):
            subject.validate_observations(result, data, out, err)


@pytest.mark.parametrize("timeout", [0, -1, 916, 10 ** 1000, True, float("nan"), float("inf"), "1"])
def test_observation_timeout_domain_is_closed(timeout):
    good = subject.normal_observations(0, INPUT, FRAMES, b"", 1.0)
    with pytest.raises(subject.GenerationCompletionError, match="timeout is invalid"):
        subject.validate_observations({**good, "overall_timeout_seconds": timeout}, INPUT, FRAMES, b"")


@pytest.mark.parametrize("returncode", [1, -signal.SIGKILL, True, None])
def test_normal_facts_require_actual_typed_exit0(returncode):
    with pytest.raises(subject.GenerationCompletionError, match="actual exit0"):
        subject.normal_observations(returncode, INPUT, FRAMES, b"", 1.0)


def test_natural_nonzero_is_not_rescued_by_a_valid_terminal(tmp_path):
    policy = subject.GenerationRecoveryPolicy(_frame_check)
    with _child(tmp_path, _emit_after_eof(exit_code=23)) as process:
        assert _capture(process, tmp_path, policy) is None
        assert process.returncode == 23
    assert _retained(tmp_path) == (FRAMES, b"")
    assert policy._signal_attempted is False
    with pytest.raises(subject.GenerationCompletionError, match="actual exit0"):
        subject.normal_observations(23, INPUT, FRAMES, b"", 2.0)


@pytest.mark.parametrize("overflow", [False, True], ids=["exact", "plus-one"])
def test_combined_stdout_stderr_cap_and_accepted_prefix(tmp_path, overflow):
    cap = 257
    output = b"O" * 129
    error = b"E" * (128 + int(overflow))
    body = ("import os,sys\nsys.stdin.buffer.read()\n"
            f"os.write(1, {output!r})\nos.write(2, {error!r})\n")
    policy = subject.GenerationRecoveryPolicy(_frame_check)
    with _child(tmp_path, body) as process:
        if overflow:
            with pytest.raises(CaptureError, match="CLI output exceeds budget"):
                _capture(process, tmp_path, policy, cap=cap)
        else:
            assert _capture(process, tmp_path, policy, cap=cap) is None
            assert process.returncode == 0
    stdout, stderr = _retained(tmp_path)
    assert len(stdout) + len(stderr) == cap
    assert output.startswith(stdout) and error.startswith(stderr)
    if not overflow:
        assert (stdout, stderr) == (output, error)
        assert policy.failure_facts is None
    else:
        facts = policy.failure_facts
        assert facts["eligible"] is False
        assert facts["output_digest_domain"] == "CAPTURE_ACCEPTED_FILE_WRITES_NOT_POSTCLOSE_READBACK"
        for phase in ("before_cleanup", "after_cleanup"):
            observed = facts[phase]
            for name, raw in (("stdout", stdout), ("stderr", stderr)):
                assert observed[name + "_bytes"] == len(raw)
                assert observed[name + "_sha256"] == hashlib.sha256(raw).hexdigest()


def test_early_result_with_unwritten_input_is_a_failure_not_completion(tmp_path):
    body = f"import os\nos.close(0)\nos.write(1, {FRAMES!r})\n"
    policy = subject.GenerationRecoveryPolicy(_frame_check)
    data = b"x" * (2 * 1024 * 1024)  # Strictly larger than an ordinary pipe buffer.
    with _child(tmp_path, body) as process:
        with pytest.raises(CaptureError, match="input was not completely written"):
            _capture(process, tmp_path, policy, data=data)
    assert _retained(tmp_path) == (FRAMES, b"")
    facts = policy.failure_facts["before_cleanup"]
    assert facts["input_written_bytes"] < len(data)
    assert facts["input_writer_close_observed"] is False
    assert facts["signal_request_attempted"] is False


def test_exited_parent_with_inherited_writer_times_out_and_attempts_owned_cleanup(tmp_path):
    # Retain a writer in this test, not an orphan grandchild. EOF cannot be
    # inferred from direct-parent exit; no credentials/network/process tree needed.
    read_fd, write_fd = os.pipe()
    cleanup_calls = []

    class TrackingPolicy(subject.GenerationRecoveryPolicy):
        def cleanup_owned_group_on_failure(self):
            cleanup_calls.append(self._pgid)
            return super().cleanup_owned_group_on_failure()

    policy = TrackingPolicy(_frame_check)
    try:
        with _child(tmp_path, _emit_after_eof(), stdout=write_fd) as process:
            process.stdout = os.fdopen(read_fd, "rb", buffering=0)
            read_fd = None  # Ownership transferred to this process object's pipe.
            with pytest.raises(subprocess.TimeoutExpired):
                _capture(process, tmp_path, policy, timeout=0.8)
            assert process.returncode == 0
            assert cleanup_calls == [process.pid]
    finally:
        os.close(write_fd)
        if read_fd is not None:
            os.close(read_fd)
    assert _retained(tmp_path) == (FRAMES, b"")
    facts = policy.failure_facts
    assert facts["eligible"] is False
    assert facts["before_cleanup"]["parent_poll_returncode"] == 0
    assert facts["before_cleanup"]["stdout_eof_observed"] is False
    assert facts["before_cleanup"]["signal_request_attempted"] is False
    assert facts["after_cleanup"]["parent_poll_returncode"] == 0


class _Clock:
    def __init__(self):
        self.now = 100.0

    def advance(self, seconds):
        self.now += seconds


class _Process:
    pid = 752001
    args = ["synthetic-owned-child"]

    def __init__(self, clock):
        self.clock = clock
        self.returncode = None
        self.waits = []

    def poll(self):
        return self.returncode

    def wait(self, timeout):
        assert timeout > 0
        self.waits.append(timeout)
        if self.returncode is None:
            self.clock.advance(timeout)
            raise subprocess.TimeoutExpired(self.args, timeout)
        return self.returncode


def _unit_policy(monkeypatch, *, owned=True, timeout=20.0):
    """Pure lifecycle state tests: no OS signals or real children in this seam."""
    clock = _Clock()
    process = _Process(clock)
    calls = []

    def killpg(group, requested_signal):
        assert group == process.pid  # Any unpinned/unrelated group is a test failure.
        calls.append((group, requested_signal))
        if requested_signal == 0:
            if process.returncode is not None:
                raise ProcessLookupError
        else:
            assert requested_signal == signal.SIGKILL
            process.returncode = -signal.SIGKILL

    monkeypatch.setattr(subject, "time", SimpleNamespace(
        monotonic=lambda: clock.now, sleep=clock.advance,
    ))
    monkeypatch.setattr(subject, "os", SimpleNamespace(
        getpgid=lambda pid: pid if owned else pid + 1, killpg=killpg,
    ))
    policy = subject.GenerationRecoveryPolicy(_frame_check)
    policy.start(process, INPUT, timeout, clock.now + timeout)
    return clock, process, policy, calls


def _ready(clock, policy):
    policy.input_closed(len(INPUT))
    clock.advance(0.125)
    policy.observe_stdout(FRAMES)


@pytest.mark.parametrize("width", [1, 7, len(FRAMES)])
def test_incremental_valid_terminal_waits_full_fixed_grace(monkeypatch, width):
    clock, process, policy, calls = _unit_policy(monkeypatch)
    policy.input_closed(len(INPUT))
    for offset in range(0, len(FRAMES), width):
        policy.observe_stdout(FRAMES[offset:offset + width])
    assert policy.next_wait(30.0) == 5.0
    clock.advance(4.999)
    policy.maybe_shutdown(process)
    assert calls == [] and process.returncode is None
    clock.advance(0.002)
    policy.maybe_shutdown(process)
    assert calls == [(process.pid, signal.SIGKILL)]
    result = policy.finish(process, stdout_eof=True, stderr_eof=True, written=len(INPUT))
    assert result["outcome"] == "RECOVERED_COMPLETED_RESULT"
    assert result["post_final_grace_seconds"] == 5.0


@pytest.mark.parametrize("raw", [
    _wire(INIT, {**FINAL, "status": "error"}),
    _wire(INIT, {**FINAL, "conversation_id": "wrong-cid"}),
    _wire(INIT, {**FINAL, "model": "wrong-model"}),
    _wire(INIT, {**FINAL, "tool_calls": 1}),
    _wire(INIT, {**FINAL, "num_turns": 2}),
    FRAMES[:-1], FRAMES + b"partial", FRAMES + b"{}\n",
    FRAMES + _wire(FINAL), b"not-json\n",
], ids=["terminal-error", "cid", "model", "tool", "extra-turn", "partial-final",
        "partial-tail", "extra-line", "second-result", "malformed"])
def test_invalid_consumer_stream_never_earns_recovery(monkeypatch, raw):
    clock, process, policy, calls = _unit_policy(monkeypatch)
    policy.input_closed(len(INPUT))
    policy.observe_stdout(raw)
    clock.advance(6.0)
    policy.maybe_shutdown(process)
    assert calls == [] and policy.next_wait(9.0) == 9.0
    assert policy.finish(process, stdout_eof=True, stderr_eof=True, written=len(INPUT)) is None


@pytest.mark.parametrize("tail,stderr", [(b"late", b""), (b"{}\n", b""), (b"", b"warning")])
def test_late_tail_or_stderr_revokes_previously_valid_candidate(monkeypatch, tail, stderr):
    clock, process, policy, calls = _unit_policy(monkeypatch)
    _ready(clock, policy)
    assert policy.next_wait(30.0) == 5.0
    policy.observe_stdout(tail)
    policy.observe_stderr(stderr)
    clock.advance(6.0)
    policy.maybe_shutdown(process)
    assert calls == [] and policy.next_wait(30.0) == 30.0


def test_final_before_owned_input_close_cannot_be_rehabilitated(monkeypatch):
    clock, process, policy, calls = _unit_policy(monkeypatch)
    policy.observe_stdout(FRAMES)
    policy.input_closed(len(INPUT))
    clock.advance(6.0)
    policy.maybe_shutdown(process)
    assert calls == [] and policy._early_terminal_read is True
    assert policy.finish(process, stdout_eof=True, stderr_eof=True, written=len(INPUT)) is None


def test_unpinned_group_never_signalled_even_during_failure_cleanup(monkeypatch):
    clock, process, policy, calls = _unit_policy(monkeypatch, owned=False)
    _ready(clock, policy)
    clock.advance(6.0)
    policy.maybe_shutdown(process)
    policy.cleanup_owned_group_on_failure()
    assert calls == [] and policy._pgid is None
    assert policy.next_wait(9.0) == 9.0


def test_capture_deadline_never_extended_to_accommodate_grace(monkeypatch):
    clock, process, policy, calls = _unit_policy(monkeypatch, timeout=4.0)
    _ready(clock, policy)
    clock.advance(5.1)
    with pytest.raises(subject.GenerationCompletionError, match="cleanup deadline exhausted"):
        policy.maybe_shutdown(process)
    assert calls == [] and process.returncode is None


def test_eof_wait_exhausts_only_original_deadline(monkeypatch):
    clock, process, policy, calls = _unit_policy(monkeypatch, timeout=2.0)
    with pytest.raises(subprocess.TimeoutExpired):
        policy.wait_after_pipe_eof(process)
    assert process.waits == [2.0] and clock.now == 102.0
    assert calls == []


def test_late_invalid_tail_after_signal_is_not_recovered_success(monkeypatch):
    clock, process, policy, calls = _unit_policy(monkeypatch)
    _ready(clock, policy)
    clock.advance(5.0)
    policy.maybe_shutdown(process)
    assert calls == [(process.pid, signal.SIGKILL)]
    policy.observe_stdout(b"untrusted tail\n")
    with pytest.raises(subject.GenerationCompletionError, match="tail invalidates"):
        policy.finish(process, stdout_eof=True, stderr_eof=True, written=len(INPUT))


@pytest.mark.parametrize("field", ["stdout_eof", "stderr_eof", "written"])
def test_signalled_child_still_requires_complete_io_evidence(monkeypatch, field):
    clock, process, policy, _calls = _unit_policy(monkeypatch)
    _ready(clock, policy)
    clock.advance(5.0)
    policy.maybe_shutdown(process)
    arguments = {"stdout_eof": True, "stderr_eof": True, "written": len(INPUT)}
    arguments[field] = len(INPUT) - 1 if field == "written" else False
    with pytest.raises(subject.GenerationCompletionError, match="incomplete lifecycle"):
        policy.finish(process, **arguments)


def test_signal_exit_race_is_not_relabelled_recovered(monkeypatch):
    clock, process, policy, _calls = _unit_policy(monkeypatch)
    _ready(clock, policy)

    def vanished(_group, _signal):
        raise ProcessLookupError

    monkeypatch.setattr(subject.os, "killpg", vanished)
    clock.advance(5.0)
    policy.maybe_shutdown(process)
    process.returncode = 0
    with pytest.raises(subject.GenerationCompletionError, match="exit race is ambiguous"):
        policy.finish(process, stdout_eof=True, stderr_eof=True, written=len(INPUT))


def test_policy_cannot_be_reused_or_constructed_without_checker(monkeypatch):
    clock, process, policy, _calls = _unit_policy(monkeypatch)
    with pytest.raises(subject.GenerationCompletionError, match="observer reused"):
        policy.start(process, INPUT, 1.0, clock.now + 1.0)
    with pytest.raises(subject.GenerationCompletionError, match="checker is required"):
        subject.GenerationRecoveryPolicy(None)


@pytest.mark.parametrize("failure_at", ["body", "first-open", "second-open", "first-close"])
def test_first_failure_survives_every_cleanup_and_close_error(monkeypatch, failure_at):
    """Pure fault injection: no OS process, descriptors or signals are involved."""
    events = []

    class PrimaryFailure(BaseException):
        pass

    class CleanupFailure(Exception):
        pass

    primary = PrimaryFailure(failure_at)

    def fail_cleanup(label):
        events.append(label)
        raise CleanupFailure(label)

    class Stream:
        def __init__(self, name):
            self.name, self.closed = name, False

        def fileno(self):
            return 752

        def close(self):
            self.closed = True
            if failure_at == "first-close" and self.name == "stdin":
                events.append("initial-stdin-close")
                return
            fail_cleanup("close-" + self.name)

    class Output:
        def __init__(self, name):
            self.name = name

        def close(self):
            label = "close-" + self.name
            events.append(label)
            if failure_at == "first-close" and self.name == "stderr-file":
                raise primary
            raise CleanupFailure(label)

    class OutputPath:
        def __init__(self, name):
            self.name = name

        def open(self, mode):
            assert mode == "xb"
            events.append("open-" + self.name)
            if ((failure_at == "first-open" and self.name == "stdout-file")
                    or (failure_at == "second-open" and self.name == "stderr-file")):
                raise primary
            return Output(self.name)

    class Selector:
        def register(self, *_args):
            if failure_at == "body":
                raise primary

        def get_map(self):
            return {}

        def close(self):
            fail_cleanup("close-selector")

    process = SimpleNamespace(
        stdin=Stream("stdin"), stdout=Stream("stdout"), stderr=Stream("stderr"),
        wait=lambda timeout: fail_cleanup("wait-cleanup"),
    )
    policy = SimpleNamespace(
        start=lambda *_args: None, wait_after_pipe_eof=lambda *_args: None,
        input_closed=lambda *_args: None,
        finish=lambda *_args, **_kwargs: None,
        failure_before_cleanup=lambda *_args, **_kwargs: fail_cleanup("diagnose-before"),
        failure_after_cleanup=lambda *_args, **_kwargs: fail_cleanup("diagnose-after"),
        cleanup_owned_group_on_failure=lambda: fail_cleanup("group-cleanup"),
    )
    monkeypatch.setattr(selectors, "DefaultSelector", Selector)
    monkeypatch.setattr(subject, "os", SimpleNamespace(set_blocking=lambda *_args: None))
    with pytest.raises(PrimaryFailure) as caught:
        subject.capture_generation_process(
            process, b"" if failure_at == "first-close" else INPUT,
            OutputPath("stdout-file"), OutputPath("stderr-file"),
            1.0, 100, policy, error_type=CaptureError,
            terminate=lambda _process: fail_cleanup("terminate"), stream_chunk_size=13,
        )
    assert caught.value is primary
    for label in ("diagnose-before", "group-cleanup", "terminate", "wait-cleanup",
                  "diagnose-after", "close-selector", "close-stdout", "close-stderr"):
        assert events.count(label) == 1
    assert events.count("initial-stdin-close" if failure_at == "first-close" else "close-stdin") == 1
    if failure_at != "first-open":
        assert events.count("close-stdout-file") == 1
    if failure_at in ("body", "first-close"):
        assert events.count("close-stderr-file") == 1


@pytest.mark.parametrize("group_state", ["present", "absent", "permission-denied"])
def test_failure_cleanup_targets_pinned_group_even_after_parent_exit(monkeypatch, group_state):
    """Pure syscall oracle: parent exit is NOT proof its owned group disappeared."""
    _clock, process, policy, _old_calls = _unit_policy(monkeypatch)
    process.returncode = 0
    calls = []

    def owned_group(group, requested_signal):
        calls.append((group, requested_signal))
        assert group == process.pid and requested_signal == signal.SIGKILL
        if group_state == "absent":
            raise ProcessLookupError
        if group_state == "permission-denied":
            raise PermissionError("synthetic group denial")
        # A live owned descendant is represented here, NOT a real orphan child.
        # Signalling it must not rewrite the already-reaped parent's exit status.

    monkeypatch.setattr(subject.os, "killpg", owned_group)
    if group_state == "permission-denied":
        with pytest.raises(PermissionError, match="synthetic group denial"):
            policy.cleanup_owned_group_on_failure()
    else:
        policy.cleanup_owned_group_on_failure()
    assert calls == [(process.pid, signal.SIGKILL)]
    assert process.poll() == 0
    assert policy._signal_attempted is False  # Failure containment is not recovery.


def test_group_probe_crossing_deadline_fails_before_any_sleep(monkeypatch):
    clock, process, policy, _calls = _unit_policy(monkeypatch)
    _ready(clock, policy)
    clock.advance(5.0)
    policy.maybe_shutdown(process)
    sleeps = []

    def still_present_after_slow_probe(group, requested_signal):
        assert (group, requested_signal) == (process.pid, 0)
        assert policy._deadline - clock.now > 0
        clock.now = policy._deadline + 0.25

    monkeypatch.setattr(subject.os, "killpg", still_present_after_slow_probe)
    monkeypatch.setattr(subject.time, "sleep", sleeps.append)
    with pytest.raises(subject.GenerationCompletionError, match="group quiescence deadline exhausted"):
        policy.finish(process, stdout_eof=True, stderr_eof=True, written=len(INPUT))
    assert sleeps == []  # The post-probe recheck prevents a negative sleep value.
