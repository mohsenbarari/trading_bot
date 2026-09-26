"""Tests for terminal Worker-2 positive Reviewer-2 verification in remainder."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest.mock import patch
import pytest

from scripts.two_site_proposal_pipeline import (
    _run_post_worker_remainder,
    validate_run,
)
from scripts.two_site_proposal_runner import file_sha256
from tests.test_two_site_proposal_pipeline import (
    HEAD,
    make_runner,
    passed_receipt,
)


def test_worker_two_reviewer_positive_terminal(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    evidence_dir = (root / "evidence").resolve()
    evidence_dir.mkdir(parents=True, exist_ok=True)
    task_candidate = root / "task.json"
    src_file = root / "src.py"
    src_file.write_text("# orig\n")

    task = {
        "task_id": "T1",
        "goal": "G",
        "context_files": ["src.py"],
        "allowed_files": ["src.py"],
        "requirements": ["R1"],
        "test_selectors": ["tests/test_a.py"],
    }
    task_candidate.write_text(json.dumps(task))
    sources = {"src.py": {"sha256": file_sha256(src_file), "content": "# orig\n"}}

    run_prefix = "run-w2"
    worker_run_id = f"{run_prefix}-worker-2"
    reviewer_run_id = f"{run_prefix}-reviewer-2"

    runner, calls = make_runner(
        root,
        [
            {"content": "# worker 2\n"},
            {"status": "VERIFIED"},
        ],
    )

    worker_dir = runner(root, task_candidate, "worker", worker_run_id, 300)
    seen_convs: set[str] = set()
    worker_res, worker_st, worker_sha, worker_txt = validate_run(
        root, worker_dir, "worker", worker_run_id, HEAD, task, sources, seen_convs
    )
    current_worker = (worker_run_id, worker_sha, worker_txt, worker_st["conversation_id"])

    state = {
        "state": "TASK_BLOCKED",
        "correction_count": 2,
        "blockers": [],
    }

    def provider_adapter(
        r: Path,
        t: Path,
        role: str,
        run_id: str,
        timeout: int,
        _state_check: object,
        proposal: Path | None = None,
        feedback: Path | None = None,
        cli: Path | None = None,
        test_evidence: Path | None = None,
        **_kw: object,
    ) -> tuple[str, Path]:
        out = runner(
            r,
            t,
            role,
            run_id,
            timeout,
            feedback=feedback,
            proposal=proposal,
            cli=cli,
            test_evidence=test_evidence,
        )
        return run_id, out

    with (
        patch(
            "scripts.two_site_proposal_pipeline.run_candidate_tests",
            side_effect=lambda _r, _t, _w, w_sha, **_kw: passed_receipt(w_sha),
        ) as mock_candidate_tests,
        patch(
            "scripts.two_site_proposal_pipeline.run_provider_session",
            side_effect=provider_adapter,
        ),
        patch(
            "scripts.two_site_proposal_pipeline.check_state",
            return_value=None,
        ),
    ):
        _run_post_worker_remainder(
            root=root,
            task_candidate=task_candidate,
            task=task,
            sources=sources,
            initial_head=HEAD,
            initial_refs={},
            run_prefix=run_prefix,
            timeout=300,
            cli=None,
            state=state,
            evidence_dir=evidence_dir,
            state_check=lambda: None,
            worker_dir=worker_dir,
            worker_res=worker_res,
            current_worker=current_worker,
            seen_convs=seen_convs,
            correction_count=2,
            seen_norm_findings=set(),
            seen_test_signatures=set(),
            seen_bootstrap_signatures=set(),
        )

    mock_candidate_tests.assert_called_once_with(
        root, task, worker_res, worker_sha, timeout=1200
    )

    receipt_path = evidence_dir / "pre-review-tests-2.json"
    assert receipt_path.is_file()
    raw_receipt_bytes = receipt_path.read_bytes()
    receipt_sha = hashlib.sha256(raw_receipt_bytes).hexdigest()
    parsed_receipt = json.loads(raw_receipt_bytes.decode("utf-8"))

    assert parsed_receipt["state"] == "PASSED"
    assert parsed_receipt["proposal_sha256"] == worker_sha

    reviewer_dir = root / ".local/two-site-refactor/proposals" / reviewer_run_id
    assert state["state"] == "READY_FOR_CODEX"
    assert state["blockers"] == []
    assert state["correction_count"] == 2
    assert state["pre_review_test_state"] == "PASSED"
    assert state["pre_review_test_path"] == receipt_path.relative_to(root).as_posix()
    assert state["pre_review_test_sha256"] == receipt_sha
    assert state["final_reviewer_run_id"] == reviewer_run_id
    assert state["reviewer_path"] == (reviewer_dir / "result.json").relative_to(root).as_posix()

    assert not (evidence_dir / "feedback-correction-3.json").exists()

    assert len(calls) == 2
    assert calls[0][0] == "worker" and calls[0][1] == worker_run_id
    assert calls[1][0] == "verifier" and calls[1][1] == reviewer_run_id

    reviewer_st = json.loads((reviewer_dir / "status.json").read_text())
    assert worker_st["conversation_id"] != reviewer_st["conversation_id"]
    assert seen_convs == {worker_st["conversation_id"], reviewer_st["conversation_id"]}

    assert reviewer_st["input_proposal_sha256"] == worker_sha
    assert reviewer_st["input_worker_run_id"] == worker_run_id
    assert reviewer_st["input_worker_conversation_id"] == worker_st["conversation_id"]
    assert reviewer_st["input_test_evidence_sha256"] == receipt_sha

    reviewer_task = json.loads((reviewer_dir / "task.json").read_text())
    assert reviewer_task["proposal"] == worker_txt
    assert reviewer_task["test_evidence"] == parsed_receipt
