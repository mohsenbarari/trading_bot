"""Hermetic unit tests for scripts/two_site_quota_wait_record.py."""

from __future__ import annotations

import copy
from typing import Any
import pytest

try:
    from scripts.two_site_quota_wait_record import (
        WaitRecordError,
        validate_initial_wait_record,
    )
    from scripts.two_site_quota_wait_schedule import initial_wait_schedule
except ImportError:
    from two_site_quota_wait_record import (
        WaitRecordError,
        validate_initial_wait_record,
    )
    from two_site_quota_wait_schedule import initial_wait_schedule

_CYCLE_ID = "cycle-2026-20260925T010203Z-123"
_REQUEST_ID = "0123456789abcdef0123456789abcdef"
_SHA256_A = "a" * 64
_SHA256_B = "b" * 64
_SHA256_C = "c" * 64
_SHA256_D = "d" * 64
_HEAD_SHA = "e" * 40
_RESCOPE_EPOCH = f"rescope-{'1' * 64}"
_RUN_PREFIX = "ae03.c5b-test"
_WORKER_RUN_ID = "ae03.c5b-worker-0"
_ASSIGNMENT_ID = "assignment-ae03-c5b-001"
_TASK_PATH = ".local/two-site-refactor/tasks/task-156.json"
_CREATED_AT = "2026-09-25T01:02:03Z"
_RESET_AT = "2026-09-25T01:45:00Z"


