"""Tests for 24k UTF-8 new test byte budget gate in run_candidate_tests."""
from __future__ import annotations

from pathlib import Path

_this_file = Path(__file__).resolve()
_repo_root = _this_file.parents[1]
_source_path = _repo_root / "scripts/two_site_pre_review_receipt.py"
_test_path = _this_file

_src_stat = _source_path.stat().st_size
assert _src_stat <= 48000, f"scripts/two_site_pre_review_receipt.py exceeds 48000: {_src_stat}"
with open(_source_path, "rb") as _f:
    _src_bytes = _f.read(48001)
assert len(_src_bytes) <= 48000, "scripts/two_site_pre_review_receipt.py read exceeds 48000"
assert len(_src_bytes) == _src_stat, "scripts/two_site_pre_review_receipt.py size inconsistent"

_test_stat = _test_path.stat().st_size
assert _test_stat <= 24000, f"tests/test_two_site_pre_review_new_test_byte_budget.py exceeds 24000: {_test_stat}"
with open(_test_path, "rb") as _f:
    _test_bytes = _f.read(24001)
assert len(_test_bytes) <= 24000, "tests/test_two_site_pre_review_new_test_byte_budget.py read exceeds 24000"
assert len(_test_bytes) == _test_stat, "tests/test_two_site_pre_review_new_test_byte_budget.py size inconsistent"

_combo_size = _src_stat + _test_stat
assert _combo_size <= 62000, f"combined source and test size exceeds 62000: {_combo_size}"

import copy
from typing import Any
from unittest.mock import patch
import pytest

from scripts.two_site_agent_sandbox import SandboxError
from scripts.two_site_pre_review_receipt import (
    MAX_NEW_TEST_BYTES,
    run_candidate_tests,
    validate_test_receipt,
)
from scripts.two_site_proposal_pipeline import test_gate_feedback as pipeline_test_gate_feedback

VALID_SHA = "0" * 64
VALID_TASK = {"test_selectors": ["tests/test_a.py"]}
VALID_WORKER = {
    "changes": [
        {
            "path": "tests/test_a.py",
            "original_sha256": None,
            "content": "def test_ok(): pass\n",
        }
    ]
}
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


def _run_good_fault_good_cycle(
    tmp_path: Path,
    mock_deps,
    bad_worker: dict,
    expected_bad_path: str,
    expected_bytes: int,
) -> dict:
    _, _, m_run = mock_deps
    task_golden = copy.deepcopy(VALID_TASK)
    worker_golden = copy.deepcopy(VALID_WORKER)
    sha_golden = copy.deepcopy(VALID_SHA)
    bad_worker_golden = copy.deepcopy(bad_worker)

    # 1. SAME-layer good call BEFORE fault
    m_run.reset_mock()
    good_before = run_candidate_tests(tmp_path, VALID_TASK, VALID_WORKER, VALID_SHA)
    assert good_before["state"] == "PASSED"
    assert good_before["returncode"] == 0
    assert good_before["isolation"] == "bwrap"
    assert m_run.call_count == 1
    assert VALID_TASK == task_golden
    assert VALID_WORKER == worker_golden
    assert VALID_SHA == sha_golden

    # 2. Fault call
    m_run.reset_mock()
    bad_receipt = run_candidate_tests(tmp_path, VALID_TASK, bad_worker, VALID_SHA)
    assert m_run.call_count == 0
    assert bad_receipt["state"] == "FAILED"
    assert bad_receipt["proposal_sha256"] == VALID_SHA
    assert bad_receipt["selectors"] == ["tests/test_a.py"]
    assert bad_receipt["unsupported_specs"] == []
    assert bad_receipt["returncode"] == 1
    assert bad_receipt["stdout_tail"] == ""
    assert bad_receipt["snapshot_files"] == 0
    assert bad_receipt["snapshot_bytes"] == 0
    assert bad_receipt["isolation"] == "static-byte-budget"
    assert bad_receipt["stderr_tail"].startswith("AssertionError:")
    assert expected_bad_path in bad_receipt["stderr_tail"]
    assert str(expected_bytes) in bad_receipt["stderr_tail"]
    assert "24000" in bad_receipt["stderr_tail"]
    assert bad_worker == bad_worker_golden
    assert VALID_TASK == task_golden
    assert VALID_SHA == sha_golden

    # 3. SAME-layer good call AFTER fault
    m_run.reset_mock()
    good_after = run_candidate_tests(tmp_path, VALID_TASK, VALID_WORKER, VALID_SHA)
    assert good_after == good_before
    assert m_run.call_count == 1
    assert VALID_TASK == task_golden
    assert VALID_WORKER == worker_golden
    assert VALID_SHA == sha_golden
    assert bad_worker == bad_worker_golden

    return bad_receipt


