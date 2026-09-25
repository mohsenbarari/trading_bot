from __future__ import annotations

import json
import pytest

from scripts.two_site_proposal_pipeline import ProposalError
from scripts.two_site_quota_resume_worker_artifacts import (
    derive_worker_resume_artifacts,
    derive_worker_zero_resume_artifacts,
)
from tests.test_two_site_quota_resume_pipeline import _build_valid_pair


def _build_worker_zero_fixtures(*, worker_retry: str = ""):
    admitted_request, receipt, pipeline_state = _build_valid_pair()
    admitted_request["role"] = "worker"
    orig_prefix = receipt["request"]["run_prefix"]
    worker_run_id = f"{orig_prefix}-worker-0{worker_retry}"

    pipeline_state["correction_count"] = 0
    pipeline_state["final_worker_run_id"] = None
    pipeline_state["final_reviewer_run_id"] = None
    pipeline_state["proposal_sha256"] = None
    pipeline_state["proposal_path"] = None
    pipeline_state["reviewer_path"] = None
    pipeline_state["pre_review_test_path"] = None
    pipeline_state["pre_review_test_sha256"] = None
    pipeline_state["pre_review_test_state"] = None
    pipeline_state["changed_paths"] = []
    status_sha = pipeline_state["quota_attempt"]["status_sha256"]
    pipeline_state["quota_attempt"] = {
        "role": "worker",
        "run_id": worker_run_id,
        "status_sha256": status_sha,
    }
    receipt["response"]["stdout"] = json.dumps(pipeline_state)
    return admitted_request, receipt, pipeline_state


def test_worker_zero_derivation_success_and_retry():
    admitted_request, receipt, _ = _build_worker_zero_fixtures()
    artifacts = derive_worker_zero_resume_artifacts(admitted_request, receipt)

    orig_prefix = receipt["request"]["run_prefix"]
    assert artifacts["task_id"] == admitted_request["task_id"]
    assert artifacts["semantic_task_fingerprint"] == admitted_request["semantic_task_fingerprint"]
    assert artifacts["source_head"] == admitted_request["source_head"]
    assert artifacts["original_run_prefix"] == orig_prefix
    assert artifacts["attempted_run_id"] == f"{orig_prefix}-worker-0"
    assert len(artifacts["status_sha256"]) == 64
    assert artifacts["status_sha"] == artifacts["status_sha256"]
    assert artifacts["quota_evidence"] == receipt["quota_evidence"]
    assert artifacts["worker_index"] == 0

    for retry_tag in ("-retry-1", "-retry-2"):
        req_r, rec_r, _ = _build_worker_zero_fixtures(worker_retry=retry_tag)
        res_r = derive_worker_zero_resume_artifacts(req_r, rec_r)
        assert res_r["attempted_run_id"] == f"{orig_prefix}-worker-0{retry_tag}"


def test_worker_zero_immutability():
    admitted_request, receipt, _ = _build_worker_zero_fixtures()
    artifacts = derive_worker_zero_resume_artifacts(admitted_request, receipt)
    original_provider = receipt["quota_evidence"]["provider"]
    artifacts["quota_evidence"]["provider"] = "mutated-provider-value"
    assert receipt["quota_evidence"]["provider"] == original_provider


def test_worker_zero_lineage_and_prefix_mismatch():
    admitted_request, receipt, _ = _build_worker_zero_fixtures()
    receipt["request"]["task_path"] = "corrupted/task/path"
    with pytest.raises(ProposalError, match="lineage field 'task_path' mismatch"):
        derive_worker_zero_resume_artifacts(admitted_request, receipt)

    admitted_request, receipt, _ = _build_worker_zero_fixtures()
    receipt["request"]["source_head"] = "0" * 40
    with pytest.raises(ProposalError, match="lineage field 'source_head' mismatch"):
        derive_worker_zero_resume_artifacts(admitted_request, receipt)

    admitted_request, receipt, _ = _build_worker_zero_fixtures()
    receipt["request"]["run_prefix"] = "invalid prefix with spaces"
    with pytest.raises(ProposalError, match="run_prefix invalid"):
        derive_worker_zero_resume_artifacts(admitted_request, receipt)


