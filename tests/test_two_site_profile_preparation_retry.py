"""Offline preparation retry admission; no CLI/Provider is ever invoked."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from scripts import two_site_proposal_pipeline as subject

HEAD = "a" * 40
MODEL = "public-fixture-model"


@pytest.fixture
def case(tmp_path, monkeypatch):
    task = {"task_id": "profile-retry-task", "goal": "fixed", "allowed_files": ["tests/test_x.py"]}
    task_path = tmp_path / "task.json"
    task_path.write_text(json.dumps(task))
    monkeypatch.setattr(subject, "load_task", lambda *_: (task, {}))
    monkeypatch.setattr(subject, "expected_role_model", lambda *_: MODEL)
    return tmp_path, task_path, task


def failed_attempt(root, task, run_id, role="verifier"):
    out = root / ".local/two-site-refactor/proposals" / run_id
    out.mkdir(parents=True, mode=0o700)
    status = {
        "run_id": run_id, "task_id": task["task_id"], "role": role,
        "head": HEAD, "model": MODEL, "state": "BLOCKED",
        "authority": "PROPOSAL_ONLY_NOT_ACCEPTANCE",
        "error": "ProposalProfileError: " + subject._EMPTY_INITIAL_PROFILE,
        # Actual Runner records ProposalError for absent outputs. Eligibility
        # must inspect actual absence, not pretend this means FileNotFoundError.
        "failure_retained_outputs": {
            "stream.jsonl": {"read_error_type": "ProposalError"},
            "stderr.txt": {"read_error_type": "ProposalError"},
        },
    }
    (out / "status.json").write_text(json.dumps(status))
    (out / "task.json").write_text(json.dumps({"source_head": HEAD, "task": task}))
    (out / "prompt.txt").write_text("public synthetic preparation input")
    (out / "response-schema.json").write_text("{}")
    return out


def admitted(root, task, out, error=None):
    return subject._retryable_profile_preparation(
        root, out, out.name, "verifier", HEAD, task,
        error if error is not None else subject.ProposalProfileError(subject._EMPTY_INITIAL_PROFILE),
    )


@pytest.mark.parametrize("role", ["worker", "verifier"])
def test_known_pre_generation_failure_gets_fresh_id_and_preserves_arguments(case, monkeypatch, role):
    root, task_path, task = case
    calls = []
    state_check = Mock()
    args = {"feedback": None, "proposal": root / "prior.json", "cli": root / "cli", "test_evidence": root / "evidence.json"}

    def runner(_root, _task, actual_role, run_id, timeout, **kwargs):
        calls.append((run_id, actual_role, timeout, kwargs))
        if len(calls) == 1:
            failed_attempt(root, task, run_id, role)
            raise subject.ProposalProfileError(subject._EMPTY_INITIAL_PROFILE)
        return root / ".local/two-site-refactor/proposals" / run_id

    monkeypatch.setattr(subject, "runner_run", runner)
    run_id, out = subject.run_provider_session(
        root, task_path, role, "fresh", 300, state_check, expected_head=HEAD, **args,
    )
    assert run_id == "fresh-retry-1" and out.name == run_id
    assert [c[0] for c in calls] == ["fresh", "fresh-retry-1"]
    assert all(c[1:] == (role, 300, args) for c in calls)
    assert state_check.call_count == 3
    assert (root / ".local/two-site-refactor/proposals/fresh/status.json").is_file()


def test_repeated_known_failure_stops_after_existing_three_attempts(case, monkeypatch):
    root, task_path, task = case
    calls, errors, retained = [], [], []
    state_check = Mock()

    def runner(_root, _task, role, run_id, _timeout, **_kwargs):
        calls.append(run_id)
        out = failed_attempt(root, task, run_id, role)
        retained.append((out / "status.json", (out / "status.json").read_bytes()))
        error = subject.ProposalProfileError(subject._EMPTY_INITIAL_PROFILE)
        errors.append(error)
        raise error

    monkeypatch.setattr(subject, "runner_run", runner)
    with pytest.raises(subject.ProposalError, match="after 3 attempts") as found:
        subject.run_provider_session(root, task_path, "verifier", "bounded", 300, state_check, expected_head=HEAD)
    assert calls == ["bounded", "bounded-retry-1", "bounded-retry-2"]
    assert found.value.__cause__ is errors[-1]
    assert state_check.call_count == 6
    assert all(path.read_bytes() == data for path, data in retained)


@pytest.mark.parametrize("change", [
    {"run_id": "other"}, {"task_id": "other"}, {"role": "worker"},
    {"head": "b" * 40}, {"model": "other"}, {"state": "RUNNING"},
    {"authority": "other"}, {"error": "ProposalProfileError: unknown"},
    {"runtime_profile": None}, {"prepared_effort": None}, {"cli_pid": None},
    {"conversation_id": None}, {"usage": {}}, {"result_sha256": None},
    {"delta_result_sha256": None}, {"completion_proof_sha256": None},
    {"cli_returncode": 0}, {"cli_normal_exit_observed": False},
    {"generation_completion_outcome": None}, {"generation_capture_failure": {}},
    {"noneligible_completion_observations": {}}, {"secondary_context_error_type": "unknown"},
    {"quota_evidence": {}},
])
def test_unsafe_status_never_admits_profile_retry(case, change):
    root, _, task = case
    out = failed_attempt(root, task, "unsafe")
    path = out / "status.json"
    status = json.loads(path.read_text())
    status.update(change)
    path.write_text(json.dumps(status))
    assert not admitted(root, task, out)


@pytest.mark.parametrize("name", [
    "stream.jsonl", "stderr.txt", "result.json", "delta-result.json",
    "transport-completion.json", "proposal.patch", "unknown-generation-proof.json",
])
def test_any_generation_or_unexpected_artifact_even_empty_rejects(case, name):
    root, _, task = case
    out = failed_attempt(root, task, "generated")
    (out / name).write_bytes(b"")
    assert not admitted(root, task, out)


@pytest.mark.parametrize("fault", [
    "snapshot-head", "snapshot-task", "duplicate-status-key", "status-over-bound",
    "missing-status", "missing-task", "missing-prompt", "missing-schema",
    "symlink-status", "hardlink-status", "symlink-directory", "writable-file",
    "writable-directory", "wrong-owner",
])
def test_untrusted_or_incomplete_owned_evidence_rejects(case, fault, monkeypatch):
    root, _, task = case
    out = failed_attempt(root, task, "untrusted")
    status = out / "status.json"
    if fault.startswith("snapshot-"):
        snapshot = json.loads((out / "task.json").read_text())
        if fault == "snapshot-head":
            snapshot["source_head"] = "b" * 40
        else:
            snapshot["task"] = {**task, "goal": "changed"}
        (out / "task.json").write_text(json.dumps(snapshot))
    elif fault == "duplicate-status-key":
        status.write_text(status.read_text()[:-1] + ', "state": "BLOCKED"}')
    elif fault == "status-over-bound":
        status.write_bytes(b" " * (subject.MAX_CONTEXT + 1))
    elif fault.startswith("missing-"):
        names = {"missing-status": "status.json", "missing-task": "task.json", "missing-prompt": "prompt.txt", "missing-schema": "response-schema.json"}
        (out / names[fault]).unlink()
    elif fault in {"symlink-status", "hardlink-status"}:
        original = root / "original-status.json"
        status.rename(original)
        if fault == "symlink-status":
            status.symlink_to(original)
        else:
            import os
            os.link(original, status)
    elif fault == "symlink-directory":
        actual = out.with_name("actual-directory")
        out.rename(actual)
        out.symlink_to(actual, target_is_directory=True)
    elif fault == "writable-file":
        status.chmod(0o666)
    elif fault == "writable-directory":
        out.chmod(0o777)
    elif fault == "wrong-owner":
        owner = subject.os.geteuid()
        monkeypatch.setattr(subject.os, "geteuid", lambda: owner + 1)
    assert not admitted(root, task, out)


def test_unknown_profile_error_is_raised_without_retry(case, monkeypatch):
    root, task_path, task = case
    error = subject.ProposalProfileError(subject._EMPTY_INITIAL_PROFILE + "; unknown")
    calls = []

    def runner(_root, _task, role, run_id, _timeout, **_kwargs):
        calls.append(run_id)
        failed_attempt(root, task, run_id, role)
        raise error

    monkeypatch.setattr(subject, "runner_run", runner)
    with pytest.raises(subject.ProposalProfileError) as found:
        subject.run_provider_session(root, task_path, "verifier", "unknown", 300, Mock(), expected_head=HEAD)
    assert found.value is error and calls == ["unknown"]


def test_state_drift_prevents_next_retry(case, monkeypatch):
    root, task_path, task = case
    calls = []

    def runner(_root, _task, role, run_id, _timeout, **_kwargs):
        calls.append(run_id)
        failed_attempt(root, task, run_id, role)
        raise subject.ProposalProfileError(subject._EMPTY_INITIAL_PROFILE)

    monkeypatch.setattr(subject, "runner_run", runner)
    state_check = Mock(side_effect=[None, subject.ProposalError("state drift")])
    with pytest.raises(subject.ProposalError, match="state drift"):
        subject.run_provider_session(root, task_path, "verifier", "drift", 300, state_check, expected_head=HEAD)
    assert calls == ["drift"]
