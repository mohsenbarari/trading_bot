"""Tests for scripts.two_site_pre_review_receipt."""
from __future__ import annotations

from pathlib import Path
import re
from unittest.mock import patch
import pytest

from scripts.two_site_agent_sandbox import SandboxError
from scripts.two_site_pre_review_receipt import _bound_utf8_tail, run_candidate_tests

VALID_SHA = "0" * 64
VALID_TASK = {"test_selectors": ["tests/test_a.py"]}
VALID_WORKER = {"changes": [{"path": "tests/test_a.py", "content": "def test_ok(): pass\n"}]}
VALID_SANDBOX_RESULT = {
    "returncode": 0,
    "stdout": "ok",
    "stderr": "",
    "snapshot_files": 1,
    "snapshot_bytes": 10,
    "isolation": "bwrap",
}


@pytest.fixture
def mock_deps():
    with (
        patch("scripts.two_site_pre_review_receipt.tracked_test_inventory", return_value=["tests/test_a.py"]) as m_inv,
        patch("scripts.two_site_pre_review_receipt.normalize_test_selectors", side_effect=lambda s, i: ([s[0]], [])) as m_norm,
        patch("scripts.two_site_pre_review_receipt.two_site_agent_sandbox.run_tests", return_value=dict(VALID_SANDBOX_RESULT)) as m_run,
    ):
        yield m_inv, m_norm, m_run


@pytest.mark.parametrize(("rc", "expected_state"), [(0, "PASSED"), (2, "FAILED")])
def test_receipt_pass_failed_forwarding_and_no_leak(
    tmp_path: Path, mock_deps, rc: int, expected_state: str
) -> None:
    _, _, m_run = mock_deps
    m_run.return_value = {**VALID_SANDBOX_RESULT, "returncode": rc}
    receipt = run_candidate_tests(tmp_path, VALID_TASK, VALID_WORKER, VALID_SHA, timeout=600)
    assert receipt["state"] == expected_state
    assert receipt["returncode"] == rc
    assert receipt["proposal_sha256"] == VALID_SHA
    assert receipt["selectors"] == ["tests/test_a.py"]
    assert receipt["unsupported_specs"] == []
    assert set(receipt) == {
        "state", "proposal_sha256", "selectors", "unsupported_specs", "returncode",
        "stdout_tail", "stderr_tail", "snapshot_files", "snapshot_bytes", "isolation",
    }
    assert "content" not in receipt and "overlays" not in receipt
    assert "def test_ok" not in str(receipt)
    m_run.assert_called_once_with(
        tmp_path.resolve(), ["tests/test_a.py"], timeout=600, overlays={"tests/test_a.py": "def test_ok(): pass\n"}
    )


def test_receipt_utf8_tail_truncation_no_broken_chars(tmp_path: Path, mock_deps) -> None:
    _, _, m_run = mock_deps
    long_stdout = "a" * 8190 + "🌟" * 10
    long_stderr = "x" * 10000 + "✓"
    m_run.return_value = {**VALID_SANDBOX_RESULT, "stdout": long_stdout, "stderr": long_stderr}
    receipt = run_candidate_tests(tmp_path, VALID_TASK, VALID_WORKER, VALID_SHA)
    for key in ("stdout_tail", "stderr_tail"):
        encoded = receipt[key].encode("utf-8")
        assert len(encoded) <= 8192
        assert "\ufffd" not in receipt[key]


@pytest.mark.parametrize("offset", [1, 2, 3])
def test_bound_utf8_tail_multibyte_boundary(offset: int) -> None:
    head = b"a" * (8192 - offset)
    raw = (head + "🌟".encode("utf-8") + b"tail").decode("utf-8", errors="ignore")
    result = _bound_utf8_tail(raw)
    assert len(result.encode("utf-8")) <= 8192
    assert "\ufffd" not in result


