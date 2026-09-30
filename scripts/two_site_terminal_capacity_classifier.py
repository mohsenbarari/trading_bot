"""Pure structural classifier for Codex terminal model-capacity event stream and failed run status."""

from __future__ import annotations

import json
import math

CAPACITY_MESSAGE = "Selected model is at capacity. Please try a different model."
AUTOPILOT_ERROR_MESSAGE = "AutopilotError: Codex CLI exited with status 1"
REQUIRED_SCOPE_KEYS = frozenset({"assignment_id", "stage_id", "sha256"})
MAX_STREAM_BYTES = 64 * 1024


def _no_dup_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    res: dict[str, object] = {}
    for k, v in pairs:
        if k in res:
            raise ValueError(f"duplicate key: {k}")
        res[k] = v
    return res


def classify_terminal_capacity_stream(raw: bytes) -> bool:
    """Classify whether raw bytes represent the exact Codex model capacity failure stream.

    Grammar:
    Exactly five JSONL events:
      0: {"type": "thread.started", "thread_id": <nonempty str>}
      1: {"type": "item.completed", "item": {"type": "error", "id": <nonempty str>, "message": <nonempty str>}}
      2: {"type": "turn.started"}
      3: {"type": "error", "message": CAPACITY_MESSAGE}
      4: {"type": "turn.failed", "error": {"message": CAPACITY_MESSAGE}}

    Or four-event variant omitting item.completed:
      0: {"type": "thread.started", "thread_id": <nonempty str>}
      1: {"type": "turn.started"}
      2: {"type": "error", "message": CAPACITY_MESSAGE}
      3: {"type": "turn.failed", "error": {"message": CAPACITY_MESSAGE}}

    Must end with newline. No extra keys, no other events.
    Returns False on any malformed, oversized, or non-matching stream.
    """
    if not isinstance(raw, bytes):
        return False
    if len(raw) == 0 or len(raw) > MAX_STREAM_BYTES:
        return False
    if not raw.endswith(b"\n"):
        return False

    body = raw[:-1]
    if body.endswith(b"\n"):
        return False
    if b"\r" in body:
        return False

    raw_lines = body.split(b"\n")
    if len(raw_lines) not in (4, 5):
        return False

    events: list[dict[str, object]] = []
    for line in raw_lines:
        if not line or line.isspace():
            return False
        try:
            line_str = line.decode("utf-8")
            parsed = json.loads(line_str, object_pairs_hook=_no_dup_pairs)
        except Exception:
            return False
        if not isinstance(parsed, dict):
            return False
        events.append(parsed)

    # Event 0: thread.started
    evt0 = events[0]
    if set(evt0.keys()) != {"type", "thread_id"}:
        return False
    if evt0.get("type") != "thread.started":
        return False
    tid = evt0.get("thread_id")
    if not isinstance(tid, str) or not tid or len(tid) > 256:
        return False

    if len(events) == 5:
        # Event 1: item.completed warning
        evt1 = events[1]
        if set(evt1.keys()) != {"type", "item"}:
            return False
        if evt1.get("type") != "item.completed":
            return False
        item = evt1.get("item")
        if not isinstance(item, dict):
            return False
        if set(item.keys()) != {"type", "id", "message"}:
            return False
        if item.get("type") != "error":
            return False
        iid = item.get("id")
        imsg = item.get("message")
        if not isinstance(iid, str) or not iid or len(iid) > 256:
            return False
        if not isinstance(imsg, str) or not imsg or len(imsg) > 4096:
            return False

        # Event 2: turn.started
        evt2 = events[2]
        if set(evt2.keys()) != {"type"}:
            return False
        if evt2.get("type") != "turn.started":
            return False

        # Event 3: error
        evt3 = events[3]
        if set(evt3.keys()) != {"type", "message"}:
            return False
        if evt3.get("type") != "error":
            return False
        if evt3.get("message") != CAPACITY_MESSAGE:
            return False

        # Event 4: turn.failed
        evt4 = events[4]
        if set(evt4.keys()) != {"type", "error"}:
            return False
        if evt4.get("type") != "turn.failed":
            return False
        err = evt4.get("error")
        if not isinstance(err, dict):
            return False
        if set(err.keys()) != {"message"}:
            return False
        if err.get("message") != CAPACITY_MESSAGE:
            return False

        return True

    # 4-event variant: turn.started, error, turn.failed
    evt1 = events[1]
    if set(evt1.keys()) != {"type"}:
        return False
    if evt1.get("type") != "turn.started":
        return False

    evt2 = events[2]
    if set(evt2.keys()) != {"type", "message"}:
        return False
    if evt2.get("type") != "error":
        return False
    if evt2.get("message") != CAPACITY_MESSAGE:
        return False

    evt3 = events[3]
    if set(evt3.keys()) != {"type", "error"}:
        return False
    if evt3.get("type") != "turn.failed":
        return False
    err = evt3.get("error")
    if not isinstance(err, dict):
        return False
    if set(err.keys()) != {"message"}:
        return False
    if err.get("message") != CAPACITY_MESSAGE:
        return False

    return True


