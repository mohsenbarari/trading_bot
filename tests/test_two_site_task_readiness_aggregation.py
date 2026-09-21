"""Hermetic tests for private readiness-issue contract foundation."""
from __future__ import annotations

from unittest import mock
import pytest

try:
    from scripts.two_site_proposal_runner import (
        ProposalError,
        _TASK_BOUNDARY_CODES,
        _readiness_issue,
        _normalize_readiness_issues,
    )
    import scripts.two_site_proposal_runner as runner_mod
except ImportError:
    from two_site_proposal_runner import (
        ProposalError,
        _TASK_BOUNDARY_CODES,
        _readiness_issue,
        _normalize_readiness_issues,
    )
    import two_site_proposal_runner as runner_mod


def test_readiness_issue_contract_uses_only_fixed_structural_metadata() -> None:
    assert isinstance(_TASK_BOUNDARY_CODES, frozenset)
    assert len(_TASK_BOUNDARY_CODES) >= 14

    for code in _TASK_BOUNDARY_CODES:
        issue_no_path = _readiness_issue(code)
        assert type(issue_no_path) is dict
        assert set(issue_no_path.keys()) == {"code", "detail"}
        assert issue_no_path["code"] == code
        assert isinstance(issue_no_path["detail"], str)
        assert len(issue_no_path["detail"]) > 0

        issue_none_path = _readiness_issue(code, None)
        assert set(issue_none_path.keys()) == {"code", "detail"}
        assert issue_none_path == issue_no_path

        safe_path = "models/trade.py"
        issue_path = _readiness_issue(code, safe_path)
        assert type(issue_path) is dict
        assert set(issue_path.keys()) == {"code", "path", "detail"}
        assert issue_path["code"] == code
        assert issue_path["path"] == safe_path
        assert issue_path["detail"] == issue_no_path["detail"]

    for bad_code in ("UNKNOWN_CODE", "", "TASK_UNKNOWN", 123, None, True):
        with pytest.raises(ProposalError):
            _readiness_issue(bad_code)  # type: ignore[arg-type]

    with pytest.raises(ProposalError):
        _readiness_issue("TASK_FILE_NOT_FOUND", "models/trade.py", extra="forbidden")
    with pytest.raises(ProposalError):
        _readiness_issue("TASK_FILE_NOT_FOUND", "models/trade.py", detail="attacker prose")
    with pytest.raises(ProposalError):
        _readiness_issue("TASK_FILE_NOT_FOUND", "models/trade.py", source="code")
    with pytest.raises(ProposalError):
        _readiness_issue("TASK_FILE_NOT_FOUND", "models/trade.py", traceback="trace")
    with pytest.raises(ProposalError):
        _readiness_issue("TASK_FILE_NOT_FOUND", "models/trade.py", "extra_arg")

    for bad_path in (123, True, False, 0.5, b"models/trade.py", ["models/trade.py"], {"p": "p"}):
        with pytest.raises(ProposalError):
            _readiness_issue("TASK_FILE_NOT_FOUND", bad_path)  # type: ignore[arg-type]

    unsafe_paths = (
        "/etc/passwd",
        "C:/windows/system32",
        "../escape.py",
        "a/../b.py",
        "a/./b.py",
        "",
        "a//b.py",
        "a\\b.py",
        "a\0b.py",
        "a/" * 130 + "long.py",
        "sk-proj-" + "a" * 30,
        "ghp_" + "b" * 30,
        "1234567890:" + "c" * 32,
    )
    for p in unsafe_paths:
        with pytest.raises(ProposalError):
            _readiness_issue("TASK_FILE_NOT_FOUND", p)


