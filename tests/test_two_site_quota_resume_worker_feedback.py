"""Tests for load_bound_correction_feedback with real schema and negative cases."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
import pytest

from scripts.two_site_pre_review_receipt import (
    SandboxError,
    p1_00_characterization_bootstrap_findings,
)
from scripts.two_site_proposal_pipeline import (
    ProposalError,
    test_gate_feedback as producer_test_gate_feedback,
)
from scripts.two_site_quota_resume_worker_feedback import (
    MAX_FEEDBACK_BYTES,
    load_bound_correction_feedback,
    load_bound_test_gate_feedback,
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


def _make_bootstrap_artifacts(
    worker_index: int = 1,
    prefix: str = "run-20260925-120000",
    worker_retry: str = "",
) -> dict:
    prior_idx = worker_index - 1
    w_id = f"{prefix}-worker-{prior_idx}{worker_retry}"
    return {
        "correction_origin": "BOOTSTRAP",
        "worker_index": worker_index,
        "original_run_prefix": prefix,
        "final_worker_run_id": w_id,
        "final_reviewer_run_id": None,
        "proposal_path": f".local/two-site-refactor/proposals/{w_id}/result.json",
        "reviewer_path": None,
        "pre_review_test_path": None,
        "pre_review_test_sha256": None,
        "pre_review_test_state": None,
        "expected_feedback_path": f".local/two-site-refactor/pipeline/{prefix}/feedback-correction-{worker_index}.json",
        "proposal_sha256": "1" * 64,
    }


def _make_test_failed_artifacts(
    worker_index: int = 1,
    prefix: str = "run-20260925-120000",
    worker_retry: str = "",
    receipt_sha: str = "2" * 64,
) -> dict:
    prior_idx = worker_index - 1
    w_id = f"{prefix}-worker-{prior_idx}{worker_retry}"
    return {
        "correction_origin": "TEST_FAILED",
        "worker_index": worker_index,
        "original_run_prefix": prefix,
        "final_worker_run_id": w_id,
        "final_reviewer_run_id": None,
        "proposal_path": f".local/two-site-refactor/proposals/{w_id}/result.json",
        "reviewer_path": None,
        "pre_review_test_path": f".local/two-site-refactor/pipeline/{prefix}/pre-review-tests-{prior_idx}.json",
        "pre_review_test_sha256": receipt_sha,
        "pre_review_test_state": "FAILED",
        "expected_feedback_path": f".local/two-site-refactor/pipeline/{prefix}/feedback-correction-{worker_index}.json",
        "proposal_sha256": "1" * 64,
    }


def _make_bootstrap_worker_info(proposal_sha: str = "1" * 64) -> dict:
    bad_code = "import models\n\ndef test_dummy():\n    pass\n"
    return {
        "result": {
            "status": "PROPOSAL_READY",
            "summary": "real bootstrap defect test",
            "covered_requirements": [1],
            "findings": [],
            "changes": [
                {
                    "path": "tests/test_p1_00_characterization_sample.py",
                    "original_sha256": None,
                    "content": bad_code,
                    "replacements": [],
                }
            ],
        },
        "result_sha": proposal_sha,
    }


def _make_failed_receipt(proposal_sha: str = "1" * 64, rc: int = 1) -> dict:
    return {
        "state": "FAILED",
        "proposal_sha256": proposal_sha,
        "selectors": ["tests/test_sample.py"],
        "unsupported_specs": [],
        "returncode": rc,
        "stdout_tail": "FAILED tests/test_sample.py::test_case - AssertionError",
        "stderr_tail": "E   AssertionError: assert False",
        "snapshot_files": 1,
        "snapshot_bytes": 100,
        "isolation": "hermetic",
    }


def test_positive_test_gate_bootstrap_worker_1_and_worker_2():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        worker_info = _make_bootstrap_worker_info()
        bootstrap_findings = p1_00_characterization_bootstrap_findings(worker_info["result"])
        assert bootstrap_findings

        for idx, retry in ((1, ""), (2, "-retry-1")):
            art = _make_bootstrap_artifacts(worker_index=idx, worker_retry=retry)
            expected_fb = {
                "reviewer": "test-gate",
                "reviewed_proposal_sha256": art["proposal_sha256"],
                "findings": bootstrap_findings,
                "test_evidence": [
                    "gate=P1-00-characterization-bootstrap",
                    f"proposal_sha256={art['proposal_sha256']}",
                    f"finding_count={len(bootstrap_findings)}",
                ],
            }
            raw = _write_feedback(root, art["expected_feedback_path"], expected_fb)
            fb, sha, path = load_bound_test_gate_feedback(root, art, worker_info, None)
            assert fb == expected_fb
            assert sha == hashlib.sha256(raw).hexdigest()
            assert path == art["expected_feedback_path"]


def test_positive_test_gate_test_failed_worker_1_and_worker_2():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for idx, retry in ((1, ""), (2, "-retry-2")):
            art = _make_test_failed_artifacts(worker_index=idx, worker_retry=retry)
            receipt = _make_failed_receipt(proposal_sha=art["proposal_sha256"])
            expected_fb = producer_test_gate_feedback(receipt, art["proposal_sha256"])
            raw = _write_feedback(root, art["expected_feedback_path"], expected_fb)

            fb, sha, path = load_bound_test_gate_feedback(root, art, None, receipt)
            assert fb == expected_fb
            assert sha == hashlib.sha256(raw).hexdigest()
            assert path == art["expected_feedback_path"]


def test_negative_test_gate_mixed_and_invalid_origins():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        worker_info = _make_bootstrap_worker_info()

        # Origin not BOOTSTRAP or TEST_FAILED
        art_rev = _make_bootstrap_artifacts()
        art_rev["correction_origin"] = "REVIEWER"
        with pytest.raises(ProposalError, match="correction_origin"):
            load_bound_test_gate_feedback(root, art_rev, worker_info, None)

        # BOOTSTRAP with non-None receipt
        art_boot = _make_bootstrap_artifacts()
        receipt = _make_failed_receipt()
        with pytest.raises(ProposalError, match="BOOTSTRAP origin requires test_receipt to be None"):
            load_bound_test_gate_feedback(root, art_boot, worker_info, receipt)

        # BOOTSTRAP with non-None test artifacts fields
        for field, val in (
            ("pre_review_test_path", "path.json"),
            ("pre_review_test_sha256", "2" * 64),
            ("pre_review_test_state", "FAILED"),
            ("final_reviewer_run_id", "run-reviewer-0"),
            ("reviewer_path", "rev.json"),
        ):
            bad_art = dict(art_boot, **{field: val})
            with pytest.raises(ProposalError):
                load_bound_test_gate_feedback(root, bad_art, worker_info, None)

        # TEST_FAILED with None receipt
        art_failed = _make_test_failed_artifacts()
        with pytest.raises(ProposalError, match="TEST_FAILED origin requires test_receipt"):
            load_bound_test_gate_feedback(root, art_failed, None, None)

        # TEST_FAILED with PASSED receipt
        passed_receipt = _make_receipt(proposal_sha=art_failed["proposal_sha256"], state="PASSED", rc=0)
        with pytest.raises(ProposalError, match="state must be 'FAILED'"):
            load_bound_test_gate_feedback(root, art_failed, None, passed_receipt)

        # TEST_FAILED with pre_review_test_state PASSED
        art_bad_state = dict(art_failed, pre_review_test_state="PASSED")
        with pytest.raises(ProposalError, match="pre_review_test_state must be 'FAILED'"):
            load_bound_test_gate_feedback(root, art_bad_state, None, receipt)

        # TEST_FAILED with alias keys
        art_alias = dict(art_failed, attempted_index=1)
        with pytest.raises(ProposalError, match="alias"):
            load_bound_test_gate_feedback(root, art_alias, None, receipt)


def test_negative_test_gate_altered_feedback_and_hashes():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        worker_info = _make_bootstrap_worker_info()
        bootstrap_findings = p1_00_characterization_bootstrap_findings(worker_info["result"])
        art = _make_bootstrap_artifacts()
        expected_fb = {
            "reviewer": "test-gate",
            "reviewed_proposal_sha256": art["proposal_sha256"],
            "findings": bootstrap_findings,
            "test_evidence": [
                "gate=P1-00-characterization-bootstrap",
                f"proposal_sha256={art['proposal_sha256']}",
                f"finding_count={len(bootstrap_findings)}",
            ],
        }

        # Altered findings
        bad_findings = dict(expected_fb, findings=["tampered finding"])
        _write_feedback(root, art["expected_feedback_path"], bad_findings)
        with pytest.raises(ProposalError, match="findings do not match"):
            load_bound_test_gate_feedback(root, art, worker_info, None)

        # Altered test_evidence
        bad_evidence = dict(expected_fb, test_evidence=["tampered evidence"])
        _write_feedback(root, art["expected_feedback_path"], bad_evidence)
        with pytest.raises(ProposalError, match="test_evidence does not match"):
            load_bound_test_gate_feedback(root, art, worker_info, None)

        # Wrong reviewer
        bad_rev = dict(expected_fb, reviewer="gemini")
        _write_feedback(root, art["expected_feedback_path"], bad_rev)
        with pytest.raises(ProposalError, match="reviewer must be 'test-gate'"):
            load_bound_test_gate_feedback(root, art, worker_info, None)

        # Bad proposal SHA in artifacts
        _write_feedback(root, art["expected_feedback_path"], expected_fb)
        bad_sha_art = dict(art, proposal_sha256="not-a-valid-sha")
        with pytest.raises(ProposalError, match="proposal_sha256 must be 64 lowercase hex"):
            load_bound_test_gate_feedback(root, bad_sha_art, worker_info, None)

        # TEST_FAILED wrong pre_review_test_sha256
        art_failed = _make_test_failed_artifacts(receipt_sha="bad-sha")
        receipt = _make_failed_receipt(proposal_sha=art_failed["proposal_sha256"])
        with pytest.raises(ProposalError, match="pre_review_test_sha256 must be 64 lowercase hex"):
            load_bound_test_gate_feedback(root, art_failed, None, receipt)


def test_negative_test_gate_bootstrap_worker_info():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        art = _make_bootstrap_artifacts()
        fb_data = {
            "reviewer": "test-gate",
            "reviewed_proposal_sha256": art["proposal_sha256"],
            "findings": ["f"],
            "test_evidence": ["gate=P1-00-characterization-bootstrap", f"proposal_sha256={art['proposal_sha256']}", "finding_count=1"],
        }
        _write_feedback(root, art["expected_feedback_path"], fb_data)

        # worker_info is None
        with pytest.raises(ProposalError, match="worker_info must be a mapping"):
            load_bound_test_gate_feedback(root, art, None, None)

        # missing result in worker_info
        with pytest.raises(ProposalError, match="missing valid result mapping"):
            load_bound_test_gate_feedback(root, art, {}, None)

        # status != PROPOSAL_READY
        bad_res = {"result": {"status": "CHANGES_REQUIRED", "changes": []}}
        with pytest.raises(ProposalError, match="status must be 'PROPOSAL_READY'"):
            load_bound_test_gate_feedback(root, art, bad_res, None)

        # empty bootstrap findings
        empty_defects = {"result": {"status": "PROPOSAL_READY", "changes": []}}
        with pytest.raises(ProposalError, match="empty findings"):
            load_bound_test_gate_feedback(root, art, empty_defects, None)


def test_negative_test_gate_paths_links_oversize():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        worker_info = _make_bootstrap_worker_info()
        bootstrap_findings = p1_00_characterization_bootstrap_findings(worker_info["result"])
        art = _make_bootstrap_artifacts()
        expected_fb = {
            "reviewer": "test-gate",
            "reviewed_proposal_sha256": art["proposal_sha256"],
            "findings": bootstrap_findings,
            "test_evidence": [
                "gate=P1-00-characterization-bootstrap",
                f"proposal_sha256={art['proposal_sha256']}",
                f"finding_count={len(bootstrap_findings)}",
            ],
        }

        # proposal_path mismatch
        bad_prop = dict(art, proposal_path=".local/wrong/path.json")
        with pytest.raises(ProposalError, match="proposal_path mismatch"):
            load_bound_test_gate_feedback(root, bad_prop, worker_info, None)

        # expected_feedback_path mismatch
        bad_fb = dict(art, expected_feedback_path=".local/wrong/feedback.json")
        with pytest.raises(ProposalError, match="expected_feedback_path mismatch"):
            load_bound_test_gate_feedback(root, bad_fb, worker_info, None)

        # TEST_FAILED pre_review_test_path mismatch
        art_failed = _make_test_failed_artifacts()
        receipt = _make_failed_receipt(proposal_sha=art_failed["proposal_sha256"])
        bad_test_path = dict(art_failed, pre_review_test_path=".local/wrong/test.json")
        with pytest.raises(ProposalError, match="pre_review_test_path mismatch"):
            load_bound_test_gate_feedback(root, bad_test_path, None, receipt)

        # Symlink
        target = root / "real.json"
        target.write_bytes(json.dumps(expected_fb).encode("utf-8"))
        fb_file = root / art["expected_feedback_path"]
        fb_file.parent.mkdir(parents=True, exist_ok=True)
        os.symlink(target, fb_file)
        with pytest.raises(ProposalError, match="symlink"):
            load_bound_test_gate_feedback(root, art, worker_info, None)

        # Hardlink
        fb_file.unlink()
        os.link(target, fb_file)
        with pytest.raises(ProposalError, match="hardlink"):
            load_bound_test_gate_feedback(root, art, worker_info, None)

        # Oversize
        fb_file.unlink()
        fb_file.write_bytes(b"x" * (MAX_FEEDBACK_BYTES + 1))
        with pytest.raises(ProposalError, match="budget"):
            load_bound_test_gate_feedback(root, art, worker_info, None)


def test_negative_test_gate_detachment_and_no_mutation():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        worker_info = _make_bootstrap_worker_info()
        bootstrap_findings = p1_00_characterization_bootstrap_findings(worker_info["result"])
        art = _make_bootstrap_artifacts()
        expected_fb = {
            "reviewer": "test-gate",
            "reviewed_proposal_sha256": art["proposal_sha256"],
            "findings": bootstrap_findings,
            "test_evidence": [
                "gate=P1-00-characterization-bootstrap",
                f"proposal_sha256={art['proposal_sha256']}",
                f"finding_count={len(bootstrap_findings)}",
            ],
        }
        _write_feedback(root, art["expected_feedback_path"], expected_fb)

        art_copy = json.loads(json.dumps(art))
        worker_copy = json.loads(json.dumps(worker_info))
        fb, sha, path = load_bound_test_gate_feedback(root, art, worker_info, None)

        assert art == art_copy
        assert worker_info == worker_copy

        fb["findings"].append("extra")
        fb2, _, _ = load_bound_test_gate_feedback(root, art, worker_info, None)
        assert fb2["findings"] == expected_fb["findings"]
