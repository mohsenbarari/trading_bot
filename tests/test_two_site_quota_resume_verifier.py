"""Tests for one-shot verifier quota continuation execution."""

from __future__ import annotations

from contextlib import contextmanager
import copy
from pathlib import Path
from typing import Any
import pytest

from scripts.two_site_proposal_pipeline import ProposalError, QuotaBlockError
from scripts.two_site_quota_continuation_admission import ContinuationAdmissionError
import scripts.two_site_quota_resume_verifier as verifier_mod
from scripts.two_site_quota_resume_verifier import execute_verifier_once


@pytest.fixture
def accepted_ctx(tmp_path: Path) -> dict[str, Any]:
    task_p = tmp_path / "task.json"
    task_p.write_text("{}", encoding="utf-8")
    return {
        "continuation_run_prefix": "cont-run",
        "original_run_prefix": "orig-run",
        "worker_run_id": "orig-run-worker-0",
        "final_worker_run_id": "orig-run-worker-0",
        "proposal_sha256": "a" * 64,
        "proposal_path": ".local/two-site-refactor/proposals/orig-run-worker-0/result.json",
        "pre_review_test_sha256": "b" * 64,
        "pre_review_test_path": ".local/two-site-refactor/pipeline/orig-run/pre-review-tests-0.json",
        "test_receipt_sha256": "b" * 64,
        "test_receipt_path": ".local/two-site-refactor/pipeline/orig-run/pre-review-tests-0.json",
        "source_head": "c" * 40,
        "original_receipt": {"request": {"request_id": "req-1"}, "response": {}},
        "original_terminal_receipt": {"request": {"request_id": "req-1"}, "response": {}},
        "admitted_request": {
            "schema_version": "1.0",
            "request_type": "QUOTA_CONTINUATION",
            "request_id": "cont-1",
            "original_request_id": "req-1",
            "run_prefix": "cont-run",
            "role": "verifier",
            "timeout": 120,
        },
        "admitted_task_path": task_p,
        "artifacts": {
            "original_run_prefix": "orig-run",
            "correction_count": 0,
            "final_worker_run_id": "orig-run-worker-0",
            "proposal_sha256": "a" * 64,
            "proposal_path": ".local/two-site-refactor/proposals/orig-run-worker-0/result.json",
            "pre_review_test_path": ".local/two-site-refactor/pipeline/orig-run/pre-review-tests-0.json",
            "pre_review_test_sha256": "b" * 64,
            "pre_review_test_state": "PASSED",
            "task_id": "task-1",
            "source_head": "c" * 40,
            "semantic_task_fingerprint": "d" * 64,
        },
        "validated_test_receipt": {"state": "PASSED", "selectors": ["test_sample.py"]},
        "validated_worker": {
            "result": {"status": "PROPOSAL_READY", "changes": [{"path": "pkg/mod.py"}]},
            "status": {"conversation_id": "worker-conv"},
            "result_sha": "a" * 64,
            "result_text": "{}",
            "conversation_id": "worker-conv",
        },
        "task": {"task_id": "task-1"},
        "sources": {},
    }


def _setup_common(monkeypatch, ctx: dict[str, Any]):
    @contextmanager
    def fake_ctx(root, req):
        yield ctx

    monkeypatch.setattr(verifier_mod, "verifier_continuation_context", fake_ctx)
    monkeypatch.setattr(verifier_mod, "ref_snapshot", lambda root: {"ref": "hash"})
    monkeypatch.setattr(verifier_mod, "check_state", lambda *a, **kw: None)
    monkeypatch.setattr(
        verifier_mod,
        "validate_quota_continuation_request",
        lambda root, req: (copy.deepcopy(ctx["admitted_request"]), ctx["admitted_task_path"], None),
    )
    monkeypatch.setattr(
        verifier_mod, "read_terminal_quota_record", lambda root, rid: copy.deepcopy(ctx["original_terminal_receipt"])
    )
    monkeypatch.setattr(
        verifier_mod, "_load_verifier_pre_review_receipt", lambda root, arts: copy.deepcopy(ctx["validated_test_receipt"])
    )
    monkeypatch.setattr(
        verifier_mod, "load_prior_worker_chain", lambda root, tp, arts: copy.deepcopy(ctx["validated_worker"])
    )


def test_verified_result(tmp_path: Path, accepted_ctx: dict[str, Any], monkeypatch):
    _setup_common(monkeypatch, accepted_ctx)
    run_dir = tmp_path / ".local/two-site-refactor/proposals/cont-run-reviewer-0"
    calls = []

    def fake_runner(root, task_path, role, run_id, timeout, **kwargs):
        calls.append(run_id)
        if kwargs.get("pre_provider_check"):
            kwargs["pre_provider_check"]()
        run_dir.mkdir(parents=True, exist_ok=True)
        return run_dir

    monkeypatch.setattr(verifier_mod, "runner_run", fake_runner)
    monkeypatch.setattr(
        verifier_mod,
        "validate_run",
        lambda **kw: ({"status": "VERIFIED"}, {"conversation_id": "rev-conv"}, "res_sha", "res_txt"),
    )
    res = execute_verifier_once(tmp_path, {"role": "verifier"})
    assert calls == ["cont-run-reviewer-0"]
    assert res["state"] == "READY_FOR_CODEX"
    assert res["blockers"] == []
    assert res["final_reviewer_run_id"] == "cont-run-reviewer-0"
    assert res["pre_review_test_path"] == accepted_ctx["pre_review_test_path"]
    assert res["pre_review_test_sha256"] == accepted_ctx["pre_review_test_sha256"]
    assert res["changed_paths"] == ["pkg/mod.py"]