def _build_valid_fixtures(
    *,
    known_reset: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    evidence: dict[str, Any] = {
        "kind": "QUOTA_RESET_KNOWN" if known_reset else "QUOTA_RESET_UNKNOWN",
        "provider": "gemini",
        "model": "gemini-2.5-pro",
        "conversation_id": "conv-ae03-c5b-001",
        "reset_at_utc": _RESET_AT if known_reset else None,
    }
    schedule = initial_wait_schedule(evidence, _CREATED_AT)

    reservation: dict[str, Any] = {
        "controller_run_id": _CYCLE_ID,
        "run_prefix": _RUN_PREFIX,
        "task_path": _TASK_PATH,
        "task_sha256": _SHA256_A,
        "source_head": _HEAD_SHA,
        "assignment_id": _ASSIGNMENT_ID,
        "assignment_sha256": _SHA256_B,
    }

    quota_attempt: dict[str, Any] = {
        "role": "worker",
        "run_id": _WORKER_RUN_ID,
        "status_sha256": _SHA256_C,
    }

    repository: dict[str, Any] = {
        "head": _HEAD_SHA,
        "status": "",
        "protected_refs": {"refs/heads/main": _HEAD_SHA},
        "control_blobs": {"config/rules.json": _HEAD_SHA},
        "assignment_scope": {
            "assignment_id": _ASSIGNMENT_ID,
            "stage_id": "P1-00b",
            "sha256": _SHA256_B,
            "expires_at": "2026-09-25T05:00:00Z",
            "allowed_paths": ["tests/test_two_site_quota_wait_record.py"],
            "forbidden_paths": ["scripts/two_site_quota_wait_record.py"],
        },
    }

    record: dict[str, Any] = {
        "schema_version": "1.0",
        "state": "WAITING_QUOTA",
        "controller_run_id": _CYCLE_ID,
        "request_id": _REQUEST_ID,
        "reservation": reservation,
        "request_sha256": _SHA256_A,
        "terminal_receipt_sha256": _SHA256_B,
        "response_sha256": _SHA256_C,
        "semantic_task_fingerprint": _SHA256_D,
        "rescope_epoch": _RESCOPE_EPOCH,
        "rescope_attempt": 1,
        "quota_attempt": quota_attempt,
        "quota_evidence": evidence,
        "schedule": schedule,
        "repository": repository,
    }

    expected_keys = (
        "controller_run_id",
        "request_id",
        "reservation",
        "request_sha256",
        "terminal_receipt_sha256",
        "response_sha256",
        "semantic_task_fingerprint",
        "rescope_epoch",
        "rescope_attempt",
        "quota_attempt",
        "quota_evidence",
        "repository",
    )
    expected = {k: copy.deepcopy(record[k]) for k in expected_keys}

    return record, expected


def test_valid_record_unknown_reset_success_and_independence() -> None:
    record, expected = _build_valid_fixtures(known_reset=False)
    assert len(record) == 15
    assert len(expected) == 12

    record_snapshot = copy.deepcopy(record)
    expected_snapshot = copy.deepcopy(expected)

    result = validate_initial_wait_record(record, expected)

    assert result == record
    assert result is not record
    assert result["reservation"] is not record["reservation"]
    assert result["schedule"] is not record["schedule"]
    assert result["repository"] is not record["repository"]
    assert result["schedule"]["reset_kind"] == "QUOTA_RESET_UNKNOWN"
    assert result["schedule"]["remaining_check_budget"] == 5
    assert result["schedule"]["remaining_resume_budget"] == 1

    assert record == record_snapshot
    assert expected == expected_snapshot


def test_valid_record_known_reset_success() -> None:
    record, expected = _build_valid_fixtures(known_reset=True)
    result = validate_initial_wait_record(record, expected)

    assert result["schedule"]["reset_kind"] == "QUOTA_RESET_KNOWN"
    assert result["schedule"]["reset_at_utc"] == _RESET_AT
    assert result["schedule"]["remaining_check_budget"] == 0
    assert result["schedule"]["remaining_resume_budget"] == 1


def test_explicit_acceptance_of_dots_in_run_prefix_and_run_id() -> None:
    """Explicitly verify dotted identifiers prevent task154 false-negative recurrence."""
    record, expected = _build_valid_fixtures()
    dotted_prefix = "ae03.c5b-test.sub.v1"
    dotted_run_id = "ae03.c5b-worker-0.step.42"

    record["reservation"]["run_prefix"] = dotted_prefix
    expected["reservation"]["run_prefix"] = dotted_prefix
    record["quota_attempt"]["run_id"] = dotted_run_id
    expected["quota_attempt"]["run_id"] = dotted_run_id

    result = validate_initial_wait_record(record, expected)
    assert result["reservation"]["run_prefix"] == dotted_prefix
    assert result["quota_attempt"]["run_id"] == dotted_run_id


def test_verifier_role_accepted() -> None:
    record, expected = _build_valid_fixtures()
    record["quota_attempt"]["role"] = "verifier"
    expected["quota_attempt"]["role"] = "verifier"
    result = validate_initial_wait_record(record, expected)
    assert result["quota_attempt"]["role"] == "verifier"


@pytest.mark.parametrize(
    ("mutator", "match_snippet"),
    [
        # Top-level record and expected shape
        (lambda r, e: r.pop("schema_version"), "record keys must exactly match"),
        (lambda r, e: r.__setitem__("extra_key", 1), "record keys must exactly match"),
        (lambda r, e: e.pop("controller_run_id"), "expected keys must exactly match"),
        (lambda r, e: e.__setitem__("extra_key", 1), "expected keys must exactly match"),
        (lambda r, e: (r.__setitem__("schema_version", "2.0")), "schema_version must be '1.0'"),
        (lambda r, e: (r.__setitem__("schema_version", 1.0)), "schema_version must be '1.0'"),
        (lambda r, e: (r.__setitem__("state", "RUNNING")), "state must be 'WAITING_QUOTA'"),
        (lambda r, e: (r.__setitem__("state", None)), "state must be 'WAITING_QUOTA'"),
        # Maintenance / invalid controller run ID
        (lambda r, e: r.__setitem__("controller_run_id", "maint-2026-20260925T010203Z-123"), "controller_run_id must match cycle run pattern"),
        (lambda r, e: r.__setitem__("controller_run_id", "cycle-bad"), "controller_run_id must match cycle run pattern"),
        (lambda r, e: r.__setitem__("controller_run_id", 12345), "controller_run_id must match cycle run pattern"),
        # Request ID and top-level digests
        (lambda r, e: r.__setitem__("request_id", "NOT_HEX_32_CHARS_LONG_XXXXXXXXXX"), "request_id must be 32 lowercase hex"),
        (lambda r, e: r.__setitem__("request_id", "0123456789ABCDEF0123456789ABCDEF"), "request_id must be 32 lowercase hex"),
        (lambda r, e: r.__setitem__("request_id", "1234"), "request_id must be 32 lowercase hex"),
        (lambda r, e: r.__setitem__("request_sha256", "bad"), "request_sha256 must be 64 lowercase hex"),
        (lambda r, e: r.__setitem__("terminal_receipt_sha256", "G" * 64), "terminal_receipt_sha256 must be 64 lowercase hex"),
        (lambda r, e: r.__setitem__("response_sha256", ""), "response_sha256 must be 64 lowercase hex"),
        (lambda r, e: r.__setitem__("semantic_task_fingerprint", 12345), "semantic_task_fingerprint must be 64 lowercase hex"),
        # Rescope epoch and attempt
        (lambda r, e: r.__setitem__("rescope_epoch", "invalid-epoch"), "rescope_epoch must match"),
        (lambda r, e: r.__setitem__("rescope_epoch", f"rescope-{'A' * 64}"), "rescope_epoch must match"),
        (lambda r, e: r.__setitem__("rescope_attempt", 0), "rescope_attempt must be int in range 1..3"),
        (lambda r, e: r.__setitem__("rescope_attempt", 4), "rescope_attempt must be int in range 1..3"),
        (lambda r, e: r.__setitem__("rescope_attempt", True), "rescope_attempt must be int in range 1..3"),
        (lambda r, e: r.__setitem__("rescope_attempt", "1"), "rescope_attempt must be int in range 1..3"),
        # Reservation trust boundary
        (lambda r, e: r["reservation"].pop("run_prefix"), "reservation must contain exact seven reservation keys"),
        (lambda r, e: r["reservation"].__setitem__("controller_run_id", "cycle-2026-20260925T010203Z-999"), "reservation controller_run_id does not match outer"),
        (lambda r, e: r["reservation"].__setitem__("run_prefix", ".leading_dot"), "reservation run_prefix is invalid"),
        (lambda r, e: r["reservation"].__setitem__("run_prefix", "invalid prefix with spaces"), "reservation run_prefix is invalid"),
        (lambda r, e: r["reservation"].__setitem__("task_path", "/absolute/path/task.json"), "reservation task_path is not canonical relative"),
        (lambda r, e: r["reservation"].__setitem__("task_path", "rel\\with\\backslash.json"), "reservation task_path is not canonical relative"),
        (lambda r, e: r["reservation"].__setitem__("task_path", ".local/two-site-refactor/tasks/"), "reservation task_path is not canonical relative"),
        (lambda r, e: r["reservation"].__setitem__("task_path", ".local/two-site-refactor/tasks/../task.json"), "reservation task_path contains empty or dot components"),
        (lambda r, e: r["reservation"].__setitem__("task_path", ".local/other/tasks/task.json"), "reservation task_path must be under .local/two-site-refactor/tasks"),
        (lambda r, e: r["reservation"].__setitem__("task_sha256", "bad_sha"), "reservation task_sha256 must be 64 lowercase hex"),
        (lambda r, e: r["reservation"].__setitem__("source_head", "bad_head"), "reservation source_head must be 40 lowercase hex"),
        (lambda r, e: r["reservation"].__setitem__("assignment_id", "inv@lid!"), "reservation assignment_id is invalid"),
        (lambda r, e: r["reservation"].__setitem__("assignment_sha256", "not_64_hex"), "reservation assignment_sha256 must be 64 lowercase hex"),
        # Quota attempt
        (lambda r, e: r["quota_attempt"].pop("role"), "quota_attempt must contain exact keys"),
        (lambda r, e: r["quota_attempt"].__setitem__("role", "worker_alias"), "quota_attempt role must be 'worker' or 'verifier'"),
        (lambda r, e: r["quota_attempt"].__setitem__("role", ["worker"]), "quota_attempt role must be 'worker' or 'verifier'"),
        (lambda r, e: r["quota_attempt"].__setitem__("run_id", ".invalid_run_id"), "quota_attempt run_id is invalid"),
        (lambda r, e: r["quota_attempt"].__setitem__("run_id", 12345), "quota_attempt run_id is invalid"),
        (lambda r, e: r["quota_attempt"].__setitem__("status_sha256", "f" * 63), "quota_attempt status_sha256 must be 64 lowercase hex"),
        # Quota evidence
        (lambda r, e: r["quota_evidence"].pop("kind"), "quota_evidence must contain exact five producer keys"),
        (lambda r, e: r["quota_evidence"].__setitem__("kind", "QUOTA_UNKNOWN_ALIAS"), "schedule recomputation failed"),
        (lambda r, e: r["quota_evidence"].__setitem__("reset_at_utc", "2026-09-25T02:00:00Z"), "schedule recomputation failed"),
        # Schedule tampering / inflation / bool type confusion
        (lambda r, e: r["schedule"].pop("remaining_check_budget"), "schedule must contain exact six schedule keys"),
        (lambda r, e: r["schedule"].__setitem__("next_check_at_utc", "2099-01-01T00:00:00Z"), "schedule does not match type-sensitive recomputation"),
        (lambda r, e: r["schedule"].__setitem__("remaining_check_budget", 10), "schedule does not match type-sensitive recomputation"),
        (lambda r, e: r["schedule"].__setitem__("remaining_check_budget", True), "schedule does not match type-sensitive recomputation"),
        (lambda r, e: r["schedule"].__setitem__("remaining_resume_budget", 2), "schedule does not match type-sensitive recomputation"),
        (lambda r, e: r["schedule"].__setitem__("remaining_resume_budget", True), "schedule does not match type-sensitive recomputation"),
        # Repository mismatch
        (lambda r, e: r["repository"].pop("status"), "repository must contain exact five repository keys"),
        (lambda r, e: r["repository"].__setitem__("status", "dirty"), "repository status must be ''"),
        (lambda r, e: r["repository"].__setitem__("head", "0" * 40), "repository head does not match reservation source_head"),
        (lambda r, e: r["repository"].__setitem__("protected_refs", {}), "repository protected_refs must be a nonempty dict"),
        (lambda r, e: r["repository"]["protected_refs"].__setitem__("", _HEAD_SHA), "protected_refs ref name must be a nonempty string"),
        (lambda r, e: r["repository"]["protected_refs"].__setitem__("refs/heads/main", "bad_sha"), "sha must be 40 lowercase hex"),
        (lambda r, e: r["repository"].__setitem__("control_blobs", {}), "repository control_blobs must be a nonempty dict"),
        (lambda r, e: r["repository"]["control_blobs"].__setitem__("config.json", "bad_sha"), "sha must be 40 lowercase hex"),
        (lambda r, e: r["repository"]["assignment_scope"].pop("stage_id"), "assignment_scope must contain exact six keys"),
        (lambda r, e: r["repository"]["assignment_scope"].__setitem__("assignment_id", "mismatch-id"), "assignment_scope assignment_id does not match reservation"),
        (lambda r, e: r["repository"]["assignment_scope"].__setitem__("sha256", "f" * 64), "assignment_scope sha256 does not match reservation assignment_sha256"),
        (lambda r, e: r["repository"]["assignment_scope"].__setitem__("allowed_paths", [123]), "assignment_scope allowed_paths must be a list of strings"),
    ],
)
def test_fail_closed_record_mutations(mutator: Any, match_snippet: str) -> None:
    record, expected = _build_valid_fixtures()
    mutator(record, expected)
    with pytest.raises(WaitRecordError, match=match_snippet):
        validate_initial_wait_record(record, expected)


@pytest.mark.parametrize(
    ("drift_mutator", "match_snippet"),
    [
        (lambda e: e.__setitem__("request_id", "f" * 32), "does not match expected"),
        (lambda e: e.__setitem__("rescope_attempt", 2), "does not match expected"),
        (lambda e: e.__setitem__("rescope_attempt", True), "does not match expected"),
        (lambda e: e["reservation"].__setitem__("run_prefix", "ae03.c5b-other"), "does not match expected"),
        (lambda e: e["quota_attempt"].__setitem__("run_id", "ae03.c5b-worker-1"), "does not match expected"),
        (lambda e: e["repository"]["assignment_scope"].__setitem__("stage_id", "P1-00a"), "does not match expected"),
    ],
)
def test_expected_identity_drift_rejections(drift_mutator: Any, match_snippet: str) -> None:
    record, expected = _build_valid_fixtures()
    drift_mutator(expected)
    with pytest.raises(WaitRecordError, match=match_snippet):
        validate_initial_wait_record(record, expected)


def test_non_dict_inputs_rejected() -> None:
    record, expected = _build_valid_fixtures()
    with pytest.raises(WaitRecordError, match="record must be dict"):
        validate_initial_wait_record(["not", "a", "dict"], expected)
    with pytest.raises(WaitRecordError, match="expected must be dict"):
        validate_initial_wait_record(record, ("not", "a", "dict"))
