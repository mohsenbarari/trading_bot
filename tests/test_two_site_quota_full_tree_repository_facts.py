"""Hermetic AE03 C5d boundary proof for full-tree repository facts.

Original Provider remains synthetic and Broker/Supervisor live hook, joined
W2 continuation and replay authority are still unproven.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
from typing import Any

import pytest

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from scripts.two_site_codex_autopilot import (
    BRANCH,
    CONTROL_PATHS,
    PROTECTED_REFS,
    AutopilotError,
    validate_accepted_repository,
    validate_repository,
)
from scripts.two_site_proposal_runner import GEMINI_RUNTIME_REFERENCE_PATHS
from tests.test_two_site_assignment_scope_core import (
    _write_assignment,
    _write_exec_order,
    _write_worktrees,
)
from tests.test_two_site_provider_broker import broker_repo

SOURCE_ROOT = Path(__file__).resolve().parent.parent
ASSIGNMENT_ID = "WA-P1-00B-QUOTA-FULLTREE-20260927-492"
STAGE_ID = "P1-00b"


def _populate_full_tree_repo(root: Path) -> Path:
    root = root.resolve(strict=True)

    _write_exec_order(root, stage_id=STAGE_ID, state="IN_PROGRESS")
    _write_worktrees(
        root,
        path=str(root),
        branch=BRANCH,
        active_assignment_id=ASSIGNMENT_ID,
    )
    _write_assignment(
        root,
        assignment_id=ASSIGNMENT_ID,
        integration_branch=BRANCH,
        stage_id=STAGE_ID,
    )

    all_paths = set(CONTROL_PATHS) | set(GEMINI_RUNTIME_REFERENCE_PATHS)
    for rel in all_paths:
        src = SOURCE_ROOT / rel
        assert src.is_file(), f"Required source control path missing: {rel}"
        dst = root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(src.read_bytes())
        os.chmod(dst, src.stat().st_mode)

    files_to_commit = [p.as_posix() for p in all_paths]
    files_to_commit.append("docs/refactor/two-server/execution-order.yaml")
    subprocess.run(["git", "-C", str(root), "add", *files_to_commit], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "commit",
            "-qm",
            "feat: track control paths and runtime references",
        ],
        check=True,
    )
    subprocess.run(["git", "-C", str(root), "branch", "-M", BRANCH], check=True)

    head = subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", "HEAD"], text=True
    ).strip()
    assert re.fullmatch(r"[0-9a-f]{40}", head)

    for ref in PROTECTED_REFS:
        subprocess.run(["git", "-C", str(root), "update-ref", ref, head], check=True)

    status = subprocess.check_output(
        ["git", "-C", str(root), "status", "--porcelain=v1", "--untracked-files=all"],
        text=True,
    ).strip()
    assert status == ""

    return root


@pytest.fixture
def full_tree_repo(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
) -> Path:
    root, _task, _scope, _env = broker_repo
    return _populate_full_tree_repo(root)


def test_two_site_quota_full_tree_repository_facts_accepted(
    full_tree_repo: Path,
) -> None:
    """Real unpatched validate_repository accepts valid repository facts.

    Original Provider remains synthetic and Broker/Supervisor live hook, joined
    W2 continuation and replay authority are still unproven.
    """
    root = full_tree_repo
    facts = validate_repository(root)

    head = facts["head"]
    assert isinstance(head, str)
    assert re.fullmatch(r"[0-9a-f]{40}", head)

    status = facts["status"]
    assert status == ""

    protected_refs = facts["protected_refs"]
    assert isinstance(protected_refs, dict)
    assert set(protected_refs.keys()) == set(PROTECTED_REFS)
    for ref_name in PROTECTED_REFS:
        ref_sha = protected_refs[ref_name]
        assert isinstance(ref_sha, str)
        assert re.fullmatch(r"[0-9a-f]{40}", ref_sha)
        assert ref_sha == head

    control_blobs = facts["control_blobs"]
    assert isinstance(control_blobs, dict)
    expected_control_keys = {p.as_posix() for p in CONTROL_PATHS}
    assert set(control_blobs.keys()) == expected_control_keys
    for rel_str, blob_sha in control_blobs.items():
        assert isinstance(blob_sha, str)
        assert re.fullmatch(r"[0-9a-f]{40}", blob_sha)

    scope = facts["assignment_scope"]
    assert scope is not None
    assert scope.assignment_id == ASSIGNMENT_ID
    assert scope.stage_id == STAGE_ID

    accepted_facts = validate_accepted_repository(facts)
    assert accepted_facts is None or isinstance(accepted_facts, dict)
    if isinstance(accepted_facts, dict):
        assert accepted_facts["head"] == head
        assert accepted_facts["protected_refs"] == protected_refs
        assert accepted_facts["control_blobs"] == control_blobs


def test_two_site_quota_full_tree_repository_facts_hardlink_rejected(
    full_tree_repo: Path,
) -> None:
    """Hardlink outside temporary Git repo fails real unsafe-control guard.

    Original Provider remains synthetic and Broker/Supervisor live hook, joined
    W2 continuation and replay authority are still unproven.
    """
    root = full_tree_repo
    safe_control = Path("scripts/two_site_cycle_packet.py")
    assert safe_control in CONTROL_PATHS
    assert safe_control not in GEMINI_RUNTIME_REFERENCE_PATHS

    target_file = root / safe_control
    assert target_file.is_file()
    assert target_file.stat(follow_symlinks=False).st_nlink == 1

    outside_file = root.parent / "outside_cycle_packet_hardlink.tmp"
    if outside_file.exists():
        outside_file.unlink()
    os.link(target_file, outside_file)
    try:
        assert target_file.stat(follow_symlinks=False).st_nlink == 2
        match_pattern = (
            f"required tracked input is missing or unsafe: {re.escape(safe_control.as_posix())}"
        )
        with pytest.raises(AutopilotError, match=match_pattern):
            validate_repository(root, require_clean=False)
    finally:
        if outside_file.exists():
            outside_file.unlink()
