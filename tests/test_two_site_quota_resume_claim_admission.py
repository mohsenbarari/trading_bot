"""Hermetic unit tests for scripts/two_site_quota_resume_claim_admission.py."""

from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
from typing import Any
import pytest

try:
    from scripts.two_site_quota_resume_claim_admission import (
        ClaimAdmissionError,
        prepare_known_reset_resume_claim,
    )
    from scripts.two_site_quota_resume_claim_record import (
        ResumeClaimRecordError,
        validate_known_reset_resume_claim,
    )
    from scripts.two_site_quota_wait_schedule import initial_wait_schedule
except ImportError:
    from two_site_quota_resume_claim_admission import (
        ClaimAdmissionError,
        prepare_known_reset_resume_claim,
    )
    from two_site_quota_resume_claim_record import (
        ResumeClaimRecordError,
        validate_known_reset_resume_claim,
    )
    from two_site_quota_wait_schedule import initial_wait_schedule

_CYCLE_ID = "cycle-2026-20260925T010203Z-123"
_CONT_CYCLE_ID = "cycle-2026-20260925T020304Z-456"
_REQUEST_ID = "0123456789abcdef0123456789abcdef"
_RESUME_REQUEST_ID = "fedcba9876543210fedcba9876543210"
_SHA256_WAIT = "1" * 64
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
_TASK_ID = "task-ae03-c5b-001"
_CREATED_AT = "2026-09-25T01:02:03Z"
_RESET_AT = "2026-09-25T01:45:00Z"


def _step_iso_seconds(iso_str: str, seconds: int) -> str:
    clean = iso_str[:-1] + "+00:00" if iso_str.endswith("Z") else iso_str
    dt = datetime.fromisoformat(clean) + timedelta(seconds=seconds)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _build_valid_wait_fixtures(
    *,
    known_reset: bool = True,
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


def _build_valid_semantic_attempt() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "fingerprint": _SHA256_D,
        "source_sha256": _SHA256_A,
        "stage_id": "P1-00b",
        "gap_reference": "docs/refactor/two-server/AE03_C5_EXECUTION_GATES_20260925.md#small-implementation-units-and-acceptance",
        "scenario_key": "ae03.c5d.known-reset-pure-claim-admission-20260925-205",
        "task_id": _TASK_ID,
        "run_prefix": _RUN_PREFIX,
        "state": "RESERVED",
        "created_at": _CREATED_AT,
    }


def test_prepare_known_reset_resume_claim_success_and_immutability() -> None:
    record, expected_wait = _build_valid_wait_fixtures(known_reset=True)
    semantic_attempt = _build_valid_semantic_attempt()
    due_time = record["schedule"]["next_check_at_utc"]
    claimed_at = _step_iso_seconds(due_time, 5)

    record_snap = copy.deepcopy(record)
    expected_wait_snap = copy.deepcopy(expected_wait)
    semantic_attempt_snap = copy.deepcopy(semantic_attempt)

    result = prepare_known_reset_resume_claim(
        wait_record=record,
        expected_wait=expected_wait,
        semantic_attempt=semantic_attempt,
        expected_task_id=_TASK_ID,
        original_wait_sha256=_SHA256_WAIT,
        continuation_controller_run_id=_CONT_CYCLE_ID,
        resume_request_id=_RESUME_REQUEST_ID,
        claimed_at_utc=claimed_at,
    )

    assert set(result.keys()) == {"claim", "expected"}
    claim = result["claim"]
    expected = result["expected"]

    # Invariant contracts
    assert claim["schema_version"] == "1.0"
    assert claim["state"] == "RESUME_CLAIMED"
    assert claim["original_controller_run_id"] == _CYCLE_ID
    assert claim["continuation_controller_run_id"] == _CONT_CYCLE_ID
    assert claim["original_request_id"] == _REQUEST_ID
    assert claim["resume_request_id"] == _RESUME_REQUEST_ID
    assert claim["original_wait_sha256"] == _SHA256_WAIT
    assert claim["semantic_task_fingerprint"] == _SHA256_D
    assert claim["task_id"] == _TASK_ID
    assert claim["task_path"] == _TASK_PATH
    assert claim["task_sha256"] == _SHA256_A
    assert claim["source_head"] == _HEAD_SHA
    assert claim["assignment_id"] == _ASSIGNMENT_ID
    assert claim["assignment_sha256"] == _SHA256_B
    assert claim["original_run_prefix"] == _RUN_PREFIX
    assert claim["resume_run_prefix"] == f"qresume-{_REQUEST_ID}"
    assert claim["role"] == "worker"
    assert claim["reset_kind"] == "QUOTA_RESET_KNOWN"
    assert claim["next_check_at_utc"] == due_time
    assert claim["claimed_at_utc"] == claimed_at
    assert claim["remaining_resume_budget_before"] == 1
    assert type(claim["remaining_resume_budget_before"]) is int
    assert type(claim["remaining_resume_budget_before"]) is not bool
    assert claim["remaining_resume_budget_after"] == 0
    assert type(claim["remaining_resume_budget_after"]) is int
    assert type(claim["remaining_resume_budget_after"]) is not bool

    # Expected identity matches claim content
    for k in expected:
        assert expected[k] == claim[k]
        assert type(expected[k]) is type(claim[k])

    # Deep detached copy verification
    assert claim is not record
    assert expected is not record
    assert claim is not expected
    claim["mutated"] = True
    assert "mutated" not in expected

    assert record == record_snap
    assert expected_wait == expected_wait_snap
    assert semantic_attempt == semantic_attempt_snap

    # Exact due boundary also accepted
    result_exact = prepare_known_reset_resume_claim(
        wait_record=record,
        expected_wait=expected_wait,
        semantic_attempt=semantic_attempt,
        expected_task_id=_TASK_ID,
        original_wait_sha256=_SHA256_WAIT,
        continuation_controller_run_id=_CONT_CYCLE_ID,
        resume_request_id=_RESUME_REQUEST_ID,
        claimed_at_utc=due_time,
    )
    assert result_exact["claim"]["claimed_at_utc"] == due_time


