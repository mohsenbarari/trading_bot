"""Tests for one-shot worker-0 quota continuation execution."""

from __future__ import annotations

from contextlib import contextmanager
import copy
import hashlib
import json
import os
from pathlib import Path
from typing import Any
import pytest

from scripts.two_site_proposal_pipeline import ProposalError, QuotaBlockError
try:
    from scripts.two_site_proposal_runner import WORKER_MODEL
except ImportError:
    from two_site_proposal_runner import WORKER_MODEL  # type: ignore[no-redef]
from scripts.two_site_quota_continuation_admission import ContinuationAdmissionError
import scripts.two_site_quota_resume_worker_zero_once as worker_zero_mod
from scripts.two_site_quota_resume_worker_zero_once import execute_worker_zero_once


@pytest.fixture
def accepted_ctx(tmp_path: Path) -> dict[str, Any]:
    task_p = tmp_path / "task.json"
    task_p.write_text("{}", encoding="utf-8")
    return {
        "continuation_run_prefix": "cont-run",
        "original_run_prefix": "orig-run",
        "source_head": "c" * 40,
        "initial_refs": {"ref": "hash"},
        "original_receipt": {
            "request": {"request_id": "req-1"},
            "response": {},
        },
        "original_terminal_receipt": {
            "request": {"request_id": "req-1"},
            "response": {},
        },
        "admitted_request": {
            "schema_version": "1.0",
            "request_type": "QUOTA_CONTINUATION",
            "request_id": "cont-1",
            "original_request_id": "req-1",
            "run_prefix": "cont-run",
            "role": "worker",
            "timeout": 120,
            "task_id": "task-1",
            "semantic_task_fingerprint": "d" * 64,
            "source_head": "c" * 40,
        },
        "admitted_task_path": task_p,
        "artifacts": {
            "task_id": "task-1",
            "semantic_task_fingerprint": "d" * 64,
            "source_head": "c" * 40,
            "original_run_prefix": "orig-run",
            "attempted_run_id": "orig-run-worker-0",
            "status_sha256": "e" * 64,
            "status_sha": "e" * 64,
            "quota_evidence": {
                "kind": "QUOTA_RESET_UNKNOWN",
                "provider": "gemini",
                "model": WORKER_MODEL,
                "conversation_id": "c1",
                "reset_at_utc": None,
            },
            "worker_index": 0,
        },
        "task": {"task_id": "task-1"},
        "sources": {},
    }


def _setup_common(monkeypatch, ctx: dict[str, Any]):
    @contextmanager
    def fake_ctx(root, req):
        yield ctx

    monkeypatch.setattr(worker_zero_mod, "worker_zero_continuation_context", fake_ctx)
    monkeypatch.setattr(worker_zero_mod, "ref_snapshot", lambda root: {"ref": "hash"})
    monkeypatch.setattr(worker_zero_mod, "check_state", lambda *a, **kw: None)
    monkeypatch.setattr(
        worker_zero_mod,
        "validate_quota_continuation_request",
        lambda root, req: (copy.deepcopy(ctx["admitted_request"]), ctx["admitted_task_path"], None),
    )
    monkeypatch.setattr(
        worker_zero_mod,
        "read_terminal_quota_record",
        lambda root, rid: copy.deepcopy(ctx["original_terminal_receipt"]),
    )
    monkeypatch.setattr(
        worker_zero_mod,
        "derive_worker_resume_artifacts",
        lambda adm, rec: copy.deepcopy(ctx["artifacts"]),
    )


