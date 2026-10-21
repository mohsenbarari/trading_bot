"""Dormant read-only collection of known-reset resume context under Supervisor lock."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from scripts.two_site_proposal_runner import (
    MAX_CONTEXT,
    bounded_file_bytes,
    load_task,
)
from scripts.two_site_provider_broker import (
    _task_relative_path, _open_existing_cycle_run_directory, canonical_root,
)
from scripts.two_site_quota_resume_claim_request import ClaimRequestError
from scripts.two_site_quota_resume_claim_storage import QUOTA_RESUME_CLAIM_FILENAME
from scripts.two_site_quota_wait_due import evaluate_initial_wait_check
from scripts.two_site_quota_wait_intent import read_pending_rescope_intent
from scripts.two_site_quota_wait_recovery import (
    recover_durable_initial_wait_locked, _check_stop_absent,
)
from scripts.two_site_quota_wait_sources import collect_initial_wait_source_expected
from scripts.two_site_quota_wait_storage import read_initial_wait_record_bytes
from scripts.two_site_semantic_attempts import read_existing_semantic_attempt

__all__ = ["ResumeContextError", "collect_known_reset_resume_context_locked",
           "wait_for_known_reset_resume_ready_locked"]


class ResumeContextError(ValueError):
    """Raised when supervisor resume context collection fails."""


def _reject_existing_resume_claim_locked(root: Path, controller_run_id: str) -> None:
    """Deny duplicate intent only; a claim never establishes resolved history."""
    if canonical_root(root) != root:
        raise ResumeContextError("root must be exact canonical path")

    def check_stop() -> None:
        _check_stop_absent(root)
        try:
            (root / "STOP").lstat()
        except FileNotFoundError:
            return
        raise ResumeContextError("root STOP sentinel exists")

    check_stop()
    try:
        with _open_existing_cycle_run_directory(root, controller_run_id) as (_, run_fd, verify_identity):
            try:
                verify_identity()
                try:
                    os.stat(QUOTA_RESUME_CLAIM_FILENAME, dir_fd=run_fd, follow_symlinks=False)
                except FileNotFoundError:
                    pass
                else:
                    raise ClaimRequestError("quota resume claim already exists; refusing duplicate continuation")
            finally:
                verify_identity()
    finally:
        check_stop()


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
        _reject_existing_resume_claim_locked(root, original_controller_run_id)
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

        task_path = root / _task_relative_path(task_rel_path)
        task_bytes_initial = bounded_file_bytes(root, task_path, MAX_CONTEXT, "task file")
        if hashlib.sha256(task_bytes_initial).hexdigest() != task_sha256:
            raise ResumeContextError("task file sha256 mismatch with reservation")

        task, _sources = load_task(root, task_path)
        task_bytes_again = bounded_file_bytes(root, task_path, MAX_CONTEXT, "task file")
        if task_bytes_again != task_bytes_initial:
            raise ResumeContextError("task file changed during load_task")

        from scripts.two_site_quota_continuation_admission import quota_semantic_directory
        pipeline_dir = quota_semantic_directory(root)
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
    except (KeyboardInterrupt, SystemExit, ResumeContextError, ClaimRequestError):
        raise
    except Exception as exc:
        raise ResumeContextError(f"failed collecting resume context: {exc}") from exc


def wait_for_known_reset_resume_ready_locked(
    root: Path,
    request: dict[str, Any],
    *,
    expected_broker_identity: tuple[int, float],
) -> dict[str, Any]:
    """Read one existing continuation result; caller retains Supervisor lock.

    No queue/reservation publication, retry, cleanup, Provider or lock acquisition.
    The shared Broker wait-only helper is supplied by the matching Q2 change.
    """
    import math
    from scripts import two_site_provider_broker as broker
    from scripts import two_site_cycle_tools as tools
    from scripts.two_site_quota_broker_route import _check_pre_dispatch_stop
    from scripts.two_site_quota_continuation_admission import (
        validate_quota_continuation_request,
    )
    from scripts.two_site_quota_continuation_terminal_storage import (
        read_terminal_continuation_record,
    )
    from scripts.two_site_proposal_pipeline import _load_verifier_pre_review_receipt

    def stop_check() -> None:
        _check_pre_dispatch_stop(root)
        try:
            (root / "STOP").lstat()
        except FileNotFoundError:
            return
        raise ResumeContextError("resume READY handoff stopped")

    def canonical(value: Any) -> bytes:
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, allow_nan=False).encode("utf-8")

    try:
        if not isinstance(root, Path) or root.resolve(strict=True) != root:
            raise ResumeContextError("resume READY root must be canonical Path")
        if type(request) is not dict:
            raise ResumeContextError("resume READY request must be exact dict")
        if (type(expected_broker_identity) is not tuple
                or len(expected_broker_identity) != 2
                or type(expected_broker_identity[0]) is not int
                or expected_broker_identity[0] <= 0
                or type(expected_broker_identity[1]) not in (int, float)
                or not math.isfinite(expected_broker_identity[1])):
            raise ResumeContextError("resume READY actual Broker identity required")
        original_request = deepcopy(request)
        stop_check()
        admitted, task_path, _scope = validate_quota_continuation_request(root, request)
        if canonical(admitted) != canonical(original_request):
            raise ResumeContextError("resume READY admission mismatch")
        task_before = bounded_file_bytes(root, task_path, MAX_CONTEXT, "resume Task")
        if hashlib.sha256(task_before).hexdigest() != admitted["task_sha256"]:
            raise ResumeContextError("resume READY Task SHA mismatch")
        # This helper waits only; it must never enqueue or publish a reservation.
        response = broker._wait_existing_response(
            root, admitted, expected_broker_identity, admitted["timeout"]
        )
        stop_check()
        response = broker.validate_response(admitted, response)
        terminal = read_terminal_continuation_record(
            root, admitted["request_id"], expected_request=admitted
        )
        if canonical(terminal["response"]) != canonical(response):
            raise ResumeContextError("resume READY queue/terminal mismatch")
        if (response["state"] != "COMPLETED"
                or type(response["exit_code"]) is not int
                or response["exit_code"] != 0 or response["stderr"] != ""):
            raise ResumeContextError("resume READY response is not successful")
        ready = broker.unique_json(response["stdout"])
        ready_keys = {
            "state", "task_id", "source_head", "final_worker_run_id",
            "final_reviewer_run_id", "proposal_sha256", "changed_paths",
            "proposal_path", "reviewer_path", "correction_count", "blockers",
            "pre_review_test_path", "pre_review_test_sha256", "pre_review_test_state",
            "semantic_task_fingerprint",
        }
        if (type(ready) is not dict or set(ready) != ready_keys
                or ready.get("state") != "READY_FOR_CODEX" or ready.get("blockers") != []):
            raise ResumeContextError("resume result is not READY_FOR_CODEX")
        for field in ("task_id", "source_head", "semantic_task_fingerprint"):
            if type(ready.get(field)) is not str or ready[field] != admitted[field]:
                raise ResumeContextError("resume READY lineage mismatch")
        proof = tools.review(
            root, task_path, ready["final_worker_run_id"],
            ready["final_reviewer_run_id"], ready["proposal_sha256"], applied=False,
        )
        if (proof["state"] != "BINDINGS_VALID" or proof["head"] != admitted["source_head"]
                or proof["gemini_worker_run_id"] != ready["final_worker_run_id"]
                or proof["gemini_reviewer_run_id"] != ready["final_reviewer_run_id"]
                or proof["proposal_sha256"] != ready["proposal_sha256"]
                or proof["changed_paths"] != ready["changed_paths"]):
            raise ResumeContextError("resume READY real chain mismatch")
        for key, proof_key in (("proposal_path", "worker_result"),
                               ("reviewer_path", "reviewer_result")):
            if ready.get(key) != Path(proof[proof_key]).relative_to(root).as_posix():
                raise ResumeContextError("resume READY result path mismatch")
        original_terminal = broker.read_terminal_quota_record(
            root, admitted["original_request_id"]
        )
        prefix = (original_terminal["request"]["run_prefix"]
                  if admitted["role"] == "verifier" else admitted["run_prefix"])
        if admitted["role"] not in ("worker", "verifier"):
            raise ResumeContextError("resume READY role mismatch")
        test_receipt = _load_verifier_pre_review_receipt(
            root, dict(ready, original_run_prefix=prefix)
        )
        if test_receipt["selectors"] != proof["test_selectors"]:
            raise ResumeContextError("resume READY native selectors mismatch")
        reviewer_status = broker.unique_json(bounded_file_bytes(
            root, Path(proof["reviewer_result"]).parent / "status.json",
            MAX_CONTEXT, "resume Reviewer status",
        ).decode("utf-8"))
        if (ready["pre_review_test_state"] != "PASSED"
                or reviewer_status.get("input_test_evidence_sha256")
                != ready["pre_review_test_sha256"]):
            raise ResumeContextError("resume READY Reviewer/native SHA mismatch")
        # Re-admit and re-read exact immutable custody after all proof readers.
        again, again_path, _scope = validate_quota_continuation_request(root, admitted)
        if canonical(again) != canonical(admitted) or again_path != task_path:
            raise ResumeContextError("resume READY request drift")
        if bounded_file_bytes(root, task_path, MAX_CONTEXT, "resume Task") != task_before:
            raise ResumeContextError("resume READY Task drift")
        terminal_again = read_terminal_continuation_record(
            root, admitted["request_id"], expected_request=admitted
        )
        if canonical(terminal_again) != canonical(terminal):
            raise ResumeContextError("resume READY terminal drift")
        response_path = (root / ".local/two-site-refactor/provider-broker/responses"
                         / (admitted["request_id"] + ".json"))
        if canonical(broker._safe_read_existing_response(root, response_path)) != canonical(response):
            raise ResumeContextError("resume READY response drift")
        stop_check()
        return {
            "request": deepcopy(admitted),
            "response": deepcopy(response),
            "ready": deepcopy(ready),
            "terminal_receipt_sha256": hashlib.sha256(canonical(terminal)).hexdigest(),
        }
    except (KeyboardInterrupt, SystemExit, ResumeContextError):
        raise
    except Exception as exc:
        raise ResumeContextError("resume READY handoff validation failed") from exc
