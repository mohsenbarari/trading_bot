from __future__ import annotations

import copy
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

import scripts.two_site_pre_review_receipt
from scripts.two_site_pre_review_receipt import (
    candidate_test_whitespace_findings,
    run_candidate_tests,
)

VALID_SHA = "0" * 64
VALID_SANDBOX_RESULT: dict[str, Any] = {
    "returncode": 0,
    "stdout": "ok",
    "stderr": "",
    "snapshot_files": 1,
    "snapshot_bytes": 10,
    "isolation": "bwrap",
}
EXPECTED_RECEIPT_KEYS = frozenset({
    "state",
    "proposal_sha256",
    "selectors",
    "unsupported_specs",
    "returncode",
    "stdout_tail",
    "stderr_tail",
    "snapshot_files",
    "snapshot_bytes",
    "isolation",
})


@pytest.fixture
def mock_deps():
    with (
        patch(
            "scripts.two_site_pre_review_receipt.tracked_test_inventory",
            side_effect=lambda root, overlays: sorted(overlays),
        ) as m_inv,
        patch(
            "scripts.two_site_pre_review_receipt.normalize_test_selectors",
            side_effect=lambda specs, inv: (list(specs), []),
        ) as m_norm,
        patch(
            "scripts.two_site_pre_review_receipt.two_site_agent_sandbox.run_tests",
            return_value=dict(VALID_SANDBOX_RESULT),
        ) as m_run,
    ):
        yield m_inv, m_norm, m_run


def witness_run_candidate_tests_whitespace_rejection(
    root: Path,
    mock_sandbox: MagicMock,
    task_clean: dict[str, Any],
    worker_clean: dict[str, Any],
    task_bad: dict[str, Any],
    worker_bad: dict[str, Any],
    proposal_sha: str,
) -> None:
    """Reusable public witness establishing positive success and whitespace failure."""
    task_clean_snap = copy.deepcopy(task_clean)
    worker_clean_snap = copy.deepcopy(worker_clean)
    task_bad_snap = copy.deepcopy(task_bad)
    worker_bad_snap = copy.deepcopy(worker_bad)

    mock_sandbox.reset_mock()
    pos_receipt = run_candidate_tests(root, task_clean, worker_clean, proposal_sha)
    assert set(pos_receipt.keys()) == EXPECTED_RECEIPT_KEYS
    assert pos_receipt["state"] == "PASSED"
    assert pos_receipt["returncode"] == 0
    assert pos_receipt["isolation"] == "bwrap"
    assert mock_sandbox.call_count == 1

    mock_sandbox.reset_mock()
    bad_receipt = run_candidate_tests(root, task_bad, worker_bad, proposal_sha)
    assert set(bad_receipt.keys()) == EXPECTED_RECEIPT_KEYS
    assert bad_receipt["state"] == "FAILED"
    assert bad_receipt["returncode"] == 1
    assert bad_receipt["snapshot_files"] == 0
    assert bad_receipt["snapshot_bytes"] == 0
    assert bad_receipt["stdout_tail"] == ""
    assert bad_receipt["isolation"] == "static-new-test-whitespace"
    assert "trailing-whitespace" in bad_receipt["stderr_tail"]
    mock_sandbox.assert_not_called()

    assert task_clean == task_clean_snap
    assert worker_clean == worker_clean_snap
    assert task_bad == task_bad_snap
    assert worker_bad == worker_bad_snap


def test_whitespace_gate_counterproof(tmp_path: Path, mock_deps) -> None:
    _, _, m_run = mock_deps
    task_clean = {"test_selectors": ["tests/test_clean.py"]}
    worker_clean = {
        "changes": [
            {
                "path": "tests/test_clean.py",
                "original_sha256": None,
                "content": "def test_clean():\n    pass\n",
            }
        ]
    }
    task_bad = {"test_selectors": ["tests/test_bad.py"]}
    worker_bad = {
        "changes": [
            {
                "path": "tests/test_bad.py",
                "original_sha256": None,
                "content": "def test_bad(): \n    pass\n",
            }
        ]
    }

    witness_run_candidate_tests_whitespace_rejection(
        tmp_path, m_run, task_clean, worker_clean, task_bad, worker_bad, VALID_SHA
    )

    orig_gate = scripts.two_site_pre_review_receipt.candidate_test_whitespace_findings
    scripts.two_site_pre_review_receipt.candidate_test_whitespace_findings = lambda changes: []
    try:
        with pytest.raises(AssertionError):
            witness_run_candidate_tests_whitespace_rejection(
                tmp_path, m_run, task_clean, worker_clean, task_bad, worker_bad, VALID_SHA
            )
    finally:
        scripts.two_site_pre_review_receipt.candidate_test_whitespace_findings = orig_gate

    m_run.reset_mock()
    witness_run_candidate_tests_whitespace_rejection(
        tmp_path, m_run, task_clean, worker_clean, task_bad, worker_bad, VALID_SHA
    )


