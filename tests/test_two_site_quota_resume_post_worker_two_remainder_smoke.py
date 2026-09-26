"""Hermetic smoke tests for Worker-2 post-result remainder execution.

Explicit Mock Boundary Notice:
This smoke suite hermetically mocks the Worker-2 binding context manager
(worker_two_post_result_context) and canonical _run_post_worker_remainder to
prove correct lock-lifecycle, parameter mapping (exact correction_count 2),
state_check call ordering, and fail-closed state validation.
The real bootstrap/test/reviewer fault matrix and dispatcher route remain open.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from scripts.two_site_proposal_pipeline import ProposalError
from scripts.two_site_quota_resume_post_worker_two_remainder import (
    execute_worker_two_post_result_remainder,
)


def _make_harness(tmp_path: Path) -> tuple[Path, dict, MagicMock]:
    root = tmp_path.resolve()
    pipeline_dir = root / ".local/two-site-refactor/pipeline"
    pipeline_dir.mkdir(parents=True, exist_ok=True)

    task_file = root / "task.json"
    task_file.write_text("{}", encoding="utf-8")

    run_id = "prefix-worker-2"
    worker_dir = root / f".local/two-site-refactor/proposals/{run_id}"
    worker_dir.mkdir(parents=True, exist_ok=True)
    proposal_file = worker_dir / "result.json"
    proposal_file.write_text("{}", encoding="utf-8")

    bound_returned_state = {
        "state": "WORKER_PROPOSAL_READY",
        "task_id": "task-436",
        "source_head": "0" * 40,
        "final_worker_run_id": run_id,
        "proposal_sha256": "1" * 64,
        "changed_paths": ["scripts/foo.py"],
        "proposal_path": f".local/two-site-refactor/proposals/{run_id}/result.json",
        "correction_count": 2,
        "semantic_task_fingerprint": "2" * 64,
    }
    base_context = {
        "admitted_task_path": task_file,
        "admitted_request": {"timeout": 300},
        "continuation_run_prefix": "prefix",
        "task": {"task_id": "task-436"},
        "sources": {},
        "source_head": "0" * 40,
        "initial_refs": {},
    }
    state_check = MagicMock()
    post_ctx = {
        "returned_state": bound_returned_state,
        "base_context": base_context,
        "worker_info": {"result": {"status": "PROPOSAL_READY", "changes": []}},
        "current_worker": (run_id, "1" * 64, "{}", "conv-1"),
        "seen_convs": {"conv-1"},
        "complete_history": {"complete": True},
        "state_check": state_check,
    }
    return root, post_ctx, state_check


def test_w2_remainder_success_under_held_lock(tmp_path: Path) -> None:
    root, post_ctx, state_check = _make_harness(tmp_path)
    lock_held = False

    @contextmanager
    def mock_context(root_arg, req_arg, state_arg):
        nonlocal lock_held
        lock_held = True
        try:
            yield post_ctx
        finally:
            lock_held = False

    def mock_remainder(**kwargs):
        assert lock_held is True, "Remainder must execute while lock is held"
        assert kwargs["correction_count"] == 2
        assert kwargs["seen_norm_findings"] == set()
        assert kwargs["seen_test_signatures"] == set()
        assert kwargs["seen_bootstrap_signatures"] == set()
        assert kwargs["run_prefix"] == "prefix"
        assert kwargs["timeout"] == 300
        assert state_check.call_count == 1
        kwargs["state"]["state"] = "READY_FOR_CODEX"
        kwargs["state"]["final_reviewer_run_id"] = "prefix-reviewer-2"

    with patch(
        "scripts.two_site_quota_resume_post_worker_two_remainder.worker_two_post_result_context",
        side_effect=mock_context,
    ), patch(
        "scripts.two_site_quota_resume_post_worker_two_remainder._run_post_worker_remainder",
        side_effect=mock_remainder,
    ):
        res = execute_worker_two_post_result_remainder(root, {}, {})

    assert lock_held is False
    assert state_check.call_count == 2
    assert res["state"] == "READY_FOR_CODEX"
    assert res["final_reviewer_run_id"] == "prefix-reviewer-2"
    assert res["correction_count"] == 2


def test_w2_remainder_fails_when_not_ready_for_codex(tmp_path: Path) -> None:
    root, post_ctx, _ = _make_harness(tmp_path)

    @contextmanager
    def mock_context(root_arg, req_arg, state_arg):
        yield post_ctx

    def mock_remainder_incomplete(**kwargs):
        kwargs["state"]["state"] = "TASK_BLOCKED"

    with patch(
        "scripts.two_site_quota_resume_post_worker_two_remainder.worker_two_post_result_context",
        side_effect=mock_context,
    ), patch(
        "scripts.two_site_quota_resume_post_worker_two_remainder._run_post_worker_remainder",
        side_effect=mock_remainder_incomplete,
    ):
        with pytest.raises(ProposalError, match="remainder did not complete READY_FOR_CODEX"):
            execute_worker_two_post_result_remainder(root, {}, {})


def test_w2_remainder_fails_when_reviewer_id_missing_or_empty(tmp_path: Path) -> None:
    for idx, bad_reviewer in enumerate([None, "   "]):
        root, post_ctx, _ = _make_harness(tmp_path / f"sub_{idx}")

        @contextmanager
        def mock_context(root_arg, req_arg, state_arg, ctx=post_ctx):
            yield ctx

        def mock_remainder(val=bad_reviewer, **kwargs):
            kwargs["state"]["state"] = "READY_FOR_CODEX"
            kwargs["state"]["final_reviewer_run_id"] = val

        with patch(
            "scripts.two_site_quota_resume_post_worker_two_remainder.worker_two_post_result_context",
            side_effect=mock_context,
        ), patch(
            "scripts.two_site_quota_resume_post_worker_two_remainder._run_post_worker_remainder",
            side_effect=mock_remainder,
        ):
            with pytest.raises(ProposalError, match="final_reviewer_run_id must be non-empty str"):
                execute_worker_two_post_result_remainder(root, {}, {})