def test_new_ascii_test_boundary_24000_and_24001(tmp_path: Path, mock_deps) -> None:
    _, _, m_run = mock_deps
    prefix = "# ASCII comment\ndef test_ascii():\n    pass\n# "
    pad_24000 = "a" * (24000 - len(prefix) - 1) + "\n"
    content_24000 = prefix + pad_24000
    assert len(content_24000.encode("utf-8")) == 24000

    worker_24000 = {"changes": [{"path": "tests/test_ascii.py", "original_sha256": None, "content": content_24000}]}
    m_run.reset_mock()
    receipt_24000 = run_candidate_tests(tmp_path, VALID_TASK, worker_24000, VALID_SHA)
    assert receipt_24000["state"] == "PASSED"
    assert m_run.call_count == 1

    content_24001 = content_24000 + "x"
    assert len(content_24001.encode("utf-8")) == 24001
    bad_worker = {"changes": [{"path": "tests/test_ascii.py", "original_sha256": None, "content": content_24001}]}
    _run_good_fault_good_cycle(tmp_path, mock_deps, bad_worker, "tests/test_ascii.py", 24001)


def test_unicode_multibyte_byte_boundary_astral_nonascii(tmp_path: Path, mock_deps) -> None:
    _, _, m_run = mock_deps
    prefix = "# 🌟 سلام\ndef test_uni():\n    pass\n# "
    content_uni_24000 = prefix + ("🌟" * 5988) + "αی\n"
    assert len(content_uni_24000) == 6027
    assert len(content_uni_24000.encode("utf-8")) == 24000

    worker_24000 = {"changes": [{"path": "tests/test_uni.py", "original_sha256": None, "content": content_uni_24000}]}
    m_run.reset_mock()
    receipt_24000 = run_candidate_tests(tmp_path, VALID_TASK, worker_24000, VALID_SHA)
    assert receipt_24000["state"] == "PASSED"
    assert m_run.call_count == 1

    content_uni_24001 = content_uni_24000 + "x"
    assert len(content_uni_24001) == 6028
    assert len(content_uni_24001.encode("utf-8")) == 24001
    bad_worker = {"changes": [{"path": "tests/test_uni.py", "original_sha256": None, "content": content_uni_24001}]}
    _run_good_fault_good_cycle(tmp_path, mock_deps, bad_worker, "tests/test_uni.py", 24001)


def test_mixed_source_and_multiple_oversized_tests(tmp_path: Path, mock_deps) -> None:
    mixed_worker = {
        "changes": [
            {"path": "scripts/new_feature.py", "original_sha256": None, "content": "value = 42\n"},
            {"path": "tests/test_new_feature.py", "original_sha256": None, "content": "# " + "a" * 24000},
        ]
    }
    _run_good_fault_good_cycle(tmp_path, mock_deps, mixed_worker, "tests/test_new_feature.py", 24002)

    multi_worker = {
        "changes": [
            {"path": "tests/test_first.py", "original_sha256": None, "content": "# " + "a" * 24100},
            {"path": "tests/test_second.py", "original_sha256": None, "content": "# " + "b" * 24200},
        ]
    }
    receipt = _run_good_fault_good_cycle(tmp_path, mock_deps, multi_worker, "tests/test_first.py", 24102)
    assert "tests/test_second.py" in receipt["stderr_tail"]
    assert "24202" in receipt["stderr_tail"]
    idx1 = receipt["stderr_tail"].index("tests/test_first.py")
    idx2 = receipt["stderr_tail"].index("tests/test_second.py")
    assert idx1 < idx2


@pytest.mark.parametrize(
    ("path", "sha"),
    [
        ("tests/test_existing.py", "a" * 64),
        ("tests/test_legacy.py", ...),
        ("scripts/tool.py", None),
        ("docs/test_doc.py", None),
        ("tests/helper.py", None),
        ("tests/test_example.txt", None),
    ],
)
def test_excluded_files_unaffected_by_byte_gate(tmp_path: Path, mock_deps, path: str, sha: object) -> None:
    _, _, m_run = mock_deps
    large_content = "# large content\n" + "x" * 25000
    change: dict[str, Any] = {"path": path, "content": large_content}
    if sha is not ...:
        change["original_sha256"] = sha
    worker = {"changes": [change]}

    m_run.reset_mock()
    receipt = run_candidate_tests(tmp_path, VALID_TASK, worker, VALID_SHA)
    assert receipt["state"] == "PASSED"
    assert m_run.call_count == 1
    assert receipt["isolation"] == "bwrap"


