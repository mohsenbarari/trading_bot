"""Core contract tests for active-Assignment scope incident repair."""

from __future__ import annotations

import datetime
from pathlib import Path
import subprocess
from typing import Any, Dict
import pytest
import yaml

from scripts.two_site_assignment_scope import (
    AssignmentScope,
    AssignmentScopeError,
    load_active_assignment_scope,
    load_expected_assignment_scope,
    validate_changed_paths,
    validate_task_scope,
)

ASSIGNMENT_ID = "WA-P1-00B-FREEZE-20260904-001"
STAGE_ID = "P1-00b"
BRANCH = "main"


def _write_assignment(
    repo_dir: Path,
    assignment_id: str = ASSIGNMENT_ID,
    **overrides: Any,
) -> None:
    data: Dict[str, Any] = {
        "id": assignment_id,
        "status": "READY",
        "role": "WORKER",
        "integration_branch": BRANCH,
        "external_access": "NONE",
        "expires_at": "2099-01-01T00:00:00Z",
        "stage_id": STAGE_ID,
        "allowed_paths": [
            "scripts/two_site_assignment_scope.py",
            "tests/test_*.py",
        ],
        "forbidden_paths": [
            "tests/test_bot_trade_execute_local_success.py",
        ],
    }
    data.update(overrides)
    doc = repo_dir / f".local/two-site-refactor/assignments/{assignment_id}.md"
    doc.parent.mkdir(parents=True, exist_ok=True)
    body = yaml.dump(data, sort_keys=False).strip()
    doc.write_text(f"# Assignment\n\n```yaml\n{body}\n```\n", encoding="utf-8")


def _write_worktrees(
    repo_dir: Path,
    path: str | None = None,
    branch: str = BRANCH,
    active_assignment_id: str = ASSIGNMENT_ID,
) -> None:
    wt_data = {
        "integration": {
            "path": path if path is not None else str(repo_dir),
            "branch": branch,
            "active_assignment_id": active_assignment_id,
        }
    }
    wt_file = repo_dir / ".local/two-site-refactor/worktrees.yaml"
    wt_file.parent.mkdir(parents=True, exist_ok=True)
    wt_file.write_text(yaml.dump(wt_data), encoding="utf-8")


def _write_exec_order(
    repo_dir: Path,
    stages: Any = None,
    stage_id: str = STAGE_ID,
    state: str = "IN_PROGRESS",
) -> None:
    if stages is None:
        eo_data = {"stages": {stage_id: {"state": state}}}
    else:
        eo_data = {"stages": stages}
    eo_file = repo_dir / "docs/refactor/two-server/execution-order.yaml"
    eo_file.parent.mkdir(parents=True, exist_ok=True)
    eo_file.write_text(yaml.dump(eo_data), encoding="utf-8")


@pytest.fixture
def valid_repo(tmp_path: Path) -> Path:
    repo = tmp_path.resolve()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "ci@example.com"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "CI"], check=True)

    _write_exec_order(repo)
    subprocess.run(["git", "-C", str(repo), "add", "docs/refactor/two-server/execution-order.yaml"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "init"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "branch", "-M", BRANCH], check=True)

    _write_worktrees(repo)
    _write_assignment(repo)
    return repo


def test_load_active_assignment_scope_success(valid_repo: Path) -> None:
    scope = load_active_assignment_scope(valid_repo)
    assert isinstance(scope, AssignmentScope)
    assert scope.assignment_id == ASSIGNMENT_ID
    assert scope.stage_id == STAGE_ID
    assert scope.allowed_paths == (
        "scripts/two_site_assignment_scope.py",
        "tests/test_*.py",
    )
    assert scope.forbidden_paths == (
        "tests/test_bot_trade_execute_local_success.py",
    )
    assert scope.expires_at == "2099-01-01T00:00:00Z"
    assert len(scope.sha256) == 64
    scope_second = load_active_assignment_scope(valid_repo)
    assert scope_second.sha256 == scope.sha256


def test_load_expected_assignment_scope_binding_and_rejections(valid_repo: Path) -> None:
    scope = load_active_assignment_scope(valid_repo)
    expected = load_expected_assignment_scope(valid_repo, scope.assignment_id, scope.sha256)
    assert expected == scope

    with pytest.raises(AssignmentScopeError, match="expected assignment ID"):
        load_expected_assignment_scope(valid_repo, "STALE-ASSIGNMENT-ID", scope.sha256)

    with pytest.raises(AssignmentScopeError, match="sha256 digest does not match"):
        load_expected_assignment_scope(valid_repo, scope.assignment_id, "0" * 64)


