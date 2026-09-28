"""Dormant read-only collection of known-reset resume context under Supervisor lock."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
from typing import Any

from scripts.two_site_proposal_runner import (
    MAX_CONTEXT,
    bounded_file_bytes,
    load_task,
    relative_file,
)
from scripts.two_site_quota_wait_due import evaluate_initial_wait_check
from scripts.two_site_quota_wait_intent import read_pending_rescope_intent
from scripts.two_site_quota_wait_recovery import recover_durable_initial_wait_locked
from scripts.two_site_quota_wait_sources import collect_initial_wait_source_expected
from scripts.two_site_quota_wait_storage import read_initial_wait_record_bytes
from scripts.two_site_semantic_attempts import read_existing_semantic_attempt

__all__ = ["ResumeContextError", "collect_known_reset_resume_context_locked"]


class ResumeContextError(ValueError):
    """Raised when supervisor resume context collection fails."""


def collect_known_reset_resume_context_locked(
    root: Path,
    original_controller_run_id: str,
    claimed_at_utc: str,
) -> dict[str, Any]:
    """Dormant read-only collection of validated known-reset resume context."""
    if not isinstance(root, Path):
        raise ResumeContextError("root must be a Path")
    if not isinstance(original_controller_run_id, str) or not original_controller_run_id:
        raise ResumeContextError("original_controller_run_id must be a non-empty string")
    if not isinstance(claimed_at_utc, str) or not claimed_at_utc:
        raise ResumeContextError("claimed_at_utc must be a non-empty string")

    try:
        recovery = recover_durable_initial_wait_locked(root)
        if recovery is None:
            raise ResumeContextError("no active durable initial wait")
        recovered_cid, wait_record = recovery
        if recovered_cid != original_controller_run_id:
            raise ResumeContextError(
                f"controller_run_id mismatch: expected {original_controller_run_id!r}, got {recovered_cid!r}"
            )
        if not isinstance(wait_record, dict):
            raise ResumeContextError("wait_record must be a dict")

        epoch, attempt, repository = read_pending_rescope_intent(root)
        if type(wait_record.get("rescope_epoch")) is not str or wait_record["rescope_epoch"] != epoch:
            raise ResumeContextError("wait_record rescope_epoch mismatch")
        if (
            type(wait_record.get("rescope_attempt")) is not int
            or isinstance(wait_record.get("rescope_attempt"), bool)
            or wait_record["rescope_attempt"] != attempt
        ):
            raise ResumeContextError("wait_record rescope_attempt mismatch")
        if wait_record.get("repository") != repository:
            raise ResumeContextError("wait_record repository mismatch")

        expected_wait = collect_initial_wait_source_expected(
            root, original_controller_run_id, epoch, attempt
        )
        if not isinstance(expected_wait, dict):
            raise ResumeContextError("expected_wait must be a dict")
        if type(expected_wait.get("rescope_epoch")) is not str or expected_wait["rescope_epoch"] != epoch:
            raise ResumeContextError("expected_wait rescope_epoch mismatch")
        if (
            type(expected_wait.get("rescope_attempt")) is not int
            or isinstance(expected_wait.get("rescope_attempt"), bool)
            or expected_wait["rescope_attempt"] != attempt
        ):
            raise ResumeContextError("expected_wait rescope_attempt mismatch")
        if expected_wait.get("repository") != repository:
            raise ResumeContextError("expected_wait repository mismatch")

        due = evaluate_initial_wait_check(wait_record, expected_wait, claimed_at_utc)
        if not isinstance(due, dict) or due.get("state") != "KNOWN_RESET_REVALIDATION_DUE":
            state = due.get("state") if isinstance(due, dict) else due
            raise ResumeContextError(f"wait check state is not KNOWN_RESET_REVALIDATION_DUE: {state!r}")

        raw_wait_bytes = read_initial_wait_record_bytes(root, original_controller_run_id)
        canonical_wait_bytes = json.dumps(
            wait_record, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode("utf-8")
        if raw_wait_bytes != canonical_wait_bytes:
            raise ResumeContextError("raw wait bytes do not match canonical wait record JSON")

        reservation = wait_record.get("reservation")
        if not isinstance(reservation, dict):
            raise ResumeContextError("wait_record missing reservation dict")
        task_rel_path = reservation.get("task_path")
        task_sha256 = reservation.get("task_sha256")
        run_prefix = reservation.get("run_prefix")
        if not isinstance(task_rel_path, str) or not task_rel_path:
            raise ResumeContextError("reservation missing task_path")
        if not isinstance(task_sha256, str) or not task_sha256:
            raise ResumeContextError("reservation missing task_sha256")
        if not isinstance(run_prefix, str) or not run_prefix:
            raise ResumeContextError("reservation missing run_prefix")

        task_path = relative_file(root, task_rel_path)
        task_bytes_initial = bounded_file_bytes(root, task_path, MAX_CONTEXT, "task file")
        if hashlib.sha256(task_bytes_initial).hexdigest() != task_sha256:
            raise ResumeContextError("task file sha256 mismatch with reservation")

        task, _sources = load_task(root, task_path)
        task_bytes_again = bounded_file_bytes(root, task_path, MAX_CONTEXT, "task file")
        if task_bytes_again != task_bytes_initial:
            raise ResumeContextError("task file changed during load_task")

        pipeline_dir = root / ".local" / "two-site-refactor"
        semantic_attempt = read_existing_semantic_attempt(root, pipeline_dir, task, run_prefix)

        if not isinstance(task, dict) or "task_id" not in task:
            raise ResumeContextError("task missing task_id")
        expected_task_id = task["task_id"]

        if semantic_attempt.get("task_id") != expected_task_id:
            raise ResumeContextError("semantic_attempt task_id mismatch with task")
        fingerprint = semantic_attempt.get("fingerprint")
        if not fingerprint or wait_record.get("semantic_task_fingerprint") != fingerprint:
            raise ResumeContextError("semantic fingerprint does not match wait record")
        if expected_wait.get("semantic_task_fingerprint") != fingerprint:
            raise ResumeContextError("semantic fingerprint does not match expected wait")

        return {
            "original_controller_run_id": original_controller_run_id,
            "wait_record": deepcopy(wait_record),
            "expected_wait": deepcopy(expected_wait),
            "raw_wait_bytes": raw_wait_bytes,
            "semantic_attempt": deepcopy(semantic_attempt),
            "expected_task_id": expected_task_id,
            "due": deepcopy(due),
        }
    except (KeyboardInterrupt, SystemExit, ResumeContextError):
        raise
    except Exception as exc:
        raise ResumeContextError(f"failed collecting resume context: {exc}") from exc
