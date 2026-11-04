"""Synthetic formatting transport; production proof validators are explicit boundaries."""
import copy
import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.two_site_assignment_scope import AssignmentScope

from scripts import two_site_qoder_driver as qoder
from scripts import two_site_qoder_output_repair as helper


def framed(text, session="original-session", **changes):
    init = {"type": "system", "subtype": "init", "session_id": session,
            "model": "fixture-configured-model", "tools": [], "mcp_servers": [], "plugins": []}
    result = {"type": "result", "subtype": "success", "is_error": False,
              "session_id": session, "stop_reason": "end_turn", "num_turns": 1, "result": text}
    result.update(changes)
    return (json.dumps(init) + "\n" + json.dumps(result) + "\n").encode()


def write(path, value, *, binary=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value if binary else json.dumps(value).encode())
    path.chmod(0o600)


@pytest.fixture
def fx(tmp_path, monkeypatch):
    root = tmp_path / "root"; root.mkdir()
    original = root / ".local/two-site-refactor/autopilot/runs/cycle-0001-20261103T000000Z-1"
    original.mkdir(parents=True, mode=0o700)
    cli = root / "fake-cli"; cli.write_bytes(b"SYNTHETIC_PUBLIC_CLI"); cli.chmod(0o500)
    pin = {"path": str(cli), "version": "fixture-version", "sha256": hashlib.sha256(cli.read_bytes()).hexdigest()}
    runtime = {"schema_version": 1, "coordinator": pin}
    write(root / helper.config.AI_RUNTIME_REL, runtime)
    role = {"provider": "qoder", "model": "fixture-configured-model", "reasoning_effort": "high"}
    ai = {"schema_version": 1, "roles": {"coordinator": role, "final_reviewer": role}}
    public_template = (Path(__file__).resolve().parents[1] / helper.config.AI_CONFIG_REL)
    public_policy = json.loads(public_template.read_text())
    for name in ("coordinator", "final_reviewer"):
        public_policy["roles"][name] = copy.deepcopy(role)
    write(root / helper.config.AI_CONFIG_REL, public_policy)
    monkeypatch.setattr(helper.config, "load_ai_config", lambda _root: copy.deepcopy(ai))
    identity = {"provider": "qoder", **pin, "model": role["model"], "reasoning": "high",
                "logged_in": True, "auto_update_enabled": False}
    scope = AssignmentScope("FIXTURE-001", "P1-00b", ["tests/**"], [".env*"], "2099-01-01T00:00:00Z", "a" * 64)
    before = {"head": "1" * 40, "status": "", "protected_refs": {"main": "3" * 40},
              "assignment_scope": scope, "control_blobs": {"control": "4" * 40}}
    current = {**before, "head": "2" * 40}
    monkeypatch.setattr(helper.autopilot, "validate_repository", lambda *_a, **_kw: copy.deepcopy(current))
    monkeypatch.setattr(helper.autopilot, "_load_accepted_checkpoint", lambda *_a: None)
    write(original / "status.json", {"coordinator": identity, "model_config": ai,
          "head_before": before["head"], "assignment_scope": helper._scope(scope)})
    write(original / "stream.jsonl", framed("Completed the business commit; here is a prose summary."), binary=True)
    write(original / "stderr.txt", b"", binary=True)
    write(original / "prompt.txt", b"Synthetic original prompt.", binary=True)
    schema_source = Path(helper.autopilot.__file__).resolve().parents[1] / helper.autopilot.SCHEMA_REL
    write(root / helper.autopilot.SCHEMA_REL, schema_source.read_bytes(), binary=True)
    facts = {"commit": current["head"], "gemini_worker_run_id": "fixture-worker-0",
             "gemini_reviewer_run_id": "fixture-verifier-0", "proposal_sha256": "b" * 64}
    result = {"schema_version": "1.0", "state": "CONTINUE", "disposition": "NONE", "category": "NONE",
              "summary": "Existing business commit and approvals are complete.", "next_action": "Continue the next real unit.",
              "blockers": [], **facts}
    pipeline = root / ".local/two-site-refactor/pipeline/fixture-prefix"
    native = {"synthetic_native_boundary": "PASSED"}
    native_path = pipeline / "pre-review-tests-0.json"; write(native_path, native)
    receipt_path = root / helper.review.REVIEW_REL / (facts["proposal_sha256"] + ".json")
    write(receipt_path, {"synthetic_review_boundary": "APPROVE"})
    history = {"schema_version": "1.0", "correction_count": 0,
               "worker_run_id": facts["gemini_worker_run_id"],
               "gemini_reviewer_run_id": facts["gemini_reviewer_run_id"],
               "proposal_sha256": facts["proposal_sha256"],
               "pre_review_test_sha256": helper.digest(native_path.read_bytes()),
               "qoder_review_receipt": receipt_path.relative_to(root).as_posix(),
               "qoder_review_receipt_sha256": helper.digest(receipt_path.read_bytes()), "decision": "APPROVE"}
    history_path = pipeline / "qoder-review-0.json"; write(history_path, history)
    monkeypatch.setattr(helper.broker, "read_cycle_reservation", lambda _r, cid:
                        {"reservation": {"run_prefix": "fixture-prefix", "source_head": before["head"],
                         "assignment_id": scope.assignment_id, "assignment_sha256": scope.sha256}})
    proof_calls = []
    monkeypatch.setattr(helper.autopilot, "validate_scoped_gemini_chain",
                        lambda *_a: proof_calls.append("gemini") or {"changes": []})
    monkeypatch.setattr(helper.autopilot, "validate_scoped_committed_proposal",
                        lambda *_a: proof_calls.append("committed"))
    monkeypatch.setattr(helper.review, "validate_result_review", lambda *_a:
                        proof_calls.append("qoder") or {"bindings": {"native_evidence_sha256":
                        helper.digest(helper.runner.serialize_json(native))}})
    monkeypatch.setattr(helper.autopilot, "validate_transition", lambda _r, _b, _v:
                        proof_calls.append("transition") or {**_v, "head_after": current["head"]})
    monkeypatch.setattr(qoder, "qoder_identity", lambda *_a: dict(identity))
    commands = []
    monkeypatch.setattr(helper, "buildwrap_coordinator_command",
                        lambda _r, _d, argv, **_kw: commands.append(argv) or argv)
    processes = []

    class Process:
        def __init__(self, *_a, **_kw):
            self.returncode = 0
            self.stdin, self.stdout, self.stderr = io.BytesIO(), io.BytesIO(), io.BytesIO()
            processes.append(self)
        def poll(self): return self.returncode

    monkeypatch.setattr(helper.subprocess, "Popen", Process)
    options = {"after_capture": None, "session": "repair-session", "tool": False, "error": False}

    def capture(process, _stdin, stream, stderr, *_args, **_kwargs):
        raw = framed(json.dumps(result), options["session"], is_error=options["error"])
        if options["tool"]:
            events = [json.loads(line) for line in raw.splitlines()]
            events.insert(1, {"type": "assistant", "session_id": options["session"],
                             "message": {"content": [{"type": "tool_use", "name": "Read", "id": "t1"}]}})
            raw = ("\n".join(json.dumps(event) for event in events) + "\n").encode()
        write(stream, raw, binary=True); write(stderr, b"", binary=True)
        if options["after_capture"]: options["after_capture"]()
    monkeypatch.setattr(helper.runner, "capture_bounded_process", capture)
    return SimpleNamespace(**locals())


