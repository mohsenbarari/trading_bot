"""Synthetic owned-request lifecycle tests; no model/acceptance evidence."""
from __future__ import annotations
import copy
import hashlib
import json
import time
from unittest.mock import Mock
import pytest
from scripts import two_site_codex_autopilot as ap
from scripts import two_site_coordinator_broker_wait as sut
from scripts import two_site_provider_broker as broker
from scripts import two_site_proposal_runner as runner
from scripts import two_site_qoder_driver as qoder
from scripts.two_site_assignment_scope import AssignmentScope
from scripts.two_site_preparation_contract import PreparationStartup

CYCLE = "cycle-0001-20990101T000000Z-123"
HEAD = "1" * 40
MODEL = "synthetic-qoder-model"

def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")))
    path.chmod(0o600)

def sdk(path, text, session="original-session"):
    frames = [{"type":"system","subtype":"init","model":MODEL,"session_id":session,"tools":["Read"]},
              {"type":"result","subtype":"success","is_error":False,"stop_reason":"end_turn",
               "session_id":session,"num_turns":1,"result":text}]
    path.write_text("".join(json.dumps(e)+"\n" for e in frames)); path.chmod(0o600)

def blocked():
    return dict(schema_version="1.0",state="BLOCKED",disposition="HALT",category="EXTERNAL_GATE",
                summary="A real final gate remains blocked.",commit=None,next_action="Inspect actual evidence.",
                blockers=["Synthetic final gate"],gemini_worker_run_id=None,gemini_reviewer_run_id=None,proposal_sha256=None)

@pytest.fixture
def owned(tmp_path, monkeypatch):
    root=tmp_path;directory=root/".local/two-site-refactor/autopilot/runs"/CYCLE
    directory.mkdir(parents=True,mode=0o700);directory.chmod(0o700);directory.parent.chmod(0o700)
    scope=AssignmentScope(assignment_id="WA-OWNED",stage_id="P1-00b",allowed_paths=("tests/**",),
                          forbidden_paths=(),expires_at="2099-01-01T00:00:00Z",sha256="a"*64)
    before=dict(head=HEAD,status="",assignment_scope=scope,protected_refs={},control_blobs={})
    task=root/".local/two-site-refactor/tasks/owned.json";put(task,{"task_id":"OWNED-TEST"})
    request=dict(schema_version="1.0",request_id="b"*32,task_path=task.relative_to(root).as_posix(),
                 task_sha256=hashlib.sha256(task.read_bytes()).hexdigest(),run_prefix="owned-test",timeout=30,
                 source_head=HEAD,assignment_id=scope.assignment_id,assignment_sha256=scope.sha256,
                 controller_run_id=CYCLE,created_at=time.time()-2)
    put(directory/"preparation-startup.json",PreparationStartup(1,"one_shot",CYCLE,HEAD,scope.sha256).to_mapping())
    broker.publish_cycle_reservation(root,request)
    _,status_path,_,_,_=broker.broker_paths(root)
    status=dict(state="RUNNING",pid=1234,started_at=time.time()-10,updated_at=time.time(),request_id=request["request_id"])
    put(status_path,status);broker._publish_cycle_dispatch(root,request,status)
    sdk(directory/"stream.jsonl","Waiting for the exact background request.")
    config={"roles":{"coordinator":{"provider":"qoder","model":MODEL,"reasoning_effort":"xhigh"}}}
    monkeypatch.setattr(ap,"validate_repository",lambda *a,**k:copy.deepcopy(before))
    monkeypatch.setattr(sut.config,"load_ai_config",lambda _r:copy.deepcopy(config))
    monkeypatch.setattr(broker,"POLL_SECONDS",.001)
    payload=dict(state="READY_FOR_CODEX",task_id="OWNED-TEST",source_head=HEAD,final_worker_run_id="owned-worker-0",
                 final_reviewer_run_id="owned-reviewer-0",proposal_sha256="c"*64,blockers=[])
    response=dict(schema_version="1.0",request_id=request["request_id"],run_prefix=request["run_prefix"],
                  source_head=HEAD,state="COMPLETED",exit_code=0,stdout=json.dumps(payload),stderr="",finished_at=time.time())
    from scripts import two_site_cycle_packet as packet
    from scripts import two_site_qoder_review as review
    monkeypatch.setattr(runner,"load_task",lambda *_a,**_k:({"task_id":"OWNED-TEST"},{}))
    prepare=Mock(return_value=({}, {}, {}, {}, []));final=Mock(return_value={})
    monkeypatch.setattr(packet,"_prepare_proposal",prepare);monkeypatch.setattr(review,"validate_review_receipt",final)
    for name in ("make_request","_submit_request","execute_pipeline"):
        monkeypatch.setattr(broker,name,Mock(side_effect=AssertionError("proposal replay forbidden")))
    monkeypatch.setattr(runner,"run",Mock(side_effect=AssertionError("generation forbidden")))
    return dict(root=root,directory=directory,before=before,config=config,request=request,response=response,
                status_path=status_path,status=status,payload=payload,prepare=prepare,final=final)

