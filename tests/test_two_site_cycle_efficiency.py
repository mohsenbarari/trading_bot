"""Offline regression tests for bounded continuation and usage accounting."""
import json
import os
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from scripts import two_site_codex_autopilot as ap
from scripts import two_site_cycle_metrics as metrics


def synthetic_scope():
    return ap.AssignmentScope(
        assignment_id="WA-P1-00B-FREEZE-20260904-001",
        stage_id="stage-p1-freeze",
        allowed_paths=("tests/*",),
        forbidden_paths=(".git/*",),
        expires_at="2030-01-01T00:00:00Z",
        sha256="0" * 64,
    )


def success(head="a" * 40):
    return {"state": "CONTINUE", "head_changed": True, "head_after": head,
            "head_before": "b" * 40, "commit": head, "blockers": [],
            "summary": "Accepted one boundary", "next_action": "Read tracked stage card gap B",
            "gemini_worker_run_id": "w1", "gemini_reviewer_run_id": "r1",
            "proposal_sha256": "f" * 64}


def test_hint_is_small_head_bound_and_separate_from_recovery():
    result = success()
    before = {
        "head": result["commit"],
        "protected_refs": {},
        "assignment_scope": synthetic_scope(),
    }
    prompt = ap.prompt_for_cycle(ap.ROOT, 2, before, recovery={"attempt": 1}, continuation=result)
    context = json.loads(prompt.split("SUPERVISOR_CONTEXT_JSON:\n")[1])
    assert context["validated_previous_cycle_hint"]["next_action"] == result["next_action"]
    assert context["recovery"] == {"attempt": 1}
    assert "blockers" not in context["validated_previous_cycle_hint"]


@pytest.mark.parametrize("change", [
    {"head_after": "c" * 40}, {"commit": None}, {"state": "BLOCKED"},
    {"head_changed": False}, {"blockers": ["rejected"]}, {"next_action": "x" * 8001},
    {"summary": None}, {"proposal_sha256": "wrong"}, {"gemini_worker_run_id": "../bad"},
])
def test_invalid_hints_are_omitted(change):
    assert ap.continuation_hint({**success(), **change}, "a" * 40) is None
    assert ap.continuation_hint(None, "a" * 40) is None


def test_loop_propagates_only_success_and_clears_after_failure(tmp_path):
    calls = []
    def cycle(root, codex, state, number, timeout, recovery=None, **kwargs):
        calls.append(kwargs)
        if number == 2:
            raise ap.AutopilotError("transient test error")
        return state / "runs" / "fake", success()
    with patch.object(ap, "git", side_effect=lambda *a: "" if "status" in a else "a" * 40), \
         patch.object(ap, "ref_snapshot", return_value={}), \
         patch.object(ap, "run_cycle", side_effect=cycle), \
         patch.object(ap, "prune_runs", return_value=[]), \
         patch.object(ap.time, "sleep"):
        ap.run_loop(tmp_path, Path("codex"), 3, 10, False)
    assert calls[0] == {}
    assert calls[1]["continuation"]["commit"] == "a" * 40
    assert calls[2] == {}


def write_stream(path, events):
    path.write_text("".join(json.dumps(x) + "\n" for x in events))


def turn(total=100, cached=90, output=5):
    return {"type": "turn.completed", "usage": {
        "input_tokens": total, "cached_input_tokens": cached, "output_tokens": output,
        "reasoning_output_tokens": min(3, output)}}


def test_usage_sums_turns_without_double_counting_reasoning(tmp_path):
    path = tmp_path / "stream.jsonl"
    write_stream(path, [turn(), {"type": "item.completed", "item": {
        "type": "command_execution", "command": "private command text"}}, turn(20, 10, 2)])
    value = metrics.usage_from_stream(tmp_path, path)
    assert value["input_tokens"] == 120
    assert value["uncached_input_tokens"] == 20
    assert value["output_tokens"] == 7
    assert value["completed_commands"] == 1
    assert "private" not in json.dumps(value)