def test_selection_order_dedup_and_skips_invalid_specs(tmp_path: Path, mock_deps) -> None:
    _, m_norm, _ = mock_deps
    def _fake_norm(specs, inv):
        s = specs[0]
        if s == "tests/test_1.py":
            return ["tests/test_1.py", "tests/test_2.py"], []
        if s == "invalid;spec":
            raise SandboxError("invalid syntax")
        if s == "tests/unmatched.py":
            return [], []
        if s == "tests/test_2.py":
            return ["tests/test_2.py", "tests/test_3.py"], []
        if s == "tests/partially_invalid.py":
            raise SandboxError("no concrete selector remains")
        if s == "tests/test_4.py":
            return ["tests/test_4.py"], []
        return [s], []

    m_norm.side_effect = _fake_norm
    task = {
        "test_selectors": [
            "tests/test_1.py", "invalid;spec", "tests/unmatched.py",
            "tests/test_2.py", "tests/partially_invalid.py", "tests/test_4.py",
        ]
    }
    receipt = run_candidate_tests(tmp_path, task, VALID_WORKER, VALID_SHA)
    assert receipt["selectors"] == ["tests/test_1.py", "tests/test_2.py", "tests/test_3.py", "tests/test_4.py"]
    assert receipt["unsupported_specs"] == ["invalid;spec", "tests/unmatched.py", "tests/partially_invalid.py"]


def test_selection_over_200_whole_spec_retained(tmp_path: Path, mock_deps) -> None:
    _, m_norm, _ = mock_deps
    def _fake_norm(specs, inv):
        s = specs[0]
        if s == "spec1":
            return [f"tests/test_{i}.py" for i in range(195)], []
        if s == "spec_over":
            return [f"tests/extra_{i}.py" for i in range(10)], []
        if s == "spec2":
            return [f"tests/last_{i}.py" for i in range(5)], []
        return [s], []

    m_norm.side_effect = _fake_norm
    task = {"test_selectors": ["spec1", "spec_over", "spec2"]}
    receipt = run_candidate_tests(tmp_path, task, VALID_WORKER, VALID_SHA)
    assert len(receipt["selectors"]) == 200
    assert receipt["selectors"][:195] == [f"tests/test_{i}.py" for i in range(195)]
    assert receipt["selectors"][195:] == [f"tests/last_{i}.py" for i in range(5)]
    assert receipt["unsupported_specs"] == ["spec_over"]
    assert not any("extra" in s for s in receipt["selectors"])


def test_selection_fails_closed_when_empty(tmp_path: Path, mock_deps) -> None:
    _, m_norm, m_run = mock_deps
    m_norm.side_effect = SandboxError("unmatched")
    task = {"test_selectors": ["tests/missing1.py", "tests/missing2.py"]}
    with pytest.raises(SandboxError, match="at least one focused selector required"):
        run_candidate_tests(tmp_path, task, VALID_WORKER, VALID_SHA)
    m_run.assert_not_called()


def _file_path(p: Path) -> Path:
    f = p / "reg.txt"
    f.write_text("x", encoding="utf-8")
    return f