def invoke(s,*,progress=None,complete=None,deadline=None):
    return sut.finish_owned_request(s["root"],s["directory"],s["before"],s["config"],
        deadline_monotonic=time.monotonic()+60 if deadline is None else deadline,
        progress=progress or (lambda _phase:None),complete=complete or Mock())

def completion(s,*,session="new-session",result=None):
    def perform(directory,prompt,timeout):
        assert "Do not build/freeze a Task" in prompt and CYCLE in prompt
        assert s["request"]["task_sha256"] in prompt and 0<timeout<=900
        value=blocked() if result is None else result
        sdk(directory/"stream.jsonl",json.dumps(value),session)
        value=qoder.finalize_qoder_output(directory,expected_model=MODEL)
        return value,qoder.validate_qoder_terminal(directory,expected_model=MODEL)
    return Mock(side_effect=perform)

def terminal(s):broker._publish_cycle_terminal(s["root"],s["request"],s["response"])

def test_pending_ready_waits_same_request_then_only_one_completion(owned):
    phases=[];complete=completion(owned)
    def progress(phase):
        phases.append(phase)
        if phases.count("WAITING_PROVIDER")==2:terminal(owned)
    value=invoke(owned,progress=progress,complete=complete)
    assert value["kind"]=="OWNED_BROKER_COORDINATOR_COMPLETION" and value["result"]==blocked()
    assert complete.call_count==1 and "COORDINATOR_COMPLETION" in phases
    owned["prepare"].assert_called_once();owned["final"].assert_called_once()
    receipt=json.loads((owned["directory"]/"owned-broker-completion/receipt.json").read_text())
    assert receipt["controller_run_id"]==CYCLE and receipt["original_session_id"]=="original-session"
    assert receipt["completion_session_id"]=="new-session" and receipt["observed_exit_code"]==0
    with pytest.raises(FileExistsError):invoke(owned,complete=complete)
    assert complete.call_count==1

def test_terminal_failure_never_completes_or_formats(owned):
    owned["response"].update(exit_code=1,stdout='{"state":"TASK_BLOCKED"}');terminal(owned)
    complete=Mock(side_effect=AssertionError("completion forbidden"));value=invoke(owned,complete=complete)
    assert value["kind"]=="ROOT_OBSERVED_BROKER_TERMINAL_FAILURE";ap.validate_result(value["result"])
    assert value["result"]["commit"] is None and value["result"]["gemini_worker_run_id"] is None
    assert complete.call_count==owned["prepare"].call_count==owned["final"].call_count==0

def test_already_valid_final_decision_is_not_rejudged(owned):
    terminal(owned);sdk(owned["directory"]/"stream.jsonl",json.dumps(blocked()))
    complete=Mock(side_effect=AssertionError("rejudging forbidden"))
    assert invoke(owned,complete=complete) is None;complete.assert_not_called()

