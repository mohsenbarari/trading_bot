"""802R bounded owned-child/typed-consumer regressions; no provider or CLI.

The real child test observes Linux WNOWAIT zombie exit0 without reaping it,
then an actual successful SIGKILL syscall, then the SUT's actual wait/reap.
All other process states below are explicitly synthetic unit fixtures.
"""
from contextlib import ExitStack
import os
import signal
import subprocess
import sys
import time

import pytest

from scripts import two_site_generation_completion as subject
from tests import test_two_site_generation_completion as lifecycle
from tests import test_two_site_generation_completion_contract as consumers


def _returned_zero_state(monkeypatch):
    clock, process, policy, calls = lifecycle._unit_policy(monkeypatch)
    lifecycle._ready(clock, policy)

    def returned(group, requested_signal):
        assert group == process.pid
        calls.append((group, requested_signal))
        if requested_signal == signal.SIGKILL:
            process.returncode = 0
        else:
            assert requested_signal == 0
            raise ProcessLookupError

    monkeypatch.setattr(subject.os, "killpg", returned)
    clock.advance(subject.POST_FINAL_GRACE_SECONDS)
    policy.maybe_shutdown(process)
    assert policy._signal_attempted is True and policy._signal_sent is True
    return clock, process, policy, calls


def _finish(process, policy, **changes):
    arguments = dict(stdout_eof=True, stderr_eof=True, written=len(lifecycle.INPUT))
    arguments.update(changes)
    return policy.finish(process, **arguments)


def test_actual_owned_zombie_exit0_and_returned_sigkill_are_bounded_recovered_facts(tmp_path, monkeypatch):
    read_fd, write_fd = os.pipe()
    process = None
    actual_killpg = os.killpg
    observed = []
    home = tmp_path / "empty-home"; home.mkdir()
    body = (
        "import os,sys\n"
        "sys.stdin.buffer.read()\n"
        f"os.write(1,{lifecycle.FRAMES!r})\n"
        f"assert os.read({read_fd},1)==b'Q'\n"
        "sys.exit(0)\n"
    )
    try:
        process = subprocess.Popen(
            [sys.executable, "-I", "-B", "-c", body], cwd=tmp_path,
            env={"PATH": os.defpath, "HOME": str(home), "LANG": "C", "LC_ALL": "C"},
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            pass_fds=(read_fd,), start_new_session=True, close_fds=True,
        )
        os.close(read_fd); read_fd = None
        assert process.pid > 0 and os.getpgid(process.pid) == process.pid != os.getpgrp()

        def killpg(group, requested_signal):
            assert group == process.pid, "Only this real test-owned group is permitted"
            if requested_signal == signal.SIGKILL and not observed:
                assert os.write(write_fd, b"Q") == 1
                until = time.monotonic() + 2
                while True:
                    info = os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOWAIT | os.WNOHANG)
                    if info is not None:
                        break
                    assert time.monotonic() < until, "Owned zombie observation deadline"
                    time.sleep(0.001)
                assert info.si_pid == process.pid and info.si_code == os.CLD_EXITED and info.si_status == 0
                assert process.returncode is None, "WNOWAIT must not fake or reap Popen's returncode"
                actual_killpg(group, requested_signal)  # The real kernel syscall must return.
                observed.append("ACTUAL_SIGKILL_RETURNED_AFTER_OWN_ZOMBIE_EXIT0")
                return
            return actual_killpg(group, requested_signal)

        policy = subject.GenerationRecoveryPolicy(lifecycle._frame_check)
        with monkeypatch.context() as patch:
            patch.setattr(subject.os, "killpg", killpg)
            started = time.monotonic()
            result = lifecycle._capture(process, tmp_path, policy, timeout=12.0)
            assert subject.POST_FINAL_GRACE_SECONDS <= time.monotonic() - started < 12.0
        assert observed == ["ACTUAL_SIGKILL_RETURNED_AFTER_OWN_ZOMBIE_EXIT0"]
        assert process.poll() == process.wait(timeout=2) == 0
        with pytest.raises(ProcessLookupError):
            actual_killpg(process.pid, 0)
        assert all(stream.closed for stream in (process.stdin, process.stdout, process.stderr))
        stdout, stderr = lifecycle._retained(tmp_path)
        assert (stdout, stderr) == (lifecycle.FRAMES, b"")
        checked = subject.validate_observations(result, lifecycle.INPUT, stdout, stderr)
        assert checked["outcome"] == "RECOVERED_COMPLETED_RESULT"
        assert checked["actual_returncode"] == 0 and type(checked["actual_returncode"]) is int
        assert checked["timeout_kind"] == "POST_FINAL_EXIT_RACE"
        assert checked["normal_exit_observed"] is False
        assert checked["signal_attempted"] is True and checked["cleanup_signal"] == signal.SIGKILL
        assert checked["owned_group_absent"] is True and policy.failure_facts is None
    finally:
        def reap_owned_child():
            if process is not None:
                if process.poll() is None:
                    try:
                        actual_killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                process.wait(timeout=2)
        # Attempt every owned teardown, even if an earlier wait/close fails.
        with ExitStack() as cleanup:
            for fd in (read_fd, write_fd):
                if fd is not None:
                    cleanup.callback(os.close, fd)
            if process is not None:
                for stream in (process.stdin, process.stdout, process.stderr):
                    if stream is not None and not stream.closed:
                        cleanup.callback(stream.close)
                cleanup.callback(reap_owned_child)


