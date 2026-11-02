"""Offline framing regressions; fixed local children are NOT Provider evidence."""
from __future__ import annotations

from copy import deepcopy
import json
import os
import subprocess
import sys

import jsonschema
import pytest

from scripts import two_site_proposal_runner as runner
from scripts.two_site_generation_completion import normal_observations
from tests import test_two_site_generation_completion_contract as contracts

CID = "00000000-0000-4000-8000-000000007007"


def wire_events(events):
    return "\n".join(json.dumps(e) for e in events) + "\n"


def final_result():
    return {"status": "PROPOSAL_READY", "summary": "Complete synthetic change.",
            "covered_requirements": [1], "findings": [],
            "changes": [{"path": "tests/test_small.py", "original_sha256": None,
                         "content": "assert True\n", "replacements": []}]}


def framed_events(manifest, result=None, model=None):
    events = [{"event": "init", "conversation_id": CID,
               "init": {"model": model or runner.WORKER_MODEL}}]
    for frame in manifest["frames"]:
        value = frame["ack"] if frame["ack"] is not None else (result or final_result())
        events.append({"event": "result", "result": {"status": "SUCCESS",
            "conversation_id": CID, "num_turns": frame["index"], "structured_output": deepcopy(value)}})
    return events


def small_task():
    return ({"task_id": "FRAMED-OFFLINE", "goal": "Synthetic complete change.",
             "allowed_files": ["tests/test_small.py"], "context_files": [],
             "requirements": ["Keep quality."], "test_selectors": ["tests/test_small.py"]},
            {"tests/test_small.py": {"sha256": None, "content": None}})


@pytest.mark.parametrize("prompt", ["x" * 180000, "é🙂" * 53000, "\u2066" * 100000])
def test_ordered_utf8_frames_reconstruct_every_byte_and_bound_wire(prompt):
    manifest, wires = runner._framed_input_plan(prompt)
    pieces = []
    for frame, wire in zip(manifest["frames"], wires):
        text = json.loads(wire)["message"]["content"]
        assert len(text.encode()) <= 120000
        piece = text.split("\nBEGIN_EXACT_LOGICAL_FRAGMENT\n", 1)[1].rsplit(
            "\nEND_EXACT_LOGICAL_FRAGMENT\nEND_FULL_INPUT_TOKEN=", 1)[0].encode()
        assert frame["offset"] == sum(map(len, pieces))
        assert len(piece) == frame["utf8_bytes"] and runner.sha(piece) == frame["sha256"]
        assert runner.sha(wire) == frame["wire_sha256"] and len(wire) == frame["wire_bytes"]
        if frame["ack"]:
            token = frame["ack"]["summary"].rsplit(":", 1)[1]
            assert token not in text.split("\nBEGIN_EXACT_LOGICAL_FRAGMENT\n", 1)[0]
        pieces.append(piece)
    assert b"".join(pieces) == prompt.encode()
    assert manifest["logical_prompt_sha256"] == runner.sha(prompt.encode())
    assert manifest["frame_count"] == len(wires) == len(pieces) in {2, 3}
    assert sum(map(len, wires)) <= 6 * (2 * runner.MAX_CONTEXT) + 46
    assert runner._framed_input_plan(prompt) == (manifest, wires)


@pytest.mark.parametrize("prompt", ["x" * 160000, "x" * 320001, "\0" * 320000])
def test_logical_and_existing_combined_wire_bounds_stay_closed(prompt):
    with pytest.raises(runner.ProposalError):
        runner._framed_input_plan(prompt)


@pytest.mark.parametrize("role,correction", [("worker", False), ("worker", True), ("verifier", False)])
def test_ack_uses_unchanged_actual_schema_for_all_three_modes(role, correction):
    task, sources = small_task()
    original = deepcopy(sources)
    forced = ()
    if correction:
        sources["tests/test_small.py"] = {"content": "assert False\n", "sha256": runner.sha(b"assert False\n")}
        forced = runner.originally_new_paths(task, original)
    schema = runner.schema(role, task, sources, forced, original_sources=original)
    before = deepcopy(schema)
    manifest, _ = runner._framed_input_plan("x" * 180000)
    for frame in manifest["frames"][:-1]:
        jsonschema.validate(frame["ack"], schema)
        assert set(frame["ack"]) == set(schema["required"])
    assert schema == before and len(schema["required"]) == 5


@pytest.mark.parametrize("fault", ["wrong-ack", "malformed-structured", "extra-init", "missing-final",
    "late-event", "bad-turn", "bool-turn", "foreign-step", "malformed-step", "finish", "error", "denial"])
