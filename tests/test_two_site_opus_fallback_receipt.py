from __future__ import annotations

import json
import sys
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.two_site_opus_fallback_receipt import (
    OpusReceiptValidationError,
    validate_opus_failure_receipt,
)


def _make_status_dict(
    *,
    run_id: str = "run-opus-459",
    task_id: str = "task-459",
    head: str = "0655392de0594432d41a73641e5134376b4c9e43",
    error: str = "ForbiddenToolAttemptError: tool attempt invalidates a proposal-only run",
    **extra,
) -> dict:
    data = {
        "run_id": run_id,
        "task_id": task_id,
        "role": "worker",
        "head": head,
        "model": "claude-opus-4-6-thinking",
        "effort": "model-default",
        "state": "BLOCKED",
        "started_at": 1770000000.0,
        "duration_seconds": 4.2,
        "authority": "PROPOSAL_ONLY_NOT_ACCEPTANCE",
        "cli": "cli-1.1.27",
        "cli_source": "test",
        "session_gate": "gate-001",
        "error": error,
    }
    data.update(extra)
    return data


def _make_snapshot_dict(
    expected_task: dict,
    expected_sources: dict,
    expected_head: str,
    *,
    feedback: str | None = None,
    proposal: str | None = None,
    **extra,
) -> dict:
    data = {
        "task": expected_task,
        "sources": expected_sources,
        "source_head": expected_head,
    }
    if feedback is not None:
        data["feedback"] = feedback
    if proposal is not None:
        data["proposal"] = proposal
    data.update(extra)
    return data


def _make_stream_bytes(session_id: str = "conv-opus-123", *, tool_name: str = "view_file") -> bytes:
    lines = [
        json.dumps({"event": "init", "conversation_id": session_id, "init": {"model": "claude-opus-4-6-thinking"}}),
        json.dumps({"event": "step_update", "conversation_id": session_id, "step_update": {"conversation_id": session_id, "tool_name": tool_name}}),
        json.dumps({"event": "result", "conversation_id": session_id, "result": {"conversation_id": session_id, "status": "ERROR", "error": "ForbiddenToolAttemptError: tool attempt invalidates a proposal-only run"}}),
    ]
    return "\n".join(lines).encode("utf-8")


def test_realistic_initial_worker_receipt_valid():
    expected_run_id = "run-opus-initial-001"
    expected_task = {"task_id": "task-opus-001", "allowed_files": ["a.py"]}
    expected_sources = {"a.py": {"content": "# initial"}}
    expected_head = "0655392de0594432d41a73641e5134376b4c9e43"
    session_id = "conv-opus-init-459"

    status_raw = json.dumps(_make_status_dict(run_id=expected_run_id, task_id="task-opus-001", head=expected_head)).encode("utf-8")
    task_raw = json.dumps(_make_snapshot_dict(expected_task, expected_sources, expected_head)).encode("utf-8")
    stream_raw = _make_stream_bytes(session_id=session_id, tool_name="view_file")

    receipt = validate_opus_failure_receipt(
        status_raw, task_raw, stream_raw,
        expected_run_id=expected_run_id,
        expected_task=expected_task,
        expected_sources=expected_sources,
        expected_head=expected_head,
    )

    assert receipt["schema_version"] == 1
    assert receipt["reason"] == "FORBIDDEN_TOOL_ATTEMPT"
    assert receipt["run_id"] == expected_run_id
    assert receipt["task_id"] == "task-opus-001"
    assert receipt["head"] == expected_head
    assert receipt["conversation_id"] == session_id
    assert "status_sha256" in receipt
    assert "task_sha256" in receipt
    assert "stream_sha256" in receipt


def test_realistic_correction_worker_receipt_valid():
    expected_run_id = "run-opus-correction-002"
    expected_task = {"task_id": "task-opus-002", "allowed_files": ["b.py"]}
    expected_sources = {"b.py": {"content": "# candidate"}}
    expected_head = "0655392de0594432d41a73641e5134376b4c9e43"
    session_id = "conv-opus-corr-459"

    status_raw = json.dumps(
        _make_status_dict(
            run_id=expected_run_id,
            task_id="task-opus-002",
            head=expected_head,
            input_worker_run_id="prior-run-001",
            input_proposal_sha256="sha-prop-001",
            input_feedback_sha256="sha-feed-001",
        )
    ).encode("utf-8")
    task_raw = json.dumps(
        _make_snapshot_dict(
            expected_task,
            expected_sources,
            expected_head,
            feedback="controller feedback text",
            proposal="prior proposal text",
        )
    ).encode("utf-8")
    stream_raw = _make_stream_bytes(session_id=session_id, tool_name="run_command")

    receipt = validate_opus_failure_receipt(
        status_raw, task_raw, stream_raw,
        expected_run_id=expected_run_id,
        expected_task=expected_task,
        expected_sources=expected_sources,
        expected_head=expected_head,
    )

    assert receipt["schema_version"] == 1
    assert receipt["reason"] == "FORBIDDEN_TOOL_ATTEMPT"
    assert receipt["run_id"] == expected_run_id
    assert receipt["task_id"] == "task-opus-002"
    assert receipt["head"] == expected_head
    assert receipt["conversation_id"] == session_id