def test_returned_signal_and_exact_int_zero_use_distinct_closed_pair(monkeypatch):
    _clock, process, policy, calls = _returned_zero_state(monkeypatch)
    result = _finish(process, policy)
    assert calls == [(process.pid, signal.SIGKILL), (process.pid, 0)]
    assert result["outcome"] == subject.OUTCOME_RECOVERED
    assert result["actual_returncode"] == 0 and result["timeout_kind"] == "POST_FINAL_EXIT_RACE"
    assert result["normal_exit_observed"] is False and result["signal_attempted"] is True
    assert subject.validate_observations(result, lifecycle.INPUT, lifecycle.FRAMES, b"") == result


def test_original_sigkill_pair_remains_unchanged(monkeypatch):
    clock, process, policy, _calls = lifecycle._unit_policy(monkeypatch)
    lifecycle._ready(clock, policy); clock.advance(5.0); policy.maybe_shutdown(process)
    result = _finish(process, policy)
    assert result["actual_returncode"] == -signal.SIGKILL
    assert result["timeout_kind"] == "POST_FINAL_SHUTDOWN"
    assert subject.validate_observations(result, lifecycle.INPUT, lifecycle.FRAMES, b"") == result


def test_esrch_without_returned_syscall_and_exit0_is_still_ambiguous(monkeypatch):
    clock, process, policy, _calls = lifecycle._unit_policy(monkeypatch)
    lifecycle._ready(clock, policy)
    def absent(_group, _signal):
        raise ProcessLookupError
    monkeypatch.setattr(subject.os, "killpg", absent)
    clock.advance(5.0); policy.maybe_shutdown(process); process.returncode = 0
    assert policy._signal_attempted is True and policy._signal_sent is False
    with pytest.raises(subject.GenerationCompletionError, match="exit race is ambiguous"):
        _finish(process, policy)


@pytest.mark.parametrize("returncode", [1, 23, -signal.SIGTERM, None, False, 0.0])
def test_returned_signal_does_not_rescue_nonzero_unknown_or_boolean_exit(monkeypatch, returncode):
    _clock, process, policy, _calls = _returned_zero_state(monkeypatch)
    process.returncode = returncode
    with pytest.raises(subject.GenerationCompletionError, match="exit race is ambiguous"):
        _finish(process, policy)


@pytest.mark.parametrize("field", ["stdout_eof", "stderr_eof", "written", "input_close", "terminal_read", "early_terminal", "stderr"])
def test_exit_race_requires_all_original_input_eof_order_and_stderr_guards(monkeypatch, field):
    _clock, process, policy, _calls = _returned_zero_state(monkeypatch)
    changes = {}
    if field in ("stdout_eof", "stderr_eof"):
        changes[field] = False
    elif field == "written":
        changes[field] = len(lifecycle.INPUT) - 1
    elif field == "input_close":
        policy._closed_at = None
    elif field == "terminal_read":
        policy._terminal_read_at = None
    elif field == "early_terminal":
        policy._early_terminal_read = True
    else:
        policy.observe_stderr(b"warning")
    with pytest.raises(subject.GenerationCompletionError, match="incomplete lifecycle"):
        _finish(process, policy, **changes)


@pytest.mark.parametrize("tail", [b"partial", b"{}\n", lifecycle._wire(lifecycle.FINAL)])
def test_late_invalid_tail_after_zero_exit_is_not_accepted(monkeypatch, tail):
    _clock, process, policy, _calls = _returned_zero_state(monkeypatch)
    policy.observe_stdout(tail)
    with pytest.raises(subject.GenerationCompletionError, match="tail invalidates"):
        _finish(process, policy)


def test_unknown_or_live_owned_group_never_becomes_success(monkeypatch):
    clock, process, policy, _calls = _returned_zero_state(monkeypatch)
    def denied(_group, _signal):
        raise PermissionError
    monkeypatch.setattr(subject.os, "killpg", denied)
    with pytest.raises(subject.GenerationCompletionError, match="group absence is unknown"):
        _finish(process, policy)
    monkeypatch.setattr(subject.os, "killpg", lambda _group, _signal: None)
    with pytest.raises(subject.GenerationCompletionError, match="quiescence deadline exhausted"):
        _finish(process, policy)
    assert clock.now >= policy._deadline


