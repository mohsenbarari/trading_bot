"""Tests for scripts.two_site_pre_review_receipt."""
from __future__ import annotations

from pathlib import Path
import re
from unittest.mock import patch
import pytest

from scripts.two_site_agent_sandbox import SandboxError
from scripts.two_site_pre_review_receipt import (
    _bound_utf8_tail,
    candidate_test_assertion_findings,
    p1_00_characterization_bootstrap_findings,
    run_candidate_tests,
    validate_test_receipt,
)

VALID_SHA = "0" * 64
VALID_RECEIPT = {
    "state": "PASSED",
    "proposal_sha256": VALID_SHA,
    "selectors": ["tests/test_a.py"],
    "unsupported_specs": [],
    "returncode": 0,
    "stdout_tail": "ok",
    "stderr_tail": "",
    "snapshot_files": 1,
    "snapshot_bytes": 10,
    "isolation": "bwrap",
}
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


def _p1_characterization_worker(
    content: str,
    path: str = "tests/test_p1_00b_characterization_example.py",
) -> dict:
    return {"changes": [{"path": path, "original_sha256": None, "content": content}]}


def test_p1_00_characterization_bootstrap_accepts_canonical_ordering() -> None:
    content = """from __future__ import annotations
import os
os.environ.setdefault(\"APP_ENV_FILE\", \"config/unit-test.env.example\")
from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence
install_p1_00_network_fence()
from api.routers import user_flags
"""
    assert p1_00_characterization_bootstrap_findings(_p1_characterization_worker(content)) == []


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        (
            """import os
os.environ.setdefault(\"APP_ENV_FILE\", \".env.test\")
from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence
install_p1_00_network_fence()
from api.routers import user_flags
""",
            "noncanonical APP_ENV_FILE",
        ),
        (
            """import os
os.environ.setdefault(\"APP_ENV_FILE\", \"config/unit-test.env.example\")
from api.routers import user_flags
from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence
install_p1_00_network_fence()
""",
            "before application imports",
        ),
        (
            """import os
import socket
os.environ.setdefault(\"APP_ENV_FILE\", \"config/unit-test.env.example\")
from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence
install_p1_00_network_fence()
socket.socket.connect = lambda *_args: None
from api.routers import user_flags
""",
            "must not assign socket connection hooks",
        ),
    ],
)
def test_p1_00_characterization_bootstrap_reports_exact_deterministic_defect(
    content: str, expected: str,
) -> None:
    findings = p1_00_characterization_bootstrap_findings(_p1_characterization_worker(content))
    assert len(findings) == 1
    assert expected in findings[0]


def test_bootstrap_gate_does_not_expand_to_unrelated_tests() -> None:
    findings = p1_00_characterization_bootstrap_findings(
        _p1_characterization_worker("from api.routers import user_flags\n", "tests/test_unrelated.py")
    )
    assert findings == []


def test_bootstrap_gate_does_not_retroactively_block_existing_test_files() -> None:
    worker = _p1_characterization_worker("from api.routers import user_flags\n")
    worker["changes"][0]["original_sha256"] = "a" * 64
    assert p1_00_characterization_bootstrap_findings(worker) == []


def test_bootstrap_gate_keeps_correction_findings_bounded() -> None:
    worker = {
        "changes": [
            {
                "path": f"tests/test_p1_00b_characterization_example_{index}.py",
                "original_sha256": None,
                "content": "from api.routers import user_flags\n",
            }
            for index in range(32)
        ]
    }
    assert len(p1_00_characterization_bootstrap_findings(worker)) == 32


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
            return [f"tests/test_extra_{i}.py" for i in range(10)], []
        if s == "spec2":
            return [f"tests/test_last_{i}.py" for i in range(5)], []
        return [s], []

    m_norm.side_effect = _fake_norm
    task = {"test_selectors": ["spec1", "spec_over", "spec2"]}
    receipt = run_candidate_tests(tmp_path, task, VALID_WORKER, VALID_SHA)
    assert len(receipt["selectors"]) == 200
    assert receipt["selectors"][:195] == [f"tests/test_{i}.py" for i in range(195)]
    assert receipt["selectors"][195:] == [f"tests/test_last_{i}.py" for i in range(5)]
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