def test_changes_required_result(tmp_path: Path, accepted_ctx: dict[str, Any], monkeypatch):
    _setup_common(monkeypatch, accepted_ctx)
    run_dir = tmp_path / ".local/two-site-refactor/proposals/cont-run-reviewer-0"

    def fake_runner(root, task_path, role, run_id, timeout, **kwargs):
        if kwargs.get("pre_provider_check"):
            kwargs["pre_provider_check"]()
        run_dir.mkdir(parents=True, exist_ok=True)
        return run_dir

    monkeypatch.setattr(verifier_mod, "runner_run", fake_runner)
    monkeypatch.setattr(
        verifier_mod,
        "validate_run",
        lambda **kw: ({"status": "CHANGES_REQUIRED", "findings": ["bad"]}, {"conversation_id": "rev-c"}, "sha", "txt"),
    )
    res = execute_verifier_once(tmp_path, {"role": "verifier"})
    assert res["state"] == "TASK_BLOCKED"
    assert res["blockers"] == ["correction required"]


def test_preexisting_run_dir(tmp_path: Path, accepted_ctx: dict[str, Any], monkeypatch):
    _setup_common(monkeypatch, accepted_ctx)
    (tmp_path / ".local/two-site-refactor/proposals/cont-run-reviewer-0").mkdir(parents=True, exist_ok=True)
    calls = []
    monkeypatch.setattr(verifier_mod, "runner_run", lambda *a, **kw: calls.append(1))
    with pytest.raises(ProposalError, match="already exists"):
        execute_verifier_once(tmp_path, {"role": "verifier"})
    assert len(calls) == 0


def test_strict_admission_stop(tmp_path: Path, accepted_ctx: dict[str, Any], monkeypatch):
    _setup_common(monkeypatch, accepted_ctx)

    def fake_admission(root, req):
        raise ContinuationAdmissionError("STOP sentinel detected at .local/two-site-refactor/autopilot/STOP")

    monkeypatch.setattr(verifier_mod, "validate_quota_continuation_request", fake_admission)
    simulated_popen = []

    def fake_runner(root, task_path, role, run_id, timeout, **kwargs):
        kwargs["pre_provider_check"]()
        simulated_popen.append(1)
        return tmp_path

    monkeypatch.setattr(verifier_mod, "runner_run", fake_runner)
    with pytest.raises(ContinuationAdmissionError, match="STOP sentinel detected"):
        execute_verifier_once(tmp_path, {"role": "verifier"})
    assert len(simulated_popen) == 0


def test_strict_admission_drift(tmp_path: Path, accepted_ctx: dict[str, Any], monkeypatch):
    _setup_common(monkeypatch, accepted_ctx)
    drifted_req = copy.deepcopy(accepted_ctx["admitted_request"])
    drifted_req["timeout"] = 999
    monkeypatch.setattr(
        verifier_mod,
        "validate_quota_continuation_request",
        lambda root, req: (drifted_req, accepted_ctx["admitted_task_path"], None),
    )

    def fake_runner(root, task_path, role, run_id, timeout, **kwargs):
        kwargs["pre_provider_check"]()
        return tmp_path

    monkeypatch.setattr(verifier_mod, "runner_run", fake_runner)
    with pytest.raises(ProposalError, match="continuation request drifted"):
        execute_verifier_once(tmp_path, {"role": "verifier"})


def test_original_evidence_drift(tmp_path: Path, accepted_ctx: dict[str, Any], monkeypatch):
    _setup_common(monkeypatch, accepted_ctx)
    monkeypatch.setattr(
        verifier_mod,
        "_load_verifier_pre_review_receipt",
        lambda root, arts: {"state": "FAILED", "selectors": []},
    )

    def fake_runner(root, task_path, role, run_id, timeout, **kwargs):
        kwargs["pre_provider_check"]()
        return tmp_path

    monkeypatch.setattr(verifier_mod, "runner_run", fake_runner)
    with pytest.raises(ProposalError, match="pre-review test receipt drifted"):
        execute_verifier_once(tmp_path, {"role": "verifier"})


def test_second_quota_one_runner_call(tmp_path: Path, accepted_ctx: dict[str, Any], monkeypatch):
    _setup_common(monkeypatch, accepted_ctx)
    calls = []

    def fake_runner(root, task_path, role, run_id, timeout, **kwargs):
        calls.append(run_id)
        if kwargs.get("pre_provider_check"):
            kwargs["pre_provider_check"]()
        raise QuotaBlockError(
            {"kind": "QUOTA_RESET_UNKNOWN", "provider": "gemini", "model": "gemini-2.5-pro", "conversation_id": "c1", "reset_at_utc": None}
        )

    monkeypatch.setattr(verifier_mod, "runner_run", fake_runner)
    with pytest.raises(QuotaBlockError):
        execute_verifier_once(tmp_path, {"role": "verifier"})
    assert len(calls) == 1