def repair(fx, **kwargs):
    return helper.repair_coordinator_output(fx.root, fx.original, fx.before, original_cli=fx.identity,
        model_config=fx.ai, observed_returncode=kwargs.pop("returncode", 0), environment={}, **kwargs)


def test_one_prose_repair_is_tool_free_source_bound_and_preserves_original(fx):
    original_raw = (fx.original / "stream.jsonl").read_bytes()
    repaired = repair(fx)
    assert repaired["result"] == fx.result
    assert (fx.original / "stream.jsonl").read_bytes() == original_raw
    assert json.loads((fx.original / "result.json").read_text()) == fx.result
    receipt = json.loads((fx.root / repaired["receipt_path"]).read_text())
    assert receipt["original_session_id"] == "original-session" and receipt["repair_session_id"] == "repair-session"
    assert receipt["repair_hashes"]["stream.jsonl"]
    tools = fx.commands[0].index("--tools")
    assert fx.commands[0][tools + 1] == ""
    assert "--append-system-prompt" in fx.commands[0]
    assert fx.proof_calls == ["gemini", "committed", "qoder", "transition", "qoder"]
    with pytest.raises(helper.OutputRepairError, match="ALREADY_ATTEMPTED"):
        repair(fx)
    assert len(fx.processes) == 1