def test_validate_test_receipt_pass_and_fail() -> None:
    res_pass = validate_test_receipt(VALID_RECEIPT, VALID_SHA)
    assert res_pass == VALID_RECEIPT
    assert res_pass["state"] == "PASSED"
    assert res_pass["returncode"] == 0

    failed_receipt = {
        **VALID_RECEIPT,
        "state": "FAILED",
        "returncode": 1,
        "unsupported_specs": ["tests/unsupported.py"],
    }
    res_fail = validate_test_receipt(failed_receipt, VALID_SHA, require_passed=False)
    assert res_fail["state"] == "FAILED"
    assert res_fail["returncode"] == 1
    assert res_fail["unsupported_specs"] == ["tests/unsupported.py"]


def test_validate_test_receipt_no_mutation_and_copy_semantics() -> None:
    selectors = ["tests/test_a.py"]
    unsupported = ["tests/test_b.py"]
    receipt = {
        **VALID_RECEIPT,
        "selectors": selectors,
        "unsupported_specs": unsupported,
    }
    res = validate_test_receipt(receipt, VALID_SHA)
    assert res == receipt
    assert res is not receipt
    assert res["selectors"] is not selectors
    assert res["unsupported_specs"] is not unsupported

    receipt["selectors"].append("tests/test_c.py")
    receipt["unsupported_specs"].append("tests/test_d.py")
    assert res["selectors"] == ["tests/test_a.py"]
    assert res["unsupported_specs"] == ["tests/test_b.py"]

    res["selectors"].append("tests/test_e.py")
    assert selectors == ["tests/test_a.py", "tests/test_c.py"]


def test_validate_test_receipt_require_passed() -> None:
    res = validate_test_receipt(VALID_RECEIPT, VALID_SHA, require_passed=True)
    assert res["state"] == "PASSED"

    failed_receipt = {**VALID_RECEIPT, "state": "FAILED", "returncode": 2}
    with pytest.raises(SandboxError, match="receipt state must be PASSED when require_passed=True"):
        validate_test_receipt(failed_receipt, VALID_SHA, require_passed=True)

    for bad_rp in ("True", 1, None, [True]):
        with pytest.raises(SandboxError, match="require_passed must be a bool"):
            validate_test_receipt(VALID_RECEIPT, VALID_SHA, require_passed=bad_rp)


@pytest.mark.parametrize("bad_sha", ["bad", "A" * 64, 123, None, " " * 64])
def test_validate_test_receipt_bad_arg_sha(bad_sha) -> None:
    with pytest.raises(SandboxError, match="proposal_sha256 must be a lowercase 64-hex SHA"):
        validate_test_receipt(VALID_RECEIPT, bad_sha)


