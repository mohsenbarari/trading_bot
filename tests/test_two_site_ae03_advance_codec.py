from __future__ import annotations

import copy
from pathlib import Path
from unittest.mock import patch
import pytest

from scripts.two_site_codex_autopilot import (
    CONTROL_PATHS,
    PROTECTED_REFS,
    AutopilotError,
    parse_rescope_receipt,
    serialize_next_rescope_receipt,
    serialize_rescope_receipt,
)

HEAD_A = "1" * 40
HEAD_B = "2" * 40
REF_SHA = "3" * 40
BLOB_SHA = "4" * 40


def _valid_repo_facts(head: str = HEAD_A) -> dict[str, object]:
    return {
        "head": head,
        "status": "",
        "protected_refs": {ref: REF_SHA for ref in sorted(PROTECTED_REFS)},
        "control_blobs": {p.as_posix(): BLOB_SHA for p in CONTROL_PATHS},
        "assignment_scope": {
            "assignment_id": "scope-test-1",
            "stage_id": "P1-00b",
            "sha256": "5" * 64,
            "expires_at": "2026-10-01T00:00:00Z",
            "allowed_paths": ["scripts/two_site_codex_autopilot.py"],
            "forbidden_paths": [],
        },
    }


def _valid_rescope_result(head: str = HEAD_A, summary: str = "Rescope cycle") -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "state": "BLOCKED",
        "disposition": "RESCOPE",
        "category": "INTERNAL_REJECTION",
        "summary": summary,
        "commit": None,
        "next_action": "Narrow changes to allowed scope",
        "blockers": ["Scope boundary violation"],
        "gemini_worker_run_id": None,
        "gemini_reviewer_run_id": None,
        "proposal_sha256": None,
        "head_before": head,
        "head_after": head,
        "head_changed": False,
    }


def test_advance_rescope_receipt_1_to_2_and_2_to_3() -> None:
    repo = _valid_repo_facts(HEAD_A)
    res1 = _valid_rescope_result(HEAD_A, summary="Cycle 1")
    raw1 = serialize_rescope_receipt(res1, repo, attempt=1)
    prev1 = parse_rescope_receipt(raw1, repo)

    # 1 -> 2
    res2 = _valid_rescope_result(HEAD_A, summary="Cycle 2")
    raw2 = serialize_next_rescope_receipt(prev1, res2, repo)
    expected_raw2 = serialize_rescope_receipt(res2, repo, attempt=2)
    assert raw2 == expected_raw2

    parsed2 = parse_rescope_receipt(raw2, repo)
    assert parsed2["attempt"] == 2
    assert parsed2["terminal"] is False
    assert parsed2["result"]["summary"] == "Cycle 2"

    # 2 -> 3 (same result is allowed)
    raw3 = serialize_next_rescope_receipt(parsed2, res2, repo)
    expected_raw3 = serialize_rescope_receipt(res2, repo, attempt=3)
    assert raw3 == expected_raw3

    parsed3 = parse_rescope_receipt(raw3, repo)
    assert parsed3["attempt"] == 3
    assert parsed3["terminal"] is True


def test_terminal_and_invalid_attempt_rejection() -> None:
    repo = _valid_repo_facts(HEAD_A)
    res = _valid_rescope_result(HEAD_A)
    raw3 = serialize_rescope_receipt(res, repo, attempt=3)
    prev3 = parse_rescope_receipt(raw3, repo)

    # Attempt 3 cannot advance
    with pytest.raises(AutopilotError, match="previous attempt must be 1 or 2 to advance"):
        serialize_next_rescope_receipt(prev3, res, repo)

    # Boolean attempt rejected
    raw1 = serialize_rescope_receipt(res, repo, attempt=1)
    prev1 = parse_rescope_receipt(raw1, repo)
    prev_bool = copy.deepcopy(prev1)
    prev_bool["attempt"] = True
    with pytest.raises(AutopilotError):
        serialize_next_rescope_receipt(prev_bool, res, repo)

    # Out of range attempt
    prev_out_of_range = copy.deepcopy(prev1)
    prev_out_of_range["attempt"] = 0
    with pytest.raises(AutopilotError):
        serialize_next_rescope_receipt(prev_out_of_range, res, repo)

    # Forged terminal True on attempt 1
    prev_forged_term = copy.deepcopy(prev1)
    prev_forged_term["terminal"] = True
    with pytest.raises(AutopilotError):
        serialize_next_rescope_receipt(prev_forged_term, res, repo)


