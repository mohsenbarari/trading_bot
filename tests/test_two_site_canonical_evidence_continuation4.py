"""Adversarial and canonical tests for continuation-4 evidence inspection."""
from __future__ import annotations

from collections.abc import Callable
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

os.environ.setdefault("APP_ENV_FILE", "config/unit-test.env.example")
from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

install_p1_00_network_fence()

from scripts.two_site_cycle_tools import inspect_canonical_evidence
from tests.test_two_site_canonical_evidence_inspect import _make_canonical_repo, _run_git


def _make_continuation4_repo(
    repo: Path,
    *,
    records: list[dict] | Callable[[str, str, str, str], list[dict]] | None = None,
    predecessor_path: str | None = None,
    predecessor_sha: str | None = None,
    predecessor_last_commit: str | None = None,
    extra_manifest_sources: dict[str, str] | None = None,
    continuation4_padding: int = 0,
    commit_continuation4: bool = True,
) -> tuple[Path, str, str, str, str]:
    _make_canonical_repo(repo)
    c0 = _run_git(repo, "log", "--format=%H", "-1", "tests/test_sample.py")
    c0_content = "def test_sample() -> None:\n    pass\n"
    c0_sha = hashlib.sha256(c0_content.encode("utf-8")).hexdigest()

    c4_content = "def test_sample() -> None:\n    # continuation 4 modification\n    pass\n"
    (repo / "tests/test_sample.py").write_text(c4_content, encoding="utf-8")
    c4_sha = hashlib.sha256(c4_content.encode("utf-8")).hexdigest()
    _run_git(repo, "add", "tests/test_sample.py")
    _run_git(repo, "commit", "-m", "commit 4: update test_sample.py")
    c4 = _run_git(repo, "rev-parse", "HEAD")

    c3_rel = "docs/refactor/two-server/section-1/inventory/continuation-3.json"
    c3_bytes = (repo / c3_rel).read_bytes()
    real_c3_sha = hashlib.sha256(c3_bytes).hexdigest()
    real_c3_commit = _run_git(repo, "log", "-1", "--format=%H", "HEAD", "--", c3_rel)

    link_path = predecessor_path if predecessor_path is not None else c3_rel
    link_sha = predecessor_sha if predecessor_sha is not None else real_c3_sha
    link_commit = predecessor_last_commit if predecessor_last_commit is not None else real_c3_commit

    if callable(records):
        resolved_records = records(c0, c0_sha, c4, c4_sha)
    elif records is not None:
        resolved_records = records
    else:
        resolved_records = [
            {
                "seed_ids": ["API-DEMO-SEED-PROVEN"],
                "path": "tests/test_sample.py",
                "sha256": c4_sha,
                "last_commit": c4,
                "test_selectors": ["tests/test_sample.py::test_sample"],
            },
            {
                "seed_ids": ["API-DEMO-SEED-PROVEN"],
                "path": "tests/test_sample.py",
                "sha256": c0_sha,
                "last_commit": c0,
                "test_selectors": ["tests/test_sample.py::test_sample"],
            },
            {
                "seed_ids": ["BOT-DEMO-SEED-PARTIAL"],
                "path": "tests/test_sample.py",
                "sha256": c0_sha,
                "last_commit": c0,
                "test_selectors": ["tests/test_sample.py::test_sample"],
            },
        ]

    c4_doc: dict = {
        "schema_version": 1,
        "stage": "P1-00b",
        "predecessor_inventory": {
            "path": link_path,
            "sha256": link_sha,
            "last_commit": link_commit,
        },
        "records": resolved_records,
    }
    if continuation4_padding > 0:
        c4_doc["padding"] = "x" * continuation4_padding

    c4_text = json.dumps(c4_doc, indent=2) + "\n"
    c4_file = repo / "docs/refactor/two-server/section-1/inventory/continuation-4.json"
    c4_file.write_text(c4_text, encoding="utf-8")

    manifest_file = repo / "docs/refactor/two-server/section-1/inventory/seed-handler-coverage.json"
    manifest_doc = json.loads(manifest_file.read_text(encoding="utf-8"))
    manifest_doc["evidence_sources"]["continuation_4_path"] = "docs/refactor/two-server/section-1/inventory/continuation-4.json"
    if extra_manifest_sources:
        manifest_doc["evidence_sources"].update(extra_manifest_sources)
    manifest_file.write_text(json.dumps(manifest_doc, indent=2) + "\n", encoding="utf-8")

    if commit_continuation4:
        _run_git(
            repo,
            "add",
            "docs/refactor/two-server/section-1/inventory/continuation-4.json",
            "docs/refactor/two-server/section-1/inventory/seed-handler-coverage.json",
        )
        _run_git(repo, "commit", "-m", "commit 5: continuation-4 and manifest update")

    return repo, c0, c0_sha, c4, c4_sha