def test_worker_zero_role_and_attempt_validation():
    admitted_request, receipt, _ = _build_worker_zero_fixtures()
    admitted_request["role"] = "verifier"
    with pytest.raises(ProposalError, match="admitted_request role must be 'worker'"):
        derive_worker_zero_resume_artifacts(admitted_request, receipt)

    admitted_request, receipt, state = _build_worker_zero_fixtures()
    state["quota_attempt"]["role"] = "verifier"
    receipt["response"]["stdout"] = json.dumps(state)
    with pytest.raises(ProposalError, match="quota_attempt.role must be 'worker'"):
        derive_worker_zero_resume_artifacts(admitted_request, receipt)

    orig_prefix = receipt["request"]["run_prefix"]
    for bad_id in (
        f"{orig_prefix}-worker-1",
        f"{orig_prefix}-reviewer-0",
        f"{orig_prefix}-worker-0-retry-3",
        "completely-wrong-run-id",
    ):
        admitted_request, receipt, state = _build_worker_zero_fixtures()
        state["quota_attempt"]["run_id"] = bad_id
        receipt["response"]["stdout"] = json.dumps(state)
        with pytest.raises(ProposalError, match="does not match original run_prefix"):
            derive_worker_zero_resume_artifacts(admitted_request, receipt)


def test_worker_zero_pipeline_state_and_prior_artifacts():
    admitted_request, receipt, state = _build_worker_zero_fixtures()
    state["correction_count"] = 1
    receipt["response"]["stdout"] = json.dumps(state)
    with pytest.raises(ProposalError, match="correction_count must be exact int 0"):
        derive_worker_zero_resume_artifacts(admitted_request, receipt)

    admitted_request, receipt, state = _build_worker_zero_fixtures()
    state["changed_paths"] = ["scripts/demo.py"]
    receipt["response"]["stdout"] = json.dumps(state)
    with pytest.raises(ProposalError, match="changed_paths must be empty list"):
        derive_worker_zero_resume_artifacts(admitted_request, receipt)

    orig_prefix = receipt["request"]["run_prefix"]
    non_null_cases = (
        ("final_worker_run_id", f"{orig_prefix}-worker-0"),
        ("final_reviewer_run_id", f"{orig_prefix}-reviewer-0"),
        ("proposal_sha256", "a" * 64),
        ("proposal_path", ".local/two-site-refactor/proposals/test/result.json"),
        ("reviewer_path", ".local/two-site-refactor/proposals/test/review.json"),
        ("pre_review_test_path", ".local/two-site-refactor/pipeline/test/test.json"),
        ("pre_review_test_sha256", "b" * 64),
        ("pre_review_test_state", "PASSED"),
    )
    for field_name, sample_val in non_null_cases:
        admitted_request, receipt, state = _build_worker_zero_fixtures()
        state[field_name] = sample_val
        receipt["response"]["stdout"] = json.dumps(state)
        with pytest.raises(ProposalError, match=f"{field_name!r} must be None at blocked worker-0"):
            derive_worker_zero_resume_artifacts(admitted_request, receipt)


def test_worker_zero_schema_and_evidence_mismatch():
    admitted_request, receipt, state = _build_worker_zero_fixtures()
    state["quota_attempt"]["status_sha256"] = "not-a-64-hex-sha"
    receipt["response"]["stdout"] = json.dumps(state)
    with pytest.raises(ProposalError, match="status_sha256 must be 64 lowercase hex"):
        derive_worker_zero_resume_artifacts(admitted_request, receipt)

    admitted_request, receipt, state = _build_worker_zero_fixtures()
    state["state"] = "TASK_COMPLETED"
    receipt["response"]["stdout"] = json.dumps(state)
    with pytest.raises(ProposalError, match="pipeline state must be 'TASK_BLOCKED'"):
        derive_worker_zero_resume_artifacts(admitted_request, receipt)

    admitted_request, receipt, state = _build_worker_zero_fixtures()
    state["blockers"] = ["different blocker sentinel"]
    receipt["response"]["stdout"] = json.dumps(state)
    with pytest.raises(ProposalError, match="pipeline blockers sentinel mismatch"):
        derive_worker_zero_resume_artifacts(admitted_request, receipt)

    admitted_request, receipt, state = _build_worker_zero_fixtures()
    del receipt["quota_evidence"]["reset_at_utc"]
    with pytest.raises(ProposalError, match="receipt quota_evidence keys mismatch"):
        derive_worker_zero_resume_artifacts(admitted_request, receipt)

    admitted_request, receipt, state = _build_worker_zero_fixtures()
    state["quota_evidence"]["conversation_id"] = "mismatched-conversation-id"
    receipt["response"]["stdout"] = json.dumps(state)
    with pytest.raises(ProposalError, match="pipeline quota_evidence must equal receipt quota_evidence"):
        derive_worker_zero_resume_artifacts(admitted_request, receipt)