def test_correction_snapshot_extras_paired_positive_and_negative():
    task = {"task_id": "task-003"}
    sources = {"x.py": {"content": ""}}
    head = "0655392de0594432d41a73641e5134376b4c9e43"
    status_raw = json.dumps(_make_status_dict(task_id="task-003", head=head)).encode("utf-8")
    stream_raw = _make_stream_bytes()

    snap_only_feedback = json.dumps(_make_snapshot_dict(task, sources, head, feedback="fb")).encode("utf-8")
    with pytest.raises(OpusReceiptValidationError, match="must include both 'feedback' and 'proposal'"):
        validate_opus_failure_receipt(status_raw, snap_only_feedback, stream_raw, expected_run_id="run-opus-459", expected_task=task, expected_sources=sources, expected_head=head)

    snap_only_proposal = json.dumps(_make_snapshot_dict(task, sources, head, proposal="prop")).encode("utf-8")
    with pytest.raises(OpusReceiptValidationError, match="must include both 'feedback' and 'proposal'"):
        validate_opus_failure_receipt(status_raw, snap_only_proposal, stream_raw, expected_run_id="run-opus-459", expected_task=task, expected_sources=sources, expected_head=head)

    snap_unknown_extra = json.dumps(_make_snapshot_dict(task, sources, head, feedback="fb", proposal="prop", test_evidence="extra")).encode("utf-8")
    with pytest.raises(OpusReceiptValidationError, match="unallowed extra keys"):
        validate_opus_failure_receipt(status_raw, snap_unknown_extra, stream_raw, expected_run_id="run-opus-459", expected_task=task, expected_sources=sources, expected_head=head)

    snap_head_alias = json.dumps({"task": task, "sources": sources, "source_head": head, "head": head}).encode("utf-8")
    with pytest.raises(OpusReceiptValidationError, match="'head' alias"):
        validate_opus_failure_receipt(status_raw, snap_head_alias, stream_raw, expected_run_id="run-opus-459", expected_task=task, expected_sources=sources, expected_head=head)

    status_source_head_alias = json.dumps(_make_status_dict(task_id="task-003", head=head, source_head=head)).encode("utf-8")
    task_raw = json.dumps(_make_snapshot_dict(task, sources, head)).encode("utf-8")
    with pytest.raises(OpusReceiptValidationError, match="'source_head' alias"):
        validate_opus_failure_receipt(status_source_head_alias, task_raw, stream_raw, expected_run_id="run-opus-459", expected_task=task, expected_sources=sources, expected_head=head)


def test_tamper_mandatory_status_and_snapshot_fields():
    task = {"task_id": "task-004"}
    sources = {"y.py": {"content": ""}}
    head = "0655392de0594432d41a73641e5134376b4c9e43"
    task_raw = json.dumps(_make_snapshot_dict(task, sources, head)).encode("utf-8")
    stream_raw = _make_stream_bytes()

    def check_bad_status(overrides, match):
        s = _make_status_dict(task_id="task-004", head=head)
        s.update(overrides)
        with pytest.raises(OpusReceiptValidationError, match=match):
            validate_opus_failure_receipt(json.dumps(s).encode("utf-8"), task_raw, stream_raw, expected_run_id="run-opus-459", expected_task=task, expected_sources=sources, expected_head=head)

    check_bad_status({"role": "verifier"}, "role 'worker'")
    check_bad_status({"run_id": "foreign-run"}, "run_id")
    check_bad_status({"task_id": "wrong-task"}, "task_id")
    check_bad_status({"head": "bad-head"}, "status head")
    check_bad_status({"model": "gemini-2.5-pro"}, "status model")
    check_bad_status({"state": "SUCCESS"}, "state 'BLOCKED'")
    check_bad_status({"error": "RuntimeError: CLI timeout"}, "ForbiddenToolAttemptError:")

    valid_status_raw = json.dumps(_make_status_dict(task_id="task-004", head=head)).encode("utf-8")
    bad_task = json.dumps(_make_snapshot_dict({"task_id": "mismatched"}, sources, head)).encode("utf-8")
    with pytest.raises(OpusReceiptValidationError, match="snapshot task does not match"):
        validate_opus_failure_receipt(valid_status_raw, bad_task, stream_raw, expected_run_id="run-opus-459", expected_task=task, expected_sources=sources, expected_head=head)


