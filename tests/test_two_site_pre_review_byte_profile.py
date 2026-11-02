"""Consolidated byte feedback; old receipts and sandbox authority stay unchanged."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from unittest.mock import patch

import pytest

for _path, _cap in ((Path(__file__).resolve(), 24000),
                   (Path(__file__).resolve().parents[1] / "scripts/two_site_pre_review_receipt.py", 48000)):
    with _path.open("rb") as _stream:
        _bytes = _stream.read(_cap + 1)
    assert len(_bytes) == _path.stat().st_size <= _cap

from scripts import two_site_pre_review_receipt as subject
from scripts.two_site_proposal_pipeline import test_gate_feedback as feedback

SHA = "a" * 64
SOURCE, TEST = "scripts/source.py", "tests/test_profile.py"
PROFILE = {"version": "1.0", "file_limits": {SOURCE: 12000, TEST: 24000}, "total_limit": 36000}
SANDBOX = {"returncode": 0, "stdout": "ok", "stderr": "", "snapshot_files": 2, "snapshot_bytes": 100, "isolation": "bwrap"}


def fixtures(profile=PROFILE):
    task = {"requirements": ["ordinary requirement", "PROPOSAL_UTF8_BUDGET_V1:" +
        json.dumps(profile, sort_keys=True, separators=(",", ":"), ensure_ascii=True)],
        "allowed_files": [SOURCE, TEST], "test_selectors": [TEST, "unsupported"]}
    worker = {"changes": [
        {"path": SOURCE, "original_sha256": "b" * 64, "content": "# source\n"},
        {"path": TEST, "original_sha256": None, "content": "def test_ok(): pass\n"}]}
    return task, worker


def expected(stderr="", isolation="bwrap", state="PASSED"):
    return {"state": state, "proposal_sha256": SHA, "selectors": [TEST],
        "unsupported_specs": ["unsupported"], "returncode": 0 if state == "PASSED" else 1,
        "stdout_tail": "ok" if state == "PASSED" else "", "stderr_tail": stderr,
        "snapshot_files": 2 if state == "PASSED" else 0,
        "snapshot_bytes": 100 if state == "PASSED" else 0, "isolation": isolation}


@pytest.fixture
def deps():
    def normalize(specs, inventory):
        return ([TEST], []) if specs == [TEST] else ([], [])
    with (patch.object(subject, "tracked_test_inventory", return_value=[TEST]),
          patch.object(subject, "normalize_test_selectors", side_effect=normalize),
          patch.object(subject.two_site_agent_sandbox, "run_tests", return_value=copy.deepcopy(SANDBOX)) as run):
        yield run


def cycle(root, deps, task, worker, bad_expected):
    good_task, good_worker = fixtures()
    all_inputs = copy.deepcopy([good_task, good_worker, task, worker, bad_expected, SANDBOX])
    deps.reset_mock()
    assert subject.run_candidate_tests(root, good_task, good_worker, SHA) == expected()
    deps.assert_called_once_with(root.resolve(), [TEST], timeout=1200,
        overlays={SOURCE: good_worker["changes"][0]["content"], TEST: good_worker["changes"][1]["content"]})
    deps.reset_mock()
    result = subject.run_candidate_tests(root, task, worker, SHA)
    assert result == bad_expected
    deps.assert_not_called()
    deps.reset_mock()
    assert subject.run_candidate_tests(root, good_task, good_worker, SHA) == expected()
    assert deps.call_count == 1
    assert [good_task, good_worker, task, worker, bad_expected, SANDBOX] == all_inputs
    return result


def test_historical_253_reports_all_three_violations_once(tmp_path, deps):
    task, worker = fixtures()
    worker["changes"][0]["content"] = "#" * 13401
    worker["changes"][1]["content"] = "#" * 28902
    stderr = (f"AssertionError: {SOURCE} exceeds 12000 byte limit: 13401 bytes\n"
              f"AssertionError: {TEST} exceeds 24000 byte limit: 28902 bytes\n"
              "AssertionError: proposal total exceeds 36000 byte limit: 42303 bytes")
    receipt = cycle(tmp_path, deps, task, worker, expected(stderr, "static-byte-profile", "FAILED"))
    result = feedback(receipt, SHA)
    assert result["reviewed_proposal_sha256"] == SHA
    evidence = json.dumps(result)
    for value in (SOURCE, TEST, "13401", "28902", "42303", "36000"):
        assert value in evidence


@pytest.mark.parametrize("bad_declaration", [
    "PROPOSAL_UTF8_BUDGET_V1:{}", "PROPOSAL_UTF8_BUDGET_V1: private marker",
    "PROPOSAL_UTF8_BUDGET_V1:" + "x" * 8193])
def test_malformed_declaration_fixed_receipt_no_echo(tmp_path, deps, bad_declaration):
    task, worker = fixtures()
    task["requirements"][1] = bad_declaration
    receipt = cycle(tmp_path, deps, task, worker,
        expected("invalid proposal UTF-8 budget profile", "static-byte-profile", "FAILED"))
    assert "private marker" not in json.dumps(receipt)


def test_utf8_counts_not_characters_and_sorted_paths(tmp_path, deps):
    task, worker = fixtures()
    worker["changes"].reverse()
    worker["changes"][1]["content"] = "é" * 6001
    cycle(tmp_path, deps, task, worker,
          expected(f"AssertionError: {SOURCE} exceeds 12000 byte limit: 12002 bytes", "static-byte-profile", "FAILED"))


def test_total_only_counts_every_changed_file_including_docs(tmp_path, deps):
    profile = copy.deepcopy(PROFILE)
    profile["file_limits"]["docs/notes.md"] = 100
    profile["total_limit"] = 30
    task, worker = fixtures(profile)
    task["allowed_files"].append("docs/notes.md")
    worker["changes"].append({"path": "docs/notes.md", "original_sha256": None, "content": "#" * 15})
    total = sum(len(c["content"].encode()) for c in worker["changes"])
    cycle(tmp_path, deps, task, worker,
          expected(f"AssertionError: proposal total exceeds 30 byte limit: {total} bytes", "static-byte-profile", "FAILED"))


def test_explicit_new_test_declared_cap_and_legacy_existing_source_remain_distinct(tmp_path, deps):
    profile = copy.deepcopy(PROFILE)
    profile["file_limits"][TEST] = 30000
    profile["total_limit"] = 40000
    task, worker = fixtures(profile)
    worker["changes"][1]["content"] = "#" * 30001
    cycle(tmp_path, deps, task, worker,
          expected(f"AssertionError: {TEST} exceeds 30000 byte limit: 30001 bytes", "static-byte-profile", "FAILED"))
    worker["changes"][1]["content"] = "#" * 24001
    worker["changes"][1]["original_sha256"] = "c" * 64
    deps.reset_mock()
    assert subject.run_candidate_tests(tmp_path, task, worker, SHA) == expected()
    assert deps.call_count == 1


def test_legacy_no_profile_existing_source_and_missing_sha_unchanged(tmp_path, deps):
    task, worker = fixtures()
    task.pop("requirements")
    task.pop("allowed_files")
    worker["changes"][0]["content"] = "#" * 13401
    worker["changes"][1].pop("original_sha256")
    worker["changes"][1]["content"] = "#" * 24001
    frozen = copy.deepcopy([task, worker])
    deps.reset_mock()
    assert subject.run_candidate_tests(tmp_path, task, worker, SHA) == expected()
    assert deps.call_count == 1 and [task, worker] == frozen
    worker["changes"][1]["original_sha256"] = None
    cycle(tmp_path, deps, task, worker,
          expected(f"AssertionError: {TEST} exceeds 24000 byte limit: 24001 bytes", "static-byte-budget", "FAILED"))


def test_profile_inclusive_boundary_and_changed_subset(tmp_path, deps):
    task, worker = fixtures()
    worker["changes"][0]["content"] = "#" * 12000
    worker["changes"][1]["content"] = "#" * 24000
    assert subject.run_candidate_tests(tmp_path, task, worker, SHA) == expected()
    worker["changes"].pop(0)
    assert subject.run_candidate_tests(tmp_path, task, worker, SHA) == expected()


def test_all_32_findings_and_total_fit_without_losing_source(tmp_path, deps):
    paths = [f"scripts/{'x' * 112}{i:02}.py" for i in range(31)] + [TEST]
    assert max(len(p.encode()) for p in paths) <= 128
    profile = {"version": "1.0", "file_limits": dict.fromkeys(paths, 1), "total_limit": 1}
    task, _ = fixtures(profile)
    task["allowed_files"] = paths
    worker = {"changes": [{"path": p, "original_sha256": None, "content": "é"} for p in reversed(paths)]}
    stderr = "\n".join([f"AssertionError: {p} exceeds 1 byte limit: 2 bytes" for p in sorted(paths)] +
                       ["AssertionError: proposal total exceeds 1 byte limit: 64 bytes"])
    assert len(stderr.encode()) < 8192
    result = cycle(tmp_path, deps, task, worker, expected(stderr, "static-byte-profile", "FAILED"))
    correction = feedback(result, SHA)
    assert correction["test_evidence"][-1] == stderr
    assert correction["reviewed_proposal_sha256"] == SHA
