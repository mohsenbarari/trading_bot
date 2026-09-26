from __future__ import annotations

import json
import pytest

from scripts.two_site_proposal_pipeline import ProposalError
from scripts.two_site_quota_resume_worker_artifacts import derive_worker_resume_artifacts
from tests.test_two_site_quota_resume_worker_artifacts import _build_worker_correction_fixtures


@pytest.mark.parametrize(
    ("attempted_index", "category"),
    [
        (1, "REV"),
        (1, "BOOT"),
        (1, "FAIL"),
        (2, "REV"),
        (2, "BOOT"),
        (2, "FAIL"),
    ],
)
def test_six_case_correction_origin_baseline(attempted_index: int, category: str):
    req, rec, state = _build_worker_correction_fixtures(attempted_index)
    assert state["correction_count"] == attempted_index - 1
    orig_prefix = rec["request"]["run_prefix"]

    if category == "BOOT":
        state["final_reviewer_run_id"] = None
        state["reviewer_path"] = None
        state["pre_review_test_path"] = None
        state["pre_review_test_sha256"] = None
        state["pre_review_test_state"] = None
        rec["response"]["stdout"] = json.dumps(state)

        assert state["final_reviewer_run_id"] is None
        assert state["reviewer_path"] is None
        assert state["pre_review_test_path"] is None
        assert state["pre_review_test_sha256"] is None
        assert state["pre_review_test_state"] is None

        artifacts = derive_worker_resume_artifacts(req, rec)
        assert artifacts["worker_index"] == attempted_index
        assert artifacts["correction_origin"] == "BOOTSTRAP"
        assert artifacts["final_worker_run_id"] == state["final_worker_run_id"]
        assert artifacts["final_reviewer_run_id"] is None
        assert artifacts["proposal_path"] == state["proposal_path"]
        assert artifacts["proposal_sha256"] == state["proposal_sha256"]
        assert artifacts["reviewer_path"] is None
        assert artifacts["pre_review_test_path"] is None
        assert artifacts["pre_review_test_sha256"] is None
        assert artifacts["pre_review_test_state"] is None
        assert artifacts["changed_paths"] == state["changed_paths"]
        assert artifacts["expected_feedback_path"] == (
            f".local/two-site-refactor/pipeline/{orig_prefix}/feedback-correction-{attempted_index}.json"
        )

    elif category == "FAIL":
        state["final_reviewer_run_id"] = None
        state["reviewer_path"] = None
        state["pre_review_test_state"] = "FAILED"
        rec["response"]["stdout"] = json.dumps(state)

        assert state["final_reviewer_run_id"] is None
        assert state["reviewer_path"] is None
        assert state["pre_review_test_path"] is not None
        assert len(state["pre_review_test_sha256"]) == 64
        assert state["pre_review_test_state"] == "FAILED"

        artifacts = derive_worker_resume_artifacts(req, rec)
        assert artifacts["worker_index"] == attempted_index
        assert artifacts["correction_origin"] == "TEST_FAILED"
        assert artifacts["final_worker_run_id"] == state["final_worker_run_id"]
        assert artifacts["final_reviewer_run_id"] is None
        assert artifacts["proposal_path"] == state["proposal_path"]
        assert artifacts["proposal_sha256"] == state["proposal_sha256"]
        assert artifacts["reviewer_path"] is None
        assert artifacts["pre_review_test_path"] == state["pre_review_test_path"]
        assert artifacts["pre_review_test_sha256"] == state["pre_review_test_sha256"]
        assert artifacts["pre_review_test_state"] == "FAILED"
        assert artifacts["changed_paths"] == state["changed_paths"]
        assert artifacts["expected_feedback_path"] == (
            f".local/two-site-refactor/pipeline/{orig_prefix}/feedback-correction-{attempted_index}.json"
        )

    else:  # REV
        assert state["final_reviewer_run_id"] is not None
        assert state["reviewer_path"] is not None
        assert state["pre_review_test_path"] is not None
        assert state["pre_review_test_state"] == "PASSED"

        artifacts = derive_worker_resume_artifacts(req, rec)
        assert artifacts["worker_index"] == attempted_index
        assert artifacts["correction_origin"] == "REVIEWER"
        assert artifacts["final_worker_run_id"] == state["final_worker_run_id"]
        assert artifacts["final_reviewer_run_id"] == state["final_reviewer_run_id"]
        assert artifacts["proposal_path"] == state["proposal_path"]
        assert artifacts["proposal_sha256"] == state["proposal_sha256"]
        assert artifacts["reviewer_path"] == state["reviewer_path"]
        assert artifacts["pre_review_test_path"] == state["pre_review_test_path"]
        assert artifacts["pre_review_test_sha256"] == state["pre_review_test_sha256"]
        assert artifacts["pre_review_test_state"] == "PASSED"
        assert artifacts["changed_paths"] == state["changed_paths"]
        assert artifacts["expected_feedback_path"] == (
            f".local/two-site-refactor/pipeline/{orig_prefix}/feedback-correction-{attempted_index}.json"
        )