@pytest.mark.parametrize(
    ("corrupt_fn", "match"),
    [
        (lambda r: "not-a-mapping", "receipt must be a mapping"),
        (lambda r: 123, "receipt must be a mapping"),
        (lambda r: None, "receipt must be a mapping"),
        (lambda r: {k: v for k, v in r.items() if k != "state"}, "receipt keys must match expected schema exactly"),
        (lambda r: {k: v for k, v in r.items() if k != "proposal_sha256"}, "receipt keys must match expected schema exactly"),
        (lambda r: {k: v for k, v in r.items() if k != "selectors"}, "receipt keys must match expected schema exactly"),
        (lambda r: {k: v for k, v in r.items() if k != "unsupported_specs"}, "receipt keys must match expected schema exactly"),
        (lambda r: {k: v for k, v in r.items() if k != "returncode"}, "receipt keys must match expected schema exactly"),
        (lambda r: {k: v for k, v in r.items() if k != "stdout_tail"}, "receipt keys must match expected schema exactly"),
        (lambda r: {k: v for k, v in r.items() if k != "stderr_tail"}, "receipt keys must match expected schema exactly"),
        (lambda r: {k: v for k, v in r.items() if k != "snapshot_files"}, "receipt keys must match expected schema exactly"),
        (lambda r: {k: v for k, v in r.items() if k != "snapshot_bytes"}, "receipt keys must match expected schema exactly"),
        (lambda r: {k: v for k, v in r.items() if k != "isolation"}, "receipt keys must match expected schema exactly"),
        (lambda r: {**r, "extra": 1}, "receipt keys must match expected schema exactly"),
        (lambda r: {**r, "proposal_sha256": "badsha"}, "receipt proposal_sha256 must be a lowercase 64-hex SHA"),
        (lambda r: {**r, "proposal_sha256": "A" * 64}, "receipt proposal_sha256 must be a lowercase 64-hex SHA"),
        (lambda r: {**r, "proposal_sha256": 123}, "receipt proposal_sha256 must be a lowercase 64-hex SHA"),
        (lambda r: {**r, "proposal_sha256": "1" * 64}, "receipt proposal_sha256 does not match expected SHA"),
        (lambda r: {**r, "returncode": True}, "receipt returncode must be a non-bool integer"),
        (lambda r: {**r, "returncode": False}, "receipt returncode must be a non-bool integer"),
        (lambda r: {**r, "returncode": "0"}, "receipt returncode must be a non-bool integer"),
        (lambda r: {**r, "returncode": None}, "receipt returncode must be a non-bool integer"),
        (lambda r: {**r, "state": 123}, "receipt state must be a string"),
        (lambda r: {**r, "state": "UNKNOWN"}, "receipt state must be PASSED or FAILED"),
        (lambda r: {**r, "state": "PASSED", "returncode": 1}, "receipt state PASSED requires returncode 0"),
        (lambda r: {**r, "state": "FAILED", "returncode": 0}, "receipt state FAILED requires nonzero returncode"),
        (lambda r: {**r, "selectors": "tests/test_a.py"}, "receipt selectors must be a list"),
        (lambda r: {**r, "selectors": ("tests/test_a.py",)}, "receipt selectors must be a list"),
        (lambda r: {**r, "selectors": []}, "explicit bounded pytest selectors required"),
        (lambda r: {**r, "selectors": [f"tests/test_{i}.py" for i in range(201)]}, "explicit bounded pytest selectors required"),
        (lambda r: {**r, "selectors": ["tests/test_a.py", "tests/test_a.py"]}, "duplicate pytest selector"),
        (lambda r: {**r, "selectors": ["invalid_selector"]}, "unsupported pytest selector or option"),
        (lambda r: {**r, "selectors": ["tests/test_a.py/../test_a.py"]}, "source path escapes the assigned worktree"),
        (lambda r: {**r, "unsupported_specs": "tests/test_b.py"}, "receipt unsupported_specs must be a list"),
        (lambda r: {**r, "unsupported_specs": ("tests/test_b.py",)}, "receipt unsupported_specs must be a list"),
        (lambda r: {**r, "unsupported_specs": [f"spec_{i}" for i in range(33)]}, "receipt unsupported_specs cannot exceed 32 entries"),
        (lambda r: {**r, "unsupported_specs": ["spec1", "spec1"]}, "receipt unsupported_specs entries must be unique"),
        (lambda r: {**r, "unsupported_specs": [""]}, "receipt unsupported_specs entries must be nonempty strings"),
        (lambda r: {**r, "unsupported_specs": [123]}, "receipt unsupported_specs entries must be nonempty strings"),
        (lambda r: {**r, "unsupported_specs": ["\ud800"]}, "receipt unsupported_specs entries must be valid UTF-8"),
        (lambda r: {**r, "stdout_tail": 123}, "receipt stdout_tail must be a string"),
        (lambda r: {**r, "stdout_tail": b"ok"}, "receipt stdout_tail must be a string"),
        (lambda r: {**r, "stdout_tail": "\ud800"}, "receipt stdout_tail must be valid UTF-8"),
        (lambda r: {**r, "stdout_tail": "a" * 8193}, "receipt stdout_tail exceeds 8192 bytes"),
        (lambda r: {**r, "stderr_tail": None}, "receipt stderr_tail must be a string"),
        (lambda r: {**r, "stderr_tail": "\ud800"}, "receipt stderr_tail must be valid UTF-8"),
        (lambda r: {**r, "stderr_tail": "a" * 8193}, "receipt stderr_tail exceeds 8192 bytes"),
        (lambda r: {**r, "snapshot_files": True}, "receipt snapshot_files must be a non-negative integer"),
        (lambda r: {**r, "snapshot_files": -1}, "receipt snapshot_files must be a non-negative integer"),
        (lambda r: {**r, "snapshot_files": 1.5}, "receipt snapshot_files must be a non-negative integer"),
        (lambda r: {**r, "snapshot_files": "1"}, "receipt snapshot_files must be a non-negative integer"),
        (lambda r: {**r, "snapshot_bytes": False}, "receipt snapshot_bytes must be a non-negative integer"),
        (lambda r: {**r, "snapshot_bytes": -1}, "receipt snapshot_bytes must be a non-negative integer"),
        (lambda r: {**r, "snapshot_bytes": None}, "receipt snapshot_bytes must be a non-negative integer"),
        (lambda r: {**r, "isolation": ""}, "receipt isolation must be a non-empty string"),
        (lambda r: {**r, "isolation": 123}, "receipt isolation must be a non-empty string"),
        (lambda r: {**r, "isolation": "\ud800"}, "receipt isolation must be valid UTF-8"),
    ],
)
def test_validate_test_receipt_invariants(corrupt_fn, match: str) -> None:
    bad_receipt = corrupt_fn(dict(VALID_RECEIPT))
    with pytest.raises(SandboxError, match=re.escape(match)):
        validate_test_receipt(bad_receipt, VALID_SHA)


