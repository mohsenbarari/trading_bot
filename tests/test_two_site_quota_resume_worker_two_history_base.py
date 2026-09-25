"""Unit tests for derive_worker_two_history_base."""

from __future__ import annotations

import json
import os
from pathlib import Path
import pytest

from scripts.two_site_proposal_pipeline import ProposalError
from scripts.two_site_quota_resume_worker_two_history_base import (
    derive_worker_two_history_base,
)


def _create_valid_fixtures(
    tmp_path: Path,
    orig_prefix: str = "run-20260925-sample",
    reviewer_kind: str = "test-gate",
) -> tuple[dict, dict, str, str]:
    worker0_sha = "0" * 64
    worker1_sha = "1" * 64
    artifacts = {
        "worker_index": 2,
        "original_run_prefix": orig_prefix,
        "final_worker_run_id": f"{orig_prefix}-worker-1",
        "final_reviewer_run_id": f"{orig_prefix}-reviewer-1",
        "proposal_sha256": worker1_sha,
        "task_id": "task-sample",
        "source_head": "a" * 40,
    }
    evidence = {
        "worker_info": {
            "result": {"status": "PROPOSAL_READY", "changes": []},
            "status": {
                "run_id": f"{orig_prefix}-worker-1",
                "conversation_id": "conv-w1",
                "input_worker_run_id": f"{orig_prefix}-worker-0",
                "input_worker_conversation_id": "conv-w0",
                "input_proposal_sha256": worker0_sha,
            },
            "result_sha": worker1_sha,
            "result_text": "{}",
            "conversation_id": "conv-w1",
        },
        "reviewer_status": {
            "run_id": f"{orig_prefix}-reviewer-1",
            "conversation_id": "conv-r1",
        },
    }
    fb_dir = tmp_path / ".local" / "two-site-refactor" / "pipeline" / orig_prefix
    fb_dir.mkdir(parents=True, exist_ok=True)
    fb_path = fb_dir / "feedback-correction-1.json"
    fb_data = {
        "reviewer": reviewer_kind,
        "reviewed_proposal_sha256": worker0_sha,
        "findings": ["issue found in Worker0 proposal"],
        "test_evidence": ["test failure log"],
    }
    fb_path.write_text(json.dumps(fb_data), encoding="utf-8")
    return artifacts, evidence, worker0_sha, worker1_sha


def test_test_gate_happy_path(tmp_path: Path) -> None:
    artifacts, evidence, w0_sha, w1_sha = _create_valid_fixtures(
        tmp_path, reviewer_kind="test-gate"
    )
    base = derive_worker_two_history_base(tmp_path, artifacts, evidence)
    assert base["worker0_run_id"] == artifacts["original_run_prefix"] + "-worker-0"
    assert base["worker1_run_id"] == artifacts["final_worker_run_id"]
    assert base["reviewer1_run_id"] == artifacts["final_reviewer_run_id"]
    assert base["worker0_conversation_id"] == "conv-w0"
    assert base["worker1_conversation_id"] == "conv-w1"
    assert base["reviewer1_conversation_id"] == "conv-r1"
    assert base["prior_worker0_sha"] == w0_sha
    assert base["feedback1_kind"] == "test-gate"
    assert base["needs_reviewer0"] is False


def test_gemini_happy_path(tmp_path: Path) -> None:
    artifacts, evidence, w0_sha, _ = _create_valid_fixtures(
        tmp_path, reviewer_kind="gemini"
    )
    base = derive_worker_two_history_base(tmp_path, artifacts, evidence)
    assert base["feedback1_kind"] == "gemini"
    assert base["needs_reviewer0"] is True


def test_duplicate_conversation_id_rejection(tmp_path: Path) -> None:
    artifacts, evidence, _, _ = _create_valid_fixtures(tmp_path)
    evidence["worker_info"]["conversation_id"] = "conv-w0"
    evidence["worker_info"]["status"]["conversation_id"] = "conv-w0"
    with pytest.raises(ProposalError, match="duplicate conversation IDs detected"):
        derive_worker_two_history_base(tmp_path, artifacts, evidence)

    artifacts, evidence, _, _ = _create_valid_fixtures(tmp_path)
    evidence["reviewer_status"]["conversation_id"] = "conv-w1"
    with pytest.raises(ProposalError, match="duplicate conversation IDs detected"):
        derive_worker_two_history_base(tmp_path, artifacts, evidence)