def _build_worker_correction_fixtures(
    attempted_index: int = 1,
    *,
    worker_retry: str = "",
    prior_worker_retry: str = "",
    prior_reviewer_retry: str = "",
):
    admitted_request, receipt, pipeline_state = _build_valid_pair()
    admitted_request["role"] = "worker"
    orig_prefix = receipt["request"]["run_prefix"]
    worker_run_id = f"{orig_prefix}-worker-{attempted_index}{worker_retry}"

    prior_index = attempted_index - 1
    prior_worker_id = f"{orig_prefix}-worker-{prior_index}{prior_worker_retry}"
    prior_reviewer_id = f"{orig_prefix}-reviewer-{prior_index}{prior_reviewer_retry}"

    pipeline_state["correction_count"] = attempted_index - 1
    pipeline_state["final_worker_run_id"] = prior_worker_id
    pipeline_state["final_reviewer_run_id"] = prior_reviewer_id
    pipeline_state["proposal_sha256"] = "a" * 64
    pipeline_state["proposal_path"] = f".local/two-site-refactor/proposals/{prior_worker_id}/result.json"
    pipeline_state["reviewer_path"] = f".local/two-site-refactor/proposals/{prior_reviewer_id}/result.json"
    pipeline_state["pre_review_test_path"] = (
        f".local/two-site-refactor/pipeline/{orig_prefix}/pre-review-tests-{prior_index}.json"
    )
    pipeline_state["pre_review_test_sha256"] = "b" * 64
    pipeline_state["pre_review_test_state"] = "PASSED"
    pipeline_state["changed_paths"] = ["scripts/two_site_proposal_pipeline.py"]

    status_sha = pipeline_state["quota_attempt"]["status_sha256"]
    pipeline_state["quota_attempt"] = {
        "role": "worker",
        "run_id": worker_run_id,
        "status_sha256": status_sha,
    }
    receipt["response"]["stdout"] = json.dumps(pipeline_state)
    return admitted_request, receipt, pipeline_state


