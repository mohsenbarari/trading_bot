from __future__ import annotations

import copy
from unittest import mock

import pytest
from scripts.two_site_proposal_runner import ProposalError, validate_task_readiness
from scripts.two_site_readiness_facts import _collect_session_readiness_facts


class StrSubclass(str):
    pass


class DictSubclass(dict):
    pass


class ListSubclass(list):
    pass


def _valid_session_context(**overrides) -> dict:
    ctx = {
        "requested": True,
        "adapter_symbols": ["app.adapters:SessionAdapter"],
        "root_symbols": ["app.entrypoint:main"],
        "omitted_required_symbol_count": 0,
        "omitted_missing_symbol_count": 0,
        "omitted_query_operation_count": 0,
        "omitted_unresolved_symbol_count": 0,
        "unresolved_symbols": [],
        "missing_symbols": [],
        "query_operations": [{"operation": "select"}],
    }
    ctx.update(overrides)
    return ctx


@pytest.mark.parametrize(
    "dep_ctx",
    [
        None,
        {},
        {"session_context": None},
        {"session_context": {"requested": False}},
        {"session_context": {"requested": None}},
        {"session_context": {"requested": 0}},
        {"session_context": {"requested": ""}},
        {"session_context": _valid_session_context()},
    ],
)
def test_collect_session_facts_pass_differential(dep_ctx):
    facts = _collect_session_readiness_facts(dep_ctx)
    assert facts == []

    task_notes = {"dependency_context": dep_ctx}
    validated = validate_task_readiness(task_notes)
    assert validated is task_notes


@pytest.mark.parametrize(
    "bad_dep",
    [
        "invalid",
        123,
        [1, 2],
        DictSubclass({"session_context": None}),
    ],
)
def test_collect_session_facts_rejects_non_exact_dict_dep(bad_dep):
    with pytest.raises(ProposalError, match="invalid dependency context"):
        _collect_session_readiness_facts(bad_dep)


@pytest.mark.parametrize(
    "bad_session",
    [
        "invalid",
        123,
        [1, 2],
        DictSubclass({"requested": True}),
    ],
)
def test_collect_session_facts_rejects_non_exact_dict_session(bad_session):
    ctx = {"session_context": bad_session}
    with pytest.raises(ProposalError, match="invalid session context"):
        _collect_session_readiness_facts(ctx)


@pytest.mark.parametrize(
    "bad_list",
    [
        None,
        "not_a_list",
        123,
        {"a": 1},
        ListSubclass(["sym"]),
    ],
)
def test_collect_session_facts_rejects_malformed_symbol_lists(bad_list):
    ctx_unresolved = {
        "session_context": _valid_session_context(unresolved_symbols=bad_list)
    }
    with pytest.raises(ProposalError, match="unresolved_symbols must be a list"):
        _collect_session_readiness_facts(ctx_unresolved)

    ctx_missing = {
        "session_context": _valid_session_context(missing_symbols=bad_list)
    }
    with pytest.raises(ProposalError, match="missing_symbols must be a list"):
        _collect_session_readiness_facts(ctx_missing)


@pytest.mark.parametrize(
    "field,code,legacy_error_match",
    [
        ("adapter_symbols", "SESSION_ADAPTER_MISSING", "adapter"),
        ("root_symbols", "SESSION_ENTRYPOINT_MISSING", "entrypoint"),
        ("query_operations", "SESSION_QUERY_CONTRACT_MISSING", "query"),
    ],
)
@pytest.mark.parametrize("falsey_val", [None, [], "", False, 0])
def test_collect_session_facts_truthiness_fields(field, code, legacy_error_match, falsey_val):
    session = _valid_session_context(**{field: falsey_val})
    dep = {"session_context": session}

    facts = _collect_session_readiness_facts(dep)
    assert facts == [{"code": code, "path": None}]

    with pytest.raises(ProposalError, match=legacy_error_match):
        validate_task_readiness({"dependency_context": dep})


COUNT_FIELDS = [
    "omitted_required_symbol_count",
    "omitted_missing_symbol_count",
    "omitted_query_operation_count",
    "omitted_unresolved_symbol_count",
]


@pytest.mark.parametrize("field", COUNT_FIELDS)
@pytest.mark.parametrize("invalid_val", [-1, -10, True, False, "0", 1.5, [1], {"a": 1}])
def test_collect_session_facts_invalid_count_differential(field, invalid_val):
    session = _valid_session_context(**{field: invalid_val})
    dep = {"session_context": session}

    facts = _collect_session_readiness_facts(dep)
    assert facts == [{"code": "SESSION_CONTEXT_INVALID", "path": None}]

    with pytest.raises(ProposalError, match="invalid live session dependency counts"):
        validate_task_readiness({"dependency_context": dep})


@pytest.mark.parametrize("field", COUNT_FIELDS)
@pytest.mark.parametrize("positive_val", [1, 5, 100])
def test_collect_session_facts_limit_exceeded_differential(field, positive_val):
    session = _valid_session_context(**{field: positive_val})
    dep = {"session_context": session}

    facts = _collect_session_readiness_facts(dep)
    assert facts == [{"code": "SESSION_CONTEXT_LIMIT_EXCEEDED", "path": None}]

    with pytest.raises(ProposalError, match="exceeds bounded context"):
        validate_task_readiness({"dependency_context": dep})


