"""Synthetic SDK evidence, real source/transport/reservation validators; no models."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path

import pytest

from scripts import two_site_proposal_runner as runner
from scripts import two_site_proposal_pipeline as pipeline
from scripts import two_site_qoder_author as author
from scripts import two_site_qoder_driver as driver
from scripts.two_site_semantic_task import build_identity
from scripts.two_site_semantic_attempts import MARKER, reserve_semantic_attempt

HEAD = "a" * 40
CONTROLLER = "cycle-0001-20261104T090000Z-123"


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value if type(value) is bytes else runner.serialize_json(value))
    path.chmod(0o600)


@pytest.fixture
def fx(tmp_path):
    root = tmp_path.resolve()
    policy = {"schema_version": 1, "enabled": True, "model_role": "coordinator",
              "generation_variants_per_phase": 1,
              "qualified_failures": ["QUALIFIED_GENERATION_EXHAUSTION", "QUALIFIED_PREGEN_PROFILE"]}
    save(root / author.AUTHOR_POLICY_REL, policy)
    model = {"provider": "qoder", "model": "qoder-fixture", "reasoning_effort": "xhigh"}
    gemini = {"provider": "antigravity", "model": "gemini-fixture", "reasoning_effort": "high"}
    config = {"schema_version": 1, "roles": {"coordinator": model, "final_reviewer": model,
                                               "worker": gemini, "reviewer": gemini}}
    save(root / "docs/refactor/two-server/templates/automation-models.json", config)
    cli = root / "qoder-cli"
    save(cli, b"#!/bin/sh\nexit 99\n"); cli.chmod(0o700)
    runtime = {"schema_version": 1, "coordinator": {"path": str(cli), "version": "fixture-version",
                                                    "sha256": runner.file_sha256(cli)}}
    save(root / ".local/two-site-refactor/ai-runtime.json", runtime)
    save(root / "src.py", b"value = 1\n")
    sources = {"src.py": {"sha256": runner.file_sha256(root / "src.py"), "content": "value = 1\n"},
               author.AUTHOR_POLICY_REL.as_posix(): {
                   "sha256": runner.file_sha256(root / author.AUTHOR_POLICY_REL),
                   "content": (root / author.AUTHOR_POLICY_REL).read_text()}}
    identity = build_identity(stage_id="P1-00b", gap_reference="docs/example.md#actual-gap",
        scenario_key="one-handler-boundary", symbol_groups=[],
        source_manifest={k: v["sha256"] for k, v in sources.items()})
    task = {"task_id": "AUTHOR1", "goal": "One complete behavior" + MARKER + json.dumps({
        "head": HEAD, "gap_reference": identity["gap_reference"], "excerpts": [], "semantic_identity": identity}),
        "context_files": list(sources), "allowed_files": ["src.py"], "requirements": ["Change value to 2"],
        "test_selectors": ["tests/test_value.py"]}
    directory = root / ".local/two-site-refactor/pipeline"
    directory.mkdir(parents=True, mode=0o700)
    reservation = reserve_semantic_attempt(root, directory, task, task["task_id"], "run-author")
    return root, task, sources, model, gemini, runtime, reservation


def failed(fx, run_id="run-author-worker-0", profile=None):
    root, task, sources, _model, gemini, _runtime, _reservation = fx
    directory = root / ".local/two-site-refactor/proposals" / run_id
    directory.mkdir(parents=True, mode=0o700)
    configured = {"worker": gemini, "reviewer": gemini}
    status = {"run_id": run_id, "task_id": task["task_id"], "role": "worker", "head": HEAD,
              "state": "BLOCKED", "authority": "PROPOSAL_ONLY_NOT_ACCEPTANCE", "model": gemini["model"],
              "model_config": configured, "model_config_sha256": runner.sha(runner.serialize_json(configured)),
              "controller_run_id": CONTROLLER}
    save(directory / "task.json", {"task": task, "sources": sources, "source_head": HEAD})
    schema = runner.schema("worker", task, sources)
    save(directory / "response-schema.json", schema); save(directory / "prompt.txt", b"fixture complete input\n")
    if profile is not None:
        status["error"] = "ProposalProfileError: " + profile
    else:
        cid = "failed-fixture-session"
        events = [{"event": "init", "conversation_id": cid,
                   "init": {"model": gemini["model"], "json_schema": schema}},
                  {"event": "result", "result": {"status": "ERROR", "conversation_id": cid,
                   "num_turns": 1, "json_schema": schema, "response": "",
                   "error": "Your previous response exceeded the output token limit"}}]
        raw = ("\n".join(json.dumps(e) for e in events) + "\n").encode()
        save(directory / "stream.jsonl", raw); save(directory / "stderr.txt", b"")
        status.update(error="ProposalError: " + author.EXHAUSTED,
            runtime_profile={"conversation_id": cid},
            failure_retained_outputs={"stream.jsonl": {"utf8_bytes": len(raw), "sha256": runner.sha(raw)},
                                      "stderr.txt": {"utf8_bytes": 0, "sha256": runner.sha(b"")}})
    save(directory / "status.json", status)
    return directory


def captured_author(fx, raw=None):
    root, task, sources, model, _gemini, runtime, _reservation = fx
    failed(fx)
    authorization = author.business_authorization(root, base_run_id="run-author-worker-0",
        task=task, sources=sources, source_head=HEAD)
    directory = root / ".local/two-site-refactor/proposals/run-author-worker-0-qa"
    directory.mkdir(mode=0o700)
    if raw is None:
        raw = {"status": "PROPOSAL_READY", "summary": "Complete value behavior", "covered_requirements": [1],
               "findings": [], "changes": [{"path": "src.py", "original_sha256": sources["src.py"]["sha256"],
                                              "content": "value = 2\n", "replacements": []}]}
    result = author.normalize_author_result(root, raw, task=task, sources=sources, source_head=HEAD,
                                            authorization=authorization)
    material = {"task": task, "sources": sources, "source_head": HEAD,
                "author_authorization": authorization, "author_controller_run_id": CONTROLLER}
    schema = author._schema(task, sources, authorization)
    status = {"run_id": directory.name, "task_id": task["task_id"], "role": "worker", "head": HEAD,
              "state": "PROPOSAL_READY", "authority": "PROPOSAL_ONLY_NOT_ACCEPTANCE",
              "author_provider": "qoder", "controller_run_id": CONTROLLER,
              "model": model["model"], "prepared_effort": model["reasoning_effort"], "author_model_config": model,
              "author_model_config_sha256": runner.sha(runner.serialize_json(model)),
              "conversation_id": "new-distinct-author-session", "completion_policy": author.POLICY,
              "author_cli": {**runtime["coordinator"], "provider": "qoder", "model": model["model"],
                             "reasoning": model["reasoning_effort"], "logged_in": True, "auto_update_enabled": False}}
    sdk = [{"type": "system", "subtype": "init", "session_id": status["conversation_id"],
            "model": model["model"], "tools": []},
           {"type": "result", "subtype": "success", "session_id": status["conversation_id"],
            "is_error": False, "stop_reason": "end_turn", "num_turns": 1, "result": json.dumps(raw)}]
    save(directory / "task.json", material); save(directory / "response-schema.json", schema)
    save(directory / "prompt.txt", author._prompt(task, sources, HEAD, schema, material).encode())
    save(directory / "stream.jsonl", ("\n".join(json.dumps(e) for e in sdk) + "\n").encode())
    save(directory / "stderr.txt", b""); save(directory / "result.json", result); save(directory / "raw-result.json", raw)
    save(directory / "capture.json", {"exit_code": 0, "normal_exit_observed": True, "owned_group_absent": True})
    status["result_sha256"] = runner.file_sha256(directory / "result.json")
    proof = author._proof(root, directory, task=task, sources=sources, status=status, result=result)
    save(directory / author.PROOF, proof); status["completion_proof_sha256"] = runner.file_sha256(directory / author.PROOF)
    save(directory / "status.json", status)
    return directory, status, result


def test_first_class_qoder_completion_preserves_reservation_and_failed_gemini(fx):
    root, task, sources, _model, _gemini, _runtime, reservation = fx
    original = runner.serialize_json(reservation)
    directory, status, result = captured_author(fx)
    proof = runner.validate_generation_completion(root, directory, task=task, sources=sources,
                                                  role="worker", status=status, result=result)
    assert proof["author_provider"] == "qoder" and proof["policy"] == author.POLICY
    assert proof["authorization"]["semantic_task_fingerprint"] == reservation["fingerprint"]
    assert runner.serialize_json(reservation) == original
    assert not (directory.parent / "run-author-worker-0/result.json").exists()
    assert result["changes"][0] == {"path": "src.py", "original_sha256": sources["src.py"]["sha256"],
                                     "content": "value = 2\n"}


@pytest.mark.parametrize("fault", ["model", "session", "provider", "exit", "raw_result", "failed_stream", "snapshot"])
def test_tampered_sdk_source_exit_or_failure_custody_never_becomes_completion(fx, fault):
    root, task, sources, *_ = fx
    directory, status, result = captured_author(fx)
    if fault in {"model", "session", "provider"}:
        status[{"model": "model", "session": "conversation_id", "provider": "author_provider"}[fault]] = "forged"
    elif fault == "exit":
        save(directory / "capture.json", {"exit_code": -9, "normal_exit_observed": False, "owned_group_absent": True})
    elif fault == "raw_result":
        save(directory / "raw-result.json", {"different": "data"})
    elif fault == "snapshot":
        value = json.loads((directory / "task.json").read_bytes()); value["sources"]["src.py"]["content"] = "forged"
        save(directory / "task.json", value)
    else:
        (directory.parent / "run-author-worker-0/stream.jsonl").write_bytes(b"changed\n")
    with pytest.raises((runner.ProposalError, driver.QoderError, ValueError)):
        runner.validate_generation_completion(root, directory, task=task, sources=sources,
                                              role="worker", status=status, result=result)


def test_profile_fallback_requires_all_three_exact_failed_preparation_attempts(fx):
    root, task, sources, *_ = fx
    ids = ["run-author-worker-0", "run-author-worker-0-retry-1", "run-author-worker-0-retry-2"]
    for index, run_id in enumerate(ids):
        failed(fx, run_id, profile=pipeline._EMPTY_INITIAL_PROFILE_PRE_SECOND)
        if index < 2:
            with pytest.raises(runner.ProposalError, match="BUDGET"):
                author.business_authorization(root, base_run_id=ids[0], task=task, sources=sources, source_head=HEAD)
    value = author.business_authorization(root, base_run_id=ids[0], task=task, sources=sources, source_head=HEAD)
    assert [x["run_id"] for x in value["failed_attempts"]] == ids
    save(root / ".local/two-site-refactor/proposals" / ids[1] / "stream.jsonl", b"unqualified generation")
    with pytest.raises(runner.ProposalError):
        author.validate_authorization(root, value, task=task, sources=sources, source_head=HEAD)


@pytest.mark.parametrize("error", ["authentication failed", "quota exceeded", "network timeout", "unknown error"])
def test_unknown_or_quota_auth_transport_errors_do_not_grant_author_fallback(fx, error):
    root, task, sources, *_ = fx; directory = failed(fx)
    value = json.loads((directory / "status.json").read_bytes()); value["error"] = "ProposalError: " + error
    save(directory / "status.json", value)
    with pytest.raises(runner.ProposalError, match="NOT_QUALIFIED"):
        author.business_authorization(root, base_run_id=directory.name, task=task, sources=sources, source_head=HEAD)


def test_schema_transport_requires_four_keys_and_real_replacement_normalization(fx):
    root, task, sources, *_ = fx
    sources["src.py"] = {"content": "#" + "x" * 9000 + "\nvalue = 1\n", "sha256": None}
    sources["src.py"]["sha256"] = runner.sha(sources["src.py"]["content"].encode())
    raw = {"status": "PROPOSAL_READY", "summary": "One exact change", "covered_requirements": [1], "findings": [],
           "changes": [{"path": "src.py", "original_sha256": sources["src.py"]["sha256"], "content": None,
                        "replacements": [{"old": "value = 1\n", "new": "value = 2\n"}]}]}
    normalized = author.normalize_author_result(root, raw, task=task, sources=sources, source_head=HEAD,
                                                 authorization={"kind": author.BUSINESS})
    assert normalized["changes"][0]["content"] == sources["src.py"]["content"].replace("1", "2")
    raw["changes"][0]["content"] = normalized["changes"][0]["content"]
    raw["changes"][0]["replacements"] = []
    with pytest.raises(runner.ProposalError, match="transport policy"):
        author.normalize_author_result(root, raw, task=task, sources=sources, source_head=HEAD,
                                         authorization={"kind": author.BUSINESS})
    raw["changes"][0].pop("replacements")
    with pytest.raises(runner.ProposalError, match="RAW_WORKER_SCHEMA"):
        author.normalize_author_result(root, raw, task=task, sources=sources, source_head=HEAD,
                                         authorization={"kind": author.BUSINESS})


def test_author_command_uses_configured_model_tool_free_worker_schema(fx):
    root, task, sources, model, *_ = fx
    directory = root / ".local/author-command"; directory.mkdir(parents=True)
    schema = runner.schema("worker", task, sources)
    command = driver.qoder_command(root / "qoder-cli", root, directory, "complete prompt", model,
                                    role="proposal_author", response_schema=schema)
    assert command[command.index("--model") + 1] == model["model"]
    assert command[command.index("--tools") + 1] == ""
    assert "receipt_formatter" not in command and "--no-session-persistence" in command
    contract = command[command.index("--append-system-prompt") + 1]
    assert '"changes"' in contract and '"gemini_worker_run_id"' not in contract
    with pytest.raises(driver.QoderError, match="SCHEMA"):
        driver.qoder_command(root / "qoder-cli", root, directory, "prompt", model, role="proposal_author")


def timeout_failure(fx):
    directory = failed(fx)
    status = runner.unique_json((directory / 'status.json').read_text())
    stdout = (directory / 'stream.jsonl').read_bytes()
    stderr = b'[agy] print timeout after 15m0s with turn in progress; returning partial output\n'
    prompt = (directory / 'prompt.txt').read_text()
    wire = runner._cli_stream_input(prompt)
    observed = {'owned_pgid': 12345, 'parent_poll_returncode': None,
        'parent_poll_error_type': None, 'group_probe_error_type': None,
        'input_writer_close_observed': True, 'terminal_read_observed': True,
        'capture_deadline_reached': True, 'owned_group_absent': False,
        'input_written_bytes':len(wire), 'input_expected_bytes':len(wire), 'input_bytes':len(wire),
        'input_sha256':runner.sha(wire), 'stdout_bytes':len(stdout),'stdout_sha256':runner.sha(stdout),
        'stderr_bytes':len(stderr),'stderr_sha256':runner.sha(stderr)}
    after = dict(observed, parent_poll_returncode=-9, owned_group_absent=True)
    status['error']='TimeoutExpired: actual owned CLI timed out after915 seconds'
    status['generation_capture_failure']={'kind':'GENERATION_CAPTURE_FAILURE_OBSERVATIONS_V1',
        'eligible':False,'output_digest_domain':'CAPTURE_ACCEPTED_FILE_WRITES_NOT_POSTCLOSE_READBACK',
        'before_cleanup':observed,'after_cleanup':after}
    status['failure_retained_outputs']['stderr.txt']={'utf8_bytes':len(stderr),'sha256':runner.sha(stderr)}
    save(directory/'stderr.txt',stderr);save(directory/'status.json',status)
    return directory, status


def test_known_outputlimit_timeout_is_failure_qualified_only_after_full_input_and_owned_cleanup(fx):
    root,task,sources,*_=fx
    directory,_=timeout_failure(fx)
    value=author.business_authorization(root,base_run_id=directory.name,task=task,sources=sources,source_head=HEAD)
    assert value['failed_attempts'][0]['classification']=='QUALIFIED_GENERATION_EXHAUSTION'
    assert not (directory/'result.json').exists()


@pytest.mark.parametrize('fault',['group','returncode','input','input_bool','stdout','stderr','session','schema','auth','tool'])
def test_timeout_classification_never_admits_unknown_or_tampered_partial_output(fx,fault):
    root,task,sources,*_=fx;directory,status=timeout_failure(fx)
    after=status['generation_capture_failure']['after_cleanup']
    if fault=='group': after['owned_group_absent']=False
    elif fault=='returncode':after['parent_poll_returncode']=0
    elif fault=='input':after['input_written_bytes']-=1
    elif fault=='input_bool':after['input_bytes']=True
    elif fault=='stdout':after['stdout_sha256']='0'*64
    elif fault=='stderr':save(directory/'stderr.txt',b'authentication refused\n')
    else:
        events=[runner.unique_json(x) for x in (directory/'stream.jsonl').read_text().splitlines()]
        if fault=='session':events[-1]['result']['conversation_id']='other'
        elif fault=='schema':events[-1]['result']['json_schema']={}
        elif fault=='auth':events[-1]['result']['error']='authentication output token limit exceeded'
        else:events.insert(1,{'event':'tool_use','conversation_id':'failed-fixture-session','tool_name':'Bash'})
        raw=('\n'.join(json.dumps(x) for x in events)+'\n').encode();save(directory/'stream.jsonl',raw)
        status['failure_retained_outputs']['stream.jsonl']={'utf8_bytes':len(raw),'sha256':runner.sha(raw)}
        for obs in status['generation_capture_failure'].values():
            if type(obs)is dict:obs.update(stdout_bytes=len(raw),stdout_sha256=runner.sha(raw))
    save(directory/'status.json',status)
    with pytest.raises((runner.ProposalError,ValueError)):
        author.business_authorization(root,base_run_id=directory.name,task=task,sources=sources,source_head=HEAD)


def test_author_actual_four_key_new_file_transport_is_normalized_without_fabricated_legacy_shape(fx):
    root,task,sources,*_=fx
    task=copy.deepcopy(task);sources=copy.deepcopy(sources)
    task['allowed_files']=['new.py'];sources['new.py']={'content':None,'sha256':None}
    raw={'status':'PROPOSAL_READY','summary':'New actual file','covered_requirements':[1],'findings':[],
         'changes':[{'path':'new.py','original_sha256':None,'content':'value = 2\n','replacements':[]}]}
    value=author.normalize_author_result(root,raw,task=task,sources=sources,source_head=HEAD,
                                         authorization={'kind':author.BUSINESS})
    assert value['changes']==[{'path':'new.py','original_sha256':None,'content':'value = 2\n'}]


def test_author_prompt_uses_real320k_worker_bound_and_counts_utf8(fx):
    _root,task,sources,*_=fx
    expanded=copy.deepcopy(sources);expanded['src.py']['content']='#'+('ی'*90000)+'\n'
    schema=runner.schema('worker')
    prompt=author._prompt(task,expanded,HEAD,schema,{})
    assert 160000<len(prompt.encode())<320000
    expanded['src.py']['content']='ی'*160000
    with pytest.raises(runner.ProposalError,match='PROMPT_BOUND'):
        author._prompt(task,expanded,HEAD,schema,{})


def test_exited_parent_descendant_is_killed_before_runner_custody_release(tmp_path):
    # A dedicated harness is subreaper so the spawned orphan is actually reaped;
    # no test runner / unrelated process group is signalled.
    import subprocess,sys
    script=r'''
import ctypes,os,subprocess,sys,threading,time
from scripts.two_site_qoder_author import cleanup_owned_author_group
ctypes.CDLL(None).prctl(36,1,0,0,0)
p=subprocess.Popen([sys.executable,'-c',"import os,time; child=os.fork(); os._exit(0) if child else time.sleep(60)"],start_new_session=True)
p.wait(timeout=5)
reaped=[]
def reap():
    pid,status=os.waitpid(-1,0);reaped.append((pid,status))
t=threading.Thread(target=reap);t.start()
cleanup_owned_author_group(p,maximum_seconds=5)
t.join(5)
assert reaped and os.WIFSIGNALED(reaped[0][1]) and os.WTERMSIG(reaped[0][1])==9
try:os.killpg(p.pid,0)
except ProcessLookupError:pass
else:raise AssertionError('owned group survived')
print('OWNED_DESCENDANT_ABSENT')
'''
    result=subprocess.run([sys.executable,'-c',script],capture_output=True,text=True,timeout=15)
    assert result.returncode==0,(result.stdout,result.stderr)
    assert result.stdout.strip()=='OWNED_DESCENDANT_ABSENT'


@pytest.fixture
def maintenance_boundary(monkeypatch):
    # Unit boundary double only; App's separate immutable-job/Git suite qualifies
    # actual admission. This fixture must not be cited as real authorization.
    import sys,types
    auth={'kind':author.MAINTENANCE,'job_id':'maintenance-fixture','sha256':'b'*64}
    calls=[]
    def validate(root,pointer,*,task,sources,source_head,evidence_only=False):
        assert pointer==auth and source_head==HEAD
        calls.append(evidence_only)
        return {'controller_run_id':CONTROLLER}
    module=types.ModuleType('scripts.two_site_qoder_maintenance')
    module.validate_maintenance_authorization=validate
    module.maintenance_native_overlays=lambda *_:{}
    monkeypatch.setitem(sys.modules,'scripts.two_site_qoder_maintenance',module)
    return auth,calls


def test_maintenance210k_original_one_unique_hunk_materializes_with_source_once_under320k(fx,maintenance_boundary):
    root,task,sources,*_=fx;auth,calls=maintenance_boundary
    task=copy.deepcopy(task);sources=copy.deepcopy(sources)
    original='#'+('x'*210000)+'\nvalue = 1\n'
    sources['src.py']={'content':original,'sha256':runner.sha(original.encode())}
    raw={'status':'PROPOSAL_READY','summary':'One bounded repair','covered_requirements':[1],'findings':[],
         'changes':[{'path':'src.py','original_sha256':sources['src.py']['sha256'],'content':None,
                     'replacements':[{'old':'value = 1\n','new':'value = 2\n'}]}]}
    result=author.normalize_author_result(root,raw,task=task,sources=sources,source_head=HEAD,authorization=auth)
    assert result['changes'][0]['content']==original.replace('value = 1','value = 2')
    assert len(runner.serialize_json(result))>160000 and calls==[True]
    material={'author_authorization':auth,'proposal':runner.serialize_json(result).decode(),'prior_transport':raw,
              'feedback':{'findings':['Preserve every Source condition']}}
    prompt=author._prompt(task,sources,HEAD,author._schema(task,sources,auth),material)
    assert prompt.count('x'*210000)==1 and len(prompt.encode())<320000
    assert 'raw_worker_transport' in prompt and 'canonical_proposal_sha256' in prompt
    transport=runner.build_prepared_review_transport(root,auth,result,task,sources,source_head=HEAD,
        proposal_sha256=runner.sha(runner.serialize_json(result)),raw_transport=raw)
    assert transport['raw_worker_transport']==raw and len(runner.serialize_json(transport))<2048
    too_large=copy.deepcopy(raw);too_large['changes'][0]['replacements'][0]['new']='y'*320000
    with pytest.raises(runner.ProposalError,match='RAW_TRANSPORT_BOUND|320000'):
        author.normalize_author_result(root,too_large,task=task,sources=sources,source_head=HEAD,authorization=auth)


def test_business_materialization_limit_never_inherits_maintenance320k(fx):
    root,task,sources,*_=fx
    task=copy.deepcopy(task);task['allowed_files']=['new.py'];sources=copy.deepcopy(sources)
    sources['new.py']={'content':None,'sha256':None}
    raw={'status':'PROPOSAL_READY','summary':'Outside normal bound','covered_requirements':[1],'findings':[],
         'changes':[{'path':'new.py','original_sha256':None,'content':'#'+('x'*160000),'replacements':[]}]}
    with pytest.raises(runner.ProposalError,match='RAW_TRANSPORT_BOUND'):
        author.normalize_author_result(root,raw,task=task,sources=sources,source_head=HEAD,authorization={'kind':author.BUSINESS})


def test_only_exact_historical33_checkpoint_schema_decodes_and_never_matches_new_controls():
    from scripts import two_site_codex_autopilot as ap
    from tests.test_two_site_autopilot_checkpoint import _make_repository,_make_continue_receipt
    current=_make_repository();receipt=_make_continue_receipt()
    normalized=ap.validate_accepted_repository(current)
    historical=copy.deepcopy(normalized)
    historical['control_blobs']={k:'e'*40 for k in ap._HISTORICAL_CONTROL_KEYS_20261104}
    assert len(historical['control_blobs'])==33
    assert ap._validate_persisted_repository(historical)==historical
    checkpoint={'schema_version':ap.ACCEPTED_CHECKPOINT_SCHEMA_VERSION,'result':receipt,'repository':historical}
    assert ap._accepted_checkpoint_matches_repository(checkpoint,current) is False
    with pytest.raises(ap.AutopilotError):ap.validate_accepted_repository(historical)
    partial=copy.deepcopy(historical);partial['control_blobs'].pop(next(iter(partial['control_blobs'])))
    with pytest.raises(ap.AutopilotError):ap._validate_persisted_repository(partial)


from tests.test_two_site_provider_broker import broker_repo
from tests.test_two_site_model_policy_task_identity import repo as policy_repo


@pytest.mark.parametrize('enabled,required,expected',[(False,False,False),(False,True,False),(True,True,True)])
def test_broker_only_wires_enabled_author_policy_with_required_independent_final_review(broker_repo,monkeypatch,enabled,required,expected):
    from scripts import two_site_provider_broker as broker
    from tests.test_two_site_provider_broker import make_valid_request
    from unittest import mock
    root,task,scope,environment=broker_repo
    if enabled:
        save(root/author.AUTHOR_POLICY_REL,{'schema_version':1,'enabled':True,'model_role':'coordinator',
            'generation_variants_per_phase':1,'qualified_failures':['QUALIFIED_GENERATION_EXHAUSTION','QUALIFIED_PREGEN_PROFILE']})
    monkeypatch.setenv('TWO_SITE_QODER_FINAL_REVIEW_REQUIRED','1' if required else '0')
    request=make_valid_request(root,task,environment)
    process=mock.MagicMock();process.returncode=0;process.__enter__.return_value=process
    def capture(_process,_input,out,err,*args,**kwargs):out.write_text('{}');err.write_text('')
    with mock.patch.object(broker.subprocess,'Popen',return_value=process) as popen, \
         mock.patch.object(broker,'capture_bounded_process',side_effect=capture):
        broker.execute_pipeline(root,request,task,scope)
    command=popen.call_args.args[0]
    assert ('--qoder-author-fallback' in command) is expected
    assert ('--qoder-final-review' in command) is required


def test_enabled_author_policy_cannot_dispatch_without_independent_final_gate(broker_repo,monkeypatch):
    from scripts import two_site_provider_broker as broker
    from tests.test_two_site_provider_broker import make_valid_request
    from unittest import mock
    root,task,scope,environment=broker_repo
    save(root/author.AUTHOR_POLICY_REL,{'schema_version':1,'enabled':True,'model_role':'coordinator',
        'generation_variants_per_phase':1,'qualified_failures':['QUALIFIED_GENERATION_EXHAUSTION','QUALIFIED_PREGEN_PROFILE']})
    monkeypatch.delenv('TWO_SITE_QODER_FINAL_REVIEW_REQUIRED',raising=False)
    request=make_valid_request(root,task,environment)
    with mock.patch.object(broker.subprocess,'Popen') as popen,pytest.raises(broker.BrokerError,match='independent final'):
        broker.execute_pipeline(root,request,task,scope)
    popen.assert_not_called()


def test_new_task_binds_actual_author_policy_source_without_rewriting_old_identity(policy_repo):
    from tests.test_two_site_model_policy_task_identity import build
    root,git=policy_repo
    policy={'schema_version':1,'enabled':True,'model_role':'coordinator','generation_variants_per_phase':1,
            'qualified_failures':['QUALIFIED_GENERATION_EXHAUSTION','QUALIFIED_PREGEN_PROFILE']}
    save(root/author.AUTHOR_POLICY_REL,policy)
    git('add','--',author.AUTHOR_POLICY_REL.as_posix());git('commit','-m','fixture public author policy')
    path,first,sources,report=build(root,'author-first');original=path.read_bytes()
    assert first['context_files'].count(author.AUTHOR_POLICY_REL.as_posix())==1
    assert sources[author.AUTHOR_POLICY_REL.as_posix()]['sha256']==runner.file_sha256(root/author.AUTHOR_POLICY_REL)
    _,duplicate,_,same=build(root,'author-duplicate')
    assert same['semantic_task_fingerprint']==report['semantic_task_fingerprint']
    policy['enabled']=False;save(root/author.AUTHOR_POLICY_REL,policy)
    git('add','--',author.AUTHOR_POLICY_REL.as_posix());git('commit','-m','fixture explicit author policy change')
    _,changed,_,new=build(root,'author-changed')
    assert new['semantic_task_fingerprint']!=report['semantic_task_fingerprint']
    assert path.read_bytes()==original


def test_genuine_independent_gemini_completion_reader_supports217k_and41k_correction_snapshot(fx,maintenance_boundary):
    from tests.two_site_generation_fixture import seal_completion_fixture
    root,task,sources,*_=fx;auth,_=maintenance_boundary
    task=copy.deepcopy(task);sources=copy.deepcopy(sources)
    original='#'+('x'*217590)+'\nvalue = 1\n'
    regression='#'+('y'*41120)+'\ntest_value = 1\n'
    task['allowed_files']=['src.py','tests/test_regression.py']
    sources['src.py']={'content':original,'sha256':runner.sha(original.encode())}
    sources['tests/test_regression.py']={'content':regression,'sha256':runner.sha(regression.encode())}
    proposal={'status':'PROPOSAL_READY','summary':'Two actual repaired files','covered_requirements':[1],'findings':[],
        'changes':[{'path':path,'original_sha256':sources[path]['sha256'],'content':sources[path]['content'].replace(' = 1',' = 2')}
                   for path in task['allowed_files']]}
    directory=root/'.local/two-site-refactor/proposals/maint-fixture-reviewer-0'
    result={'status':'VERIFIED','summary':'Independent fixture review','covered_requirements':[1],'findings':[],'changes':[]}
    material={'source_head':HEAD,'task':task,'sources':sources,'maintenance_authorization':auth,
              'proposal':runner.serialize_json(proposal).decode()}
    assert 480000<len(runner.serialize_json(material))<960000
    runner._validate_task_snapshot_budget(material,root=root)
    correction=copy.deepcopy(material);correction['feedback']={'findings':['Keep both repairs']}
    runner._validate_task_snapshot_budget(correction,root=root)
    save(directory/'task.json',material)
    raw,loaded=runner.read_task_snapshot(root,directory/'task.json')
    assert loaded==material and len(raw)>480000
    assert runner.worker_result_limit(root,directory)==320000
    assert author._snapshot(root,directory/'task.json')==(raw,loaded)
    save(directory/'result.json',result)
    save(directory/'status.json',{'run_id':directory.name,'task_id':task['task_id'],'role':'verifier','head':HEAD,
        'state':'VERIFIED','conversation_id':'distinct-gemini-reviewer','model':fx[4]['model'],
        'result_sha256':runner.file_sha256(directory/'result.json')})
    seal_completion_fixture(root,directory)
    status=runner.unique_json((directory/'status.json').read_text())
    proof=runner.validate_generation_completion(root,directory,task=task,sources=sources,role='verifier',status=status,result=result)
    assert proof['role']=='verifier' and proof['outer_context_exited_normally'] is True


def test_author_complete_user_and_system_schema_share_one320k_budget(fx):
    root,task,sources,model,*_=fx
    directory=root/'.local/budget-check';directory.mkdir(parents=True)
    schema=runner.schema('worker',task,sources)
    with pytest.raises(driver.QoderError,match='COMPLETE_PROMPT_BOUND'):
        driver.qoder_command(root/'qoder-cli',root,directory,'x'*319999,model,
                             role='proposal_author',response_schema=schema)


def test_prepared_review_rejects_raw_hunks_that_do_not_materialize_canonical_sha(fx,maintenance_boundary):
    root,task,sources,*_=fx;auth,_=maintenance_boundary
    raw={'status':'PROPOSAL_READY','summary':'A change','covered_requirements':[1],'findings':[],
         'changes':[{'path':'src.py','original_sha256':sources['src.py']['sha256'],'content':'value = 2\n','replacements':[]}]}
    result=author.normalize_author_result(root,raw,task=task,sources=sources,source_head=HEAD,authorization=auth)
    tampered=copy.deepcopy(raw);tampered['changes'][0]['content']='value = 3\n'
    with pytest.raises(runner.ProposalError,match='does not materialize'):
        runner.build_prepared_review_transport(root,auth,result,task,sources,source_head=HEAD,
            proposal_sha256=runner.sha(runner.serialize_json(result)),raw_transport=tampered)


@pytest.mark.parametrize("maintenance", [False, True])
def test_actual_author_capture_on_synthetic_local_sdk_emitter_is_genuine_separate_proof(fx,monkeypatch,maintenance_boundary,maintenance):
    root,task,sources,model,_gemini,runtime,*_=fx
    failed(fx)
    auth=(maintenance_boundary[0] if maintenance else author.business_authorization(root,
        base_run_id='run-author-worker-0',task=task,sources=sources,source_head=HEAD))
    variant=('maint-'+('a'*32)+'-worker-0-qa' if maintenance else 'run-author-worker-0-qa')
    raw={'status':'PROPOSAL_READY','summary':'Complete synthetic implementation','covered_requirements':[1],
         'findings':[],'changes':[{'path':'src.py','original_sha256':sources['src.py']['sha256'],
                                  'content':'value = 2\n','replacements':[]}]}
    binary=root/'qoder-cli'
    script='''#!/usr/bin/python3
import json,sys
if '--version' in sys.argv: print('fixture-version')
elif 'status' in sys.argv: print(json.dumps({'logged_in':True}))
else:
 prompt=sys.stdin.read()
 assert 'COMPLETE_TASK_AND_SOURCE_JSON:' in prompt
 model=sys.argv[sys.argv.index('--model')+1]
 sid='synthetic-new-author-sdk-session'
 print(json.dumps({'type':'system','subtype':'init','session_id':sid,'model':model,'tools':[]}))
 print(json.dumps({'type':'result','subtype':'success','session_id':sid,'is_error':False,'stop_reason':'end_turn','num_turns':1,'result':json.dumps(RAW)}))
'''.replace('json.dumps(RAW)','json.dumps('+repr(raw)+')')
    save(binary,script.encode());binary.chmod(0o700)
    runtime['coordinator']['sha256']=runner.file_sha256(binary)
    save(root/'.local/two-site-refactor/ai-runtime.json',runtime)
    monkeypatch.setattr(runner,'git',lambda _root,*args:HEAD if args==('rev-parse','HEAD') else '')
    from scripts import two_site_coordinator_sandbox as containment
    monkeypatch.setattr(containment,'buildwrap_coordinator_command',lambda _root,_directory,command,**kwargs:command)
    monkeypatch.setenv('TWO_SITE_AUTOPILOT_RUN_ID',CONTROLLER)
    monkeypatch.delenv('TWO_SITE_MAINTENANCE_RUN_ID',raising=False)
    name,directory=author.run_prepared_author(root,variant,task=task,sources=sources,
        source_head=HEAD,authorization=auth,timeout=30)
    status=runner.unique_json((directory/'status.json').read_text());result=runner.unique_json((directory/'result.json').read_text())
    proof=runner.validate_generation_completion(root,directory,task=task,sources=sources,role='worker',status=status,result=result)
    assert name==variant and proof['author_provider']=='qoder'
    assert proof['session_id']=='synthetic-new-author-sdk-session'
    assert (directory/'raw-result.json').exists() and not (directory/'transport-completion.json').exists()
    assert runner.unique_json((directory/'capture.json').read_text())=={'exit_code':0,'normal_exit_observed':True,'owned_group_absent':True}
    assert (directory/'author-process.json').exists() is maintenance  # Root custody is maintenance-only.
    with pytest.raises(runner.ProposalError,match='RUN_EXISTS'):
        author.run_prepared_author(root,name,task=task,sources=sources,source_head=HEAD,authorization=auth,timeout=30)


from tests.test_two_site_qoder_author_resume import episode as interrupted_maintenance_episode


def test_maintenance_author_resume_uses_actual_stopped_custody_once_without_new_reservation(interrupted_maintenance_episode,monkeypatch):
    from tests.test_two_site_qoder_author_resume import stopped
    root,directory,process,values,material=interrupted_maintenance_episode
    stopped(process);calls=[]
    def dispatch(_root,run_id,**kwargs):
        calls.append((run_id,kwargs))
        out=directory.parent/run_id;out.mkdir(mode=0o700)
        return run_id,out
    monkeypatch.setattr(author,'run_prepared_author',dispatch)
    run_id,_=author.maintenance_author_phase(root,directory.name,task=material['task'],sources=material['sources'],
        source_head=values['source_head'],authorization=material['author_authorization'],timeout=30,state_check=lambda:None)
    assert run_id==directory.name+'-recovery-1' and len(calls)==1
    assert calls[0][1]['author_recovery']['origin_run_id']==directory.name
    assert calls[0][1]['authorization']==material['author_authorization']
    assert (directory/'author-recovery-1.json').exists() and not (directory/'result.json').exists()
    with pytest.raises(runner.ProposalError,match='EXHAUSTED_OR_UNKNOWN'):
        author.maintenance_author_phase(root,directory.name,task=material['task'],sources=material['sources'],
            source_head=values['source_head'],authorization=material['author_authorization'],timeout=30,state_check=lambda:None)
    assert len(calls)==1


def test_maintenance_live_original_author_never_dispatches_resume(interrupted_maintenance_episode,monkeypatch):
    from scripts import two_site_qoder_author_resume as recovery
    root,directory,_process,values,material=interrupted_maintenance_episode
    def forbidden(*args,**kwargs):raise AssertionError('live original must not get another author')
    monkeypatch.setattr(author,'run_prepared_author',forbidden)
    with pytest.raises(recovery.AuthorResumeError,match='LIVE_OR_REUSED'):
        author.maintenance_author_phase(root,directory.name,task=material['task'],sources=material['sources'],
            source_head=values['source_head'],authorization=material['author_authorization'],timeout=30,state_check=lambda:None)
    assert not (directory/'author-recovery-1.json').exists()


@pytest.mark.parametrize('fault',[None,'install','busy','interrupt','system_exit'])
def test_postcpp_lkg_refresh_never_replays_or_invalidates_accepted_checkpoint(tmp_path,monkeypatch,fault):
    import sys,types,fcntl
    from scripts import two_site_codex_autopilot as ap
    save(tmp_path/'docs/refactor/two-server/templates/automation-maintenance-policy.json',{'synthetic_fixture':True})
    module=types.ModuleType('scripts.two_site_qoder_maintenance');module.maintenance_policy_enabled=lambda _r:True
    monkeypatch.setitem(sys.modules,'scripts.two_site_qoder_maintenance',module)
    calls=[]
    def install(root):
        calls.append(root)
        if fault=='install':raise OSError('synthetic store full')
        if fault=='interrupt':raise KeyboardInterrupt()
        if fault=='system_exit':raise SystemExit(2)
        return {'head':HEAD,'manifest_sha256':'f'*64}
    lkg=types.ModuleType('scripts.two_site_maintenance_lkg');lkg.install=install
    monkeypatch.setitem(sys.modules,'scripts.two_site_maintenance_lkg',lkg)
    cp=tmp_path/'.local/two-site-refactor/autopilot/accepted_checkpoint.json';save(cp,{'accepted_before_refresh':'fixture'})
    before=cp.read_bytes()
    lock=None
    if fault=='busy':
        path=tmp_path/'.local/two-site-refactor/monitor/monitor.lock';path.parent.mkdir(parents=True)
        lock=os.open(path,os.O_WRONLY|os.O_CREAT,0o600);fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    try:report=ap._refresh_accepted_lkg(tmp_path)
    finally:
        if lock is not None:os.close(lock)
    assert report['acceptance']=='ACCEPTED_CPP_RETAINED' and cp.read_bytes()==before
    assert report['state']==('LKG_CURRENT' if fault is None else 'LKG_REFRESH_PENDING')
    assert len(calls)==(0 if fault=='busy' else 1)
    assert (tmp_path/'.local/two-site-refactor/autopilot/STOP').exists() is (fault in {'interrupt','system_exit'})


def test_legacy_postcpp_root_without_maintenance_policy_never_touches_host_store(tmp_path,monkeypatch):
    from scripts import two_site_codex_autopilot as ap
    assert ap._refresh_accepted_lkg(tmp_path)=={'state':'NOT_CONFIGURED'}
    assert not (tmp_path/'.local').exists()


def test_run_cycle_invokes_lkg_after_real_cpp_publication_and_retains_continue_on_pending(tmp_path,monkeypatch):
    from scripts import two_site_codex_autopilot as ap
    from tests.test_two_site_autopilot_checkpoint import (PostValidationCheckpointPublicationCallTests,
        _make_repository,_make_continue_receipt)
    helper=PostValidationCheckpointPublicationCallTests()
    root,cli,state_root=helper._setup_cycle_env(tmp_path)
    prior=_make_repository(head='b'*40);after=_make_repository(head='a'*40)
    receipt=_make_continue_receipt(head_after='a'*40,head_before='b'*40)
    calls=[];published=[]
    report={'state':'LKG_REFRESH_PENDING','acceptance':'ACCEPTED_CPP_RETAINED','error_type':'OSError'}
    def refresh(actual_root):
        assert actual_root==root
        checkpoint=state_root/ap.ACCEPTED_CHECKPOINT_FILENAME
        assert checkpoint.exists()
        assert ap._load_accepted_checkpoint(state_root)['repository']['head']=='a'*40
        published.append(checkpoint.read_bytes())
        return report
    monkeypatch.setattr(ap,'_refresh_accepted_lkg',refresh)
    directory,result=helper._execute_run_cycle(root,cli,state_root,
        codex_result={k:receipt[k] for k in ap.RESULT_KEYS},transition_result=receipt,
        repo_snapshots=[prior,after],call_events=calls,on_publish=ap.publish_accepted_checkpoint)
    assert result['state']=='CONTINUE' and len(published)==1
    assert (state_root/ap.ACCEPTED_CHECKPOINT_FILENAME).read_bytes()==published[0]
    assert runner.unique_json((directory/'status.json').read_text())['lkg_refresh']==report


def test_postacceptance_stop_preserves_existing_marker_without_replacement(tmp_path):
    from scripts import two_site_codex_autopilot as ap
    marker=tmp_path/'.local/two-site-refactor/autopilot/STOP';save(marker,b'operator-owned original stop\n')
    original=marker.stat();ap._publish_postacceptance_operator_stop(tmp_path)
    assert marker.read_bytes()==b'operator-owned original stop\n'
    assert marker.stat().st_ino==original.st_ino


def test_postacceptance_stop_publication_failure_terminates_instead_of_continuing(tmp_path,monkeypatch):
    from scripts import two_site_codex_autopilot as ap
    directory=tmp_path/'.local/two-site-refactor/autopilot';directory.mkdir(parents=True)
    original=ap.os.open
    def fail_stop(path,*args,**kwargs):
        if str(path).endswith('/STOP'):raise OSError('synthetic full store')
        return original(path,*args,**kwargs)
    monkeypatch.setattr(ap.os,'open',fail_stop)
    with pytest.raises(SystemExit,match='ACCEPTED_CPP_RETAINED'):
        ap._publish_postacceptance_operator_stop(tmp_path)


def test_postaccepted_interrupt_stops_loop_before_any_second_unit_or_new_epoch(tmp_path,monkeypatch):
    from scripts import two_site_codex_autopilot as ap
    from tests.test_two_site_autopilot_checkpoint import _make_repository,_make_continue_receipt
    state=tmp_path/'.local/two-site-refactor/autopilot';state.mkdir(mode=0o700,parents=True)
    before=_make_repository(head='a'*40);after=_make_repository(head='b'*40)
    prior=_make_continue_receipt(head_after='a'*40,head_before='c'*40)
    ap.publish_accepted_checkpoint(state,prior,before)
    accepted=_make_continue_receipt(head_after='b'*40,head_before='a'*40)
    calls=[];observed=[];current=[before]
    monkeypatch.setattr(ap,'validate_repository',lambda *_a,**_k:current[0])
    monkeypatch.setattr(ap,'git',lambda *_a:'a'*40)
    monkeypatch.setattr(ap,'ref_snapshot',lambda *_a:before['protected_refs'])
    monkeypatch.setattr(ap,'write_supervisor_state',lambda _r,status,**kwargs:observed.append(status))
    def one_cycle(root,cli,state_root,cycle,timeout,**kwargs):
        kwargs['before_model'](before)
        calls.append(cycle);current[0]=after
        ap.publish_accepted_checkpoint(state_root,accepted,after)
        ap._publish_postacceptance_operator_stop(root)
        return root/'accepted-run',dict(accepted,operator_stop_requested=True)
    monkeypatch.setattr(ap,'run_cycle',one_cycle)
    def no_prune(*_a,**_kw):raise AssertionError('must stop before continuation preparation')
    monkeypatch.setattr(ap,'prune_runs',no_prune)
    assert ap.run_loop(tmp_path,tmp_path/'unused-cli',max_cycles=5,timeout=30,once=False)==0
    assert calls==[1] and observed[-1]=='STOPPED'
    assert ap._load_accepted_checkpoint(state)['repository']['head']=='b'*40


@pytest.mark.parametrize('authority',['absent','null','spoofed','valid'])
def test_snapshot960k_is_authenticated_and_business480k_remains_strict(fx,maintenance_boundary,authority):
    root,task,sources,*_=fx;auth,_=maintenance_boundary
    material={'source_head':HEAD,'task':task,'sources':sources,'retained_metadata':'é'*250000}
    if authority=='null':material['maintenance_authorization']=None
    elif authority=='spoofed':material['maintenance_authorization']={'kind':author.MAINTENANCE,'job_id':'unknown','sha256':'0'*64}
    elif authority=='valid':material['maintenance_authorization']=auth
    path=root/'retained/task.json';save(path,material)
    if authority=='valid':
        assert runner.read_task_snapshot(root,path)[1]==material
        # A field cannot activate the larger writer allowance without root validation.
        with pytest.raises(runner.ProposalError,match='context budget'):
            runner._validate_task_snapshot_budget(material)
        material['retained_metadata']='é'*480001
        save(path,material)
        with pytest.raises(runner.ProposalError,match='context budget'):
            runner.read_task_snapshot(root,path)
    else:
        with pytest.raises((runner.ProposalError,AssertionError)):
            runner.read_task_snapshot(root,path)


def test_run_cycle_stop_publication_exit_retains_real_continue_checkpoint(tmp_path,monkeypatch):
    from scripts import two_site_codex_autopilot as ap
    from tests.test_two_site_autopilot_checkpoint import (PostValidationCheckpointPublicationCallTests,
        _make_repository,_make_continue_receipt)
    helper=PostValidationCheckpointPublicationCallTests();root,cli,state_root=helper._setup_cycle_env(tmp_path)
    prior=_make_repository(head='b'*40);after=_make_repository(head='a'*40)
    receipt=_make_continue_receipt(head_after='a'*40,head_before='b'*40)
    def stop_failed(_root):raise SystemExit('ACCEPTED_CPP_RETAINED: synthetic stop publication failure')
    monkeypatch.setattr(ap,'_refresh_accepted_lkg',stop_failed)
    with pytest.raises(SystemExit,match='ACCEPTED_CPP_RETAINED'):
        helper._execute_run_cycle(root,cli,state_root,
            codex_result={k:receipt[k] for k in ap.RESULT_KEYS},transition_result=receipt,
            repo_snapshots=[prior,after],call_events=[],on_publish=ap.publish_accepted_checkpoint)
    assert ap._load_accepted_checkpoint(state_root)['repository']['head']=='a'*40
    directories=list((state_root/'runs').iterdir());assert len(directories)==1
    status=runner.unique_json((directories[0]/'status.json').read_text())
    assert status['state']=='CONTINUE' and status['lkg_refresh']['acceptance']=='ACCEPTED_CPP_RETAINED'
    assert status['lkg_refresh']['operator_stop_requested'] is True