def test_worker1_result_sha_mismatch(tmp_path: Path) -> None:
    artifacts, evidence, _, _ = _create_valid_fixtures(tmp_path)
    evidence["worker_info"]["result_sha"] = "2" * 64
    with pytest.raises(ProposalError, match="Worker1 result_sha mismatch"):
        derive_worker_two_history_base(tmp_path, artifacts, evidence)


def test_feedback_target_sha_mismatch(tmp_path: Path) -> None:
    artifacts, evidence, _, _ = _create_valid_fixtures(tmp_path)
    fb_path = (
        tmp_path
        / ".local"
        / "two-site-refactor"
        / "pipeline"
        / artifacts["original_run_prefix"]
        / "feedback-correction-1.json"
    )
    fb_data = {
        "reviewer": "test-gate",
        "reviewed_proposal_sha256": "f" * 64,
        "findings": ["mismatched SHA findings"],
        "test_evidence": [],
    }
    fb_path.write_text(json.dumps(fb_data), encoding="utf-8")
    with pytest.raises(ProposalError, match="correction feedback targets a different proposal"):
        derive_worker_two_history_base(tmp_path, artifacts, evidence)


def test_invalid_worker_index(tmp_path: Path) -> None:
    artifacts, evidence, _, _ = _create_valid_fixtures(tmp_path)
    artifacts["worker_index"] = 1
    with pytest.raises(ProposalError, match="worker_index must be exact int 2"):
        derive_worker_two_history_base(tmp_path, artifacts, evidence)


def test_invalid_worker_run_ids(tmp_path: Path) -> None:
    artifacts, evidence, _, _ = _create_valid_fixtures(tmp_path)
    evidence["worker_info"]["status"]["input_worker_run_id"] = "bad-worker-0"
    with pytest.raises(ProposalError, match="invalid input_worker_run_id"):
        derive_worker_two_history_base(tmp_path, artifacts, evidence)

    artifacts, evidence, _, _ = _create_valid_fixtures(tmp_path)
    artifacts["final_worker_run_id"] = artifacts["original_run_prefix"] + "-worker-2"
    with pytest.raises(ProposalError, match="invalid final_worker_run_id"):
        derive_worker_two_history_base(tmp_path, artifacts, evidence)


def test_codex_reviewer_fails_closed(tmp_path: Path) -> None:
    artifacts, evidence, _, _ = _create_valid_fixtures(tmp_path, reviewer_kind="codex")
    with pytest.raises(ProposalError, match="unsupported original-pipeline history reviewer"):
        derive_worker_two_history_base(tmp_path, artifacts, evidence)


def test_symlink_feedback_rejected(tmp_path: Path) -> None:
    artifacts, evidence, _, _ = _create_valid_fixtures(tmp_path)
    fb_path = (
        tmp_path
        / ".local"
        / "two-site-refactor"
        / "pipeline"
        / artifacts["original_run_prefix"]
        / "feedback-correction-1.json"
    )
    real_file = tmp_path / "real_feedback.json"
    real_file.write_text(fb_path.read_text(encoding="utf-8"), encoding="utf-8")
    fb_path.unlink()
    fb_path.symlink_to(real_file)
    with pytest.raises(ProposalError, match="symlink"):
        derive_worker_two_history_base(tmp_path, artifacts, evidence)


def test_hardlink_feedback_rejected(tmp_path: Path) -> None:
    artifacts, evidence, _, _ = _create_valid_fixtures(tmp_path)
    fb_path = (
        tmp_path
        / ".local"
        / "two-site-refactor"
        / "pipeline"
        / artifacts["original_run_prefix"]
        / "feedback-correction-1.json"
    )
    link_target = tmp_path / "hardlink_target.json"
    os.link(fb_path, link_target)
    with pytest.raises(ProposalError, match="hardlinked"):
        derive_worker_two_history_base(tmp_path, artifacts, evidence)