@pytest.mark.parametrize("body", ["[]", '{"state":"CONTINUE"}',
    '{"schema_version":"1.0","state":[],"extra":"missing required fields"}'])
def test_nonobject_or_schema_shape_can_be_formatted(fx, body):
    write(fx.original / "stream.jsonl", framed(body), binary=True)
    assert repair(fx)["result"] == fx.result


@pytest.mark.parametrize("bad", ["duplicate", "nonfinite", "sdk-error", "session-drift", "model-drift", "nonzero-exit", "valid-wrong-id", "semantic", "oversize"])
def test_ineligible_original_never_launches_formatter(fx, bad):
    returncode = 0
    if bad == "duplicate": body = '{"x":1,"x":2}'
    elif bad == "nonfinite": body = '{"x":NaN}'
    elif bad == "oversize": body = "x" * (qoder.MAX_RESULT_BYTES + 1)
    else: body = "plain successful prose"
    raw = framed(body)
    if bad == "sdk-error": raw = framed(body, is_error=True)
    elif bad == "session-drift": raw = raw.replace(b'"session_id": "original-session", "stop_reason"', b'"session_id": "other", "stop_reason"')
    elif bad == "model-drift": raw = raw.replace(b'fixture-configured-model', b'other-model')
    elif bad == "nonzero-exit": returncode = 1
    elif bad in {"valid-wrong-id", "semantic"}:
        value = dict(fx.result)
        if bad == "valid-wrong-id": value["gemini_worker_run_id"] = "valid-shape-but-wrong-worker"
        else: value["blockers"] = ["semantic blocker cannot be erased"]
        raw = framed(json.dumps(value))
    write(fx.original / "stream.jsonl", raw, binary=True)
    with pytest.raises((helper.OutputRepairError, qoder.QoderError)):
        repair(fx, returncode=returncode)
    assert not fx.processes


@pytest.mark.parametrize("bad", ["refs", "scope", "accepted", "missing-history", "map-hash", "proof-reject", "stop"])
def test_no_formatter_for_unproven_or_stale_business(fx, monkeypatch, bad):
    if bad == "refs": fx.current["protected_refs"] = {"main": "9" * 40}
    elif bad == "scope": fx.current["assignment_scope"] = AssignmentScope("OTHER", "P1-00b", [], [], "2099", "9" * 64)
    elif bad == "accepted": monkeypatch.setattr(helper.autopilot, "_load_accepted_checkpoint", lambda *_a: {"repository": {"head": fx.current["head"]}})
    elif bad == "missing-history": fx.history_path.unlink()
    elif bad == "map-hash":
        data = dict(fx.history); data["qoder_review_receipt_sha256"] = "0" * 64; write(fx.history_path, data)
    elif bad == "proof-reject": monkeypatch.setattr(helper.review, "validate_result_review", lambda *_a: (_ for _ in ()).throw(ValueError("real proof rejected")))
    else: (fx.root / "STOP").touch()
    with pytest.raises(ValueError): repair(fx)
    assert not fx.processes