def test_run_candidate_tests_self_validation_success_and_spy(tmp_path: Path, mock_deps) -> None:
    with patch(
        "scripts.two_site_pre_review_receipt.validate_test_receipt",
        wraps=validate_test_receipt,
    ) as m_validate:
        receipt = run_candidate_tests(tmp_path, VALID_TASK, VALID_WORKER, VALID_SHA)
        assert receipt["state"] == "PASSED"
        m_validate.assert_called_once()
        call_args = m_validate.call_args[0]
        assert call_args[0]["proposal_sha256"] == VALID_SHA
        assert call_args[1] == VALID_SHA


def test_run_candidate_tests_self_validation_fails_on_corrupt_tail(tmp_path: Path, mock_deps) -> None:
    _, _, m_run = mock_deps
    with patch(
        "scripts.two_site_pre_review_receipt._bound_utf8_tail",
        return_value="\ud800",
    ):
        with pytest.raises(SandboxError, match="receipt stdout_tail must be valid UTF-8"):
            run_candidate_tests(tmp_path, VALID_TASK, VALID_WORKER, VALID_SHA)


def _two_site_worker(
    content: str,
    path: str = "tests/test_two_site_example.py",
    original_sha256: str | None = None,
) -> dict:
    return {"changes": [{"path": path, "original_sha256": original_sha256, "content": content}]}


def test_candidate_test_assertion_findings_ignores_assert_message_constant_ifexp() -> None:
    content = """
def test_valid():
    real_condition = True
    assert real_condition, 'left' if False else 'right'
"""
    assert candidate_test_assertion_findings(_two_site_worker(content)) == []


def test_candidate_test_assertion_findings_ignores_assert_message_constant_set() -> None:
    content = """
def test_valid():
    real_condition = True
    assert real_condition, "task_id" in {"task_id", "status"}
"""
    assert candidate_test_assertion_findings(_two_site_worker(content)) == []


def test_candidate_test_assertion_findings_detects_task_514_patterns() -> None:
    content_ifexp = """
def test_task_514_ifexp():
    task_data = {"task_id": "123"}
    assert task_data["task_id atmospheric" if False else "task_id"]
"""
    findings_ifexp = candidate_test_assertion_findings(_two_site_worker(content_ifexp))
    assert len(findings_ifexp) == 1
    assert "tests/test_two_site_example.py:4" in findings_ifexp[0]
    assert "constant-branch" in findings_ifexp[0]
    assert "task_id atmospheric" not in findings_ifexp[0]

    content_set = """
def test_task_514_set():
    assert "task_id" in {"task_id", "status"}
"""
    findings_set = candidate_test_assertion_findings(_two_site_worker(content_set))
    assert len(findings_set) == 1
    assert "tests/test_two_site_example.py:3" in findings_set[0]
    assert "constant-set" in findings_set[0]
    assert "task_id" not in findings_set[0]


