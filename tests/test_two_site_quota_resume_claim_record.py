"""Hermetic unit tests for scripts/two_site_quota_resume_claim_record.py."""

from __future__ import annotations

import copy
from typing import Any
import pytest

try:
    from scripts.two_site_quota_resume_claim_record import (
        ResumeClaimRecordError,
        validate_known_reset_resume_claim,
    )
    from tests.test_two_site_quota_wait_record import _build_valid_fixtures
except ImportError:
    from two_site_quota_resume_claim_record import (
        ResumeClaimRecordError,
        validate_known_reset_resume_claim,
    )
    from test_two_site_quota_wait_record import _build_valid_fixtures


def _build_valid_claim_fixtures() -> tuple[dict[str, Any], dict[str, Any]]:
    wait_record, _ = _build_valid_fixtures(known_reset=True)
    res = wait_record["reservation"]
    schedule = wait_record["schedule"]
    next_check = schedule["next_check_at_utc"]
    claimed_at = "2026-09-25T01:46:00Z"
    continuation_cid = "cycle-2026-20260925T014500Z-999"
    resume_rid = "f" * 32
    orig_wait_sha = "9" * 64
    task_id = "task-156"
    orig_rid = wait_record["request_id"]
    resume_prefix = f"qresume-{orig_rid}"

    claim: dict[str, Any] = {
        "schema_version": "1.0",
        "state": "RESUME_CLAIMED",
        "original_controller_run_id": wait_record["controller_run_id"],
        "continuation_controller_run_id": continuation_cid,
        "original_request_id": orig_rid,
        "resume_request_id": resume_rid,
        "original_wait_sha256": orig_wait_sha,
        "semantic_task_fingerprint": wait_record["semantic_task_fingerprint"],
        "task_id": task_id,
        "task_path": res["task_path"],
        "task_sha256": res["task_sha256"],
        "source_head": res["source_head"],
        "assignment_id": res["assignment_id"],
        "assignment_sha256": res["assignment_sha256"],
        "original_run_prefix": res["run_prefix"],
        "resume_run_prefix": resume_prefix,
        "role": wait_record["quota_attempt"]["role"],
        "reset_kind": schedule["reset_kind"],
        "next_check_at_utc": next_check,
        "claimed_at_utc": claimed_at,
        "remaining_resume_budget_before": 1,
        "remaining_resume_budget_after": 0,
    }

    # Construct expected independently without copying claim container
    expected: dict[str, Any] = {
        "original_controller_run_id": wait_record["controller_run_id"],
        "continuation_controller_run_id": continuation_cid,
        "original_request_id": orig_rid,
        "resume_request_id": resume_rid,
        "original_wait_sha256": orig_wait_sha,
        "semantic_task_fingerprint": wait_record["semantic_task_fingerprint"],
        "task_id": task_id,
        "task_path": res["task_path"],
        "task_sha256": res["task_sha256"],
        "source_head": res["source_head"],
        "assignment_id": res["assignment_id"],
        "assignment_sha256": res["assignment_sha256"],
        "original_run_prefix": res["run_prefix"],
        "resume_run_prefix": resume_prefix,
        "role": wait_record["quota_attempt"]["role"],
        "reset_kind": schedule["reset_kind"],
        "next_check_at_utc": next_check,
        "claimed_at_utc": claimed_at,
        "remaining_resume_budget_before": 1,
        "remaining_resume_budget_after": 0,
    }
    return claim, expected


def test_valid_roundtrip_and_no_mutation() -> None:
    claim, expected = _build_valid_claim_fixtures()
    claim_snapshot = copy.deepcopy(claim)
    expected_snapshot = copy.deepcopy(expected)

    result = validate_known_reset_resume_claim(claim, expected)

    assert result == claim
    assert result is not claim
    assert claim == claim_snapshot
    assert expected == expected_snapshot

    # Detached copy: mutation does not bleed back
    result["state"] = "MUTATED"
    assert claim["state"] == "RESUME_CLAIMED"


