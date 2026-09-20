"""Hermetic unit tests for the typed AssignmentFact contract and fact-aware AssignmentScopeError."""

import dataclasses
import pytest

from scripts.two_site_assignment_scope import (
    ASSIGNMENT_CONTRACT_INVALID,
    ASSIGNMENT_EXPIRED,
    ASSIGNMENT_FACT_CODES,
    TASK_ASSIGNMENT_TOKEN_MISSING,
    TASK_PATH_OUTSIDE_SCOPE,
    AssignmentFact,
    AssignmentScopeError,
)


def test_assignment_fact_valid_instances() -> None:
    """Valid AssignmentFact instances can be constructed with or without path."""
    for code in ASSIGNMENT_FACT_CODES:
        fact_no_path = AssignmentFact(code)
        assert fact_no_path.code == code
        assert fact_no_path.path is None

        fact_with_none = AssignmentFact(code, None)
        assert fact_with_none.code == code
        assert fact_with_none.path is None
        assert fact_no_path == fact_with_none
        assert hash(fact_no_path) == hash(fact_with_none)

    fact_with_path = AssignmentFact(
        TASK_PATH_OUTSIDE_SCOPE, "scripts/two_site_assignment_scope.py"
    )
    assert fact_with_path.code == TASK_PATH_OUTSIDE_SCOPE
    assert fact_with_path.path == "scripts/two_site_assignment_scope.py"

    fact_nested = AssignmentFact(
        ASSIGNMENT_CONTRACT_INVALID, "docs/refactor/two-server/execution-order.yaml"
    )
    assert fact_nested.code == ASSIGNMENT_CONTRACT_INVALID
    assert fact_nested.path == "docs/refactor/two-server/execution-order.yaml"

    assert fact_with_path != fact_nested
    assert len({fact_with_path, fact_nested, fact_no_path}) == 3


def test_assignment_fact_immutability() -> None:
    """AssignmentFact is frozen and rejects mutation of attributes."""
    fact = AssignmentFact(ASSIGNMENT_EXPIRED, "scripts/two_site_assignment_scope.py")

    with pytest.raises(dataclasses.FrozenInstanceError):
        fact.code = TASK_PATH_OUTSIDE_SCOPE  # type: ignore[misc]

    with pytest.raises(dataclasses.FrozenInstanceError):
        fact.path = None  # type: ignore[misc]

    with pytest.raises(dataclasses.FrozenInstanceError):
        fact.extra_attr = "injected"  # type: ignore[misc]

    with pytest.raises(dataclasses.FrozenInstanceError):
        del fact.code  # type: ignore[misc]

    with pytest.raises(dataclasses.FrozenInstanceError):
        del fact.path  # type: ignore[misc]


def test_assignment_fact_rejects_subclasses() -> None:
    """AssignmentFact cannot be subclassed or instantiated via subclass."""
    class SubFact(AssignmentFact):
        pass

    with pytest.raises(TypeError, match="cannot be subclassed"):
        SubFact(ASSIGNMENT_EXPIRED)


def test_assignment_fact_code_validation() -> None:
    """AssignmentFact rejects invalid, non-string, or unknown codes."""
    with pytest.raises(ValueError, match="Unknown assignment fact code"):
        AssignmentFact("UNKNOWN_CODE")

    with pytest.raises(ValueError, match="Unknown assignment fact code"):
        AssignmentFact("TASK_PATH_OUTSIDE_SCOPE_EXTRA")

    with pytest.raises(TypeError, match="exact string"):
        AssignmentFact(123)  # type: ignore[arg-type]

    with pytest.raises(TypeError, match="exact string"):
        AssignmentFact(None)  # type: ignore[arg-type]

    with pytest.raises(TypeError, match="exact string"):
        AssignmentFact([TASK_PATH_OUTSIDE_SCOPE])  # type: ignore[arg-type]

    class CustomStr(str):
        pass

    with pytest.raises(TypeError, match="exact string"):
        AssignmentFact(CustomStr(ASSIGNMENT_EXPIRED))


