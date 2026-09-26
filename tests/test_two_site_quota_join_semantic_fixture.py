"""observational quota, no replay authority: hermetic fixture and test.

This test is only common Broker + semantic attempt, not C5d completion.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
from typing import Any

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_two_site_assignment_scope_core import (
    _write_assignment,
    _write_exec_order,
    _write_worktrees,
)
from scripts.two_site_assignment_scope import load_active_assignment_scope
from scripts.two_site_semantic_task_builder import build_task_identity
from scripts.two_site_semantic_attempts import (
    MARKER,
    read_existing_semantic_attempt,
    reserve_semantic_attempt,
)
from scripts.two_site_proposal_runner import load_task
from scripts.two_site_provider_broker import validate_request
from tests.test_two_site_provider_broker import broker_repo, make_valid_request


def setup_two_site_quota_join_semantic_fixture(
    root: Path,
    task: Path,
    original_scope: Any,
    original_environment: dict[str, str],
) -> dict[str, Any]:
    """Hermetic setup helper for two-site quota join semantic fixture."""
    _write_exec_order(root)
    gap_rel = "docs/refactor/two-server/ae03-fixture-gap.md"
    gap_file = root / gap_rel
    gap_file.parent.mkdir(parents=True, exist_ok=True)
    gap_file.write_text("# gap\n\nFixture anchor documentation.\n", encoding="utf-8")

    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "add",
            "docs/refactor/two-server/execution-order.yaml",
            gap_rel,
        ],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "commit",
            "-qm",
            "docs: add ae03 fixture gap and execution order",
        ],
        check=True,
    )

    head = subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", "HEAD"], text=True
    ).strip()

    _write_worktrees(
        root,
        branch="refactor/two-site-architecture",
        active_assignment_id="WA-BROKER-TEST",
    )
    _write_assignment(
        root,
        assignment_id="WA-BROKER-TEST",
        integration_branch="refactor/two-site-architecture",
        allowed_paths=["tests/test_new.py"],
        forbidden_paths=["scripts/**"],
    )

    actual_scope = load_active_assignment_scope(root)
    assert actual_scope.stage_id == "P1-00b"

    updated_environment = dict(original_environment)
    updated_environment["TWO_SITE_ASSIGNMENT_SHA256"] = actual_scope.sha256

    gap = f"{gap_rel}#gap"
    scenario = "ae03.c5d.real-common-semantic-fixture-330"
    context_files = ["source.py"]
    allowed_files = ["tests/test_new.py"]

    identity = build_task_identity(
        root,
        gap,
        scenario,
        context_files,
        allowed_files,
        [],
    )

    package = {
        "head": head,
        "gap_reference": gap,
        "excerpts": [],
        "semantic_identity": identity,
    }

    original_task_data = json.loads(task.read_text(encoding="utf-8"))
    goal_prefix = original_task_data["goal"]
    if MARKER in goal_prefix:
        goal_prefix = goal_prefix.split(MARKER, 1)[0]
    assert goal_prefix.startswith("WA-BROKER-TEST:")

    task_payload = dict(original_task_data)
    task_payload["goal"] = (
        goal_prefix + MARKER + json.dumps(package, separators=(",", ":"))
    )
    task.write_text(json.dumps(task_payload, indent=2), encoding="utf-8")

    request = make_valid_request(root, task, updated_environment)

    validated_req, validated_task_path, validated_scope = validate_request(
        root, request, scope_loader=lambda _: actual_scope
    )
    assert validated_req == request
    assert validated_task_path == task
    assert validated_scope == actual_scope

    task_result = load_task(root, task)
    loaded_task = task_result[0] if isinstance(task_result, tuple) else task_result
    assert loaded_task["task_id"] == task_payload["task_id"]
    assert loaded_task["goal"] == task_payload["goal"]

    autopilot_dir = root / ".local/two-site-refactor/autopilot"
    autopilot_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(autopilot_dir, 0o700)

    reserve_semantic_attempt(
        root,
        root / ".local/two-site-refactor",
        loaded_task,
        loaded_task["task_id"],
        request["run_prefix"],
        now=request["created_at"],
    )

    persisted = read_existing_semantic_attempt(
        root,
        root / ".local/two-site-refactor",
        loaded_task,
        request["run_prefix"],
    )
    assert persisted["fingerprint"] == identity["fingerprint"]
    assert persisted["task_id"] == loaded_task["task_id"]
    assert persisted["run_prefix"] == request["run_prefix"]

    attempt_file = (
        root
        / ".local/two-site-refactor/semantic-attempts"
        / f"{identity['fingerprint']}.json"
    )
    assert stat.S_IMODE(attempt_file.stat().st_mode) == 0o600
    assert stat.S_ISREG(attempt_file.stat().st_mode)
    assert attempt_file.stat().st_nlink == 1

    return {
        "root": root,
        "task": task,
        "head": head,
        "actual_scope": actual_scope,
        "updated_environment": updated_environment,
        "request": request,
        "loaded_task": loaded_task,
        "identity": identity,
        "package": package,
        "persisted_attempt": persisted,
        "attempt_file": attempt_file,
    }


def test_two_site_quota_join_semantic_fixture(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
) -> None:
    root, task, original_scope, original_environment = broker_repo
    fixture = setup_two_site_quota_join_semantic_fixture(
        root, task, original_scope, original_environment
    )

    request = fixture["request"]
    head = fixture["head"]
    scope = fixture["actual_scope"]
    loaded_task = fixture["loaded_task"]
    identity = fixture["identity"]
    persisted = fixture["persisted_attempt"]

    assert request["source_head"] == head
    expected_task_sha = hashlib.sha256(task.read_bytes()).hexdigest()
    assert request["task_sha256"] == expected_task_sha
    assert request["assignment_id"] == scope.assignment_id
    assert request["assignment_sha256"] == scope.sha256

    git_status = subprocess.check_output(
        ["git", "-C", str(root), "status", "--porcelain=v1"], text=True
    ).strip()
    assert git_status == ""

    assert loaded_task["task_id"] == "BROKER-TEST-1"
    assert loaded_task["context_files"] == ["source.py"]
    assert loaded_task["allowed_files"] == ["tests/test_new.py"]
    assert MARKER in loaded_task["goal"]

    package_json = loaded_task["goal"].split(MARKER, 1)[1]
    parsed_package = json.loads(package_json)
    assert set(parsed_package.keys()) == {
        "head",
        "gap_reference",
        "excerpts",
        "semantic_identity",
    }
    assert parsed_package["head"] == head
    assert (
        parsed_package["gap_reference"]
        == "docs/refactor/two-server/ae03-fixture-gap.md#gap"
    )
    assert parsed_package["excerpts"] == []
    assert (
        parsed_package["semantic_identity"]["fingerprint"]
        == identity["fingerprint"]
    )

    assert persisted["fingerprint"] == identity["fingerprint"]
    assert persisted["task_id"] == "BROKER-TEST-1"
    assert persisted["run_prefix"] == request["run_prefix"]
    assert persisted["source_sha256"] == identity["source_sha256"]
    assert persisted["stage_id"] == "P1-00b"
    assert (
        persisted["gap_reference"]
        == "docs/refactor/two-server/ae03-fixture-gap.md#gap"
    )
    assert persisted["scenario_key"] == "ae03.c5d.real-common-semantic-fixture-330"
    assert persisted["state"] == "RESERVED"
    assert stat.S_IMODE(fixture["attempt_file"].stat().st_mode) == 0o600
