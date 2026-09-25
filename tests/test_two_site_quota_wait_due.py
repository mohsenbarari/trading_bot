"""Hermetic unit tests for scripts/two_site_quota_wait_due.py."""

from __future__ import annotations

import copy
from datetime import datetime, timedelta
from typing import Any
import pytest

try:
    from scripts.two_site_quota_wait_due import (
        WaitDueError,
        evaluate_initial_wait_check,
    )
except ImportError:
    from two_site_quota_wait_due import (
        WaitDueError,
        evaluate_initial_wait_check,
    )

try:
    from tests.test_two_site_quota_wait_record import _build_valid_fixtures
except ImportError:
    from test_two_site_quota_wait_record import _build_valid_fixtures


def _shift_iso_seconds(iso_str: str, seconds: int) -> str:
    dt = datetime.fromisoformat(iso_str[:-1] + "+00:00")
    shifted = dt + timedelta(seconds=seconds)
    return shifted.strftime("%Y-%m-%dT%H:%M:%SZ")


def test_clock_regression_before_creation() -> None:
    record, expected = _build_valid_fixtures(known_reset=False)
    created_at = record["schedule"]["created_at_utc"]
    before_created = _shift_iso_seconds(created_at, -1)

    result = evaluate_initial_wait_check(record, expected, before_created)
    assert result == {
        "schema_version": 1,
        "state": "CLOCK_REGRESSION",
        "next_check_at_utc": record["schedule"]["next_check_at_utc"],
    }


def test_boundary_unknown_reset_transitions() -> None:
    record, expected = _build_valid_fixtures(known_reset=False)
    created_at = record["schedule"]["created_at_utc"]
    next_check = record["schedule"]["next_check_at_utc"]

    # At creation timestamp: NOT_DUE
    at_created = evaluate_initial_wait_check(record, expected, created_at)
    assert at_created["state"] == "NOT_DUE"

    # One second before next_check: NOT_DUE
    one_sec_before = _shift_iso_seconds(next_check, -1)
    before_res = evaluate_initial_wait_check(record, expected, one_sec_before)
    assert before_res["state"] == "NOT_DUE"
    assert before_res["next_check_at_utc"] == next_check

    # Exactly at next_check: UNKNOWN_BACKOFF_REVIEW_DUE
    at_res = evaluate_initial_wait_check(record, expected, next_check)
    assert at_res["state"] == "UNKNOWN_BACKOFF_REVIEW_DUE"
    assert at_res["next_check_at_utc"] == next_check

    # One second after next_check: UNKNOWN_BACKOFF_REVIEW_DUE
    one_sec_after = _shift_iso_seconds(next_check, 1)
    after_res = evaluate_initial_wait_check(record, expected, one_sec_after)
    assert after_res["state"] == "UNKNOWN_BACKOFF_REVIEW_DUE"
    assert after_res["next_check_at_utc"] == next_check


def test_boundary_known_reset_transitions() -> None:
    record, expected = _build_valid_fixtures(known_reset=True)
    next_check = record["schedule"]["next_check_at_utc"]

    # One second before: NOT_DUE
    before_res = evaluate_initial_wait_check(record, expected, _shift_iso_seconds(next_check, -1))
    assert before_res["state"] == "NOT_DUE"

    # Exactly at next_check: KNOWN_RESET_REVALIDATION_DUE
    at_res = evaluate_initial_wait_check(record, expected, next_check)
    assert at_res["state"] == "KNOWN_RESET_REVALIDATION_DUE"
    assert at_res["next_check_at_utc"] == next_check

    # One second after: KNOWN_RESET_REVALIDATION_DUE
    after_res = evaluate_initial_wait_check(record, expected, _shift_iso_seconds(next_check, 1))
    assert after_res["state"] == "KNOWN_RESET_REVALIDATION_DUE"
    assert after_res["next_check_at_utc"] == next_check


def test_canonical_fractional_timestamps() -> None:
    record, expected = _build_valid_fixtures(known_reset=False)
    # 0.5s after creation: NOT_DUE
    res_frac_not_due = evaluate_initial_wait_check(record, expected, "2026-09-25T01:02:03.500000Z")
    assert res_frac_not_due["state"] == "NOT_DUE"

    # Fractional before creation: CLOCK_REGRESSION
    res_frac_regr = evaluate_initial_wait_check(record, expected, "2026-09-25T01:02:02.999999Z")
    assert res_frac_regr["state"] == "CLOCK_REGRESSION"

    # Fractional well after next_check: UNKNOWN_BACKOFF_REVIEW_DUE
    res_frac_due = evaluate_initial_wait_check(record, expected, "2026-09-25T02:00:00.123456Z")
    assert res_frac_due["state"] == "UNKNOWN_BACKOFF_REVIEW_DUE"


@pytest.mark.parametrize(
    "invalid_now",
    [
        "2026-09-25T01:02:03",
        "2026-09-25T01:02:03+00:00",
        "2026-09-25T01:02:03+02:00",
        "2026-09-25 01:02:03Z",
        "invalid-date",
        "",
        1234567890,
        None,
    ],
)
def test_invalid_now_utc_rejected(invalid_now: Any) -> None:
    record, expected = _build_valid_fixtures()
    with pytest.raises(WaitDueError) as exc_info:
        evaluate_initial_wait_check(record, expected, invalid_now)
    assert exc_info.value.__cause__ is not None


@pytest.mark.parametrize(
    ("corrupt_record", "corrupt_expected"),
    [
        ("not_a_dict", {"controller_run_id": "x"}),
        ({"schema_version": "1.0"}, "not_a_dict"),
        ({"schema_version": "2.0"}, {"controller_run_id": "x"}),
    ],
)
def test_corrupt_record_or_expected_rejected(corrupt_record: Any, corrupt_expected: Any) -> None:
    with pytest.raises(WaitDueError) as exc_info:
        evaluate_initial_wait_check(corrupt_record, corrupt_expected, "2026-09-25T01:02:03Z")
    assert exc_info.value.__cause__ is not None


def test_tampered_fixture_or_drift_rejected() -> None:
    record, expected = _build_valid_fixtures()
    expected["request_id"] = "f" * 32
    with pytest.raises(WaitDueError):
        evaluate_initial_wait_check(record, expected, "2026-09-25T01:02:03Z")


def test_pure_no_mutation_and_no_wake_authority() -> None:
    record, expected = _build_valid_fixtures(known_reset=False)
    record_snap = copy.deepcopy(record)
    expected_snap = copy.deepcopy(expected)

    check_budget_before = record["schedule"]["remaining_check_budget"]
    resume_budget_before = record["schedule"]["remaining_resume_budget"]

    res_due = evaluate_initial_wait_check(record, expected, "2026-09-25T02:00:00Z")
    res_not_due = evaluate_initial_wait_check(record, expected, "2026-09-25T01:02:03Z")

    assert record == record_snap
    assert expected == expected_snap
    assert record["schedule"]["remaining_check_budget"] == check_budget_before
    assert record["schedule"]["remaining_resume_budget"] == resume_budget_before

    forbidden_states = {"READY_TO_RESUME", "RESUME", "WAKE", "PERMITTED"}
    assert res_due["state"] not in forbidden_states
    assert res_not_due["state"] not in forbidden_states
    assert res_due["state"] in {
        "CLOCK_REGRESSION",
        "NOT_DUE",
        "KNOWN_RESET_REVALIDATION_DUE",
        "UNKNOWN_BACKOFF_REVIEW_DUE",
    }
