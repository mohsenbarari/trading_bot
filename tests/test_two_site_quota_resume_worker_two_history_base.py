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
    correction_origin: str | None = None,
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
    if correction_origin is not None:
        artifacts["correction_origin"] = correction_origin
        evidence["correction_origin"] = correction_origin
        if correction_origin in ("BOOTSTRAP", "TEST_FAILED"):
            artifacts["final_reviewer_run_id"] = None
            evidence.pop("reviewer_status", None)
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
    assert base["correction_origin"] == "REVIEWER"
    assert base["needs_reviewer1"] is True


def test_gemini_happy_path(tmp_path: Path) -> None:
    artifacts, evidence, w0_sha, _ = _create_valid_fixtures(
        tmp_path, reviewer_kind="gemini"
    )
    base = derive_worker_two_history_base(tmp_path, artifacts, evidence)
    assert base["feedback1_kind"] == "gemini"
    assert base["needs_reviewer0"] is True
    assert base["correction_origin"] == "REVIEWER"
    assert base["needs_reviewer1"] is True


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


@pytest.mark.parametrize(
    ("bad_run_id", "is_missing"),
    [
        pytest.param(None, True, id="missing"),
        pytest.param(None, False, id="none"),
        pytest.param("", False, id="empty"),
        pytest.param("wrong-worker-1", False, id="wrong"),
    ],
)
def test_worker1_status_run_id_negative(
    tmp_path: Path, bad_run_id: str | None, is_missing: bool
) -> None:
    artifacts, evidence, _, _ = _create_valid_fixtures(tmp_path)
    if is_missing:
        evidence["worker_info"]["status"].pop("run_id", None)
    else:
        evidence["worker_info"]["status"]["run_id"] = bad_run_id
    with pytest.raises(ProposalError, match="Worker1 status run_id mismatch"):
        derive_worker_two_history_base(tmp_path, artifacts, evidence)


@pytest.mark.parametrize(
    ("bad_run_id", "is_missing"),
    [
        pytest.param(None, True, id="missing"),
        pytest.param(None, False, id="none"),
        pytest.param("", False, id="empty"),
        pytest.param("wrong-reviewer-1", False, id="wrong"),
    ],
)
def test_reviewer1_status_run_id_negative(
    tmp_path: Path, bad_run_id: str | None, is_missing: bool
) -> None:
    artifacts, evidence, _, _ = _create_valid_fixtures(tmp_path)
    if is_missing:
        evidence["reviewer_status"].pop("run_id", None)
    else:
        evidence["reviewer_status"]["run_id"] = bad_run_id
    with pytest.raises(ProposalError, match="Reviewer1 status run_id mismatch"):
        derive_worker_two_history_base(tmp_path, artifacts, evidence)


def test_reviewer_explicit_origin_happy_path(tmp_path: Path) -> None:
    artifacts, evidence, _, _ = _create_valid_fixtures(
        tmp_path, reviewer_kind="test-gate", correction_origin="REVIEWER"
    )
    base = derive_worker_two_history_base(tmp_path, artifacts, evidence)
    assert base["correction_origin"] == "REVIEWER"
    assert base["needs_reviewer1"] is True
    assert base["reviewer1_id"] == artifacts["final_reviewer_run_id"]
    assert base["reviewer1_run_id"] == artifacts["final_reviewer_run_id"]
    assert base["reviewer1_conversation_id"] == "conv-r1"


@pytest.mark.parametrize("origin", ["BOOTSTRAP", "TEST_FAILED"])
@pytest.mark.parametrize("reviewer_kind", ["test-gate", "gemini"])
def test_non_reviewer_origins_matrix(
    tmp_path: Path, origin: str, reviewer_kind: str
) -> None:
    artifacts, evidence, w0_sha, _ = _create_valid_fixtures(
        tmp_path, reviewer_kind=reviewer_kind, correction_origin=origin
    )
    base = derive_worker_two_history_base(tmp_path, artifacts, evidence)
    assert base["correction_origin"] == origin
    assert base["needs_reviewer1"] is False
    assert base["reviewer1_id"] is None
    assert base["reviewer1_run_id"] is None
    assert base["reviewer1_conversation_id"] is None
    assert base["worker0_run_id"] == artifacts["original_run_prefix"] + "-worker-0"
    assert base["worker1_run_id"] == artifacts["final_worker_run_id"]
    assert base["worker0_conversation_id"] == "conv-w0"
    assert base["worker1_conversation_id"] == "conv-w1"
    assert base["prior_worker0_sha"] == w0_sha
    assert base["feedback1_kind"] == reviewer_kind
    assert base["needs_reviewer0"] is (reviewer_kind == "gemini")


