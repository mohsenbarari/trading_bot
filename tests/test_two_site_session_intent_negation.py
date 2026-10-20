"""Offline Session-intent regressions; no application, Provider or database calls."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import two_site_proposal_runner as runner


# This is the exact mandatory prohibition in the issued 763B Assignment, not a
# rewritten Task which evades the original readiness failure.
ISSUED_SCOPE_PROHIBITION = (
    "Use local standard Mock/AsyncMock/SimpleNamespace and one small ExitStack "
    "fixture, not copied handler logic, a custom Session or another "
    "characterization TestCase fixture."
)
NEGATIVE_INTENT = [
    ISSUED_SCOPE_PROHIBITION,
    "No custom Session.",
    "Call the real handler without a custom Session.",
    "Do not use a custom Session.",
    "Don't use a custom Session.",
    "Not a custom Session.",
    "No fake Session or Session adapter.",
    "Without using a stub_session.",
]
POSITIVE_OR_UNCERTAIN_INTENT = [
    "Use a custom Session.",
    "No custom Session. Use a custom Session.",
    "Do not use a custom Session, but provide a session adapter.",
    "Without a fake Session, use a custom Session.",
    "Not only a custom Session.",
    "Do not only use a custom Session.",
    "Not without a custom Session.",
    "No custom Session unless needed.",
    "No custom Session?",
    "No copied handler logic, use a custom Session.",
    "No custom Session, an existing Session adapter is required.",
    "No custom Session, build one.",
    "No custom Session, use it.",
    "No custom Session is prohibited.",
    "No custom Session is forbidden.",
    "No custom Session is banned.",
    "No custom Session is disallowed.",
    "No custom Session is excluded.",
    "No custom Session is restricted.",
    "No custom Session or another characterization TestCase fixture?",
    "No custom Session prohibition.",
    "No custom Session restriction.",
    "No custom Session, disregard this prohibition.",
    "No custom Session" + " " * 161 + " is required.",
    "No restriction on a custom Session.",
    "Do not exclude a custom Session.",
    "Do not forbid a custom Session.",
    "Not a ban on a custom Session.",
    "No restriction: use a custom Session.",
    "Not custom Session; use a session adapter.",
    "No reuse; a Session.",
]


def _fixture(tmp_path: Path, intent: str) -> tuple[Path, dict, dict]:
    root = tmp_path / "repo"
    handler = root / "api/live.py"
    handler.parent.mkdir(parents=True)
    source = "async def live(db):\n    return await db.execute('select value')\n"
    handler.write_text(source, encoding="utf-8")
    task = {
        "task_id": "session-intent-negation",
        "goal": "Call the real handler api.live.live. " + intent,
        "context_files": ["api/live.py"],
        "allowed_files": ["tests/test_generated.py"],
        "requirements": ["Prove exception identity before downstream work."],
        "test_selectors": ["tests/test_generated.py::Case::test_generated"],
    }
    return root, task, {"api/live.py": {"content": source}}


def _validate(context: dict) -> None:
    notes = {"dependency_context": {"session_context": context}}
    assert runner.validate_task_readiness(notes) is notes


@pytest.mark.parametrize("intent", NEGATIVE_INTENT)
def test_explicit_prohibition_does_not_request_or_traverse_session_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, intent: str,
):
    root, task, sources = _fixture(tmp_path, intent)

    def unexpected_resolution(*args, **kwargs):
        raise AssertionError("a prohibited adapter must not start closure traversal")

    monkeypatch.setattr(runner, "_resolve_project_symbol", unexpected_resolution)
    context = runner._build_live_session_context(root, task, sources)
    assert context["requested"] is False
    assert context["activation_reason"] == "NOT_REQUESTED"
    assert context["adapter_symbols"] == context["root_symbols"] == []
    assert context["required_symbol_count"] == 0
    assert context["missing_symbols"] == context["unresolved_symbols"] == []
    assert context["query_operations"] == []
    _validate(context)


@pytest.mark.parametrize("intent", POSITIVE_OR_UNCERTAIN_INTENT)
def test_positive_mixed_and_uncertain_requests_keep_adapter_readiness_gate(
    tmp_path: Path, intent: str,
):
    root, task, _ = _fixture(tmp_path, intent)
    # The source exists for genuine AST resolution, but is deliberately absent
    # from supplied context. Neither an adapter nor its closure may be invented.
    context = runner._build_live_session_context(root, task, {})
    assert context["requested"] is True
    assert context["activation_reason"] == "LEGACY_HEURISTIC"
    assert context["root_symbols"] == ["api/live.py:live"]
    assert context["missing_symbols"] == ["api/live.py:live"]
    with pytest.raises(runner.ProposalError, match="missing live session adapter context"):
        _validate(context)


def test_explicit_declaration_takes_precedence_over_negative_prose(tmp_path: Path):
    root, task, sources = _fixture(tmp_path, ISSUED_SCOPE_PROHIBITION)
    task["requirements"].append("session_context: exact entrypoint api/live.py:live;")
    context = runner._build_live_session_context(root, task, sources)
    assert context["requested"] is True
    assert context["activation_reason"] == "EXPLICIT_DECLARATION"
    assert context["root_symbols"] == ["api/live.py:live"]
    with pytest.raises(runner.ProposalError, match="missing live session adapter context"):
        _validate(context)


def test_supplied_adapter_still_requires_complete_live_closure_despite_prohibition(tmp_path: Path):
    root, task, _ = _fixture(tmp_path, ISSUED_SCOPE_PROHIBITION)
    sources = {"tests/helper.py": {"content": (
        "class HybridSession:\n"
        "    async def execute(self, statement):\n"
        "        raise AssertionError(statement)\n"
    )}}
    context = runner._build_live_session_context(root, task, sources)
    assert context["requested"] is True
    assert context["activation_reason"] == "LEGACY_HEURISTIC"
    assert context["adapter_symbols"] == ["tests/helper.py:HybridSession"]
    assert context["missing_symbols"] == ["api/live.py:live"]
    with pytest.raises(runner.ProposalError, match="missing live session context: api/live.py:live"):
        _validate(context)


def test_malformed_explicit_declaration_is_not_waived_by_negative_prose(tmp_path: Path):
    root, task, sources = _fixture(tmp_path, ISSUED_SCOPE_PROHIBITION)
    task["requirements"].append("session_context: exact entrypoint invalid;")
    with pytest.raises(runner.ProposalError, match="session_context"):
        runner._build_live_session_context(root, task, sources)


def test_original_scope_prohibition_passes_real_check_without_adapter_or_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    root, task, _ = _fixture(tmp_path, ISSUED_SCOPE_PROHIBITION)
    task_path = root / "task.json"
    task_path.write_text(json.dumps(task), encoding="utf-8")
    for name in ("TWO_SITE_ASSIGNMENT_SCOPE_REQUIRED", "TWO_SITE_ASSIGNMENT_ID", "TWO_SITE_ASSIGNMENT_SHA256"):
        monkeypatch.delenv(name, raising=False)

    def unexpected_provider(*args, **kwargs):
        raise AssertionError("offline readiness must not enter a Provider boundary")

    monkeypatch.setattr(runner, "proposal_cli_context", unexpected_provider)
    result = runner.check(root, task_path)
    assert result["state"] == "TASK_READY"
    # Serialized notes intentionally omit a context which was not requested.
    assert "session_context" not in result["preparation_notes"]["dependency_context"]


def test_mixed_request_still_blocks_real_check_before_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    root, task, _ = _fixture(
        tmp_path, "Do not use a custom Session, but provide a session adapter.",
    )
    task_path = root / "task.json"
    task_path.write_text(json.dumps(task), encoding="utf-8")
    for name in ("TWO_SITE_ASSIGNMENT_SCOPE_REQUIRED", "TWO_SITE_ASSIGNMENT_ID", "TWO_SITE_ASSIGNMENT_SHA256"):
        monkeypatch.delenv(name, raising=False)

    def unexpected_provider(*args, **kwargs):
        raise AssertionError("incomplete adapter readiness must not enter a Provider boundary")

    monkeypatch.setattr(runner, "proposal_cli_context", unexpected_provider)
    with pytest.raises(runner.ProposalError, match="missing live session adapter context"):
        runner.check(root, task_path)
