"""Finite extra-Reviewer IDs preserve quota lineage and successful phases."""
from __future__ import annotations

import copy
import json

import pytest

from scripts import two_site_proposal_pipeline as pipeline
from scripts import two_site_proposal_runner as runner
from scripts.two_site_quota_resume_worker_paths import validate_prior_worker_paths
from scripts.two_site_quota_resume_worker_two_history_base import derive_worker_two_history_base
from scripts.two_site_quota_ready_payload import validate_quota_ready_payload
from scripts.two_site_quota_continuation_response import build_continuation_broker_response
from scripts.two_site_quota_continuation_terminal_record import build_terminal_continuation_record
from tests.test_two_site_pipeline_resume import HEAD, install_provider, scenario
from tests.test_two_site_quota_resume_pipeline import _build_valid_pair, _canonical_sha256
from tests.test_two_site_quota_ready_payload import _make_ready_fixture, _make_ready_payload
from tests.test_two_site_quota_resume_worker_two_history_base import _create_valid_fixtures


@pytest.mark.parametrize("correction", [0, 1, 2])
@pytest.mark.parametrize("batch,retry", [(1, ""), (2, "-retry-2")])
def test_extra_quota_artifacts_keep_worker_correction_index(correction, batch, retry):
    request, receipt, state = _build_valid_pair(correction_count=correction)
    prefix = receipt["request"]["run_prefix"]
    state["quota_attempt"]["run_id"] = f"{prefix}-v{correction}r{batch}{retry}"
    receipt["response"]["stdout"] = json.dumps(state)
    receipt["response_sha256"] = _canonical_sha256(receipt["response"])
    before = copy.deepcopy(receipt)
    artifacts = pipeline.derive_verifier_resume_artifacts(request, receipt)
    assert artifacts["correction_count"] == correction
    assert artifacts["final_worker_run_id"] == f"{prefix}-worker-{correction}"
    assert artifacts["verifier_run_id"] == state["quota_attempt"]["run_id"]
    assert artifacts["pre_review_test_path"] == state["pre_review_test_path"]
    assert receipt == before


@pytest.mark.parametrize("suffix", ["-v1r1", "-v3r1", "-v0r0", "-v0r3", "-v0r1-retry-3", "-v0r1-other"])
def test_malformed_or_wrong_correction_extra_ids_are_not_admitted(suffix):
    request, receipt, state = _build_valid_pair(correction_count=0)
    state["quota_attempt"]["run_id"] = receipt["request"]["run_prefix"] + suffix
    receipt["response"]["stdout"] = json.dumps(state)
    with pytest.raises(pipeline.ProposalError):
        pipeline.derive_verifier_resume_artifacts(request, receipt)


def test_real_extra_reviewer_quota_preserves_worker_native_and_failed_batch(scenario, monkeypatch):
    root, task_path, task, sources, state, tests = scenario
    calls, failed = install_provider(scenario, monkeypatch)
    original = pipeline.runner_run
    prefix = "run-extra-quota"
    quota = {"kind": "QUOTA_RESET_KNOWN", "provider": "gemini", "model": runner.MODEL,
             "conversation_id": "extra-quota-cid", "reset_at_utc": "2026-11-04T12:00:00Z"}

    def provider(root, path, role, run_id, timeout, **kwargs):
        if run_id != f"{prefix}-v0r1":
            return original(root, path, role, run_id, timeout, **kwargs)
        calls.append((role, run_id))
        directory = root / ".local/two-site-refactor/proposals" / run_id
        directory.mkdir(mode=0o700)
        policy = runner.configured_gemini_roles(root)
        runner.write_json(directory / "status.json", {
            "run_id": run_id, "task_id": task["task_id"], "role": "verifier", "head": HEAD,
            "model": policy["reviewer"]["model"], "effort": policy["reviewer"]["reasoning_effort"], "state": "BLOCKED",
            "conversation_id": quota["conversation_id"], "quota_evidence": quota,
            "model_config": policy, "model_config_sha256": runner.sha(runner.serialize_json(policy)),
        })
        raise pipeline.ProposalError("provider quota observed; no replay authority")

    monkeypatch.setattr(pipeline, "runner_run", provider)
    with pytest.raises(pipeline.QuotaBlockError) as caught:
        pipeline.run_pipeline(root, task_path, prefix, 60, None, state)
    assert caught.value.quota_attempt["run_id"] == f"{prefix}-v0r1"
    assert caught.value.quota_evidence == quota
    assert state["final_worker_run_id"] == f"{prefix}-worker-0"
    assert state["pre_review_test_state"] == "PASSED" and len(tests) == 1
    assert sum(role == "worker" for role, _ in calls) == 1 and len(failed) == 3
    from scripts import two_site_provider_broker as broker
    _admitted, terminal, _payload = _build_valid_pair()
    actual_task = root / ".local/two-site-refactor/tasks/extra-quota.json"
    actual_task.parent.mkdir(parents=True)
    actual_task.write_bytes(task_path.read_bytes())
    request = terminal["request"]
    request.update(run_prefix=prefix, source_head=HEAD, task_path=actual_task.relative_to(root).as_posix(),
                   task_sha256=runner.file_sha256(actual_task))
    blocked = {**state, "quota_evidence": caught.value.quota_evidence, "quota_attempt": caught.value.quota_attempt,
               "blockers": ["observational quota, no replay authority"]}
    response = terminal["response"]
    response.update(run_prefix=prefix, source_head=HEAD, stdout=json.dumps(blocked))
    assert set(blocked) == broker.PIPELINE_QUOTA_KEYS
    assert broker.detect_quota_response(root, request, response) == quota
    prior = root / ".local/two-site-refactor/pipeline" / prefix / "reviewer-phase-0-batch-0.json"
    history = json.loads(prior.read_text())
    assert history["resume_batches_used"] == 0 and history["remaining_resume_batches"] == 2
    for item in history["failed_attempts"]:
        directory = root / ".local/two-site-refactor/proposals" / item["run_id"]
        assert runner.file_sha256(directory / "stream.jsonl") == item["stream_sha256"]
        assert runner.file_sha256(directory / "status.json") == item["status_sha256"]
    artifacts = {"original_run_prefix": prefix, "correction_count": 0, "task_id": task["task_id"],
        "source_head": HEAD, "final_worker_run_id": state["final_worker_run_id"],
        "proposal_path": state["proposal_path"], "proposal_sha256": state["proposal_sha256"],
        "pre_review_test_path": state["pre_review_test_path"], "pre_review_test_sha256": state["pre_review_test_sha256"],
        "pre_review_test_state": "PASSED"}
    # The existing quota readers select the same complete proof and native receipt.
    from scripts.two_site_quota_resume_worker_reader import load_prior_worker_chain
    worker = load_prior_worker_chain(root, task_path, artifacts)
    native = pipeline.load_verifier_pre_review_receipt(root, artifacts)
    assert worker["status"]["run_id"] == state["final_worker_run_id"]
    assert native["proposal_sha256"] == state["proposal_sha256"]