def test_admission_rejects_too_early_and_clock_regression() -> None:
    record, expected_wait = _build_valid_wait_fixtures(known_reset=True)
    semantic_attempt = _build_valid_semantic_attempt()
    due_time = record["schedule"]["next_check_at_utc"]

    # Too early: claimed 1s before next_check_at_utc
    too_early = _step_iso_seconds(due_time, -1)
    with pytest.raises(ClaimAdmissionError, match="NOT_DUE"):
        prepare_known_reset_resume_claim(
            wait_record=record,
            expected_wait=expected_wait,
            semantic_attempt=semantic_attempt,
            expected_task_id=_TASK_ID,
            original_wait_sha256=_SHA256_WAIT,
            continuation_controller_run_id=_CONT_CYCLE_ID,
            resume_request_id=_RESUME_REQUEST_ID,
            claimed_at_utc=too_early,
        )

    # Clock regression: claimed before created_at_utc
    clock_regression = _step_iso_seconds(record["schedule"]["created_at_utc"], -1)
    with pytest.raises(ClaimAdmissionError, match="CLOCK_REGRESSION"):
        prepare_known_reset_resume_claim(
            wait_record=record,
            expected_wait=expected_wait,
            semantic_attempt=semantic_attempt,
            expected_task_id=_TASK_ID,
            original_wait_sha256=_SHA256_WAIT,
            continuation_controller_run_id=_CONT_CYCLE_ID,
            resume_request_id=_RESUME_REQUEST_ID,
            claimed_at_utc=clock_regression,
        )


def test_admission_rejects_unknown_reset() -> None:
    record, expected_wait = _build_valid_wait_fixtures(known_reset=False)
    semantic_attempt = _build_valid_semantic_attempt()
    due_time = record["schedule"]["next_check_at_utc"]

    with pytest.raises(ClaimAdmissionError, match="UNKNOWN_BACKOFF_REVIEW_DUE"):
        prepare_known_reset_resume_claim(
            wait_record=record,
            expected_wait=expected_wait,
            semantic_attempt=semantic_attempt,
            expected_task_id=_TASK_ID,
            original_wait_sha256=_SHA256_WAIT,
            continuation_controller_run_id=_CONT_CYCLE_ID,
            resume_request_id=_RESUME_REQUEST_ID,
            claimed_at_utc=due_time,
        )