def test_usage_records_only_fixed_command_group_counts(tmp_path):
    path = tmp_path / "stream.jsonl"
    commands = [
        "sed -n '1,100p' AGENTS.md",
        "rg -n 'gap' docs/refactor/two-server/section-1/inventory/scenario-records.json",
        "sed -n '1,100p' bot/handlers/trade_create.py",
        "python3 scripts/two_site_proposal_runner.py check --task task.json",
        "python3 scripts/two_site_provider_broker.py submit --task task.json --run-prefix run --timeout 900",
        "pytest -q tests/test_unit.py",
        "python3 scripts/two_site_cycle_packet.py packet --task task.json --worker w --reviewer r --sha " + "a" * 64,
        "git status --short",
        "command with opaque private detail",
    ]
    write_stream(path, [
        *({"type": "item.completed", "item": {"type": "command_execution", "command": value}}
          for value in commands),
        turn(),
    ])
    value = metrics.usage_from_stream(tmp_path, path)
    assert value["completed_commands"] == len(commands)
    assert value["command_groups"] == {
        "startup_orientation": 1,
        "task_discovery": 1,
        "source_inspection": 1,
        "task_preflight": 1,
        "provider": 1,
        "candidate_validation": 1,
        "packet_review": 1,
        "git_verification": 1,
        "other": 1,
    }
    assert "opaque" not in json.dumps(value)


def test_controller_phase_seconds_requires_a_complete_safe_vector():
    phase = {key: 1.25 for key in metrics.CONTROLLER_PHASE_KEYS}
    assert metrics.controller_phase_seconds({"controller_phase_seconds": phase}) == phase
    assert metrics.controller_phase_seconds({"controller_phase_seconds": {**phase, "extra": 1}}) is None
    assert metrics.controller_phase_seconds({"controller_phase_seconds": {**phase, "coordinator": -1}}) is None
    assert metrics.controller_phase_seconds({"controller_phase_seconds": {**phase, "task_context": True}}) is None


def test_record_cycle_persists_complete_controller_phase_timing(tmp_path):
    state = tmp_path / ".local/two-site-refactor/autopilot"
    run = state / "runs" / "timed"
    run.mkdir(parents=True)
    (run / "prompt.txt").write_text("bounded prompt")
    write_stream(run / "stream.jsonl", [turn()])
    phases = {key: 1.25 for key in metrics.CONTROLLER_PHASE_KEYS}
    record = metrics.record_cycle(
        tmp_path, state, run,
        {"state": "CONTINUE", "controller_phase_seconds": phases}, "sol", "xhigh",
    )
    assert record["controller_phase_seconds"] == phases
    assert json.loads((state / "metrics.json").read_text())[-1]["controller_phase_seconds"] == phases


@pytest.mark.parametrize("value", [turn(-1), turn(10, 11), turn(True), {"type": "turn.completed"}])
def test_bad_usage_fails_instead_of_partial_totals(tmp_path, value):
    path = tmp_path / "stream.jsonl"
    write_stream(path, [turn(), value])
    with pytest.raises(ValueError):
        metrics.usage_from_stream(tmp_path, path)


def test_stream_limits_links_and_unknown(tmp_path, monkeypatch):
    path = tmp_path / "stream.jsonl"
    write_stream(path, [{"type": "item.started"}])
    assert metrics.usage_from_stream(tmp_path, path)["input_tokens"] is None
    linked = tmp_path / "linked"
    os.link(path, linked)
    with pytest.raises(ValueError):
        metrics.usage_from_stream(tmp_path, path)
    linked.unlink()
    linked.symlink_to(path)
    with pytest.raises(ValueError):
        metrics.usage_from_stream(tmp_path, linked)
    monkeypatch.setattr(metrics, "MAX_LINE", 4)
    with pytest.raises(ValueError):
        metrics.usage_from_stream(tmp_path, path)


def test_large_command_record_does_not_hide_later_usage(tmp_path, monkeypatch):
    path = tmp_path / "stream.jsonl"
    monkeypatch.setattr(metrics, "MAX_LINE", 256)
    command = {
        "type": "item.completed",
        "item": {
            "type": "command_execution",
            "command": "bounded command",
            "aggregated_output": "x" * 1024,
        },
    }
    write_stream(path, [command, turn(200, 150, 10)])
    value = metrics.usage_from_stream(tmp_path, path)
    assert value["usage_state"] == "OBSERVED_COMPLETED_TURNS"
    assert value["input_tokens"] == 200
    assert value["uncached_input_tokens"] == 50
    assert value["output_tokens"] == 10
    assert value["completed_commands"] == 0
    assert value["skipped_oversized_events"] == 1


def test_large_non_command_record_is_not_silently_accepted(tmp_path, monkeypatch):
    path = tmp_path / "stream.jsonl"
    monkeypatch.setattr(metrics, "MAX_LINE", 128)
    write_stream(path, [{"type": "turn.completed", "padding": "x" * 1024}])
    with pytest.raises(ValueError):
        metrics.usage_from_stream(tmp_path, path)