def test_strict_framed_parser_rejects_preload_and_final_corruption(fault):
    manifest, _ = runner._framed_input_plan("x" * 180000)
    events = framed_events(manifest)
    if fault == "wrong-ack":
        events[1]["result"]["structured_output"]["summary"] += "bad"
    elif fault == "malformed-structured":
        events[1]["result"]["response"] = json.dumps(events[1]["result"]["structured_output"])
        events[1]["result"]["structured_output"] = None
    elif fault == "extra-init":
        events.insert(1, deepcopy(events[0]))
    elif fault == "missing-final":
        events.pop()
    elif fault == "late-event":
        events.append({"event": "agent_response", "text": "late"})
    elif fault in {"bad-turn", "bool-turn"}:
        events[1]["result"]["num_turns"] = 2 if fault == "bad-turn" else True
    elif fault == "foreign-step":
        events.insert(1, {"event": "step_update", "step_update": {"conversation_id": "other"}})
    elif fault == "malformed-step":
        events.insert(1, {"event": "step_update", "step_update": "bad"})
    elif fault == "finish":
        events.insert(1, {"event": "step_update", "step_update": {"step_type": "tool", "tool_name": "finish"}})
    elif fault == "error":
        events.insert(1, {"event": "error", "error": "synthetic"})
    else:
        events.insert(1, {"event": "agent_response", "denied_actions": ["synthetic"]})
    task, sources = small_task()
    with pytest.raises((runner.ProposalError, ValueError)):
        runner._parse_framed_stream(wire_events(events), manifest, task, sources,
                                    "worker", (), runner.WORKER_MODEL, CID, original_sources=sources)


def test_final_role_validation_uses_real_final_turn_and_never_accepts_ack():
    manifest, _ = runner._framed_input_plan("x" * 180000)
    task, sources = small_task()
    events = framed_events(manifest)
    actual, envelope = runner._parse_framed_stream(wire_events(events), manifest, task,
        sources, "worker", (), runner.WORKER_MODEL, CID, original_sources=sources)
    assert actual["changes"][0]["content"] == "assert True\n"
    assert envelope["num_turns"] == 2
    # Same exact legacy semantic gate still rejects a nonexistent allowed path.
    events[-1]["result"]["structured_output"]["changes"][0]["path"] = "tests/test_outside.py"
    with pytest.raises(runner.ProposalError):
        runner._parse_framed_stream(wire_events(events), manifest, task, sources,
            "worker", (), runner.WORKER_MODEL, CID, original_sources=sources)
    events[-1]["result"]["structured_output"] = manifest["frames"][0]["ack"]
    with pytest.raises(runner.ProposalError, match="ACK cannot"):
        runner._parse_framed_stream(wire_events(events), manifest, task, sources,
            "worker", (), runner.WORKER_MODEL, CID, original_sources=sources)


@pytest.fixture
def proof_case():
    case = contracts.GenerationCompletionContracts("runTest")
    case.setUp()
    try:
        yield case
    finally:
        case.doCleanups()


def install_framed_fixture(case, directory):
    """Synthetic retained bytes only; no Provider or OS lifecycle is claimed."""
    status, proof = case.status(directory), case.proof(directory)
    events = [json.loads(x) for x in (directory / "stream.jsonl").read_text().splitlines()]
    prompt = "PUBLIC synthetic full context.\n" * 7000
    manifest, wires = runner._framed_input_plan(prompt)
    framed = [events[0]]
    for frame in manifest["frames"][:-1]:
        framed.append({"event": "result", "result": {"status": "SUCCESS", "num_turns": frame["index"],
            "conversation_id": status["conversation_id"], "structured_output": frame["ack"]}})
    events[-1]["result"]["num_turns"] = len(wires)
    framed.append(events[-1])
    raw = wire_events(framed).encode()
    (directory / "prompt.txt").write_text(prompt)
    (directory / "stream.jsonl").write_bytes(raw)
    runner.write_json(directory / runner._FRAMED_MANIFEST, manifest)
    status.update(input_transport=runner._FRAMED_INPUT,
                  input_framing_sha256=runner.file_sha256(directory / runner._FRAMED_MANIFEST))
    proof.update(prompt_sha256=runner.sha(prompt.encode()), input_framing=manifest,
        observations=normal_observations(0, b"".join(wires), raw, b"", 25.0))
    case.save_proof(directory, proof, status)
    return manifest, wires


def test_full_framed_proof_reaches_all_existing_consumers(proof_case):
    case = proof_case
    for directory in (case.worker, case.reviewer):
        install_framed_fixture(case, directory)
    for kind in ("runner", "pipeline", "supervisor"):
        assert case.gate(kind) == case.result