def test_worker_zero_proposal_ready_result(tmp_path: Path, accepted_ctx: dict[str, Any], monkeypatch):
    _setup_common(monkeypatch, accepted_ctx)
    run_dir = tmp_path / ".local/two-site-refactor/proposals/cont-run-worker-0"
    calls = []

    def fake_runner(root, task_path, role, run_id, timeout, **kwargs):
        calls.append(run_id)
        assert role == "worker"
        assert timeout == 120
        if kwargs.get("pre_provider_check"):
            kwargs["pre_provider_check"]()
        run_dir.mkdir(parents=True, exist_ok=True)
        return run_dir

    monkeypatch.setattr(worker_zero_mod, "runner_run", fake_runner)
    monkeypatch.setattr(
        worker_zero_mod,
        "validate_run",
        lambda **kw: (
            {"status": "PROPOSAL_READY", "changes": [{"path": "pkg/mod.py"}]},
            {"conversation_id": "w-conv"},
            "res_sha_123",
            "{}",
        ),
    )
    res = execute_worker_zero_once(tmp_path, {"role": "worker"})
    assert calls == ["cont-run-worker-0"]
    assert res["state"] == "WORKER_PROPOSAL_READY"
    assert res["task_id"] == "task-1"
    assert res["source_head"] == accepted_ctx["source_head"]
    assert res["final_worker_run_id"] == "cont-run-worker-0"
    assert res["proposal_sha256"] == "res_sha_123"
    assert res["changed_paths"] == ["pkg/mod.py"]
    assert res["proposal_path"] == ".local/two-site-refactor/proposals/cont-run-worker-0/result.json"
    assert res["correction_count"] == 0
    assert res["semantic_task_fingerprint"] == accepted_ctx["artifacts"]["semantic_task_fingerprint"]
    assert "READY_FOR_CODEX" not in res.values()
    expected_keys = {
        "state", "task_id", "source_head", "final_worker_run_id", "proposal_sha256",
        "changed_paths", "proposal_path", "correction_count", "semantic_task_fingerprint",
    }
    assert set(res.keys()) == expected_keys


def test_lock_held_through_runner_and_validation(tmp_path: Path, accepted_ctx: dict[str, Any], monkeypatch):
    is_locked = False
    runner_checked_lock = False
    validate_checked_lock = False

    @contextmanager
    def fake_ctx(root, req):
        nonlocal is_locked
        is_locked = True
        try:
            yield accepted_ctx
        finally:
            is_locked = False

    monkeypatch.setattr(worker_zero_mod, "worker_zero_continuation_context", fake_ctx)
    monkeypatch.setattr(worker_zero_mod, "ref_snapshot", lambda root: {"ref": "hash"})
    monkeypatch.setattr(worker_zero_mod, "check_state", lambda *a, **kw: None)
    monkeypatch.setattr(
        worker_zero_mod,
        "validate_quota_continuation_request",
        lambda root, req: (copy.deepcopy(accepted_ctx["admitted_request"]), accepted_ctx["admitted_task_path"], None),
    )
    monkeypatch.setattr(
        worker_zero_mod,
        "read_terminal_quota_record",
        lambda root, rid: copy.deepcopy(accepted_ctx["original_terminal_receipt"]),
    )
    monkeypatch.setattr(
        worker_zero_mod,
        "derive_worker_resume_artifacts",
        lambda adm, rec: copy.deepcopy(accepted_ctx["artifacts"]),
    )

    run_dir = tmp_path / ".local/two-site-refactor/proposals/cont-run-worker-0"

    def fake_runner(*a, **kw):
        nonlocal runner_checked_lock
        runner_checked_lock = is_locked
        if kw.get("pre_provider_check"):
            kw["pre_provider_check"]()
        run_dir.mkdir(parents=True, exist_ok=True)
        return run_dir

    def fake_validate(**kw):
        nonlocal validate_checked_lock
        assert kw.get("role") == "worker"
        assert kw.get("prior_worker") is None
        assert kw.get("seen_convs") == set()
        validate_checked_lock = is_locked
        return ({"status": "PROPOSAL_READY", "changes": []}, {"conversation_id": "c"}, "sha", "txt")

    monkeypatch.setattr(worker_zero_mod, "runner_run", fake_runner)
    monkeypatch.setattr(worker_zero_mod, "validate_run", fake_validate)

    res = execute_worker_zero_once(tmp_path, {"role": "worker"})
    assert runner_checked_lock is True
    assert validate_checked_lock is True
    assert is_locked is False
    assert res["state"] == "WORKER_PROPOSAL_READY"


@pytest.mark.parametrize("suffix", ["", "-retry-1", "-retry-2"])
def test_preexisting_run_dir(tmp_path: Path, accepted_ctx: dict[str, Any], monkeypatch, suffix: str):
    _setup_common(monkeypatch, accepted_ctx)
    (tmp_path / f".local/two-site-refactor/proposals/cont-run-worker-0{suffix}").mkdir(parents=True, exist_ok=True)
    calls = []
    monkeypatch.setattr(worker_zero_mod, "runner_run", lambda *a, **kw: calls.append(1))
    with pytest.raises(ProposalError, match="already exists"):
        execute_worker_zero_once(tmp_path, {"role": "worker"})
    assert len(calls) == 0


