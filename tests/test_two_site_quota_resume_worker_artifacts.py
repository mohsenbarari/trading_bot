from __future__ import annotations

import json
import pytest

from scripts.two_site_proposal_pipeline import ProposalError
from scripts.two_site_quota_resume_worker_artifacts import (
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