def test_validate_allowed_characterization_and_scope_rejections(valid_repo: Path) -> None:
    scope = load_active_assignment_scope(valid_repo)
    char_path = "tests/test_p1_00b_characterization_scope_guard.py"
    validate_changed_paths(scope, [char_path, "scripts/two_site_assignment_scope.py"])
    validate_task_scope(
        scope,
        {
            "allowed_files": [char_path, "scripts/two_site_assignment_scope.py"],
            "goal": f"Execute incident {ASSIGNMENT_ID} core repair",
        },
    )

    with pytest.raises(AssignmentScopeError, match="outside assignment scope"):
        validate_changed_paths(scope, ["tests/test_bot_trade_execute_local_success.py"])

    with pytest.raises(AssignmentScopeError, match="outside assignment scope"):
        validate_changed_paths(scope, ["scripts/other_untracked.py"])


def test_forbidden_precedence_over_allowed_pattern(valid_repo: Path) -> None:
    scope = load_active_assignment_scope(valid_repo)
    target = "tests/test_bot_trade_execute_local_success.py"
    with pytest.raises(AssignmentScopeError, match="outside assignment scope"):
        validate_changed_paths(scope, [target])
    with pytest.raises(AssignmentScopeError, match="outside assignment scope"):
        validate_task_scope(scope, {"allowed_files": [target], "goal": f"Fix {ASSIGNMENT_ID}"})


@pytest.mark.parametrize(
    "goal,should_succeed",
    [
        (f"Task for {ASSIGNMENT_ID} fix", True),
        (f"Run [{ASSIGNMENT_ID}] now", True),
        ("Task without assignment token", False),
        (f"Task PREFIX_{ASSIGNMENT_ID}", False),
        (f"Task {ASSIGNMENT_ID}_SUFFIX", False),
        (f"Task SUB-{ASSIGNMENT_ID}", False),
        (f"Task {ASSIGNMENT_ID}-EXTRA", False),
        (f"Task PREFIX{ASSIGNMENT_ID}", False),
        (f"Task {ASSIGNMENT_ID}SUFFIX", False),
    ],
)
def test_validate_task_scope_assignment_token(
    valid_repo: Path, goal: str, should_succeed: bool
) -> None:
    scope = load_active_assignment_scope(valid_repo)
    task = {"allowed_files": ["scripts/two_site_assignment_scope.py"], "goal": goal}
    if should_succeed:
        validate_task_scope(scope, task)
    else:
        with pytest.raises(AssignmentScopeError, match="bounded token"):
            validate_task_scope(scope, task)


def test_validate_paths_empty_and_duplicate(valid_repo: Path) -> None:
    scope = load_active_assignment_scope(valid_repo)
    path = "scripts/two_site_assignment_scope.py"
    goal = f"Fix {ASSIGNMENT_ID}"

    with pytest.raises(AssignmentScopeError, match="paths cannot be empty"):
        validate_changed_paths(scope, [])
    with pytest.raises(AssignmentScopeError, match="allowed_files cannot be empty"):
        validate_task_scope(scope, {"allowed_files": [], "goal": goal})

    with pytest.raises(AssignmentScopeError, match="duplicate paths"):
        validate_changed_paths(scope, [path, path])
    with pytest.raises(AssignmentScopeError, match="duplicate paths"):
        validate_task_scope(scope, {"allowed_files": [path, path], "goal": goal})


@pytest.mark.parametrize(
    "bad_path,err_match",
    [
        ("", "cannot be empty"),
        ("/scripts/two_site_assignment_scope.py", "Absolute paths rejected"),
        ("-scripts/two_site_assignment_scope.py", "Leading dash rejected"),
        ("scripts/../escape.py", "Dot-dot '..' component rejected"),
        ("scripts/./dot.py", "Dot '.' component rejected"),
        ("scripts//double.py", "Empty path component rejected"),
        ("scripts/wild*.py", "Wildcard characters rejected in concrete path"),
    ],
)
def test_validate_paths_malformed(valid_repo: Path, bad_path: str, err_match: str) -> None:
    scope = load_active_assignment_scope(valid_repo)
    with pytest.raises(AssignmentScopeError, match=err_match):
        validate_changed_paths(scope, [bad_path])