def test_same_original_attempted_id(tmp_path: Path, accepted_ctx: dict[str, Any], monkeypatch):
    same_prefix_ctx = copy.deepcopy(accepted_ctx)
    same_prefix_ctx["continuation_run_prefix"] = "orig-run"
    _setup_common(monkeypatch, same_prefix_ctx)
    calls = []
    monkeypatch.setattr(worker_zero_mod, "runner_run", lambda *a, **kw: calls.append(1))
    with pytest.raises(ProposalError, match="cannot match original run ID"):
        execute_worker_zero_once(tmp_path, {"role": "worker"})
    assert len(calls) == 0


def test_strict_admission_stop(tmp_path: Path, accepted_ctx: dict[str, Any], monkeypatch):
    _setup_common(monkeypatch, accepted_ctx)

    def fake_admission(root, req):
        raise ContinuationAdmissionError("STOP sentinel detected at .local/two-site-refactor/autopilot/STOP")

    monkeypatch.setattr(worker_zero_mod, "validate_quota_continuation_request", fake_admission)
    simulated_popen = []

    def fake_runner(root, task_path, role, run_id, timeout, **kwargs):
        kwargs["pre_provider_check"]()
        simulated_popen.append(1)
        return tmp_path

    monkeypatch.setattr(worker_zero_mod, "runner_run", fake_runner)
    with pytest.raises(ContinuationAdmissionError, match="STOP sentinel detected"):
        execute_worker_zero_once(tmp_path, {"role": "worker"})
    assert len(simulated_popen) == 0


def test_strict_admission_drift(tmp_path: Path, accepted_ctx: dict[str, Any], monkeypatch):
    _setup_common(monkeypatch, accepted_ctx)
    drifted_req = copy.deepcopy(accepted_ctx["admitted_request"])
    drifted_req["timeout"] = 999
    monkeypatch.setattr(
        worker_zero_mod,
        "validate_quota_continuation_request",
        lambda root, req: (drifted_req, accepted_ctx["admitted_task_path"], None),
    )

    def fake_runner(root, task_path, role, run_id, timeout, **kwargs):
        kwargs["pre_provider_check"]()
        return tmp_path

    monkeypatch.setattr(worker_zero_mod, "runner_run", fake_runner)
    with pytest.raises(ProposalError, match="continuation request drifted"):
        execute_worker_zero_once(tmp_path, {"role": "worker"})


def test_original_receipt_drift(tmp_path: Path, accepted_ctx: dict[str, Any], monkeypatch):
    _setup_common(monkeypatch, accepted_ctx)
    monkeypatch.setattr(
        worker_zero_mod,
        "read_terminal_quota_record",
        lambda root, rid: {"request": {"request_id": "drifted"}, "response": {}},
    )

    def fake_runner(root, task_path, role, run_id, timeout, **kwargs):
        kwargs["pre_provider_check"]()
        return tmp_path

    monkeypatch.setattr(worker_zero_mod, "runner_run", fake_runner)
    with pytest.raises(ProposalError, match="original terminal receipt drifted"):
        execute_worker_zero_once(tmp_path, {"role": "worker"})


def test_artifacts_drift(tmp_path: Path, accepted_ctx: dict[str, Any], monkeypatch):
    _setup_common(monkeypatch, accepted_ctx)
    drifted_arts = copy.deepcopy(accepted_ctx["artifacts"])
    drifted_arts["semantic_task_fingerprint"] = "f" * 64
    monkeypatch.setattr(
        worker_zero_mod,
        "derive_worker_resume_artifacts",
        lambda adm, rec: drifted_arts,
    )

    def fake_runner(root, task_path, role, run_id, timeout, **kwargs):
        kwargs["pre_provider_check"]()
        return tmp_path

    monkeypatch.setattr(worker_zero_mod, "runner_run", fake_runner)
    with pytest.raises(ProposalError, match="worker resume artifacts drifted"):
        execute_worker_zero_once(tmp_path, {"role": "worker"})


def test_worker_index_not_zero(tmp_path: Path, accepted_ctx: dict[str, Any], monkeypatch):
    _setup_common(monkeypatch, accepted_ctx)
    drifted_arts = copy.deepcopy(accepted_ctx["artifacts"])
    drifted_arts["worker_index"] = 1
    monkeypatch.setattr(
        worker_zero_mod,
        "derive_worker_resume_artifacts",
        lambda adm, rec: drifted_arts,
    )

    def fake_runner(root, task_path, role, run_id, timeout, **kwargs):
        kwargs["pre_provider_check"]()
        return tmp_path

    monkeypatch.setattr(worker_zero_mod, "runner_run", fake_runner)
    with pytest.raises(ProposalError, match="worker_index must be 0"):
        execute_worker_zero_once(tmp_path, {"role": "worker"})


