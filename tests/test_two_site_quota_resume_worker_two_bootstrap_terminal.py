"""Tests for terminal Worker-2 bootstrap failure in proposal pipeline remainder."""
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
)

WORKER_SHA = "0" * 64
SAMPLE_CONTENT = "from api.routers import user_flags\n"


def _build_context(tmp_path: Path) -> tuple[dict, Path, Path, dict]:
    worker_res = {
        "status": "PROPOSAL_READY",
        "changes": [
            {
                "path": "tests/test_p1_00b_characterization_example.py",
                "original_sha256": None,
                "content": SAMPLE_CONTENT,
            }
        ],
    }
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    worker_dir = tmp_path / "worker"
    worker_dir.mkdir(parents=True, exist_ok=True)
    state = {
        "state": "TASK_BLOCKED",
        "correction_count": 2,
        "blockers": [],
    }
    return worker_res, evidence_dir, worker_dir, state


def test_worker_two_bootstrap_terminal_exhaustion_raises_before_provider_or_tests(
    tmp_path: Path,
) -> None:
    worker_res, evidence_dir, worker_dir, state = _build_context(tmp_path)
    findings = p1_00_characterization_bootstrap_findings(worker_res)
    assert findings
    assert any("before application imports" in item for item in findings)

    seen_bootstrap_sigs: set[str] = set()

    with (
        patch(
            "scripts.two_site_proposal_pipeline.run_provider_session",
            side_effect=AssertionError("run_provider_session must not be called"),
        ) as mock_provider,
        patch(
            "scripts.two_site_proposal_pipeline.persist_pre_review_tests",
            side_effect=AssertionError("persist_pre_review_tests must not be called"),
        ) as mock_persist,
    ):
        with pytest.raises(
            ProposalError,
            match="correction exhaustion: maximum 2 corrections exceeded",
        ):
            _run_post_worker_remainder(
                root=tmp_path.resolve(),
                task_candidate=tmp_path / "task.json",
                task={"test_selectors": ["tests/test_p1_00b_characterization_example.py"]},
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
                seen_test_signatures=set(),
                seen_bootstrap_signatures=seen_bootstrap_sigs,
            )

        mock_provider.assert_not_called()
        mock_persist.assert_not_called()
        assert not (evidence_dir / "feedback-correction-3.json").exists()
        assert state.get("state") != "READY_FOR_CODEX"
        assert len(seen_bootstrap_sigs) == 1


def test_worker_two_bootstrap_terminal_repeated_signature_raises_without_provider(
    tmp_path: Path,
) -> None:
    worker_res, evidence_dir, worker_dir, state = _build_context(tmp_path)
    findings = p1_00_characterization_bootstrap_findings(worker_res)
    assert findings

    sig = hashlib.sha256(
        json.dumps(findings, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    seen_bootstrap_sigs = {sig}

    with (
        patch(
            "scripts.two_site_proposal_pipeline.run_provider_session",
            side_effect=AssertionError("run_provider_session must not be called"),
        ) as mock_provider,
        patch(
            "scripts.two_site_proposal_pipeline.persist_pre_review_tests",
            side_effect=AssertionError("persist_pre_review_tests must not be called"),
        ) as mock_persist,
    ):
        with pytest.raises(
            ProposalError,
            match="repeated test bootstrap failure signature",
        ):
            _run_post_worker_remainder(
                root=tmp_path.resolve(),
                task_candidate=tmp_path / "task.json",
                task={"test_selectors": ["tests/test_p1_00b_characterization_example.py"]},
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
                current_worker=("ae03-c5d-w2-worker-2", WORKER_SHA, "{}", "conv-2"),
                seen_convs=set(),
                correction_count=2,
                seen_norm_findings=set(),
                seen_test_signatures=set(),
                seen_bootstrap_signatures=seen_bootstrap_sigs,
            )

        mock_provider.assert_not_called()
        mock_persist.assert_not_called()
        assert not (evidence_dir / "feedback-correction-3.json").exists()
        assert state.get("state") != "READY_FOR_CODEX"
