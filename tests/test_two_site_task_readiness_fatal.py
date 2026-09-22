"""Hermetic unit tests for check() fatal safe-read readiness handling."""
from __future__ import annotations

import json
import os
from pathlib import Path
import unittest.mock as mock

import pytest

from scripts.two_site_proposal_runner import (
    _TaskBoundaryError,
    check,
    enrich_repository_dependency_context,
    load_task,
    task_preparation_notes,
    validate_task_readiness,
)

ASSIGNMENT_AUTOPILOT_VARS = (
    "TWO_SITE_ASSIGNMENT_SCOPE_REQUIRED",
    "TWO_SITE_ASSIGNMENT_ID",
    "TWO_SITE_ASSIGNMENT_SHA256",
    "TWO_SITE_AUTOPILOT_RUN_ID",
)


@pytest.fixture(autouse=True)
def isolate_assignment_autopilot_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ASSIGNMENT_AUTOPILOT_VARS:
        monkeypatch.delenv(var, raising=False)


def _write_valid_task_files(root: Path) -> tuple[Path, dict]:
    root.mkdir(parents=True, exist_ok=True)
    task = {
        "task_id": "TASK-VALID-01",
        "goal": "Hermetic valid task goal",
        "context_files": ["context.py"],
        "allowed_files": ["context.py"],
        "requirements": ["req 1"],
        "test_selectors": ["tests/test_two_site_task_readiness_fatal.py"],
    }
    task_file = root / "valid_task.json"
    task_file.write_text(json.dumps(task))
    (root / "context.py").write_text("# valid python context\nx = 1\n")
    return task_file, task


