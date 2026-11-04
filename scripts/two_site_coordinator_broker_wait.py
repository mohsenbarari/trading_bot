"""Finish one already-owned normal Broker request; never dispatch proposal work."""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import stat
import time

from scripts import two_site_ai_config as config
from scripts import two_site_provider_broker as broker
from scripts import two_site_qoder_driver as qoder
from scripts import two_site_proposal_runner as runner


class OwnedBrokerWaitError(ValueError):
    pass


def _need(value, message):
    if not value:
        raise OwnedBrokerWaitError(message)


def _private(root, path, limit):
    info = path.stat(follow_symlinks=False)
    _need(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid()
          and info.st_nlink == 1 and stat.S_IMODE(info.st_mode) == 0o600,
          "OWNED_BROKER_RECORD_CUSTODY")
    return broker._read_json_file(root, path, limit, "owned Broker record")


def _same_request(record, request):
    broker._validate_claimed_request_shape(request, record["request_id"])
    encoded = json.dumps(request, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False).encode()
    _need(hashlib.sha256(encoded).hexdigest() == record["request_sha256"],
          "OWNED_BROKER_REQUEST_SHA")
    for key, value in record["reservation"].items():
        _need(request.get(key) == value, "OWNED_BROKER_REQUEST_BINDING")


def _no_stop(root):
    _need(not any(os.path.lexists(path) for path in (
        root / "STOP", root / ".local/two-site-refactor/autopilot/STOP")),
        "OWNED_BROKER_STOP_BEFORE_COMPLETION")


def _ready(root, record, response):
    """Recheck the actual native/Gemini/Qoder chain, not stdout claims alone."""
    from scripts import two_site_cycle_packet as packet
    from scripts import two_site_qoder_review as review
    payload = runner.unique_json(response["stdout"])
    _need(type(payload) is dict and payload.get("state") == "READY_FOR_CODEX"
          and payload.get("source_head") == record["reservation"]["source_head"]
          and payload.get("blockers") == [], "OWNED_BROKER_READY_SHAPE")
    task = root / record["reservation"]["task_path"]
    loaded, _ = runner.load_task(root, task)
    _need(payload.get("task_id") == loaded["task_id"], "OWNED_BROKER_READY_TASK")
    values = [payload.get(key) for key in (
        "final_worker_run_id", "final_reviewer_run_id", "proposal_sha256")]
    _need(all(type(value) is str and value for value in values), "OWNED_BROKER_READY_IDS")
    packet._prepare_proposal(root, task, *values)
    review.validate_review_receipt(root, task, *values,
                                   expected_source_head=record["reservation"]["source_head"])
    return payload


