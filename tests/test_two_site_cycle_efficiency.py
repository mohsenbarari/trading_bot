"""Offline regression tests for bounded continuation and usage accounting."""
import json
import os
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
