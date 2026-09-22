from __future__ import annotations

import json
import os
from pathlib import Path
import pytest

try:
    from scripts.two_site_assignment_scope import (
        AssignmentFact,
        AssignmentScopeError,
    )
    from scripts.two_site_proposal_runner import (
        ProposalError,
        _TaskBoundaryError,
        _load_task_snapshot,
        load_task,
    )
    import scripts.two_site_proposal_runner as runner
except ImportError:
    from two_site_assignment_scope import (
        AssignmentFact,
        AssignmentScopeError,
    )
    from two_site_proposal_runner import (
        ProposalError,
        _TaskBoundaryError,
        _load_task_snapshot,
        load_task,
    )
    import two_site_proposal_runner as runner

try:
    from tests.two_site_preflight_support import (
        make_scoped_task,
        scope_failure,
    )
except ImportError:
    from two_site_preflight_support import (
        make_scoped_task,
        scope_failure,
    )


def test_valid_snapshot_equality(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, task_path, _ = make_scoped_task(tmp_path, monkeypatch)

    task_strict, sources_strict = load_task(root, task_path)
    task_snap, sources_snap = _load_task_snapshot(root, task_path)
    facts: list[dict[str, str | None]] = []
    task_opt, sources_opt = _load_task_snapshot(root, task_path, assignment_facts=facts)

    assert task_strict == task_snap == task_opt
    assert sources_strict == sources_snap == sources_opt
    assert "a.py" in sources_strict
    assert len(facts) == 0


def test_missing_token_strict_block_and_opt_in(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, task_path, _ = make_scoped_task(tmp_path, monkeypatch, goal="no token here")

    with pytest.raises(ProposalError) as exc_info_strict:
        load_task(root, task_path)
    assert "TASK_ASSIGNMENT_TOKEN_MISSING" in str(exc_info_strict.value)

    with pytest.raises(ProposalError) as exc_info_default:
        _load_task_snapshot(root, task_path)
    assert "TASK_ASSIGNMENT_TOKEN_MISSING" in str(exc_info_default.value)

    facts: list[dict[str, str | None]] = []
    task_loaded, sources_loaded = _load_task_snapshot(root, task_path, assignment_facts=facts)
    assert facts == [{"code": "TASK_ASSIGNMENT_TOKEN_MISSING", "path": None}]
    assert "a.py" in sources_loaded
    assert sources_loaded["a.py"]["content"] == "# inert a\n"


def test_invalid_assignment_facts_sink_zero_io(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, task_path, _ = make_scoped_task(tmp_path, monkeypatch)

    def reader_trap(*args: object, **kwargs: object) -> bytes:
        raise AssertionError("file IO executed despite invalid assignment_facts sink")

    monkeypatch.setattr("scripts.two_site_proposal_runner.bounded_file_bytes", reader_trap)

    invalid_sinks = [
        ["non-empty"],
        [{"code": "OTHER", "path": None}],
        "not-a-list",
        123,
        {"facts": []},
        (1, 2),
    ]
    for bad_sink in invalid_sinks:
        with pytest.raises(ProposalError) as exc_info:
            _load_task_snapshot(root, task_path, assignment_facts=bad_sink)  # type: ignore[arg-type]
        assert "assignment_facts must be None or an empty list" in str(exc_info.value)


def test_nontoken_factless_and_runtime_errors_same_identity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, task_path, _ = make_scoped_task(tmp_path, monkeypatch)

    # 1. Non-token scope error
    non_token_err = scope_failure("TASK_PATH_OUTSIDE_SCOPE")
    monkeypatch.setattr(
        "scripts.two_site_proposal_runner.validate_assignment_scope",
        lambda *args, **kwargs: (_ for _ in ()).throw(non_token_err),
    )
    with pytest.raises(ProposalError) as exc1:
        _load_task_snapshot(root, task_path, assignment_facts=[])
    assert exc1.value is non_token_err

    # 2. Factless scope error
    factless_err = scope_failure(None)
    monkeypatch.setattr(
        "scripts.two_site_proposal_runner.validate_assignment_scope",
        lambda *args, **kwargs: (_ for _ in ()).throw(factless_err),
    )
    with pytest.raises(ProposalError) as exc2:
        _load_task_snapshot(root, task_path, assignment_facts=[])
    assert exc2.value is factless_err

    # 3. Runtime error
    runtime_err = ProposalError("arbitrary scope validation runtime failure")
    monkeypatch.setattr(
        "scripts.two_site_proposal_runner.validate_assignment_scope",
        lambda *args, **kwargs: (_ for _ in ()).throw(runtime_err),
    )
    with pytest.raises(ProposalError) as exc3:
        _load_task_snapshot(root, task_path, assignment_facts=[])
    assert exc3.value is runtime_err


def test_actual_outside_scope_blocked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, task_path, task = make_scoped_task(
        tmp_path,
        monkeypatch,
        context_files=["a.py"],
        allowed_files=["outside.py"],
    )
    (root / "outside.py").write_text("# inert\n", encoding="utf-8")
    task_path.write_text(json.dumps(task), encoding="utf-8")

    with pytest.raises(ProposalError) as exc_strict:
        load_task(root, task_path)
    assert "TASK_PATH_OUTSIDE_SCOPE" in str(exc_strict.value)

    facts: list[dict[str, str | None]] = []
    with pytest.raises(ProposalError) as exc_opt:
        _load_task_snapshot(root, task_path, assignment_facts=facts)
    assert "TASK_PATH_OUTSIDE_SCOPE" in str(exc_opt.value)
    assert len(facts) == 0


def test_hardlink_stops_before_subsequent_read_with_provider_trap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, task_path, task = make_scoped_task(
        tmp_path,
        monkeypatch,
        goal="no token here",
        context_files=["a.py", "b.py"],
        allowed_files=["a.py", "b.py"],
    )
    task_path.write_text(json.dumps(task), encoding="utf-8")

    # Create a hardlink for a.py making st_nlink == 2
    os.link(root / "a.py", root / "a_link.py")

    read_paths: list[str] = []
    real_bounded = runner.bounded_file_bytes

    def callspy(r: Path, path: Path, limit: int, label: str, failure_callback=None) -> bytes:
        read_paths.append(Path(path).name)
        return real_bounded(r, path, limit, label, failure_callback=failure_callback)

    monkeypatch.setattr("scripts.two_site_proposal_runner.bounded_file_bytes", callspy)

    def provider_trap(*args: object, **kwargs: object) -> object:
        raise AssertionError("provider trap: provider invocation attempted")

    monkeypatch.setattr("subprocess.Popen", provider_trap)

    facts: list[dict[str, str | None]] = []
    with pytest.raises(ProposalError) as exc_info:
        _load_task_snapshot(root, task_path, assignment_facts=facts)

    assert "TASK_FILE_HARDLINKED" in str(exc_info.value)
    # Missing assignment token was collected before reader failure halted loader
    assert facts == [{"code": "TASK_ASSIGNMENT_TOKEN_MISSING", "path": None}]
    # Hardlink on a.py halted loader before b.py was ever read
    assert "a.py" in read_paths
    assert "b.py" not in read_paths