def test_persisted_continuation_requires_exact_accepted_checkpoint(tmp_path):
    state = tmp_path / ".local/two-site-refactor/autopilot"
    state.mkdir(parents=True)
    accepted = success("a" * 40)
    state_value = {
        "state": accepted["state"],
        "head_changed": accepted["head_changed"],
        "head_after": accepted["head_after"],
        "commit": accepted["commit"],
        "blockers": accepted["blockers"],
        "summary": accepted["summary"],
        "next_action": accepted["next_action"],
        "gemini_worker_run_id": accepted["gemini_worker_run_id"],
        "gemini_reviewer_run_id": accepted["gemini_reviewer_run_id"],
        "proposal_sha256": accepted["proposal_sha256"],
    }
    (state / "status.json").write_text(json.dumps(state_value))
    hint = ap.persisted_continuation(tmp_path, state, "a" * 40)
    assert hint is not None
    assert hint["commit"] == "a" * 40
    assert ap.persisted_continuation(tmp_path, state, "b" * 40) is None
    state_value.pop("proposal_sha256")
    (state / "status.json").write_text(json.dumps(state_value))
    assert ap.persisted_continuation(tmp_path, state, "a" * 40) is None


def test_loop_reuses_only_persisted_accepted_continuation(tmp_path):
    state = tmp_path / ".local/two-site-refactor/autopilot"
    state.mkdir(parents=True)
    accepted = success("a" * 40)
    (state / "status.json").write_text(json.dumps({
        "state": accepted["state"], "head_changed": accepted["head_changed"],
        "head_after": accepted["head_after"], "commit": accepted["commit"],
        "blockers": accepted["blockers"], "summary": accepted["summary"],
        "next_action": accepted["next_action"],
        "gemini_worker_run_id": accepted["gemini_worker_run_id"],
        "gemini_reviewer_run_id": accepted["gemini_reviewer_run_id"],
        "proposal_sha256": accepted["proposal_sha256"],
    }))
    calls = []

    def cycle(root, codex, s_root, cycle, timeout, recovery=None, **kwargs):
        calls.append(kwargs)
        return s_root / "runs" / "fake", success("c" * 40)

    with patch.object(ap, "git", side_effect=lambda *a: "" if "status" in a else "a" * 40), \
         patch.object(ap, "ref_snapshot", return_value={}), \
         patch.object(ap, "run_cycle", side_effect=cycle), \
         patch.object(ap, "prune_runs", return_value=[]):
        assert ap.run_loop(tmp_path, Path("codex"), 1, 10, True) == 2
    assert calls[0]["continuation"]["commit"] == "a" * 40


def test_heartbeat_is_source_free_best_effort_and_silent(tmp_path):
    stopped = threading.Event()
    calls = []

    def publish(_root, state, *, announce=True, **fields):
        calls.append((state, announce, fields))
        stopped.set()

    with patch.object(ap, "write_supervisor_state", side_effect=publish):
        ap._heartbeat_running_cycle(
            stopped, tmp_path, 7, "a" * 40, 123.0, interval=0.001
        )

    assert len(calls) == 1
    state, announce, fields = calls[0]
    assert state == "RUNNING"
    assert announce is False
    assert fields["cycle"] == 7
    assert fields["head"] == "a" * 40
    assert fields["phase"] == "COORDINATOR"
    assert fields["phase_started_at"] == 123.0
    assert fields["blockers"] == []
    assert isinstance(fields["heartbeat_at"], float)


def test_persistent_metrics_are_bounded_idempotent_and_keep_failed_runs(tmp_path, monkeypatch):
    state = tmp_path / ".local/two-site-refactor/autopilot"
    monkeypatch.setattr(metrics, "MAX_RECORDS", 2)
    for number in range(3):
        run = state / "runs" / str(number)
        run.mkdir(parents=True)
        (run / "prompt.txt").write_text("secret-free prompt not retained")
        write_stream(run / "stream.jsonl", [turn()])
        value = {"state": "FAILED" if number == 2 else "CONTINUE"}
        metrics.record_cycle(tmp_path, state, run, value, "astra", "xhigh")
    metrics.record_cycle(tmp_path, state, run, value, "astra", "xhigh")
    records = json.loads((state / "metrics.json").read_text())
    assert [x["run_id"] for x in records] == ["1", "2"]
    assert records[-1]["state"] == "FAILED"
    assert "secret-free" not in json.dumps(records)
    assert (state / "metrics.json").stat().st_mode & 0o777 == 0o600


