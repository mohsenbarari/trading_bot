"""Admission logic for known-reset quota resume claims."""

from __future__ import annotations

import copy
from typing import Any

try:
    from scripts.two_site_quota_resume_claim_record import (
        ResumeClaimRecordError,
        validate_known_reset_resume_claim,
    )
    from scripts.two_site_quota_wait_due import (
        WaitDueError,
        evaluate_initial_wait_check,
    )
    from scripts.two_site_quota_wait_record import (
        WaitRecordError,
        validate_initial_wait_record,
    )
except ImportError:
    from two_site_quota_resume_claim_record import (
        ResumeClaimRecordError,
        validate_known_reset_resume_claim,
    )
    from two_site_quota_wait_due import (
        WaitDueError,
        evaluate_initial_wait_check,
    )
    from two_site_quota_wait_record import (
        WaitRecordError,
        validate_initial_wait_record,
    )

__all__ = [
    "ClaimAdmissionError",
    "prepare_known_reset_resume_claim",
]


class ClaimAdmissionError(ValueError):
    """Raised when quota resume claim admission validation fails."""


_SEMANTIC_ATTEMPT_KEYS = frozenset({
    "schema_version",
    "fingerprint",
    "source_sha256",
    "stage_id",
    "gap_reference",
    "scenario_key",
    "task_id",
    "run_prefix",
    "state",
    "created_at",
})