def test_check_state_drift_in_pre_provider_check(tmp_path: Path, accepted_ctx: dict[str, Any], monkeypatch):
    _setup_common(monkeypatch, accepted_ctx)
    state_call_count = 0

    def failing_check_state(*a, **kw):
        nonlocal state_call_count
        state_call_count += 1
        if state_call_count > 1:
            raise ProposalError("HEAD drift: expected head")

    monkeypatch.setattr(worker_zero_mod, "check_state", failing_check_state)
    simulated_calls = []

    def fake_runner(root, task_path, role, run_id, timeout, **kwargs):
        kwargs["pre_provider_check"]()
        simulated_calls.append(1)
        return tmp_path

    monkeypatch.setattr(worker_zero_mod, "runner_run", fake_runner)
    with pytest.raises(ProposalError, match="HEAD drift"):
        execute_worker_zero_once(tmp_path, {"role": "worker"})
    assert len(simulated_calls) == 0


def test_second_quota_one_runner_call(tmp_path: Path, accepted_ctx: dict[str, Any], monkeypatch):
    _setup_common(monkeypatch, accepted_ctx)
    run_dir = tmp_path / ".local/two-site-refactor/proposals/cont-run-worker-0"
    calls = []
    status_content = None

    def fake_runner(root, task_path, role, run_id, timeout, **kwargs):
        nonlocal status_content
        calls.append(run_id)
        if kwargs.get("pre_provider_check"):
            kwargs["pre_provider_check"]()
        run_dir.mkdir(parents=True, exist_ok=True)
        status_payload = {
            "run_id": run_id,
            "task_id": accepted_ctx["task"]["task_id"],
            "role": "worker",
            "head": accepted_ctx["source_head"],
            "model": WORKER_MODEL,
            "state": "BLOCKED",
            "conversation_id": "c1",
            "quota_evidence": {
                "kind": "QUOTA_RESET_UNKNOWN",
                "provider": "gemini",
                "model": WORKER_MODEL,
                "conversation_id": "c1",
                "reset_at_utc": None,
            },
        }
        status_path = run_dir / "status.json"
        status_content = json.dumps(status_payload).encode("utf-8")
        status_path.write_bytes(status_content)
        raise ProposalError("runner failed with quota error")

    monkeypatch.setattr(worker_zero_mod, "runner_run", fake_runner)
    with pytest.raises(QuotaBlockError) as exc_info:
        execute_worker_zero_once(tmp_path, {"role": "worker"})

    assert len(calls) == 1
    assert status_content is not None
    expected_sha256 = hashlib.sha256(status_content).hexdigest()
    assert exc_info.value.quota_attempt == {
        "role": "worker",
        "run_id": "cont-run-worker-0",
        "status_sha256": expected_sha256,
    }
    assert exc_info.value.quota_evidence == {
        "kind": "QUOTA_RESET_UNKNOWN",
        "provider": "gemini",
        "model": WORKER_MODEL,
        "conversation_id": "c1",
        "reset_at_utc": None,
    }

    with pytest.raises(ProposalError, match="already exists"):
        execute_worker_zero_once(tmp_path, {"role": "worker"})
    assert len(calls) == 1


def test_ambiguous_failure_no_retry(tmp_path: Path, accepted_ctx: dict[str, Any], monkeypatch):
    _setup_common(monkeypatch, accepted_ctx)
    calls = []

    def fake_runner(root, task_path, role, run_id, timeout, **kwargs):
        calls.append(run_id)
        if kwargs.get("pre_provider_check"):
            kwargs["pre_provider_check"]()
        raise ProposalError("ambiguous process termination")

    monkeypatch.setattr(worker_zero_mod, "runner_run", fake_runner)
    with pytest.raises(ProposalError, match="ambiguous process termination") as exc_info:
        execute_worker_zero_once(tmp_path, {"role": "worker"})
    assert not isinstance(exc_info.value, QuotaBlockError)
    assert len(calls) == 1