def test_worker_dispatcher_success_and_retries():
    for idx in (1, 2):
        for att_retry in ("", "-retry-1", "-retry-2"):
            for pw_retry in ("", "-retry-1", "-retry-2"):
                for pr_retry in ("", "-retry-1", "-retry-2"):
                    req, rec, state = _build_worker_correction_fixtures(
                        idx,
                        worker_retry=att_retry,
                        prior_worker_retry=pw_retry,
                        prior_reviewer_retry=pr_retry,
                    )
                    artifacts = derive_worker_resume_artifacts(req, rec)
                    orig_prefix = rec["request"]["run_prefix"]
                    prior_idx = idx - 1
                    expected_prior_worker = f"{orig_prefix}-worker-{prior_idx}{pw_retry}"
                    expected_prior_reviewer = f"{orig_prefix}-reviewer-{prior_idx}{pr_retry}"

                    assert artifacts["task_id"] == req["task_id"]
                    assert artifacts["semantic_task_fingerprint"] == req["semantic_task_fingerprint"]
                    assert artifacts["source_head"] == req["source_head"]
                    assert artifacts["original_run_prefix"] == orig_prefix
                    assert artifacts["attempted_run_id"] == f"{orig_prefix}-worker-{idx}{att_retry}"
                    assert len(artifacts["status_sha256"]) == 64
                    assert artifacts["status_sha"] == artifacts["status_sha256"]
                    assert artifacts["quota_evidence"] == rec["quota_evidence"]
                    assert artifacts["worker_index"] == idx

                    assert artifacts["final_worker_run_id"] == expected_prior_worker
                    assert artifacts["final_reviewer_run_id"] == expected_prior_reviewer
                    assert artifacts["proposal_path"] == state["proposal_path"]
                    assert artifacts["proposal_sha256"] == "a" * 64
                    assert artifacts["reviewer_path"] == state["reviewer_path"]
                    assert artifacts["pre_review_test_path"] == state["pre_review_test_path"]
                    assert artifacts["pre_review_test_sha256"] == "b" * 64
                    assert artifacts["pre_review_test_state"] == "PASSED"
                    assert artifacts["changed_paths"] == ["scripts/two_site_proposal_pipeline.py"]
                    assert artifacts["expected_feedback_path"] == (
                        f".local/two-site-refactor/pipeline/{orig_prefix}/feedback-correction-{idx}.json"
                    )
                    assert "feedback_sha256" not in artifacts
                    assert "expected_feedback_sha256" not in artifacts

                    # Immutability and detachment
                    artifacts["changed_paths"].append("extra.py")
                    assert state["changed_paths"] == ["scripts/two_site_proposal_pipeline.py"]
                    artifacts["quota_evidence"]["provider"] = "mutated-provider-val"
                    assert rec["quota_evidence"]["provider"] != "mutated-provider-val"


def test_worker_dispatcher_wrong_state_count_and_ids():
    for idx in (1, 2):
        wrong_counts = [idx, idx + 1, "0", True, False]
        for bad_count in wrong_counts:
            req, rec, state = _build_worker_correction_fixtures(idx)
            state["correction_count"] = bad_count
            rec["response"]["stdout"] = json.dumps(state)
            with pytest.raises(ProposalError, match="correction_count must be exact int"):
                derive_worker_resume_artifacts(req, rec)

    orig_prefix = "ae03-20260925-c5d"
    for idx in (1, 2):
        req, rec, state = _build_worker_correction_fixtures(idx)
        state["final_worker_run_id"] = None
        rec["response"]["stdout"] = json.dumps(state)
        with pytest.raises(ProposalError, match="prior worker/reviewer paths validation failed"):
            derive_worker_resume_artifacts(req, rec)

        req, rec, state = _build_worker_correction_fixtures(idx)
        state["final_worker_run_id"] = f"{orig_prefix}-worker-{idx-1}-retry-3"
        rec["response"]["stdout"] = json.dumps(state)
        with pytest.raises(ProposalError, match="prior worker/reviewer paths validation failed"):
            derive_worker_resume_artifacts(req, rec)

        req, rec, state = _build_worker_correction_fixtures(idx)
        state["final_reviewer_run_id"] = None
        rec["response"]["stdout"] = json.dumps(state)
        with pytest.raises(ProposalError, match="prior worker/reviewer paths validation failed"):
            derive_worker_resume_artifacts(req, rec)

        req, rec, state = _build_worker_correction_fixtures(idx)
        state["final_reviewer_run_id"] = f"{orig_prefix}-reviewer-{idx-1}-retry-3"
        rec["response"]["stdout"] = json.dumps(state)
        with pytest.raises(ProposalError, match="prior worker/reviewer paths validation failed"):
            derive_worker_resume_artifacts(req, rec)

        for bad_att in (
            f"{orig_prefix}-worker-{idx}-retry-3",
            f"{orig_prefix}-worker-3",
            f"{orig_prefix}-reviewer-{idx}",
            "unknown-run-id",
        ):
            req, rec, state = _build_worker_correction_fixtures(idx)
            state["quota_attempt"]["run_id"] = bad_att
            rec["response"]["stdout"] = json.dumps(state)
            with pytest.raises(ProposalError, match="does not match original run_prefix"):
                derive_worker_resume_artifacts(req, rec)


