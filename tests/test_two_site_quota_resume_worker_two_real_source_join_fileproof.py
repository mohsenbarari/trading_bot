"""Real source join fileproof for worker-two history prerequisite.

Downstream Open Gates Disclosure:
The following components and gates remain explicitly OPEN and are NOT closed
by this prerequisite test suite:
- real Broker admission
- candidate sandbox isolation
- live Provider interaction
- replay authority
- full C5d completion
- terminal receipt verification
- Broker coordination
- wait/claim lock acquisition
These gates remain open pending subsequent phase implementation.
"""

from __future__ import annotations

import copy
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

try:
    from scripts.two_site_quota_resume_worker_two_file_reader import (
        read_and_bind_worker_two_result,
    )
except ImportError:
    from two_site_quota_resume_worker_two_file_reader import (  # type: ignore[no-redef]
        read_and_bind_worker_two_result,
    )

from tests.test_two_site_assignment_scope_core import (
    _write_assignment,
    _write_worktrees,
    valid_repo,
)
from tests.test_two_site_quota_resume_worker_two_fileproof import (
    _setup_materialized_worker_two_env,
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


def test_real_source_join_worker_two_read_and_bind_positive(
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

    root, ctx, returned_state, meta = _setup_materialized_worker_two_env(
        valid_repo,
        source_head=head,
        task_data=task_data,
    )

    res = read_and_bind_worker_two_result(root, ctx, returned_state)

    expected_current_worker = (
        meta["w2_id"],
        meta["w2_sha"],
        meta["w2_result_text"],
        meta["w2_conv_id"],
    )
    assert res["current_worker"] == expected_current_worker

    expected_ordered_history_convs = [
        "conv-w0",
        "conv-r0",
        "conv-w1",
        f"conv-{meta['r1_id']}",
    ]
    assert res["complete_history"]["complete"] is True
    assert (
        res["complete_history"]["ordered_conversation_ids"]
        == expected_ordered_history_convs
    )
    expected_seen_convs = set(expected_ordered_history_convs) | {meta["w2_conv_id"]}
    assert res["seen_convs"] == expected_seen_convs

    assert res["returned_state"]["state"] == "WORKER_PROPOSAL_READY"
    assert res["returned_state"]["correction_count"] == 2
    assert res["returned_state"]["proposal_sha256"] == meta["w2_sha"]
    assert res["returned_state"]["final_worker_run_id"] == meta["w2_id"]
    assert res["returned_state"]["task_id"] == task_data["task_id"]
    assert res["returned_state"]["source_head"] == head

    worker_info = res["worker_info"]
    assert worker_info["result_sha"] == meta["w2_sha"]
    assert worker_info["conversation_id"] == meta["w2_conv_id"]
    assert worker_info["result"]["status"] == "PROPOSAL_READY"
    assert worker_info["status"]["state"] == "PROPOSAL_READY"
    assert worker_info["status"]["run_id"] == meta["w2_id"]
    assert worker_info["status"]["task_id"] == task_data["task_id"]
    assert worker_info["status"]["head"] == head

    proposals_dir = root / ".local" / "two-site-refactor" / "proposals"
    w2_run_dir = proposals_dir / meta["w2_id"]
    w2_task_snapshot = json.loads(
        (w2_run_dir / "task.json").read_text(encoding="utf-8")
    )
    assert w2_task_snapshot["sources"] == ctx["sources"]
    assert w2_task_snapshot["source_head"] == head
    assert ctx["source_head"] == head
    assert ctx["task"]["task_id"] == task_data["task_id"]

    src_bytes = (root / "src.py").read_bytes()
    expected_src_sha = hashlib.sha256(src_bytes).hexdigest()
    assert ctx["sources"]["src.py"]["sha256"] == expected_src_sha
    assert w2_task_snapshot["sources"]["src.py"]["sha256"] == expected_src_sha
    assert "output.py" in ctx["sources"]
    assert ctx["sources"]["output.py"] == {"content": None, "sha256": None}

    for run_id in (
        f"{meta['orig_prefix']}-worker-0",
        f"{meta['orig_prefix']}-reviewer-0",
        meta["w1_id"],
        meta["r1_id"],
        meta["w2_id"],
    ):
        snap = json.loads((proposals_dir / run_id / "task.json").read_text(encoding="utf-8"))
        assert snap["sources"] == ctx["sources"]
        assert snap["source_head"] == head
        assert snap["task"]["task_id"] == task_data["task_id"]


def test_real_source_join_worker_two_tamper_w2_source_snapshot_rejected_negative(
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

    root, ctx, returned_state, meta = _setup_materialized_worker_two_env(
        valid_repo,
        source_head=head,
        task_data=task_data,
    )

    proposals_dir = root / ".local" / "two-site-refactor" / "proposals"
    prior_runs = [
        f"{meta['orig_prefix']}-worker-0",
        f"{meta['orig_prefix']}-reviewer-0",
        meta["w1_id"],
        meta["r1_id"],
    ]
    prior_snapshots = {
        run_id: (
            (proposals_dir / run_id / "result.json").read_bytes(),
            (proposals_dir / run_id / "status.json").read_bytes(),
            (proposals_dir / run_id / "task.json").read_bytes(),
        )
        for run_id in prior_runs
    }

    w2_task_file = proposals_dir / meta["w2_id"] / "task.json"
    w2_task = json.loads(w2_task_file.read_text(encoding="utf-8"))
    w2_task["sources"]["src.py"]["sha256"] = "0" * 64
    w2_task_file.write_text(json.dumps(w2_task), encoding="utf-8")

    with pytest.raises((ProposalError, SandboxError)):
        read_and_bind_worker_two_result(root, ctx, returned_state)

    for run_id, (res_bytes, st_bytes, task_bytes) in prior_snapshots.items():
        assert (proposals_dir / run_id / "result.json").read_bytes() == res_bytes
        assert (proposals_dir / run_id / "status.json").read_bytes() == st_bytes
        assert (proposals_dir / run_id / "task.json").read_bytes() == task_bytes

    r2_id = f"{meta['continuation_run_prefix']}-reviewer-2"
    assert not (proposals_dir / r2_id).exists()
    assert not hasattr(load_task, "mock_calls")


def test_real_source_join_canonical_task_path_worker_two_positive(
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

    canonical_task_rel = Path("custom_tasks") / "canonical_task.json"
    root, ctx, returned_state, meta = _setup_materialized_worker_two_env(
        valid_repo,
        source_head=head,
        task_data=task_data,
        admitted_task_path=canonical_task_rel,
    )

    assert not (root / "task.json").exists()
    assert (root / canonical_task_rel).is_file()
    assert ctx["admitted_task_path"] == (root / canonical_task_rel).resolve()

    res = read_and_bind_worker_two_result(root, ctx, returned_state)

    expected_current_worker = (
        meta["w2_id"],
        meta["w2_sha"],
        meta["w2_result_text"],
        meta["w2_conv_id"],
    )
    assert res["current_worker"] == expected_current_worker

    expected_ordered_history_convs = [
        "conv-w0",
        "conv-r0",
        "conv-w1",
        f"conv-{meta['r1_id']}",
    ]
    assert res["complete_history"]["complete"] is True
    assert (
        res["complete_history"]["ordered_conversation_ids"]
        == expected_ordered_history_convs
    )
    expected_seen_convs = set(expected_ordered_history_convs) | {meta["w2_conv_id"]}
    assert res["seen_convs"] == expected_seen_convs

    assert res["returned_state"]["state"] == "WORKER_PROPOSAL_READY"
    assert res["returned_state"]["correction_count"] == 2
    assert res["returned_state"]["proposal_sha256"] == meta["w2_sha"]
    assert res["returned_state"]["final_worker_run_id"] == meta["w2_id"]
    assert res["returned_state"]["task_id"] == task_data["task_id"]
    assert res["returned_state"]["source_head"] == head

    worker_info = res["worker_info"]
    assert worker_info["result_sha"] == meta["w2_sha"]
    assert worker_info["conversation_id"] == meta["w2_conv_id"]
    assert worker_info["result"]["status"] == "PROPOSAL_READY"
    assert worker_info["status"]["state"] == "PROPOSAL_READY"
    assert worker_info["status"]["run_id"] == meta["w2_id"]
    assert worker_info["status"]["task_id"] == task_data["task_id"]
    assert worker_info["status"]["head"] == head

    proposals_dir = root / ".local" / "two-site-refactor" / "proposals"
    w2_run_dir = proposals_dir / meta["w2_id"]
    w2_task_snapshot = json.loads(
        (w2_run_dir / "task.json").read_text(encoding="utf-8")
    )
    assert w2_task_snapshot["sources"] == ctx["sources"]
    assert w2_task_snapshot["source_head"] == head
    assert ctx["source_head"] == head
    assert ctx["task"]["task_id"] == task_data["task_id"]

    src_bytes = (root / "src.py").read_bytes()
    expected_src_sha = hashlib.sha256(src_bytes).hexdigest()
    assert ctx["sources"]["src.py"]["sha256"] == expected_src_sha
    assert w2_task_snapshot["sources"]["src.py"]["sha256"] == expected_src_sha
    assert "output.py" in ctx["sources"]
    assert ctx["sources"]["output.py"] == {"content": None, "sha256": None}

    for run_id in (
        f"{meta['orig_prefix']}-worker-0",
        f"{meta['orig_prefix']}-reviewer-0",
        meta["w1_id"],
        meta["r1_id"],
        meta["w2_id"],
    ):
        snap = json.loads(
            (proposals_dir / run_id / "task.json").read_text(encoding="utf-8")
        )
        assert snap["sources"] == ctx["sources"]
        assert snap["source_head"] == head
        assert snap["task"]["task_id"] == task_data["task_id"]
        assert snap["task"]["goal"] == task_data["goal"]

    assert not (root / "task.json").exists()


def test_real_source_join_canonical_task_content_tamper_rejected_negative(
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

    canonical_task_rel = Path("custom_tasks") / "canonical_task.json"
    root, ctx, returned_state, meta = _setup_materialized_worker_two_env(
        valid_repo,
        source_head=head,
        task_data=task_data,
        admitted_task_path=canonical_task_rel,
    )
    assert not (root / "task.json").exists()

    proposals_dir = root / ".local" / "two-site-refactor" / "proposals"
    all_runs = [
        f"{meta['orig_prefix']}-worker-0",
        f"{meta['orig_prefix']}-reviewer-0",
        meta["w1_id"],
        meta["r1_id"],
        meta["w2_id"],
    ]
    prior_snapshots = {
        run_id: (
            (proposals_dir / run_id / "result.json").read_bytes(),
            (proposals_dir / run_id / "status.json").read_bytes(),
            (proposals_dir / run_id / "task.json").read_bytes(),
        )
        for run_id in all_runs
    }

    canonical_task_path = root / canonical_task_rel
    tampered_task = copy.deepcopy(task_data)
    tampered_task["requirements"] = ["R1: tampered requirement for negative proof"]
    canonical_task_path.write_text(json.dumps(tampered_task), encoding="utf-8")

    with pytest.raises((ProposalError, SandboxError)):
        read_and_bind_worker_two_result(root, ctx, returned_state)

    for run_id, (res_bytes, st_bytes, task_bytes) in prior_snapshots.items():
        assert (proposals_dir / run_id / "result.json").read_bytes() == res_bytes
        assert (proposals_dir / run_id / "status.json").read_bytes() == st_bytes
        assert (proposals_dir / run_id / "task.json").read_bytes() == task_bytes

    r2_id = f"{meta['continuation_run_prefix']}-reviewer-2"
    assert not (proposals_dir / r2_id).exists()
    assert not (root / "task.json").exists()
    assert not hasattr(load_task, "mock_calls")


def test_real_source_join_preexisting_canonical_task_bytes_preserved_positive(
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

    canonical_task_rel = Path(".local/two-site-refactor/tasks/canonical-task.json")
    canonical_task_path = valid_repo / canonical_task_rel
    canonical_task_path.parent.mkdir(parents=True, exist_ok=True)
    canonical_task_path.write_text(
        json.dumps(task_data, indent=2), encoding="utf-8"
    )
    raw_bytes = canonical_task_path.read_bytes()
    raw_sha = hashlib.sha256(raw_bytes).hexdigest()

    root, ctx, returned_state, meta = _setup_materialized_worker_two_env(
        valid_repo,
        source_head=head,
        task_data=task_data,
        admitted_task_path=canonical_task_rel,
    )

    assert canonical_task_path.read_bytes() == raw_bytes
    assert hashlib.sha256(canonical_task_path.read_bytes()).hexdigest() == raw_sha
    assert ctx["admitted_task_path"] == canonical_task_path.resolve()
    assert not (valid_repo / "task.json").exists()

    res = read_and_bind_worker_two_result(root, ctx, returned_state)

    expected_current_worker = (
        meta["w2_id"],
        meta["w2_sha"],
        meta["w2_result_text"],
        meta["w2_conv_id"],
    )
    assert res["current_worker"] == expected_current_worker

    expected_ordered_history_convs = [
        "conv-w0",
        "conv-r0",
        "conv-w1",
        f"conv-{meta['r1_id']}",
    ]
    assert res["complete_history"]["complete"] is True
    assert (
        res["complete_history"]["ordered_conversation_ids"]
        == expected_ordered_history_convs
    )
    expected_seen_convs = set(expected_ordered_history_convs) | {meta["w2_conv_id"]}
    assert res["seen_convs"] == expected_seen_convs

    assert res["returned_state"]["state"] == "WORKER_PROPOSAL_READY"
    assert res["returned_state"]["correction_count"] == 2
    assert res["returned_state"]["proposal_sha256"] == meta["w2_sha"]
    assert res["returned_state"]["final_worker_run_id"] == meta["w2_id"]
    assert res["returned_state"]["task_id"] == task_data["task_id"]
    assert res["returned_state"]["source_head"] == head

    worker_info = res["worker_info"]
    assert worker_info["result_sha"] == meta["w2_sha"]
    assert worker_info["conversation_id"] == meta["w2_conv_id"]
    assert worker_info["result"]["status"] == "PROPOSAL_READY"
    assert worker_info["status"]["state"] == "PROPOSAL_READY"
    assert worker_info["status"]["run_id"] == meta["w2_id"]
    assert worker_info["status"]["task_id"] == task_data["task_id"]
    assert worker_info["status"]["head"] == head

    assert canonical_task_path.read_bytes() == raw_bytes
    assert hashlib.sha256(canonical_task_path.read_bytes()).hexdigest() == raw_sha
    assert not (valid_repo / "task.json").exists()


def test_real_source_join_preexisting_canonical_task_mismatch_rejected_negative(
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

    canonical_task_rel = Path(".local/two-site-refactor/tasks/canonical-task.json")
    canonical_task_path = valid_repo / canonical_task_rel
    canonical_task_path.parent.mkdir(parents=True, exist_ok=True)

    mismatched_task = copy.deepcopy(task_data)
    mismatched_task["requirements"] = [
        "R1: schema-valid divergent requirement to test equality rejection"
    ]
    canonical_task_path.write_text(
        json.dumps(mismatched_task, indent=2), encoding="utf-8"
    )
    raw_bytes = canonical_task_path.read_bytes()

    with pytest.raises((ProposalError, SandboxError)):
        _setup_materialized_worker_two_env(
            valid_repo,
            source_head=head,
            task_data=task_data,
            admitted_task_path=canonical_task_rel,
        )

    assert canonical_task_path.read_bytes() == raw_bytes
    assert not (valid_repo / "task.json").exists()

    proposals_dir = valid_repo / ".local" / "two-site-refactor" / "proposals"
    assert not proposals_dir.exists()
    assert list(valid_repo.glob("**/result.json")) == []
    assert list(valid_repo.glob("**/status.json")) == []
    assert not hasattr(load_task, "mock_calls")


def test_real_source_join_worker_two_reordered_allowed_files_output_target_positive(
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

    reordered_task = copy.deepcopy(task_data)
    reordered_task["allowed_files"] = ["output.py", "src.py"]

    canonical_task_rel = Path(".local/two-site-refactor/tasks/canonical-task.json")
    canonical_task_path = valid_repo / canonical_task_rel
    canonical_task_path.parent.mkdir(parents=True, exist_ok=True)
    canonical_task_path.write_text(
        json.dumps(reordered_task, indent=2), encoding="utf-8"
    )
    raw_bytes = canonical_task_path.read_bytes()
    raw_sha = hashlib.sha256(raw_bytes).hexdigest()

    root, ctx, returned_state, meta = _setup_materialized_worker_two_env(
        valid_repo,
        source_head=head,
        task_data=reordered_task,
        admitted_task_path=canonical_task_rel,
    )

    assert canonical_task_path.read_bytes() == raw_bytes
    assert hashlib.sha256(canonical_task_path.read_bytes()).hexdigest() == raw_sha
    assert not (valid_repo / "task.json").exists()
    assert ctx["admitted_task_path"] == canonical_task_path.resolve()
    assert not hasattr(load_task, "mock_calls")

    res = read_and_bind_worker_two_result(root, ctx, returned_state)

    assert res["returned_state"]["state"] == "WORKER_PROPOSAL_READY"
    assert res["returned_state"]["correction_count"] == 2
    assert res["returned_state"]["proposal_sha256"] == meta["w2_sha"]
    assert res["returned_state"]["final_worker_run_id"] == meta["w2_id"]
    assert res["returned_state"]["task_id"] == reordered_task["task_id"]
    assert res["returned_state"]["source_head"] == head

    expected_target = reordered_task["allowed_files"][0]
    expected_sha = (
        hashlib.sha256((root / expected_target).read_bytes()).hexdigest()
        if (root / expected_target).exists()
        else None
    )
    assert expected_target == "output.py"
    assert expected_sha is None

    proposals_dir = root / ".local" / "two-site-refactor" / "proposals"
    for run_id in (
        f"{meta['orig_prefix']}-worker-0",
        meta["w1_id"],
        meta["w2_id"],
    ):
        snap = json.loads(
            (proposals_dir / run_id / "result.json").read_text(encoding="utf-8")
        )
        assert len(snap["changes"]) == 1
        assert snap["changes"][0]["path"] == expected_target
        assert snap["changes"][0]["original_sha256"] is expected_sha

    assert res["worker_info"]["result"]["changes"][0]["path"] == expected_target
    assert res["worker_info"]["result"]["changes"][0]["original_sha256"] is expected_sha

    assert canonical_task_path.read_bytes() == raw_bytes
    assert hashlib.sha256(canonical_task_path.read_bytes()).hexdigest() == raw_sha
    assert not (valid_repo / "task.json").exists()


def test_real_source_join_unsafe_allowed_target_rejected_negative(
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

    canonical_task_rel = Path(".local/two-site-refactor/tasks/canonical-task.json")
    canonical_task_path = valid_repo / canonical_task_rel
    canonical_task_path.parent.mkdir(parents=True, exist_ok=True)

    unsafe_task = copy.deepcopy(task_data)
    unsafe_target = "../unsafe.py"
    unsafe_task["allowed_files"] = [unsafe_target, "src.py"]
    canonical_task_path.write_text(
        json.dumps(unsafe_task, indent=2), encoding="utf-8"
    )
    raw_bytes = canonical_task_path.read_bytes()
    raw_sha = hashlib.sha256(raw_bytes).hexdigest()

    outside_file = valid_repo.parent / "unsafe.py"
    assert not outside_file.exists()

    with pytest.raises((ProposalError, SandboxError)):
        _setup_materialized_worker_two_env(
            valid_repo,
            source_head=head,
            task_data=unsafe_task,
            admitted_task_path=canonical_task_rel,
        )

    assert canonical_task_path.read_bytes() == raw_bytes
    assert hashlib.sha256(canonical_task_path.read_bytes()).hexdigest() == raw_sha
    assert not (valid_repo / "task.json").exists()
    assert not outside_file.exists()
    assert not (valid_repo / unsafe_target).exists()

    proposals_dir = valid_repo / ".local" / "two-site-refactor" / "proposals"
    assert not proposals_dir.exists() or list(proposals_dir.glob("**/result.json")) == []
    assert list(valid_repo.glob("**/result.json")) == []
    assert list(valid_repo.glob("**/status.json")) == []
    assert not hasattr(load_task, "mock_calls")
