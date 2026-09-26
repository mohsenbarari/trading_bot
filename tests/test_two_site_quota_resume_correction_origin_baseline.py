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

        with pytest.raises(ProposalError):
            derive_worker_resume_artifacts(req, rec)

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

        with pytest.raises(ProposalError):
            derive_worker_resume_artifacts(req, rec)

    else:  # REV
        assert state["final_reviewer_run_id"] is not None
        assert state["reviewer_path"] is not None
        assert state["pre_review_test_path"] is not None
        assert state["pre_review_test_state"] == "PASSED"

        artifacts = derive_worker_resume_artifacts(req, rec)
        assert artifacts["worker_index"] == attempted_index
        assert artifacts["final_reviewer_run_id"] == state["final_reviewer_run_id"]
        assert artifacts["reviewer_path"] == state["reviewer_path"]
        assert artifacts["pre_review_test_path"] == state["pre_review_test_path"]
        assert artifacts["pre_review_test_state"] == "PASSED"
        assert "expected_feedback_path" in artifacts


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