@pytest.mark.parametrize("fault",["request","source","schema","task","model","owner","stale-owner","record-mode"])
def test_wrong_binding_never_completes(owned,monkeypatch,fault):
    p=owned["directory"]/broker._CYCLE_DISPATCH;x=json.loads(p.read_text())
    if fault=="request":x["request"]["request_id"]="d"*32
    elif fault=="source":owned["before"]["head"]="e"*40
    elif fault=="schema":x["schema_version"]="wrong"
    elif fault=="task":(owned["root"]/owned["request"]["task_path"]).write_text("changed")
    elif fault=="model":monkeypatch.setattr(sut.config,"load_ai_config",lambda _r:{})
    elif fault=="owner":owned["status"]["pid"]=9999;put(owned["status_path"],owned["status"])
    elif fault=="stale-owner":owned["status"]["updated_at"]=time.time()-100;put(owned["status_path"],owned["status"])
    put(p,x)
    if fault=="record-mode":p.chmod(0o644)
    complete=Mock(side_effect=AssertionError("completion forbidden"))
    with pytest.raises((sut.OwnedBrokerWaitError,broker.BrokerError)):invoke(owned,complete=complete)
    complete.assert_not_called()

def test_original_deadline_is_not_reset(owned):
    complete=Mock(side_effect=AssertionError("completion forbidden"))
    with pytest.raises(sut.OwnedBrokerWaitError,match="DEADLINE"):
        invoke(owned,complete=complete,deadline=time.monotonic()-1)
    complete.assert_not_called()

def test_stop_waits_existing_work_but_denies_fresh_completion(owned):
    (owned["directory"].parents[1]/"STOP").write_text("STOP\n");seen=[]
    def progress(phase):
        seen.append(phase)
        if len(seen)==2:terminal(owned)
    complete=Mock(side_effect=AssertionError("completion forbidden"))
    with pytest.raises(sut.OwnedBrokerWaitError,match="STOP"):
        invoke(owned,progress=progress,complete=complete)
    assert len(seen)>=2 and (owned["directory"]/broker._CYCLE_TERMINAL).exists();complete.assert_not_called()

def test_ready_revalidates_actual_proof_gate(owned):
    terminal(owned);owned["prepare"].side_effect=runner.ProposalError("invalid actual proof")
    complete=Mock(side_effect=AssertionError("completion forbidden"))
    with pytest.raises(runner.ProposalError,match="invalid actual proof"):invoke(owned,complete=complete)
    complete.assert_not_called();owned["final"].assert_not_called()

def test_reused_completion_session_rejected(owned):
    terminal(owned)
    with pytest.raises(sut.OwnedBrokerWaitError,match="SESSION_REUSE"):
        invoke(owned,complete=completion(owned,session="original-session"))

def test_completion_cannot_substitute_other_proposal_ids(owned):
    terminal(owned);value=dict(blocked(),state="CONTINUE",disposition="NONE",category="NONE",commit="f"*40,
        blockers=[],gemini_worker_run_id="other",gemini_reviewer_run_id="other",proposal_sha256="f"*64)
    with pytest.raises(sut.OwnedBrokerWaitError,match="COMPLETION_EVIDENCE"):
        invoke(owned,complete=completion(owned,result=value))

def test_consumed_response_still_has_immutable_cycle_terminal(owned):
    terminal(owned);complete=completion(owned)
    assert invoke(owned,complete=complete)["request_id"]==owned["request"]["request_id"]
    assert complete.call_count==1

@pytest.mark.parametrize("fault", ["foreign-session", "wrong-model", "error-terminal"])
def test_unqualified_original_sdk_never_starts_completion(owned, fault):
    terminal(owned);path=owned["directory"]/"stream.jsonl"
    frames=[json.loads(line) for line in path.read_text().splitlines()]
    if fault=="foreign-session":frames[-1]["session_id"]="foreign"
    elif fault=="wrong-model":frames[0]["model"]="wrong-model"
    else:frames[-1]["is_error"]=True
    path.write_text("".join(json.dumps(e)+"\n" for e in frames))
    complete=Mock(side_effect=AssertionError("completion forbidden"))
    with pytest.raises(qoder.QoderError):invoke(owned,complete=complete)
    complete.assert_not_called()