def test_tamper_stream_invariants():
    task = {"task_id": "task-005"}
    sources = {"z.py": {"content": ""}}
    head = "0655392de0594432d41a73641e5134376b4c9e43"
    session_id = "conv-main-005"
    status_raw = json.dumps(_make_status_dict(task_id="task-005", head=head)).encode("utf-8")
    task_raw = json.dumps(_make_snapshot_dict(task, sources, head)).encode("utf-8")

    # Foreign session in event
    lines = [
        json.dumps({"event": "init", "conversation_id": session_id, "init": {"model": "claude-opus-4-6-thinking"}}),
        json.dumps({"event": "step_update", "conversation_id": "foreign-session", "step_update": {"conversation_id": session_id, "tool_name": "view_file"}}),
        json.dumps({"event": "result", "conversation_id": session_id, "result": {"conversation_id": session_id, "status": "ERROR"}}),
    ]
    with pytest.raises(OpusReceiptValidationError, match="conversation_id does not match session"):
        validate_opus_failure_receipt(status_raw, task_raw, "\n".join(lines).encode("utf-8"), expected_run_id="run-opus-459", expected_task=task, expected_sources=sources, expected_head=head)

    # Foreign session in step
    lines[1] = json.dumps({"event": "step_update", "conversation_id": session_id, "step_update": {"conversation_id": "foreign-step", "tool_name": "view_file"}})
    with pytest.raises(OpusReceiptValidationError, match="step_update conversation_id does not match"):
        validate_opus_failure_receipt(status_raw, task_raw, "\n".join(lines).encode("utf-8"), expected_run_id="run-opus-459", expected_task=task, expected_sources=sources, expected_head=head)

    # Missing step ID (neither event nor step has conversation_id)
    lines[1] = json.dumps({"event": "step_update", "step_update": {"tool_name": "view_file"}})
    with pytest.raises(OpusReceiptValidationError, match="missing explicit matching event or step ID"):
        validate_opus_failure_receipt(status_raw, task_raw, "\n".join(lines).encode("utf-8"), expected_run_id="run-opus-459", expected_task=task, expected_sources=sources, expected_head=head)

    # Ambiguous finish: tool_name == 'finish'
    lines[1] = json.dumps({"event": "step_update", "conversation_id": session_id, "step_update": {"conversation_id": session_id, "tool_name": "finish"}})
    with pytest.raises(OpusReceiptValidationError, match="no qualifying explicit forbidden tool evidence"):
        validate_opus_failure_receipt(status_raw, task_raw, "\n".join(lines).encode("utf-8"), expected_run_id="run-opus-459", expected_task=task, expected_sources=sources, expected_head=head)

    # Ambiguous finish: tool_info name == 'finish'
    lines[1] = json.dumps({"event": "step_update", "conversation_id": session_id, "step_update": {"conversation_id": session_id, "tool_info": {"name": "finish"}}})
    with pytest.raises(OpusReceiptValidationError, match="no qualifying explicit forbidden tool evidence"):
        validate_opus_failure_receipt(status_raw, task_raw, "\n".join(lines).encode("utf-8"), expected_run_id="run-opus-459", expected_task=task, expected_sources=sources, expected_head=head)

    # Ambiguous finish: tool_name == ''
    lines[1] = json.dumps({"event": "step_update", "conversation_id": session_id, "step_update": {"conversation_id": session_id, "tool_name": ""}})
    with pytest.raises(OpusReceiptValidationError, match="no qualifying explicit forbidden tool evidence"):
        validate_opus_failure_receipt(status_raw, task_raw, "\n".join(lines).encode("utf-8"), expected_run_id="run-opus-459", expected_task=task, expected_sources=sources, expected_head=head)

    # tool_calls or denied text alone without Runner recognized tool_name
    lines[1] = json.dumps({"event": "step_update", "conversation_id": session_id, "step_update": {"conversation_id": session_id, "tool_calls": [{"function": {"name": "view_file"}}], "denied_actions": ["view_file"]}})
    with pytest.raises(OpusReceiptValidationError, match="no qualifying explicit forbidden tool evidence"):
        validate_opus_failure_receipt(status_raw, task_raw, "\n".join(lines).encode("utf-8"), expected_run_id="run-opus-459", expected_task=task, expected_sources=sources, expected_head=head)

    # Result not last
    lines_not_last = [
        json.dumps({"event": "init", "conversation_id": session_id, "init": {"model": "claude-opus-4-6-thinking"}}),
        json.dumps({"event": "result", "conversation_id": session_id, "result": {"conversation_id": session_id, "status": "ERROR"}}),
        json.dumps({"event": "step_update", "conversation_id": session_id, "step_update": {"conversation_id": session_id, "tool_name": "view_file"}}),
    ]
    with pytest.raises(OpusReceiptValidationError, match="result event must be LAST"):
        validate_opus_failure_receipt(status_raw, task_raw, "\n".join(lines_not_last).encode("utf-8"), expected_run_id="run-opus-459", expected_task=task, expected_sources=sources, expected_head=head)