def test_candidate_test_assertion_findings_detects_not_in_same_constant_set() -> None:
    content = """
def test_not_in():
    assert "task_id" not in {"task_id", "status"}
"""
    findings = candidate_test_assertion_findings(_two_site_worker(content))
    assert len(findings) == 1
    assert "constant-set" in findings[0]


def test_candidate_test_assertion_findings_ignores_meaningful_nonmatches() -> None:
    content = """
def test_meaningful():
    x = 10
    task_data = {"a": 1}
    assert task_data["a" if x > 0 else "b"]
    key = "task_id"
    assert key in {"task_id", "status"}
    valid = {"task_id", "status"}
    assert "task_id" in valid
    assert "task_id" in {"status", "other"}
    assert "task_id" in {"task_id", str(x)}
"""
    assert candidate_test_assertion_findings(_two_site_worker(content)) == []


def test_candidate_test_assertion_findings_skips_modified_unrelated_and_syntax_error() -> None:
    assert candidate_test_assertion_findings(
        _two_site_worker("assert 'a' in {'a'}\n", original_sha256="a" * 64)
    ) == ["tests/test_two_site_example.py:1: constant-set"]
    assert candidate_test_assertion_findings(
        _two_site_worker("assert 'a' in {'a'}\n", path="tests/test_other.py")
    ) == []
    assert candidate_test_assertion_findings(
        _two_site_worker("def broken_syntax(:\n")
    ) == []
    assert candidate_test_assertion_findings({}) == []
    assert candidate_test_assertion_findings({"changes": "not-a-list"}) == []
    assert candidate_test_assertion_findings({"changes": [{"path": None}]}) == []


def test_candidate_test_assertion_findings_deterministic_bound_32() -> None:
    changes = [
        {
            "path": f"tests/test_two_site_file_{i}.py",
            "original_sha256": None,
            "content": f"assert 'id' in {{'id', 'status'}}\nassert task['a' if False else 'b']\n",
        }
        for i in range(20)
    ]
    findings = candidate_test_assertion_findings({"changes": changes})
    assert len(findings) == 32
    assert all("constant-" in f for f in findings)


def test_candidate_test_assertion_findings_self_proving_and_selector_default() -> None:
    sentinel_src = "def test_s():\n    x = 'sentinel'\n    assert 'sentinel' in x\n"
    sel_src = "def test_sel():\n    sel = task.get('test_selectors', ['tests/default.py'])\n"
    for src, expected_code in [(sentinel_src, "self-proving"), (sel_src, "selector-default")]:
        for sha in (None, "b" * 64):
            findings = candidate_test_assertion_findings(_two_site_worker(src, original_sha256=sha))
            assert len(findings) == 1
            assert expected_code in findings[0]
            assert "tests/test_two_site_example.py:" in findings[0]


def test_candidate_test_assertion_findings_negatives() -> None:
    rebind_src = "def test_r():\n    x = 'sentinel'\n    if True:\n        x = 'other'\n    assert 'sentinel' in x\n"
    walrus_src = "def test_w():\n    x = 'sentinel'\n    assert (x := 'other') and ('sentinel' in x)\n"
    safe_sel_src = (
        "def test_safe():\n"
        "    a = task.get('test_selectors')\n"
        "    b = task.get('test_selectors', None)\n"
        "    c = task['test_selectors']\n"
        "    d = task.get('other', ['fallback'])\n"
    )
    for src in (rebind_src, walrus_src, safe_sel_src):
        assert candidate_test_assertion_findings(_two_site_worker(src)) == []
    bad_sentinel = "def test_s():\n    x = 'sentinel'\n    assert 'sentinel' in x\n"
    bad_sel = "def test_sel():\n    sel = task.get('test_selectors', ['default.py'])\n"
    assert candidate_test_assertion_findings(_two_site_worker(bad_sentinel, path="tests/test_other.py")) == []
    assert candidate_test_assertion_findings(_two_site_worker(bad_sel, path="tests/test_other.py")) == []


