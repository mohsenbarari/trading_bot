from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from scripts.two_site_assignment_scope import (
    AssignmentFact,
    AssignmentScope,
    AssignmentScopeError,
)
from scripts.two_site_proposal_runner import ProposalError


def make_scoped_task(
    tmp_path: Path | str,
    monkeypatch: Any,
    **overrides: Any,
) -> tuple[Path, Path, dict[str, Any]]:
    """Create an isolated hermetic repo and task environment with real assignment scope."""
    root = Path(tmp_path).resolve()
    root.mkdir(parents=True, exist_ok=True)

    # Isolated temp repo, inert source files
    (root / "a.py").write_text("# inert a\n", encoding="utf-8")
    (root / "b.py").write_text("# inert b\n", encoding="utf-8")

    # Valid 6-key task specification with nonempty lists
    task: dict[str, Any] = {
        "task_id": "T1",
        "goal": "ASG-1 safe",
        "context_files": ["a.py"],
        "allowed_files": ["a.py"],
        "requirements": ["verify"],
        "test_selectors": ["tests/test_a.py"],
    }
    if overrides:
        task.update(overrides)

    task_path = root / "task.json"
    task_path.write_text(json.dumps(task), encoding="utf-8")

    # Real AssignmentScope without guessed metadata
    scope = AssignmentScope(
        "ASG-1",
        "P1-00",
        ("a.py", "b.py"),
        (),
        "2099-01-01T00:00:00Z",
        "a" * 64,
    )

    # Explicitly isolate and set 3 scope env vars using supplied pytest monkeypatch
    monkeypatch.setenv("TWO_SITE_ASSIGNMENT_SCOPE_REQUIRED", "1")
    monkeypatch.setenv("TWO_SITE_ASSIGNMENT_ID", "ASG-1")
    monkeypatch.setenv("TWO_SITE_ASSIGNMENT_SHA256", "a" * 64)

    # Mock ONLY runner.load_expected_assignment_scope; validate_task_scope remains real
    monkeypatch.setattr(
        "scripts.two_site_proposal_runner.load_expected_assignment_scope",
        lambda root_arg, aid_arg, sha_arg: scope,
    )

    return root, task_path, copy.deepcopy(task)


def scope_failure(code: str | None = None) -> ProposalError:
    """Build a ProposalError wrapping an authentic typed AssignmentScopeError."""
    fact = AssignmentFact(code) if code is not None else None
    cause = AssignmentScopeError("fixture scope rejection", fact=fact)
    err = ProposalError("fixture scope rejection")
    err.__cause__ = cause
    return err
