"""Real disposable Git/Scope/CPP/request and synthetic SDK proofs; no models.

All acceptance metadata below is explicitly an offline test fixture. Actual
production proof readers run without authorization, repository, Source, native
receipt, Gemini proof or Qoder final receipt mocks.
"""
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import signal
import time

import pytest
import yaml
from scripts import two_site_qoder_business_reconcile as rec
from scripts import two_site_codex_autopilot as ap
from scripts import two_site_proposal_runner as runner
from scripts import two_site_provider_broker as broker
from scripts import two_site_cycle_tools as ct
from scripts import two_site_qoder_review as final
from scripts import two_site_ai_config as ai
from scripts.two_site_preparation_contract import PreparationStartup
from scripts.two_site_semantic_attempts import reserve_semantic_attempt
from tests.two_site_generation_fixture import seal_completion_fixture


def put(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_bytes(value if type(value)is bytes else runner.serialize_json(value))
    path.chmod(0o600)


@pytest.fixture
def episode(tmp_path,monkeypatch):
    root=tmp_path.resolve()
    def git(*args):
        return subprocess.check_output(['/usr/bin/git','-C',str(root),*args],stderr=subprocess.DEVNULL).decode().strip()
    git('init','-q');git('config','user.email','offline@example.invalid');git('config','user.name','Offline fixture')
    git('branch','-M',ap.BRANCH)
    (root/'.gitignore').write_text('.local/\n')
    source=Path(runner.__file__).resolve().parents[1]
    for name in set(ap.CONTROL_PATHS)|set(runner.GEMINI_RUNTIME_REFERENCE_PATHS):
        path=root/name;path.parent.mkdir(parents=True,exist_ok=True)
        original=source/name
        # A public native snapshot can omit hidden instructional controls.
        path.write_bytes(original.read_bytes() if original.is_file() else b'automation-models.json\n')
    (root/'docs/refactor/two-server/execution-order.yaml').write_text(yaml.safe_dump({'stages':{'P1-00b':{'state':'IN_PROGRESS'}}}))
    (root/'gap.md').write_text('# Exact offline gap\nOne first exception boundary.\n')
    (root/'tests').mkdir(exist_ok=True)
    (root/'tests/test_p1_00b_existing_fixture.py').write_text('def test_original():\n    assert True\n')
    git('add','.');git('commit','-qm','Public offline Source fixture')
    head=git('rev-parse','HEAD')
    for ref in ap.PROTECTED_REFS:git('update-ref',ref,head)
    shared=root/'.local/two-site-refactor';shared.mkdir(parents=True,exist_ok=True)
    aid='WA-RECONCILIATION-OFFLINE-FIXTURE'
    (shared/'worktrees.yaml').write_text(yaml.safe_dump({'integration':{'path':str(root),'branch':ap.BRANCH,'active_assignment_id':aid}}))
    assignment={'id':aid,'status':'READY','role':'WORKER','integration_branch':ap.BRANCH,'external_access':'NONE',
        'expires_at':'2099-01-01T00:00:00Z','stage_id':'P1-00b','allowed_paths':['tests/test_p1_00b_*.py'],'forbidden_paths':['scripts/**']}
    put(shared/'assignments'/(aid+'.md'),('# Offline test fixture\n```yaml\n'+yaml.safe_dump(assignment)+'```\n').encode())
    before=ap.validate_repository(root,require_clean=True);normalized=ap.validate_accepted_repository(before)
    state=root/rec.STATE;state.mkdir(mode=0o700)
    accepted={'schema_version':'1.0','state':'CONTINUE','disposition':'NONE','category':'NONE',
        'summary':'Offline fixture checkpoint, never project acceptance.','commit':head,'next_action':'Exercise exact offline effects.',
        'blockers':[],'gemini_worker_run_id':'prior-offline-worker','gemini_reviewer_run_id':'prior-offline-reviewer','proposal_sha256':'d'*64,
        'head_before':'0'*40,'head_after':head,'head_changed':True}
    ap._persist_accepted_checkpoint(state,accepted,before)
    original_cp=(state/ap.ACCEPTED_CHECKPOINT_FILENAME).read_bytes()
    cli=root/'.local/fake-qodercli'
    script="""#!/usr/bin/python3
import json,sys
if '--version' in sys.argv:print('offline-1')
elif 'status' in sys.argv:print(json.dumps({'logged_in':True}))
else:
 data=json.loads(sys.stdin.read().rsplit('\\n',1)[1])
 model=sys.argv[sys.argv.index('--model')+1]
 result={'decision':'APPROVE','findings':[],'proposal_sha256':data['proposal_sha256'],'source_head':data['source_head']}
 sid='offline-independent-final-sdk'
 print(json.dumps({'type':'system','subtype':'init','session_id':sid,'model':model,'tools':[],'mcp_servers':[],'plugins':[]}))
 print(json.dumps({'type':'result','subtype':'success','is_error':False,'stop_reason':'end_turn','session_id':sid,'num_turns':1,'result':json.dumps(result)}))
"""
    put(cli,script.encode());cli.chmod(0o700)
    runtime={'schema_version':1,'coordinator':{'path':str(cli),'version':'offline-1','sha256':runner.file_sha256(cli)}}
    put(root/ai.AI_RUNTIME_REL,runtime)
    allowed='tests/test_p1_00b_reviewed_fixture.py'
    spec={'task':{'task_id':'offline-exact-business-effects','goal':aid+': add one complete offline boundary.',
        'context_files':['gap.md','tests/test_p1_00b_existing_fixture.py'],'allowed_files':[allowed],
        'requirements':['Assert one complete boundary.'],'test_selectors':[allowed+'::test_boundary']},
        'gap_reference':'gap.md#exact-offline-gap','scenario_key':'offline-exact-effect','snippets':[]}
    spec_path=shared/'offline-spec.json';put(spec_path,spec)
    task_path=shared/'tasks/offline-exact.json';ct.build(root,spec_path,task_path)
    task,sources=runner.load_task(root,task_path)
    prefix='offline-reconciliation-business';pipeline=shared/'pipeline'/prefix;pipeline.mkdir(mode=0o700,parents=True)
    semantic=reserve_semantic_attempt(root,shared/'pipeline',task,task['task_id'],prefix)
    worker=prefix+'-worker-0';reviewer=prefix+'-reviewer-0';wd=shared/'proposals'/worker;vd=shared/'proposals'/reviewer
    proposal={'status':'PROPOSAL_READY','summary':'Explicit synthetic business fixture.','covered_requirements':[1],'findings':[],
        'changes':[{'path':allowed,'original_sha256':None,'content':'def test_boundary():\n    assert 2 + 2 == 4\n'}]}
    put(wd/'result.json',proposal);digest=runner.file_sha256(wd/'result.json');worker_text=(wd/'result.json').read_text()
    snapshot={'task':task,'sources':sources,'source_head':head};put(wd/'task.json',snapshot)
    roles=runner.configured_gemini_roles(root)
    put(wd/'status.json',{'run_id':worker,'task_id':task['task_id'],'role':'worker','head':head,'state':'PROPOSAL_READY',
        'model':roles['worker']['model'],'conversation_id':'offline-worker-sdk','result_sha256':digest})
    seal_completion_fixture(root,wd)
    native={'state':'PASSED','proposal_sha256':digest,'selectors':task['test_selectors'],'unsupported_specs':[],
        'returncode':0,'stdout_tail':'OFFLINE SYNTHETIC native receipt fixture','stderr_tail':'','snapshot_files':1,'snapshot_bytes':100,
        'isolation':'bwrap:ro-source,empty-home,clearenv,unshare-all'}
    native_path=pipeline/'pre-review-tests-0.json';put(native_path,native)
    native_sha=runner.file_sha256(native_path)
    put(vd/'result.json',{'status':'VERIFIED','summary':'Explicit synthetic independent review.','covered_requirements':[1],'findings':[],'changes':[]})
    put(vd/'task.json',dict(snapshot,proposal=worker_text,test_evidence=native))
    put(vd/'status.json',{'run_id':reviewer,'task_id':task['task_id'],'role':'verifier','head':head,'state':'VERIFIED',
        'model':roles['reviewer']['model'],'conversation_id':'offline-gemini-reviewer-sdk','result_sha256':runner.file_sha256(vd/'result.json'),
        'input_worker_run_id':worker,'input_worker_conversation_id':'offline-worker-sdk','input_proposal_sha256':digest,
        'input_test_evidence_sha256':runner.sha(runner.serialize_json(native))})
    seal_completion_fixture(root,vd)
    monkeypatch.setenv('TWO_SITE_COORDINATOR_SANDBOX','1')  # Explicit synthetic binary, not a paid model.
    receipt=final.review_proposal(root,task_path,worker,reviewer,digest,timeout=30)
    cycle='cycle-0001-20261104T000000Z-123';run=state/'runs'/cycle;run.mkdir(mode=0o700,parents=True);run.parent.chmod(0o700)
    startup=PreparationStartup(1,'one_shot',cycle,head,before['assignment_scope'].sha256)
    put(run/'preparation-startup.json',startup.to_mapping())
    request={'schema_version':'1.0','request_id':'a'*32,'task_path':str(task_path.relative_to(root)),
        'task_sha256':runner.file_sha256(task_path),'run_prefix':prefix,'timeout':900,'source_head':head,
        'assignment_id':aid,'assignment_sha256':before['assignment_scope'].sha256,'controller_run_id':cycle,'created_at':time.time()}
    broker.publish_cycle_reservation(root,request)
    put(run/broker._CYCLE_DISPATCH,{'schema_version':'1.0','request':request,'broker_pid':os.getpid(),'broker_started_at':time.time()})
    ready={'state':'READY_FOR_CODEX','task_id':task['task_id'],'source_head':head,'final_worker_run_id':worker,'final_reviewer_run_id':reviewer,
        'proposal_sha256':digest,'changed_paths':[allowed],'proposal_path':str((wd/'result.json').relative_to(root)),
        'reviewer_path':str((vd/'result.json').relative_to(root)),'correction_count':0,'blockers':[],
        'pre_review_test_path':str(native_path.relative_to(root)),'pre_review_test_sha256':native_sha,'pre_review_test_state':'PASSED',
        'semantic_task_fingerprint':semantic['fingerprint']}
    response={'schema_version':'1.0','request_id':request['request_id'],'run_prefix':prefix,'source_head':head,
        'state':'COMPLETED','exit_code':0,'stdout':json.dumps(ready),'stderr':'','finished_at':time.time()}
    _b,_s,_r,_c,responses=broker.broker_paths(root)
    put(responses/(request['request_id']+'.json'),response)
    put(run/'broker-terminal-response.json',{'schema_version':'1.0','request':request,'response':response})
    role=ai.role_settings(root,'coordinator')
    dead=subprocess.Popen(['/usr/bin/true']);dead.wait()
    identity={**runtime['coordinator'],'provider':'qoder','logged_in':True,'auto_update_enabled':False,
              'model':role['model'],'reasoning':role['reasoning_effort']}
    put(run/'status.json',{'state':'FAILED','error':rec.KNOWN_FAILURE,'coordinator_provider':'qoder','pid':dead.pid,
        'head_before':head,'protected_refs':normalized['protected_refs'],
        'assignment_scope':{k:normalized['assignment_scope'][k] for k in ('assignment_id','stage_id','sha256')},
        'model_config':ai.load_ai_config(root),'coordinator':identity})
    sdk=[{'type':'system','subtype':'init','session_id':'offline-failed-coordinator-sdk','model':role['model'],'tools':[]},
         {'type':'result','subtype':'success','session_id':'offline-failed-coordinator-sdk','stop_reason':'end_turn',
          'num_turns':1,'is_error':False,'result':'Offline coordinator fixture stopped after exact staging.'}]
    put(run/'stream.jsonl',('\n'.join(json.dumps(x)for x in sdk)+'\n').encode());put(run/'stderr.txt',b'')
    put(shared/broker.STATE_REL.relative_to('.local/two-site-refactor')/'status.json',{'state':'IDLE','qoder_final_review_required':True})
    put(state/'status.json',{'state':'FAILED','evidence':str(run)})
    put(root/allowed,proposal['changes'][0]['content'].encode());(root/allowed).chmod(0o644);git('add','--',allowed)
    return {'root':root,'git':git,'head':head,'run':run,'allowed':allowed,'proposal':proposal,'task':task,'task_path':task_path,
        'request':request,'worker':wd,'reviewer':vd,'receipt':receipt,'checkpoint':original_cp,'before':normalized}


def test_real_chain_exact_staged_effects_get_one_zero_credit_transition_and_no_checkpoint_write(episode):
    e=episode;root=e['root'];job=rec.classify_business_reconciliation(root,e['run'])
    assert job['business_credit']==job['clean_credit']==0
    auth=rec.prepare_business_reconciliation(root,e['run']);execution=rec.finish_business_reconciliation(root,auth)
    assert execution['head_before']==e['head'] and execution['head_after']!=e['head']
    assert e['git']('rev-list','--count',e['head']+'..HEAD')=='1'
    assert execution['normal_checkpoint_written'] is False and execution['business_credit']==execution['clean_credit']==0
    assert (root/rec.STATE/ap.ACCEPTED_CHECKPOINT_FILENAME).read_bytes()==e['checkpoint']
    assert e['git']('status','--porcelain')==''
    before=[p.read_bytes()for p in (e['run']/'status.json',e['worker']/'result.json',e['reviewer']/'result.json')]
    again=rec.finish_business_reconciliation(root,auth);assert again==execution
    assert [p.read_bytes()for p in (e['run']/'status.json',e['worker']/'result.json',e['reviewer']/'result.json')]==before
    record=rec.publish_business_reconciliation_transition_locked(root,root/rec.JOBS/auth['job_id']/'execution.json')
    assert rec.read_business_reconciliation_transition_locked(root,root/rec.STATE)==record
    assert e['git']('rev-list','--count',e['head']+'..HEAD')=='1'


@pytest.mark.parametrize('fault',['foreign','content','mode','index','control','ref','config','scope','stop','live','proof'])
def test_actual_same_source_counterpart_rejects_every_foreign_or_unproven_effect(episode,fault):
    e=episode;root=e['root']
    if fault=='foreign':put(root/'foreign.txt',b'untouched user work\n')
    elif fault=='content':(root/e['allowed']).write_text('def test_boundary(): assert False\n')
    elif fault=='mode':(root/e['allowed']).chmod(0o755)
    elif fault=='index':e['git']('restore','--staged','--source=HEAD','--',e['allowed'])
    elif fault=='control':(root/ap.PROMPT_REL).write_bytes((root/ap.PROMPT_REL).read_bytes()+b'\n')
    elif fault=='ref':e['git']('update-ref',ap.PROTECTED_REFS[0],e['git']('commit-tree',e['git']('rev-parse','HEAD^{tree}'),'-p',e['head'],'-m','Offline reference drift fixture'))
    elif fault=='config':(root/ai.AI_CONFIG_REL).write_bytes((root/ai.AI_CONFIG_REL).read_bytes()+b'\n')
    elif fault=='scope':
        path=root/'.local/two-site-refactor/assignments'/('WA-RECONCILIATION-OFFLINE-FIXTURE.md')
        path.write_bytes(path.read_bytes()+b'\n')
    elif fault=='stop':put(root/'STOP',b'')
    elif fault=='live':
        path=e['run']/'status.json';value=json.loads(path.read_text());value['pid']=os.getpid();put(path,value)
    else:(e['reviewer']/'stream.jsonl').write_bytes((e['reviewer']/'stream.jsonl').read_bytes()+b'\n')
    with pytest.raises(Exception):rec.classify_business_reconciliation(root,e['run'])
    assert e['git']('rev-parse','HEAD')==e['head']
    assert (root/rec.STATE/ap.ACCEPTED_CHECKPOINT_FILENAME).read_bytes()==e['checkpoint']


@pytest.mark.parametrize('phase',['unstage','restage','commit'])
def test_real_index_and_commit_crash_reconciliation_never_replays_author_or_commit(episode,monkeypatch,phase):
    e=episode;root=e['root'];auth=rec.prepare_business_reconciliation(root,e['run'])
    original=rec.git;once=[]
    def injected(root,*args,**kwargs):
        result=original(root,*args,**kwargs)
        operation={'unstage':'restore','restage':'add','commit':'commit'}[phase]
        if args[0]==operation and not once:once.append(True);raise InterruptedError('Offline after exact Git effect')
        return result
    monkeypatch.setattr(rec,'git',injected)
    with pytest.raises(InterruptedError):rec.finish_business_reconciliation(root,auth)
    monkeypatch.setattr(rec,'git',original)
    execution=rec.finish_business_reconciliation(root,auth)
    assert execution['head_after']==e['git']('rev-parse','HEAD')
    assert e['git']('rev-list','--count',e['head']+'..HEAD')=='1'
    assert (root/rec.STATE/ap.ACCEPTED_CHECKPOINT_FILENAME).read_bytes()==e['checkpoint']


@pytest.mark.parametrize('first_fails',[False,True])
def test_host_releases_ap_but_holds_mon_for_real_transition_bound_ordinary_handoff(episode,monkeypatch,first_fails):
    import fcntl
    from types import SimpleNamespace
    from scripts import two_site_agent_sandbox as sandbox
    e=episode;root=e['root'];original=sandbox._run_bounded_process;calls=[]
    def observed(command,**kwargs):
        if command[:1]!=['/usr/bin/bash']:return original(command,**kwargs)
        assert command==['/usr/bin/bash',str(root/'scripts/two_site_autopilot_tmux.sh'),'start','--quota-wait-enabled']
        assert rec.read_business_reconciliation_transition_locked(root,root/rec.STATE) is not None
        descriptor=os.open(root/rec.STATE/'autopilot.lock',os.O_WRONLY)
        try:fcntl.flock(descriptor,fcntl.LOCK_EX|fcntl.LOCK_NB)
        finally:os.close(descriptor)
        descriptor=os.open(root/'.local/two-site-refactor/monitor/monitor.lock',os.O_WRONLY)
        try:
            with pytest.raises(BlockingIOError):fcntl.flock(descriptor,fcntl.LOCK_EX|fcntl.LOCK_NB)
        finally:os.close(descriptor)
        calls.append(command)
        return SimpleNamespace(returncode=1 if first_fails and len(calls)==1 else 0,stdout=b'',stderr=b'')
    monkeypatch.setattr(sandbox,'_run_bounded_process',observed)
    if first_fails:
        with pytest.raises(rec.BusinessReconciliationError,match='ORDINARY_HANDOFF_FAILED'):
            rec.recover_business_reconciliation(root)
        assert e['git']('rev-list','--count',e['head']+'..HEAD')=='1'
    result=rec.recover_business_reconciliation(root)
    assert result['ordinary_handoff']=={'state':'LAUNCH_ACCEPTED','returncode':0}
    assert len(calls)==(2 if first_fails else 1)
    assert e['git']('rev-list','--count',e['head']+'..HEAD')=='1'
    assert (root/rec.STATE/ap.ACCEPTED_CHECKPOINT_FILENAME).read_bytes()==e['checkpoint']


def test_unrelated_terminal_never_enters_known_effect_profile(tmp_path):
    run=tmp_path/rec.STATE/'runs/cycle-0001-20261104T000000Z-123';put(run/'status.json',{'state':'FAILED','error':'Unknown user failure'});run.chmod(0o700)
    assert rec.classify_business_reconciliation(tmp_path,run) is None
    assert not (tmp_path/rec.JOBS).exists()


def test_dead_coordinator_parent_with_live_owned_descendant_is_not_quiescent(tmp_path):
    child_file=tmp_path/'child.pid'
    code=("import pathlib,subprocess,sys,time; "
          "p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']); "
          "pathlib.Path(sys.argv[1]).write_text(str(p.pid)); time.sleep(.2)")
    parent=subprocess.Popen(['/usr/bin/python3','-c',code,str(child_file)],start_new_session=True)
    try:
        parent.wait(timeout=5)
        assert child_file.exists()
        with pytest.raises(rec.BusinessReconciliationError,match='PROCESS_GROUP_LIVE'):
            rec._dead_original({'pid':parent.pid})
    finally:
        try:os.killpg(parent.pid,signal.SIGKILL)
        except ProcessLookupError:pass


@pytest.mark.parametrize('raw',[b'{"x":1,"x":2}',b'{"x":NaN}',b'[]'])
def test_duplicate_nonfinite_or_nonobject_journal_is_never_authority(raw):
    with pytest.raises(ValueError):rec.decode(raw)