def prepare_known_reset_resume_claim(
    wait_record: object,
    expected_wait: object,
    semantic_attempt: object,
    expected_task_id: object,
    original_wait_sha256: object,
    continuation_controller_run_id: object,
    resume_request_id: object,
    claimed_at_utc: object,
) -> dict[str, Any]:
    """Pure data admission and preparation for known-reset quota resume claim.

    Validates wait record due state, binds and checks semantic attempt lineage,
    constructs independent expected values from validated sources, builds the
    claim record, and verifies both with validate_known_reset_resume_claim.

    Returns deep detached copies of 'claim' and 'expected'. Never mutates inputs.
    """
    try:
        due_result = evaluate_initial_wait_check(
            wait_record,
            expected_wait,
            claimed_at_utc,
        )
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        raise ClaimAdmissionError(f"Wait record due evaluation failed: {exc}") from exc

    due_state = due_result.get("state")
    if due_state != "KNOWN_RESET_REVALIDATION_DUE":
        raise ClaimAdmissionError(
            f"Wait check state must be 'KNOWN_RESET_REVALIDATION_DUE', got {due_state!r}"
        )

    try:
        validated_wait = validate_initial_wait_record(wait_record, expected_wait)
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        raise ClaimAdmissionError(f"Wait record validation failed: {exc}") from exc

    for req_field in (
        "controller_run_id",
        "request_id",
        "semantic_task_fingerprint",
        "reservation",
        "quota_attempt",
        "schedule",
    ):
        if req_field not in validated_wait:
            raise ClaimAdmissionError(f"validated wait record missing field: {req_field!r}")

    if type(semantic_attempt) is not dict:
        raise ClaimAdmissionError(
            f"semantic_attempt must be dict, got {type(semantic_attempt).__name__}"
        )

    sem_keys = set(semantic_attempt.keys())
    if sem_keys != _SEMANTIC_ATTEMPT_KEYS:
        missing = sorted(_SEMANTIC_ATTEMPT_KEYS - sem_keys)
        extra = sorted(sem_keys - _SEMANTIC_ATTEMPT_KEYS)
        parts: list[str] = []
        if missing:
            parts.append(f"missing={missing}")
        if extra:
            parts.append(f"extra={extra}")
        raise ClaimAdmissionError(
            f"semantic_attempt keys mismatch: {', '.join(parts)}"
        )

    sem_schema_version = semantic_attempt["schema_version"]
    if type(sem_schema_version) is not int or type(sem_schema_version) is bool or sem_schema_version != 1:
        raise ClaimAdmissionError(
            f"semantic_attempt schema_version must be int 1, got {sem_schema_version!r} ({type(sem_schema_version).__name__})"
        )

    sem_state = semantic_attempt["state"]
    if type(sem_state) is not str or sem_state != "RESERVED":
        raise ClaimAdmissionError(
            f"semantic_attempt state must be 'RESERVED', got {sem_state!r}"
        )

    wait_fingerprint = validated_wait.get("semantic_task_fingerprint")
    if semantic_attempt["fingerprint"] != wait_fingerprint:
        raise ClaimAdmissionError(
            f"semantic_attempt fingerprint ({semantic_attempt['fingerprint']!r}) "
            f"does not match wait semantic_task_fingerprint ({wait_fingerprint!r})"
        )

    if semantic_attempt["task_id"] != expected_task_id:
        raise ClaimAdmissionError(
            f"semantic_attempt task_id ({semantic_attempt['task_id']!r}) "
            f"does not match expected_task_id ({expected_task_id!r})"
        )

    reservation = validated_wait.get("reservation")
    if not isinstance(reservation, dict):
        raise ClaimAdmissionError("validated wait record missing reservation dict")

    for req_field in (
        "task_path",
        "task_sha256",
        "source_head",
        "assignment_id",
        "assignment_sha256",
        "run_prefix",
    ):
        if req_field not in reservation:
            raise ClaimAdmissionError(f"reservation missing field: {req_field!r}")

    wait_run_prefix = reservation.get("run_prefix")
    if semantic_attempt["run_prefix"] != wait_run_prefix:
        raise ClaimAdmissionError(
            f"semantic_attempt run_prefix ({semantic_attempt['run_prefix']!r}) "
            f"does not match wait reservation run_prefix ({wait_run_prefix!r})"
        )

    quota_attempt = validated_wait.get("quota_attempt")
    if not isinstance(quota_attempt, dict):
        raise ClaimAdmissionError("validated wait record missing quota_attempt dict")
    if "role" not in quota_attempt:
        raise ClaimAdmissionError("quota_attempt missing 'role'")

    schedule = validated_wait.get("schedule")
    if not isinstance(schedule, dict):
        raise ClaimAdmissionError("validated wait record missing schedule dict")
    for req_field in ("reset_kind", "next_check_at_utc"):
        if req_field not in schedule:
            raise ClaimAdmissionError(f"schedule missing field: {req_field!r}")

    orig_req_id = validated_wait["request_id"]
    expected: dict[str, Any] = {
        "original_controller_run_id": validated_wait["controller_run_id"],
        "continuation_controller_run_id": continuation_controller_run_id,
        "original_request_id": orig_req_id,
        "resume_request_id": resume_request_id,
        "original_wait_sha256": original_wait_sha256,
        "semantic_task_fingerprint": wait_fingerprint,
        "task_id": expected_task_id,
        "task_path": reservation["task_path"],
        "task_sha256": reservation["task_sha256"],
        "source_head": reservation["source_head"],
        "assignment_id": reservation["assignment_id"],
        "assignment_sha256": reservation["assignment_sha256"],
        "original_run_prefix": reservation["run_prefix"],
        "resume_run_prefix": f"qresume-{orig_req_id}",
        "role": quota_attempt["role"],
        "reset_kind": schedule["reset_kind"],
        "next_check_at_utc": schedule["next_check_at_utc"],
        "claimed_at_utc": claimed_at_utc,
        "remaining_resume_budget_before": 1,
        "remaining_resume_budget_after": 0,
    }

    claim: dict[str, Any] = copy.deepcopy(expected)
    claim["schema_version"] = "1.0"
    claim["state"] = "RESUME_CLAIMED"

    try:
        validated_claim = validate_known_reset_resume_claim(claim, expected)
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        raise ClaimAdmissionError(f"Claim validation failed: {exc}") from exc

    return {
        "claim": copy.deepcopy(validated_claim),
        "expected": copy.deepcopy(expected),
    }