@pytest.mark.parametrize(
    ("bad_code", "expected_line_fragment"),
    [
        ("def test_spaces(): \n    pass\n", ": trailing-whitespace on line(s) 1"),
        ("def test_tabs():\t\n    pass\n", ": trailing-whitespace on line(s) 1"),
        ("def test_indent():\n    \n    pass\n", ": trailing-whitespace on line(s) 2"),
        ("def test_multiline():\n    msg = \"\"\"\n    \n    \"\"\"\n", ": trailing-whitespace on line(s) 3"),
    ],
)
def test_whitespace_defect_variants(tmp_path: Path, mock_deps, bad_code: str, expected_line_fragment: str) -> None:
    _, _, m_run = mock_deps
    task = {"test_selectors": ["tests/test_x.py"]}
    worker = {
        "changes": [
            {
                "path": "tests/test_x.py",
                "original_sha256": None,
                "content": bad_code,
            }
        ]
    }
    task_snap = copy.deepcopy(task)
    worker_snap = copy.deepcopy(worker)

    receipt = run_candidate_tests(tmp_path, task, worker, VALID_SHA)
    assert receipt["state"] == "FAILED"
    assert receipt["returncode"] == 1
    assert receipt["isolation"] == "static-new-test-whitespace"
    assert expected_line_fragment in receipt["stderr_tail"]
    assert receipt["snapshot_files"] == 0
    assert receipt["snapshot_bytes"] == 0
    assert receipt["stdout_tail"] == ""
    m_run.assert_not_called()
    assert task == task_snap
    assert worker == worker_snap


@pytest.mark.parametrize(
    "clean_code",
    [
        "def test_ok():\n    pass\n",
        "def test_no_trailing_nl():\n    pass",
    ],
)
def test_clean_tests_pass_without_whitespace_findings(tmp_path: Path, mock_deps, clean_code: str) -> None:
    _, _, m_run = mock_deps
    task = {"test_selectors": ["tests/test_clean.py"]}
    worker = {
        "changes": [
            {
                "path": "tests/test_clean.py",
                "original_sha256": None,
                "content": clean_code,
            }
        ]
    }
    receipt = run_candidate_tests(tmp_path, task, worker, VALID_SHA)
    assert receipt["state"] == "PASSED"
    assert receipt["returncode"] == 0
    assert receipt["isolation"] == "bwrap"
    m_run.assert_called_once_with(
        tmp_path.resolve(), ["tests/test_clean.py"], timeout=1200, overlays={"tests/test_clean.py": clean_code}
    )


def test_unicode_content_and_path(tmp_path: Path, mock_deps) -> None:
    _, _, m_run = mock_deps
    unicode_path = "tests/test_unicode_نام.py"
    unicode_content = "# سلام\nنام = 'علی' \nassert True\n"
    task = {"test_selectors": ["tests/test_unicode.py"]}
    worker = {
        "changes": [
            {
                "path": unicode_path,
                "original_sha256": None,
                "content": unicode_content,
            }
        ]
    }
    receipt = run_candidate_tests(tmp_path, task, worker, VALID_SHA)
    assert receipt["state"] == "FAILED"
    assert receipt["isolation"] == "static-new-test-whitespace"
    assert unicode_path in receipt["stderr_tail"]
    assert "line(s) 2" in receipt["stderr_tail"]
    m_run.assert_not_called()


