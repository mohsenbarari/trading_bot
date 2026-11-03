"""Known-reset Worker correction history retains the actual Qoder finding owner."""
from __future__ import annotations

import copy
import json

import pytest

from scripts import two_site_proposal_pipeline as pipeline
from scripts import two_site_proposal_runner as runner
from scripts import two_site_qoder_review as qoder_review
from scripts import two_site_quota_resume_worker_evidence as evidence_reader
from scripts import two_site_quota_resume_worker_two_history_complete as complete_reader
from scripts.two_site_quota_resume_worker_artifacts import derive_worker_resume_artifacts
from tests.test_two_site_pipeline_resume import HEAD, install_provider, scenario
from tests.test_two_site_quota_resume_pipeline import _build_valid_pair, _canonical_sha256


@pytest.fixture(params=[1, 2])
def qoder_worker_quota(scenario, monkeypatch, request):
    root, task_path, task, sources, state, native_calls = scenario
    index = request.param
    prefix = "qoder-worker-quota"
    calls, _ = install_provider(scenario, monkeypatch, fail_count=0, responses=[
        {"content": "# initial\n"}, {"status": "VERIFIED"},
        {"content": "# corrected once\n"}, {"status": "VERIFIED"},
    ])
    original = pipeline.runner_run
    quota = {"kind": "QUOTA_RESET_KNOWN", "provider": "gemini", "model": runner.WORKER_MODEL,
             "conversation_id": "quota-only-session", "reset_at_utc": "2026-11-04T12:00:00Z"}
    accepted_receipts = {}

    def provider(root, path, role, run_id, timeout, **kwargs):
        if run_id != f"{prefix}-worker-{index}":
            return original(root, path, role, run_id, timeout, **kwargs)
        calls.append((role, run_id))
        directory = root / ".local/two-site-refactor/proposals" / run_id
        directory.mkdir(mode=0o700)
        policy = runner.configured_gemini_roles(root)
        runner.write_json(directory / "status.json", {
            "run_id": run_id, "task_id": task["task_id"], "role": "worker", "head": HEAD,
            "model": policy["worker"]["model"], "effort": policy["worker"]["reasoning_effort"],
            "state": "BLOCKED", "conversation_id": quota["conversation_id"], "quota_evidence": quota,
            "model_config": policy, "model_config_sha256": runner.sha(runner.serialize_json(policy)),
        })
        raise pipeline.ProposalError("provider quota observed; no replay authority")

    def final_review(root, path, worker, reviewer, sha, *, timeout):
        number = len(accepted_receipts)
        receipt = {"bindings": {"worker_run_id": worker, "reviewer_run_id": reviewer,
            "proposal_sha256": sha, "source_head": HEAD},
            "result": {"decision": "REQUEST_CHANGES", "findings": [f"exact missing assertion {number}"],
                       "proposal_sha256": sha, "source_head": HEAD},
            "proof": {"session_id": f"independent-qoder-{number}"}}
        directory = root / qoder_review.REVIEW_REL
        directory.mkdir(parents=True, exist_ok=True)
        runner.write_json(directory / (sha + ".json"), receipt)
        accepted_receipts[sha] = copy.deepcopy(receipt)
        return receipt

    monkeypatch.setattr(pipeline, "runner_run", provider)
    with pytest.raises(pipeline.QuotaBlockError) as caught:
        pipeline.run_pipeline(root, task_path, prefix, 60, None, state, final_review_hook=final_review)
    assert caught.value.quota_evidence == quota and len(native_calls) == index
    assert caught.value.quota_attempt["run_id"] == f"{prefix}-worker-{index}"

    admitted, terminal, _old_state = _build_valid_pair()
    admitted.update(role="worker", task_id=task["task_id"], source_head=HEAD,
                    semantic_task_fingerprint=state["semantic_task_fingerprint"])
    terminal["request"].update(run_prefix=prefix, source_head=HEAD)
    blocked = {**state, "quota_evidence": quota, "quota_attempt": caught.value.quota_attempt,
               "blockers": ["observational quota, no replay authority"]}
    terminal["response"].update(run_prefix=prefix, source_head=HEAD, stdout=json.dumps(blocked))
    terminal["quota_evidence"] = quota
    terminal["request_sha256"] = _canonical_sha256(terminal["request"])
    terminal["response_sha256"] = _canonical_sha256(terminal["response"])
    artifacts = derive_worker_resume_artifacts(admitted, terminal)
    assert artifacts["worker_index"] == index and artifacts["correction_origin"] == "REVIEWER"

    def validate(root, path, worker, reviewer, sha, *, expected_source_head, require_approve):
        # Only the model-independent full-receipt verifier is stubbed. The original
        # Gemini Worker/Reviewer generation proofs and native receipts remain real fixtures.
        assert require_approve is False and expected_source_head == HEAD
        expected = accepted_receipts[sha]
        assert expected["bindings"]["worker_run_id"] == worker
        assert expected["bindings"]["reviewer_run_id"] == reviewer
        return copy.deepcopy(expected)

    monkeypatch.setattr(qoder_review, "validate_review_receipt", validate)
    monkeypatch.setattr(evidence_reader, "load_task", pipeline.load_task)
    monkeypatch.setattr(complete_reader, "load_task", pipeline.load_task)
    return root, task_path, prefix, index, artifacts, state, calls