@pytest.mark.parametrize("attempted", [1, 2])
@pytest.mark.parametrize("batch", [1, 2])
def test_worker_history_paths_keep_same_prior_index(attempted, batch):
    index = attempted - 1
    paths = validate_prior_worker_paths("original", attempted, f"original-worker-{index}", f"original-v{index}r{batch}-retry-2")
    assert paths["reviewer_path"].endswith(f"original-v{index}r{batch}-retry-2/result.json")
    assert paths["pre_review_test_path"].endswith(f"pre-review-tests-{index}.json")


def test_worker_two_history_accepts_exact_extra_reviewer_one(tmp_path):
    artifacts, evidence, _w0, _w1 = _create_valid_fixtures(tmp_path, reviewer_kind="gemini")
    reviewer = artifacts["original_run_prefix"] + "-v1r2-retry-1"
    artifacts["final_reviewer_run_id"] = reviewer
    evidence["reviewer_status"]["run_id"] = reviewer
    history = derive_worker_two_history_base(tmp_path, artifacts, evidence)
    assert history["reviewer1_run_id"] == reviewer and history["needs_reviewer0"] is True


def test_worker_two_history_finds_the_real_extra_reviewer_zero(tmp_path, monkeypatch):
    from scripts import two_site_quota_resume_worker_two_history_complete as history
    from tests.test_two_site_quota_resume_worker_two_history_complete_smoke import _make_base_dict, _make_artifacts
    prefix = "original"
    monkeypatch.setattr(history, "derive_worker_two_history_base", lambda *a: _make_base_dict(prefix, "gemini", True))
    monkeypatch.setattr(history, "load_prior_worker_zero", lambda *a: {"conversation_id": "conv-w0", "result_sha": "0"*64})
    monkeypatch.setattr(history, "_load_verifier_pre_review_receipt", lambda *a: {"state": "PASSED", "proposal_sha256": "0"*64})
    monkeypatch.setattr(history, "load_task", lambda *a: ({"task_id": "task-sample"}, {}))
    monkeypatch.setattr(history, "validate_bound_prior_reviewer", lambda *a, **k: (
        {"findings": ["finding-alpha"], "status": "CHANGES_REQUIRED"},
        {"conversation_id": "conv-r0", "state": "CHANGES_REQUIRED"}, "r0_sha"*8))
    directory = tmp_path / ".local/two-site-refactor/pipeline" / prefix
    directory.mkdir(parents=True)
    (directory / "pre-review-tests-0.json").write_text("{}")
    reviewer = prefix + "-v0r2-retry-1"
    output = tmp_path / ".local/two-site-refactor/proposals" / reviewer
    output.mkdir(parents=True)
    (output / "result.json").write_text("{}")
    result = history.complete_worker_two_history(tmp_path, "task.json", _make_artifacts(prefix), {})
    assert result["reviewer0_run_id"] == reviewer
    assert result["ordered_conversation_ids"] == ["conv-w0", "conv-r0", "conv-w1", "conv-r1"]


@pytest.mark.parametrize("correction", [0, 1, 2])
def test_ready_payload_keeps_exact_continuation_prefix_and_index(correction):
    _old, intent, request, task_id, allowed = _make_ready_fixture(correction_count=correction)
    payload = _make_ready_payload(request, correction_count=correction)
    reviewer = request["run_prefix"] + f"-v{correction}r2-retry-1"
    payload.update(final_reviewer_run_id=reviewer, reviewer_path=f".local/two-site-refactor/proposals/{reviewer}/result.json")
    response = build_continuation_broker_response(request, payload)
    record = build_terminal_continuation_record(request, response, intent)
    result = validate_quota_ready_payload(record, intent, request, task_id, allowed)
    assert result["final_reviewer_run_id"] == reviewer and result["correction_count"] == correction
