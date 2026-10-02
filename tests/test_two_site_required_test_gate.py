"""Tests for required-test coverage gate feedback module.

Pure module does not itself authorize Reviewer; pipeline owns admission, and live acceptance needs separate receipts.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any
import unittest.mock

import pytest

from scripts.two_site_pre_review_receipt import (
    SandboxError,
    run_candidate_tests,
    validate_test_receipt,
)
import scripts.two_site_required_test_gate as gate_mod
from scripts.two_site_required_test_gate import required_selector_gate_feedback


def _make_receipt(
    proposal_sha256: str,
    *,
    state: str = "PASSED",
    returncode: int = 0,
    selectors: list[str] | None = None,
    unsupported_specs: list[str] | None = None,
    stdout_tail: str = "out sentinel",
    stderr_tail: str = "err sentinel",
    snapshot_files: int = 2,
    snapshot_bytes: int = 120,
    isolation: str = "sandboxed",
) -> dict[str, Any]:
    return {
        "state": state,
        "proposal_sha256": proposal_sha256,
        "selectors": ["tests/test_existing.py"] if selectors is None else list(selectors),
        "unsupported_specs": [] if unsupported_specs is None else list(unsupported_specs),
        "returncode": returncode,
        "stdout_tail": stdout_tail,
        "stderr_tail": stderr_tail,
        "snapshot_files": snapshot_files,
        "snapshot_bytes": snapshot_bytes,
        "isolation": isolation,
    }


def test_required_feedback_validates_and_preserves_receipts() -> None:
    sha = "1" * 64
    expected_tokens = ["tests/test_foo.py"]
    expected_json = json.dumps(sorted(set(expected_tokens)), separators=(",", ":")).encode("utf-8")
    expected_hash = hashlib.sha256(expected_json).hexdigest()

    for state, rc in [("PASSED", 0), ("FAILED", 1)]:
        receipt = _make_receipt(sha, state=state, returncode=rc, unsupported_specs=["tests/test_foo.py"])
        orig_receipt = copy.deepcopy(receipt)
        fb = required_selector_gate_feedback(receipt, sha)

        assert receipt == orig_receipt
        assert set(receipt.keys()) == {
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
        }
        assert len(receipt) == 10

        assert fb is not None
        assert set(fb.keys()) == {"reviewer", "reviewed_proposal_sha256", "findings", "test_evidence"}
        assert fb["reviewer"] == "test-gate"
        assert fb["reviewed_proposal_sha256"] == sha
        assert isinstance(fb["findings"], list) and 1 <= len(fb["findings"]) <= 32
        assert all(len(f.encode("utf-8")) <= 2048 for f in fb["findings"])
        assert isinstance(fb["test_evidence"], list) and 1 <= len(fb["test_evidence"]) <= 32
        assert all(len(e.encode("utf-8")) <= 8192 for e in fb["test_evidence"])
        assert expected_hash in fb["findings"][0]

        finding_text = fb["findings"][0]
        evidence_text = fb["test_evidence"][0]
        assert "out sentinel" not in finding_text
        assert "err sentinel" not in finding_text
        assert "out sentinel" not in evidence_text
        assert "err sentinel" not in evidence_text

    valid_receipt = _make_receipt(sha, unsupported_specs=["tests/test_foo.py"])
    with pytest.raises(SandboxError):
        required_selector_gate_feedback(valid_receipt, "2" * 64)
    with pytest.raises(SandboxError):
        required_selector_gate_feedback(valid_receipt, "invalid_sha")

    extra_key_receipt = dict(valid_receipt)
    extra_key_receipt["unexpected_key"] = 99
    with pytest.raises(SandboxError):
        required_selector_gate_feedback(extra_key_receipt, sha)

    missing_key_receipt = dict(valid_receipt)
    del missing_key_receipt["isolation"]
    with pytest.raises(SandboxError):
        required_selector_gate_feedback(missing_key_receipt, sha)

    with pytest.raises(SandboxError):
        required_selector_gate_feedback("not_a_mapping", sha)

    prose_receipt = _make_receipt(sha, unsupported_specs=["please run my tests", "pytest -invalid_flag tests/test_a.py"])
    fb_prose = required_selector_gate_feedback(prose_receipt, sha)
    assert fb_prose is None

    empty_receipt = _make_receipt(sha, unsupported_specs=[])
    fb_empty = required_selector_gate_feedback(empty_receipt, sha)
    assert fb_empty is None

    mixed_receipt = _make_receipt(sha, unsupported_specs=["unsupported prose spec", "tests/test_foo.py"])
    fb_mixed = required_selector_gate_feedback(mixed_receipt, sha)
    assert fb_mixed is not None
    assert set(fb_mixed.keys()) == {"reviewer", "reviewed_proposal_sha256", "findings", "test_evidence"}
    assert expected_hash in fb_mixed["findings"][0]
    assert "unsupported prose spec" not in fb_mixed["findings"][0]


def test_canonical_aliases_share_full_root_identity() -> None:
    sha1 = "a" * 64
    sha2 = "b" * 64
    expected_tokens = ["tests/test_alpha.py", "tests/test_beta.py::test_fn"]
    expected_json = json.dumps(sorted(set(expected_tokens)), separators=(",", ":")).encode("utf-8")
    expected_hash = hashlib.sha256(expected_json).hexdigest()

    r1 = _make_receipt(
        sha1,
        unsupported_specs=["tests/test_alpha.py", "tests/test_beta.py::test_fn"],
    )
    r2 = _make_receipt(
        sha2,
        unsupported_specs=[
            "APP_ENV_FILE=config/unit-test.env.example python3 -m pytest tests.test_alpha",
            "python3 -m unittest -v tests/test_alpha.py tests/test_alpha.py",
            "pytest -q tests/test_beta.py::test_fn tests/test_alpha.py",
            "tests.test_alpha",
            "python3 -m unittest tests.test_alpha",
        ],
    )

    fb1 = required_selector_gate_feedback(r1, sha1)
    fb2 = required_selector_gate_feedback(r2, sha2)

    assert fb1 is not None and fb2 is not None
    assert set(fb1.keys()) == {"reviewer", "reviewed_proposal_sha256", "findings", "test_evidence"}
    assert set(fb2.keys()) == {"reviewer", "reviewed_proposal_sha256", "findings", "test_evidence"}
    assert fb1["reviewer"] == "test-gate"
    assert fb2["reviewer"] == "test-gate"
    assert fb1["reviewed_proposal_sha256"] == sha1
    assert fb2["reviewed_proposal_sha256"] == sha2
    assert expected_hash in fb1["findings"][0]
    assert expected_hash in fb2["findings"][0]
    assert fb1["findings"] == fb2["findings"]


def test_full_union_and_long_prefix_identity_remain_distinct() -> None:
    sha = "3" * 64
    tokens_base = [f"tests/test_suite_{i:02d}.py" for i in range(32)]
    tokens_a = tokens_base + ["tests/test_suite_32.py"]
    tokens_b = tokens_base + ["tests/test_suite_alt.py"]

    hash_a = hashlib.sha256(json.dumps(sorted(set(tokens_a)), separators=(",", ":")).encode("utf-8")).hexdigest()
    hash_b = hashlib.sha256(json.dumps(sorted(set(tokens_b)), separators=(",", ":")).encode("utf-8")).hexdigest()
    assert hash_a != hash_b

    r_a = _make_receipt(sha, unsupported_specs=[" ".join(tokens_a)])
    r_b = _make_receipt(sha, unsupported_specs=[" ".join(tokens_b)])
    fb_a = required_selector_gate_feedback(r_a, sha)
    fb_b = required_selector_gate_feedback(r_b, sha)

    assert fb_a is not None and fb_b is not None
    assert hash_a in fb_a["findings"][0]
    assert hash_b in fb_b["findings"][0]
    assert fb_a["findings"] != fb_b["findings"]

    prefix = "tests/" + "common_prefix_level/" * 100
    tok_p1 = f"{prefix}test_alpha.py"
    tok_p2 = f"{prefix}test_omega.py"

    hash_p1 = hashlib.sha256(json.dumps([tok_p1], separators=(",", ":")).encode("utf-8")).hexdigest()
    hash_p2 = hashlib.sha256(json.dumps([tok_p2], separators=(",", ":")).encode("utf-8")).hexdigest()
    assert hash_p1 != hash_p2

    r_p1 = _make_receipt(sha, unsupported_specs=[tok_p1])
    r_p2 = _make_receipt(sha, unsupported_specs=[tok_p2])
    fb_p1 = required_selector_gate_feedback(r_p1, sha)
    fb_p2 = required_selector_gate_feedback(r_p2, sha)

    assert fb_p1 is not None and fb_p2 is not None
    assert tok_p1 not in fb_p1["findings"][0]
    assert tok_p2 not in fb_p2["findings"][0]
    assert hash_p1 in fb_p1["findings"][0]
    assert hash_p2 in fb_p2["findings"][0]
    assert fb_p1["findings"] != fb_p2["findings"]
    assert len(fb_p1["findings"][0].encode("utf-8")) <= 2048
    assert len(fb_p2["findings"][0].encode("utf-8")) <= 2048
    assert len(fb_p1["test_evidence"][0].encode("utf-8")) <= 8192
    assert len(fb_p2["test_evidence"][0].encode("utf-8")) <= 8192


def test_actual_candidate_receipts_keep_coverage_and_legacy_truth(tmp_path: Path) -> None:
    task = {
        "test_selectors": [
            "tests/test_existing.py",
            "tests/test_required.py::test_required",
        ]
    }
    worker_result1 = {
        "changes": [
            {"path": "src.py", "content": "value = 42\n"},
        ]
    }
    proposal_sha256 = "4" * 64

    fake_sandbox_result = {
        "returncode": 0,
        "stdout": "tests/test_existing.py PASSED\n",
        "stderr": "",
        "snapshot_files": 1,
        "snapshot_bytes": 64,
        "isolation": "sandboxed",
    }

    with unittest.mock.patch("scripts.two_site_pre_review_tests.source_files", return_value=["tests/test_existing.py", "src.py"]):
        with unittest.mock.patch("scripts.two_site_pre_review_receipt.two_site_agent_sandbox.run_tests", return_value=fake_sandbox_result):
            receipt1 = run_candidate_tests(tmp_path, task, worker_result1, proposal_sha256)

    assert set(receipt1.keys()) == {
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
    }
    assert len(receipt1) == 10
    assert receipt1["state"] == "PASSED"
    assert receipt1["returncode"] == 0
    assert receipt1["selectors"] == ["tests/test_existing.py"]
    assert receipt1["unsupported_specs"] == ["tests/test_required.py::test_required"]

    fb1 = required_selector_gate_feedback(receipt1, proposal_sha256)
    assert fb1 is not None
    assert set(fb1.keys()) == {"reviewer", "reviewed_proposal_sha256", "findings", "test_evidence"}
    assert fb1["reviewer"] == "test-gate"
    assert receipt1["state"] == "PASSED"
    assert receipt1["returncode"] == 0

    worker_result2 = {
        "changes": [
            {"path": "src.py", "content": "value = 42\n"},
            {"path": "tests/test_required.py", "content": "def test_required(): pass\n"},
        ]
    }
    with unittest.mock.patch("scripts.two_site_pre_review_tests.source_files", return_value=["tests/test_existing.py", "src.py"]):
        with unittest.mock.patch("scripts.two_site_pre_review_receipt.two_site_agent_sandbox.run_tests", return_value=fake_sandbox_result):
            receipt2 = run_candidate_tests(tmp_path, task, worker_result2, proposal_sha256)

    assert set(receipt2.keys()) == {
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
    }
    assert len(receipt2) == 10
    assert receipt2["state"] == "PASSED"
    assert receipt2["returncode"] == 0
    assert receipt2["selectors"] == ["tests/test_existing.py", "tests/test_required.py::test_required"]
    assert receipt2["unsupported_specs"] == []
    fb2 = required_selector_gate_feedback(receipt2, proposal_sha256)
    assert fb2 is None

    old_paths = [f"tests/test_old_{i:03d}.py" for i in range(195)]
    new_paths = [f"tests/test_new_{i:03d}.py" for i in range(10)]
    inventory_cap = old_paths + new_paths + ["src.py"]
    glob_spec1 = "tests/test_old_*.py"
    glob_spec2 = "tests/test_new_*.py"
    task_cap = {"test_selectors": [glob_spec1, glob_spec2]}

    with unittest.mock.patch("scripts.two_site_pre_review_tests.source_files", return_value=inventory_cap):
        with unittest.mock.patch("scripts.two_site_pre_review_receipt.two_site_agent_sandbox.run_tests", return_value=fake_sandbox_result):
            receipt_cap = run_candidate_tests(tmp_path, task_cap, worker_result1, proposal_sha256)

    assert len(receipt_cap) == 10
    assert len(receipt_cap["selectors"]) == 195
    assert receipt_cap["unsupported_specs"] == [glob_spec2]
    fb_cap = required_selector_gate_feedback(receipt_cap, proposal_sha256)
    assert fb_cap is not None
    assert set(fb_cap.keys()) == {"reviewer", "reviewed_proposal_sha256", "findings", "test_evidence"}
    assert "missing or budget omission" in fb_cap["findings"][0]

    task_no_concrete = {"test_selectors": ["tests/test_nonexistent.py"]}
    with unittest.mock.patch("scripts.two_site_pre_review_tests.source_files", return_value=["tests/test_existing.py", "src.py"]):
        with unittest.mock.patch("scripts.two_site_pre_review_receipt.two_site_agent_sandbox.run_tests", return_value=fake_sandbox_result) as mock_sandbox:
            with pytest.raises(SandboxError, match="at least one focused selector required"):
                run_candidate_tests(tmp_path, task_no_concrete, worker_result1, proposal_sha256)
            assert mock_sandbox.call_count == 0


def test_independent_guard_mutation_and_file_byte_limit(tmp_path: Path) -> None:
    sha = "5" * 64
    task = {
        "test_selectors": [
            "tests/test_existing.py",
            "tests/test_required.py::test_required",
        ]
    }
    worker_result = {
        "changes": [
            {"path": "src.py", "content": "value = 42\n"},
        ]
    }
    fake_sandbox_result = {
        "returncode": 0,
        "stdout": "tests/test_existing.py PASSED\n",
        "stderr": "",
        "snapshot_files": 1,
        "snapshot_bytes": 64,
        "isolation": "sandboxed",
    }
    with unittest.mock.patch("scripts.two_site_pre_review_tests.source_files", return_value=["tests/test_existing.py", "src.py"]):
        with unittest.mock.patch("scripts.two_site_pre_review_receipt.two_site_agent_sandbox.run_tests", return_value=fake_sandbox_result):
            omitted_receipt = run_candidate_tests(tmp_path, task, worker_result, sha)

    assert set(omitted_receipt.keys()) == {
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
    }
    assert len(omitted_receipt) == 10

    expected_token = "tests/test_required.py::test_required"
    expected_hash = hashlib.sha256(
        json.dumps([expected_token], separators=(",", ":")).encode("utf-8")
    ).hexdigest()

    def witness() -> None:
        gate_func = getattr(gate_mod, "required_selector_gate_feedback")
        fb = gate_func(omitted_receipt, sha)
        assert fb is not None
        assert set(fb.keys()) == {"reviewer", "reviewed_proposal_sha256", "findings", "test_evidence"}
        assert fb["reviewer"] == "test-gate"
        assert fb["reviewed_proposal_sha256"] == sha
        assert expected_hash in fb["findings"][0]

    witness()

    with unittest.mock.patch.object(gate_mod, "required_selector_gate_feedback", return_value=None):
        with pytest.raises(AssertionError):
            witness()

    witness()

    file_bytes = Path(__file__).read_bytes()
    assert 0 < len(file_bytes) <= 24000
