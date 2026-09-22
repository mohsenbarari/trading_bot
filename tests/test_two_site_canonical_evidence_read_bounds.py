"""Hermetic tests for canonical evidence and Git blob read bounds and invocation-local caches."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any
import pytest

os.environ.setdefault("APP_ENV_FILE", "config/unit-test.env.example")
from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

install_p1_00_network_fence()

from scripts.two_site_agent_sandbox import SandboxError
import scripts.two_site_cycle_tools as cycle_tools
from scripts.two_site_cycle_tools import (
    CANONICAL_READ_LIMIT_BYTES,
    CANONICAL_TOTAL_BUDGET_BYTES,
    inspect_canonical_evidence,
)
from tests.test_two_site_canonical_evidence_inspect import (
    _make_canonical_repo,
    _run_git,
    canonical_repo,
)


def test_bounded_reads_and_cache_matrix(
    canonical_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    git_calls: list[dict[str, Any]] = []
    orig_git_read = cycle_tools._git_read

    def spy_git_read(worktree: Path, arguments: Any, *, output_limit: int) -> bytes:
        git_calls.append({"worktree": worktree, "args": list(arguments), "limit": output_limit})
        return orig_git_read(worktree, arguments, output_limit=output_limit)

    monkeypatch.setattr(cycle_tools, "_git_read", spy_git_read)

    head_before = _run_git(canonical_repo, "rev-parse", "HEAD")
    status_before = _run_git(canonical_repo, "status", "--porcelain")

    result = inspect_canonical_evidence(canonical_repo)
    assert result["status"] == "VALID", result
    assert result["expected_seed_count"] == 4
    assert result["seed_count"] == 4
    assert result["coverage"] == {
        "API-DEMO-SEED-PROVEN": "PROVEN_FOR_REQUESTED_SCENARIO",
        "BOT-DEMO-SEED-PARTIAL": "PARTIAL_OR_SEED_ONLY",
        "WEB-DEMO-SEED-UNKNOWN": "UNKNOWN",
        "JOB-DEMO-SEED-EXTRA": "PARTIAL_OR_SEED_ONLY",
    }

    assert len(git_calls) > 0
    for call in git_calls:
        assert call["limit"] <= CANONICAL_READ_LIMIT_BYTES
        assert call["limit"] <= CANONICAL_TOTAL_BUDGET_BYTES

    sample_calls = [c for c in git_calls if any("tests/test_sample.py" in str(arg) for arg in c["args"])]
    assert len(sample_calls) == 1

    assert _run_git(canonical_repo, "rev-parse", "HEAD") == head_before
    assert _run_git(canonical_repo, "status", "--porcelain") == status_before


def test_invocation_local_cache_and_head_invalidation(
    canonical_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    res1 = inspect_canonical_evidence(canonical_repo, "API-DEMO-SEED-PROVEN")
    assert res1["status"] == "VALID"
    assert res1["requested_coverage"] == "PROVEN_FOR_REQUESTED_SCENARIO"

    git_calls: list[dict[str, Any]] = []
    orig_git_read = cycle_tools._git_read

    def spy_git_read(worktree: Path, arguments: Any, *, output_limit: int) -> bytes:
        git_calls.append({"args": list(arguments), "limit": output_limit})
        return orig_git_read(worktree, arguments, output_limit=output_limit)

    monkeypatch.setattr(cycle_tools, "_git_read", spy_git_read)

    res2 = inspect_canonical_evidence(canonical_repo, "API-DEMO-SEED-PROVEN")
    assert res2["status"] == "VALID"
    assert res2["requested_coverage"] == "PROVEN_FOR_REQUESTED_SCENARIO"
    assert len(git_calls) > 0

    sample_file = canonical_repo / "tests/test_sample.py"
    sample_file.write_text("def test_sample() -> None:\n    # modified content\n    pass\n", encoding="utf-8")
    _run_git(canonical_repo, "add", "tests/test_sample.py")
    _run_git(canonical_repo, "commit", "-m", "modify sample proof file")

    res3 = inspect_canonical_evidence(canonical_repo, "API-DEMO-SEED-PROVEN")
    assert res3["status"] == "VALID"
    assert res3["requested_coverage"] == "UNKNOWN"


def test_boundary_fixtures_shared_budget_counter(
    canonical_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert cycle_tools.CANONICAL_READ_LIMIT_BYTES == 2_000_000
    assert cycle_tools.CANONICAL_TOTAL_BUDGET_BYTES == 8_000_000

    recorded_ops: list[tuple[str, int]] = []
    orig_bounded_bytes = cycle_tools.runner.bounded_file_bytes
    orig_git_read = cycle_tools._git_read

    def spy_bounded_bytes(root: Path, path: Path, limit: int, label: str) -> bytes:
        recorded_ops.append(("live", limit))
        return orig_bounded_bytes(root, path, limit, label)

    def spy_git_read(worktree: Path, arguments: Any, *, output_limit: int) -> bytes:
        recorded_ops.append(("git", output_limit))
        return orig_git_read(worktree, arguments, output_limit=output_limit)

    monkeypatch.setattr(cycle_tools.runner, "bounded_file_bytes", spy_bounded_bytes)
    monkeypatch.setattr(cycle_tools, "_git_read", spy_git_read)

    manifest_path = canonical_repo / "docs/refactor/two-server/section-1/inventory/seed-handler-coverage.json"
    manifest_size = manifest_path.stat().st_size
    assert manifest_size > 0

    monkeypatch.setattr(cycle_tools, "CANONICAL_READ_LIMIT_BYTES", manifest_size + 100)
    monkeypatch.setattr(cycle_tools, "CANONICAL_TOTAL_BUDGET_BYTES", manifest_size * 2)

    result = inspect_canonical_evidence(canonical_repo)
    assert result["status"] == "INVENTORY_INCONSISTENT"
    assert "canonical content budget exhausted" in result.get("reason", "")

    assert len(recorded_ops) == 2
    assert recorded_ops[0] == ("live", manifest_size + 100)
    assert recorded_ops[1] == ("git", manifest_size)
    assert recorded_ops[1][1] < manifest_size + 100


def test_oversized_tracked_proof_blob_fails_bounded(tmp_path: Path) -> None:
    repo = tmp_path / "oversized_repo"
    repo.mkdir(parents=True, exist_ok=True)
    _run_git(repo, "init")
    _run_git(repo, "config", "commit.gpgsign", "false")

    sample_dir = repo / "tests"
    sample_dir.mkdir(parents=True, exist_ok=True)
    sample_file = sample_dir / "test_sample.py"
    large_bytes = b"# oversized\n" + (b"x" * (2_000_001 - 12))
    sample_file.write_bytes(large_bytes)
    sample_sha = hashlib.sha256(large_bytes).hexdigest()
    _run_git(repo, "add", "tests/test_sample.py")
    _run_git(repo, "commit", "-m", "commit 0: oversized sample source")
    c0 = _run_git(repo, "rev-parse", "HEAD")

    inv_dir = repo / "docs/refactor/two-server/section-1/inventory"
    scen_dir = repo / "docs/refactor/two-server/section-1/scenarios"
    inv_dir.mkdir(parents=True, exist_ok=True)
    scen_dir.mkdir(parents=True, exist_ok=True)

    base_doc = {
        "schema_version": 1,
        "stage": "P1-00b",
        "inspected_head": c0,
        "records": [
            {
                "seed_ids": ["API-DEMO-SEED-PROVEN"],
                "path": "tests/test_sample.py",
                "sha256": sample_sha,
                "last_commit": c0,
                "test_selectors": ["tests/test_sample.py::test_sample"],
            },
        ],
    }
    base_text = json.dumps(base_doc, indent=2) + "\n"
    (inv_dir / "base.json").write_text(base_text, encoding="utf-8")
    base_sha = hashlib.sha256(base_text.encode("utf-8")).hexdigest()

    c1_doc = {
        "schema_version": 1,
        "stage": "P1-00b",
        "base_inventory": {
            "path": "docs/refactor/two-server/section-1/inventory/base.json",
            "sha256": base_sha,
            "inspected_head": c0,
        },
        "records": [],
    }
    c1_text = json.dumps(c1_doc, indent=2) + "\n"
    (inv_dir / "continuation-1.json").write_text(c1_text, encoding="utf-8")
    c1_sha = hashlib.sha256(c1_text.encode("utf-8")).hexdigest()

    _run_git(repo, "add", "docs/refactor/two-server/section-1/inventory/base.json", "docs/refactor/two-server/section-1/inventory/continuation-1.json")
    _run_git(repo, "commit", "-m", "commit 1: base and continuation 1")
    c1_commit = _run_git(repo, "rev-parse", "HEAD")

    c2_doc = {
        "schema_version": 1,
        "stage": "P1-00b",
        "predecessor_inventory": {
            "path": "docs/refactor/two-server/section-1/inventory/continuation-1.json",
            "sha256": c1_sha,
            "last_commit": c1_commit,
        },
        "records": [],
    }
    c2_text = json.dumps(c2_doc, indent=2) + "\n"
    (inv_dir / "continuation-2.json").write_text(c2_text, encoding="utf-8")
    c2_sha = hashlib.sha256(c2_text.encode("utf-8")).hexdigest()
    _run_git(repo, "add", "docs/refactor/two-server/section-1/inventory/continuation-2.json")
    _run_git(repo, "commit", "-m", "commit 2: continuation 2")
    c2_commit = _run_git(repo, "rev-parse", "HEAD")

    c3_doc = {
        "schema_version": 1,
        "stage": "P1-00b",
        "predecessor_inventory": {
            "path": "docs/refactor/two-server/section-1/inventory/continuation-2.json",
            "sha256": c2_sha,
            "last_commit": c2_commit,
        },
        "records": [],
    }
    (inv_dir / "continuation-3.json").write_text(json.dumps(c3_doc, indent=2) + "\n", encoding="utf-8")

    surfaces = [
        ("docs/refactor/two-server/section-1/inventory/surface-api.json", ["API-DEMO-SEED-PROVEN"]),
        ("docs/refactor/two-server/section-1/inventory/surface-bot.json", ["BOT-DEMO-SEED-PARTIAL"]),
        ("docs/refactor/two-server/section-1/inventory/surface-web.json", ["WEB-DEMO-SEED-UNKNOWN"]),
        ("docs/refactor/two-server/section-1/inventory/surface-jobs.json", ["JOB-DEMO-SEED-EXTRA"]),
    ]
    for surface_path, b_ids in surfaces:
        s_doc = {
            "schema_version": "two-server-refactor.surface-behavior-inventory.v1",
            "entries": [{"behavior_id": bid} for bid in b_ids],
        }
        (repo / surface_path).write_text(json.dumps(s_doc, indent=2) + "\n", encoding="utf-8")

    scenario_doc = {
        "schema_version": "two-server-refactor.scenario-records.v2",
        "seed_mapping": {
            "expected_seed_count": 4,
            "source_inventories": ["inventory/surface-api.json", "inventory/surface-bot.json", "inventory/surface-web.json", "inventory/surface-jobs.json"],
        },
        "blocker_registry": {
            "items": [
                {"blocker_id": "P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE", "disposition": "BLOCKED_EXPLICIT", "status": "BLOCKED_EXPLICIT"},
                {"blocker_id": "P1-00-HIGH-ORIGIN-MATRIX-UNPROVEN", "disposition": "BLOCKED_EXPLICIT", "status": "BLOCKED_EXPLICIT"},
            ],
        },
    }
    (scen_dir / "baseline.json").write_text(json.dumps(scenario_doc, indent=2) + "\n", encoding="utf-8")

    manifest_doc = {
        "schema_version": 1,
        "stage": "P1-00b",
        "inventory": {"expected_seed_count": 4, "paths": [s[0] for s in surfaces]},
        "evidence_sources": {
            "baseline_path": "docs/refactor/two-server/section-1/scenarios/baseline.json",
            "base_path": "docs/refactor/two-server/section-1/inventory/base.json",
            "continuation_path": "docs/refactor/two-server/section-1/inventory/continuation-1.json",
            "continuation_2_path": "docs/refactor/two-server/section-1/inventory/continuation-2.json",
            "continuation_3_path": "docs/refactor/two-server/section-1/inventory/continuation-3.json",
        },
        "counts": {"baseline_evidenced_seed_count": 1, "reconciled_new_seed_count": 1, "evidenced_seed_count": 2, "remaining_seed_count": 2},
        "canonical_blockers": ["P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE", "P1-00-HIGH-ORIGIN-MATRIX-UNPROVEN"],
    }
    (inv_dir / "seed-handler-coverage.json").write_text(json.dumps(manifest_doc, indent=2) + "\n", encoding="utf-8")

    _run_git(
        repo,
        "add",
        "docs/refactor/two-server/section-1/inventory/continuation-3.json",
        *[s[0] for s in surfaces],
        "docs/refactor/two-server/section-1/scenarios/baseline.json",
        "docs/refactor/two-server/section-1/inventory/seed-handler-coverage.json",
    )
    _run_git(repo, "commit", "-m", "commit 3: complete manifest")

    result = inspect_canonical_evidence(repo, "API-DEMO-SEED-PROVEN")
    assert result["status"] == "VALID", result
    assert result["requested_coverage"] == "UNKNOWN"


def test_repeated_sandbox_error_consumes_conservatively_no_retry(
    canonical_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orig_git_read = cycle_tools._git_read
    sample_calls = 0

    def mock_git_read(worktree: Path, arguments: Any, *, output_limit: int) -> bytes:
        nonlocal sample_calls
        if any("tests/test_sample.py" in str(arg) for arg in arguments):
            sample_calls += 1
            raise SandboxError("isolated process exceeded evidence budget")
        return orig_git_read(worktree, arguments, output_limit=output_limit)

    monkeypatch.setattr(cycle_tools, "_git_read", mock_git_read)

    result = inspect_canonical_evidence(canonical_repo)
    assert result["status"] == "VALID", result
    assert result["coverage"]["API-DEMO-SEED-PROVEN"] == "UNKNOWN"
    assert result["coverage"]["WEB-DEMO-SEED-UNKNOWN"] == "UNKNOWN"
    assert sample_calls == 1


def test_canonical_read_error_yields_inventory_inconsistent(
    canonical_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orig_bounded_bytes = cycle_tools.runner.bounded_file_bytes

    def failing_live_read(root: Path, path: Path, limit: int, label: str) -> bytes:
        raise cycle_tools.runner.ProposalError("simulated safe live read failure")

    monkeypatch.setattr(cycle_tools.runner, "bounded_file_bytes", failing_live_read)
    result = inspect_canonical_evidence(canonical_repo)
    assert result["status"] == "INVENTORY_INCONSISTENT", result
    assert "canonical file read failed" in result.get("reason", "")

    monkeypatch.setattr(cycle_tools.runner, "bounded_file_bytes", orig_bounded_bytes)
    orig_git_read = cycle_tools._git_read

    def failing_git_read(worktree: Path, arguments: Any, *, output_limit: int) -> bytes:
        raise SandboxError("simulated git blob error")

    monkeypatch.setattr(cycle_tools, "_git_read", failing_git_read)
    result_git = inspect_canonical_evidence(canonical_repo)
    assert result_git["status"] == "INVENTORY_INCONSISTENT", result_git
    assert "bounded Git blob read failed" in result_git.get("reason", "")


def test_unexpected_exceptions_propagate(
    canonical_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def raise_generator_exit(*_args: Any, **_kwargs: Any) -> bytes:
        raise GeneratorExit()

    monkeypatch.setattr(cycle_tools, "_git_read", raise_generator_exit)
    with pytest.raises(GeneratorExit):
        inspect_canonical_evidence(canonical_repo)

    def raise_type_error(*_args: Any, **_kwargs: Any) -> bytes:
        raise TypeError("unexpected git seam error")

    monkeypatch.setattr(cycle_tools, "_git_read", raise_type_error)
    with pytest.raises(TypeError):
        inspect_canonical_evidence(canonical_repo)

    def raise_live_generator_exit(*_args: Any, **_kwargs: Any) -> bytes:
        raise GeneratorExit()

    monkeypatch.setattr(cycle_tools.runner, "bounded_file_bytes", raise_live_generator_exit)
    with pytest.raises(GeneratorExit):
        inspect_canonical_evidence(canonical_repo)

    def raise_runtime_error(*_args: Any, **_kwargs: Any) -> bytes:
        raise RuntimeError("unexpected live seam error")

    monkeypatch.setattr(cycle_tools.runner, "bounded_file_bytes", raise_runtime_error)
    with pytest.raises(RuntimeError):
        inspect_canonical_evidence(canonical_repo)