def failed_run_matches_repository(
    status: object,
    expected_head: str,
    expected_refs: dict[str, str],
    expected_assignment_scope: dict[str, str],
) -> bool:
    """Classify whether status represents a real failed run for the expected repository state.

    Validates:
      - state == 'FAILED'
      - head_before == expected_head
      - protected_refs == expected_refs
      - assignment_scope == expected_assignment_scope (keys: assignment_id, stage_id, sha256)
      - finite numeric started_at and finished_at with started_at <= finished_at
      - error == 'AutopilotError: Codex CLI exited with status 1'
      - no result/receipt claim in status
    """
    if not isinstance(status, dict):
        return False
    if len(status) > 100:
        return False

    if not isinstance(expected_head, str) or not expected_head or len(expected_head) > 256:
        return False

    if not isinstance(expected_refs, dict) or len(expected_refs) > 100:
        return False
    for rk, rv in expected_refs.items():
        if not isinstance(rk, str) or not isinstance(rv, str) or len(rk) > 256 or len(rv) > 256:
            return False

    if not isinstance(expected_assignment_scope, dict):
        return False
    if set(expected_assignment_scope.keys()) != REQUIRED_SCOPE_KEYS:
        return False
    for sk, sv in expected_assignment_scope.items():
        if not isinstance(sk, str) or not isinstance(sv, str) or not sv or len(sv) > 256:
            return False

    # Check state
    if status.get("state") != "FAILED":
        return False

    # Check head_before
    if status.get("head_before") != expected_head:
        return False

    # Check protected_refs
    actual_refs = status.get("protected_refs")
    if not isinstance(actual_refs, dict):
        return False
    if actual_refs != expected_refs:
        return False

    # Check assignment_scope
    actual_scope = status.get("assignment_scope")
    if not isinstance(actual_scope, dict):
        return False
    if set(actual_scope.keys()) != REQUIRED_SCOPE_KEYS:
        return False
    if actual_scope != expected_assignment_scope:
        return False

    # Check started_at and finished_at
    started_at = status.get("started_at")
    finished_at = status.get("finished_at")
    if type(started_at) not in (int, float) or type(finished_at) not in (int, float):
        return False
    if not (math.isfinite(started_at) and math.isfinite(finished_at)):
        return False
    if started_at > finished_at:
        return False

    # Check error
    if status.get("error") != AUTOPILOT_ERROR_MESSAGE:
        return False

    # Ensure no result or receipt claim
    for k, v in status.items():
        if not isinstance(k, str):
            return False
        k_lower = k.lower()
        if ("result" in k_lower or "receipt" in k_lower) and v is not None:
            return False

    return True