def test_readiness_issue_normalization_is_sorted_deduplicated_and_capped() -> None:
    for bad_facts in (None, "facts", 123, True, {"code": "TASK_FILE_NOT_FOUND", "path": None}):
        with pytest.raises(ProposalError):
            _normalize_readiness_issues(bad_facts)  # type: ignore[arg-type]

    bad_items = [
        "not-a-dict",
        123,
        None,
        {"code": "TASK_FILE_NOT_FOUND"},
        {"path": "models/trade.py"},
        {"code": "TASK_FILE_NOT_FOUND", "path": None, "detail": "spoofed"},
        {"code": "TASK_FILE_NOT_FOUND", "path": None, "source": "prompt"},
        {"code": "TASK_FILE_NOT_FOUND", "path": None, "traceback": "err"},
        {"code": "TASK_FILE_NOT_FOUND", "path": None, "extra": 1},
    ]
    for bad_item in bad_items:
        with pytest.raises(ProposalError):
            _normalize_readiness_issues([bad_item])  # type: ignore[list-item]

    raw_facts = [
        {"code": "TASK_FILE_NOT_FOUND", "path": "z_models/b.py"},
        {"code": "TASK_FILE_NOT_FOUND", "path": None},
        {"code": "TASK_FILE_NOT_FOUND", "path": "a_models/a.py"},
        {"code": "TASK_FILE_NOT_FOUND", "path": None},
        {"code": "TASK_FILE_NOT_FOUND", "path": "a_models/a.py"},
        {"code": "ASSIGNMENT_EXPIRED", "path": None},
        {"code": "ASSIGNMENT_EXPIRED", "path": None},
    ]
    normalized = _normalize_readiness_issues(raw_facts)
    expected = [
        {"code": "ASSIGNMENT_EXPIRED", "detail": _readiness_issue("ASSIGNMENT_EXPIRED")["detail"]},
        {"code": "TASK_FILE_NOT_FOUND", "detail": _readiness_issue("TASK_FILE_NOT_FOUND")["detail"]},
        {"code": "TASK_FILE_NOT_FOUND", "path": "a_models/a.py", "detail": _readiness_issue("TASK_FILE_NOT_FOUND", "a_models/a.py")["detail"]},
        {"code": "TASK_FILE_NOT_FOUND", "path": "z_models/b.py", "detail": _readiness_issue("TASK_FILE_NOT_FOUND", "z_models/b.py")["detail"]},
    ]
    assert normalized == expected

    permuted = list(reversed(raw_facts))
    assert _normalize_readiness_issues(permuted) == expected

    many_facts = []
    codes_list = sorted(_TASK_BOUNDARY_CODES)
    for i in range(20):
        code = codes_list[i % len(codes_list)]
        many_facts.append({"code": code, "path": f"module_{i:02d}/sub.py"})

    capped_1 = _normalize_readiness_issues(many_facts)
    assert len(capped_1) == 16

    capped_2 = _normalize_readiness_issues(list(reversed(many_facts)))
    assert capped_2 == capped_1

    facts_input = [{"code": "TASK_FILE_NOT_FOUND", "path": "models/trade.py"}]
    res = _normalize_readiness_issues(facts_input)
    assert res[0]["path"] == "models/trade.py"

    facts_input[0]["path"] = "models/mutated.py"
    facts_input.append({"code": "ASSIGNMENT_EXPIRED", "path": None})
    assert res[0]["path"] == "models/trade.py"
    assert len(res) == 1

    res[0]["path"] = "models/output_mutated.py"
    res2 = _normalize_readiness_issues([{"code": "TASK_FILE_NOT_FOUND", "path": "models/trade.py"}])
    assert res2[0]["path"] == "models/trade.py"
    assert res2[0] is not res[0]


def test_readiness_issue_foundation_preserves_existing_entrypoints() -> None:
    assert not hasattr(runner_mod, "aggregate_task_readiness")
    assert not hasattr(runner_mod, "_aggregate_task_readiness")

    for name in (
        "load_task",
        "validate_task_readiness",
        "check",
        "run",
        "task_preparation_notes",
        "proposal_cli_context",
    ):
        fn = getattr(runner_mod, name)
        assert callable(fn)

    with mock.patch.object(runner_mod, "load_task", side_effect=AssertionError("load_task called")), \
         mock.patch.object(runner_mod, "validate_task_readiness", side_effect=AssertionError("validate_task_readiness called")), \
         mock.patch.object(runner_mod, "check", side_effect=AssertionError("check called")), \
         mock.patch.object(runner_mod, "run", side_effect=AssertionError("run called")), \
         mock.patch.object(runner_mod, "task_preparation_notes", side_effect=AssertionError("task_preparation_notes called")), \
         mock.patch.object(runner_mod, "proposal_cli_context", side_effect=AssertionError("proposal_cli_context called")):

        issue = _readiness_issue("TASK_FILE_NOT_FOUND", "models/trade.py")
        assert issue["code"] == "TASK_FILE_NOT_FOUND"

        norm = _normalize_readiness_issues([{"code": "TASK_FILE_NOT_FOUND", "path": "models/trade.py"}])
        assert len(norm) == 1