def test_incomplete_stream_does_not_publish_partial_usage(tmp_path):
    state = tmp_path / ".local/two-site-refactor/autopilot"
    run = state / "runs" / "one"
    run.mkdir(parents=True)
    (run / "prompt.txt").write_text("bounded prompt")
    (run / "stream.jsonl").write_text(json.dumps(turn()) + "\n{")
    record = metrics.record_cycle(tmp_path, state, run, {"state": "FAILED"}, "astra", "xhigh")
    assert record["usage_state"] == "UNKNOWN"
    assert record["input_tokens"] is None


def test_total_stream_limit_fifo_and_unsafe_ledger(tmp_path, monkeypatch):
    path = tmp_path / "stream.jsonl"
    write_stream(path, [turn()])
    monkeypatch.setattr(metrics, "MAX_STREAM", 10)
    with pytest.raises(ValueError):
        metrics.usage_from_stream(tmp_path, path)
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)
    with pytest.raises(ValueError):
        metrics.usage_from_stream(tmp_path, fifo)
    external = tmp_path / "external.json"
    external.write_text("[]")
    alias = tmp_path / "metrics.json"
    os.link(external, alias)
    with pytest.raises(ValueError):
        metrics.atomic_json(tmp_path, alias, [{"run_id": "bad"}], 2000)
    assert external.read_text() == "[]"


@pytest.mark.parametrize("capture_fails", [False, True])
def test_metrics_failure_never_changes_cycle_success_or_original_failure(tmp_path, capture_fails):
    state = tmp_path / ".local/two-site-refactor/autopilot"
    (state / "runs").mkdir(parents=True)
    codex = tmp_path / "codex"
    codex.write_text("offline fixture")
    process = MagicMock()
    process.returncode = 0
    process.pid = 123
    process.__enter__.return_value = process
    def capture(_process, _input, output, *args, **kwargs):
        if capture_fails:
            raise ap.AutopilotError("original provider failure")
        (output.parent / "result.json").write_text(json.dumps(success()))
    with patch.object(
        ap, "validate_repository",
        return_value={"head": "a" * 40, "protected_refs": {}, "assignment_scope": synthetic_scope()},
    ), \
         patch.object(ap, "prompt_for_cycle", return_value="bounded prompt"), \
         patch.object(ap, "codex_identity", return_value={"sha256": "pin", "runtime": {}}), \
         patch.object(ap, "codex_runtime_identity", return_value={}), \
         patch.object(ap, "file_sha256", return_value="pin"), \
         patch.object(ap.subprocess, "Popen", return_value=process), \
         patch.object(ap, "capture_bounded_process", side_effect=capture), \
         patch.object(ap, "validate_result", side_effect=lambda value: value), \
         patch.object(ap, "validate_transition", return_value=success()), \
         patch.object(metrics, "record_cycle", side_effect=RuntimeError("telemetry offline")) as recorder:
        if capture_fails:
            with pytest.raises(ap.AutopilotError, match="original provider failure"):
                ap.run_cycle(tmp_path, codex, state, 1, 10)
        else:
            _, result = ap.run_cycle(tmp_path, codex, state, 1, 10)
            assert result["state"] == "CONTINUE"
    recorder.assert_called_once()
    saved = list((state / "runs").iterdir())[0] / "status.json"
    status = json.loads(saved.read_text())
    assert status["state"] == ("FAILED" if capture_fails else "CONTINUE")
    assert status["metrics_error"] == "RuntimeError"
    assert status["phase"] == "COORDINATOR"
    assert isinstance(status["phase_started_at"], float)
    assert isinstance(status["heartbeat_at"], float)


CANONICAL_IDENTITY = {
    "stage_id": "P1-00b",
    "scenario_key": "ae00_identity_recorder_tests",
    "gap_reference": "docs/refactor/two-server/AUTOMATION_EFFICIENCY_PLAN.md#gap-b",
    "source_sha256": "0123456789abcdef" * 4,
    "fingerprint": "fedcba9876543210" * 4,
}


def test_semantic_identity_accepts_canonical_identity():
    projected = metrics.semantic_identity(CANONICAL_IDENTITY)
    assert projected == CANONICAL_IDENTITY
    assert projected is not CANONICAL_IDENTITY
    assert set(projected.keys()) == {
        "stage_id", "scenario_key", "gap_reference", "source_sha256", "fingerprint",
    }


