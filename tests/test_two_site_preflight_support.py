from __future__ import annotations

import os
from pathlib import Path
import pytest

from scripts.two_site_assignment_scope import (
    ASSIGNMENT_FACT_CODES,
    AssignmentFact,
    AssignmentScopeError,
)
from scripts.two_site_proposal_runner import (
    load_task,
    ProposalError,
    validate_assignment_scope,
)
from tests.two_site_preflight_support import make_scoped_task, scope_failure


def test_make_scoped_task_loads_successfully(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, task_path, task = make_scoped_task(tmp_path, monkeypatch)

    # Confirm all 4 task lists are nonempty
    assert isinstance(task["context_files"], list) and len(task["context_files"]) > 0
    assert isinstance(task["allowed_files"], list) and len(task["allowed_files"]) > 0
    assert isinstance(task["requirements"], list) and len(task["requirements"]) > 0
    assert isinstance(task["test_selectors"], list) and len(task["test_selectors"]) > 0

    # Confirm inert sources and task.json exist on disk
    assert (root / "a.py").exists()
    assert (root / "b.py").exists()
    assert task_path.exists()

    # Exercises actual production load_task validator
    loaded_task, sources = load_task(root, task_path)
    assert loaded_task["task_id"] == "T1"
    assert loaded_task["goal"] == "ASG-1 safe"
    assert "a.py" in sources
    assert sources["a.py"]["content"] == "# inert a\n"
    assert sources["a.py"]["sha256"] is not None


def test_make_scoped_task_missing_token_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, task_path, _ = make_scoped_task(
        tmp_path, monkeypatch, goal="safe task without assignment token"
    )

    with pytest.raises(ProposalError) as exc_info:
        load_task(root, task_path)

    assert "assignment scope validation failed [TASK_ASSIGNMENT_TOKEN_MISSING]" in str(
        exc_info.value
    )
    cause = exc_info.value.__cause__
    assert isinstance(cause, AssignmentScopeError)
    assert str(cause) == "task.goal does not contain assignment ID as a bounded token"


def test_make_scoped_task_outside_scope_override_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "c.py").write_text("# inert c\n", encoding="utf-8")
    root, task_path, _ = make_scoped_task(
        tmp_path, monkeypatch, allowed_files=["c.py"]
    )

    with pytest.raises(ProposalError) as exc_info:
        load_task(root, task_path)

    assert "assignment scope validation failed [TASK_PATH_OUTSIDE_SCOPE]" in str(
        exc_info.value
    )
    cause = exc_info.value.__cause__
    assert isinstance(cause, AssignmentScopeError)
    assert str(cause) == "Task allowed_file is outside assignment scope"


def test_scope_failure_builds_exact_types_and_frozen_property() -> None:
    assert len(ASSIGNMENT_FACT_CODES) > 0

    for code in ASSIGNMENT_FACT_CODES:
        err = scope_failure(code)
        assert isinstance(err, ProposalError)
        assert str(err) == "fixture scope rejection"
        cause = err.__cause__
        assert isinstance(cause, AssignmentScopeError)
        assert str(cause) == "fixture scope rejection"

        fact = cause.fact
        assert fact is not None
        assert type(fact) is AssignmentFact
        assert fact.code == code
        assert fact.path is None
        assert cause.code == code
        assert cause.fact_code == code
        assert cause.path is None
        assert cause.fact_path is None

        # Assert immutable/frozen fact contract
        with pytest.raises((AttributeError, TypeError)):
            fact.code = "NEW_CODE"  # type: ignore[misc]

    # Test with code=None
    err_none = scope_failure(None)
    assert isinstance(err_none, ProposalError)
    assert str(err_none) == "fixture scope rejection"
    cause_none = err_none.__cause__
    assert isinstance(cause_none, AssignmentScopeError)
    assert str(cause_none) == "fixture scope rejection"
    assert cause_none.fact is None
    assert cause_none.code is None
    assert cause_none.path is None
    assert cause_none.fact_code is None
    assert cause_none.fact_path is None


def test_caller_patches_validator_with_exact_returned_error_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, task_path, task = make_scoped_task(tmp_path, monkeypatch)
    code = next(iter(ASSIGNMENT_FACT_CODES))
    injected_err = scope_failure(code)

    def _failing_validator(r: Path, t: dict) -> None:
        raise injected_err

    monkeypatch.setattr(
        "scripts.two_site_proposal_runner.validate_assignment_scope",
        _failing_validator,
    )

    with pytest.raises(ProposalError) as exc_info:
        load_task(root, task_path)

    assert exc_info.value is injected_err
    assert exc_info.value.__cause__ is injected_err.__cause__
    assert exc_info.value.__cause__.fact is injected_err.__cause__.fact


def test_runtime_proposal_boundary_trapping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _, task = make_scoped_task(tmp_path, monkeypatch)

    def _unexpected_loader(r: Path, aid: str, sha: str) -> None:
        raise RuntimeError("unexpected disk fault during scope lookup")

    monkeypatch.setattr(
        "scripts.two_site_proposal_runner.load_expected_assignment_scope",
        _unexpected_loader,
    )

    with pytest.raises(ProposalError) as exc_info:
        validate_assignment_scope(root, task)

    assert str(exc_info.value) == "assignment scope validation failed"
    assert isinstance(exc_info.value.__cause__, RuntimeError)
    assert str(exc_info.value.__cause__) == "unexpected disk fault during scope lookup"


def test_monkeypatch_context_teardown_restores(tmp_path: Path) -> None:
    scope_vars = (
        "TWO_SITE_ASSIGNMENT_SCOPE_REQUIRED",
        "TWO_SITE_ASSIGNMENT_ID",
        "TWO_SITE_ASSIGNMENT_SHA256",
    )
    saved_env = {var: os.environ.get(var) for var in scope_vars}
    import scripts.two_site_proposal_runner as runner

    orig_loader = runner.load_expected_assignment_scope

    with pytest.MonkeyPatch.context() as mp:
        make_scoped_task(tmp_path, mp)
        for var in scope_vars:
            assert var in os.environ
        assert runner.load_expected_assignment_scope is not orig_loader

    for var, val in saved_env.items():
        assert os.environ.get(var) == val
    assert runner.load_expected_assignment_scope is orig_loader


def test_separate_fixture_instances_share_no_mutable_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dir1 = tmp_path / "run1"
    dir2 = tmp_path / "run2"
    r1, p1, t1 = make_scoped_task(dir1, monkeypatch)
    r2, p2, t2 = make_scoped_task(dir2, monkeypatch)

    assert r1 != r2
    assert p1 != p2
    assert t1 is not t2
    assert t1["context_files"] is not t2["context_files"]
    assert t1["allowed_files"] is not t2["allowed_files"]
    assert t1["requirements"] is not t2["requirements"]
    assert t1["test_selectors"] is not t2["test_selectors"]

    t1["context_files"].append("mutated.py")
    assert "mutated.py" not in t2["context_files"]

    t1["goal"] = "mutated goal"
    assert t2["goal"] == "ASG-1 safe"
