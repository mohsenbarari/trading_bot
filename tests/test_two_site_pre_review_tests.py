"""Tests for scripts.two_site_pre_review_tests."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import call, patch

import pytest

from scripts.two_site_pre_review_tests import (
    SandboxError,
    normalize_test_selectors,
    parse_test_spec,
    tracked_test_inventory,
)


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        ("tests/test_a.py", ("tests/test_a.py",)),
        ("tests/unit/test_b.py::TestClass::test_fn", ("tests/unit/test_b.py::TestClass::test_fn",)),
        ("tests.test_a", ("tests/test_a.py",)),
        ("tests.sub.test_b", ("tests/sub/test_b.py",)),
        ("pytest -q -v tests/test_a.py", ("tests/test_a.py",)),
        ("python3 -m pytest -qq tests/test_a.py tests/test_b.py", ("tests/test_a.py", "tests/test_b.py")),
        ("python3 -m unittest -v tests.test_a", ("tests/test_a.py",)),
        ("python3 -m unittest -vv tests/test_a.py", ("tests/test_a.py",)),
        ("APP_ENV_FILE=config/unit-test.env.example pytest tests/test_a.py", ("tests/test_a.py",)),
        ("PYTHONPATH=. PYTHONDONTWRITEBYTECODE=1 pytest tests/test_a.py", ("tests/test_a.py",)),
        ("tests/test_*.py", ("tests/test_*.py",)),
        ("pytest tests/sub/test_*_extra*.py", ("tests/sub/test_*_extra*.py",)),
    ],
)
def test_parse_test_spec_valid(spec: str, expected: tuple[str, ...]) -> None:
    assert parse_test_spec(spec) == expected


@pytest.mark.parametrize(
    "spec",
    [
        "tests/test_a.py; rm -rf /", "tests/test_a.py &", "tests/test_a.py | cat",
        "tests/test_`id`.py", "tests/test_$FOO.py", "tests/test_a.py\n", "tests/test_a.py\r",
        "tests/test_{a,b}.py", "tests/test_a.py > out", "< tests/test_a.py", "(pytest tests/test_a.py)",
        "!tests/test_a.py", "~tests/test_a.py", "tests\\test_a.py",
        "python3 tests/test_a.py", "python -m pytest tests/test_a.py", "pytest -s tests/test_a.py",
        "python3 -m unittest -q tests.test_a", "pytest --tb=short tests/test_a.py", "-v tests/test_a.py",
        "UNKNOWN=1 pytest tests/test_a.py", "pytest PYTHONPATH=. tests/test_a.py",
        "pytest 'tests/test_a.py", 'pytest "tests/test_a.py',
        "tests/../tests/test_a.py", "tests//test_a.py", "./tests/test_a.py",
        "other/test_a.py", "test_a.py", "tests/foo.py", "tests/test_a.txt",
        "tests/test_a.py::", "tests/test_a.py::bad-node", "tests/test_a.py::123bad",
        "tests/test_*.py::Node", "tests.test_a.TestCase",
        "", "   ", "PYTHONPATH=.", "pytest -q",
    ],
)
def test_parse_test_spec_invalid(spec: str) -> None:
    assert parse_test_spec(spec) is None


@pytest.mark.parametrize("value", [None, 123, ["tests/test_a.py"], {"spec": "tests/test_a.py"}])
def test_parse_test_spec_non_string(value: object) -> None:
    with pytest.raises(SandboxError, match="spec must be a str"):
        parse_test_spec(value)  # type: ignore[arg-type]


def test_normalize_test_selectors_resolution() -> None:
    inv1 = ["tests/test_b.py", "tests/test_a.py", "scripts/ignore.py"]
    inv2 = ["tests/test_a.py", "tests/test_b.py"]
    r1, _ = normalize_test_selectors(["tests/test_*.py"], inv1)
    r2, _ = normalize_test_selectors(["tests/test_*.py"], inv2)
    assert r1 == r2 == ["tests/test_a.py", "tests/test_b.py"]

    specs = ["tests/test_b.py::TestClass::test_fn", "tests/test_*.py", "tests/test_b.py::TestClass::test_fn"]
    sel, unsupp = normalize_test_selectors(specs, inv1)
    assert sel == ["tests/test_b.py::TestClass::test_fn", "tests/test_a.py", "tests/test_b.py"]
    assert unsupp == []

    deep_inv = ["tests/test_root.py", "tests/sub/test_sub.py"]
    sel_scope, _ = normalize_test_selectors(["tests/test_*.py"], deep_inv)
    assert sel_scope == ["tests/test_root.py"]

    invalid_specs = [
        "pytest tests/test_a.py tests/test_missing.py",
        "tests/test_nomatch*.py",
        "invalid;spec",
        "tests/test_nomatch*.py",
    ]
    sel_part, unsupp_part = normalize_test_selectors(["tests/test_a.py", *invalid_specs], inv1)
    assert sel_part == ["tests/test_a.py"]
    assert unsupp_part == [
        "pytest tests/test_a.py tests/test_missing.py",
        "tests/test_nomatch*.py",
        "invalid;spec",
    ]


@pytest.mark.parametrize(
    ("specs", "paths", "match"),
    [
        ([], ["tests/test_a.py"], "specs must be nonempty and within bounded size"),
        ([f"tests/test_{i}.py" for i in range(33)], ["tests/test_a.py"], "specs must be nonempty and within bounded size"),
        (["tests/test_a.py"], [f"tests/test_{i}.py" for i in range(10001)], "available_paths exceeds bounded size"),
        ("not-a-list", ["tests/test_a.py"], "specs and available_paths must be lists or tuples"),
        (["tests/test_a.py"], 123, "specs and available_paths must be lists or tuples"),
        ([123], ["tests/test_a.py"], "specs and available_paths entries must be strings"),
        (["tests/test_a.py"], [None], "specs and available_paths entries must be strings"),
        (["tests/test_missing.py"], ["tests/test_a.py"], "no concrete selector remains"),
    ],
)
def test_normalize_test_selectors_bounds_and_errors(specs: object, paths: object, match: str) -> None:
    with pytest.raises(SandboxError, match=match):
        normalize_test_selectors(specs, paths)  # type: ignore[arg-type]


def test_tracked_test_inventory_mocked() -> None:
    mock_files = ["tests/test_b.py", "scripts/foo.py", "tests/not_test.py", "tests/test_a.py"]
    with patch("scripts.two_site_pre_review_tests.source_files", return_value=mock_files) as mock_src, \
         patch("scripts.two_site_pre_review_tests._validate_overlay_path") as mock_val:
        result = tracked_test_inventory(
            Path("/mock/root"),
            overlay_paths=["tests/test_new.py", "scripts/bar.py"],
        )
        assert result == ["tests/test_a.py", "tests/test_b.py", "tests/test_new.py"]
        mock_src.assert_called_once_with(Path("/mock/root"), [])
        mock_val.assert_has_calls([call("tests/test_new.py"), call("scripts/bar.py")])

    with patch("scripts.two_site_pre_review_tests._validate_overlay_path", side_effect=SandboxError("bad overlay")):
        with pytest.raises(SandboxError, match="bad overlay"):
            tracked_test_inventory("/mock/root", overlay_paths=["tests/test_bad.py"])


@pytest.mark.parametrize(
    ("root", "overlays", "match"),
    [
        ("", (), "root must be a nonempty path"),
        ("   ", (), "root must be a nonempty path"),
        (123, (), "root must be a str or Path"),
        (Path("/mock"), "not-list", "overlay_paths must be a list or tuple"),
        (Path("/mock"), [f"tests/test_{i}.py" for i in range(33)], "overlay_paths exceeds bounded size"),
        (Path("/mock"), [123], "overlay path must be a nonempty string"),
        (Path("/mock"), [""], "overlay path must be a nonempty string"),
        (Path("/mock"), ["   "], "overlay path must be a nonempty string"),
    ],
)
def test_tracked_test_inventory_errors(root: object, overlays: object, match: str) -> None:
    with pytest.raises(SandboxError, match=match):
        tracked_test_inventory(root, overlays)  # type: ignore[arg-type]


def test_current_repo_inventory_smoke() -> None:
    root = Path(__file__).resolve().parents[1]
    inventory = tracked_test_inventory(root)
    assert isinstance(inventory, list)
    assert "tests/test_two_site_agent_sandbox_overlay.py" in inventory
    synthetic_inventory = inventory + [f"tests/test_synth_{i}.py" for i in range(1300)]
    assert len(synthetic_inventory) > 1224
    selectors, unsupported = normalize_test_selectors(
        ["tests/test_two_site_agent_sandbox_overlay.py", "tests/test_synth_*.py"],
        synthetic_inventory,
    )
    assert len(selectors) == 1301
    assert unsupported == []