@pytest.mark.parametrize("origin", ["BOOTSTRAP", "TEST_FAILED"])
def test_forged_reviewer_fields_rejected_for_non_reviewer(
    tmp_path: Path, origin: str
) -> None:
    # 1. artifacts final_reviewer_run_id present
    artifacts, evidence, _, _ = _create_valid_fixtures(
        tmp_path, correction_origin=origin
    )
    artifacts["final_reviewer_run_id"] = f"{artifacts['original_run_prefix']}-reviewer-1"
    with pytest.raises(ProposalError, match="artifacts final_reviewer_run_id must be None"):
        derive_worker_two_history_base(tmp_path, artifacts, evidence)

    # 2. artifacts reviewer_path present
    artifacts, evidence, _, _ = _create_valid_fixtures(
        tmp_path, correction_origin=origin
    )
    artifacts["reviewer_path"] = "proposals/reviewer-1/result.json"
    with pytest.raises(ProposalError, match="artifacts reviewer_path must be None"):
        derive_worker_two_history_base(tmp_path, artifacts, evidence)

    # 3. evidence reviewer_status present
    artifacts, evidence, _, _ = _create_valid_fixtures(
        tmp_path, correction_origin=origin
    )
    evidence["reviewer_status"] = {"run_id": "rev-1", "conversation_id": "conv-r"}
    with pytest.raises(ProposalError, match="evidence reviewer_status must be None"):
        derive_worker_two_history_base(tmp_path, artifacts, evidence)

    # 4. evidence reviewer_result present
    artifacts, evidence, _, _ = _create_valid_fixtures(
        tmp_path, correction_origin=origin
    )
    evidence["reviewer_result"] = {"status": "PROPOSAL_READY"}
    with pytest.raises(ProposalError, match="evidence reviewer_result must be None"):
        derive_worker_two_history_base(tmp_path, artifacts, evidence)

    # 5. evidence reviewer_sha/sha256/result_sha present
    for sha_key in ("reviewer_sha", "reviewer_sha256", "reviewer_result_sha"):
        artifacts, evidence, _, _ = _create_valid_fixtures(
            tmp_path, correction_origin=origin
        )
        evidence[sha_key] = "0" * 64
        with pytest.raises(ProposalError, match="evidence reviewer sha must be None"):
            derive_worker_two_history_base(tmp_path, artifacts, evidence)


def test_missing_reviewer_metadata_for_reviewer_origin(tmp_path: Path) -> None:
    # missing final_reviewer_run_id in artifacts
    artifacts, evidence, _, _ = _create_valid_fixtures(tmp_path)
    artifacts.pop("final_reviewer_run_id", None)
    with pytest.raises(ProposalError, match="missing or non-string final_reviewer_run_id"):
        derive_worker_two_history_base(tmp_path, artifacts, evidence)

    # missing reviewer_status in evidence
    artifacts, evidence, _, _ = _create_valid_fixtures(tmp_path)
    evidence.pop("reviewer_status", None)
    with pytest.raises(ProposalError, match="evidence missing valid reviewer_status Mapping"):
        derive_worker_two_history_base(tmp_path, artifacts, evidence)


def test_mismatched_and_unknown_correction_origin(tmp_path: Path) -> None:
    # unknown origin in artifacts
    artifacts, evidence, _, _ = _create_valid_fixtures(tmp_path)
    artifacts["correction_origin"] = "UNKNOWN_ORIGIN"
    with pytest.raises(ProposalError, match="unknown correction_origin"):
        derive_worker_two_history_base(tmp_path, artifacts, evidence)

    # mismatch between artifacts and evidence
    artifacts, evidence, _, _ = _create_valid_fixtures(
        tmp_path, correction_origin="BOOTSTRAP"
    )
    evidence["correction_origin"] = "TEST_FAILED"
    with pytest.raises(ProposalError, match="correction_origin mismatch"):
        derive_worker_two_history_base(tmp_path, artifacts, evidence)

    # evidence has origin when artifacts does not
    artifacts, evidence, _, _ = _create_valid_fixtures(tmp_path)
    evidence["correction_origin"] = "REVIEWER"
    with pytest.raises(ProposalError, match="correction_origin mismatch"):
        derive_worker_two_history_base(tmp_path, artifacts, evidence)


@pytest.mark.parametrize("origin", ["BOOTSTRAP", "TEST_FAILED"])
def test_duplicate_w0_w1_conversations_rejected_non_reviewer(
    tmp_path: Path, origin: str
) -> None:
    artifacts, evidence, _, _ = _create_valid_fixtures(
        tmp_path, correction_origin=origin
    )
    evidence["worker_info"]["conversation_id"] = "same-conv"
    evidence["worker_info"]["status"]["conversation_id"] = "same-conv"
    evidence["worker_info"]["status"]["input_worker_conversation_id"] = "same-conv"
    with pytest.raises(ProposalError, match="duplicate conversation IDs detected"):
        derive_worker_two_history_base(tmp_path, artifacts, evidence)
