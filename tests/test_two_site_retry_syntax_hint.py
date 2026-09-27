"""Hermetic tests for pure syntax hint extraction and validation."""

import pytest

from scripts.two_site_retry_syntax_hint import (
    extract_syntax_hint,
    validate_retry_hint,
)

ALLOWED = ("tests/test_a.py", "tests/test_b.py")


def test_extract_syntax_hint_success() -> None:
    msg = "generated Python syntax is invalid: tests/test_a.py:10:5: invalid syntax"
    res = extract_syntax_hint(msg, ALLOWED)
    assert res == {
        "code": "PYTHON_SYNTAX_ERROR",
        "path": "tests/test_a.py",
        "line": 10,
        "column": 5,
    }


def test_extract_syntax_hint_negatives() -> None:
    assert extract_syntax_hint("bad prefix", ALLOWED) is None
    assert extract_syntax_hint("", ALLOWED) is None
    assert (
        extract_syntax_hint(
            "generated Python syntax is invalid: tests/other.py:1:1: invalid syntax",
            ALLOWED,
        )
        is None
    )
    assert (
        extract_syntax_hint(
            "generated Python syntax is invalid: /tests/test_a.py:1:1: invalid syntax",
            ALLOWED,
        )
        is None
    )
    assert (
        extract_syntax_hint(
            "generated Python syntax is invalid: tests/../test_a.py:1:1: invalid syntax",
            ALLOWED,
        )
        is None
    )
    assert (
        extract_syntax_hint(
            "generated Python syntax is invalid: tests/test_a.py:0:1: invalid syntax",
            ALLOWED,
        )
        is None
    )
    assert (
        extract_syntax_hint(
            "generated Python syntax is invalid: tests/test_a.py:100001:1: invalid syntax",
            ALLOWED,
        )
        is None
    )
    assert (
        extract_syntax_hint(
            "generated Python syntax is invalid: tests/test_a.py:1:0: invalid syntax",
            ALLOWED,
        )
        is None
    )
    assert (
        extract_syntax_hint(
            "generated Python syntax is invalid: tests/test_a.py:1:10001: invalid syntax",
            ALLOWED,
        )
        is None
    )
    assert (
        extract_syntax_hint(
            "generated Python syntax is invalid: tests/test_a.py:1:1: ",
            ALLOWED,
        )
        is None
    )
    assert (
        extract_syntax_hint(
            "generated Python syntax is invalid: tests/test_a.py:1:1:invalid syntax",
            ALLOWED,
        )
        is None
    )
    assert (
        extract_syntax_hint(
            "generated Python syntax is invalid: tests/test_a.py:1:1: invalid syntax\n",
            ALLOWED,
        )
        is None
    )
    oversize = "generated Python syntax is invalid: tests/test_a.py:1:1: " + ("x" * 600)
    assert extract_syntax_hint(oversize, ALLOWED) is None


def test_validate_retry_hint_success() -> None:
    valid = {
        "code": "PYTHON_SYNTAX_ERROR",
        "path": "tests/test_a.py",
        "line": 42,
        "column": 12,
    }
    canonical = validate_retry_hint(valid, ALLOWED)
    assert canonical == valid


def test_validate_retry_hint_negatives() -> None:
    with pytest.raises(ValueError):
        validate_retry_hint("not-a-dict", ALLOWED)
    with pytest.raises(ValueError):
        validate_retry_hint({}, ALLOWED)
    with pytest.raises(ValueError):
        validate_retry_hint(
            {
                "code": "PYTHON_SYNTAX_ERROR",
                "path": "tests/test_a.py",
                "line": 1,
                "column": 1,
                "extra": 1,
            },
            ALLOWED,
        )
    with pytest.raises(ValueError):
        validate_retry_hint(
            {
                "code": "PYTHON_SYNTAX_ERROR",
                "path": "tests/test_a.py",
                "line": True,
                "column": 1,
            },
            ALLOWED,
        )
    with pytest.raises(ValueError):
        validate_retry_hint(
            {
                "code": "PYTHON_SYNTAX_ERROR",
                "path": "tests/test_a.py",
                "line": 1,
                "column": False,
            },
            ALLOWED,
        )
    with pytest.raises(ValueError):
        validate_retry_hint(
            {
                "code": "WRONG_CODE",
                "path": "tests/test_a.py",
                "line": 1,
                "column": 1,
            },
            ALLOWED,
        )
    with pytest.raises(ValueError):
        validate_retry_hint(
            {
                "code": "PYTHON_SYNTAX_ERROR",
                "path": "tests/other.py",
                "line": 1,
                "column": 1,
            },
            ALLOWED,
        )
    with pytest.raises(ValueError):
        validate_retry_hint(
            {
                "code": "PYTHON_SYNTAX_ERROR",
                "path": "/tests/test_a.py",
                "line": 1,
                "column": 1,
            },
            ALLOWED,
        )
    with pytest.raises(ValueError):
        validate_retry_hint(
            {
                "code": "PYTHON_SYNTAX_ERROR",
                "path": "tests/../test_a.py",
                "line": 1,
                "column": 1,
            },
            ALLOWED,
        )
    with pytest.raises(ValueError):
        validate_retry_hint(
            {
                "code": "PYTHON_SYNTAX_ERROR",
                "path": "tests/test_a.py",
                "line": 0,
                "column": 1,
            },
            ALLOWED,
        )
    with pytest.raises(ValueError):
        validate_retry_hint(
            {
                "code": "PYTHON_SYNTAX_ERROR",
                "path": "tests/test_a.py",
                "line": 1,
                "column": 10001,
            },
            ALLOWED,
        )