def test_verifier_role_and_exact_next_check_claimed_at_accepted() -> None:
    claim, expected = _build_valid_claim_fixtures()
    claim["role"] = "verifier"
    expected["role"] = "verifier"
    claim["claimed_at_utc"] = claim["next_check_at_utc"]
    expected["claimed_at_utc"] = expected["next_check_at_utc"]

    result = validate_known_reset_resume_claim(claim, expected)
    assert result["role"] == "verifier"
    assert result["claimed_at_utc"] == result["next_check_at_utc"]


def test_non_dict_inputs_rejected() -> None:
    claim, expected = _build_valid_claim_fixtures()
    with pytest.raises(ResumeClaimRecordError, match="claim must be dict"):
        validate_known_reset_resume_claim(["not", "a", "dict"], expected)
    with pytest.raises(ResumeClaimRecordError, match="expected must be dict"):
        validate_known_reset_resume_claim(claim, ("not", "a", "dict"))


@pytest.mark.parametrize(
    ("mutator", "match_snippet"),
    [
        # Key count and identity
        (lambda c, e: c.pop("schema_version"), "claim keys mismatch"),
        (lambda c, e: c.__setitem__("extra_key", 1), "claim keys mismatch"),
        (lambda c, e: (c.__setitem__("schema_version", "2.0"), e), "schema_version must be '1.0'"),
        (lambda c, e: (c.__setitem__("schema_version", 1.0), e), "schema_version must be '1.0'"),
        (lambda c, e: (c.__setitem__("state", "WAITING_QUOTA"), e), "state must be 'RESUME_CLAIMED'"),
        (lambda c, e: (c.__setitem__("state", None), e), "state must be 'RESUME_CLAIMED'"),
        # Controller run ID matching and collision
        (lambda c, e: (c.__setitem__("original_controller_run_id", "bad"), e.__setitem__("original_controller_run_id", "bad")), "original_controller_run_id must match cycle run pattern"),
        (lambda c, e: (c.__setitem__("continuation_controller_run_id", "bad"), e.__setitem__("continuation_controller_run_id", "bad")), "continuation_controller_run_id must match cycle run pattern"),
        (lambda c, e: (c.__setitem__("continuation_controller_run_id", c["original_controller_run_id"]), e.__setitem__("continuation_controller_run_id", c["original_controller_run_id"])), "continuation_controller_run_id must differ from original_controller_run_id"),
        # Request IDs
        (lambda c, e: (c.__setitem__("original_request_id", "NOT_HEX_32_CHARS_LONG_XXXXXXXXXX"), e.__setitem__("original_request_id", "NOT_HEX_32_CHARS_LONG_XXXXXXXXXX")), "original_request_id must be 32 lowercase hex"),
        (lambda c, e: (c.__setitem__("resume_request_id", "0123456789ABCDEF0123456789ABCDEF"), e.__setitem__("resume_request_id", "0123456789ABCDEF0123456789ABCDEF")), "resume_request_id must be 32 lowercase hex"),
        (lambda c, e: (c.__setitem__("resume_request_id", c["original_request_id"]), e.__setitem__("resume_request_id", c["original_request_id"])), "resume_request_id must differ from original_request_id"),
        # 64-hex and 40-hex digests
        (lambda c, e: (c.__setitem__("original_wait_sha256", "bad"), e.__setitem__("original_wait_sha256", "bad")), "original_wait_sha256 must be 64 lowercase hex"),
        (lambda c, e: (c.__setitem__("semantic_task_fingerprint", "G" * 64), e.__setitem__("semantic_task_fingerprint", "G" * 64)), "semantic_task_fingerprint must be 64 lowercase hex"),
        (lambda c, e: (c.__setitem__("task_sha256", "f" * 63), e.__setitem__("task_sha256", "f" * 63)), "task_sha256 must be 64 lowercase hex"),
        (lambda c, e: (c.__setitem__("assignment_sha256", 12345), e.__setitem__("assignment_sha256", 12345)), "assignment_sha256 must be 64 lowercase hex"),
        (lambda c, e: (c.__setitem__("source_head", "e" * 39), e.__setitem__("source_head", "e" * 39)), "source_head must be 40 lowercase hex"),
        (lambda c, e: (c.__setitem__("source_head", "E" * 40), e.__setitem__("source_head", "E" * 40)), "source_head must be 40 lowercase hex"),
        # Canonical task_path
        (lambda c, e: (c.__setitem__("task_path", "/abs/path/task.json"), e.__setitem__("task_path", "/abs/path/task.json")), "task_path is not canonical relative"),
        (lambda c, e: (c.__setitem__("task_path", "rel\\\\\\\\with\\\\\\\\backslash.json"), e.__setitem__("task_path", "rel\\\\\\\\with\\\\\\\\backslash.json")), "task_path is not canonical relative"),
        (lambda c, e: (c.__setitem__("task_path", ".local/two-site-refactor/tasks/"), e.__setitem__("task_path", ".local/two-site-refactor/tasks/")), "task_path is not canonical relative"),
        (lambda c, e: (c.__setitem__("task_path", ".local/two-site-refactor/tasks/../task.json"), e.__setitem__("task_path", ".local/two-site-refactor/tasks/../task.json")), "task_path contains empty or dot components"),
        (lambda c, e: (c.__setitem__("task_path", ".local/other/tasks/task.json"), e.__setitem__("task_path", ".local/other/tasks/task.json")), "task_path must be under .local/two-site-refactor/tasks"),
        # Run prefixes
        (lambda c, e: (c.__setitem__("original_run_prefix", ".leading_dot"), e.__setitem__("original_run_prefix", ".leading_dot")), "original_run_prefix is invalid"),
        (lambda c, e: (c.__setitem__("resume_run_prefix", "resume-alias"), e.__setitem__("resume_run_prefix", "resume-alias")), "resume_run_prefix must be exactly"),
        (lambda c, e: (c.__setitem__("resume_run_prefix", f"qresume-{c['resume_request_id']}"), e.__setitem__("resume_run_prefix", f"qresume-{c['resume_request_id']}")), "resume_run_prefix must be exactly"),
        # Canonical identifiers
        (lambda c, e: (c.__setitem__("task_id", "inv@lid!"), e.__setitem__("task_id", "inv@lid!")), "task_id is invalid"),
        (lambda c, e: (c.__setitem__("assignment_id", "-leading-hyphen"), e.__setitem__("assignment_id", "-leading-hyphen")), "assignment_id is invalid"),
        # Role
        (lambda c, e: (c.__setitem__("role", "buyer"), e.__setitem__("role", "buyer")), "role must be 'worker' or 'verifier'"),
        # reset_kind: known only
        (lambda c, e: (c.__setitem__("reset_kind", "QUOTA_RESET_UNKNOWN"), e.__setitem__("reset_kind", "QUOTA_RESET_UNKNOWN")), "reset_kind must be 'QUOTA_RESET_KNOWN'"),
        # Timestamps: premature, naive, format
        (lambda c, e: (c.__setitem__("claimed_at_utc", "2026-09-25T01:44:59Z"), e.__setitem__("claimed_at_utc", "2026-09-25T01:44:59Z")), "cannot be earlier than"),
        (lambda c, e: (c.__setitem__("claimed_at_utc", "2026-09-25T01:45:10"), e.__setitem__("claimed_at_utc", "2026-09-25T01:45:10")), "claimed_at_utc invalid"),
        (lambda c, e: (c.__setitem__("next_check_at_utc", "invalid-date"), e.__setitem__("next_check_at_utc", "invalid-date")), "next_check_at_utc invalid"),
        # Budgets: ints 1 then 0, rejecting bool type confusion
        (lambda c, e: (c.__setitem__("remaining_resume_budget_before", 2), e.__setitem__("remaining_resume_budget_before", 2)), "remaining_resume_budget_before must be int 1"),
        (lambda c, e: (c.__setitem__("remaining_resume_budget_before", True), e.__setitem__("remaining_resume_budget_before", True)), "remaining_resume_budget_before must be int 1"),
        (lambda c, e: (c.__setitem__("remaining_resume_budget_after", 1), e.__setitem__("remaining_resume_budget_after", 1)), "remaining_resume_budget_after must be int 0"),
        (lambda c, e: (c.__setitem__("remaining_resume_budget_after", False), e.__setitem__("remaining_resume_budget_after", False)), "remaining_resume_budget_after must be int 0"),
    ],
)
def test_fail_closed_on_claim_mutations(mutator: Any, match_snippet: str) -> None:
    claim, expected = _build_valid_claim_fixtures()
    mutator(claim, expected)
    with pytest.raises(ResumeClaimRecordError, match=match_snippet):
        validate_known_reset_resume_claim(claim, expected)


