"""Hermetic integration tests for controller-tagged provider telemetry and record_cycle."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts import two_site_cycle_metrics as ledger
from scripts import two_site_provider_metrics as metrics

HEAD = "a" * 40
TOKENS = {"input_tokens": 100, "output_tokens": 40, "total_tokens": 140}
SENTINEL = "CONFIDENTIAL_TELEMETRY_SENTINEL_آزمون_\u200c_TOKEN_20260913_XYZ"


def _make_cycle_run(tmp_path: Path, run_name: str = "ctrl-run-1") -> tuple[Path, Path]:
    state = tmp_path / ".local/two-site-refactor/autopilot"
    run = state / "runs" / run_name
    run.mkdir(parents=True, exist_ok=True)
    (run / "stream.jsonl").write_text("{}\n")
    (run / "prompt.txt").write_text("hermetic cycle prompt text\n")
    return state, run


def _write_task_snapshot(
    prop_dir: Path,
    task_id: str,
    head: str = HEAD,
    gap_ref: str = "docs/memory/brief.md#اتوماسیون-فعال",
    requirements: list[str] | None = None,
    test_selectors: list[str] | None = None,
    goal_prefix: str = "Hermetic integration task scope",
) -> tuple[dict, str]:
    if requirements is None:
        requirements = ["req_alpha", "req_beta"]
    if test_selectors is None:
        test_selectors = ["pytest -q test_hermetic.py"]
    marker = "\nCANONICAL_SOURCE_EXCERPTS_JSON:\n"
    excerpt_payload = json.dumps({"gap_reference": gap_ref})
    task_dict = {
        "task_id": task_id,
        "goal": f"{goal_prefix}{marker}{excerpt_payload}",
        "requirements": requirements,
        "test_selectors": test_selectors,
    }
    task_file_content = {
        "source_head": head,
        "task": task_dict,
    }
    (prop_dir / "task.json").write_text(json.dumps(task_file_content))
    task_sha = hashlib.sha256(json.dumps(task_dict, sort_keys=True).encode("utf-8")).hexdigest()
    return task_dict, task_sha


def _create_proposal_attempt(
    root: Path,
    name: str,
    controller_run_id: str | None = None,
    task_id: str = "task-1",
    head: str = HEAD,
    role: str = "worker",
    state: str = "PROPOSAL_READY",
    usage: dict | None = TOKENS,
    duration_seconds: float = 8.0,
    stream_usage: dict | None = None,
    stream_response: str | None = None,
    write_task: bool = True,
    gap_ref: str = "docs/memory/brief.md#اتوماسیون-فعال",
    requirements: list[str] | None = None,
    test_selectors: list[str] | None = None,
    goal_prefix: str = "Scope description",
    **extra_status,
) -> tuple[Path, str]:
    prop_dir = root / ".local/two-site-refactor/proposals" / name
    prop_dir.mkdir(parents=True, exist_ok=True)
    status: dict = {
        "run_id": name,
        "task_id": task_id,
        "head": head,
        "role": role,
        "state": state,
        "usage": usage,
        "duration_seconds": duration_seconds,
    }
    if controller_run_id is not None:
        status["controller_run_id"] = controller_run_id
    status.update(extra_status)
    (prop_dir / "status.json").write_text(json.dumps(status))

    if stream_usage is not None or stream_response is not None:
        event = {
            "event": "result",
            "result": {
                "usage": stream_usage or TOKENS,
                "response": stream_response or "stream output",
            },
        }
        (prop_dir / "stream.jsonl").write_text(json.dumps(event) + "\n")

    task_sha = ""
    if write_task:
        _, task_sha = _write_task_snapshot(
            prop_dir,
            task_id=task_id,
            head=head,
            gap_ref=gap_ref,
            requirements=requirements,
            test_selectors=test_selectors,
            goal_prefix=goal_prefix,
        )
    return prop_dir, task_sha


def test_failed_or_truncated_controller_cycle_records_observed_attempts_and_metadata(tmp_path):
    state, run = _make_cycle_run(tmp_path, "ctrl-run-failed-01")
    reqs = ["req-1", "req-2", "req-3"]
    selectors = ["pytest -q test_a.py", "pytest -q test_b.py"]
    gap = "docs/memory/brief.md#اتوماسیون-فعال"
    attempt_usage = {"input_tokens": 120, "output_tokens": 45, "total_tokens": 165}

    _, expected_task_sha = _create_proposal_attempt(
        tmp_path,
        "ctrl-worker-failed-0",
        controller_run_id="ctrl-run-failed-01",
        task_id="task-controller-failed",
        head=HEAD,
        role="worker",
        state="BLOCKED",
        usage=None,
        stream_usage=attempt_usage,
        stream_response="model truncated at token boundary",
        gap_ref=gap,
        requirements=reqs,
        test_selectors=selectors,
    )

    # Failed/truncated controller cycle with public final identifiers absent
    meta = {
        "state": "FAILED",
        "head_before": HEAD,
        "duration_seconds": 25.0,
    }
    record = ledger.record_cycle(tmp_path, state, run, meta, model="gemini-test", effort="high")

    # Prove provider telemetry state, usage, and first_pass null
    prov = record["provider_metrics"]
    assert prov["state"] == "OBSERVED_CONTROLLER_ATTEMPTS"
    assert prov["first_pass"] is None
    assert prov["failed_attempts"] == 1
    assert prov["attempts"] == 1
    assert prov["worker_attempts"] == 1
    assert prov["reviewer_attempts"] == 0
    assert prov["usage"] == attempt_usage
    assert prov["observed_usage_subtotal"] == attempt_usage

    # Prove exact top-level five-field task metadata
    assert record["task_id"] == "task-controller-failed"
    assert record["task_sha256"] == expected_task_sha
    assert record["declared_requirement_count"] == 3
    assert record["focused_selector_count"] == 2
    assert record["gap_reference"] == gap

    # Prove all three public final identifiers remain absent
    assert "gemini_worker_run_id" not in record
    assert "gemini_reviewer_run_id" not in record
    assert "proposal_sha256" not in record

    # Prove persisted run metrics and ledger preserve the exact contract
    written_run = json.loads((run / "metrics.json").read_text())
    assert written_run == record
    ledger_records = json.loads((state / "metrics.json").read_text())
    assert len(ledger_records) == 1
    assert ledger_records[0] == record


def test_controller_partial_evidence_never_falls_back_to_historical_prefix_chain(tmp_path):
    # Set up complete historical final-worker-prefix chain
    hist_reqs = ["hist_req_1"]
    hist_selectors = ["pytest -q test_hist.py"]
    _create_proposal_attempt(
        tmp_path,
        "hist-worker-0",
        task_id="hist-task",
        head=HEAD,
        role="worker",
        state="PROPOSAL_READY",
        usage=TOKENS,
        duration_seconds=6.0,
        gap_ref="docs/memory/brief.md#تاریخچه",
        requirements=hist_reqs,
        test_selectors=hist_selectors,
    )
    _create_proposal_attempt(
        tmp_path,
        "hist-reviewer-0",
        task_id="hist-task",
        head=HEAD,
        role="verifier",
        state="VERIFIED",
        usage=TOKENS,
        duration_seconds=4.0,
        input_worker_run_id="hist-worker-0",
        gap_ref="docs/memory/brief.md#تاریخچه",
        requirements=hist_reqs,
        test_selectors=hist_selectors,
    )

    # Set up controller-tagged attempt that yields PARTIAL evidence (e.g. mismatched git head)
    state, run = _make_cycle_run(tmp_path, "ctrl-run-partial-01")
    _create_proposal_attempt(
        tmp_path,
        "crun-worker-0",
        controller_run_id="ctrl-run-partial-01",
        task_id="ctrl-task",
        head="b" * 40,  # mismatch with HEAD
        role="worker",
        state="PROPOSAL_READY",
        usage=TOKENS,
    )

    meta = {
        "state": "FAILED",
        "head_before": HEAD,
        "gemini_worker_run_id": "hist-worker-0",
        "duration_seconds": 30.0,
    }
    record = ledger.record_cycle(tmp_path, state, run, meta, model="gemini-test", effort="high")

    # Controller PARTIAL evidence must NOT fall back to historical prefix chain
    assert record["provider_metrics"]["state"] == "PARTIAL"
    assert record["provider_metrics"]["first_pass"] is None
    assert record["task_id"] != "hist-task" if "task_id" in record else True
    assert record.get("declared_requirement_count") is None
    assert record.get("focused_selector_count") is None
    assert record.get("gap_reference") is None


def test_historical_fallback_preserved_when_no_controller_tagged_evidence(tmp_path):
    hist_reqs = ["hist_req_1", "hist_req_2"]
    hist_selectors = ["pytest -q test_hist.py"]
    gap = "docs/memory/brief.md#تاریخچه"
    _, expected_hist_sha = _create_proposal_attempt(
        tmp_path,
        "hist-worker-0",
        task_id="hist-task",
        head=HEAD,
        role="worker",
        state="PROPOSAL_READY",
        usage=TOKENS,
        duration_seconds=5.0,
        gap_ref=gap,
        requirements=hist_reqs,
        test_selectors=hist_selectors,
    )
    _create_proposal_attempt(
        tmp_path,
        "hist-reviewer-0",
        task_id="hist-task",
        head=HEAD,
        role="verifier",
        state="VERIFIED",
        usage=TOKENS,
        duration_seconds=5.0,
        input_worker_run_id="hist-worker-0",
        gap_ref=gap,
        requirements=hist_reqs,
        test_selectors=hist_selectors,
    )

    state, run = _make_cycle_run(tmp_path, "ctrl-run-no-tagged-01")
    meta = {
        "state": "CONTINUE",
        "head_before": HEAD,
        "gemini_worker_run_id": "hist-worker-0",
        "duration_seconds": 40.0,
    }
    record = ledger.record_cycle(tmp_path, state, run, meta, model="gemini-test", effort="high")

    # Historical fallback must succeed and populate matching telemetry and metadata
    assert record["provider_metrics"]["state"] == "OBSERVED_MATCHING_ATTEMPTS"
    assert record["provider_metrics"]["first_pass"] is True
    assert record["task_id"] == "hist-task"
    assert record["task_sha256"] == expected_hist_sha
    assert record["declared_requirement_count"] == 2
    assert record["focused_selector_count"] == 1
    assert record["gap_reference"] == gap


@pytest.mark.parametrize("exc_cls", [ValueError, TypeError, KeyError, OSError, OverflowError])
def test_provider_validation_failure_is_nonblocking_and_records_diagnostic(tmp_path, monkeypatch, exc_cls):
    state, run = _make_cycle_run(tmp_path, f"ctrl-run-err-{exc_cls.__name__}")
    meta = {
        "state": "FAILED",
        "head_before": HEAD,
        "duration_seconds": 15.0,
    }

    def _raise(*args, **kwargs):
        raise exc_cls("simulated provider metrics exception")

    monkeypatch.setattr(ledger, "_call_provider_metrics", _raise)

    record = ledger.record_cycle(tmp_path, state, run, meta, model="gemini-test", effort="medium")

    assert record["state"] == "FAILED"
    assert record["provider_metrics"]["state"] == "UNAVAILABLE"
    assert record["provider_metrics"]["first_pass"] is None
    assert record["provider_metrics"]["diagnostic"] == exc_cls.__name__
    assert record["provider_metrics"]["rework_taxonomy"] == {
        "state": "UNAVAILABLE",
        "initial_outcome": "UNKNOWN",
        "rework_path": "UNKNOWN",
        "terminal_outcome": "UNKNOWN",
    }
    assert json.loads((run / "metrics.json").read_text()) == record


def test_sentinel_suppression_across_fields_and_streams(tmp_path):
    state, run = _make_cycle_run(tmp_path, "ctrl-run-sentinel-01")

    # Place sentinel in run prompt and stream
    (run / "prompt.txt").write_text(f"Run prompt with {SENTINEL}\n")
    (run / "stream.jsonl").write_text(
        json.dumps({
            "type": "item.completed",
            "item": {"type": "command_execution", "command": f"echo {SENTINEL}"},
        }) + "\n"
    )

    # Place sentinel in proposal task prose, status free-text, error, command, conversation-like fields, and stream
    gap = "docs/memory/brief.md#اتوماسیون-فعال"
    _create_proposal_attempt(
        tmp_path,
        "ctrl-sentinel-worker-0",
        controller_run_id="ctrl-run-sentinel-01",
        task_id="task-sentinel-clean",
        head=HEAD,
        role="worker",
        state="BLOCKED",
        usage=None,
        stream_usage=TOKENS,
        stream_response=f"Sensitive stream text containing {SENTINEL}",
        gap_ref=gap,
        goal_prefix=f"Goal prose embedding {SENTINEL}",
        error=SENTINEL,
        error_text=f"Failure with {SENTINEL}",
        command=f"run_tool --arg={SENTINEL}",
        response=SENTINEL,
        conversation_id=SENTINEL,
        summary=f"Summary of {SENTINEL}",
        free_text=SENTINEL,
        stdout=f"Output {SENTINEL}",
        stderr=f"Stderr {SENTINEL}",
    )

    meta = {
        "state": "FAILED",
        "head_before": HEAD,
        "duration_seconds": 20.0,
    }
    record = ledger.record_cycle(tmp_path, state, run, meta, model="gemini-test", effort="medium")

    # Assert sentinel appears nowhere in returned record, run metrics.json, or ledger metrics.json
    serialized_record = json.dumps(record)
    assert SENTINEL not in serialized_record
    assert "CONFIDENTIAL_TELEMETRY_SENTINEL" not in serialized_record

    run_metrics_text = (run / "metrics.json").read_text(encoding="utf-8")
    assert SENTINEL not in run_metrics_text
    assert "CONFIDENTIAL_TELEMETRY_SENTINEL" not in run_metrics_text

    ledger_text = (state / "metrics.json").read_text(encoding="utf-8")
    assert SENTINEL not in ledger_text
    assert "CONFIDENTIAL_TELEMETRY_SENTINEL" not in ledger_text