def test_real_worktree_continuation4_evidence() -> None:
    root = Path(__file__).resolve().parents[1]
    probe = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        cwd=root,
        text=True,
        capture_output=True,
    )
    if probe.returncode != 0 or probe.stdout.strip() != "true":
        pytest.skip("Git metadata unavailable in the hermetic snapshot")
    behavior = "BOT-ADMIN-MESSAGE-ADMIN-PROCESS-INVITATION-ACCOUNT-NAME-L183"
    result = inspect_canonical_evidence(root, behavior)
    assert result["status"] == "VALID", result
    assert result["expected_seed_count"] == result["seed_count"] == 465
    assert result["requested_coverage"] == "PROVEN_FOR_REQUESTED_SCENARIO"


def test_real_worktree_cli_inventory_request() -> None:
    root = Path(__file__).resolve().parents[1]
    probe = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        cwd=root,
        text=True,
        capture_output=True,
    )
    if probe.returncode != 0 or probe.stdout.strip() != "true":
        pytest.skip("Git metadata unavailable in the hermetic snapshot")
    behavior = "BOT-ADMIN-MESSAGE-ADMIN-PROCESS-INVITATION-ACCOUNT-NAME-L183"
    env = {
        **os.environ,
        "APP_ENV_FILE": "config/unit-test.env.example",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    proc = subprocess.run(
        [
            sys.executable,
            str(root / "scripts/two_site_cycle_tools.py"),
            "inventory",
            "--behavior-id",
            behavior,
        ],
        cwd=root,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    raw_output = proc.stdout[:65536]
    data = json.loads(raw_output)
    assert data.get("status") == "VALID", data
    assert data.get("requested_coverage") == "PROVEN_FOR_REQUESTED_SCENARIO"


def test_continuation4_synthetic_current_and_historical_proof(tmp_path: Path) -> None:
    repo, c0, c0_sha, c4, c4_sha = _make_continuation4_repo(tmp_path / "c4_repo")

    # 1. API-DEMO-SEED-PROVEN has both current receipt and historical receipt.
    # Current receipt matches current HEAD blob; seed is PROVEN.
    proven_result = inspect_canonical_evidence(repo, "API-DEMO-SEED-PROVEN")
    assert proven_result["status"] == "VALID", proven_result
    assert proven_result["expected_seed_count"] == 4
    assert proven_result["seed_count"] == 4
    assert proven_result["requested_coverage"] == "PROVEN_FOR_REQUESTED_SCENARIO"
    assert proven_result["coverage"] == {"API-DEMO-SEED-PROVEN": "PROVEN_FOR_REQUESTED_SCENARIO"}

    # 2. BOT-DEMO-SEED-PARTIAL has only a historical receipt.
    # It enters unknown, not proven; requested coverage is UNKNOWN.
    historical_result = inspect_canonical_evidence(repo, "BOT-DEMO-SEED-PARTIAL")
    assert historical_result["status"] == "VALID", historical_result
    assert historical_result["expected_seed_count"] == 4
    assert historical_result["seed_count"] == 4
    assert historical_result["requested_coverage"] == "UNKNOWN"
    assert historical_result["coverage"] == {"BOT-DEMO-SEED-PARTIAL": "UNKNOWN"}

    # 3. Old five-source fixture remains VALID.
    old_repo = _make_canonical_repo(tmp_path / "old_five_source_repo")
    old_result = inspect_canonical_evidence(old_repo)
    assert old_result["status"] == "VALID", old_result
    assert old_result["expected_seed_count"] == 4
    assert old_result["seed_count"] == 4


def test_negative_missing_continuation4_path(tmp_path: Path) -> None:
    repo, _, _, _, _ = _make_continuation4_repo(tmp_path / "missing_c4_repo")
    _run_git(
        repo,
        "rm",
        "docs/refactor/two-server/section-1/inventory/continuation-4.json",
    )
    _run_git(repo, "commit", "-m", "commit 6: delete continuation 4 file")
    result = inspect_canonical_evidence(repo, "API-DEMO-SEED-PROVEN")
    assert result["status"] == "INVENTORY_INCONSISTENT", result
    assert any(
        phrase in result.get("reason", "")
        for phrase in ("continuation-4.json", "ls-files", "canonical file read failed", "non-zero exit status")
    )


def test_negative_tampered_predecessor_path(tmp_path: Path) -> None:
    repo, _, _, _, _ = _make_continuation4_repo(
        tmp_path / "tampered_pred_path_repo",
        predecessor_path="docs/refactor/two-server/section-1/inventory/continuation-wrong.json",
    )
    result = inspect_canonical_evidence(repo, "API-DEMO-SEED-PROVEN")
    assert result["status"] == "INVENTORY_INCONSISTENT", result
    assert "continuation 4 invalid" in result.get("reason", "")


def test_negative_tampered_predecessor_sha(tmp_path: Path) -> None:
    repo, _, _, _, _ = _make_continuation4_repo(
        tmp_path / "tampered_pred_sha_repo",
        predecessor_sha="0" * 64,
    )
    result = inspect_canonical_evidence(repo, "API-DEMO-SEED-PROVEN")
    assert result["status"] == "INVENTORY_INCONSISTENT", result
    assert "continuation 4 invalid" in result.get("reason", "")


def test_negative_tampered_predecessor_last_commit(tmp_path: Path) -> None:
    repo, _, _, _, _ = _make_continuation4_repo(
        tmp_path / "tampered_pred_commit_repo",
        predecessor_last_commit="0" * 40,
    )
    result = inspect_canonical_evidence(repo, "API-DEMO-SEED-PROVEN")
    assert result["status"] == "INVENTORY_INCONSISTENT", result
    assert "continuation 4 invalid" in result.get("reason", "")


def test_negative_malformed_historical_old_sha(tmp_path: Path) -> None:
    repo, _, _, _, _ = _make_continuation4_repo(
        tmp_path / "malformed_hist_sha_repo",
        records=lambda c0, c0_sha, c4, c4_sha: [
            {
                "seed_ids": ["API-DEMO-SEED-PROVEN"],
                "path": "tests/test_sample.py",
                "sha256": c4_sha,
                "last_commit": c4,
                "test_selectors": ["tests/test_sample.py::test_sample"],
            },
            {
                "seed_ids": ["API-DEMO-SEED-PROVEN"],
                "path": "tests/test_sample.py",
                "sha256": "f" * 64,
                "last_commit": c0,
                "test_selectors": ["tests/test_sample.py::test_sample"],
            },
        ],
    )
    result = inspect_canonical_evidence(repo, "API-DEMO-SEED-PROVEN")
    assert result["status"] == "INVENTORY_INCONSISTENT", result
    assert "historical record invalid" in result.get("reason", "")


def test_negative_malformed_historical_nonancestor(tmp_path: Path) -> None:
    repo, _, _, _, _ = _make_continuation4_repo(
        tmp_path / "nonancestor_hist_repo",
        records=lambda c0, c0_sha, c4, c4_sha: [
            {
                "seed_ids": ["API-DEMO-SEED-PROVEN"],
                "path": "tests/test_sample.py",
                "sha256": c4_sha,
                "last_commit": c4,
                "test_selectors": ["tests/test_sample.py::test_sample"],
            },
            {
                "seed_ids": ["API-DEMO-SEED-PROVEN"],
                "path": "tests/test_sample.py",
                "sha256": "a" * 64,
                "last_commit": "1" * 40,
                "test_selectors": ["tests/test_sample.py::test_sample"],
            },
        ],
    )
    result = inspect_canonical_evidence(repo, "API-DEMO-SEED-PROVEN")
    assert result["status"] == "INVENTORY_INCONSISTENT", result
    assert "historical record invalid" in result.get("reason", "")


def test_negative_duplicate_conflicting_current_receipt(tmp_path: Path) -> None:
    repo, _, _, _, _ = _make_continuation4_repo(
        tmp_path / "dup_current_repo",
        records=lambda c0, c0_sha, c4, c4_sha: [
            {
                "seed_ids": ["API-DEMO-SEED-PROVEN"],
                "path": "tests/test_sample.py",
                "sha256": c4_sha,
                "last_commit": c4,
                "test_selectors": ["tests/test_sample.py::test_sample"],
            },
            {
                "seed_ids": ["API-DEMO-SEED-PROVEN"],
                "path": "tests/test_sample.py",
                "sha256": c4_sha,
                "last_commit": c4,
                "test_selectors": ["tests/test_sample.py::test_sample_alt"],
            },
        ],
    )
    result = inspect_canonical_evidence(repo, "API-DEMO-SEED-PROVEN")
    assert result["status"] == "INVENTORY_INCONSISTENT", result
    assert "conflicting current evidence" in result.get("reason", "")


def test_negative_stale_current_head_blob(tmp_path: Path) -> None:
    repo, _, _, _, _ = _make_continuation4_repo(
        tmp_path / "stale_head_repo",
        records=lambda c0, c0_sha, c4, c4_sha: [
            {
                "seed_ids": ["API-DEMO-SEED-PROVEN"],
                "path": "tests/test_sample.py",
                "sha256": "0" * 64,
                "last_commit": c4,
                "test_selectors": ["tests/test_sample.py::test_sample"],
            },
        ],
    )
    result = inspect_canonical_evidence(repo, "API-DEMO-SEED-PROVEN")
    assert result["status"] in ("VALID", "INVENTORY_INCONSISTENT"), result
    assert result.get("requested_coverage") != "PROVEN_FOR_REQUESTED_SCENARIO"
    assert result.get("requested_coverage") == "UNKNOWN"


def test_negative_extra_seventh_source_key(tmp_path: Path) -> None:
    repo, _, _, _, _ = _make_continuation4_repo(
        tmp_path / "extra_source_repo",
        extra_manifest_sources={
            "continuation_5_path": "docs/refactor/two-server/section-1/inventory/continuation-5.json"
        },
    )
    result = inspect_canonical_evidence(repo, "API-DEMO-SEED-PROVEN")
    assert result["status"] == "INVENTORY_INCONSISTENT", result
    assert "canonical manifest inventory or sources invalid" in result.get("reason", "")


def test_negative_continuation4_oversized(tmp_path: Path) -> None:
    repo, _, _, _, _ = _make_continuation4_repo(
        tmp_path / "oversized_c4_repo",
        continuation4_padding=2_050_000,
    )
    result = inspect_canonical_evidence(repo, "API-DEMO-SEED-PROVEN")
    assert result["status"] == "INVENTORY_INCONSISTENT", result
    assert "canonical file read failed" in result.get("reason", "")
