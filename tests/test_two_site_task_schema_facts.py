from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

import pytest

from scripts.two_site_proposal_runner import (
    _READINESS_ISSUE_DETAILS,
    _TaskBoundaryError,
    check,
    load_task,
)
import scripts.two_site_proposal_runner as runner_module
from tests.two_site_preflight_support import make_scoped_task


_CRED_SENTINEL = "".join(["s", "k", "-", "p", "r", "o", "j", "-"] + ["X"] * 25)
_LIST_FIELDS = ("context_files", "allowed_files", "requirements", "test_selectors")

_REJECTION_CASES: list[tuple[str, Callable[[dict[str, Any]], Any], str]] = [
    ("non_dict", lambda t: [1, 2, 3], "TASK_SCHEMA_INVALID"),
    ("missing_key", lambda t: {k: v for k, v in t.items() if k != "task_id"}, "TASK_SCHEMA_INVALID"),
    ("extra_key", lambda t: {**t, "unexpected_extra": 1}, "TASK_SCHEMA_INVALID"),
    ("invalid_id_empty", lambda t: {**t, "task_id": ""}, "TASK_SCHEMA_INVALID"),
    ("invalid_id_chars", lambda t: {**t, "task_id": "invalid id!"}, "TASK_SCHEMA_INVALID"),
    ("invalid_id_nonstring", lambda t: {**t, "task_id": 12345}, "TASK_SCHEMA_INVALID"),
    ("goal_empty", lambda t: {**t, "goal": ""}, "TASK_SCHEMA_INVALID"),
    ("goal_whitespace", lambda t: {**t, "goal": "   \t\n"}, "TASK_SCHEMA_INVALID"),
    ("goal_nonstring", lambda t: {**t, "goal": 123}, "TASK_SCHEMA_INVALID"),
    ("goal_none", lambda t: {**t, "goal": None}, "TASK_SCHEMA_INVALID"),
]

for _field in _LIST_FIELDS:
    _REJECTION_CASES.extend([
        (f"{_field}_empty", lambda t, f=_field: {**t, f: []}, "TASK_SCHEMA_INVALID"),
        (f"{_field}_wrong_type", lambda t, f=_field: {**t, f: "not_a_list"}, "TASK_SCHEMA_INVALID"),
        (f"{_field}_duplicate", lambda t, f=_field: {**t, f: ["item1", "item1"]}, "TASK_SCHEMA_INVALID"),
        (f"{_field}_nonstring", lambda t, f=_field: {**t, f: [123]}, "TASK_SCHEMA_INVALID"),
        (f"{_field}_blank_item", lambda t, f=_field: {**t, f: ["   "]}, "TASK_SCHEMA_INVALID"),
        (f"{_field}_gt_32", lambda t, f=_field: {**t, f: [f"item_{i}" for i in range(33)]}, "TASK_SCHEMA_INVALID"),
    ])

_REJECTION_CASES.append((
    "credential_sentinel",
    lambda t: {**t, "goal": f"Task with {_CRED_SENTINEL}"},
    "TASK_CREDENTIAL_DETECTED",
))


@pytest.mark.parametrize(
    ("case_name", "mutator", "expected_code"),
    _REJECTION_CASES,
    ids=[c[0] for c in _REJECTION_CASES],
)
def test_task_schema_and_credential_guards_rejected(
    case_name: str,
    mutator: Callable[[dict[str, Any]], Any],
    expected_code: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, task_path, task = make_scoped_task(tmp_path, monkeypatch)

    mutated = mutator(task)
    task_path.write_text(json.dumps(mutated), encoding="utf-8")

    def trap_later_phase(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("Later phase entered for rejected task snapshot")

    monkeypatch.setattr(
        "scripts.two_site_proposal_runner.validate_assignment_scope",
        trap_later_phase,
    )
    monkeypatch.setattr(
        "scripts.two_site_proposal_runner.task_preparation_notes",
        trap_later_phase,
    )
    monkeypatch.setattr(
        "scripts.two_site_proposal_runner.enrich_repository_dependency_context",
        trap_later_phase,
    )
    monkeypatch.setattr(
        "scripts.two_site_proposal_runner.run",
        trap_later_phase,
    )

    real_bounded_file_bytes = runner_module.bounded_file_bytes
    recorded_reads: list[str] = []

    def reader_callspy(
        root_arg: Path,
        path_arg: Path,
        limit_arg: int,
        label_arg: str,
        failure_callback: Any = None,
    ) -> bytes:
        p = Path(path_arg)
        if p.name != "task.json":
            pytest.fail(f"Reader callspy rejected non-task read: {path_arg} ({label_arg})")
        recorded_reads.append(p.name)
        return real_bounded_file_bytes(
            root_arg, path_arg, limit_arg, label_arg, failure_callback=failure_callback
        )

    monkeypatch.setattr(
        "scripts.two_site_proposal_runner.bounded_file_bytes",
        reader_callspy,
    )

    # 1. Exercise load_task separately
    recorded_reads.clear()
    with pytest.raises(_TaskBoundaryError) as exc_info:
        load_task(root, task_path)
    err = exc_info.value
    assert err.code == expected_code
    assert err.path is None
    if expected_code == "TASK_CREDENTIAL_DETECTED":
        assert "credential-like" in str(err)
    assert recorded_reads == ["task.json"]

    # 2. Exercise check separately resetting recorded reads
    recorded_reads.clear()
    payload = check(root, task_path)
    assert payload == {
        "state": "TASK_BLOCKED",
        "issues": [
            {
                "code": expected_code,
                "detail": _READINESS_ISSUE_DETAILS[expected_code],
            }
        ],
    }
    assert recorded_reads == ["task.json"]


def test_valid_control(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, task_path, _ = make_scoped_task(tmp_path, monkeypatch)

    loaded_task, sources = load_task(root, task_path)
    assert loaded_task["task_id"] == "T1"
    assert "a.py" in sources
    assert sources["a.py"]["content"] == "# inert a\n"

    payload = check(root, task_path)
    assert payload["state"] == "TASK_READY"
    assert payload["task_id"] == "T1"
    assert payload["source_file_count"] == 1
    assert "preparation_notes" in payload


def test_preflight_task_representative_empty_requirements(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, task_path, task = make_scoped_task(tmp_path, monkeypatch)
    task["requirements"] = []
    task_path.write_text(json.dumps(task), encoding="utf-8")

    from scripts.two_site_task_preflight import preflight_task

    result = preflight_task(root, task_path)
    assert result == {
        "schema_version": 1,
        "state": "TASK_BLOCKED",
        "issues": [
            {
                "code": "TASK_SCHEMA_INVALID",
                "detail": _READINESS_ISSUE_DETAILS["TASK_SCHEMA_INVALID"],
            }
        ],
    }
    assert "task_id" not in result