def test_zero_exit_race_never_extends_original_capture_deadline(monkeypatch):
    clock, process, policy, _calls = _returned_zero_state(monkeypatch)
    clock.advance(20.0)
    with pytest.raises(subject.GenerationCompletionError, match="quiescence deadline exhausted"):
        _finish(process, policy)


@pytest.mark.parametrize("returncode", [0, -signal.SIGKILL])
def test_absent_group_probe_crossing_original_deadline_cannot_claim_in_budget(monkeypatch, returncode):
    clock, process, policy, _calls = _returned_zero_state(monkeypatch)
    process.returncode = returncode
    def late_absence(group, requested_signal):
        assert group == process.pid and requested_signal == 0
        clock.advance(policy._deadline - clock.now + 0.001)
        raise ProcessLookupError
    monkeypatch.setattr(subject.os, "killpg", late_absence)
    with pytest.raises(subject.GenerationCompletionError, match="quiescence deadline exhausted"):
        _finish(process, policy)
    assert clock.now > policy._deadline


@pytest.mark.parametrize("returncode,kind", [
    (0, "POST_FINAL_SHUTDOWN"), (-signal.SIGKILL, "POST_FINAL_EXIT_RACE"),
    (False, "POST_FINAL_EXIT_RACE"), (23, "POST_FINAL_EXIT_RACE"), (0, "UNKNOWN"),
])
def test_retained_returncode_and_kind_must_be_the_exact_closed_pair(monkeypatch, returncode, kind):
    _clock, process, policy, _calls = _returned_zero_state(monkeypatch)
    result = _finish(process, policy)
    with pytest.raises(subject.GenerationCompletionError, match="invalid shutdown observations"):
        subject.validate_observations(dict(result, actual_returncode=returncode, timeout_kind=kind),
                                      lifecycle.INPUT, lifecycle.FRAMES, b"")


@pytest.mark.parametrize("key,value", [
    ("normal_exit_observed", True), ("signal_attempted", False),
    ("cleanup_signal", signal.SIGTERM), ("owned_group_absent", None),
    ("stdout_eof", False), ("full_input_written", 1),
    ("post_final_grace_seconds", 0.0), ("lifecycle_within_capture_deadline", False),
])
def test_retained_exit_race_tampering_is_not_normal_or_recovered_success(monkeypatch, key, value):
    _clock, process, policy, _calls = _returned_zero_state(monkeypatch)
    result = _finish(process, policy)
    with pytest.raises(subject.GenerationCompletionError):
        subject.validate_observations(dict(result, **{key: value}), lifecycle.INPUT, lifecycle.FRAMES, b"")


@pytest.fixture
def contract_case():
    case = consumers.GenerationCompletionContracts("runTest")
    try:
        case.setUp()
        yield case
    finally:
        case.doCleanups()


def _exit_race_proof(case, directory):
    """Synthetic retained-proof fixture; never claims real OS or provider facts."""
    case.recovered(directory)
    proof, status = case.proof(directory), case.status(directory)
    proof["observations"].update(actual_returncode=0, timeout_kind="POST_FINAL_EXIT_RACE")
    status["cli_returncode"] = 0
    case.save_proof(directory, proof, status)


def test_same_three_actual_consumers_require_and_accept_the_closed_zero_exit_pair(contract_case):
    case = contract_case
    for directory in (case.worker, case.reviewer):
        _exit_race_proof(case, directory)
    for kind in ("runner", "pipeline", "supervisor"):
        assert case.gate(kind) == case.result
    assert case.status(case.worker)["cli_returncode"] == 0
    assert case.status(case.worker)["cli_normal_exit_observed"] is False


def test_three_actual_consumers_reject_cross_kind_unknown_signal_and_status_mismatch(contract_case):
    case = contract_case
    _exit_race_proof(case, case.reviewer)
    for mutation in ("old_kind", "wrong_rc", "no_signal", "unknown_group", "status_mismatch"):
        _exit_race_proof(case, case.worker)
        proof, status = case.proof(case.worker), case.status(case.worker)
        if mutation == "old_kind":
            proof["observations"]["timeout_kind"] = "POST_FINAL_SHUTDOWN"
        elif mutation == "wrong_rc":
            proof["observations"]["actual_returncode"] = status["cli_returncode"] = -signal.SIGKILL
        elif mutation == "no_signal":
            proof["observations"]["signal_attempted"] = False
        elif mutation == "unknown_group":
            proof["observations"]["owned_group_absent"] = None
        else:
            status["cli_returncode"] = -signal.SIGKILL
        case.save_proof(case.worker, proof, status)
        for kind in ("runner", "pipeline", "supervisor"):
            with pytest.raises((consumers.runner.ProposalError, OSError)):
                case.gate(kind)