@pytest.mark.parametrize(
    ("drift_mutator", "match_snippet"),
    [
        (lambda e: e.pop("task_id"), "expected keys mismatch"),
        (lambda e: e.__setitem__("extra_key", "foo"), "expected keys mismatch"),
        (lambda e: e.__setitem__("original_controller_run_id", "cycle-2026-20260925T010203Z-999"), "mismatch"),
        (lambda e: e.__setitem__("continuation_controller_run_id", "cycle-2026-20260925T014500Z-000"), "mismatch"),
        (lambda e: e.__setitem__("original_request_id", "0" * 32), "mismatch"),
        (lambda e: e.__setitem__("resume_request_id", "0" * 32), "mismatch"),
        (lambda e: e.__setitem__("original_wait_sha256", "0" * 64), "mismatch"),
        (lambda e: e.__setitem__("semantic_task_fingerprint", "0" * 64), "mismatch"),
        (lambda e: e.__setitem__("task_id", "task-999"), "mismatch"),
        (lambda e: e.__setitem__("task_path", ".local/two-site-refactor/tasks/task-999.json"), "mismatch"),
        (lambda e: e.__setitem__("task_sha256", "0" * 64), "mismatch"),
        (lambda e: e.__setitem__("source_head", "0" * 40), "mismatch"),
        (lambda e: e.__setitem__("assignment_id", "assignment-ae03-c5b-999"), "mismatch"),
        (lambda e: e.__setitem__("assignment_sha256", "0" * 64), "mismatch"),
        (lambda e: e.__setitem__("original_run_prefix", "ae03.c5b-other"), "mismatch"),
        (lambda e: e.__setitem__("resume_run_prefix", "qresume-other"), "mismatch"),
        (lambda e: e.__setitem__("role", "verifier"), "mismatch"),
        (lambda e: e.__setitem__("reset_kind", "QUOTA_RESET_UNKNOWN"), "mismatch"),
        (lambda e: e.__setitem__("next_check_at_utc", "2026-09-25T02:00:00Z"), "mismatch"),
        (lambda e: e.__setitem__("claimed_at_utc", "2026-09-25T02:00:00Z"), "mismatch"),
        (lambda e: e.__setitem__("remaining_resume_budget_before", 2), "mismatch"),
        (lambda e: e.__setitem__("remaining_resume_budget_before", True), "mismatch"),
        (lambda e: e.__setitem__("remaining_resume_budget_after", 1), "mismatch"),
        (lambda e: e.__setitem__("remaining_resume_budget_after", False), "mismatch"),
    ],
)
def test_expected_identity_and_type_drift_rejected(drift_mutator: Any, match_snippet: str) -> None:
    claim, expected = _build_valid_claim_fixtures()
    drift_mutator(expected)
    with pytest.raises(ResumeClaimRecordError, match=match_snippet):
        validate_known_reset_resume_claim(claim, expected)