@pytest.mark.parametrize("attempted_index", [1, 2])
def test_forgery_and_wrong_correction_count_rejects(attempted_index: int):
    req, rec, state = _build_worker_correction_fixtures(attempted_index)
    state["final_reviewer_run_id"] = None
    state["pre_review_test_path"] = None
    state["pre_review_test_sha256"] = None
    state["pre_review_test_state"] = None
    state["reviewer_path"] = f".local/two-site-refactor/proposals/forged/result.json"
    rec["response"]["stdout"] = json.dumps(state)
    with pytest.raises(ProposalError):
        derive_worker_resume_artifacts(req, rec)

    for bad_count in (attempted_index, attempted_index + 1, -1):
        req, rec, state = _build_worker_correction_fixtures(attempted_index)
        state["correction_count"] = bad_count
        rec["response"]["stdout"] = json.dumps(state)
        with pytest.raises(ProposalError, match="correction_count must be exact int"):
            derive_worker_resume_artifacts(req, rec)


@pytest.mark.parametrize("attempted_index", [1, 2])
def test_cross_origin_and_malformed_shapes_reject(attempted_index: int):
    orig_prefix = "ae03-20260925-c5d"
    prior_idx = attempted_index - 1

    # 1. Reviewer present with FAILED test state
    req, rec, state = _build_worker_correction_fixtures(attempted_index)
    state["pre_review_test_state"] = "FAILED"
    rec["response"]["stdout"] = json.dumps(state)
    with pytest.raises(ProposalError, match="pre_review_test_state must be 'PASSED'"):
        derive_worker_resume_artifacts(req, rec)

    # 2. Reviewer present with None test state
    req, rec, state = _build_worker_correction_fixtures(attempted_index)
    state["pre_review_test_state"] = None
    rec["response"]["stdout"] = json.dumps(state)
    with pytest.raises(ProposalError, match="pre_review_test_state must be 'PASSED'"):
        derive_worker_resume_artifacts(req, rec)

    # 3. Reviewer present with None reviewer_path
    req, rec, state = _build_worker_correction_fixtures(attempted_index)
    state["reviewer_path"] = None
    rec["response"]["stdout"] = json.dumps(state)
    with pytest.raises(ProposalError, match="reviewer_path cannot be None"):
        derive_worker_resume_artifacts(req, rec)

    # 4. Reviewer None with non-null reviewer_path (BOOT-like)
    req, rec, state = _build_worker_correction_fixtures(attempted_index)
    state["final_reviewer_run_id"] = None
    state["pre_review_test_path"] = None
    state["pre_review_test_sha256"] = None
    state["pre_review_test_state"] = None
    state["reviewer_path"] = f".local/two-site-refactor/proposals/{orig_prefix}-reviewer-{prior_idx}/result.json"
    rec["response"]["stdout"] = json.dumps(state)
    with pytest.raises(ProposalError, match="reviewer_path must be None"):
        derive_worker_resume_artifacts(req, rec)

    # 5. Reviewer None with non-null reviewer_path (FAIL-like)
    req, rec, state = _build_worker_correction_fixtures(attempted_index)
    state["final_reviewer_run_id"] = None
    state["pre_review_test_state"] = "FAILED"
    state["reviewer_path"] = f".local/two-site-refactor/proposals/{orig_prefix}-reviewer-{prior_idx}/result.json"
    rec["response"]["stdout"] = json.dumps(state)
    with pytest.raises(ProposalError, match="reviewer_path must be None"):
        derive_worker_resume_artifacts(req, rec)

    # 6. Bootstrap with residual pre_review_test_path
    req, rec, state = _build_worker_correction_fixtures(attempted_index)
    state["final_reviewer_run_id"] = None
    state["reviewer_path"] = None
    state["pre_review_test_sha256"] = None
    state["pre_review_test_state"] = None
    rec["response"]["stdout"] = json.dumps(state)
    with pytest.raises(ProposalError, match="pre_review_test_path and pre_review_test_sha256 must be None"):
        derive_worker_resume_artifacts(req, rec)

    # 7. Bootstrap with residual pre_review_test_sha256
    req, rec, state = _build_worker_correction_fixtures(attempted_index)
    state["final_reviewer_run_id"] = None
    state["reviewer_path"] = None
    state["pre_review_test_path"] = None
    state["pre_review_test_state"] = None
    rec["response"]["stdout"] = json.dumps(state)
    with pytest.raises(ProposalError, match="pre_review_test_path and pre_review_test_sha256 must be None"):
        derive_worker_resume_artifacts(req, rec)

    # 8. Test FAILED with forged pre_review_test_path
    req, rec, state = _build_worker_correction_fixtures(attempted_index)
    state["final_reviewer_run_id"] = None
    state["reviewer_path"] = None
    state["pre_review_test_state"] = "FAILED"
    state["pre_review_test_path"] = f".local/two-site-refactor/pipeline/{orig_prefix}/pre-review-tests-forged.json"
    rec["response"]["stdout"] = json.dumps(state)
    with pytest.raises(ProposalError, match="pre_review_test_path mismatch"):
        derive_worker_resume_artifacts(req, rec)

    # 9. Test FAILED with missing or invalid test SHA
    for bad_sha in (None, "bad-sha", "c" * 63):
        req, rec, state = _build_worker_correction_fixtures(attempted_index)
        state["final_reviewer_run_id"] = None
        state["reviewer_path"] = None
        state["pre_review_test_state"] = "FAILED"
        state["pre_review_test_sha256"] = bad_sha
        rec["response"]["stdout"] = json.dumps(state)
        with pytest.raises(ProposalError, match="pre_review_test_sha256 must be 64 lowercase hex"):
            derive_worker_resume_artifacts(req, rec)

    # 10. Reviewer None with PASSED test state
    req, rec, state = _build_worker_correction_fixtures(attempted_index)
    state["final_reviewer_run_id"] = None
    state["reviewer_path"] = None
    state["pre_review_test_state"] = "PASSED"
    rec["response"]["stdout"] = json.dumps(state)
    with pytest.raises(ProposalError, match="invalid when final_reviewer_run_id is None"):
        derive_worker_resume_artifacts(req, rec)

    # 11. Forged proposal path in BOOTSTRAP
    req, rec, state = _build_worker_correction_fixtures(attempted_index)
    state["final_reviewer_run_id"] = None
    state["reviewer_path"] = None
    state["pre_review_test_path"] = None
    state["pre_review_test_sha256"] = None
    state["pre_review_test_state"] = None
    state["proposal_path"] = f".local/two-site-refactor/proposals/forged/result.json"
    rec["response"]["stdout"] = json.dumps(state)
    with pytest.raises(ProposalError, match="proposal_path mismatch"):
        derive_worker_resume_artifacts(req, rec)

    # 12. Forged proposal path in TEST_FAILED
    req, rec, state = _build_worker_correction_fixtures(attempted_index)
    state["final_reviewer_run_id"] = None
    state["reviewer_path"] = None
    state["pre_review_test_state"] = "FAILED"
    state["proposal_path"] = f".local/two-site-refactor/proposals/forged/result.json"
    rec["response"]["stdout"] = json.dumps(state)
    with pytest.raises(ProposalError, match="proposal_path mismatch"):
        derive_worker_resume_artifacts(req, rec)
