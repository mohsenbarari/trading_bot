"""Real source join fileproof for worker-two history prerequisite.

Downstream Open Gates Disclosure:
The following components and gates remain explicitly OPEN and are NOT closed
by this prerequisite test suite:
- W2 R1/W2 join execution
- terminal receipt verification
- Broker coordination
- wait/claim lock acquisition
- Provider live interaction
- candidate sandbox isolation
- replay verification
These gates remain open pending subsequent phase implementation.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
from typing import Any
import pytest

os.environ.setdefault("APP_ENV_FILE", "config/unit-test.env.example")

try:
    from scripts.two_site_agent_sandbox import SandboxError
except ImportError:
    SandboxError = type("SandboxError", (Exception,), {})  # type: ignore[misc,assignment]

try:
    from scripts.two_site_proposal_pipeline import ProposalError
    from scripts.two_site_proposal_runner import load_task
    from scripts.two_site_quota_resume_worker_reader import load_prior_worker_chain
    from scripts.two_site_semantic_attempts import MARKER
    from scripts.two_site_semantic_task_builder import build_task_identity
except ImportError:
    from two_site_proposal_pipeline import ProposalError  # type: ignore[no-redef]
    from two_site_proposal_runner import load_task  # type: ignore[no-redef]
    from two_site_quota_resume_worker_reader import (  # type: ignore[no-redef]
        load_prior_worker_chain,
    )
    from two_site_semantic_attempts import MARKER  # type: ignore[no-redef]
    from two_site_semantic_task_builder import (  # type: ignore[no-redef]
        build_task_identity,
    )

try:
    from scripts.two_site_assignment_scope import (
        AssignmentScopeError,
        load_active_assignment_scope,
    )
except ImportError:
    from two_site_assignment_scope import (  # type: ignore[no-redef]
        AssignmentScopeError,
        load_active_assignment_scope,
    )

from tests.test_two_site_assignment_scope_core import (
    _write_assignment,
    _write_worktrees,
    valid_repo,
)
from tests.test_two_site_quota_resume_worker_two_history_materialized import (
    _build_materialized_env,
)


def _setup_repo_and_task(repo: Path) -> tuple[dict[str, Any], str]:
    assignment_id = "WA-P1-00B-QUOTA-W2-REALSOURCE-20260927-511"
    _write_assignment(
        repo,
        assignment_id=assignment_id,
        allowed_paths=["src.py", "output.py"],
        forbidden_paths=["tests/test_bot_trade_execute_local_success.py"],
    )
    _write_worktrees(repo, active_assignment_id=assignment_id)
    scope = load_active_assignment_scope(repo)
    assert scope.assignment_id == assignment_id
    assert scope.allowed_paths == ("src.py", "output.py")
    assert scope.forbidden_paths == (
        "tests/test_bot_trade_execute_local_success.py",
    )

    (repo / ".gitignore").write_text(".local/\n", encoding="utf-8")
    (repo / "src.py").write_text("# real worker source\n", encoding="utf-8")

    gap_path = "docs/refactor/two-server/AE03_C5D_TEST_GATE_RESUME_MATRIX_20260926.md"
    gap_file = repo / gap_path
    gap_file.parent.mkdir(parents=True, exist_ok=True)
    gap_file.write_text("# gap anchor\n", encoding="utf-8")

    test_selector_path = "tests/test_selector.py"
    test_file = repo / test_selector_path
    test_file.parent.mkdir(parents=True, exist_ok=True)
    test_file.write_text("# tracked test selector\n", encoding="utf-8")

    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "add",
            ".gitignore",
            "src.py",
            gap_path,
            test_selector_path,
        ],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "track files"],
        check=True,
        capture_output=True,
    )

    head = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    gap_ref = (
        f"{gap_path}#1-8-real-full-tree-repository-facts-and-common-source-join-fileproof-commits-2ef9ca50-9772f65e"
    )
    scenario_key = (
        "ae03.c5d.w2-history-source-snapshot-codex-quality-correction-20260927-515"
    )

    ident = build_task_identity(
        root=repo,
        gap=gap_ref,
        scenario=scenario_key,
        context_files=["src.py"],
        allowed_files=["src.py", "output.py"],
        excerpts=[],
    )

    marker_pkg = MARKER + json.dumps({
        "head": head,
        "gap_reference": gap_ref,
        "excerpts": [],
        "semantic_identity": ident,
    })

    task_data = {
        "task_id": assignment_id,
        "goal": f"Execute incident {assignment_id} repair {marker_pkg}",
        "context_files": ["src.py"],
        "allowed_files": ["src.py", "output.py"],
        "requirements": ["R1: execute bounded real load_task snapshot"],
        "test_selectors": ["tests/test_selector.py"],
    }
    return task_data, head


def test_real_source_join_snapshots_positive(
    valid_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_data, head = _setup_repo_and_task(valid_repo)
    scope = load_active_assignment_scope(valid_repo)
    assert scope.assignment_id == "WA-P1-00B-QUOTA-W2-REALSOURCE-20260927-511"
    assert scope.assignment_id == task_data["task_id"]
    assert scope.allowed_paths == ("src.py", "output.py")
    assert not hasattr(load_task, "mock_calls")
    assert len(head) == 40

    monkeypatch.setenv("TWO_SITE_ASSIGNMENT_SCOPE_REQUIRED", "1")
    monkeypatch.setenv("TWO_SITE_ASSIGNMENT_ID", scope.assignment_id)
    monkeypatch.setenv("TWO_SITE_ASSIGNMENT_SHA256", scope.sha256)

    task_path = valid_repo / "task.json"
    task_path.write_text(json.dumps(task_data), encoding="utf-8")

    loaded_task, initial_sources = load_task(valid_repo, task_path)
    assert loaded_task["task_id"] == task_data["task_id"]
    assert "src.py" in initial_sources
    assert (
        initial_sources["src.py"]["sha256"]
        == hashlib.sha256((valid_repo / "src.py").read_bytes()).hexdigest()
    )
    assert "output.py" in initial_sources
    assert initial_sources["output.py"] == {"content": None, "sha256": None}

    artifacts, evidence, out_task_path, orig_prefix = _build_materialized_env(
        valid_repo,
        source_head=head,
        task_data=task_data,
    )
    assert out_task_path == task_path

    proposals_dir = valid_repo / ".local" / "two-site-refactor" / "proposals"
    for run_suffix in ("worker-0", "reviewer-0", "worker-1"):
        run_dir = proposals_dir / f"{orig_prefix}-{run_suffix}"
        task_snapshot = json.loads(
            (run_dir / "task.json").read_text(encoding="utf-8")
        )
        status_snapshot = json.loads(
            (run_dir / "status.json").read_text(encoding="utf-8")
        )

        assert task_snapshot["sources"] == initial_sources
        assert task_snapshot["source_head"] == head
        assert task_snapshot["task"]["task_id"] == task_data["task_id"]
        assert status_snapshot["head"] == head
        assert status_snapshot["task_id"] == task_data["task_id"]

    w1_info = evidence["worker_info"]
    assert "result" in w1_info
    assert "status" in w1_info
    assert "conversation_id" in w1_info
    assert "result_sha" in w1_info
    assert w1_info["conversation_id"] == "conv-w1"
    assert w1_info["result_sha"] == artifacts["proposal_sha256"]
    assert w1_info["status"]["task_id"] == task_data["task_id"]
    assert w1_info["status"]["head"] == head


def test_real_source_join_tamper_w1_snapshot_rejected_negative(
    valid_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_data, head = _setup_repo_and_task(valid_repo)
    scope = load_active_assignment_scope(valid_repo)
    assert scope.assignment_id == "WA-P1-00B-QUOTA-W2-REALSOURCE-20260927-511"
    assert scope.assignment_id == task_data["task_id"]
    assert scope.allowed_paths == ("src.py", "output.py")
    assert not hasattr(load_task, "mock_calls")
    assert len(head) == 40

    monkeypatch.setenv("TWO_SITE_ASSIGNMENT_SCOPE_REQUIRED", "1")
    monkeypatch.setenv("TWO_SITE_ASSIGNMENT_ID", scope.assignment_id)
    monkeypatch.setenv("TWO_SITE_ASSIGNMENT_SHA256", scope.sha256)

    artifacts, _, task_path, orig_prefix = _build_materialized_env(
        valid_repo,
        source_head=head,
        task_data=task_data,
    )

    proposals_dir = valid_repo / ".local" / "two-site-refactor" / "proposals"
    w0_dir = proposals_dir / f"{orig_prefix}-worker-0"
    r0_dir = proposals_dir / f"{orig_prefix}-reviewer-0"
    w1_dir = proposals_dir / f"{orig_prefix}-worker-1"

    w0_res_bytes = (w0_dir / "result.json").read_bytes()
    w0_st_bytes = (w0_dir / "status.json").read_bytes()
    r0_res_bytes = (r0_dir / "result.json").read_bytes()
    r0_st_bytes = (r0_dir / "status.json").read_bytes()

    w1_task_file = w1_dir / "task.json"
    w1_task = json.loads(w1_task_file.read_text(encoding="utf-8"))
    w1_task["sources"]["src.py"]["sha256"] = "0" * 64
    w1_task_file.write_text(json.dumps(w1_task), encoding="utf-8")

    w1_artifacts = {
        "task_id": task_data["task_id"],
        "source_head": head,
        "original_run_prefix": orig_prefix,
        "final_worker_run_id": f"{orig_prefix}-worker-1",
        "proposal_path": f".local/two-site-refactor/proposals/{orig_prefix}-worker-1/result.json",
        "proposal_sha256": artifacts["proposal_sha256"],
        "correction_count": 1,
    }

    with pytest.raises((ProposalError, SandboxError)):
        load_prior_worker_chain(valid_repo, task_path, w1_artifacts)

    assert (w0_dir / "result.json").read_bytes() == w0_res_bytes
    assert (w0_dir / "status.json").read_bytes() == w0_st_bytes
    assert (r0_dir / "result.json").read_bytes() == r0_res_bytes
    assert (r0_dir / "status.json").read_bytes() == r0_st_bytes


def test_real_source_join_wrong_expected_scope_sha_rejected_negative(
    valid_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_data, head = _setup_repo_and_task(valid_repo)
    scope = load_active_assignment_scope(valid_repo)
    assert scope.assignment_id == "WA-P1-00B-QUOTA-W2-REALSOURCE-20260927-511"
    assert scope.assignment_id == task_data["task_id"]
    assert scope.allowed_paths == ("src.py", "output.py")
    assert not hasattr(load_task, "mock_calls")
    assert len(head) == 40

    task_path = valid_repo / "task.json"
    task_path.write_text(json.dumps(task_data), encoding="utf-8")

    wrong_sha = "0" * 64
    assert wrong_sha != scope.sha256

    monkeypatch.setenv("TWO_SITE_ASSIGNMENT_SCOPE_REQUIRED", "1")
    monkeypatch.setenv("TWO_SITE_ASSIGNMENT_ID", scope.assignment_id)
    monkeypatch.setenv("TWO_SITE_ASSIGNMENT_SHA256", wrong_sha)

    with pytest.raises((AssignmentScopeError, ProposalError)):
        load_task(valid_repo, task_path)

    proposals_dir = valid_repo / ".local" / "two-site-refactor" / "proposals"
    assert not proposals_dir.exists()
    assert list(valid_repo.glob("**/result.json")) == []
    assert list(valid_repo.glob("**/status.json")) == []