_this_file = Path(__file__).resolve()
_repo_root = _this_file.parents[1]
for _rel_path, _cap in (("scripts/two_site_pre_review_receipt.py", 48000), ("tests/test_two_site_pre_review_receipt.py", 40000)):
    _target = _repo_root / _rel_path
    _st = _target.stat()
    assert _st.st_size <= _cap, f"{_rel_path} exceeds {_cap}: {_st.st_size}"
    with open(_target, "rb") as _f:
        assert len(_f.read(_cap + 1)) <= _cap, f"{_rel_path} read exceeds {_cap}"

import copy


def test_shallow_dict_aliases_nested_mutation_demonstration() -> None:
    orig = {"nested": [1, 2]}
    shallow = dict(orig)
    orig["nested"].append(3)
    assert shallow["nested"] == [1, 2, 3]

    orig2 = {"nested": [1, 2]}
    deep = copy.deepcopy(orig2)
    orig2["nested"].append(3)
    assert deep["nested"] == [1, 2]


_P = "tests/test_two_site_example.py"
_S = "a" * 64
_D = "    data_snap = dict(data)\n"
_A = "    assert data == data_snap\n"

FINDING_CASES = [
    ("def test_f(data):\n" + _D + _A, _P, None, [2]),
    ("def test_f(data):\n    data_snap = list(data)\n" + _A, _P, None, [2]),
    ("def test_f(data):\n" + _D + "    assert data_snap == data\n", _P, None, [2]),
    ("class T:\n    def test_f(self, data):\n    " + _D + "        self.assertEqual(data, data_snap)\n", _P, None, [3]),
    ("class T:\n    def test_f(self, data):\n    " + _D + "        self.assertEqual(data_snap, data)\n", _P, None, [3]),
    ("def test_f(data, /):\n" + _D + _A, _P, None, [2]),
    ("def test_f(*, data):\n" + _D + _A, _P, None, [2]),
    ("def test_f(**kwargs):\n    kw_snap = dict(kwargs)\n    assert kwargs == kw_snap\n", _P, None, [2]),
    ("def test_f(*args):\n    a_snap = list(args)\n    assert args == a_snap\n", _P, None, [2]),
    ("async def test_f(data):\n" + _D + _A, _P, None, [2]),
    ("def test_f(data):\n    if True:\n    " + _D + _A, _P, None, [3]),
    ("def test_f(data):\n" + _D + _A, _P, _S, [2]),
    ("def test_f(data):\n" + _D + _A + _A, _P, None, [2]),
    ("def test_f(a, b):\n    a_snap = dict(a); b_snap = dict(b)\n    assert a == a_snap and b == b_snap\n", _P, None, [2, 2]),
]

GOOD_FIXTURE_SRC = "import copy\ndef test_deep(data):\n    data_snap = copy.deepcopy(data)\n    assert data == data_snap\n"


@pytest.mark.parametrize(("bad_src", "path", "sha", "bad_lines"), FINDING_CASES)
def test_shallow_custody_finding_rows_with_full_good_bad_good_cycle(
    bad_src: str, path: str, sha: str | None, bad_lines: list[int]
) -> None:
    good_worker = _two_site_worker(GOOD_FIXTURE_SRC, path=path, original_sha256=sha)
    bad_worker = _two_site_worker(bad_src, path=path, original_sha256=sha)
    expected_good: list[str] = []
    expected_bad: list[str] = [f"{path}:{line}: shallow-custody-snapshot" for line in bad_lines]

    good_clone_1 = copy.deepcopy(good_worker)
    bad_clone = copy.deepcopy(bad_worker)
    expected_good_clone = copy.deepcopy(expected_good)
    expected_bad_clone = copy.deepcopy(expected_bad)

    res_good_before = candidate_test_assertion_findings(good_worker)
    assert res_good_before == expected_good
    assert good_worker == good_clone_1
    assert expected_good == expected_good_clone

    res_bad = candidate_test_assertion_findings(bad_worker)
    assert res_bad == expected_bad
    assert bad_worker == bad_clone
    assert expected_bad == expected_bad_clone

    res_good_after = candidate_test_assertion_findings(good_worker)
    assert res_good_after == expected_good
    assert res_good_before == res_good_after

    assert good_worker == good_clone_1
    assert bad_worker == bad_clone
    assert expected_good == expected_good_clone
    assert expected_bad == expected_bad_clone