def test_stale_repo_and_mismatched_result_rejection() -> None:
    repo_a = _valid_repo_facts(HEAD_A)
    repo_b = _valid_repo_facts(HEAD_B)
    res_a = _valid_rescope_result(HEAD_A)
    res_b = _valid_rescope_result(HEAD_B)

    raw1 = serialize_rescope_receipt(res_a, repo_a, attempt=1)
    prev1 = parse_rescope_receipt(raw1, repo_a)

    # Stale repository compared to previous receipt
    with pytest.raises(AutopilotError, match="receipt repository facts do not match expected repository facts"):
        serialize_next_rescope_receipt(prev1, res_a, repo_b)

    # Result bound to wrong HEAD
    with pytest.raises(AutopilotError, match="head_before and head_after must equal repository head"):
        serialize_next_rescope_receipt(prev1, res_b, repo_a)

    # Repository facts modified
    tampered_repo = copy.deepcopy(repo_a)
    tampered_repo["status"] = "M modified_file.py"
    with pytest.raises(AutopilotError):
        serialize_next_rescope_receipt(prev1, res_a, tampered_repo)


def test_malformed_inputs_and_path_rejection() -> None:
    repo = _valid_repo_facts(HEAD_A)
    res = _valid_rescope_result(HEAD_A)
    raw1 = serialize_rescope_receipt(res, repo, attempt=1)
    prev1 = parse_rescope_receipt(raw1, repo)

    # Path instances rejected anywhere
    with pytest.raises(AutopilotError, match="rescope receipt arguments cannot be Path instances"):
        serialize_next_rescope_receipt(Path("/tmp"), res, repo)
    with pytest.raises(AutopilotError, match="rescope receipt arguments cannot be Path instances"):
        serialize_next_rescope_receipt(prev1, Path("/tmp"), repo)
    with pytest.raises(AutopilotError, match="rescope receipt arguments cannot be Path instances"):
        serialize_next_rescope_receipt(prev1, res, Path("/tmp"))

    # Non-mapping previous
    with pytest.raises(AutopilotError, match="previous rescope receipt must be a mapping"):
        serialize_next_rescope_receipt(["not", "a", "dict"], res, repo)
    with pytest.raises(AutopilotError, match="previous rescope receipt must be a mapping"):
        serialize_next_rescope_receipt(None, res, repo)

    # Malformed previous envelope (missing key / extra key)
    prev_extra = copy.deepcopy(prev1)
    prev_extra["unexpected_field"] = "bad"
    with pytest.raises(AutopilotError):
        serialize_next_rescope_receipt(prev_extra, res, repo)

    # Malformed current result (e.g. commit is present)
    bad_res_commit = copy.deepcopy(res)
    bad_res_commit["commit"] = "a" * 40
    with pytest.raises(AutopilotError):
        serialize_next_rescope_receipt(prev1, bad_res_commit, repo)

    # Malformed current result (empty blockers)
    bad_res_blockers = copy.deepcopy(res)
    bad_res_blockers["blockers"] = []
    with pytest.raises(AutopilotError):
        serialize_next_rescope_receipt(prev1, bad_res_blockers, repo)

    # Malformed current result (state != BLOCKED)
    bad_res_state = copy.deepcopy(res)
    bad_res_state["state"] = "CONTINUE"
    with pytest.raises(AutopilotError):
        serialize_next_rescope_receipt(prev1, bad_res_state, repo)

    # Oversized payload rejected
    oversized_res = copy.deepcopy(res)
    oversized_res["blockers"] = ["x" * 1000 for _ in range(20)]
    with pytest.raises(AutopilotError, match="exceeds 16384 bytes"):
        serialize_next_rescope_receipt(prev1, oversized_res, repo)


def test_input_non_mutation_and_no_file_io() -> None:
    repo = _valid_repo_facts(HEAD_A)
    res = _valid_rescope_result(HEAD_A)
    raw1 = serialize_rescope_receipt(res, repo, attempt=1)
    prev1 = parse_rescope_receipt(raw1, repo)

    prev_snapshot = copy.deepcopy(prev1)
    res_snapshot = copy.deepcopy(res)
    repo_snapshot = copy.deepcopy(repo)

    # Assert no file operation during pure codec execution
    with patch("builtins.open") as mock_builtin_open, patch("os.open") as mock_os_open:
        raw2 = serialize_next_rescope_receipt(prev1, res, repo)
        assert mock_builtin_open.call_count == 0
        assert mock_os_open.call_count == 0

    assert isinstance(raw2, bytes)
    # Assert inputs were never mutated
    assert prev1 == prev_snapshot
    assert res == res_snapshot
    assert repo == repo_snapshot