@pytest.mark.parametrize("bad", ["source-drift", "original-drift", "business-drift", "session-reuse", "tool", "error", "wrong-facts", "transition-reject"])
def test_failed_repair_spends_one_attempt_and_never_publishes_receipt(fx, monkeypatch, bad):
    if bad == "source-drift": fx.options["after_capture"] = lambda: fx.current.update(head="9" * 40)
    elif bad == "original-drift": fx.options["after_capture"] = lambda: (fx.original / "prompt.txt").write_text("changed")
    elif bad == "business-drift": fx.options["after_capture"] = lambda: fx.history_path.write_text("{}")
    elif bad == "session-reuse": fx.options["session"] = "original-session"
    elif bad == "tool": fx.options["tool"] = True
    elif bad == "error": fx.options["error"] = True
    elif bad == "wrong-facts": fx.result["gemini_worker_run_id"] = "forged-worker"
    else: monkeypatch.setattr(helper.autopilot, "validate_transition", lambda *_a: (_ for _ in ()).throw(ValueError("real transition rejected")))
    with pytest.raises(ValueError): repair(fx)
    assert len(fx.processes) == 1
    assert (fx.original / helper.REPAIR_DIR / "attempt.json").exists()
    assert not (fx.original / helper.REPAIR_DIR / "receipt.json").exists()


def test_no_commit_can_only_be_truthful_blocked_without_credit(fx):
    fx.current["head"] = fx.before["head"]
    fx.result.update(state="BLOCKED", disposition="RESCOPE", category="INTERNAL_REJECTION",
                     summary="No work completed before malformed output.", blockers=["No accepted business commit"],
                     commit=None, gemini_worker_run_id=None, gemini_reviewer_run_id=None, proposal_sha256=None)
    assert repair(fx)["result"]["state"] == "BLOCKED"
    assert fx.proof_calls == ["transition"]


def test_shape_invalid_canonical_result_keeps_exact_original_copy(fx):
    malformed = {"state": "CONTINUE"}
    write(fx.original / "stream.jsonl", framed(json.dumps(malformed)), binary=True)
    write(fx.original / "result.json", malformed)
    original_bytes = (fx.original / "result.json").read_bytes()
    repaired = repair(fx)
    assert (fx.original / helper.REPAIR_DIR / "original-result.json").read_bytes() == original_bytes
    assert json.loads((fx.original / "result.json").read_text()) == fx.result
    assert repaired["original_format_error"] == "COORDINATOR_SCHEMA_SHAPE"


def test_trusted_quota_ready_facts_need_no_normal_reservation(fx, monkeypatch):
    def forbidden(*_a): raise AssertionError("quota controller has no normal reservation")
    monkeypatch.setattr(helper.broker, "read_cycle_reservation", forbidden)
    evidence = {key: value for key, value in fx.facts.items() if key != "commit"}
    assert repair(fx, business_evidence=evidence)["result"] == fx.result
    assert fx.proof_calls == ["gemini", "committed", "qoder", "transition", "qoder"]


def test_multiple_approval_maps_do_not_select_arbitrarily(fx):
    second = dict(fx.history); second["correction_count"] = 1
    write(fx.pipeline / "pre-review-tests-1.json", fx.native)
    write(fx.pipeline / "qoder-review-1.json", second)
    with pytest.raises(helper.OutputRepairError, match="UNIQUE_COMMITTED_APPROVAL"):
        repair(fx)
    assert not fx.processes


def test_one_committed_unit_cannot_format_whole_plan_complete(fx):
    fx.result["state"] = "COMPLETE"
    with pytest.raises(helper.OutputRepairError, match="ONE_UNIT_ONLY"):
        repair(fx)
    assert not (fx.original / helper.REPAIR_DIR / "receipt.json").exists()