def test_check_fatal_traversal_short_circuits(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    real_task_file, _ = _write_valid_task_files(root)
    symlink_task = root / "task_symlink.json"
    symlink_task.symlink_to(real_task_file)

    with mock.patch(
        "scripts.two_site_proposal_runner.enrich_repository_dependency_context",
        wraps=enrich_repository_dependency_context,
    ) as mock_enrich, mock.patch(
        "scripts.two_site_proposal_runner.validate_task_readiness",
        wraps=validate_task_readiness,
    ) as mock_val, mock.patch(
        "scripts.two_site_proposal_runner.task_preparation_notes",
        wraps=task_preparation_notes,
    ) as mock_prep:
        result = check(root, symlink_task)

    assert result == {
        "state": "TASK_BLOCKED",
        "issues": [
            {
                "code": "TASK_FILE_SYMLINK",
                "path": "task_symlink.json",
                "detail": "is a symlink or path escape",
            }
        ],
    }
    mock_prep.assert_not_called()
    mock_enrich.assert_not_called()
    mock_val.assert_not_called()

    with pytest.raises(_TaskBoundaryError) as exc_info:
        load_task(root, symlink_task)
    assert exc_info.value.code == "TASK_FILE_SYMLINK"
    assert exc_info.value.path == "task_symlink.json"


def test_check_fatal_hardlink_short_circuits(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    real_task_file, _ = _write_valid_task_files(root)
    hardlink_task = root / "task_hardlink.json"
    os.link(real_task_file, hardlink_task)

    with mock.patch(
        "scripts.two_site_proposal_runner.enrich_repository_dependency_context",
        wraps=enrich_repository_dependency_context,
    ) as mock_enrich, mock.patch(
        "scripts.two_site_proposal_runner.validate_task_readiness",
        wraps=validate_task_readiness,
    ) as mock_val, mock.patch(
        "scripts.two_site_proposal_runner.task_preparation_notes",
        wraps=task_preparation_notes,
    ) as mock_prep:
        result = check(root, hardlink_task)

    assert result == {
        "state": "TASK_BLOCKED",
        "issues": [
            {
                "code": "TASK_FILE_HARDLINKED",
                "path": "task_hardlink.json",
                "detail": "is hardlinked and cannot be trusted",
            }
        ],
    }
    mock_prep.assert_not_called()
    mock_enrich.assert_not_called()
    mock_val.assert_not_called()

    with pytest.raises(_TaskBoundaryError) as exc_info:
        load_task(root, hardlink_task)
    assert exc_info.value.code == "TASK_FILE_HARDLINKED"
    assert exc_info.value.path == "task_hardlink.json"


def test_check_fatal_malformed_json_short_circuits(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir(parents=True, exist_ok=True)
    bad_task = root / "malformed_task.json"
    bad_task.write_text("{invalid json syntax")

    with mock.patch(
        "scripts.two_site_proposal_runner.enrich_repository_dependency_context",
        wraps=enrich_repository_dependency_context,
    ) as mock_enrich, mock.patch(
        "scripts.two_site_proposal_runner.validate_task_readiness",
        wraps=validate_task_readiness,
    ) as mock_val, mock.patch(
        "scripts.two_site_proposal_runner.task_preparation_notes",
        wraps=task_preparation_notes,
    ) as mock_prep:
        result = check(root, bad_task)

    assert result == {
        "state": "TASK_BLOCKED",
        "issues": [
            {
                "code": "TASK_SCHEMA_INVALID",
                "detail": "task schema is invalid",
            }
        ],
    }
    mock_prep.assert_not_called()
    mock_enrich.assert_not_called()
    mock_val.assert_not_called()

    with pytest.raises(_TaskBoundaryError) as exc_info:
        load_task(root, bad_task)
    assert exc_info.value.code == "TASK_SCHEMA_INVALID"
    assert exc_info.value.path is None


def test_check_fatal_task_budget_short_circuits(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir(parents=True, exist_ok=True)
    budget_task = root / "budget_task.json"
    budget_task.write_bytes(b" " * 160_001)

    with mock.patch(
        "scripts.two_site_proposal_runner.enrich_repository_dependency_context",
        wraps=enrich_repository_dependency_context,
    ) as mock_enrich, mock.patch(
        "scripts.two_site_proposal_runner.validate_task_readiness",
        wraps=validate_task_readiness,
    ) as mock_val, mock.patch(
        "scripts.two_site_proposal_runner.task_preparation_notes",
        wraps=task_preparation_notes,
    ) as mock_prep:
        result = check(root, budget_task)

    assert result == {
        "state": "TASK_BLOCKED",
        "issues": [
            {
                "code": "TASK_BUDGET_EXCEEDED",
                "path": "budget_task.json",
                "detail": "exceeds the safe context budget",
            }
        ],
    }
    mock_prep.assert_not_called()
    mock_enrich.assert_not_called()
    mock_val.assert_not_called()

    with pytest.raises(_TaskBoundaryError) as exc_info:
        load_task(root, budget_task)
    assert exc_info.value.code == "TASK_BUDGET_EXCEEDED"
    assert exc_info.value.path == "budget_task.json"


def test_check_valid_task_success(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    task_file, _ = _write_valid_task_files(root)

    with mock.patch(
        "scripts.two_site_proposal_runner.enrich_repository_dependency_context",
        wraps=enrich_repository_dependency_context,
    ) as mock_enrich, mock.patch(
        "scripts.two_site_proposal_runner.validate_task_readiness",
        wraps=validate_task_readiness,
    ) as mock_val, mock.patch(
        "scripts.two_site_proposal_runner.task_preparation_notes",
        wraps=task_preparation_notes,
    ) as mock_prep:
        result = check(root, task_file)

    assert result["state"] == "TASK_READY"
    assert result["task_id"] == "TASK-VALID-01"
    assert result["source_file_count"] == 1
    assert isinstance(result["byte_count"], int)
    assert result["byte_count"] > 0
    assert "preparation_notes" in result
    mock_prep.assert_called_once()
    mock_enrich.assert_called_once()
    mock_val.assert_called_once()

    task, sources = load_task(root, task_file)
    assert task["task_id"] == "TASK-VALID-01"
    assert "context.py" in sources


def test_isolated_assignment_autopilot_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ASSIGNMENT_AUTOPILOT_VARS:
        assert os.environ.get(var) is None
    monkeypatch.setenv("TWO_SITE_ASSIGNMENT_ID", "cycle-9999")
    assert os.environ.get("TWO_SITE_ASSIGNMENT_ID") == "cycle-9999"