def test_cycle_terminal_publication_is_idempotent_and_never_rewrites(owned):
    terminal(owned);path=owned["directory"]/broker._CYCLE_TERMINAL;original=path.read_bytes()
    terminal(owned);assert path.read_bytes()==original
    owned["response"]["stdout"]="different response"
    with pytest.raises(broker.BrokerError,match="different bytes"):terminal(owned)
    assert path.read_bytes()==original


def test_original_request_lifetime_caps_even_a_long_controller_deadline(owned,monkeypatch):
    real_time=time.time
    monkeypatch.setattr(sut.time,"time",lambda:real_time()+broker._overall_timeout(30)+31)
    complete=Mock(side_effect=AssertionError("completion forbidden"))
    with pytest.raises(sut.OwnedBrokerWaitError,match="DEADLINE"):
        invoke(owned,complete=complete,deadline=time.monotonic()+100000)
    complete.assert_not_called()


def test_fake_commit_still_fails_the_unchanged_outer_transition(owned):
    terminal(owned)
    value=dict(blocked(),state="CONTINUE",disposition="NONE",category="NONE",commit="f"*40,blockers=[],
        gemini_worker_run_id=owned["payload"]["final_worker_run_id"],
        gemini_reviewer_run_id=owned["payload"]["final_reviewer_run_id"],
        proposal_sha256=owned["payload"]["proposal_sha256"])
    effective=invoke(owned,complete=completion(owned,result=value))
    with pytest.raises(ap.AutopilotError):
        ap.validate_transition(owned["root"],owned["before"],effective["result"])


def test_wait_timeout_retains_original_custody_and_never_completes(owned):
    complete=Mock(side_effect=AssertionError("completion forbidden"))
    with pytest.raises(broker.BrokerError,match="deadline exceeded"):
        invoke(owned,complete=complete,deadline=time.monotonic()+.03)
    assert (owned["directory"]/broker._CYCLE_DISPATCH).is_file()
    assert (owned["directory"]/"broker-reservation.json").is_file()
    assert not (owned["directory"]/"owned-broker-completion").exists()
    complete.assert_not_called()

