"""Tests for load_bound_correction_feedback with real schema and negative cases."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
import pytest

from scripts.two_site_pre_review_receipt import SandboxError
from scripts.two_site_proposal_pipeline import ProposalError
from scripts.two_site_quota_resume_worker_feedback import (
    MAX_FEEDBACK_BYTES,
    load_bound_correction_feedback,
)


def _make_artifacts(
    worker_index: int = 1,
    prefix: str = "run-20260925-120000",
    worker_retry: str = "",
    reviewer_retry: str = "",
) -> dict:
    prior_idx = worker_index - 1
    w_id = f"{prefix}-worker-{prior_idx}{worker_retry}"
    r_id = f"{prefix}-reviewer-{prior_idx}{reviewer_retry}"
    return {
        "worker_index": worker_index,
        "original_run_prefix": prefix,
        "final_worker_run_id": w_id,
        "final_reviewer_run_id": r_id,
        "proposal_path": f".local/two-site-refactor/proposals/{w_id}/result.json",
        "reviewer_path": f".local/two-site-refactor/proposals/{r_id}/result.json",
        "pre_review_test_path": f".local/two-site-refactor/pipeline/{prefix}/pre-review-tests-{prior_idx}.json",
        "expected_feedback_path": f".local/two-site-refactor/pipeline/{prefix}/feedback-correction-{worker_index}.json",
        "proposal_sha256": "1" * 64,
        "pre_review_test_sha256": "2" * 64,
        "pre_review_test_state": "PASSED",
    }


def _make_receipt(proposal_sha: str = "1" * 64, state: str = "PASSED", rc: int = 0) -> dict:
    return {
        "state": state,
        "proposal_sha256": proposal_sha,
        "selectors": ["tests/test_sample.py"],
        "unsupported_specs": [],
        "returncode": rc,
        "stdout_tail": "ok",
        "stderr_tail": "",
        "snapshot_files": 1,
        "snapshot_bytes": 100,
        "isolation": "hermetic",
    }


def _write_feedback(root: Path, rel_path: str, data: dict) -> bytes:
    p = root / rel_path
    p.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(data).encode("utf-8")
    p.write_bytes(raw)
    return raw


def test_positive_worker_1_and_worker_2_with_retries():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        findings = ["fix boundary check", "add test coverage"]

        # Worker 1
        art1 = _make_artifacts(worker_index=1)
        fb_data1 = {
            "reviewer": "gemini",
            "reviewed_proposal_sha256": art1["proposal_sha256"],
            "findings": findings,
            "test_evidence": [
                f"receipt_sha256={art1['pre_review_test_sha256']} "
                f"proposal_sha256={art1['proposal_sha256']} selector_count=1"
            ],
        }
        raw1 = _write_feedback(root, art1["expected_feedback_path"], fb_data1)
        rev_res = {"status": "CHANGES_REQUIRED", "findings": findings}
        receipt1 = _make_receipt(art1["proposal_sha256"])

        fb, sha, path = load_bound_correction_feedback(root, art1, rev_res, receipt1)
        assert fb == fb_data1
        assert sha == hashlib.sha256(raw1).hexdigest()
        assert path == art1["expected_feedback_path"]

        # Worker 2 with retries
        art2 = _make_artifacts(worker_index=2, worker_retry="-retry-1", reviewer_retry="-retry-2")
        fb_data2 = {
            "reviewer": "gemini",
            "reviewed_proposal_sha256": art2["proposal_sha256"],
            "findings": findings,
            "test_evidence": [
                f"receipt_sha256={art2['pre_review_test_sha256']} "
                f"proposal_sha256={art2['proposal_sha256']} selector_count=1"
            ],
        }
        raw2 = _write_feedback(root, art2["expected_feedback_path"], fb_data2)
        receipt2 = _make_receipt(art2["proposal_sha256"])

        fb2, sha2, path2 = load_bound_correction_feedback(root, art2, rev_res, receipt2)
        assert fb2 == fb_data2
        assert sha2 == hashlib.sha256(raw2).hexdigest()
        assert path2 == art2["expected_feedback_path"]


def test_negative_task_250_invented_alias_only_dict():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        alias_dict = {
            "attempted_index": 1,
            "original_prefix": "run-20260925-120000",
            "prior_worker_id": "run-20260925-120000-worker-0",
            "prior_reviewer_id": "run-20260925-120000-reviewer-0",
            "proposal_path": ".local/two-site-refactor/proposals/run-20260925-120000-worker-0/result.json",
            "reviewer_path": ".local/two-site-refactor/proposals/run-20260925-120000-reviewer-0/result.json",
            "pre_review_test_path": ".local/two-site-refactor/pipeline/run-20260925-120000/pre-review-tests-0.json",
            "expected_feedback_path": ".local/two-site-refactor/pipeline/run-20260925-120000/feedback-correction-1.json",
            "proposal_sha256": "1" * 64,
            "pre_review_test_sha256": "2" * 64,
            "pre_review_test_state": "PASSED",
        }
        rev_res = {"status": "CHANGES_REQUIRED", "findings": ["fix"]}
        receipt = _make_receipt(alias_dict["proposal_sha256"])
        with pytest.raises(ProposalError, match="alias"):
            load_bound_correction_feedback(root, alias_dict, rev_res, receipt)


def test_negative_wrong_id_and_path():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        rev_res = {"status": "CHANGES_REQUIRED", "findings": ["fix"]}
        receipt = _make_receipt()

        # Bad worker id
        art_bad_id = _make_artifacts()
        art_bad_id["final_worker_run_id"] = "bad-worker-id"
        with pytest.raises(ProposalError):
            load_bound_correction_feedback(root, art_bad_id, rev_res, receipt)

        # Path mismatch
        art_bad_path = _make_artifacts()
        art_bad_path["proposal_path"] = "wrong/proposal/path"
        with pytest.raises(ProposalError):
            load_bound_correction_feedback(root, art_bad_path, rev_res, receipt)

        # Expected feedback path mismatch
        art_bad_fb_path = _make_artifacts()
        art_bad_fb_path["expected_feedback_path"] = "wrong/fb/path"
        with pytest.raises(ProposalError):
            load_bound_correction_feedback(root, art_bad_fb_path, rev_res, receipt)


def test_negative_sha_and_reviewer_status_findings():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        art = _make_artifacts()
        receipt = _make_receipt(art["proposal_sha256"])

        # Bad proposal SHA
        art_bad_sha = dict(art, proposal_sha256="not_a_valid_sha")
        with pytest.raises(ProposalError):
            load_bound_correction_feedback(root, art_bad_sha, {"status": "CHANGES_REQUIRED", "findings": ["f"]}, receipt)

        # Bad pre-review test SHA
        art_bad_test_sha = dict(art, pre_review_test_sha256="invalid")
        with pytest.raises(ProposalError):
            load_bound_correction_feedback(root, art_bad_test_sha, {"status": "CHANGES_REQUIRED", "findings": ["f"]}, receipt)

        # Reviewer status not CHANGES_REQUIRED
        with pytest.raises(ProposalError):
            load_bound_correction_feedback(root, art, {"status": "VERIFIED", "findings": ["f"]}, receipt)

        # Reviewer findings empty
        with pytest.raises(ProposalError):
            load_bound_correction_feedback(root, art, {"status": "CHANGES_REQUIRED", "findings": []}, receipt)

        # Reviewer in feedback is not gemini
        fb_bad_rev = {
            "reviewer": "codex",
            "reviewed_proposal_sha256": art["proposal_sha256"],
            "findings": ["f"],
            "test_evidence": [
                f"receipt_sha256={art['pre_review_test_sha256']} proposal_sha256={art['proposal_sha256']} selector_count=1"
            ],
        }
        _write_feedback(root, art["expected_feedback_path"], fb_bad_rev)
        with pytest.raises(ProposalError):
            load_bound_correction_feedback(root, art, {"status": "CHANGES_REQUIRED", "findings": ["f"]}, receipt)

        # Feedback findings do not match reviewer_result findings
        fb_mismatch = dict(fb_bad_rev, reviewer="gemini", findings=["different"])
        _write_feedback(root, art["expected_feedback_path"], fb_mismatch)
        with pytest.raises(ProposalError):
            load_bound_correction_feedback(root, art, {"status": "CHANGES_REQUIRED", "findings": ["f"]}, receipt)


def test_negative_receipt_state_and_test_evidence():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        art = _make_artifacts()
        findings = ["f"]
        rev_res = {"status": "CHANGES_REQUIRED", "findings": findings}
        fb_data = {
            "reviewer": "gemini",
            "reviewed_proposal_sha256": art["proposal_sha256"],
            "findings": findings,
            "test_evidence": [
                f"receipt_sha256={art['pre_review_test_sha256']} proposal_sha256={art['proposal_sha256']} selector_count=1"
            ],
        }
        _write_feedback(root, art["expected_feedback_path"], fb_data)

        # Artifact pre_review_test_state != PASSED
        art_failed = dict(art, pre_review_test_state="FAILED")
        with pytest.raises(ProposalError):
            load_bound_correction_feedback(root, art_failed, rev_res, _make_receipt(art["proposal_sha256"]))

        # Receipt state FAILED
        bad_receipt = _make_receipt(art["proposal_sha256"], state="FAILED", rc=1)
        with pytest.raises(SandboxError):
            load_bound_correction_feedback(root, art, rev_res, bad_receipt)

        # Feedback test_evidence mismatch
        fb_bad_ev = dict(fb_data, test_evidence=["wrong_evidence"])
        _write_feedback(root, art["expected_feedback_path"], fb_bad_ev)
        with pytest.raises(ProposalError):
            load_bound_correction_feedback(root, art, rev_res, _make_receipt(art["proposal_sha256"]))


def test_negative_symlink_hardlink_oversize():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        art = _make_artifacts()
        findings = ["fix"]
        rev_res = {"status": "CHANGES_REQUIRED", "findings": findings}
        receipt = _make_receipt(art["proposal_sha256"])
        fb_data = {
            "reviewer": "gemini",
            "reviewed_proposal_sha256": art["proposal_sha256"],
            "findings": findings,
            "test_evidence": [
                f"receipt_sha256={art['pre_review_test_sha256']} proposal_sha256={art['proposal_sha256']} selector_count=1"
            ],
        }

        target_file = root / "real_file.json"
        target_file.write_bytes(json.dumps(fb_data).encode("utf-8"))
        fb_path = root / art["expected_feedback_path"]
        fb_path.parent.mkdir(parents=True, exist_ok=True)

        # Symlink
        if fb_path.exists():
            fb_path.unlink()
        os.symlink(target_file, fb_path)
        with pytest.raises(ProposalError, match="symlink"):
            load_bound_correction_feedback(root, art, rev_res, receipt)

        # Hardlink
        fb_path.unlink()
        os.link(target_file, fb_path)
        with pytest.raises(ProposalError, match="hardlink"):
            load_bound_correction_feedback(root, art, rev_res, receipt)

        # Oversize
        fb_path.unlink()
        fb_path.write_bytes(b"x" * (MAX_FEEDBACK_BYTES + 1))
        with pytest.raises(ProposalError, match="budget"):
            load_bound_correction_feedback(root, art, rev_res, receipt)


def test_reject_symlink_hardlink_and_oversize():
    test_negative_symlink_hardlink_oversize()


def test_reject_max_bytes_keyword_argument():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        art = _make_artifacts()
        rev_res = {"status": "CHANGES_REQUIRED", "findings": ["fix"]}
        receipt = _make_receipt(art["proposal_sha256"])
        with pytest.raises(TypeError, match="max_bytes"):
            load_bound_correction_feedback(root, art, rev_res, receipt, max_bytes=65536)
