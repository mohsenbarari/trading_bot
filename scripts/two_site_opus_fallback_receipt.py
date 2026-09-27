from __future__ import annotations

import hashlib
import json
from typing import Any

MAX_STATUS_BYTES = 160_000
MAX_TASK_BYTES = 160_000
MAX_STREAM_BYTES = 2_000_000
EXPECTED_MODEL = "claude-opus-4-6-thinking"


class OpusReceiptValidationError(ValueError):
    """Raised when validation of an Opus failure receipt fails."""


def _is_explicit_forbidden_evidence(obj: object) -> bool:
    if not isinstance(obj, dict):
        return False
    tool_name = obj.get("tool_name")
    if isinstance(tool_name, str) and tool_name and tool_name != "finish":
        return True
    tool_info = obj.get("tool_info")
    if isinstance(tool_info, dict):
        name = tool_info.get("name")
        if isinstance(name, str) and name and name != "finish":
            return True
    return bool("subagent_info" in obj or obj.get("step_type") == "subagent")


def _load_json_strict(text: str) -> Any:
    def _pairs_hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise OpusReceiptValidationError(f"duplicate key in JSON: {key!r}")
            result[key] = value
        return result

    try:
        return json.loads(text, object_pairs_hook=_pairs_hook)
    except json.JSONDecodeError as exc:
        raise OpusReceiptValidationError(f"malformed JSON document: {exc}") from exc


