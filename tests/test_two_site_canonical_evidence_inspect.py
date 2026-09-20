"""Hermetic tests and baseline for canonical evidence inspection."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess

import pytest

os.environ.setdefault("APP_ENV_FILE", "config/unit-test.env.example")
from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

install_p1_00_network_fence()

from scripts.two_site_cycle_tools import inspect_canonical_evidence


def _run_git(cwd: Path, *args: str) -> str:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Test User",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Test User",
        "GIT_COMMITTER_EMAIL": "test@example.com",
    }
    res = subprocess.run(
        ["git", *args],
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    return res.stdout.strip()


def _make_canonical_repo(
    repo: Path,
    *,
    corrupt_counts: bool = False,
    corrupt_schema: bool = False,
) -> Path:
    repo.mkdir(parents=True, exist_ok=True)
    _run_git(repo, "init")
    _run_git(repo, "config", "commit.gpgsign", "false")

    # Commit 0: tracked sample file for valid/unknown records.
    sample_dir = repo / "tests"
    sample_dir.mkdir(parents=True, exist_ok=True)
    sample_file = sample_dir / "test_sample.py"
    sample_content = "def test_sample() -> None:\n    pass\n"
    sample_file.write_text(sample_content, encoding="utf-8")
    sample_sha = hashlib.sha256(sample_content.encode("utf-8")).hexdigest()
    _run_git(repo, "add", "tests/test_sample.py")
    _run_git(repo, "commit", "-m", "commit 0: sample source")
    c0 = _run_git(repo, "rev-parse", "HEAD")

    # Commit 1: base inventory and continuation 1.
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
            {
                "seed_ids": ["WEB-DEMO-SEED-UNKNOWN"],
                "path": "tests/test_sample.py",
                "sha256": "0" * 64,
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

    _run_git(
        repo,
        "add",
        "docs/refactor/two-server/section-1/inventory/base.json",
        "docs/refactor/two-server/section-1/inventory/continuation-1.json",
    )
    _run_git(repo, "commit", "-m", "commit 1: base and continuation 1")
    c1_commit = _run_git(repo, "rev-parse", "HEAD")

    # Commit 2: continuation 2 linking to continuation 1.
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

    # Commit 3: continuation 3, 4 surfaces, scenario, and manifest.
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
    c3_text = json.dumps(c3_doc, indent=2) + "\n"
    (inv_dir / "continuation-3.json").write_text(c3_text, encoding="utf-8")

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
            "source_inventories": [
                "inventory/surface-api.json",
                "inventory/surface-bot.json",
                "inventory/surface-web.json",
                "inventory/surface-jobs.json",
            ],
        },
        "blocker_registry": {
            "items": [
                {
                    "blocker_id": "P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE",
                    "disposition": "BLOCKED_EXPLICIT",
                    "status": "BLOCKED_EXPLICIT",
                },
                {
                    "blocker_id": "P1-00-HIGH-ORIGIN-MATRIX-UNPROVEN",
                    "disposition": "BLOCKED_EXPLICIT",
                    "status": "BLOCKED_EXPLICIT",
                },
            ],
        },
    }
    (scen_dir / "baseline.json").write_text(
        json.dumps(scenario_doc, indent=2) + "\n", encoding="utf-8"
    )

    manifest_counts = {
        "baseline_evidenced_seed_count": 1,
        "reconciled_new_seed_count": 1,
        "evidenced_seed_count": 2,
        "remaining_seed_count": 999 if corrupt_counts else 2,
    }
    manifest_doc = {
        "schema_version": 999 if corrupt_schema else 1,
        "stage": "P1-00b",
        "inventory": {
            "expected_seed_count": 4,
            "paths": [s[0] for s in surfaces],
        },
        "evidence_sources": {
            "baseline_path": "docs/refactor/two-server/section-1/scenarios/baseline.json",
            "base_path": "docs/refactor/two-server/section-1/inventory/base.json",
            "continuation_path": "docs/refactor/two-server/section-1/inventory/continuation-1.json",
            "continuation_2_path": "docs/refactor/two-server/section-1/inventory/continuation-2.json",
            "continuation_3_path": "docs/refactor/two-server/section-1/inventory/continuation-3.json",
        },
        "counts": manifest_counts,
        "canonical_blockers": [
            "P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE",
            "P1-00-HIGH-ORIGIN-MATRIX-UNPROVEN",
        ],
    }
    (inv_dir / "seed-handler-coverage.json").write_text(
        json.dumps(manifest_doc, indent=2) + "\n", encoding="utf-8"
    )

    _run_git(
        repo,
        "add",
        "docs/refactor/two-server/section-1/inventory/continuation-3.json",
        *[s[0] for s in surfaces],
        "docs/refactor/two-server/section-1/scenarios/baseline.json",
        "docs/refactor/two-server/section-1/inventory/seed-handler-coverage.json",
    )
    _run_git(repo, "commit", "-m", "commit 3: continuation 3, scenario, surfaces, manifest")
    return repo


@pytest.fixture
def canonical_repo(tmp_path: Path) -> Path:
    return _make_canonical_repo(tmp_path / "canonical_repo")


def test_real_worktree_baseline() -> None:
    root = Path(__file__).resolve().parents[1]
    probe = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], cwd=root, text=True, capture_output=True)
    if probe.returncode != 0 or probe.stdout.strip() != "true":
        pytest.skip("Git metadata unavailable in the hermetic snapshot")
    behavior = "BOT-TG-CALLBACK-QUERY-PANEL-BACK-TO-USER-PANEL-FROM-BLOCKED-L640"
    result = inspect_canonical_evidence(root, behavior)
    assert result["status"] == "VALID", result
    assert result["expected_seed_count"] == result["seed_count"] == 465
    assert result["requested_coverage"] == "PROVEN_FOR_REQUESTED_SCENARIO"


def test_canonical_evidence_four_state_matrix(canonical_repo: Path) -> None:
    # 1. Known seed with a valid record and lineage: PROVEN_FOR_REQUESTED_SCENARIO
    proven_result = inspect_canonical_evidence(canonical_repo, "API-DEMO-SEED-PROVEN")
    assert proven_result["status"] == "VALID", proven_result
    assert proven_result["expected_seed_count"] == 4
    assert proven_result["seed_count"] == 4
    assert proven_result["requested_coverage"] == "PROVEN_FOR_REQUESTED_SCENARIO"
    assert proven_result["coverage"] == {"API-DEMO-SEED-PROVEN": "PROVEN_FOR_REQUESTED_SCENARIO"}

    # 2. Known seed without any record: PARTIAL_OR_SEED_ONLY
    partial_result = inspect_canonical_evidence(canonical_repo, "BOT-DEMO-SEED-PARTIAL")
    assert partial_result["status"] == "VALID", partial_result
    assert partial_result["expected_seed_count"] == 4
    assert partial_result["seed_count"] == 4
    assert partial_result["requested_coverage"] == "PARTIAL_OR_SEED_ONLY"
    assert partial_result["coverage"] == {"BOT-DEMO-SEED-PARTIAL": "PARTIAL_OR_SEED_ONLY"}

    # 3. Absent requested behavior (not in inventory): NO_MATCHING_EVIDENCE
    absent_result = inspect_canonical_evidence(canonical_repo, "ABSENT-BEHAVIOR-DOES-NOT-EXIST")
    assert absent_result["status"] == "VALID", absent_result
    assert absent_result["expected_seed_count"] == 4
    assert absent_result["seed_count"] == 4
    assert absent_result["requested_coverage"] == "NO_MATCHING_EVIDENCE"
    assert absent_result["coverage"] == {}

    # 4. Known seed with mismatched record hash or lineage: UNKNOWN
    unknown_result = inspect_canonical_evidence(canonical_repo, "WEB-DEMO-SEED-UNKNOWN")
    assert unknown_result["status"] == "VALID", unknown_result
    assert unknown_result["expected_seed_count"] == 4
    assert unknown_result["seed_count"] == 4
    assert unknown_result["requested_coverage"] == "UNKNOWN"
    assert unknown_result["coverage"] == {"WEB-DEMO-SEED-UNKNOWN": "UNKNOWN"}

    # Full audit without specific behavior_id preserves aggregate matrix
    audit_result = inspect_canonical_evidence(canonical_repo)
    assert audit_result["status"] == "VALID", audit_result
    assert audit_result["expected_seed_count"] == 4
    assert audit_result["seed_count"] == 4
    assert "requested_coverage" not in audit_result
    assert audit_result["coverage"] == {
        "API-DEMO-SEED-PROVEN": "PROVEN_FOR_REQUESTED_SCENARIO",
        "BOT-DEMO-SEED-PARTIAL": "PARTIAL_OR_SEED_ONLY",
        "WEB-DEMO-SEED-UNKNOWN": "UNKNOWN",
        "JOB-DEMO-SEED-EXTRA": "PARTIAL_OR_SEED_ONLY",
    }


def test_canonical_evidence_inconsistent_manifest_counts(tmp_path: Path) -> None:
    corrupt_repo = _make_canonical_repo(tmp_path / "corrupt_counts_repo", corrupt_counts=True)
    result = inspect_canonical_evidence(corrupt_repo, "API-DEMO-SEED-PROVEN")
    assert result["status"] == "INVENTORY_INCONSISTENT", result
    assert "canonical manifest counts invalid" in result.get("reason", "")
    assert "requested_coverage" not in result
    assert "coverage" not in result


def test_canonical_evidence_inconsistent_schema(tmp_path: Path) -> None:
    corrupt_repo = _make_canonical_repo(tmp_path / "corrupt_schema_repo", corrupt_schema=True)
    result = inspect_canonical_evidence(corrupt_repo, "API-DEMO-SEED-PROVEN")
    assert result["status"] == "INVENTORY_INCONSISTENT", result
    assert "invalid canonical manifest schema or stage" in result.get("reason", "")
    assert "requested_coverage" not in result
    assert "coverage" not in result