@pytest.mark.parametrize(
    ("change", "match"),
    [
        ({"path": "../escaped.py", "original_sha256": None, "content": "x = 1"}, "source path escapes the assigned worktree"),
        ({"path": "tests/test_nul.py", "original_sha256": None, "content": "x = 1\x00"}, "content must be nonempty NUL-free"),
        ({"path": "tests/test_surrogate.py", "original_sha256": None, "content": "\ud800"}, "content must be valid UTF-8"),
    ],
)
def test_validation_errors_preserve_sandbox_error_before_gate(
    tmp_path: Path, mock_deps, change: dict, match: str
) -> None:
    _, _, m_run = mock_deps
    worker = {"changes": [change]}
    worker_golden = copy.deepcopy(worker)
    with pytest.raises(SandboxError, match=match):
        run_candidate_tests(tmp_path, VALID_TASK, worker, VALID_SHA)
    assert m_run.call_count == 0
    assert worker == worker_golden


def test_duplicate_overlay_preserves_sandbox_error(tmp_path: Path, mock_deps) -> None:
    _, _, m_run = mock_deps
    worker = {
        "changes": [
            {"path": "tests/test_a.py", "original_sha256": None, "content": "x = 1\n"},
            {"path": "tests/test_a.py", "original_sha256": None, "content": "x = 2\n"},
        ]
    }
    worker_golden = copy.deepcopy(worker)
    with pytest.raises(SandboxError, match="duplicate overlay path"):
        run_candidate_tests(tmp_path, VALID_TASK, worker, VALID_SHA)
    assert m_run.call_count == 0
    assert worker == worker_golden


def test_privacy_marker_and_feedback_integration_at_32_findings(tmp_path: Path, mock_deps) -> None:
    marker = "UNIQUE_SECRET_MARKER_987654321_DO_NOT_LEAK"
    greek_prose = "Ελληνικό κείμενο δοκιμής " * 100
    oversized_body = f"# {marker}\n# {greek_prose}\ndef test_f():\n    pass\n# " + ("z" * 24000)

    changes = [
        {
            "path": f"tests/test_batch_{i}.py",
            "original_sha256": None,
            "content": oversized_body,
        }
        for i in range(32)
    ]
    worker = {"changes": changes}
    receipt = run_candidate_tests(tmp_path, VALID_TASK, worker, VALID_SHA)

    assert receipt["state"] == "FAILED"
    assert receipt["returncode"] == 1
    assert receipt["isolation"] == "static-byte-budget"
    assert marker not in receipt["stdout_tail"]
    assert marker not in receipt["stderr_tail"]
    assert greek_prose not in receipt["stderr_tail"]
    assert len(receipt["stderr_tail"].encode("utf-8")) <= 8192
    assert "tests/test_batch_0.py" in receipt["stderr_tail"]
    assert receipt["stderr_tail"].count("AssertionError:") == 32

    val_receipt = validate_test_receipt(receipt, VALID_SHA)
    assert val_receipt["state"] == "FAILED"

    feedback = pipeline_test_gate_feedback(receipt, VALID_SHA)
    assert feedback["reviewer"] == "test-gate"
    assert feedback["reviewed_proposal_sha256"] == VALID_SHA
    assert marker not in str(feedback)
    assert any("AssertionError:" in finding for finding in feedback["findings"])


def test_static_failed_vs_sandbox_failed_distinction_and_exact_lower_args(
    tmp_path: Path, mock_deps
) -> None:
    _, _, m_run = mock_deps

    m_run.reset_mock()
    normal_receipt = run_candidate_tests(tmp_path, VALID_TASK, VALID_WORKER, VALID_SHA, timeout=600)
    assert normal_receipt["state"] == "PASSED"
    assert normal_receipt["isolation"] == "bwrap"
    m_run.assert_called_once_with(
        tmp_path.resolve(),
        ["tests/test_a.py"],
        timeout=600,
        overlays={"tests/test_a.py": "def test_ok(): pass\n"},
    )

    m_run.reset_mock()
    m_run.return_value = {**VALID_SANDBOX_RESULT, "returncode": 2, "stderr": "test assertion failed"}
    sandbox_failed = run_candidate_tests(tmp_path, VALID_TASK, VALID_WORKER, VALID_SHA)
    assert sandbox_failed["state"] == "FAILED"
    assert sandbox_failed["returncode"] == 2
    assert sandbox_failed["isolation"] == "bwrap"
    assert m_run.call_count == 1

    m_run.reset_mock()
    bad_worker = {"changes": [{"path": "tests/test_over.py", "original_sha256": None, "content": "# " + "x" * 24001}]}
    static_failed = run_candidate_tests(tmp_path, VALID_TASK, bad_worker, VALID_SHA)
    assert static_failed["state"] == "FAILED"
    assert static_failed["returncode"] == 1
    assert static_failed["isolation"] == "static-byte-budget"
    assert m_run.call_count == 0