def test_multiple_new_changes_detected(tmp_path: Path, mock_deps) -> None:
    _, _, m_run = mock_deps
    task = {"test_selectors": ["tests/test_1.py", "tests/test_2.py"]}
    worker = {
        "changes": [
            {
                "path": "tests/test_1.py",
                "original_sha256": None,
                "content": "def test_1(): \n    pass\n",
            },
            {
                "path": "tests/test_2.py",
                "original_sha256": None,
                "content": "def test_2():\t\n    pass\n",
            },
        ]
    }
    receipt = run_candidate_tests(tmp_path, task, worker, VALID_SHA)
    assert receipt["state"] == "FAILED"
    assert receipt["isolation"] == "static-new-test-whitespace"
    assert "tests/test_1.py: trailing-whitespace on line(s) 1" in receipt["stderr_tail"]
    assert "tests/test_2.py: trailing-whitespace on line(s) 1" in receipt["stderr_tail"]
    m_run.assert_not_called()


def test_both_new_and_existing_mix(tmp_path: Path, mock_deps) -> None:
    _, _, m_run = mock_deps
    task = {"test_selectors": ["tests/test_clean.py", "tests/test_existing.py"]}
    worker = {
        "changes": [
            {
                "path": "tests/test_clean.py",
                "original_sha256": None,
                "content": "def test_clean():\n    pass\n",
            },
            {
                "path": "tests/test_existing.py",
                "original_sha256": "1" * 64,
                "content": "def test_existing(): \n    pass\n",
            },
        ]
    }
    receipt = run_candidate_tests(tmp_path, task, worker, VALID_SHA)
    assert receipt["state"] == "PASSED"
    assert receipt["isolation"] == "bwrap"
    m_run.assert_called_once()


@pytest.mark.parametrize(
    "change",
    [
        {"path": "tests/test_legacy.py", "content": "def test_x(): \n    pass\n"},
        {"path": "scripts/test_script.py", "original_sha256": None, "content": "def test_x(): \n    pass\n"},
        {"path": "tests/helper.py", "original_sha256": None, "content": "def helper(): \n    pass\n"},
    ],
)
def test_noncanonical_and_missing_sha_not_blocked(tmp_path: Path, mock_deps, change: dict[str, Any]) -> None:
    _, _, m_run = mock_deps
    task = {"test_selectors": ["tests/test_standard.py"]}
    worker = {"changes": [change]}
    receipt = run_candidate_tests(tmp_path, task, worker, VALID_SHA)
    assert receipt["state"] == "PASSED"
    assert receipt["isolation"] == "bwrap"
    m_run.assert_called_once()


def test_determinism(tmp_path: Path, mock_deps) -> None:
    _, _, m_run = mock_deps
    task = {"test_selectors": ["tests/test_det.py"]}
    worker = {
        "changes": [
            {
                "path": "tests/test_det.py",
                "original_sha256": None,
                "content": "def test_det(): \n    pass\n",
            }
        ]
    }
    receipt1 = run_candidate_tests(tmp_path, task, worker, VALID_SHA)
    receipt2 = run_candidate_tests(tmp_path, task, worker, VALID_SHA)
    assert receipt1 == receipt2
    m_run.assert_not_called()


def test_many_lines_capped_and_no_sentinel_leakage(tmp_path: Path, mock_deps) -> None:
    _, _, m_run = mock_deps
    sentinel = "SECRET_CANDIDATE_SENTINEL_TOKEN_999"
    bad_lines = [f"def test_{idx}(): \n    # {sentinel}\n    pass" for idx in range(25)]
    content = "\n".join(bad_lines) + "\n"
    task = {"test_selectors": ["tests/test_many.py"]}
    worker = {
        "changes": [
            {
                "path": "tests/test_many.py",
                "original_sha256": None,
                "content": content,
            }
        ]
    }
    receipt = run_candidate_tests(tmp_path, task, worker, VALID_SHA)
    assert receipt["state"] == "FAILED"
    assert receipt["isolation"] == "static-new-test-whitespace"
    stderr = receipt["stderr_tail"]
    assert " ... (15 more)" in stderr
    assert sentinel not in stderr
    assert sentinel not in str(receipt)
    assert len(stderr.encode("utf-8")) <= 8192
    m_run.assert_not_called()
