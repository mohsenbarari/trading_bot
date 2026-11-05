"""Offline regression for valid file bytes rejected by a contradictory wire cap."""
import json
import time

import pytest

from scripts import two_site_generation_completion as completion
from scripts import two_site_proposal_runner as runner
from tests import test_two_site_terminal_output_rejection as children


PATH = "tests/test_wire_budget.py"
CID = "wire-budget-session"


def task(cap):
    profile = {"version": "1.0", "file_limits": {PATH: cap}, "total_limit": cap}
    return {"task_id": "WIRE-001", "goal": "Exercise exact transport limits.",
            "context_files": [], "allowed_files": [PATH], "test_selectors": [PATH],
            "requirements": ["Preserve the complete test.", "PROPOSAL_UTF8_BUDGET_V1:" +
                             json.dumps(profile, sort_keys=True, separators=(",", ":"))]}


def wire(content, summary, *, model=None):
    value = {"status": "PROPOSAL_READY", "summary": summary, "covered_requirements": [1, 2],
             "findings": [], "changes": [{"path": PATH, "original_sha256": None,
                                            "content": content, "replacements": []}]}
    events = [{"event": "init", "conversation_id": CID,
               "init": {"model": runner.WORKER_MODEL if model is None else model}},
              {"event": "result", "result": {"status": "SUCCESS", "conversation_id": CID,
                                               "num_turns": 1, "response": json.dumps(value)}}]
    return b"".join(json.dumps(v).encode() + b"\n" for v in events)


def check(raw, cap):
    return runner._generation_final_frame_check(
        raw, b"", task(cap), {PATH: {"sha256": None, "content": None}}, "worker", (),
        runner.WORKER_MODEL, CID,
    )


def capture(process, tmp_path, cap):
    policy = completion.GenerationRecoveryPolicy(lambda out, err: not err and check(out, cap))
    outcome = runner.capture_bounded_process(
        process, children.INPUT, tmp_path / "stdout.bin", tmp_path / "stderr.bin", 10,
        max_output=2_000_000, generation_completion=policy,
    )
    return outcome


def test_profiled_file_can_use_json_overhead_and_owned_shutdown(tmp_path, monkeypatch):
    content = "def test_value():\n    assert True\n#" + "x" * 59000 + "\n"
    raw = wire(content, "s" * 6000)
    body = json.loads(raw.splitlines()[-1])["result"]["response"]
    assert len(content.encode()) < 64000 < len(body.encode()) < runner.MAX_CONTEXT
    assert check(raw, 64000) is True
    monkeypatch.setattr(completion, "POST_FINAL_GRACE_SECONDS", .02)
    with children._child(tmp_path, raw) as process:
        outcome = capture(process, tmp_path, 64000)
    assert outcome["outcome"] == completion.OUTCOME_RECOVERED
    assert outcome["owned_group_absent"] is True
    assert outcome["stdout_eof"] is True and outcome["stderr_eof"] is True


def test_real_wire_overflow_fails_promptly_instead_of_becoming_timeout(tmp_path):
    raw = wire("def test_value():\n    assert True\n", "s" * 70000)
    began = time.monotonic()
    with children._child(tmp_path, raw) as process:
        with pytest.raises(runner.ProposalError, match="test-only raw transport exceeds budget"):
            capture(process, tmp_path, 24000)
    assert time.monotonic() - began < 3


def test_file_cap_stays_authoritative_when_wire_has_headroom():
    with pytest.raises(runner.ProposalError, match="new test file content exceeds budget"):
        check(wire("#" + "x" * 64000, "bounded"), 64000)


def test_legacy_floor_and_whole_context_ceiling_are_preserved():
    sources = {PATH: {"sha256": None, "content": None}}
    small = runner.task_preparation_notes(task(24000), sources)["output_contract"]
    large = runner.task_preparation_notes(task(160000), sources)["output_contract"]
    assert small["max_test_only_transport_bytes"] == 64000
    assert large["max_test_only_transport_bytes"] == runner.MAX_CONTEXT
    assert large["max_new_test_file_utf8_bytes"] == 64000


def test_unproven_matching_error_text_does_not_enable_fast_rejection(monkeypatch):
    def unproven(*args, **kwargs):
        raise runner.ProposalError("test-only raw transport exceeds budget")
    monkeypatch.setattr(runner, "_validate_final_generation_frame", unproven)
    assert check(wire("assert True\n", "bounded"), 24000) is False


def test_wrong_session_model_or_partial_frame_never_becomes_eligible():
    raw = wire("assert True\n", "s" * 70000)
    assert check(raw[:-1], 24000) is False
    assert check(wire("assert True\n", "s" * 70000, model="wrong-model"), 24000) is False
    assert check(raw.replace(CID.encode(), b"different-session"), 24000) is False