@pytest.mark.parametrize(
    ("root_fn", "task", "worker", "sha", "timeout", "match"),
    [
        (lambda p: "", VALID_TASK, VALID_WORKER, VALID_SHA, 1200, "root must be a nonempty path"),
        (lambda p: "   ", VALID_TASK, VALID_WORKER, VALID_SHA, 1200, "root must be a nonempty path"),
        (lambda p: 123, VALID_TASK, VALID_WORKER, VALID_SHA, 1200, "root must be a str or Path"),
        (lambda p: p / "missing", VALID_TASK, VALID_WORKER, VALID_SHA, 1200, "root must be an existing directory"),
        (_file_path, VALID_TASK, VALID_WORKER, VALID_SHA, 1200, "root must be an existing directory"),
        (lambda p: p, VALID_TASK, VALID_WORKER, "badsha", 1200, "proposal_sha256 must be a lowercase 64-hex SHA"),
        (lambda p: p, VALID_TASK, VALID_WORKER, "A" * 64, 1200, "proposal_sha256 must be a lowercase 64-hex SHA"),
        (lambda p: p, VALID_TASK, VALID_WORKER, "a" * 63, 1200, "proposal_sha256 must be a lowercase 64-hex SHA"),
        (lambda p: p, VALID_TASK, VALID_WORKER, 123, 1200, "proposal_sha256 must be a lowercase 64-hex SHA"),
        (lambda p: p, VALID_TASK, VALID_WORKER, VALID_SHA, True, "timeout must be an integer between 1 and 3600"),
        (lambda p: p, VALID_TASK, VALID_WORKER, VALID_SHA, 0, "timeout must be an integer between 1 and 3600"),
        (lambda p: p, VALID_TASK, VALID_WORKER, VALID_SHA, 3601, "timeout must be an integer between 1 and 3600"),
        (lambda p: p, VALID_TASK, VALID_WORKER, VALID_SHA, "1200", "timeout must be an integer between 1 and 3600"),
        (lambda p: p, "not-mapping", VALID_WORKER, VALID_SHA, 1200, "task must be a mapping"),
        (lambda p: p, 123, VALID_WORKER, VALID_SHA, 1200, "task must be a mapping"),
        (lambda p: p, {}, VALID_WORKER, VALID_SHA, 1200, "task test_selectors must be 1..32 strings"),
        (lambda p: p, {"test_selectors": "t.py"}, VALID_WORKER, VALID_SHA, 1200, "task test_selectors must be 1..32 strings"),
        (lambda p: p, {"test_selectors": []}, VALID_WORKER, VALID_SHA, 1200, "task test_selectors must be 1..32 strings"),
        (lambda p: p, {"test_selectors": ["t.py"] * 33}, VALID_WORKER, VALID_SHA, 1200, "task test_selectors must be 1..32 strings"),
        (lambda p: p, {"test_selectors": [123]}, VALID_WORKER, VALID_SHA, 1200, "task test_selectors must be 1..32 strings"),
        (lambda p: p, VALID_TASK, "not-mapping", VALID_SHA, 1200, "worker_result must be a mapping"),
        (lambda p: p, VALID_TASK, 123, VALID_SHA, 1200, "worker_result must be a mapping"),
        (lambda p: p, VALID_TASK, {}, VALID_SHA, 1200, "Worker changes must be 1..32 entries"),
        (lambda p: p, VALID_TASK, {"changes": "str"}, VALID_SHA, 1200, "Worker changes must be 1..32 entries"),
        (lambda p: p, VALID_TASK, {"changes": []}, VALID_SHA, 1200, "Worker changes must be 1..32 entries"),
        (lambda p: p, VALID_TASK, {"changes": [{"path": "t.py", "content": "c"}] * 33}, VALID_SHA, 1200, "Worker changes must be 1..32 entries"),
        (lambda p: p, VALID_TASK, {"changes": ["not-map"]}, VALID_SHA, 1200, "Worker change must be a mapping"),
        (lambda p: p, VALID_TASK, {"changes": [{"path": 123, "content": "c"}]}, VALID_SHA, 1200, "Worker change path must be a nonempty string"),
        (lambda p: p, VALID_TASK, {"changes": [{"path": "  ", "content": "c"}]}, VALID_SHA, 1200, "Worker change path must be a nonempty string"),
        (lambda p: p, VALID_TASK, {"changes": [{"path": "t.py", "content": 123}]}, VALID_SHA, 1200, "Worker change content must be nonempty NUL-free string"),
        (lambda p: p, VALID_TASK, {"changes": [{"path": "t.py", "content": ""}]}, VALID_SHA, 1200, "Worker change content must be nonempty NUL-free string"),
        (lambda p: p, VALID_TASK, {"changes": [{"path": "t.py", "content": "bad\x00nul"}]}, VALID_SHA, 1200, "Worker change content must be nonempty NUL-free string"),
        (lambda p: p, VALID_TASK, {"changes": [{"path": "t.py", "content": "\ud800"}]}, VALID_SHA, 1200, "Worker change content must be valid UTF-8"),
        (lambda p: p, VALID_TASK, {"changes": [{"path": "tests/test_a.py", "content": "1"}, {"path": "tests/test_a.py", "content": "2"}]}, VALID_SHA, 1200, "duplicate overlay path: tests/test_a.py"),
    ],
)
def test_input_validation_errors_before_run_tests(
    tmp_path: Path, root_fn, task, worker, sha, timeout, match
) -> None:
    with patch("scripts.two_site_pre_review_receipt.two_site_agent_sandbox.run_tests") as m_run:
        with pytest.raises(SandboxError, match=re.escape(match)):
            run_candidate_tests(root_fn(tmp_path), task, worker, sha, timeout=timeout)
        m_run.assert_not_called()