@pytest.mark.parametrize("outcome", ["failed", "ready"])
def test_run_cycle_owns_pending_before_formatter_and_keeps_outer_gates(owned, monkeypatch, outcome):
    """Reuse the AP fake-CLI pattern, but execute real run_cycle/owned_complete."""
    import io
    from contextlib import nullcontext
    from scripts import two_site_cycle_metrics as metrics
    from scripts import two_site_qoder_output_repair as formatting
    root = owned["root"]; state = root / ".local/two-site-refactor/autopilot"
    cli = root / "synthetic-qoder"; cli.write_bytes(b"SYNTHETIC_NOT_EXECUTABLE_MODEL"); cli.chmod(0o500)
    identity = {"provider": "qoder", "sha256": hashlib.sha256(cli.read_bytes()).hexdigest()}
    schema = root / ap.SCHEMA_REL; schema.parent.mkdir(parents=True, exist_ok=True)
    schema.write_bytes((runner.ROOT / ap.SCHEMA_REL).read_bytes())
    monkeypatch.setattr(ap, "load_ai_config", lambda _r: copy.deepcopy(owned["config"]))
    monkeypatch.setattr(metrics, "_observe_cycle", lambda *_a: nullcontext({}))
    monkeypatch.setattr(ap, "prompt_for_cycle", lambda *_a, **_k: "Synthetic initial Coordinator prompt.")
    monkeypatch.setattr(ap, "coordinator_identity", lambda *_a: identity)
    monkeypatch.setattr(ap, "coordinator_command", lambda *_a: [str(cli)])
    monkeypatch.setattr(ap, "buildwrap_coordinator_command", lambda _r, _d, argv, **_k: argv)
    monkeypatch.setattr(ap, "git", lambda *_a: "Synthetic")
    formatter = Mock(side_effect=AssertionError("Paid formatter must not see pending ownership"))
    monkeypatch.setattr(formatting, "repair_coordinator_output", formatter)
    checkpoint = Mock(side_effect=AssertionError("No accepted commit exists"))
    monkeypatch.setattr(ap, "publish_accepted_checkpoint", checkpoint)
    class Thread:
        def __init__(self, *a, **kw): pass
        def start(self): pass
        def join(self): pass
    monkeypatch.setattr(ap.threading, "Thread", Thread)
    processes = []
    class Process:
        pid = 12345
        returncode = 0
        def __init__(self, *a, **kw):
            self.stdin, self.stdout, self.stderr = io.BytesIO(), io.BytesIO(), io.BytesIO()
            self.environment = kw["env"]; processes.append(self)
        def __enter__(self): return self
        def __exit__(self, *_a): return False
    monkeypatch.setattr(ap.subprocess, "Popen", Process)
    captures = []; phases = []
    def capture(process, stdin, stream, stderr, *_a, **_k):
        captures.append(stream.parent)
        stderr.write_bytes(b"")
        if stream.parent.name != "owned-broker-completion":
            # run_cycle itself created the original startup; publish its actual
            # reservation/custody at the same fake-CLI submit boundary.
            owned["directory"] = stream.parent
            owned["request"]["controller_run_id"] = stream.parent.name
            broker.publish_cycle_reservation(root, owned["request"])
            broker._publish_cycle_dispatch(root, owned["request"], owned["status"])
            sdk(stream, "The owned Broker is pending; waiting for its background job.")
        else:
            assert process.environment["TWO_SITE_AUTOPILOT_RUN_ID"] == owned["directory"].name
            value = dict(blocked(), state="CONTINUE", disposition="NONE", category="NONE", commit="f"*40,
                blockers=[], gemini_worker_run_id=owned["payload"]["final_worker_run_id"],
                gemini_reviewer_run_id=owned["payload"]["final_reviewer_run_id"],
                proposal_sha256=owned["payload"]["proposal_sha256"])
            sdk(stream, json.dumps(value), session="fresh-completion-session")
    monkeypatch.setattr(ap, "capture_bounded_process", capture)
    def supervisor(*_a, **kw):
        phases.append(kw.get("phase"))
        if phases.count("WAITING_PROVIDER") == 2:
            if outcome == "failed": owned["response"].update(exit_code=1, stdout='{"state":"TASK_BLOCKED"}')
            terminal(owned)
    monkeypatch.setattr(ap, "write_supervisor_state", supervisor)
    real_finalize = qoder.finalize_qoder_output
    finalize_paths = []
    def finalize(directory, **kw):
        finalize_paths.append(directory)
        assert directory.name == "owned-broker-completion"  # Never parse original prose as JSON.
        return real_finalize(directory, **kw)
    monkeypatch.setattr(ap, "finalize_qoder_output", finalize)
    if outcome == "failed":
        directory, value = ap.run_cycle(root, cli, state, 2, 60)
        assert value["state"] == "BLOCKED" and value["commit"] is None
        assert len(processes) == len(captures) == 1 and finalize_paths == []
        status = json.loads((directory/"status.json").read_text())
        assert status["owned_broker_completion"]["kind"] == "ROOT_OBSERVED_BROKER_TERMINAL_FAILURE"
    else:
        # Real nested completion and strict SDK/schema parsing run, but the
        # unchanged outer transition rejects its fabricated commit (HEAD did not move).
        with pytest.raises(ap.AutopilotError): ap.run_cycle(root, cli, state, 2, 60)
        assert len(processes) == len(captures) == 2
        assert captures[1].parent == captures[0] and captures[1].name == "owned-broker-completion"
        assert finalize_paths == [captures[1]]
        status = json.loads((captures[0]/"status.json").read_text())
        assert status["state"] == "FAILED"
        assert processes[0].environment["TWO_SITE_AUTOPILOT_RUN_ID"] == processes[1].environment["TWO_SITE_AUTOPILOT_RUN_ID"]
    assert "WAITING_PROVIDER" in phases
    formatter.assert_not_called(); checkpoint.assert_not_called()
    assert ap._ACTIVE_PROCESS is None
