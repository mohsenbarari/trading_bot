"""Smoke tests for complete_worker_two_history."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch
import pytest

from scripts.two_site_proposal_pipeline import ProposalError
from scripts.two_site_quota_resume_worker_two_history_complete import (
    complete_worker_two_history,
)


def _make_base_dict(
    prefix: str = "run-20260925-sample",
    feedback1_kind: str = "test-gate",
    needs_reviewer0: bool = False,
) -> dict:
    return {
        "worker0_id": f"{prefix}-worker-0",
        "worker0_run_id": f"{prefix}-worker-0",
        "worker0_conversation_id": "conv-w0",
        "worker1_id": f"{prefix}-worker-1",
        "worker1_run_id": f"{prefix}-worker-1",
        "worker1_conversation_id": "conv-w1",
        "reviewer1_id": f"{prefix}-reviewer-1",
        "reviewer1_run_id": f"{prefix}-reviewer-1",
        "reviewer1_conversation_id": "conv-r1",
        "prior_worker0_sha": "0" * 64,
        "feedback1_kind": feedback1_kind,
        "feedback1_reviewer": feedback1_kind,
        "feedback1": {
            "reviewer": feedback1_kind,
            "findings": ["finding-alpha"],
            "reviewed_proposal_sha256": "0" * 64,
        },
        "needs_reviewer0": needs_reviewer0,
    }


def _make_artifacts(prefix: str = "run-20260925-sample") -> dict:
    return {
        "worker_index": 2,
        "original_run_prefix": prefix,
        "final_worker_run_id": f"{prefix}-worker-1",
        "final_reviewer_run_id": f"{prefix}-reviewer-1",
        "proposal_sha256": "1" * 64,
        "task_id": "task-sample",
        "source_head": "a" * 40,
    }


@patch("scripts.two_site_quota_resume_worker_two_history_complete.derive_worker_two_history_base")
def test_test_gate_happy_path(mock_derive, tmp_path: Path) -> None:
    prefix = "run-20260925-sample"
    mock_derive.return_value = _make_base_dict(prefix, "test-gate", False)
    artifacts = _make_artifacts(prefix)

    res = complete_worker_two_history(tmp_path, "task.json", artifacts, {})
    assert res["complete"] is True
    assert res["conversation_ids"] == ["conv-w0", "conv-w1", "conv-r1"]
    assert res["ordered_conversation_ids"] == ["conv-w0", "conv-w1", "conv-r1"]
    assert "reviewer0_id" not in res


@patch("scripts.two_site_quota_resume_worker_two_history_complete.derive_worker_two_history_base")
def test_test_gate_conflict_with_reviewer0_candidate(mock_derive, tmp_path: Path) -> None:
    prefix = "run-20260925-sample"
    mock_derive.return_value = _make_base_dict(prefix, "test-gate", False)
    artifacts = _make_artifacts(prefix)

    cand_dir = tmp_path / ".local" / "two-site-refactor" / "proposals" / f"{prefix}-reviewer-0"
    cand_dir.mkdir(parents=True, exist_ok=True)
    (cand_dir / "result.json").write_text("{}", encoding="utf-8")

    with pytest.raises(ProposalError, match="unexpected Reviewer0 result candidate"):
        complete_worker_two_history(tmp_path, "task.json", artifacts, {})


@patch("scripts.two_site_quota_resume_worker_two_history_complete.derive_worker_two_history_base")
@patch("scripts.two_site_quota_resume_worker_two_history_complete.load_prior_worker_zero")
@patch("scripts.two_site_quota_resume_worker_two_history_complete._load_verifier_pre_review_receipt")
@patch("scripts.two_site_quota_resume_worker_two_history_complete.load_task")
@patch("scripts.two_site_quota_resume_worker_two_history_complete.validate_bound_prior_reviewer")
def test_gemini_one_valid_candidate_happy_path(
    mock_validate_rev,
    mock_load_task,
    mock_load_receipt,
    mock_load_w0,
    mock_derive,
    tmp_path: Path,
) -> None:
    prefix = "run-20260925-sample"
    mock_derive.return_value = _make_base_dict(prefix, "gemini", True)
    artifacts = _make_artifacts(prefix)

    mock_load_w0.return_value = {
        "conversation_id": "conv-w0",
        "result_sha": "0" * 64,
    }
    mock_load_receipt.return_value = {
        "state": "PASSED",
        "proposal_sha256": "0" * 64,
    }
    mock_load_task.return_value = ({"task_id": "task-sample"}, {})
    mock_validate_rev.return_value = (
        {"findings": ["finding-alpha"], "status": "CHANGES_REQUIRED"},
        {"conversation_id": "conv-r0", "state": "CHANGES_REQUIRED"},
        "r0_sha" * 8,
    )

    pipe_dir = tmp_path / ".local" / "two-site-refactor" / "pipeline" / prefix
    pipe_dir.mkdir(parents=True, exist_ok=True)
    (pipe_dir / "pre-review-tests-0.json").write_text("{}", encoding="utf-8")

    cand_dir = tmp_path / ".local" / "two-site-refactor" / "proposals" / f"{prefix}-reviewer-0"
    cand_dir.mkdir(parents=True, exist_ok=True)
    (cand_dir / "result.json").write_text("{}", encoding="utf-8")

    res = complete_worker_two_history(tmp_path, "task.json", artifacts, {})
    assert res["complete"] is True
    assert res["reviewer0_id"] == f"{prefix}-reviewer-0"
    assert res["reviewer0_conversation_id"] == "conv-r0"
    assert res["conversation_ids"] == ["conv-w0", "conv-r0", "conv-w1", "conv-r1"]
    assert res["ordered_conversation_ids"] == ["conv-w0", "conv-r0", "conv-w1", "conv-r1"]
    assert res["reviewer0_result_sha"] == "r0_sha" * 8


@patch("scripts.two_site_quota_resume_worker_two_history_complete.derive_worker_two_history_base")
@patch("scripts.two_site_quota_resume_worker_two_history_complete.load_prior_worker_zero")
@patch("scripts.two_site_quota_resume_worker_two_history_complete._load_verifier_pre_review_receipt")
@patch("scripts.two_site_quota_resume_worker_two_history_complete.load_task")
def test_gemini_missing_candidate_fails_closed(
    mock_load_task,
    mock_load_receipt,
    mock_load_w0,
    mock_derive,
    tmp_path: Path,
) -> None:
    prefix = "run-20260925-sample"
    mock_derive.return_value = _make_base_dict(prefix, "gemini", True)
    artifacts = _make_artifacts(prefix)

    mock_load_w0.return_value = {
        "conversation_id": "conv-w0",
        "result_sha": "0" * 64,
    }
    mock_load_receipt.return_value = {
        "state": "PASSED",
        "proposal_sha256": "0" * 64,
    }
    mock_load_task.return_value = ({"task_id": "task-sample"}, {})

    pipe_dir = tmp_path / ".local" / "two-site-refactor" / "pipeline" / prefix
    pipe_dir.mkdir(parents=True, exist_ok=True)
    (pipe_dir / "pre-review-tests-0.json").write_text("{}", encoding="utf-8")

    with pytest.raises(ProposalError, match="no valid Reviewer0 candidate found"):
        complete_worker_two_history(tmp_path, "task.json", artifacts, {})


@patch("scripts.two_site_quota_resume_worker_two_history_complete.derive_worker_two_history_base")
@patch("scripts.two_site_quota_resume_worker_two_history_complete.load_prior_worker_zero")
@patch("scripts.two_site_quota_resume_worker_two_history_complete._load_verifier_pre_review_receipt")
@patch("scripts.two_site_quota_resume_worker_two_history_complete.load_task")
@patch("scripts.two_site_quota_resume_worker_two_history_complete.validate_bound_prior_reviewer")
def test_gemini_multiple_candidates_fails_closed(
    mock_validate_rev,
    mock_load_task,
    mock_load_receipt,
    mock_load_w0,
    mock_derive,
    tmp_path: Path,
) -> None:
    prefix = "run-20260925-sample"
    mock_derive.return_value = _make_base_dict(prefix, "gemini", True)
    artifacts = _make_artifacts(prefix)

    mock_load_w0.return_value = {
        "conversation_id": "conv-w0",
        "result_sha": "0" * 64,
    }
    mock_load_receipt.return_value = {
        "state": "PASSED",
        "proposal_sha256": "0" * 64,
    }
    mock_load_task.return_value = ({"task_id": "task-sample"}, {})
    mock_validate_rev.return_value = (
        {"findings": ["finding-alpha"], "status": "CHANGES_REQUIRED"},
        {"conversation_id": "conv-r0", "state": "CHANGES_REQUIRED"},
        "r0_sha" * 8,
    )

    pipe_dir = tmp_path / ".local" / "two-site-refactor" / "pipeline" / prefix
    pipe_dir.mkdir(parents=True, exist_ok=True)
    (pipe_dir / "pre-review-tests-0.json").write_text("{}", encoding="utf-8")

    for cid in (f"{prefix}-reviewer-0", f"{prefix}-reviewer-0-retry-1"):
        cand_dir = tmp_path / ".local" / "two-site-refactor" / "proposals" / cid
        cand_dir.mkdir(parents=True, exist_ok=True)
        (cand_dir / "result.json").write_text("{}", encoding="utf-8")

    with pytest.raises(ProposalError, match="multiple valid Reviewer0 candidates found"):
        complete_worker_two_history(tmp_path, "task.json", artifacts, {})


@patch("scripts.two_site_quota_resume_worker_two_history_complete.derive_worker_two_history_base")
@patch("scripts.two_site_quota_resume_worker_two_history_complete.load_prior_worker_zero")
@patch("scripts.two_site_quota_resume_worker_two_history_complete._load_verifier_pre_review_receipt")
@patch("scripts.two_site_quota_resume_worker_two_history_complete.load_task")
@patch("scripts.two_site_quota_resume_worker_two_history_complete.validate_bound_prior_reviewer")
def test_gemini_findings_mismatch_fails_closed(
    mock_validate_rev,
    mock_load_task,
    mock_load_receipt,
    mock_load_w0,
    mock_derive,
    tmp_path: Path,
) -> None:
    prefix = "run-20260925-sample"
    mock_derive.return_value = _make_base_dict(prefix, "gemini", True)
    artifacts = _make_artifacts(prefix)

    mock_load_w0.return_value = {
        "conversation_id": "conv-w0",
        "result_sha": "0" * 64,
    }
    mock_load_receipt.return_value = {
        "state": "PASSED",
        "proposal_sha256": "0" * 64,
    }
    mock_load_task.return_value = ({"task_id": "task-sample"}, {})
    mock_validate_rev.return_value = (
        {"findings": ["different-finding"], "status": "CHANGES_REQUIRED"},
        {"conversation_id": "conv-r0", "state": "CHANGES_REQUIRED"},
        "r0_sha" * 8,
    )

    pipe_dir = tmp_path / ".local" / "two-site-refactor" / "pipeline" / prefix
    pipe_dir.mkdir(parents=True, exist_ok=True)
    (pipe_dir / "pre-review-tests-0.json").write_text("{}", encoding="utf-8")

    cand_dir = tmp_path / ".local" / "two-site-refactor" / "proposals" / f"{prefix}-reviewer-0"
    cand_dir.mkdir(parents=True, exist_ok=True)
    (cand_dir / "result.json").write_text("{}", encoding="utf-8")

    with pytest.raises(ProposalError, match="Reviewer0 findings mismatch"):
        complete_worker_two_history(tmp_path, "task.json", artifacts, {})


@patch("scripts.two_site_quota_resume_worker_two_history_complete.derive_worker_two_history_base")
@patch("scripts.two_site_quota_resume_worker_two_history_complete.load_prior_worker_zero")
@patch("scripts.two_site_quota_resume_worker_two_history_complete._load_verifier_pre_review_receipt")
@patch("scripts.two_site_quota_resume_worker_two_history_complete.load_task")
@patch("scripts.two_site_quota_resume_worker_two_history_complete.validate_bound_prior_reviewer")
def test_gemini_duplicate_conversation_id_fails_closed(
    mock_validate_rev,
    mock_load_task,
    mock_load_receipt,
    mock_load_w0,
    mock_derive,
    tmp_path: Path,
) -> None:
    prefix = "run-20260925-sample"
    mock_derive.return_value = _make_base_dict(prefix, "gemini", True)
    artifacts = _make_artifacts(prefix)

    mock_load_w0.return_value = {
        "conversation_id": "conv-w0",
        "result_sha": "0" * 64,
    }
    mock_load_receipt.return_value = {
        "state": "PASSED",
        "proposal_sha256": "0" * 64,
    }
    mock_load_task.return_value = ({"task_id": "task-sample"}, {})
    # Duplicate Worker1 conversation_id "conv-w1"
    mock_validate_rev.return_value = (
        {"findings": ["finding-alpha"], "status": "CHANGES_REQUIRED"},
        {"conversation_id": "conv-w1", "state": "CHANGES_REQUIRED"},
        "r0_sha" * 8,
    )

    pipe_dir = tmp_path / ".local" / "two-site-refactor" / "pipeline" / prefix
    pipe_dir.mkdir(parents=True, exist_ok=True)
    (pipe_dir / "pre-review-tests-0.json").write_text("{}", encoding="utf-8")

    cand_dir = tmp_path / ".local" / "two-site-refactor" / "proposals" / f"{prefix}-reviewer-0"
    cand_dir.mkdir(parents=True, exist_ok=True)
    (cand_dir / "result.json").write_text("{}", encoding="utf-8")

    with pytest.raises(ProposalError, match="duplicate conversation IDs detected"):
        complete_worker_two_history(tmp_path, "task.json", artifacts, {})


@patch("scripts.two_site_quota_resume_worker_two_history_complete.derive_worker_two_history_base")
@patch("scripts.two_site_quota_resume_worker_two_history_complete.load_prior_worker_zero")
@patch("scripts.two_site_quota_resume_worker_two_history_complete._load_verifier_pre_review_receipt")
@patch("scripts.two_site_quota_resume_worker_two_history_complete.load_task")
@patch("scripts.two_site_quota_resume_worker_two_history_complete.validate_bound_prior_reviewer")
def test_gemini_invalid_existent_candidate_hard_stops(
    mock_validate_rev,
    mock_load_task,
    mock_load_receipt,
    mock_load_w0,
    mock_derive,
    tmp_path: Path,
) -> None:
    prefix = "run-20260925-sample"
    mock_derive.return_value = _make_base_dict(prefix, "gemini", True)
    artifacts = _make_artifacts(prefix)

    mock_load_w0.return_value = {
        "conversation_id": "conv-w0",
        "result_sha": "0" * 64,
    }
    mock_load_receipt.return_value = {
        "state": "PASSED",
        "proposal_sha256": "0" * 64,
    }
    mock_load_task.return_value = ({"task_id": "task-sample"}, {})
    mock_validate_rev.side_effect = ProposalError("corrupt reviewer result")

    pipe_dir = tmp_path / ".local" / "two-site-refactor" / "pipeline" / prefix
    pipe_dir.mkdir(parents=True, exist_ok=True)
    (pipe_dir / "pre-review-tests-0.json").write_text("{}", encoding="utf-8")

    cand_dir = tmp_path / ".local" / "two-site-refactor" / "proposals" / f"{prefix}-reviewer-0"
    cand_dir.mkdir(parents=True, exist_ok=True)
    (cand_dir / "result.json").write_text("{}", encoding="utf-8")

    with pytest.raises(ProposalError, match="corrupt reviewer result"):
        complete_worker_two_history(tmp_path, "task.json", artifacts, {})