@pytest.mark.parametrize(
    ("sem_mutator", "match_snippet"),
    [
        (lambda s: s.__setitem__("fingerprint", "f" * 64), "fingerprint"),
        (lambda s: s.__setitem__("task_id", "other-task"), "task_id"),
        (lambda s: s.__setitem__("run_prefix", "other.prefix"), "run_prefix"),
        (lambda s: s.__setitem__("state", "CANCELLED"), "state must be 'RESERVED'"),
        (lambda s: s.__setitem__("schema_version", 2), "schema_version must be int 1"),
        (lambda s: s.__setitem__("schema_version", "1"), "schema_version must be int 1"),
        (lambda s: s.__setitem__("schema_version", True), "schema_version must be int 1"),
        (lambda s: s.pop("stage_id"), r"keys mismatch:.*missing="),
        (lambda s: s.__setitem__("extra_key", 123), r"keys mismatch:.*extra="),
    ],
)
def test_admission_rejects_semantic_mismatches(sem_mutator: Any, match_snippet: str) -> None:
    record, expected_wait = _build_valid_wait_fixtures(known_reset=True)
    semantic_attempt = _build_valid_semantic_attempt()
    sem_mutator(semantic_attempt)
    due_time = record["schedule"]["next_check_at_utc"]

    with pytest.raises(ClaimAdmissionError, match=match_snippet):
        prepare_known_reset_resume_claim(
            wait_record=record,
            expected_wait=expected_wait,
            semantic_attempt=semantic_attempt,
            expected_task_id=_TASK_ID,
            original_wait_sha256=_SHA256_WAIT,
            continuation_controller_run_id=_CONT_CYCLE_ID,
            resume_request_id=_RESUME_REQUEST_ID,
            claimed_at_utc=due_time,
        )


def test_admission_rejects_non_dict_semantic_attempt() -> None:
    record, expected_wait = _build_valid_wait_fixtures(known_reset=True)
    due_time = record["schedule"]["next_check_at_utc"]
    with pytest.raises(ClaimAdmissionError, match="semantic_attempt must be dict"):
        prepare_known_reset_resume_claim(
            wait_record=record,
            expected_wait=expected_wait,
            semantic_attempt=["not", "a", "dict"],
            expected_task_id=_TASK_ID,
            original_wait_sha256=_SHA256_WAIT,
            continuation_controller_run_id=_CONT_CYCLE_ID,
            resume_request_id=_RESUME_REQUEST_ID,
            claimed_at_utc=due_time,
        )


def test_admission_rejects_duplicate_ids() -> None:
    record, expected_wait = _build_valid_wait_fixtures(known_reset=True)
    semantic_attempt = _build_valid_semantic_attempt()
    due_time = record["schedule"]["next_check_at_utc"]

    # Duplicate controller run ID
    with pytest.raises(ClaimAdmissionError, match="continuation_controller_run_id must differ"):
        prepare_known_reset_resume_claim(
            wait_record=record,
            expected_wait=expected_wait,
            semantic_attempt=semantic_attempt,
            expected_task_id=_TASK_ID,
            original_wait_sha256=_SHA256_WAIT,
            continuation_controller_run_id=_CYCLE_ID,
            resume_request_id=_RESUME_REQUEST_ID,
            claimed_at_utc=due_time,
        )

    # Duplicate request ID
    with pytest.raises(ClaimAdmissionError, match="resume_request_id must differ"):
        prepare_known_reset_resume_claim(
            wait_record=record,
            expected_wait=expected_wait,
            semantic_attempt=semantic_attempt,
            expected_task_id=_TASK_ID,
            original_wait_sha256=_SHA256_WAIT,
            continuation_controller_run_id=_CONT_CYCLE_ID,
            resume_request_id=_REQUEST_ID,
            claimed_at_utc=due_time,
        )


def test_admission_rejects_invalid_digest_and_time() -> None:
    record, expected_wait = _build_valid_wait_fixtures(known_reset=True)
    semantic_attempt = _build_valid_semantic_attempt()
    due_time = record["schedule"]["next_check_at_utc"]

    # Invalid sha256
    with pytest.raises(ClaimAdmissionError, match="original_wait_sha256 must be 64 lowercase hex"):
        prepare_known_reset_resume_claim(
            wait_record=record,
            expected_wait=expected_wait,
            semantic_attempt=semantic_attempt,
            expected_task_id=_TASK_ID,
            original_wait_sha256="bad_sha",
            continuation_controller_run_id=_CONT_CYCLE_ID,
            resume_request_id=_RESUME_REQUEST_ID,
            claimed_at_utc=due_time,
        )

    # Invalid timestamp
    with pytest.raises(ClaimAdmissionError, match="timestamp"):
        prepare_known_reset_resume_claim(
            wait_record=record,
            expected_wait=expected_wait,
            semantic_attempt=semantic_attempt,
            expected_task_id=_TASK_ID,
            original_wait_sha256=_SHA256_WAIT,
            continuation_controller_run_id=_CONT_CYCLE_ID,
            resume_request_id=_RESUME_REQUEST_ID,
            claimed_at_utc="invalid-timestamp",
        )