@pytest.mark.parametrize("body_error", [True, False])
def test_controller_wiring_persists_repair_and_runs_gates_before_checkpoint(fx, monkeypatch, body_error):
    from contextlib import nullcontext
    ap = helper.autopilot
    state_root = fx.root / ".local/two-site-refactor/autopilot"
    events = []
    monkeypatch.setattr(ap, "load_ai_config", lambda _r: copy.deepcopy(fx.ai))
    from scripts import two_site_cycle_metrics as metrics
    monkeypatch.setattr(metrics, "_observe_cycle", lambda *_a: nullcontext({}))
    monkeypatch.setattr(ap, "validate_repository", lambda *_a, **_kw: copy.deepcopy(fx.before if not events else fx.current))
    monkeypatch.setattr(ap, "prompt_for_cycle", lambda *_a, **_kw: "synthetic controller prompt")
    monkeypatch.setattr(ap, "coordinator_identity", lambda *_a: fx.identity)
    monkeypatch.setattr(ap, "coordinator_command", lambda *_a: [str(fx.cli)])
    monkeypatch.setattr(ap, "buildwrap_coordinator_command", lambda _r, _d, argv, **_kw: argv)
    monkeypatch.setattr(ap, "git", lambda *_a: "Fixture")
    monkeypatch.setattr(ap, "file_sha256", lambda *_a: fx.identity["sha256"])
    monkeypatch.setattr(ap, "write_supervisor_state", lambda *_a, **_kw: None)

    class Thread:
        def __init__(self, *a, **kw): pass
        def start(self): pass
        def join(self, **kw): pass
    class Process:
        pid = 12345
        returncode = 0
        def __enter__(self): return self
        def __exit__(self, *_args): return False
    monkeypatch.setattr(ap.threading, "Thread", Thread)
    monkeypatch.setattr(ap.subprocess, "Popen", lambda *_a, **_kw: Process())

    def capture(_p, _in, stream, stderr, *_a, **_kw):
        write(stream, framed("synthetic prose"), binary=True); write(stderr, b"", binary=True)
        if not body_error: write(stream.parent / "result.json", {"state": "CONTINUE"})
    monkeypatch.setattr(ap, "capture_bounded_process", capture)
    def finalize(*_a, **_kw):
        if body_error: raise qoder.QoderTerminalBodyError("QODER_TERMINAL_BODY_JSON")
    monkeypatch.setattr(ap, "finalize_qoder_output", finalize)
    def formatting(_root, directory, _before, **kwargs):
        events.append("repair")
        assert kwargs["observed_returncode"] == 0 and kwargs["business_evidence"] is None
        write(directory / "result.json", fx.result)
        return {"result": dict(fx.result), "receipt_path": "synthetic/receipt.json",
                "result_path": str((directory / "result.json").relative_to(fx.root)),
                "repaired_result_path": "synthetic/repaired-result.json",
                "original_format_error": "QODER_TERMINAL_BODY_JSON" if body_error else "COORDINATOR_SCHEMA_SHAPE"}
    monkeypatch.setattr(helper, "repair_coordinator_output", formatting)
    def transition(_root, _before, result):
        events.append("transition")
        return {**result, "head_before": fx.before["head"], "head_after": fx.current["head"], "head_changed": True}
    monkeypatch.setattr(ap, "validate_transition", transition)
    monkeypatch.setattr(helper.review, "validate_result_review", lambda *_a: events.append("review"))
    monkeypatch.setattr(ap, "publish_accepted_checkpoint", lambda *_a: events.append("checkpoint"))
    directory, result = ap.run_cycle(fx.root, fx.cli, state_root, 2, 30)
    assert events == ["repair", "transition", "review", "checkpoint"]
    status = json.loads((directory / "status.json").read_text())
    assert status["state"] == "CONTINUE" and result["state"] == "CONTINUE"
    assert status["output_contract_repair"]["receipt_path"] == "synthetic/receipt.json"
    assert status["output_contract_repair"]["original_format_error"]
