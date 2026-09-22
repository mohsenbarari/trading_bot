import pytest
from scripts.two_site_proposal_runner import (
    ProposalError,
    _collect_model_readiness_facts,
    _normalize_readiness_issues,
    _readiness_issue,
)


class StrSubclass(str):
    pass


def _dummy_cred_path() -> str:
    return "models/" + "gh" + "p_" + ("a" * 25) + ".py"


@pytest.mark.parametrize(
    "dep_ctx,expected",
    [
        (None, []),
        ({}, []),
        ({"persistence_contract_requested": False}, []),
        ({"persistence_contract_requested": True}, []),
        ({"persistence_contract_requested": True, "omitted_model_path_count": 0}, []),
        (
            {"persistence_contract_requested": True, "omitted_model_path_count": 3},
            [{"code": "MODEL_CONTEXT_LIMIT_EXCEEDED", "path": None}],
        ),
        (
            {
                "persistence_contract_requested": True,
                "omitted_model_path_count": 1,
                "missing_full_model_paths": ["models/b.py", "models/a.py"],
            },
            [
                {"code": "MODEL_CONTEXT_LIMIT_EXCEEDED", "path": None},
                {"code": "MODEL_CONTEXT_MISSING", "path": "models/a.py"},
                {"code": "MODEL_CONTEXT_MISSING", "path": "models/b.py"},
            ],
        ),
    ],
)
def test_collect_facts_success(dep_ctx, expected):
    assert _collect_model_readiness_facts(dep_ctx) == expected


@pytest.mark.parametrize("invalid_count", [-1, True, False, "1", 1.5, [1], {"a": 1}])
def test_collect_facts_invalid_count(invalid_count):
    ctx = {
        "persistence_contract_requested": True,
        "omitted_model_path_count": invalid_count,
    }
    assert _collect_model_readiness_facts(ctx) == [
        {"code": "MODEL_CONTEXT_INVALID", "path": None}
    ]


def test_collect_facts_order_dedup_and_purity():
    ctx = {
        "persistence_contract_requested": True,
        "missing_full_model_paths": ["models/b.py", 123, "models/a.py", "models/b.py"],
        "session_context": {"requested": True, "root_symbols": ["api:run"]},
    }
    first = _collect_model_readiness_facts(ctx)
    expected = [
        {"code": "MODEL_CONTEXT_MISSING", "path": "models/a.py"},
        {"code": "MODEL_CONTEXT_MISSING", "path": "models/b.py"},
    ]
    assert first == expected
    first.append({"code": "MUTATED", "path": None})
    second = _collect_model_readiness_facts(ctx)
    assert second == expected
    assert first is not second
    assert "MUTATED" not in [f["code"] for f in second]
    assert "missing_full_model_paths" in ctx


@pytest.mark.parametrize(
    "bad_missing",
    [
        "not_a_list",
        {"models/a.py": True},
        ["../escape.py"],
        ["/abs/path.py"],
        ["models/\0null.py"],
        ["models\\backslash.py"],
        ["models//dot.py"],
        [_dummy_cred_path()],
    ],
)
def test_collect_facts_malformed_container_and_unsafe_paths(bad_missing):
    ctx = {
        "persistence_contract_requested": True,
        "missing_full_model_paths": bad_missing,
    }
    with pytest.raises(ProposalError):
        _collect_model_readiness_facts(ctx)


def test_normalize_readiness_issues_cap16():
    raw = [
        {"code": "MODEL_CONTEXT_MISSING", "path": f"models/m{i:02d}.py"}
        for i in range(20)
    ]
    normalized = _normalize_readiness_issues(raw)
    assert len(normalized) == 16
    assert normalized[0]["code"] == "MODEL_CONTEXT_MISSING"
    assert normalized[0]["path"] == "models/m00.py"
    assert normalized[0]["detail"] == "model context is missing"
    assert normalized[-1]["path"] == "models/m15.py"


@pytest.mark.parametrize(
    "code,path,expected",
    [
        (
            "MODEL_CONTEXT_INVALID",
            None,
            {"code": "MODEL_CONTEXT_INVALID", "detail": "model context is invalid"},
        ),
        (
            "MODEL_CONTEXT_LIMIT_EXCEEDED",
            None,
            {
                "code": "MODEL_CONTEXT_LIMIT_EXCEEDED",
                "detail": "model context limit exceeded",
            },
        ),
        (
            "MODEL_CONTEXT_MISSING",
            "models/user.py",
            {
                "code": "MODEL_CONTEXT_MISSING",
                "path": "models/user.py",
                "detail": "model context is missing",
            },
        ),
    ],
)
def test_readiness_issue_renderer_valid(code, path, expected):
    assert _readiness_issue(code, path) == expected


@pytest.mark.parametrize(
    "bad_code",
    [
        [],
        {},
        123,
        None,
        StrSubclass("MODEL_CONTEXT_INVALID"),
        "UNKNOWN_MODEL_CODE",
    ],
)
def test_readiness_issue_rejects_non_str_and_unknown(bad_code):
    with pytest.raises(ProposalError):
        _readiness_issue(bad_code)


def test_readiness_issue_rejects_extra_metadata_and_unsafe_path():
    with pytest.raises(ProposalError):
        _readiness_issue("MODEL_CONTEXT_INVALID", None, "extra_arg")
    with pytest.raises(ProposalError):
        _readiness_issue("MODEL_CONTEXT_INVALID", None, extra_kw="val")
    with pytest.raises(ProposalError):
        _readiness_issue("MODEL_CONTEXT_MISSING", "../escape.py")
    with pytest.raises(ProposalError):
        _readiness_issue("MODEL_CONTEXT_MISSING", _dummy_cred_path())
