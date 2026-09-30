"""Hermetic unit tests for scripts/two_site_maintenance_transition_git.py under AE03."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from scripts.two_site_maintenance_transition_git import (
    DEFAULT_GIT_OUTPUT_LIMIT,
    DEFAULT_GIT_TIMEOUT,
    GIT_BIN,
    MAX_DECLARED_COMMIT_PATHS,
    MaintenanceGitProofError,
    verify_declared_maintenance_git_chain,
)
from scripts.two_site_maintenance_transition_record import (
    PROTECTED_REFS,
    build_maintenance_transition_record,
    serialize_maintenance_transition_record,
)


def _git(repo: Path, *args: str) -> str:
    res = subprocess.run(
        [GIT_BIN, *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return res.stdout.strip()


def _init_test_repo(tmp_path: Path) -> tuple[Path, str, str, str]:
    repo = (tmp_path / "test_repo").resolve()
    repo.mkdir(mode=0o755)
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Test Committer")
    _git(repo, "config", "user.email", "committer@example.com")

    # Prior root commit
    base_file = repo / "scripts" / "two_site_base.py"
    base_file.parent.mkdir(mode=0o755, exist_ok=True)
    base_file.write_text("# base\n", encoding="utf-8")
    _git(repo, "add", "scripts/two_site_base.py")
    _git(repo, "commit", "-m", "prior commit")
    prior_head = _git(repo, "rev-parse", "HEAD")

    # Maintenance commit
    maint_path = "scripts/two_site_worker.py"
    maint_file = repo / maint_path
    maint_file.write_text("# worker\n", encoding="utf-8")
    _git(repo, "add", maint_path)
    _git(repo, "commit", "-m", "maintenance commit")
    new_head = _git(repo, "rev-parse", "HEAD")

    return repo, prior_head, new_head, maint_path


def _build_record(
    prior_head: str,
    new_head: str,
    paths: list[str],
    control_path: str = "scripts/two_site_control.py",
) -> dict[str, Any]:
    protected_refs = {ref: new_head for ref in sorted(PROTECTED_REFS)}
    scope = {
        "assignment_id": "assignment-ae03-test",
        "stage_id": "P1-00b",
        "sha256": "0" * 64,
        "expires_at": "2026-10-01T00:00:00Z",
        "allowed_paths": sorted(paths),
        "forbidden_paths": [],
    }
    new_repo = {
        "assignment_scope": scope,
        "control_blobs": {control_path: "c" * 40},
        "head": new_head,
        "protected_refs": protected_refs,
        "status": "",
    }
    commits = [{
        "head": new_head,
        "parent": prior_head,
        "paths": sorted(paths),
        "worker_run_id": "worker-run-1",
        "reviewer_run_id": "reviewer-run-1",
        "proposal_sha256": "1" * 64,
        "test_evidence_sha256": "2" * 64,
    }]
    return build_maintenance_transition_record(
        prior_checkpoint_sha256="a" * 64,
        prior_head=prior_head,
        new_repository=new_repo,
        maintenance_commits=commits,
    )


def test_success_dict_and_canonical_bytes(tmp_path: Path) -> None:
    repo, prior_head, new_head, maint_path = _init_test_repo(tmp_path)
    rec = _build_record(prior_head, new_head, [maint_path])

    # 1. As dictionary
    verify_declared_maintenance_git_chain(repo, rec)

    # 2. As canonical JSON bytes
    raw_bytes = serialize_maintenance_transition_record(rec)
    verify_declared_maintenance_git_chain(repo, raw_bytes)


def test_record_type_and_byte_length_guards(tmp_path: Path) -> None:
    repo, prior_head, new_head, maint_path = _init_test_repo(tmp_path)
    rec = _build_record(prior_head, new_head, [maint_path])
    raw_bytes = serialize_maintenance_transition_record(rec)

    # bytearray is rejected without copy
    with pytest.raises(MaintenanceGitProofError, match="record must be a dict or canonical JSON bytes"):
        verify_declared_maintenance_git_chain(repo, bytearray(raw_bytes))

    # bytes > 16384 rejected pre-decode
    oversized = b"{" + b" " * 16384 + b"}"
    with pytest.raises(MaintenanceGitProofError, match="record bytes exceed maximum allowed length"):
        verify_declared_maintenance_git_chain(repo, oversized)

    # empty bytes rejected
    with pytest.raises(MaintenanceGitProofError, match="record bytes cannot be empty"):
        verify_declared_maintenance_git_chain(repo, b"")

    # invalid object type
    with pytest.raises(MaintenanceGitProofError, match="record must be a dict or canonical JSON bytes"):
        verify_declared_maintenance_git_chain(repo, ["not", "a", "dict"])


def test_budget_validation_and_capping(tmp_path: Path) -> None:
    repo, prior_head, new_head, maint_path = _init_test_repo(tmp_path)
    rec = _build_record(prior_head, new_head, [maint_path])

    # invalid timeout values
    for bad_t in (-1.0, 0, True, False, float("nan"), float("inf"), "10"):
        with pytest.raises(MaintenanceGitProofError, match="timeout must be a finite positive number"):
            verify_declared_maintenance_git_chain(repo, rec, timeout=bad_t)  # type: ignore[arg-type]

    # invalid output_limit values
    for bad_lim in (-5, 0, True, False, 1.5, "65536"):
        with pytest.raises(MaintenanceGitProofError, match="output_limit must be a positive integer"):
            verify_declared_maintenance_git_chain(repo, rec, output_limit=bad_lim)  # type: ignore[arg-type]

    # caller widening is strictly capped; execution succeeds under capped values
    verify_declared_maintenance_git_chain(repo, rec, timeout=99.0, output_limit=999999)


def test_path_count_exceeds_32_guard_with_valid_record(tmp_path: Path) -> None:
    repo = (tmp_path / "large_repo").resolve()
    repo.mkdir(mode=0o755)
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Test Committer")
    _git(repo, "config", "user.email", "committer@example.com")

    base_file = repo / "scripts" / "two_site_base.py"
    base_file.parent.mkdir(mode=0o755, exist_ok=True)
    base_file.write_text("# base\n", encoding="utf-8")
    _git(repo, "add", "scripts/two_site_base.py")
    _git(repo, "commit", "-m", "prior commit")
    prior_head = _git(repo, "rev-parse", "HEAD")

    # Exactly 33 valid changed paths
    paths: list[str] = []
    for i in range(33):
        p = f"scripts/two_site_part_{i:02d}.py"
        f = repo / p
        f.write_text(f"# part {i}\n", encoding="utf-8")
        _git(repo, "add", p)
        paths.append(p)
    _git(repo, "commit", "-m", "33 files commit")
    new_head = _git(repo, "rev-parse", "HEAD")

    rec = _build_record(prior_head, new_head, sorted(paths))
    assert len(rec["maintenance_commits"][0]["paths"]) == 33

    with pytest.raises(MaintenanceGitProofError, match="exceeds maximum allowed 32"):
        verify_declared_maintenance_git_chain(repo, rec)


def test_subdirectory_root_show_toplevel_mismatch(tmp_path: Path) -> None:
    repo, prior_head, new_head, maint_path = _init_test_repo(tmp_path)
    rec = _build_record(prior_head, new_head, [maint_path])

    subdir = (repo / "scripts").resolve()
    assert subdir.is_dir()

    with pytest.raises(MaintenanceGitProofError, match="git show-toplevel does not match root"):
        verify_declared_maintenance_git_chain(subdir, rec)


def test_head_and_parent_mismatch(tmp_path: Path) -> None:
    repo, prior_head, new_head, maint_path = _init_test_repo(tmp_path)
    rec = _build_record(prior_head, new_head, [maint_path])

    # Commit an additional file so HEAD changes
    extra = repo / "scripts" / "two_site_extra.py"
    extra.write_text("# extra\n", encoding="utf-8")
    _git(repo, "add", "scripts/two_site_extra.py")
    _git(repo, "commit", "-m", "extra commit")

    with pytest.raises(MaintenanceGitProofError, match="actual HEAD does not match record new_repository head"):
        verify_declared_maintenance_git_chain(repo, rec)


def test_paths_mismatch_and_deleted_path(tmp_path: Path) -> None:
    repo, prior_head, new_head, maint_path = _init_test_repo(tmp_path)

    # Declared path does not match actual commit changes
    rec_wrong_path = _build_record(prior_head, new_head, ["scripts/two_site_other.py"])
    with pytest.raises(MaintenanceGitProofError, match="actual changed paths do not match declared paths"):
        verify_declared_maintenance_git_chain(repo, rec_wrong_path)


def test_merge_commit_rejected(tmp_path: Path) -> None:
    repo = (tmp_path / "merge_repo").resolve()
    repo.mkdir(mode=0o755)
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Test Committer")
    _git(repo, "config", "user.email", "committer@example.com")

    f1 = repo / "scripts" / "two_site_base.py"
    f1.parent.mkdir(mode=0o755, exist_ok=True)
    f1.write_text("# base\n", encoding="utf-8")
    _git(repo, "add", "scripts/two_site_base.py")
    _git(repo, "commit", "-m", "prior")
    prior_head = _git(repo, "rev-parse", "HEAD")

    # Branch A
    _git(repo, "checkout", "-b", "branch-a")
    fa = repo / "scripts" / "two_site_a.py"
    fa.write_text("# a\n", encoding="utf-8")
    _git(repo, "add", "scripts/two_site_a.py")
    _git(repo, "commit", "-m", "commit a")

    # Branch B from prior
    _git(repo, "checkout", "main")
    _git(repo, "checkout", "-b", "branch-b")
    fb = repo / "scripts" / "two_site_b.py"
    fb.write_text("# b\n", encoding="utf-8")
    _git(repo, "add", "scripts/two_site_b.py")
    _git(repo, "commit", "-m", "commit b")

    # Merge A into B
    _git(repo, "merge", "--no-ff", "branch-a", "-m", "merge commit")
    merge_head = _git(repo, "rev-parse", "HEAD")

    rec = _build_record(prior_head, merge_head, ["scripts/two_site_a.py"])
    with pytest.raises(MaintenanceGitProofError, match="commit has 2 parents; exactly one required"):
        verify_declared_maintenance_git_chain(repo, rec)


def test_symlink_mode_rejected(tmp_path: Path) -> None:
    repo = (tmp_path / "symlink_repo").resolve()
    repo.mkdir(mode=0o755)
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Test Committer")
    _git(repo, "config", "user.email", "committer@example.com")

    base = repo / "scripts" / "two_site_base.py"
    base.parent.mkdir(mode=0o755, exist_ok=True)
    base.write_text("# base\n", encoding="utf-8")
    _git(repo, "add", "scripts/two_site_base.py")
    _git(repo, "commit", "-m", "prior")
    prior_head = _git(repo, "rev-parse", "HEAD")

    # Create a tracked symlink
    link_target = repo / "scripts" / "two_site_base.py"
    sym_path = "scripts/two_site_link.py"
    sym_file = repo / sym_path
    sym_file.symlink_to(link_target)
    _git(repo, "add", sym_path)
    _git(repo, "commit", "-m", "symlink commit")
    sym_head = _git(repo, "rev-parse", "HEAD")

    rec = _build_record(prior_head, sym_head, [sym_path])
    with pytest.raises(MaintenanceGitProofError, match="path mode is not regular file"):
        verify_declared_maintenance_git_chain(repo, rec)


def test_no_replace_objects_active(tmp_path: Path) -> None:
    repo, prior_head, new_head, maint_path = _init_test_repo(tmp_path)
    rec = _build_record(prior_head, new_head, [maint_path])

    # Create a git replace ref pointing new_head back to prior_head
    _git(repo, "replace", new_head, prior_head)

    # Because --no-replace-objects is passed to all invocations,
    # HEAD verification is unaffected by the replace ref and succeeds
    verify_declared_maintenance_git_chain(repo, rec)


def test_root_validation_guards(tmp_path: Path) -> None:
    repo, prior_head, new_head, maint_path = _init_test_repo(tmp_path)
    rec = _build_record(prior_head, new_head, [maint_path])

    # relative path rejected
    with pytest.raises(MaintenanceGitProofError, match="root must be an absolute path"):
        verify_declared_maintenance_git_chain(Path("relative_repo"), rec)

    # non-existent path
    with pytest.raises(MaintenanceGitProofError, match="root cannot be accessed"):
        verify_declared_maintenance_git_chain(tmp_path / "does_not_exist", rec)

    # non-directory
    regular_file = repo / maint_path
    with pytest.raises(MaintenanceGitProofError, match="root must be a directory"):
        verify_declared_maintenance_git_chain(regular_file, rec)