def test_known_reset_worker_one_and_two_load_qoder_without_relabeling(qoder_worker_quota):
    root, path, prefix, index, artifacts, state, calls = qoder_worker_quota
    before = list(calls)
    evidence = evidence_reader.load_worker_correction_evidence(root, path, artifacts)
    assert evidence["feedback"]["reviewer"] == "qoder"
    assert evidence["feedback"]["findings"] == evidence["qoder_review"]["result"]["findings"]
    assert evidence["reviewer_result"]["status"] == evidence["reviewer_status"]["state"] == "VERIFIED"
    assert evidence["reviewer_result"]["findings"] == []
    assert state["correction_count"] == index - 1 and evidence["artifacts"] == artifacts
    if index == 2:
        history = complete_reader.complete_worker_two_history(root, path, artifacts, evidence)
        assert history["feedback1_kind"] == "qoder"
        assert history["reviewer0_result"]["status"] == "VERIFIED"
        assert history["qoder_review0"]["result"]["decision"] == "REQUEST_CHANGES"
        assert history["qoder_review1"]["result"]["decision"] == "REQUEST_CHANGES"
        assert history["ordered_conversation_ids"] == [f"conv-{prefix}-worker-0", f"conv-{prefix}-reviewer-0",
            "independent-qoder-0", f"conv-{prefix}-worker-1", f"conv-{prefix}-reviewer-1", "independent-qoder-1"]
    assert calls == before  # Reading history never replays a completed phase.


def test_resumed_worker_one_preserves_qoder_findings_and_session_history(qoder_worker_quota):
    root, path, _prefix, index, artifacts, _state, _calls = qoder_worker_quota
    if index != 1:
        return  # Worker2's terminal history is exercised by the physical test above.
    from tests.test_two_site_quota_resume_worker_one_result_contract import _make_valid_bundle
    from scripts.two_site_quota_resume_worker_one_result_contract import bind_worker_one_result
    evidence = evidence_reader.load_worker_correction_evidence(root, path, artifacts)
    ctx, returned, validated = _make_valid_bundle()
    ctx.update(task=json.loads(path.read_text()), source_head=HEAD, artifacts=artifacts, evidence=evidence)
    returned.update(task_id=ctx["task"]["task_id"], source_head=HEAD,
                    semantic_task_fingerprint=artifacts["semantic_task_fingerprint"])
    bound = bind_worker_one_result(ctx, returned, validated)
    assert bound["seen_norm_findings"] == {"exact missing assertion 0"}
    assert "independent-qoder-0" in bound["seen_convs"]
    assert evidence["reviewer_result"]["status"] == "VERIFIED"


@pytest.mark.parametrize("bad", ["history", "receipt", "feedback", "native"])
def test_qoder_worker_quota_rejects_tampered_provenance(qoder_worker_quota, bad):
    root, path, prefix, index, artifacts, _state, calls = qoder_worker_quota
    if bad == "history":
        target = root / ".local/two-site-refactor/pipeline" / prefix / f"qoder-review-{index - 1}.json"
        value = json.loads(target.read_text()); value["decision"] = "APPROVE"; runner.write_json(target, value)
    elif bad == "receipt":
        target = root / qoder_review.REVIEW_REL / (artifacts["proposal_sha256"] + ".json")
        value = json.loads(target.read_text()); value["result"]["findings"] = ["different owner"]; runner.write_json(target, value)
    elif bad == "feedback":
        target = root / artifacts["expected_feedback_path"]
        value = json.loads(target.read_text()); value["findings"] = ["unbound finding"]; runner.write_json(target, value)
    else:
        target = root / artifacts["pre_review_test_path"]
        target.write_bytes(target.read_bytes() + b"\n")
    before = list(calls)
    with pytest.raises((pipeline.ProposalError, ValueError)):
        evidence_reader.load_worker_correction_evidence(root, path, artifacts)
    assert calls == before


def test_trusted_quota_remainder_policy_selects_hook_without_calling_it(monkeypatch):
    calls = []
    def review(*args, **kwargs):
        calls.append(1)
    monkeypatch.setattr(qoder_review, "review_proposal", review)
    monkeypatch.delenv("TWO_SITE_QODER_FINAL_REVIEW_REQUIRED", raising=False)
    assert pipeline.trusted_qoder_final_review_hook() is None
    monkeypatch.setenv("TWO_SITE_QODER_FINAL_REVIEW_REQUIRED", "1")
    assert pipeline.trusted_qoder_final_review_hook() is review
    assert calls == []