def test_invalid_overlay_path_before_run_tests(tmp_path: Path) -> None:
    worker = {"changes": [{"path": "tests/test_a.py", "content": "ok"}]}
    with (
        patch("scripts.two_site_pre_review_receipt._validate_overlay_path", side_effect=SandboxError("bad path")),
        patch("scripts.two_site_pre_review_receipt.two_site_agent_sandbox.run_tests") as m_run,
    ):
        with pytest.raises(SandboxError, match="bad path"):
            run_candidate_tests(tmp_path, VALID_TASK, worker, VALID_SHA)
        m_run.assert_not_called()


@pytest.mark.parametrize(
    ("bad_result", "match"),
    [
        ("not-a-dict", "malformed sandbox result: expected mapping"),
        (b"bytes", "malformed sandbox result: expected mapping"),
        ({**VALID_SANDBOX_RESULT, "returncode": True}, "malformed sandbox result: returncode must be int"),
        ({**VALID_SANDBOX_RESULT, "returncode": "0"}, "malformed sandbox result: returncode must be int"),
        ({**VALID_SANDBOX_RESULT, "returncode": None}, "malformed sandbox result: returncode must be int"),
        ({**VALID_SANDBOX_RESULT, "stdout": 123}, "malformed sandbox result: stdout/stderr must be str"),
        ({**VALID_SANDBOX_RESULT, "stderr": None}, "malformed sandbox result: stdout/stderr must be str"),
        ({**VALID_SANDBOX_RESULT, "snapshot_files": -1}, "malformed sandbox result: snapshot_files must be non-negative int"),
        ({**VALID_SANDBOX_RESULT, "snapshot_files": True}, "malformed sandbox result: snapshot_files must be non-negative int"),
        ({**VALID_SANDBOX_RESULT, "snapshot_bytes": "10"}, "malformed sandbox result: snapshot_bytes must be non-negative int"),
        ({**VALID_SANDBOX_RESULT, "snapshot_bytes": -5}, "malformed sandbox result: snapshot_bytes must be non-negative int"),
        ({**VALID_SANDBOX_RESULT, "isolation": ""}, "malformed sandbox result: isolation must be non-empty str"),
        ({**VALID_SANDBOX_RESULT, "isolation": 123}, "malformed sandbox result: isolation must be non-empty str"),
        ({**VALID_SANDBOX_RESULT, "isolation": None}, "malformed sandbox result: isolation must be non-empty str"),
    ],
)
def test_malformed_sandbox_result(tmp_path: Path, mock_deps, bad_result: object, match: str) -> None:
    _, _, m_run = mock_deps
    m_run.return_value = bad_result
    with pytest.raises(SandboxError, match=re.escape(match)):
        run_candidate_tests(tmp_path, VALID_TASK, VALID_WORKER, VALID_SHA)