def finish_owned_request(root, original_dir, before, model_config, *,
                         deadline_monotonic, progress, complete):
    """Return a strict effective result, or None when no unfinished ownership exists.

    Caller holds the original Supervisor lock. complete() starts at most one
    fresh Coordinator-only process and must return (result, real SDK terminal).
    The unchanged run_cycle transition and committed-proof gates still apply.
    """
    from scripts import two_site_codex_autopilot as ap
    root, original_dir = Path(root), Path(original_dir)
    _need(original_dir.parent == root / ".local/two-site-refactor/autopilot/runs"
          and ap.RUN_ID.fullmatch(original_dir.name), "OWNED_BROKER_RUN_PATH")
    reservation_path = original_dir / "broker-reservation.json"
    if not os.path.lexists(reservation_path):
        return None
    # Already-committed normal output follows the original validation path.
    current = ap.validate_repository(root, require_clean=True)
    if current["head"] != before["head"]:
        return None
    record = broker.read_cycle_reservation(root, original_dir.name)
    dispatch_path = original_dir / broker._CYCLE_DISPATCH
    dispatch = _private(root, dispatch_path, broker._MAX_CYCLE_RECORD)
    _need(set(dispatch) == {"schema_version", "request", "broker_pid", "broker_started_at"}
          and dispatch["schema_version"] == "1.0", "OWNED_BROKER_DISPATCH_SCHEMA")
    request = dispatch["request"]
    _same_request(record, request)
    _need(request.get("request_type") is None and request["controller_run_id"] == original_dir.name
          and request["source_head"] == before["head"]
          and request["assignment_id"] == before["assignment_scope"].assignment_id
          and request["assignment_sha256"] == before["assignment_scope"].sha256,
          "OWNED_BROKER_SOURCE_SCOPE")
    owner = (dispatch["broker_pid"], dispatch["broker_started_at"])
    _need(type(owner[0]) is int and owner[0] > 0
          and type(owner[1]) in (int, float) and math.isfinite(owner[1]),
          "OWNED_BROKER_OWNER")
    age = time.time() - request["created_at"]
    _need(age >= 0, "OWNED_BROKER_FUTURE_REQUEST")
    remaining = broker._overall_timeout(request["timeout"]) + 30 - age
    _need(type(deadline_monotonic) in (int, float) and math.isfinite(deadline_monotonic),
          "OWNED_BROKER_DEADLINE")
    deadline = min(deadline_monotonic, time.monotonic() + remaining)
    _need(deadline > time.monotonic(), "OWNED_BROKER_DEADLINE")
    original_terminal = qoder.validate_qoder_terminal(
        original_dir, expected_model=model_config["roles"]["coordinator"]["model"])
    # A complete genuine BLOCKED decision after a finished request is not
    # silently rejudged. Only pending ownership or missing JSON needs completion.
    terminal_path = original_dir / broker._CYCLE_TERMINAL
    if os.path.lexists(terminal_path):
        try:
            existing = qoder._json(original_terminal["text"])
            ap.validate_result(existing)
        except (qoder.QoderError, ap.AutopilotError, TypeError):
            pass
        else:
            return None
    record_bytes = runner.bounded_file_bytes(root, reservation_path, 4096, "owned reservation")
    dispatch_bytes = runner.bounded_file_bytes(root, dispatch_path, broker._MAX_CYCLE_RECORD,
                                              "owned dispatch")
    def check():
        _need(ap.validate_repository(root, require_clean=True) == before,
              "OWNED_BROKER_REPOSITORY_DRIFT")
        _need(config.load_ai_config(root) == model_config, "OWNED_BROKER_MODEL_DRIFT")
        _need(runner.sha(runner.bounded_file_bytes(root, root / request["task_path"],
                                                  runner.MAX_CONTEXT, "owned Task")) == request["task_sha256"],
              "OWNED_BROKER_TASK_DRIFT")
        _need(runner.bounded_file_bytes(root, reservation_path, 4096, "owned reservation") == record_bytes
              and runner.bounded_file_bytes(root, dispatch_path, broker._MAX_CYCLE_RECORD,
                                             "owned dispatch") == dispatch_bytes,
              "OWNED_BROKER_OWNERSHIP_DRIFT")
        progress("WAITING_PROVIDER")
    response = broker._wait_existing_response(root, request, owner, request["timeout"],
                                              deadline_monotonic=deadline, poll_check=check)
    broker._publish_cycle_terminal(root, request, response)
    check()
    if response["state"] != "COMPLETED" or response["exit_code"] != 0:
        result = {"schema_version": "1.0", "state": "BLOCKED", "disposition": "HALT",
                  "category": "EXTERNAL_GATE", "commit": None,
                  "summary": "The exact owned Broker request reached a real failure; no work accepted.",
                  "next_action": "Inspect the preserved owned request/response and pipeline evidence; do not replay it.",
                  "blockers": ["Owned Broker terminal failure: " + request["request_id"]],
                  "gemini_worker_run_id": None, "gemini_reviewer_run_id": None,
                  "proposal_sha256": None}
        return {"result": result, "kind": "ROOT_OBSERVED_BROKER_TERMINAL_FAILURE",
                "request_id": request["request_id"], "terminal_path": str(terminal_path)}
    ready = _ready(root, record, response)
    _no_stop(root)
    directory = original_dir / "owned-broker-completion"
    directory.mkdir(mode=0o700)  # Immutable one-attempt marker; no reuse.
    broker._atomic_new_json(directory / "attempt.json", {
        "schema_version": "1.0", "controller_run_id": original_dir.name,
        "request_id": request["request_id"], "request_sha256": record["request_sha256"],
        "original_session_id": original_terminal["session_id"],
        "original_stream_sha256": runner.file_sha256(original_dir / "stream.jsonl"),
        "source_head": before["head"], "ready": ready,
    }, runner.MAX_CONTEXT)
    progress("COORDINATOR_COMPLETION")
    prompt = (
        "Complete ONLY this SAME existing controller's already-finished Broker unit. "
        "Do not build/freeze a Task, submit/enqueue a pipeline, invoke a proposal/provider Runner, "
        "or replay Worker/native/Gemini/Qoder final-review work. Do not start background jobs. "
        "The preserved earlier Coordinator terminal was provisional; consider its text but do not "
        "treat waiting as a completed business outcome. Use ONLY the exact READY chain below. "
        "All normal semantic review, two_site_qoder_review, two_site_apply_reviewed, "
        "cycle_tools review --applied, scoped Git and matching acceptance gates remain mandatory. "
        "Existing Qoder approval is validated; reuse its exact bound receipt. Read the normal "
        "review/apply instructions from docs/refactor/two-server/templates/CODEX_AUTOPILOT_PROMPT.md "
        "if needed, skipping ALL preparation/submission. Return ONLY the closed Coordinator JSON; "
        "if a real final gate fails, return truthful BLOCKED. Never claim whole-plan COMPLETE.\n"
        + json.dumps({"controller_run_id": original_dir.name, "source_head": before["head"],
                      "assignment_id": request["assignment_id"],
                      "assignment_sha256": request["assignment_sha256"],
                      "task_path": request["task_path"], "task_sha256": request["task_sha256"],
                      "run_prefix": request["run_prefix"], "ready": ready,
                      "original_terminal_text": original_terminal["text"]}, ensure_ascii=False))
    check(); _no_stop(root)
    _need(deadline - time.monotonic() >= 10, "OWNED_BROKER_DEADLINE")
    result, terminal = complete(directory, prompt, min(900, int(deadline - time.monotonic())))
    _need(terminal["session_id"] != original_terminal["session_id"], "OWNED_BROKER_SESSION_REUSE")
    ap.validate_result(result)
    if result["state"] != "BLOCKED":
        _need(result["state"] == "CONTINUE" and tuple(result[k] for k in (
            "gemini_worker_run_id", "gemini_reviewer_run_id", "proposal_sha256")) == tuple(ready[k] for k in (
                "final_worker_run_id", "final_reviewer_run_id", "proposal_sha256")),
              "OWNED_BROKER_COMPLETION_EVIDENCE")
    broker._atomic_new_json(directory / "receipt.json", {
        "schema_version": "1.0", "controller_run_id": original_dir.name,
        "request_id": request["request_id"], "source_head": before["head"],
        "original_session_id": original_terminal["session_id"],
        "completion_session_id": terminal["session_id"],
        "completion_stream_sha256": runner.file_sha256(directory / "stream.jsonl"),
        "result_sha256": runner.file_sha256(directory / "result.json"),
        "observed_exit_code": 0, "normal_exit_observed": True,
    }, 8192)
    return {"result": result, "kind": "OWNED_BROKER_COORDINATOR_COMPLETION",
            "request_id": request["request_id"], "terminal_path": str(terminal_path),
            "receipt_path": str(directory / "receipt.json")}