def test_worker_dispatcher_alias_paths_rejected():
    for idx in (1, 2):
        prior_idx = idx - 1
        req, rec, state = _build_worker_correction_fixtures(
            idx,
            prior_worker_retry="-retry-1",
        )
        orig_prefix = rec["request"]["run_prefix"]
        state["proposal_path"] = f".local/two-site-refactor/proposals/{orig_prefix}-worker-{prior_idx}/result.json"
        rec["response"]["stdout"] = json.dumps(state)
        with pytest.raises(ProposalError, match="proposal_path mismatch"):
            derive_worker_resume_artifacts(req, rec)

        req, rec, state = _build_worker_correction_fixtures(
            idx,
            prior_reviewer_retry="-retry-2",
        )
        state["reviewer_path"] = f".local/two-site-refactor/proposals/{orig_prefix}-reviewer-{prior_idx}/result.json"
        rec["response"]["stdout"] = json.dumps(state)
        with pytest.raises(ProposalError, match="reviewer_path mismatch"):
            derive_worker_resume_artifacts(req, rec)

        req, rec, state = _build_worker_correction_fixtures(idx)
        state["pre_review_test_path"] = f".local/two-site-refactor/pipeline/{orig_prefix}/pre-review-tests-{idx}.json"
        rec["response"]["stdout"] = json.dumps(state)
        with pytest.raises(ProposalError, match="pre_review_test_path mismatch"):
            derive_worker_resume_artifacts(req, rec)


def test_worker_dispatcher_shas_test_state_and_changed_paths():
    for idx in (1, 2):
        for bad_sha in (None, "a" * 63, "A" * 64, "g" * 64):
            req, rec, state = _build_worker_correction_fixtures(idx)
            state["proposal_sha256"] = bad_sha
            rec["response"]["stdout"] = json.dumps(state)
            with pytest.raises(ProposalError, match="proposal_sha256 must be 64 lowercase hex"):
                derive_worker_resume_artifacts(req, rec)

            req, rec, state = _build_worker_correction_fixtures(idx)
            state["pre_review_test_sha256"] = bad_sha
            rec["response"]["stdout"] = json.dumps(state)
            with pytest.raises(ProposalError, match="pre_review_test_sha256 must be 64 lowercase hex"):
                derive_worker_resume_artifacts(req, rec)

        for bad_test_state in (None, "FAILED", "passed", "ERROR"):
            req, rec, state = _build_worker_correction_fixtures(idx)
            state["pre_review_test_state"] = bad_test_state
            rec["response"]["stdout"] = json.dumps(state)
            with pytest.raises(ProposalError, match="pre_review_test_state must be 'PASSED'"):
                derive_worker_resume_artifacts(req, rec)

        for bad_paths in ([], None, [""], ["valid.py", ""], [123], "scripts/path.py"):
            req, rec, state = _build_worker_correction_fixtures(idx)
            state["changed_paths"] = bad_paths
            rec["response"]["stdout"] = json.dumps(state)
            with pytest.raises(ProposalError, match="changed_paths"):
                derive_worker_resume_artifacts(req, rec)


def test_worker_zero_dispatch_and_unaffected_regression():
    req0, rec0, _ = _build_worker_zero_fixtures()
    res_zero = derive_worker_zero_resume_artifacts(req0, rec0)
    res_disp = derive_worker_resume_artifacts(req0, rec0)
    assert res_zero == res_disp

    req1, rec1, _ = _build_worker_correction_fixtures(1)
    with pytest.raises(ProposalError, match="worker-0 pattern"):
        derive_worker_zero_resume_artifacts(req1, rec1)

    req2, rec2, _ = _build_worker_correction_fixtures(2)
    with pytest.raises(ProposalError, match="worker-0 pattern"):
        derive_worker_zero_resume_artifacts(req2, rec2)


def test_helper_actually_called_and_compared(monkeypatch):
    req, rec, _ = _build_worker_correction_fixtures(1)
    called = []
    import scripts.two_site_quota_resume_worker_artifacts as mod
    orig_helper = mod.validate_prior_worker_paths

    def spy_helper(*args, **kwargs):
        called.append(True)
        return orig_helper(*args, **kwargs)

    monkeypatch.setattr(mod, "validate_prior_worker_paths", spy_helper)
    artifacts = derive_worker_resume_artifacts(req, rec)
    assert len(called) == 1
    assert artifacts["worker_index"] == 1
