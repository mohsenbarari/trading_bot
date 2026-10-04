"""Strict seam tests for _run_post_worker_remainder."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest.mock import patch
import pytest

try:
    from scripts.two_site_proposal_pipeline import (
        _run_post_worker_remainder,
        p1_00_characterization_bootstrap_findings,
    )
    from scripts.two_site_proposal_runner import ProposalError
    from scripts.two_site_pre_review_receipt import SandboxError
except ImportError:
    from two_site_proposal_pipeline import (
        _run_post_worker_remainder,
        p1_00_characterization_bootstrap_findings,
    )
    from two_site_proposal_runner import ProposalError
    from two_site_pre_review_receipt import SandboxError

PIPELINE_MOD = _run_post_worker_remainder.__module__


def _base_kwargs(tmp_path: Path) -> dict:
    evidence_dir = tmp_path / "evidence"
    worker_dir = tmp_path / "worker"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    worker_dir.mkdir(parents=True, exist_ok=True)
    return {
        "root": tmp_path,
        "task_candidate": tmp_path / "task_candidate.json",
        "task": {"task_id": "test-task"},
        "sources": {},
        "initial_head": "0" * 40,
        "initial_refs": {},
        "run_prefix": "run-20260925-001",
        "timeout": 30,
        "cli": None,
        "state": {"state": "IN_PROGRESS"},
        "evidence_dir": evidence_dir,
        "state_check": lambda: None,
        "worker_dir": worker_dir,
        "worker_res": {"changes": []},
        "current_worker": ("worker-run-1", "a" * 64, "worker-text", "conv-1"),
        "seen_convs": set(),
        "correction_count": 0,
        "seen_norm_findings": set(),
        "seen_test_signatures": set(),
        "seen_bootstrap_signatures": set(),
    }


@pytest.mark.parametrize(
    "missing_arg",
    [
        "correction_count",
        "seen_norm_findings",
        "seen_test_signatures",
        "seen_bootstrap_signatures",
    ],
)
def test_missing_mandatory_args_raises_type_error_before_provider(tmp_path, missing_arg):
    kwargs = _base_kwargs(tmp_path)
    del kwargs[missing_arg]
    with patch(f"{PIPELINE_MOD}.run_provider_session") as mock_provider:
        with pytest.raises(TypeError, match="required keyword-only argument"):
            _run_post_worker_remainder(**kwargs)
        mock_provider.assert_not_called()


def test_exhausted_correction_with_bootstrap_finding_fails_closed(tmp_path):
    kwargs = _base_kwargs(tmp_path)
    kwargs["correction_count"] = 2
    evidence_dir = kwargs["evidence_dir"]
    seen_bootstrap = kwargs["seen_bootstrap_signatures"]

    finding_msg = "tests/test_p1_00_characterization_sample.py: missing network fence"
    with patch(
        f"{PIPELINE_MOD}.p1_00_characterization_bootstrap_findings",
        return_value=[finding_msg],
    ), patch(f"{PIPELINE_MOD}.run_provider_session") as mock_provider:
        with pytest.raises(
            ProposalError, match="correction exhaustion: maximum 2 corrections exceeded"
        ):
            _run_post_worker_remainder(**kwargs)
        mock_provider.assert_not_called()

    assert list(evidence_dir.glob("feedback-*.json")) == []
    expected_sig = hashlib.sha256(
        json.dumps([finding_msg], ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    assert expected_sig in seen_bootstrap


def test_verified_path_with_passed_receipt_selects_one_reviewer(tmp_path):
    kwargs = _base_kwargs(tmp_path)
    evidence_dir = kwargs["evidence_dir"]
    reviewer_dir = tmp_path / "reviewer"
    reviewer_dir.mkdir(parents=True, exist_ok=True)
    worker_result_path = kwargs["worker_dir"] / "result.json"
    worker_result_path.write_text("{}", encoding="utf-8")
    receipt_path = evidence_dir / "pre-review-test-receipt.json"

    worker_sha = kwargs["current_worker"][1]
    receipt = {
        "state": "PASSED",
        "proposal_sha256": worker_sha,
        "selectors": ["tests/test_pipeline.py"],
        "unsupported_specs": [],
        "returncode": 0,
        "stdout_tail": "",
        "stderr_tail": "",
        "snapshot_files": 0,
        "snapshot_bytes": 0,
        "isolation": "network-fence",
    }
    receipt_bytes = json.dumps(receipt, ensure_ascii=False, indent=2).encode("utf-8")
    receipt_path.write_bytes(receipt_bytes)
    receipt_sha = hashlib.sha256(receipt_bytes).hexdigest()

    with patch(
        f"{PIPELINE_MOD}.p1_00_characterization_bootstrap_findings",
        return_value=[],
    ), patch(
        f"{PIPELINE_MOD}.persist_pre_review_tests",
        return_value=(receipt, receipt_path, receipt_sha),
    ) as mock_persist, patch(
        f"{PIPELINE_MOD}.run_provider_session",
        return_value=("rev-run-0", reviewer_dir),
    ) as mock_provider, patch(
        f"{PIPELINE_MOD}.validate_run",
        return_value=({"status": "VERIFIED", "findings": []}, {"conversation_id": "c1"}, "c" * 64, ""),
    ) as mock_validate, patch(
        f"{PIPELINE_MOD}.check_state",
    ):
        _run_post_worker_remainder(**kwargs)

    mock_persist.assert_called_once()
    assert mock_provider.call_count == 1
    assert mock_provider.call_args[0][0] == kwargs["root"]
    assert mock_provider.call_args[0][1] == kwargs["task_candidate"]
    assert mock_provider.call_args[0][2] == "verifier"
    assert mock_provider.call_args[0][3] == f"{kwargs['run_prefix']}-reviewer-0"
    assert mock_provider.call_args[0][4] == kwargs["timeout"]
    assert mock_provider.call_args[1]["proposal"] == worker_result_path
    assert mock_provider.call_args[1]["test_evidence"] == receipt_path
    assert mock_provider.call_args[1]["expected_head"] == kwargs["initial_head"]
    assert hashlib.sha256(receipt_path.read_bytes()).hexdigest() == receipt_sha
    assert json.loads(receipt_path.read_text(encoding="utf-8")) == receipt
    assert receipt["proposal_sha256"] == worker_sha

    mock_validate.assert_called_once()
    assert mock_validate.call_args[1]["expected_test_receipt"] == receipt
    assert mock_validate.call_args[1]["expected_test_evidence_sha"] == receipt_sha
    assert mock_validate.call_args[1]["expected_test_receipt"]["proposal_sha256"] == worker_sha
    assert mock_validate.call_args[1]["prior_worker"] == kwargs["current_worker"]

    assert list(evidence_dir.glob("feedback-*.json")) == []
    assert kwargs["state"]["state"] == "READY_FOR_CODEX"
    assert kwargs["state"]["blockers"] == []


def test_malformed_two_key_receipt_rejected_before_provider(tmp_path):
    kwargs = _base_kwargs(tmp_path)
    evidence_dir = kwargs["evidence_dir"]
    receipt_path = evidence_dir / "pre-review-test-receipt.json"
    receipt_path.write_text("{}", encoding="utf-8")
    malformed_receipt = {"state": "PASSED", "selectors": ["tests/test_pipeline.py"]}
    receipt_sha = "b" * 64

    with patch(
        f"{PIPELINE_MOD}.p1_00_characterization_bootstrap_findings",
        return_value=[],
    ), patch(
        f"{PIPELINE_MOD}.persist_pre_review_tests",
        return_value=(malformed_receipt, receipt_path, receipt_sha),
    ), patch(
        f"{PIPELINE_MOD}.run_provider_session",
    ) as mock_provider:
        with pytest.raises(SandboxError, match="receipt keys must match expected schema exactly"):
            _run_post_worker_remainder(**kwargs)

    mock_provider.assert_not_called()
    assert list(evidence_dir.glob("feedback-*.json")) == []
    assert kwargs["state"]["state"] == "IN_PROGRESS"
    assert kwargs["state"]["state"] != "READY_FOR_CODEX"
