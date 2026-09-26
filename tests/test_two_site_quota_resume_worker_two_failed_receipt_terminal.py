"""Tests for terminal Worker-2 FAILED receipt in proposal pipeline remainder."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest.mock import patch
import pytest

from scripts.two_site_pre_review_receipt import (
    p1_00_characterization_bootstrap_findings,
)
from scripts.two_site_proposal_pipeline import (
    ProposalError,
    _run_post_worker_remainder,
    test_failure_signature as compute_failure_signature,
)

WORKER_SHA = "0" * 64
VALID_FAILED_RECEIPT = {
    "state": "FAILED",
    "proposal_sha256": WORKER_SHA,
    "selectors": ["tests/test_a.py"],
    "unsupported_specs": [],
    "returncode": 1,
    "stdout_tail": "FAILED tests/test_a.py::test_fail\nE AssertionError: check failed\n> assert False\n",
    "stderr_tail": "",
    "snapshot_files": 1,
    "snapshot_bytes": 10,
    "isolation": "bwrap",
}


def test_worker_two_failed_receipt_terminal_exhaustion(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    evidence_dir = (root / "evidence").resolve()
    evidence_dir.mkdir(parents=True, exist_ok=True)
    worker_dir = (root / "worker").resolve()
    worker_dir.mkdir(parents=True, exist_ok=True)
    task_candidate = root / "task.json"

    worker_res = {
        "status": "PROPOSAL_READY",
        "changes": [
            {
                "path": "tests/test_a.py",
                "original_sha256": None,
                "content": "def test_ok(): pass\n",
            }
        ],
    }
    task = {"test_selectors": ["tests/test_a.py"]}
    state = {
        "state": "TASK_BLOCKED",
        "correction_count": 2,
        "blockers": [],
    }

    assert p1_00_characterization_bootstrap_findings(worker_res) == []

    seen_test_signatures: set[str] = set()

    with (
        patch(
            "scripts.two_site_proposal_pipeline.run_candidate_tests",
            return_value=dict(VALID_FAILED_RECEIPT),
        ) as mock_candidate_tests,
        patch(
            "scripts.two_site_proposal_pipeline.run_provider_session",
            side_effect=AssertionError("run_provider_session must not be called"),
        ) as mock_provider,
    ):
        with pytest.raises(
            ProposalError,
            match="correction exhaustion: maximum 2 corrections exceeded",
        ):
            _run_post_worker_remainder(
                root=root,
                task_candidate=task_candidate,
                task=task,
                sources={},
                initial_head="592402268249dee448c9304bd9e6b10c52d9fafe",
                initial_refs={},
                run_prefix="ae03-c5d-w2-test",
                timeout=300,
                cli=None,
                state=state,
                evidence_dir=evidence_dir,
                state_check=lambda: None,
                worker_dir=worker_dir,
                worker_res=worker_res,
                current_worker=("ae03-c5d-w2-worker-2", WORKER_SHA, "{}", "conv-1"),
                seen_convs=set(),
                correction_count=2,
                seen_norm_findings=set(),
                seen_test_signatures=seen_test_signatures,
                seen_bootstrap_signatures=set(),
            )

        mock_candidate_tests.assert_called_once_with(
            root, task, worker_res, WORKER_SHA, timeout=1200
        )
        mock_provider.assert_not_called()

        receipt_path = evidence_dir / "pre-review-tests-2.json"
        assert receipt_path.is_file()

        raw_bytes = receipt_path.read_bytes()
        receipt_sha = hashlib.sha256(raw_bytes).hexdigest()
        parsed_receipt = json.loads(raw_bytes.decode("utf-8"))

        assert parsed_receipt == VALID_FAILED_RECEIPT
        assert parsed_receipt["state"] == "FAILED"
        assert state.get("pre_review_test_path") == receipt_path.relative_to(root).as_posix()
        assert state.get("pre_review_test_sha256") == receipt_sha
        assert state.get("pre_review_test_state") == "FAILED"

        expected_sig = compute_failure_signature(VALID_FAILED_RECEIPT)
        assert seen_test_signatures == {expected_sig}
        assert len(seen_test_signatures) == 1

        assert not (evidence_dir / "feedback-correction-3.json").exists()
        assert state.get("state") != "READY_FOR_CODEX"