@pytest.mark.parametrize(
    "list_field",
    ["unresolved_symbols", "missing_symbols"],
)
@pytest.mark.parametrize(
    "sample_val",
    [
        "",
        "api:session_entry",
        StrSubclass("api:subclass_entry"),
    ],
)
def test_collect_session_facts_strings_in_symbol_lists_differential(list_field, sample_val):
    session = _valid_session_context(**{list_field: [sample_val]})
    dep = {"session_context": session}

    facts = _collect_session_readiness_facts(dep)
    assert facts == [{"code": "SESSION_CONTEXT_MISSING", "path": None}]

    with pytest.raises(ProposalError, match="missing live session context"):
        validate_task_readiness({"dependency_context": dep})


@pytest.mark.parametrize(
    "list_field",
    ["unresolved_symbols", "missing_symbols"],
)
def test_collect_session_facts_non_strings_ignored(list_field):
    session = _valid_session_context(**{list_field: [123, None, 45.6, object()]})
    dep = {"session_context": session}

    facts = _collect_session_readiness_facts(dep)
    assert facts == []

    validated = validate_task_readiness({"dependency_context": dep})
    assert validated == {"dependency_context": dep}


def test_collect_session_facts_all_defects_and_sorting():
    session_all_invalid = {
        "requested": True,
        "adapter_symbols": [],
        "root_symbols": None,
        "omitted_required_symbol_count": -1,
        "unresolved_symbols": ["unres_sym"],
        "missing_symbols": [StrSubclass("miss_sym")],
        "query_operations": [],
    }
    dep = {"session_context": session_all_invalid}

    facts = _collect_session_readiness_facts(dep)
    expected = [
        {"code": "SESSION_ADAPTER_MISSING", "path": None},
        {"code": "SESSION_CONTEXT_INVALID", "path": None},
        {"code": "SESSION_CONTEXT_MISSING", "path": None},
        {"code": "SESSION_ENTRYPOINT_MISSING", "path": None},
        {"code": "SESSION_QUERY_CONTRACT_MISSING", "path": None},
    ]
    assert facts == expected

    session_limit = copy.deepcopy(session_all_invalid)
    session_limit["omitted_required_symbol_count"] = 2
    facts_limit = _collect_session_readiness_facts({"session_context": session_limit})
    expected_limit = [
        {"code": "SESSION_ADAPTER_MISSING", "path": None},
        {"code": "SESSION_CONTEXT_LIMIT_EXCEEDED", "path": None},
        {"code": "SESSION_CONTEXT_MISSING", "path": None},
        {"code": "SESSION_ENTRYPOINT_MISSING", "path": None},
        {"code": "SESSION_QUERY_CONTRACT_MISSING", "path": None},
    ]
    assert facts_limit == expected_limit


def test_collect_session_facts_purity_immutability_and_traps():
    dep = {
        "session_context": _valid_session_context(
            adapter_symbols=[],
            unresolved_symbols=["sym_b", 123, "sym_a", "sym_b"],
        )
    }
    dep_before = copy.deepcopy(dep)
    expected = [
        {"code": "SESSION_ADAPTER_MISSING", "path": None},
        {"code": "SESSION_CONTEXT_MISSING", "path": None},
    ]

    def _trap(*args, **kwargs):
        raise AssertionError("collector must not perform I/O or network")

    with (
        mock.patch("builtins.open", side_effect=_trap),
        mock.patch("subprocess.Popen", side_effect=_trap),
        mock.patch("socket.socket", side_effect=_trap),
    ):
        first = _collect_session_readiness_facts(dep)

    assert first == expected
    assert dep == dep_before

    first.append({"code": "MUTATED", "path": None})
    first[0]["code"] = "MUTATED_RECORD"

    with (
        mock.patch("builtins.open", side_effect=_trap),
        mock.patch("subprocess.Popen", side_effect=_trap),
        mock.patch("socket.socket", side_effect=_trap),
    ):
        second = _collect_session_readiness_facts(dep)

    assert second == expected
    assert first is not second
    assert all(r1 is not r2 for r1 in first for r2 in second)
    assert "MUTATED" not in [f["code"] for f in second]
    assert "MUTATED_RECORD" not in [f["code"] for f in second]
    assert dep == dep_before


def test_model_fields_do_not_interfere():
    dep = {
        "persistence_contract_requested": True,
        "omitted_model_path_count": -1,
        "missing_full_model_paths": ["models/a.py"],
        "session_context": _valid_session_context(),
    }
    facts = _collect_session_readiness_facts(dep)
    assert facts == []

    for fact in facts:
        assert set(fact.keys()) == {"code", "path"}
        assert fact["path"] is None

    dep["session_context"]["adapter_symbols"] = []
    facts_with_issue = _collect_session_readiness_facts(dep)
    assert facts_with_issue == [{"code": "SESSION_ADAPTER_MISSING", "path": None}]
    assert set(facts_with_issue[0].keys()) == {"code", "path"}
    assert facts_with_issue[0]["path"] is None