def validate_opus_failure_receipt(
    status_raw: bytes,
    task_raw: bytes,
    stream_raw: bytes,
    *,
    expected_run_id: str,
    expected_task: dict,
    expected_sources: dict,
    expected_head: str,
) -> dict:
    if not isinstance(status_raw, (bytes, bytearray)):
        raise OpusReceiptValidationError("status_raw must be bytes")
    if len(status_raw) > MAX_STATUS_BYTES:
        raise OpusReceiptValidationError(f"status_raw exceeds limit of {MAX_STATUS_BYTES} bytes")
    try:
        status_text = status_raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise OpusReceiptValidationError(f"status_raw is not valid UTF-8: {exc}") from exc

    status = _load_json_strict(status_text)
    if not isinstance(status, dict):
        raise OpusReceiptValidationError("status JSON must be an object")

    if "source_head" in status:
        raise OpusReceiptValidationError("status must not contain 'source_head' alias")

    if status.get("role") != "worker":
        raise OpusReceiptValidationError(f"expected status role 'worker', got {status.get('role')!r}")
    if status.get("run_id") != expected_run_id:
        raise OpusReceiptValidationError(f"expected status run_id {expected_run_id!r}, got {status.get('run_id')!r}")
    if status.get("task_id") != expected_task.get("task_id"):
        raise OpusReceiptValidationError(f"expected status task_id {expected_task.get('task_id')!r}, got {status.get('task_id')!r}")
    if status.get("head") != expected_head:
        raise OpusReceiptValidationError(f"expected status head {expected_head!r}, got {status.get('head')!r}")
    if status.get("model") != EXPECTED_MODEL:
        raise OpusReceiptValidationError(f"expected status model {EXPECTED_MODEL!r}, got {status.get('model')!r}")
    if status.get("state") != "BLOCKED":
        raise OpusReceiptValidationError(f"expected status state 'BLOCKED', got {status.get('state')!r}")

    error_field = status.get("error")
    if not isinstance(error_field, str) or not error_field.startswith("ForbiddenToolAttemptError:"):
        raise OpusReceiptValidationError("status error must be a string starting with 'ForbiddenToolAttemptError:'")

    if not isinstance(task_raw, (bytes, bytearray)):
        raise OpusReceiptValidationError("task_raw must be bytes")
    if len(task_raw) > MAX_TASK_BYTES:
        raise OpusReceiptValidationError(f"task_raw exceeds limit of {MAX_TASK_BYTES} bytes")
    try:
        task_text = task_raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise OpusReceiptValidationError(f"task_raw is not valid UTF-8: {exc}") from exc

    snapshot = _load_json_strict(task_text)
    if not isinstance(snapshot, dict):
        raise OpusReceiptValidationError("snapshot JSON must be an object")

    if "head" in snapshot:
        raise OpusReceiptValidationError("snapshot must not contain 'head' alias")

    for canonical_key in ("task", "sources", "source_head"):
        if canonical_key not in snapshot:
            raise OpusReceiptValidationError(f"snapshot missing canonical key {canonical_key!r}")

    if snapshot["task"] != expected_task:
        raise OpusReceiptValidationError("snapshot task does not match expected_task")
    if snapshot["sources"] != expected_sources:
        raise OpusReceiptValidationError("snapshot sources does not match expected_sources")
    if snapshot["source_head"] != expected_head:
        raise OpusReceiptValidationError("snapshot source_head does not match expected_head")

    extra_keys = set(snapshot.keys()) - {"task", "sources", "source_head"}
    if extra_keys:
        if extra_keys != {"feedback", "proposal"}:
            if extra_keys == {"feedback"} or extra_keys == {"proposal"}:
                raise OpusReceiptValidationError(
                    f"correction snapshot extras must include both 'feedback' and 'proposal', found only {sorted(extra_keys)}"
                )
            raise OpusReceiptValidationError(f"snapshot contains unallowed extra keys: {sorted(extra_keys)}")
        if snapshot["feedback"] is None or snapshot["proposal"] is None:
            raise OpusReceiptValidationError("snapshot extras 'feedback' and 'proposal' must not be null")

    if not isinstance(stream_raw, (bytes, bytearray)):
        raise OpusReceiptValidationError("stream_raw must be bytes")
    if len(stream_raw) > MAX_STREAM_BYTES:
        raise OpusReceiptValidationError(f"stream_raw exceeds limit of {MAX_STREAM_BYTES} bytes")
    try:
        stream_text = stream_raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise OpusReceiptValidationError(f"stream_raw is not valid UTF-8: {exc}") from exc

    lines = [line for line in stream_text.splitlines() if line.strip()]
    if not lines:
        raise OpusReceiptValidationError("stream is empty")

    events = [_load_json_strict(line) for line in lines]
    if any(not isinstance(e, dict) for e in events):
        raise OpusReceiptValidationError("every stream event must be an object")

    for event in events:
        kind = event.get("event")
        if kind in {"init", "result", "step_update"} and not isinstance(event.get(kind), dict):
            raise OpusReceiptValidationError("malformed CLI event envelope")

    init_events = [e for e in events if e.get("event") == "init"]
    if len(init_events) != 1:
        raise OpusReceiptValidationError("stream must contain exactly one init event")

    init_event = init_events[0]
    session = init_event.get("conversation_id")
    if not isinstance(session, str) or not session.strip():
        raise OpusReceiptValidationError("init event missing non-empty top-level conversation_id")

    if init_event["init"].get("model") != EXPECTED_MODEL:
        raise OpusReceiptValidationError(
            f"init event model must be {EXPECTED_MODEL!r}, got {init_event['init'].get('model')!r}"
        )

    result_events = [e for e in events if e.get("event") == "result"]
    if len(result_events) != 1:
        raise OpusReceiptValidationError("stream must contain exactly one result event")

    if events[-1].get("event") != "result":
        raise OpusReceiptValidationError("terminal result event must be LAST in stream")

    terminal_result = events[-1]["result"]
    if terminal_result.get("conversation_id") != session:
        raise OpusReceiptValidationError("terminal result conversation_id does not match session")
    if "conversation_id" in events[-1] and events[-1].get("conversation_id") != session:
        raise OpusReceiptValidationError("terminal event top-level conversation_id does not match session")

    qualifying_forbidden_found = False

    for event in events:
        event_cid = event.get("conversation_id")
        if "conversation_id" in event and event_cid != session:
            raise OpusReceiptValidationError("event conversation_id does not match session")

        is_event_forbidden = _is_explicit_forbidden_evidence(event)

        if event.get("event") == "step_update":
            step = event["step_update"]
            step_cid = step.get("conversation_id")
            if "conversation_id" in step and step_cid != session:
                raise OpusReceiptValidationError("step_update conversation_id does not match session")

            is_step_forbidden = _is_explicit_forbidden_evidence(step)
            if is_step_forbidden or is_event_forbidden:
                has_matching_id = (event_cid == session) or (step_cid == session)
                if not has_matching_id:
                    raise OpusReceiptValidationError(
                        "qualifying step_update missing explicit matching event or step ID"
                    )
                qualifying_forbidden_found = True
        elif is_event_forbidden:
            if event_cid != session:
                raise OpusReceiptValidationError("forbidden event missing matching conversation_id")
            qualifying_forbidden_found = True

    if not qualifying_forbidden_found:
        raise OpusReceiptValidationError("stream contains no qualifying explicit forbidden tool evidence")

    status_sha = hashlib.sha256(status_raw).hexdigest()
    task_sha = hashlib.sha256(task_raw).hexdigest()
    stream_sha = hashlib.sha256(stream_raw).hexdigest()

    return {
        "schema_version": 1,
        "reason": "FORBIDDEN_TOOL_ATTEMPT",
        "run_id": expected_run_id,
        "task_id": expected_task["task_id"],
        "head": expected_head,
        "conversation_id": session,
        "status_sha256": status_sha,
        "task_sha256": task_sha,
        "snapshot_sha256": task_sha,
        "stream_sha256": stream_sha,
    }