@pytest.mark.parametrize("fault", ["manifest", "marker", "partial-marker", "all-markers", "wire", "raw", "recovery", "compact"])
def test_rehashed_framed_proof_tampering_never_downgrades(proof_case, fault):
    case, directory = proof_case, proof_case.worker
    install_framed_fixture(case, directory)
    status, proof = case.status(directory), case.proof(directory)
    path = directory / runner._FRAMED_MANIFEST
    if fault == "manifest":
        value = json.loads(path.read_bytes()); value["frames"][0]["wire_sha256"] = "0" * 64
        runner.write_json(path, value)
        status["input_framing_sha256"] = runner.file_sha256(path); proof["input_framing"] = value
    elif fault in {"marker", "partial-marker", "all-markers"}:
        del status["input_transport"]
        if fault != "marker":
            path.unlink()
        if fault == "all-markers":
            del status["input_framing_sha256"]; proof.pop("input_framing")
    elif fault == "wire":
        proof["observations"]["input_sha256"] = "0" * 64
    elif fault == "raw":
        raw = (directory / "stream.jsonl").read_bytes() + b'{"event":"agent_response"}\n'
        (directory / "stream.jsonl").write_bytes(raw)
        proof["observations"].update(stdout_bytes=len(raw), stdout_sha256=runner.sha(raw))
    elif fault == "compact":
        status["compact_review"] = proof["compact_review"] = True
    else:
        case.recovered(directory)
        status, proof = case.status(directory), case.proof(directory)
    case.save_proof(directory, proof, status)
    with pytest.raises((runner.ProposalError, OSError)):
        case.gate("runner")


@pytest.mark.parametrize("fault", [None, "bad-ack", "early-exit", "nonzero", "timeout", "overflow", "late-stop"])
def test_real_bounded_local_exchange_requires_ack_complete_writes_and_normal_exit(tmp_path, monkeypatch, fault):
    manifest, wires = runner._framed_input_plan("x" * 180000)
    events = framed_events(manifest)
    if fault == "bad-ack":
        events[1]["result"]["structured_output"]["summary"] += "bad"
    # Fixed local Python, empty HOME, no inherited auth, no model/candidate code.
    body = "import sys,json,hashlib,time\n"
    body += f"events={events!r}\nexpected={[runner.sha(w) for w in wires]!r}\n"
    body += "print(json.dumps(events[0]),flush=True)\n"
    body += "for i,wanted in enumerate(expected):\n"
    body += " line=sys.stdin.buffer.readline()\n assert hashlib.sha256(line).hexdigest()==wanted\n"
    body += " print(json.dumps(events[i+1]),flush=True)\n"
    if fault == "early-exit":
        body += " if i==0: sys.exit(0)\n"
    if fault == "timeout":
        body += "time.sleep(30)\n"
    elif fault == "nonzero":
        body += "sys.exit(7)\n"
    if fault == "overflow":
        monkeypatch.setattr(runner, "MAX_OUTPUT", 64)
    process = subprocess.Popen([sys.executable, "-I", "-B", "-u", "-c", body],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True,
        cwd=tmp_path, env={"PATH": os.defpath, "HOME": str(tmp_path), "LANG": "C"})
    write = os.write
    monkeypatch.setattr(runner.os, "write", lambda fd, data: write(fd, data[:257]))
    try:
        if fault is None:
            runner._capture_framed_process(process, wires, manifest, tmp_path / "out", tmp_path / "err",
                3, runner.WORKER_MODEL, CID)
            assert process.returncode == 0
            assert (tmp_path / "out").read_text() == wire_events(events)
        else:
            def stopped():
                raise RuntimeError("owned late STOP")
            with pytest.raises((runner.ProposalError, BrokenPipeError, subprocess.TimeoutExpired, RuntimeError)):
                runner._capture_framed_process(process, wires, manifest, tmp_path / "out", tmp_path / "err",
                    .5 if fault == "timeout" else 3, runner.WORKER_MODEL, CID,
                    pre_provider_check=stopped if fault == "late-stop" else None)
        assert process.poll() is not None
        assert all(s.closed for s in (process.stdin, process.stdout, process.stderr))
        assert sum(p.stat().st_size for p in (tmp_path / "out", tmp_path / "err")) <= runner.MAX_OUTPUT
    finally:
        if process.poll() is None:
            os.killpg(process.pid, 9)
        process.wait(timeout=3)
        for stream in (process.stdin, process.stdout, process.stderr):
            if not stream.closed:
                stream.close()
