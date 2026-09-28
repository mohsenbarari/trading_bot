"""Dormant read-only inspection of durable quota wait due state."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from scripts.two_site_quota_wait_due import evaluate_initial_wait_check
from scripts.two_site_quota_wait_intent import read_pending_rescope_intent
from scripts.two_site_quota_wait_recovery import recover_durable_initial_wait_locked
from scripts.two_site_quota_wait_sources import collect_initial_wait_source_expected

__all__ = [
    "SupervisorDueInspectError",
    "inspect_durable_quota_wait_due_locked",
]


class SupervisorDueInspectError(ValueError):
    """Raised when supervisor durable quota wait due inspection fails."""


def inspect_durable_quota_wait_due_locked(
    root: Path,
    now_utc: str,
) -> dict[str, Any] | None:
    """Observational classification only of durable quota wait due state.

    Caller already holds the exclusive Supervisor lock. Does not acquire or
    release locks, hook run_loop/CLI, claim resume, or grant authority.
    """
    recovery = recover_durable_initial_wait_locked(root)
    if recovery is None:
        return None

    controller_run_id, wait_record = recovery
    epoch, attempt, repository = read_pending_rescope_intent(root)

    if wait_record["rescope_epoch"] != epoch:
        raise SupervisorDueInspectError(
            f"rescope_epoch mismatch: expected {epoch!r}, got {wait_record['rescope_epoch']!r}"
        )
    if wait_record["rescope_attempt"] != attempt:
        raise SupervisorDueInspectError(
            f"rescope_attempt mismatch: expected {attempt!r}, got {wait_record['rescope_attempt']!r}"
        )
    if wait_record["repository"] != repository:
        raise SupervisorDueInspectError(
            "wait record repository does not match pending rescope intent repository"
        )

    expected = collect_initial_wait_source_expected(
        root, controller_run_id, epoch, attempt
    )
    if expected["repository"] != repository:
        raise SupervisorDueInspectError(
            "expected repository does not match pending rescope intent repository"
        )

    due_result = evaluate_initial_wait_check(wait_record, expected, now_utc)
    return {
        "controller_run_id": controller_run_id,
        "due": due_result,
    }