@pytest.mark.parametrize(
    ("override_key", "override_value"),
    [
        ("model", "wrong-model"),
        ("head", "wrong-head"),
        ("role", "verifier"),
        ("state", "RUNNING"),
    ],
)
def test_second_quota_negative_wrong_fields_not_upgraded(
    tmp_path: Path, accepted_ctx: dict[str, Any], monkeypatch, override_key: str, override_value: str
):
    _setup_common(monkeypatch, accepted_ctx)
    run_dir = tmp_path / ".local/two-site-refactor/proposals/cont-run-worker-0"
    calls = []

    def fake_runner(root, task_path, role, run_id, timeout, **kwargs):
        calls.append(run_id)
        if kwargs.get("pre_provider_check"):
            kwargs["pre_provider_check"]()
        run_dir.mkdir(parents=True, exist_ok=True)
        status_payload = {
            "run_id": run_id,
            "role": "worker",
            "head": accepted_ctx["source_head"],
            "model": WORKER_MODEL,
            "state": "BLOCKED",
            "conversation_id": "c1",
            "quota_evidence": {
                "kind": "QUOTA_RESET_UNKNOWN",
                "provider": "gemini",
                "model": WORKER_MODEL,
                "conversation_id": "c1",
                "reset_at_utc": None,
            },
        }
        status_payload[override_key] = override_value
        status_path = run_dir / "status.json"
        status_path.write_bytes(json.dumps(status_payload).encode("utf-8"))
        raise ProposalError("runner failed with invalid status")

    monkeypatch.setattr(worker_zero_mod, "runner_run", fake_runner)
    with pytest.raises(ProposalError, match="runner failed with invalid status") as exc_info:
        execute_worker_zero_once(tmp_path, {"role": "worker"})

    assert not isinstance(exc_info.value, QuotaBlockError)
    assert len(calls) == 1


@pytest.mark.parametrize("link_type", ["symlink", "hardlink"])
def test_second_quota_negative_linked_status_not_upgraded(
    tmp_path: Path, accepted_ctx: dict[str, Any], monkeypatch, link_type: str
):
    _setup_common(monkeypatch, accepted_ctx)
    run_dir = tmp_path / ".local/two-site-refactor/proposals/cont-run-worker-0"
    calls = []

    def fake_runner(root, task_path, role, run_id, timeout, **kwargs):
        calls.append(run_id)
        if kwargs.get("pre_provider_check"):
            kwargs["pre_provider_check"]()
        run_dir.mkdir(parents=True, exist_ok=True)
        status_payload = {
            "run_id": run_id,
            "role": "worker",
            "head": accepted_ctx["source_head"],
            "model": WORKER_MODEL,
            "state": "BLOCKED",
            "conversation_id": "c1",
            "quota_evidence": {
                "kind": "QUOTA_RESET_UNKNOWN",
                "provider": "gemini",
                "model": WORKER_MODEL,
                "conversation_id": "c1",
                "reset_at_utc": None,
            },
        }
        target_file = tmp_path / f"target_{link_type}.json"
        target_file.write_bytes(json.dumps(status_payload).encode("utf-8"))
        status_path = run_dir / "status.json"
        if link_type == "symlink":
            status_path.symlink_to(target_file)
        else:
            os.link(target_file, status_path)
        raise ProposalError(f"runner failed with {link_type} status")

    monkeypatch.setattr(worker_zero_mod, "runner_run", fake_runner)
    with pytest.raises(ProposalError, match=f"runner failed with {link_type} status") as exc_info:
        execute_worker_zero_once(tmp_path, {"role": "worker"})

    assert not isinstance(exc_info.value, QuotaBlockError)
    assert len(calls) == 1


def test_invalid_run_dir_symlink_or_mismatch(tmp_path: Path, accepted_ctx: dict[str, Any], monkeypatch):
    _setup_common(monkeypatch, accepted_ctx)
    real_dir = tmp_path / "real_dir"
    real_dir.mkdir(parents=True, exist_ok=True)
    symlink_dir = tmp_path / ".local/two-site-refactor/proposals/cont-run-worker-0"

    def fake_runner_symlink(*a, **kw):
        symlink_dir.parent.mkdir(parents=True, exist_ok=True)
        symlink_dir.symlink_to(real_dir)
        return symlink_dir

    monkeypatch.setattr(worker_zero_mod, "runner_run", fake_runner_symlink)
    with pytest.raises(ProposalError, match="run_dir cannot be a symlink"):
        execute_worker_zero_once(tmp_path, {"role": "worker"})

    symlink_dir.unlink()
    wrong_dir = tmp_path / "wrong_dir"
    wrong_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(worker_zero_mod, "runner_run", lambda *a, **kw: wrong_dir)
    with pytest.raises(ProposalError, match="run_dir mismatch"):
        execute_worker_zero_once(tmp_path, {"role": "worker"})

    monkeypatch.setattr(worker_zero_mod, "runner_run", lambda *a, **kw: "not-a-path")
    with pytest.raises(ProposalError, match="run_dir must be Path"):
        execute_worker_zero_once(tmp_path, {"role": "worker"})