def test_assignment_malformed_pattern(valid_repo: Path) -> None:
    _write_assignment(valid_repo, allowed_paths=["tests/[a-z.py"])
    with pytest.raises(AssignmentScopeError, match="Unclosed bracket expression in pattern"):
        load_active_assignment_scope(valid_repo)


@pytest.mark.parametrize(
    "bad_expires,err_match",
    [
        ("2020-01-01T00:00:00Z", "Assignment has expired"),
        ("2099-01-01T00:00:00", "must include timezone information"),
        (datetime.date(2099, 1, 1), "must include time and timezone information"),
    ],
)
def test_assignment_expiry_modes(valid_repo: Path, bad_expires: Any, err_match: str) -> None:
    _write_assignment(valid_repo, expires_at=bad_expires)
    with pytest.raises(AssignmentScopeError, match=err_match):
        load_active_assignment_scope(valid_repo)


@pytest.mark.parametrize(
    "field,bad_val,err_match",
    [
        ("status", "DRAFT", "Assignment status must be READY"),
        ("role", "CONTRIBUTOR", "Assignment role must be WORKER"),
        ("external_access", "FULL", "Assignment external_access must be NONE"),
        ("integration_branch", "feat-branch", "Assignment integration_branch does not match"),
        ("id", "WRONG-ID", "Assignment document id does not match"),
    ],
)
def test_assignment_invalid_fields(
    valid_repo: Path, field: str, bad_val: str, err_match: str
) -> None:
    _write_assignment(valid_repo, **{field: bad_val})
    with pytest.raises(AssignmentScopeError, match=err_match):
        load_active_assignment_scope(valid_repo)


@pytest.mark.parametrize(
    "key,bad_val,err_match",
    [
        ("path", "mismatch_path", "Registry integration path does not match"),
        ("branch", "feat-branch", "Registry branch does not match"),
    ],
)
def test_worktrees_registry_mismatch(
    valid_repo: Path, key: str, bad_val: str, err_match: str
) -> None:
    bad_path = str(valid_repo.parent) if key == "path" else str(valid_repo)
    bad_branch = bad_val if key == "branch" else BRANCH
    _write_worktrees(valid_repo, path=bad_path, branch=bad_branch)
    with pytest.raises(AssignmentScopeError, match=err_match):
        load_active_assignment_scope(valid_repo)


@pytest.mark.parametrize(
    "stages_payload,err_match",
    [
        ({"OTHER_STAGE": {"state": "IN_PROGRESS"}}, f"Stage '{STAGE_ID}' not found"),
        ({STAGE_ID: {"state": "PENDING"}}, "expected 'IN_PROGRESS'"),
        ({STAGE_ID: {"state": "COMPLETED"}}, "expected 'IN_PROGRESS'"),
        (
            [{"id": STAGE_ID, "state": "IN_PROGRESS"}, {"id": STAGE_ID, "state": "IN_PROGRESS"}],
            "appears multiple times in execution-order",
        ),
    ],
)
def test_execution_order_stage_validation(
    valid_repo: Path, stages_payload: Any, err_match: str
) -> None:
    _write_exec_order(valid_repo, stages=stages_payload)
    with pytest.raises(AssignmentScopeError, match=err_match):
        load_active_assignment_scope(valid_repo)


def test_execution_order_stage_list_form_success(valid_repo: Path) -> None:
    _write_exec_order(valid_repo, stages=[{"id": STAGE_ID, "state": "IN_PROGRESS"}])
    scope = load_active_assignment_scope(valid_repo)
    assert scope.stage_id == STAGE_ID


def test_document_drift_changes_digest(valid_repo: Path) -> None:
    scope = load_active_assignment_scope(valid_repo)
    doc = valid_repo / f".local/two-site-refactor/assignments/{ASSIGNMENT_ID}.md"
    doc.write_text(doc.read_text(encoding="utf-8") + "\n# Drift comment\n", encoding="utf-8")

    drifted_scope = load_active_assignment_scope(valid_repo)
    assert drifted_scope.sha256 != scope.sha256

    with pytest.raises(AssignmentScopeError, match="sha256 digest does not match expected digest"):
        load_expected_assignment_scope(valid_repo, scope.assignment_id, scope.sha256)