def test_assignment_fact_path_validation_invalid_types_and_bounds() -> None:
    """AssignmentFact rejects non-string types, empty, whitespace, and oversized paths."""
    with pytest.raises(TypeError, match="exact string or None"):
        AssignmentFact(TASK_PATH_OUTSIDE_SCOPE, 123)  # type: ignore[arg-type]

    with pytest.raises(TypeError, match="exact string or None"):
        AssignmentFact(TASK_PATH_OUTSIDE_SCOPE, ["path"])  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="cannot be empty"):
        AssignmentFact(TASK_PATH_OUTSIDE_SCOPE, "")

    with pytest.raises(ValueError, match="whitespace"):
        AssignmentFact(TASK_PATH_OUTSIDE_SCOPE, " scripts/scope.py")

    with pytest.raises(ValueError, match="whitespace"):
        AssignmentFact(TASK_PATH_OUTSIDE_SCOPE, "scripts/scope.py ")

    with pytest.raises(ValueError, match="whitespace"):
        AssignmentFact(TASK_PATH_OUTSIDE_SCOPE, "   ")

    oversized = "a/" + ("b" * 1024)
    with pytest.raises(ValueError, match="length limit"):
        AssignmentFact(TASK_PATH_OUTSIDE_SCOPE, oversized)


def test_assignment_fact_path_validation_forbidden_characters() -> None:
    """AssignmentFact rejects control chars, NUL, backslashes, wildcards, and credentials."""
    with pytest.raises(ValueError, match="Control characters or NUL"):
        AssignmentFact(TASK_PATH_OUTSIDE_SCOPE, "scripts/\x00scope.py")

    with pytest.raises(ValueError, match="Control characters or NUL"):
        AssignmentFact(TASK_PATH_OUTSIDE_SCOPE, "scripts/\nscope.py")

    with pytest.raises(ValueError, match="Control characters or NUL"):
        AssignmentFact(TASK_PATH_OUTSIDE_SCOPE, "scripts/\rscope.py")

    with pytest.raises(ValueError, match="Control characters or NUL"):
        AssignmentFact(TASK_PATH_OUTSIDE_SCOPE, "scripts/\tscope.py")

    with pytest.raises(ValueError, match="Backslashes"):
        AssignmentFact(TASK_PATH_OUTSIDE_SCOPE, "scripts\\two_site.py")

    with pytest.raises(ValueError, match="Wildcard"):
        AssignmentFact(TASK_PATH_OUTSIDE_SCOPE, "scripts/*.py")

    with pytest.raises(ValueError, match="Wildcard"):
        AssignmentFact(TASK_PATH_OUTSIDE_SCOPE, "scripts/[a-z].py")

    with pytest.raises(ValueError, match="Wildcard"):
        AssignmentFact(TASK_PATH_OUTSIDE_SCOPE, "scripts/?.py")

    with pytest.raises(ValueError, match="Credential-like"):
        AssignmentFact(TASK_PATH_OUTSIDE_SCOPE, "https://user:pass@host/path")

    with pytest.raises(ValueError, match="Credential-like"):
        AssignmentFact(TASK_PATH_OUTSIDE_SCOPE, "auth/token.json")

    with pytest.raises(ValueError, match="Credential-like"):
        AssignmentFact(TASK_PATH_OUTSIDE_SCOPE, "keys/api_key.txt")

    with pytest.raises(ValueError, match="Credential-like"):
        AssignmentFact(TASK_PATH_OUTSIDE_SCOPE, "config/password.yaml")


def test_assignment_fact_path_validation_segments_and_structure() -> None:
    """AssignmentFact rejects absolute, drive, dot, traversal, empty, .git, and leading-dash segments."""
    with pytest.raises(ValueError, match="Absolute paths"):
        AssignmentFact(TASK_PATH_OUTSIDE_SCOPE, "/scripts/scope.py")

    with pytest.raises(ValueError, match="Drive prefixes"):
        AssignmentFact(TASK_PATH_OUTSIDE_SCOPE, "C:/scripts/scope.py")

    with pytest.raises(ValueError, match="Drive prefixes"):
        AssignmentFact(TASK_PATH_OUTSIDE_SCOPE, "D:scripts/scope.py")

    with pytest.raises(ValueError, match="Empty path segment"):
        AssignmentFact(TASK_PATH_OUTSIDE_SCOPE, "scripts//scope.py")

    with pytest.raises(ValueError, match="Empty path segment"):
        AssignmentFact(TASK_PATH_OUTSIDE_SCOPE, "scripts/scope.py/")

    with pytest.raises(ValueError, match="Dot '\\.' segment"):
        AssignmentFact(TASK_PATH_OUTSIDE_SCOPE, ".")

    with pytest.raises(ValueError, match="Dot '\\.' segment"):
        AssignmentFact(TASK_PATH_OUTSIDE_SCOPE, "./scripts/scope.py")

    with pytest.raises(ValueError, match="Dot '\\.' segment"):
        AssignmentFact(TASK_PATH_OUTSIDE_SCOPE, "scripts/./scope.py")

    with pytest.raises(ValueError, match="Dot-dot '\\.\\.' traversal"):
        AssignmentFact(TASK_PATH_OUTSIDE_SCOPE, "..")

    with pytest.raises(ValueError, match="Dot-dot '\\.\\.' traversal"):
        AssignmentFact(TASK_PATH_OUTSIDE_SCOPE, "../scope.py")

    with pytest.raises(ValueError, match="Dot-dot '\\.\\.' traversal"):
        AssignmentFact(TASK_PATH_OUTSIDE_SCOPE, "scripts/../scope.py")

    with pytest.raises(ValueError, match="'\\.git' segment"):
        AssignmentFact(TASK_PATH_OUTSIDE_SCOPE, ".git/config")

    with pytest.raises(ValueError, match="'\\.git' segment"):
        AssignmentFact(TASK_PATH_OUTSIDE_SCOPE, "repo/.git/HEAD")

    with pytest.raises(ValueError, match="Leading dash"):
        AssignmentFact(TASK_PATH_OUTSIDE_SCOPE, "-leading/scope.py")

    with pytest.raises(ValueError, match="Leading dash"):
        AssignmentFact(TASK_PATH_OUTSIDE_SCOPE, "scripts/-scope.py")