@pytest.mark.parametrize("patch_data", [
    {"extra_key": "unexpected"},
    {"source_sha256": "0123456789ABCDEF" * 4},
    {"fingerprint": "invalid_hex"},
    {"gap_reference": "docs//AUTOMATION_EFFICIENCY_PLAN.md#gap-b"},
    {"gap_reference": "docs/../AUTOMATION_EFFICIENCY_PLAN.md#gap-b"},
    {"gap_reference": "docs/plan" + chr(127) + ".md#gap-b"},
    {"gap_reference": "docs/plan" + chr(31) + ".md#gap-b"},
    {"gap_reference": "/docs/AUTOMATION_EFFICIENCY_PLAN.md#gap-b"},
    {"gap_reference": "C:/docs/AUTOMATION_EFFICIENCY_PLAN.md#gap-b"},
    {"gap_reference": "C:" + chr(92) + "docs/AUTOMATION_EFFICIENCY_PLAN.md#gap-b"},
    {"gap_reference": "docs/AUTOMATION_EFFICIENCY_PLAN.md"},
    {"gap_reference": "docs/AUTOMATION_EFFICIENCY_PLAN.md#"},
    {"gap_reference": "docs/" + ("x" * 500) + "#gap-b"},
])
def test_semantic_identity_validation_and_rejections(patch_data):
    assert metrics.semantic_identity({**CANONICAL_IDENTITY, **patch_data}) is None


def test_record_cycle_projects_valid_identity_and_drops_invalid(tmp_path, monkeypatch):
    state = tmp_path / ".local/two-site-refactor/autopilot"
    run = state / "runs" / "r1"
    run.mkdir(parents=True)
    (run / "prompt.txt").write_text("bounded prompt")
    write_stream(run / "stream.jsonl", [turn()])

    provider_identity = dict(CANONICAL_IDENTITY)
    monkeypatch.setattr(
        metrics, "_call_provider_metrics",
        lambda *args, **kwargs: {"state": "OK", "semantic_identity": provider_identity},
    )

    record = metrics.record_cycle(
        tmp_path, state, run, {"state": "CONTINUE"}, "astra", "xhigh",
    )
    assert record["semantic_identity"] == CANONICAL_IDENTITY
    assert record["semantic_identity"] is not provider_identity

    run_invalid = state / "runs" / "r2"
    run_invalid.mkdir(parents=True)
    (run_invalid / "prompt.txt").write_text("bounded prompt")
    write_stream(run_invalid / "stream.jsonl", [turn()])

    monkeypatch.setattr(
        metrics, "_call_provider_metrics",
        lambda *args, **kwargs: {
            "state": "OK",
            "semantic_identity": {**CANONICAL_IDENTITY, "extra_field": "unexpected"},
        },
    )

    record_invalid = metrics.record_cycle(
        tmp_path, state, run_invalid, {"state": "CONTINUE"}, "astra", "xhigh",
    )
    assert "semantic_identity" not in record_invalid


def test_record_cycle_fallback_copies_validated_semantic_identity(tmp_path, monkeypatch):
    state = tmp_path / ".local/two-site-refactor/autopilot"
    run = state / "runs" / "fallback-run"
    run.mkdir(parents=True)
    (run / "prompt.txt").write_text("bounded prompt")
    write_stream(run / "stream.jsonl", [turn()])

    proposal_dir = tmp_path / ".local/two-site-refactor/proposals/w1"
    proposal_dir.mkdir(parents=True)
    task_snapshot = {
        "source_head": "b" * 40,
        "task": {
            "task_id": "fallback-task-1",
            "goal": "bounded task goal",
        },
    }
    (proposal_dir / "task.json").write_text(json.dumps(task_snapshot))

    fallback_identity = dict(CANONICAL_IDENTITY)

    def fake_provider(root, task_id=None, *args, **kwargs):
        if task_id is None:
            return {"state": "UNAVAILABLE"}
        return {"state": "OK", "semantic_identity": fallback_identity}

    monkeypatch.setattr(metrics, "_call_provider_metrics", fake_provider)

    meta = {
        "state": "CONTINUE",
        "head_before": "b" * 40,
        "gemini_worker_run_id": "w1",
    }
    record = metrics.record_cycle(tmp_path, state, run, meta, "astra", "xhigh")
    assert record["semantic_identity"] == CANONICAL_IDENTITY
    assert record["semantic_identity"] is not fallback_identity
    assert record["task_id"] == "fallback-task-1"
