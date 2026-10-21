"""Observational due-time evaluation for two-site quota wait records."""

from __future__ import annotations

from typing import Any

try:
    from scripts.two_site_quota_wait_record import validate_initial_wait_record
    from scripts.two_site_quota_wait_schedule import _parse_utc_iso
except ImportError:
    from two_site_quota_wait_record import validate_initial_wait_record
    from two_site_quota_wait_schedule import _parse_utc_iso

__all__ = ["WaitDueError", "evaluate_initial_wait_check"]


class WaitDueError(ValueError):
    """Raised when record, expected, or timestamp validation fails."""


def evaluate_initial_wait_check(
    record: object,
    expected: object,
    now_utc: object,
    *,
    health_checks: object = None,
) -> dict[str, Any]:
    """Pure, observational evaluation of an initial quota wait record.

    Validates record against expected binding, checks current time against
    schedule boundaries, and returns the revalidation/review state.
    Never modifies budgets or grants wake/replay permission.
    """
    try:
        validated = validate_initial_wait_record(record, expected)
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        raise WaitDueError(f"Wait record validation failed: {exc}") from exc

    try:
        parsed_now = _parse_utc_iso(now_utc)
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        raise WaitDueError(f"Invalid now_utc timestamp: {exc}") from exc

    schedule = validated["schedule"]
    created_at_utc = schedule["created_at_utc"]
    next_check_at_utc = schedule["next_check_at_utc"]
    reset_kind = schedule["reset_kind"]

    try:
        parsed_created = _parse_utc_iso(created_at_utc)
        parsed_next = _parse_utc_iso(next_check_at_utc)
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        raise WaitDueError(f"Schedule timestamp parsing failed: {exc}") from exc

    checked_count = None
    if health_checks is not None:
        if type(health_checks) is not list or len(health_checks) > 5:
            raise WaitDueError("invalid bounded health observation chain")
        if reset_kind == "QUOTA_RESET_UNKNOWN":
            from datetime import timedelta
            checked_count = len(health_checks)
            prior = parsed_created
            for index, check in enumerate(health_checks):
                if type(check) is not dict or check.get("ordinal") != index + 1:
                    raise WaitDueError("invalid health observation sequence")
                parsed = _parse_utc_iso(check["checked_at_utc"])
                if parsed < prior + timedelta(minutes=(15, 30, 60, 120, 240)[index]):
                    raise WaitDueError("health observation chronology mismatch")
                prior = parsed
            if parsed_now < prior:
                return {"schema_version": 1, "state": "CLOCK_REGRESSION",
                        "next_check_at_utc": prior.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "health_checks_completed": checked_count,
                        "health_checks_remaining": 5 - checked_count}
            if checked_count == 5:
                return {"schema_version": 1, "state": "UNKNOWN_HEALTH_CHECKS_EXHAUSTED",
                        "next_check_at_utc": None, "health_checks_completed": 5,
                        "health_checks_remaining": 0}
            parsed_next = prior + timedelta(minutes=(15, 30, 60, 120, 240)[checked_count])
            next_check_at_utc = parsed_next.strftime("%Y-%m-%dT%H:%M:%SZ")

    if parsed_now < parsed_created:
        state = "CLOCK_REGRESSION"
    elif parsed_now < parsed_next:
        state = "NOT_DUE"
    else:
        if reset_kind == "QUOTA_RESET_KNOWN":
            state = "KNOWN_RESET_REVALIDATION_DUE"
        elif reset_kind == "QUOTA_RESET_UNKNOWN":
            state = "UNKNOWN_BACKOFF_REVIEW_DUE"
        else:
            raise WaitDueError(f"Unsupported schedule reset_kind: {reset_kind!r}")

    result = {"schema_version": 1, "state": state, "next_check_at_utc": next_check_at_utc}
    if checked_count is not None:
        result.update(health_checks_completed=checked_count, health_checks_remaining=5 - checked_count)
    return result
