"""File-backed boundary proof tests for complete_worker_two_history."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from unittest.mock import patch
import pytest

from scripts.two_site_proposal_pipeline import ProposalError
from scripts.two_site_quota_resume_worker_two_history_complete import (
    complete_worker_two_history,
)
try:
    from tests.test_two_site_quota_resume_worker_two_history_base import (
        _create_valid_fixtures,
    )
except ImportError:
    from test_two_site_quota_resume_worker_two_history_base import (  # type: ignore[no-redef]
        _create_valid_fixtures,
    )


def _write_canonical_passed_receipt(
    tmp_path: Path,
    orig_prefix: str,
    worker0_sha: str,
) -> tuple[Path, str]:
    pipe_dir = tmp_path / ".local" / "two-site-refactor" / "pipeline" / orig_prefix
    pipe_dir.mkdir(parents=True, exist_ok=True)
    receipt_path = pipe_dir / "pre-review-tests-0.json"
    raw_payload = {
        "state": "PASSED",
        "proposal_sha256": worker0_sha,
        "selectors": ["tests/test_a.py"],
        "unsupported_specs": [],
        "returncode": 0,
        "stdout_tail": "1 passed",
        "stderr_tail": "",
        "snapshot_files": 1,
        "snapshot_bytes": 1,
        "isolation": "test-double",
    }
    raw_bytes = json.dumps(raw_payload).encode("utf-8")
    receipt_path.write_bytes(raw_bytes)
    receipt_sha = hashlib.sha256(raw_bytes).hexdigest()
    return receipt_path, receipt_sha


def _create_reviewer_candidate(tmp_path: Path, run_id: str) -> Path:
    cand_dir = tmp_path / ".local" / "two-site-refactor" / "proposals" / run_id
    cand_dir.mkdir(parents=True, exist_ok=True)
    result_path = cand_dir / "result.json"
    result_path.write_text("{}", encoding="utf-8")
    return result_path


def test_test_gate_fileproof_happy_path(tmp_path: Path) -> None:
    artifacts, evidence, w0_sha, _ = _create_valid_fixtures(
        tmp_path, reviewer_kind="test-gate"
    )
    res = complete_worker_two_history(tmp_path, "task.json", artifacts, evidence)
    assert res["complete"] is True
    assert res["prior_worker0_sha"] == w0_sha
    assert res["feedback1_kind"] == "test-gate"
    assert res["conversation_ids"] == ["conv-w0", "conv-w1", "conv-r1"]
    assert res["ordered_conversation_ids"] == ["conv-w0", "conv-w1", "conv-r1"]
    assert "reviewer0_id" not in res
    assert "reviewer0_conversation_id" not in res


def test_test_gate_conflict_with_reviewer0_candidate(tmp_path: Path) -> None:
    artifacts, evidence, _, _ = _create_valid_fixtures(
        tmp_path, reviewer_kind="test-gate"
    )
    _create_reviewer_candidate(
        tmp_path, f"{artifacts['original_run_prefix']}-reviewer-0"
    )
    with pytest.raises(ProposalError, match="unexpected Reviewer0 result candidate found"):
        complete_worker_two_history(tmp_path, "task.json", artifacts, evidence)


@patch("scripts.two_site_quota_resume_worker_two_history_complete.load_prior_worker_zero")
@patch("scripts.two_site_quota_resume_worker_two_history_complete.load_task")
@patch("scripts.two_site_quota_resume_worker_two_history_complete.validate_bound_prior_reviewer")
def test_gemini_fileproof_happy_path(
    mock_validate_rev,
    mock_load_task,
    mock_load_w0,
    tmp_path: Path,
) -> None:
    prefix = "run-20260925-sample"
    artifacts, evidence, w0_sha, _ = _create_valid_fixtures(
        tmp_path, orig_prefix=prefix, reviewer_kind="gemini"
    )
    _, receipt_sha = _write_canonical_passed_receipt(tmp_path, prefix, w0_sha)
    _create_reviewer_candidate(tmp_path, f"{prefix}-reviewer-0")

    mock_load_w0.return_value = {
        "conversation_id": "conv-w0",
        "result_sha": w0_sha,
        "run_id": f"{prefix}-worker-0",
        "status": {
            "run_id": f"{prefix}-worker-0",
            "conversation_id": "conv-w0",
        },
        "result": {
            "status": "PROPOSAL_READY",
            "changes": [],
        },
    }
    mock_load_task.return_value = ({"task_id": "task-sample"}, {"source_files": {}})

    observed_calls: list[dict] = []

    def _fake_validate_rev(**kwargs):
        observed_calls.append(kwargs)
        rev_art = kwargs["artifacts"]
        assert rev_art["final_worker_run_id"] == f"{prefix}-worker-0"
        assert rev_art["proposal_sha256"] == w0_sha
        assert rev_art["pre_review_test_sha256"] == receipt_sha
        assert (
            rev_art["reviewer_path"]
            == f".local/two-site-refactor/proposals/{prefix}-reviewer-0/result.json"
        )
        return (
            {
                "status": "CHANGES_REQUIRED",
                "findings": ["issue found in Worker0 proposal"],
            },
            {
                "conversation_id": "conv-r0",
                "run_id": f"{prefix}-reviewer-0",
                "state": "CHANGES_REQUIRED",
            },
            "0" * 63 + "a",
        )

    mock_validate_rev.side_effect = _fake_validate_rev

    res = complete_worker_two_history(tmp_path, "task.json", artifacts, evidence)
    assert res["complete"] is True
    assert res["reviewer0_id"] == f"{prefix}-reviewer-0"
    assert res["reviewer0_conversation_id"] == "conv-r0"
    assert res["conversation_ids"] == ["conv-w0", "conv-r0", "conv-w1", "conv-r1"]
    assert res["ordered_conversation_ids"] == ["conv-w0", "conv-r0", "conv-w1", "conv-r1"]
    assert res["reviewer0_result_sha"] == "0" * 63 + "a"

    assert len(observed_calls) == 1
    call_kwargs = observed_calls[0]
    receipt_in_call = call_kwargs["test_receipt"]
    assert receipt_in_call["state"] == "PASSED"
    assert receipt_in_call["proposal_sha256"] == w0_sha


@patch("scripts.two_site_quota_resume_worker_two_history_complete.load_prior_worker_zero")
def test_gemini_symlink_receipt_rejected(
    mock_load_w0,
    tmp_path: Path,
) -> None:
    prefix = "run-20260925-sample"
    artifacts, evidence, w0_sha, _ = _create_valid_fixtures(
        tmp_path, orig_prefix=prefix, reviewer_kind="gemini"
    )
    _create_reviewer_candidate(tmp_path, f"{prefix}-reviewer-0")

    mock_load_w0.return_value = {
        "conversation_id": "conv-w0",
        "result_sha": w0_sha,
        "run_id": f"{prefix}-worker-0",
    }

    real_receipt, _ = _write_canonical_passed_receipt(tmp_path, "other-prefix", w0_sha)
    pipe_dir = tmp_path / ".local" / "two-site-refactor" / "pipeline" / prefix
    pipe_dir.mkdir(parents=True, exist_ok=True)
    symlink_receipt = pipe_dir / "pre-review-tests-0.json"
    symlink_receipt.symlink_to(real_receipt)

    with pytest.raises(ProposalError, match="symlink"):
        complete_worker_two_history(tmp_path, "task.json", artifacts, evidence)


@patch("scripts.two_site_quota_resume_worker_two_history_complete.load_prior_worker_zero")
def test_gemini_hardlink_receipt_rejected(
    mock_load_w0,
    tmp_path: Path,
) -> None:
    prefix = "run-20260925-sample"
    artifacts, evidence, w0_sha, _ = _create_valid_fixtures(
        tmp_path, orig_prefix=prefix, reviewer_kind="gemini"
    )
    _create_reviewer_candidate(tmp_path, f"{prefix}-reviewer-0")

    mock_load_w0.return_value = {
        "conversation_id": "conv-w0",
        "result_sha": w0_sha,
        "run_id": f"{prefix}-worker-0",
    }

    receipt_path, _ = _write_canonical_passed_receipt(tmp_path, prefix, w0_sha)
    link_target = tmp_path / "hardlink_target.json"
    os.link(receipt_path, link_target)

    with pytest.raises(ProposalError, match="hardlinked"):
        complete_worker_two_history(tmp_path, "task.json", artifacts, evidence)


@patch("scripts.two_site_quota_resume_worker_two_history_complete.load_prior_worker_zero")
def test_gemini_invalid_receipt_content_rejected(
    mock_load_w0,
    tmp_path: Path,
) -> None:
    prefix = "run-20260925-sample"
    artifacts, evidence, w0_sha, _ = _create_valid_fixtures(
        tmp_path, orig_prefix=prefix, reviewer_kind="gemini"
    )
    _create_reviewer_candidate(tmp_path, f"{prefix}-reviewer-0")

    mock_load_w0.return_value = {
        "conversation_id": "conv-w0",
        "result_sha": w0_sha,
        "run_id": f"{prefix}-worker-0",
    }

    pipe_dir = tmp_path / ".local" / "two-site-refactor" / "pipeline" / prefix
    pipe_dir.mkdir(parents=True, exist_ok=True)
    receipt_path = pipe_dir / "pre-review-tests-0.json"
    receipt_path.write_text("{invalid json", encoding="utf-8")

    with pytest.raises(ProposalError, match="failed to parse pre-review test receipt JSON"):
        complete_worker_two_history(tmp_path, "task.json", artifacts, evidence)


@patch("scripts.two_site_quota_resume_worker_two_history_complete.load_prior_worker_zero")
@patch("scripts.two_site_quota_resume_worker_two_history_complete.load_task")
@patch("scripts.two_site_quota_resume_worker_two_history_complete.validate_bound_prior_reviewer")
def test_gemini_findings_mismatch_rejected(
    mock_validate_rev,
    mock_load_task,
    mock_load_w0,
    tmp_path: Path,
) -> None:
    prefix = "run-20260925-sample"
    artifacts, evidence, w0_sha, _ = _create_valid_fixtures(
        tmp_path, orig_prefix=prefix, reviewer_kind="gemini"
    )
    _write_canonical_passed_receipt(tmp_path, prefix, w0_sha)
    _create_reviewer_candidate(tmp_path, f"{prefix}-reviewer-0")

    mock_load_w0.return_value = {
        "conversation_id": "conv-w0",
        "result_sha": w0_sha,
        "run_id": f"{prefix}-worker-0",
    }
    mock_load_task.return_value = ({"task_id": "task-sample"}, {"source_files": {}})
    mock_validate_rev.return_value = (
        {
            "status": "CHANGES_REQUIRED",
            "findings": ["divergent-finding"],
        },
        {
            "conversation_id": "conv-r0",
            "run_id": f"{prefix}-reviewer-0",
            "state": "CHANGES_REQUIRED",
        },
        "0" * 64,
    )

    with pytest.raises(ProposalError, match="Reviewer0 findings mismatch"):
        complete_worker_two_history(tmp_path, "task.json", artifacts, evidence)


@patch("scripts.two_site_quota_resume_worker_two_history_complete.load_prior_worker_zero")
@patch("scripts.two_site_quota_resume_worker_two_history_complete.load_task")
@patch("scripts.two_site_quota_resume_worker_two_history_complete.validate_bound_prior_reviewer")
def test_gemini_multiple_candidates_rejected(
    mock_validate_rev,
    mock_load_task,
    mock_load_w0,
    tmp_path: Path,
) -> None:
    prefix = "run-20260925-sample"
    artifacts, evidence, w0_sha, _ = _create_valid_fixtures(
        tmp_path, orig_prefix=prefix, reviewer_kind="gemini"
    )
    _write_canonical_passed_receipt(tmp_path, prefix, w0_sha)
    _create_reviewer_candidate(tmp_path, f"{prefix}-reviewer-0")
    _create_reviewer_candidate(tmp_path, f"{prefix}-reviewer-0-retry-1")

    mock_load_w0.return_value = {
        "conversation_id": "conv-w0",
        "result_sha": w0_sha,
        "run_id": f"{prefix}-worker-0",
    }
    mock_load_task.return_value = ({"task_id": "task-sample"}, {"source_files": {}})
    mock_validate_rev.return_value = (
        {
            "status": "CHANGES_REQUIRED",
            "findings": ["issue found in Worker0 proposal"],
        },
        {
            "conversation_id": "conv-r0",
            "run_id": f"{prefix}-reviewer-0",
            "state": "CHANGES_REQUIRED",
        },
        "0" * 64,
    )

    with pytest.raises(ProposalError, match="multiple valid Reviewer0 candidates found"):
        complete_worker_two_history(tmp_path, "task.json", artifacts, evidence)