def test_caps_encoding_and_duplicates():
    task = {"task_id": "task-006"}
    sources = {"k.py": {"content": ""}}
    head = "0655392de0594432d41a73641e5134376b4c9e43"
    valid_status = json.dumps(_make_status_dict(task_id="task-006", head=head)).encode("utf-8")
    valid_task = json.dumps(_make_snapshot_dict(task, sources, head)).encode("utf-8")
    valid_stream = _make_stream_bytes()

    # Status size cap
    with pytest.raises(OpusReceiptValidationError, match="status_raw exceeds limit"):
        validate_opus_failure_receipt(b"{" + b" " * 160_001 + b"}", valid_task, valid_stream, expected_run_id="run-opus-459", expected_task=task, expected_sources=sources, expected_head=head)

    # Task size cap
    with pytest.raises(OpusReceiptValidationError, match="task_raw exceeds limit"):
        validate_opus_failure_receipt(valid_status, b"{" + b" " * 160_001 + b"}", valid_stream, expected_run_id="run-opus-459", expected_task=task, expected_sources=sources, expected_head=head)

    # Stream size cap
    with pytest.raises(OpusReceiptValidationError, match="stream_raw exceeds limit"):
        validate_opus_failure_receipt(valid_status, valid_task, b"\n" * 2_000_001, expected_run_id="run-opus-459", expected_task=task, expected_sources=sources, expected_head=head)

    # Duplicate keys in status
    dup_status = b'{"role": "worker", "role": "worker", "run_id": "run-opus-459", "task_id": "task-006", "head": "0655392de0594432d41a73641e5134376b4c9e43", "model": "claude-opus-4-6-thinking", "state": "BLOCKED", "error": "ForbiddenToolAttemptError: x"}'
    with pytest.raises(OpusReceiptValidationError, match="duplicate key"):
        validate_opus_failure_receipt(dup_status, valid_task, valid_stream, expected_run_id="run-opus-459", expected_task=task, expected_sources=sources, expected_head=head)

    # Duplicate keys in task
    dup_task = b'{"task": {"task_id": "task-006"}, "sources": {"k.py": {"content": ""}}, "source_head": "0655392de0594432d41a73641e5134376b4c9e43", "task": {"task_id": "task-006"}}'
    with pytest.raises(OpusReceiptValidationError, match="duplicate key"):
        validate_opus_failure_receipt(valid_status, dup_task, valid_stream, expected_run_id="run-opus-459", expected_task=task, expected_sources=sources, expected_head=head)

    # Duplicate keys in stream line
    dup_stream = b'{"event": "init", "event": "init", "conversation_id": "conv-opus-123", "init": {"model": "claude-opus-4-6-thinking"}}\n' + valid_stream
    with pytest.raises(OpusReceiptValidationError, match="duplicate key"):
        validate_opus_failure_receipt(valid_status, valid_task, dup_stream, expected_run_id="run-opus-459", expected_task=task, expected_sources=sources, expected_head=head)

    # Invalid UTF-8
    with pytest.raises(OpusReceiptValidationError, match="not valid UTF-8"):
        validate_opus_failure_receipt(b"\xff\xfe", valid_task, valid_stream, expected_run_id="run-opus-459", expected_task=task, expected_sources=sources, expected_head=head)