def test_assignment_scope_error_legacy_behavior() -> None:
    """AssignmentScopeError preserves legacy class hierarchy, message and args behavior."""
    exc = AssignmentScopeError("Original legacy failure message")
    assert isinstance(exc, Exception)
    assert str(exc) == "Original legacy failure message"
    assert exc.args == ("Original legacy failure message",)
    assert exc.fact is None
    assert exc.code is None
    assert exc.path is None
    assert exc.fact_code is None
    assert exc.fact_path is None

    exc_empty = AssignmentScopeError()
    assert str(exc_empty) == ""
    assert exc_empty.fact is None


def test_assignment_scope_error_with_valid_fact() -> None:
    """AssignmentScopeError accepts a valid AssignmentFact via keyword or positional argument."""
    fact = AssignmentFact(TASK_ASSIGNMENT_TOKEN_MISSING)
    exc_kw = AssignmentScopeError("Token missing in goal", fact=fact)
    assert str(exc_kw) == "Token missing in goal"
    assert exc_kw.args == ("Token missing in goal",)
    assert exc_kw.fact is fact
    assert exc_kw.code == TASK_ASSIGNMENT_TOKEN_MISSING
    assert exc_kw.path is None
    assert exc_kw.fact_code == TASK_ASSIGNMENT_TOKEN_MISSING
    assert exc_kw.fact_path is None

    fact_path = AssignmentFact(
        TASK_PATH_OUTSIDE_SCOPE, "scripts/two_site_assignment_scope.py"
    )
    exc_pos = AssignmentScopeError("Path outside scope", fact_path)
    assert str(exc_pos) == "Path outside scope"
    assert exc_pos.fact is fact_path
    assert exc_pos.code == TASK_PATH_OUTSIDE_SCOPE
    assert exc_pos.path == "scripts/two_site_assignment_scope.py"
    assert exc_pos.fact_code == TASK_PATH_OUTSIDE_SCOPE
    assert exc_pos.fact_path == "scripts/two_site_assignment_scope.py"


def test_assignment_scope_error_rejects_arbitrary_payloads() -> None:
    """AssignmentScopeError rejects dicts, strings, subclasses, and arbitrary objects as facts."""
    with pytest.raises(TypeError, match="exact AssignmentFact instance"):
        AssignmentScopeError("error", fact={"code": ASSIGNMENT_EXPIRED})  # type: ignore[arg-type]

    with pytest.raises(TypeError, match="exact AssignmentFact instance"):
        AssignmentScopeError("error", fact=ASSIGNMENT_EXPIRED)  # type: ignore[arg-type]

    with pytest.raises(TypeError, match="exact AssignmentFact instance"):
        AssignmentScopeError("error", fact=object())  # type: ignore[arg-type]

    with pytest.raises(TypeError, match="exact AssignmentFact instance"):
        AssignmentScopeError("error", fact=123)  # type: ignore[arg-type]

    class FakeFact:
        code = ASSIGNMENT_EXPIRED
        path = None

    with pytest.raises(TypeError, match="exact AssignmentFact instance"):
        AssignmentScopeError("error", fact=FakeFact())  # type: ignore[arg-type]