def test_admission_rejects_expected_drift() -> None:
    record, expected_wait = _build_valid_wait_fixtures(known_reset=True)
    semantic_attempt = _build_valid_semantic_attempt()
    due_time = record["schedule"]["next_check_at_utc"]

    # Drift expected_wait from wait_record
    expected_wait["request_id"] = "f" * 32
    with pytest.raises(ClaimAdmissionError, match="Wait record due evaluation failed"):
        prepare_known_reset_resume_claim(
            wait_record=record,
            expected_wait=expected_wait,
            semantic_attempt=semantic_attempt,
            expected_task_id=_TASK_ID,
            original_wait_sha256=_SHA256_WAIT,
            continuation_controller_run_id=_CONT_CYCLE_ID,
            resume_request_id=_RESUME_REQUEST_ID,
            claimed_at_utc=due_time,
        )


def test_admission_rejects_missing_wait_fields() -> None:
    record, expected_wait = _build_valid_wait_fixtures(known_reset=True)
    semantic_attempt = _build_valid_semantic_attempt()
    due_time = record["schedule"]["next_check_at_utc"]

    # Remove reservation field from record and expected_wait
    record["reservation"].pop("run_prefix")
    expected_wait["reservation"].pop("run_prefix")
    with pytest.raises(ClaimAdmissionError, match="Wait record due evaluation failed"):
        prepare_known_reset_resume_claim(
            wait_record=record,
            expected_wait=expected_wait,
            semantic_attempt=semantic_attempt,
            expected_task_id=_TASK_ID,
            original_wait_sha256=_SHA256_WAIT,
            continuation_controller_run_id=_CONT_CYCLE_ID,
            resume_request_id=_RESUME_REQUEST_ID,
            claimed_at_utc=due_time,
        )


def test_bool_budget_rejection_in_claim_record() -> None:
    record, expected_wait = _build_valid_wait_fixtures(known_reset=True)
    semantic_attempt = _build_valid_semantic_attempt()
    due_time = record["schedule"]["next_check_at_utc"]

    result = prepare_known_reset_resume_claim(
        wait_record=record,
        expected_wait=expected_wait,
        semantic_attempt=semantic_attempt,
        expected_task_id=_TASK_ID,
        original_wait_sha256=_SHA256_WAIT,
        continuation_controller_run_id=_CONT_CYCLE_ID,
        resume_request_id=_RESUME_REQUEST_ID,
        claimed_at_utc=due_time,
    )
    claim = result["claim"]
    expected = result["expected"]

    # When both claim and expected have boolean budget, explicit budget validator rejects
    claim_bool_before = copy.deepcopy(claim)
    expected_bool_before = copy.deepcopy(expected)
    claim_bool_before["remaining_resume_budget_before"] = True
    expected_bool_before["remaining_resume_budget_before"] = True
    with pytest.raises(ResumeClaimRecordError, match="remaining_resume_budget_before must be int 1"):
        validate_known_reset_resume_claim(claim_bool_before, expected_bool_before)

    claim_bool_after = copy.deepcopy(claim)
    expected_bool_after = copy.deepcopy(expected)
    claim_bool_after["remaining_resume_budget_after"] = False
    expected_bool_after["remaining_resume_budget_after"] = False
    with pytest.raises(ResumeClaimRecordError, match="remaining_resume_budget_after must be int 0"):
        validate_known_reset_resume_claim(claim_bool_after, expected_bool_after)

    # When claim has boolean budget and expected has int budget, type-sensitive field check rejects
    claim_mismatch = copy.deepcopy(claim)
    claim_mismatch["remaining_resume_budget_before"] = True
    with pytest.raises(ResumeClaimRecordError, match="Field 'remaining_resume_budget_before' mismatch"):
        validate_known_reset_resume_claim(claim_mismatch, expected)