NON_FINDING_CASES = [
    ("def test_f(data):\n" + _D, _P),
    ("def test_f(data):\n    other = {'a': 1}\n    data_snap = dict(other)\n    assert other == data_snap\n", _P),
    ("def test_f():\n    data = {'a': 1}\n" + _D + _A, _P),
    ("def test_f(data, other):\n" + _D + "    assert other == data_snap\n", _P),
    ("from copy import deepcopy\ndef test_f(data):\n    data_snap = deepcopy(data)\n" + _A, _P),
    ("import copy\ndef test_f(data):\n    data_snap = copy.deepcopy(data)\n" + _A, _P),
    ("def test_f(data):\n" + _D + "    def inner():\n    " + _A, _P),
    ("def test_f(data):\n" + _D + "    class Inner:\n    " + _A, _P),
    ("def test_f(data):\n" + _D + "    f = lambda: data == data_snap\n", _P),
    ("def test_f(data):\n" + _D + "    assert True, data == data_snap\n", _P),
    ("def test_f(data, dict):\n" + _D + _A, _P),
    ("def test_f(data):\n    dict = lambda x: x\n" + _D + _A, _P),
    ("for _ in (1,):\n    dict = copy.deepcopy\ndef test_f(data):\n" + _D + _A, _P),
    ("if True:\n    dict = copy.deepcopy\ndef test_f(data):\n" + _D + _A, _P),
    ("def test_f(data):\n    data_snap = data.copy()\n" + _A, _P),
    ("def test_f(data):\n" + _D + _A, "tests/test_other_unrelated.py"),
    ("def test_f(data:\n", _P),
]


@pytest.mark.parametrize(("src", "path"), NON_FINDING_CASES)
def test_shallow_custody_non_finding_positives(src: str, path: str) -> None:
    worker = _two_site_worker(src, path=path)
    expected: list[str] = []
    worker_clone = copy.deepcopy(worker)
    expected_clone = copy.deepcopy(expected)

    res1 = candidate_test_assertion_findings(worker)
    assert res1 == expected
    assert worker == worker_clone
    assert expected == expected_clone

    res2 = candidate_test_assertion_findings(worker)
    assert res2 == expected
    assert res1 == res2
    assert worker == worker_clone
    assert expected == expected_clone


def test_shallow_custody_mixed_old_new_and_bound_32() -> None:
    mixed_src = (
        "def test_f(data):\n"
        "    sel = task.get('test_selectors', ['default.py'])\n"
        "    data_snap = dict(data)\n"
        "    assert data == data_snap\n"
        "    assert data == data_snap\n"
    )
    mixed_worker = _two_site_worker(mixed_src, path=_P)
    expected_mixed = [
        f"{_P}:2: selector-default",
        f"{_P}:3: shallow-custody-snapshot",
    ]
    w_clone = copy.deepcopy(mixed_worker)
    exp_clone = copy.deepcopy(expected_mixed)
    res_mixed = candidate_test_assertion_findings(mixed_worker)
    assert res_mixed == expected_mixed
    assert mixed_worker == w_clone
    assert expected_mixed == exp_clone

    bound_changes = [
        {
            "path": f"tests/test_two_site_f_{i}.py",
            "original_sha256": None,
            "content": "def test_f(data):\n    d_snap = dict(data)\n    assert data == d_snap\n",
        }
        for i in range(40)
    ]
    expected_32 = [f"tests/test_two_site_f_{i}.py:2: shallow-custody-snapshot" for i in range(32)]
    bound_worker = {"changes": bound_changes}
    bw_clone = copy.deepcopy(bound_worker)
    exp32_clone = copy.deepcopy(expected_32)
    res_32 = candidate_test_assertion_findings(bound_worker)
    assert res_32 == expected_32
    assert bound_worker == bw_clone
    assert expected_32 == exp32_clone
