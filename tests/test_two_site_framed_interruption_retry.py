"""Only guarded framed interruptions inherit the existing fresh-session retry."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from scripts import two_site_proposal_pipeline as subject
from scripts import two_site_proposal_runner as runner

MESSAGE = "provider stream interrupted before a complete result"
HEAD = "a" * 40


def guarded_error(fault=None):
    """Replay real guard/parser code with entirely synthetic public envelopes."""
    session = "public-fixture-session"
    ack = runner._input_ack(1, 2, "public-token")
    init = {"event": "init", "conversation_id": session, "init": {"model": runner.MODEL}}
    first = {"event": "result", "result": {
        "conversation_id": session, "status": "SUCCESS", "num_turns": 1,
        "response": json.dumps(ack),
    }}
    last = {"event": "result", "result": {
        "conversation_id": session, "status": "ERROR", "num_turns": 2,
        "error": "The stream was interrupted. Please continue the task you were working on.",
    }}
    events = [init, first, last]
    manifest = {"frame_count": 2, "frames": [{"ack": ack}, {"ack": None}]}
    if fault == "ack":
        first["result"]["response"] = json.dumps({**ack, "summary": "wrong"})
    elif fault == "session":
        last["result"]["conversation_id"] = "another-session"
    elif fault == "tool":
        events.insert(2, {"event": "step_update", "step_update": {
            "conversation_id": session, "step_type": "tool", "tool_name": "not-authorized",
        }})
    elif fault == "denial":
        last["result"]["denied_actions"] = ["not-authorized"]
    elif fault == "output-limit":
        last["result"]["error"] = "Your response exceeded the output token limit"
    elif fault == "unknown":
        last["result"]["error"] = "Unknown provider failure"
    raw = "\n".join(json.dumps(e) for e in events) + "\n"
    try:
        runner._framed_stream_projection(raw, manifest, runner.MODEL, session, complete=True)
    except subject.ProposalError as error:
        return error, raw
    raise AssertionError("synthetic ERROR must never produce eligible success")


@pytest.fixture
def case(tmp_path, monkeypatch):
    task_path = tmp_path / "task.json"
    task = {"task_id": "public-task", "goal": "fixed full sources", "allowed_files": ["tests/test_x.py"]}
    task_path.write_text(json.dumps(task))
    quota = Mock(return_value=None)
    monkeypatch.setattr(subject, "load_task", lambda *_: (task, {"public.py": {"content": "fixed"}}))
    monkeypatch.setattr(subject, "load_worker_routing_policy", lambda *_args, **_kw: SimpleNamespace(enabled=False))
    monkeypatch.setattr(subject, "_extract_bound_quota_evidence", quota)
    return tmp_path, task_path, task, quota


def invoke(case, check=None, role="verifier", **kwargs):
    root, task_path, _, _ = case
    return subject.run_provider_session(root, task_path, role, "frame", 300,
                                        check or Mock(), expected_head=HEAD, **kwargs)


@pytest.mark.parametrize("role", ["worker", "verifier"])
def test_actual_wrapped_guard_error_retries_fresh_with_all_arguments_unchanged(case, monkeypatch, role):
    root, _, task, quota = case
    error, raw = guarded_error()
    assert type(error) is subject.ProposalError and str(error) == MESSAGE
    assert type(error.__context__) is subject.RetryableProviderSessionError
    assert error.__cause__ is None and error.__suppress_context__ is True
    calls = []
    kwargs = {"feedback": root / "feedback" if role == "worker" else None,
              "proposal": root / "proposal", "cli": root / "cli"}
    if role == "verifier":
        kwargs["test_evidence"] = root / "evidence"
    check = Mock()

    def run(actual_root, actual_task, actual_role, run_id, timeout, **actual_kwargs):
        calls.append((actual_root, actual_task, actual_role, run_id, timeout, actual_kwargs))
        out = root / ".local/two-site-refactor/proposals" / run_id
        out.mkdir(parents=True)
        if len(calls) == 1:
            (out / "stream.jsonl").write_text(raw)
            raise error
        return out

    monkeypatch.setattr(subject, "runner_run", run)
    run_id, out = invoke(case, check, role=role, **kwargs)
    assert run_id == "frame-retry-1" and out.name == run_id
    assert [c[3] for c in calls] == ["frame", "frame-retry-1"]
    assert all(c[:3] == (root, case[1], role) and c[4:] == (300, kwargs) for c in calls)
    assert check.call_count == 3 and quota.call_count == 1
    assert (root / ".local/two-site-refactor/proposals/frame/stream.jsonl").read_text() == raw
    assert task["goal"] == "fixed full sources"


def test_existing_three_attempt_budget_is_shared_and_last_cause_is_preserved(case, monkeypatch):
    root, _, _, quota = case
    calls, contexts = [], []
    check = Mock()

    def run(_root, _task, _role, run_id, _timeout, **_kwargs):
        calls.append(run_id)
        (root / ".local/two-site-refactor/proposals" / run_id).mkdir(parents=True)
        error, _ = guarded_error()
        contexts.append(error.__context__)
        raise error

    monkeypatch.setattr(subject, "runner_run", run)
    with pytest.raises(subject.ProposalError, match="after 3 attempts") as found:
        invoke(case, check)
    assert calls == ["frame", "frame-retry-1", "frame-retry-2"]
    assert found.value.__cause__ is contexts[-1]
    assert quota.call_count == 3 and check.call_count == 6


class OuterSubclass(subject.ProposalError):
    pass


@pytest.mark.parametrize("fault", [
    "plain-message", "outer-subclass", "wrong-context", "context-subclass",
    "forbidden-context", "outer-message", "inner-message", "explicit-cause",
    "not-from-none",
])
def test_string_or_unqualified_exception_shapes_do_not_retry(case, monkeypatch, fault):
    inner = subject.RetryableProviderSessionError(MESSAGE)
    outer = OuterSubclass(MESSAGE) if fault == "outer-subclass" else subject.ProposalError(MESSAGE)
    if fault == "wrong-context":
        inner = subject.ProposalError(MESSAGE)
    elif fault == "context-subclass":
        inner = subject.RetryableProviderOutputError(MESSAGE)
    elif fault == "forbidden-context":
        inner = subject.ForbiddenToolAttemptError(MESSAGE)
    elif fault == "outer-message":
        outer = subject.ProposalError("another failure")
    elif fault == "inner-message":
        inner = subject.RetryableProviderSessionError("another failure")
    outer.__context__ = None if fault == "plain-message" else inner
    outer.__cause__ = RuntimeError("cleanup uncertain") if fault == "explicit-cause" else None
    outer.__suppress_context__ = fault != "not-from-none"
    calls = Mock(side_effect=outer)
    monkeypatch.setattr(subject, "runner_run", calls)
    with pytest.raises(type(outer)) as found:
        invoke(case)
    assert found.value is outer and calls.call_count == 1


@pytest.mark.parametrize("fault", ["ack", "session", "tool", "denial", "output-limit", "unknown"])
def test_real_framed_guard_rejections_remain_nonretryable(case, monkeypatch, fault):
    error, _ = guarded_error(fault)
    calls = Mock(side_effect=error)
    monkeypatch.setattr(subject, "runner_run", calls)
    with pytest.raises(subject.ProposalError) as found:
        invoke(case)
    assert found.value is error and calls.call_count == 1


def test_validated_quota_has_precedence_even_over_the_exact_interruption_context(case, monkeypatch):
    _, _, _, quota = case
    error, _ = guarded_error()
    evidence = {"kind": "public-test-quota"}
    attempt = {"role": "verifier", "run_id": "frame", "status_sha256": "b" * 64}
    quota.return_value = (evidence, attempt)
    calls = Mock(side_effect=error)
    monkeypatch.setattr(subject, "runner_run", calls)
    with pytest.raises(subject.QuotaBlockError) as found:
        invoke(case)
    assert found.value.quota_evidence == evidence and found.value.quota_attempt == attempt
    assert calls.call_count == 1


def test_state_drift_stops_before_any_fresh_retry(case, monkeypatch):
    error, _ = guarded_error()
    calls = Mock(side_effect=error)
    monkeypatch.setattr(subject, "runner_run", calls)
    check = Mock(side_effect=[None, subject.ProposalError("source or authority drift")])
    with pytest.raises(subject.ProposalError, match="source or authority drift"):
        invoke(case, check)
    assert calls.call_count == 1
