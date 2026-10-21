"""Offline real-file finalization proof, no runtime wake.

Quota/model outcomes and collected external-test outcomes are explicitly synthetic.
Root separately supplies actual unpatched SDK to the same final factory.
Source/Scope/Actor/Git/transition/lineage/ACK validators are unpatched.
Not live Broker/history/C5e proof.
"""
from __future__ import annotations
import os
os.environ['APP_ENV_FILE'] ='config/unit-test.env.example'
from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence
install_p1_00_network_fence()
import ast
from datetime import datetime,timedelta
import hashlib
import inspect
import json
from pathlib import Path
import stat
import subprocess
from typing import Any,Callable,Mapping
import pytest
from scripts.two_site_codex_autopilot import BRANCH,GEMINI_MODEL,AutopilotError,exclusive_supervisor_lock,validate_repository,validate_result,validate_scoped_committed_proposal,validate_scoped_gemini_chain,validate_transition
from scripts.two_site_pre_review_receipt import EXPECTED_RECEIPT_KEYS,validate_test_receipt
from scripts.two_site_proposal_pipeline import expected_role_model
from scripts.two_site_proposal_runner import load_task
from scripts.two_site_quota_accepted_lineage_storage import read_accepted_lineage_record_candidate
from scripts.two_site_quota_continuation_response import build_continuation_broker_response
from scripts.two_site_quota_continuation_terminal_storage import publish_terminal_continuation_record
from scripts.two_site_quota_final_acceptance_lineage import collect_final_acceptance_upstream_lineage_from_snapshot_locked
from scripts.two_site_quota_final_ack_storage import read_final_ack_record_candidate
from scripts.two_site_quota_supervisor_finalization import finalize_accepted_quota_continuation_locked
from scripts.two_site_quota_supervisor_resume_context import collect_known_reset_resume_context_locked
from scripts.two_site_quota_resume_claim_request import compose_and_publish_known_reset_resume_request_locked
from scripts.two_site_quota_resume_lineage_snapshot_storage import publish_expected_lineage_snapshot
from scripts.two_site_quota_resume_submission_intent import publish_quota_resume_submission_intent
from scripts.two_site_quota_wait_admission import admit_initial_quota_wait_locked
from tests.test_two_site_provider_broker import broker_repo
from tests.test_two_site_provider_broker_cycle_reservation_read import _setup_cycle_root,_write_startup_json
from tests.test_two_site_quota_real_source_join_fileproof import _setup_scenario_through_receipt
from tests.test_two_site_quota_full_tree_repository_facts import SOURCE_ROOT
from tests.two_site_generation_fixture import seal_completion_fixture
_SIMULATED_ISOLATION='SIMULATED_EXTERNAL_TEST_OUTCOME_NOT_SANDBOX_PROOF'

def _w0600(path:Path,data:str|bytes) ->bytes:
 b=data.encode('utf-8')if isinstance(data,str)else data
 path.write_bytes(b)
 os.chmod(path,384)
 return b

def _cj(obj:Any) ->str:
 return json.dumps(obj,sort_keys=True,separators=(',',':'))

def _git(root:Path, *args:str) ->str:
 return subprocess.check_output(['git','-C',str(root), *args],text=True).strip()

def _custody(root:Path) ->dict[str,tuple[bytes,int,int,int]]:
 import stat
 files=sorted((root/'.local/two-site-refactor').rglob('*.json'))
 assert len(files) <=128
 result= {}
 for path in files:
  st=path.lstat()
  assert stat.S_ISREG(st.st_mode)and st.st_nlink==1
  assert st.st_size<=2280384
  result[str(path.relative_to(root))] = (path.read_bytes(),st.st_ino,st.st_mode,st.st_nlink)
 return result

def simulated_candidate_runner(root:Path,task:Mapping[str,Any],worker_result:Mapping[str,Any],proposal_sha256:str,timeout:int=1200) ->dict[str,Any]:
 receipt= {'state':'PASSED','proposal_sha256':proposal_sha256,'selectors':list(task['test_selectors']),'unsupported_specs': [],'returncode':0,'stdout_tail':'simulated test passed\n','stderr_tail':'','snapshot_files':1,'snapshot_bytes':100,'isolation':_SIMULATED_ISOLATION}
 assert set(receipt.keys()) ==EXPECTED_RECEIPT_KEYS
 return receipt

def _build_real_finalized_history_fixture(broker_repo_tuple:tuple[Path,Path,Any,dict[str,str]], *,candidate_runner:Callable[...,dict[str,Any]]) ->dict[str,Any]:
 if candidate_runner is None or not callable(candidate_runner):
  raise TypeError('candidate_runner must be an explicit callable')
 root,task_path,dummy_scope,env=broker_repo_tuple
 support= ('config/unit-test.env.example','scripts/two_site_pre_review_pytest_gate.py')
 for rel in support:
  target=root/rel
  target.parent.mkdir(parents=True,exist_ok=True)
  target.write_bytes((SOURCE_ROOT/rel).read_bytes())
 _git(root,'add', *support)
 _git(root,'commit','-qm','fixture: track canonical sandbox support')
 sc=_setup_scenario_through_receipt(root,task_path,dummy_scope,env)
 scope,facts= (sc['actual_scope'],sc['repo_facts'])
 orig_cid,cont_cid= (sc['original_ctrl_id'],sc['continuation_ctrl_id'])
 ap_dir=sc['autopilot_dir']
 with exclusive_supervisor_lock(ap_dir/'autopilot.lock'):
  wait=admit_initial_quota_wait_locked(root,orig_cid,clock=lambda:sc['now_utc'] -timedelta(seconds=120))
  next_check=datetime.fromisoformat(wait['schedule']['next_check_at_utc'].replace('Z','+00:00'))
  claim_dt=next_check+timedelta(seconds=10)
  claim_utc_str=claim_dt.strftime('%Y-%m-%dT%H:%M:%SZ')
  resp_dt=claim_dt+timedelta(seconds=1)
  ack_utc_str= (resp_dt+timedelta(seconds=1)).strftime('%Y-%m-%dT%H:%M:%SZ')
  ctx=collect_known_reset_resume_context_locked(root,orig_cid,claim_utc_str)
  resume_req_id=hashlib.sha256(cont_cid.encode('utf-8')).hexdigest()[:32]
  sub=compose_and_publish_known_reset_resume_request_locked(root,orig_cid,ctx['wait_record'],ctx['expected_wait'],ctx['raw_wait_bytes'],ctx['semantic_attempt'],ctx['expected_task_id'],cont_cid,resume_req_id,claim_utc_str,300,claim_dt.timestamp())
  publish_expected_lineage_snapshot(root,orig_cid,ctx['wait_record'],ctx['expected_wait'],sub['claim'],sub['expected_claim'],sub['request'])
  publish_quota_resume_submission_intent(root,sub['request'],now_epoch=claim_dt.timestamp())
 resume_req=sub['request']
 _,cont_runs=_setup_cycle_root(str(root),cont_cid)
 _write_startup_json(cont_runs,cycle_id=cont_cid,source_head=sc['head'],assignment_sha256=scope.sha256)
 t_data,sources=load_task(root,task_path)
 props=root/'.local/two-site-refactor/proposals'
 w_run,r_run= (f'{resume_req["run_prefix"]}-worker-0',f'{resume_req["run_prefix"]}-reviewer-0')
 w_dir,r_dir= (props/w_run,props/r_run)
 for d in(w_dir,r_dir):
  d.mkdir(parents=True,exist_ok=True)
  os.chmod(d,448)
 t_rel,t_content= ('tests/test_new.py','from source import VALUE\n\ndef test_source_value():\n    assert VALUE == 1\n')
 w_result= {'status':'PROPOSAL_READY','summary':'Characterize source.VALUE in test_new.py','covered_requirements': [1],'findings': [],'changes': [{'path':t_rel,'original_sha256':None,'content':t_content}]}
 w_txt=_cj(w_result)
 w_res_path=w_dir/'result.json'
 _w0600(w_res_path,w_txt)
 prop_sha=hashlib.sha256(w_txt.encode('utf-8')).hexdigest()
 _w0600(w_dir/'task.json',_cj({'source_head':sc['head'],'task':t_data,'sources':sources}))
 w_stat= {'run_id':w_run,'role':'worker','head':sc['head'],'model':expected_role_model('worker',t_data['task_id']),'state':'PROPOSAL_READY','task_id':t_data['task_id'],'result_sha256':prop_sha,'conversation_id':f'conv-worker-{cont_cid}'}
 w_stat_path=w_dir/'status.json'
 _w0600(w_stat_path,_cj(w_stat))
 receipt=candidate_runner(root,t_data,w_result,prop_sha,timeout=1200)
 if candidate_runner is not simulated_candidate_runner:
  assert receipt['isolation'] !=_SIMULATED_ISOLATION
 assert receipt['selectors'] == [t_rel]
 v_receipt=validate_test_receipt(receipt,prop_sha,require_passed=True)
 rec_path=w_dir/'pre-review-test-receipt.json'
 rec_bytes=_w0600(rec_path,_cj(v_receipt))
 rec_sha=hashlib.sha256(rec_bytes).hexdigest()
 r_result= {'status':'VERIFIED','summary':'Review verified proposal.','covered_requirements': [1],'findings': [],'changes': []}
 r_txt=_cj(r_result)
 r_res_path=r_dir/'result.json'
 _w0600(r_res_path,r_txt)
 r_sha=hashlib.sha256(r_txt.encode('utf-8')).hexdigest()
 _w0600(r_dir/'task.json',_cj({'source_head':sc['head'],'task':t_data,'sources':sources,'proposal':w_txt}))
 r_stat= {'run_id':r_run,'role':'verifier','head':sc['head'],'model':GEMINI_MODEL,'state':'VERIFIED','task_id':t_data['task_id'],'input_worker_run_id':w_run,'input_proposal_sha256':prop_sha,'input_test_evidence_sha256':rec_sha,'result_sha256':r_sha,'conversation_id':f'conv-reviewer-{cont_cid}'}
 r_stat_path=r_dir/'status.json'
 _w0600(r_stat_path,_cj(r_stat))
 seal_completion_fixture(root,w_dir)
 seal_completion_fixture(root,r_dir)
 inner_ready= {'state':'READY_FOR_CODEX','task_id':t_data['task_id'],'source_head':sc['head'],'run_prefix':resume_req['run_prefix'],'final_worker_run_id':w_run,'final_reviewer_run_id':r_run,'proposal_sha256':prop_sha,'changed_paths': [t_rel],'proposal_path':str(w_res_path.relative_to(root)),'reviewer_path':str(r_res_path.relative_to(root)),'correction_count':0,'blockers': [],'pre_review_test_path':str(rec_path.relative_to(root)),'pre_review_test_sha256':rec_sha,'pre_review_test_state':'PASSED','semantic_task_fingerprint':sc['identity']['fingerprint']}
 continuation_resp=build_continuation_broker_response(resume_req,inner_ready,now_epoch=resp_dt.timestamp())
 publish_terminal_continuation_record(root,resume_req,continuation_resp)
 target_file=root/t_rel
 target_file.parent.mkdir(parents=True,exist_ok=True)
 target_file.write_text(t_content,encoding='utf-8')
 _git(root,'add',t_rel)
 _git(root,'commit','-qm','feat: characterize source.VALUE')
 child_head=_git(root,'rev-parse','HEAD')
 raw_result= {'schema_version':'1.0','state':'CONTINUE','disposition':'NONE','category':'NONE','summary':'Accepted characterization test commit','commit':child_head,'next_action':'Proceed to next cycle','blockers': [],'gemini_worker_run_id':w_run,'gemini_reviewer_run_id':r_run,'proposal_sha256':prop_sha}
 validated_res=validate_result(raw_result)
 _w0600(cont_runs/'result.json',_cj(validated_res))
 with exclusive_supervisor_lock(ap_dir/'autopilot.lock'):
  enriched_transition=validate_transition(root,facts,validated_res)
  ack_record=finalize_accepted_quota_continuation_locked(root=root,original_controller_run_id=orig_cid,continuation_controller_run_id=cont_cid,before_repository=facts,expected_transition_result=enriched_transition,acknowledged_at_utc=ack_utc_str)
 context= {'controller_run_id':cont_cid,'protected_refs':facts['protected_refs'],'assignment_id':scope.assignment_id,'assignment_sha256':scope.sha256,'current_head':child_head}
 custody_before=_custody(root)
 with exclusive_supervisor_lock(ap_dir/'autopilot.lock'):
  upstream=collect_final_acceptance_upstream_lineage_from_snapshot_locked(root,orig_cid)
  accepted_lineage=read_accepted_lineage_record_candidate(root,cont_cid,enriched_transition,context,upstream)
  read_ack=read_final_ack_record_candidate(root,orig_cid,cont_cid,enriched_transition,context,upstream)
 assert read_ack==ack_record
 assert _custody(root) ==custody_before
 assert validate_repository(root)['protected_refs'] ==facts['protected_refs']
 assert _git(root,'status','--porcelain=v1') ==''
 return{'root':root,'original_controller_run_id':orig_cid,'continuation_controller_run_id':cont_cid,'actual_scope':scope,'before_repository':facts,'enriched_transition':enriched_transition,'upstream':upstream,'context':context,'raw_result':validated_res,'raw_proposal_sha256':prop_sha,'receipt_metadata':v_receipt,'ack':ack_record,'child_head':child_head,'head_before':sc['head'],'external_test_isolation':v_receipt['isolation'],'accepted_lineage':accepted_lineage,'autopilot_dir':ap_dir,'worker_status_path':w_stat_path,'reviewer_status_path':r_stat_path}

def test_simulated_external_outcomes_real_finalization_fixture(broker_repo):
 golden_ast_expected = {
  '_build_real_finalized_history_fixture': 'a9b6ef9ee3eb6a2dae258bc64a535ab8a4dfcb2b21ea8703a6c818a8050ed31d',
  '_cj': '8374fb6381839f20945da64a69786a82bec9d99b7932ee6c9000fb2810eb32ca',
  '_custody': 'f31e26a8a1513a358fbc3c639df27b4bcf11b95056fe676159a9a0ac0ae88cd3',
  '_git': '8892609546f3195402ffbef9c64b14f52bd6a7ceb4db7ac7c8d33f556583aae4',
  '_w0600': 'e60af2ba47a379d68bf3db0b024e0783cb74fc8a25808325699760cb09897278',
  'simulated_candidate_runner': '41017a275f00568f083dc36eee586da2f5e92e1ddac41c125a124a78cc7a19be',
  'test_finalization_fixture_requires_explicit_runner': 'e0334a2230c71719ec34e4ebbd15e7906f096b513548d23c6a6aa79478eb2c01',
  'test_real_finalization_actor_and_git_faults': 'f6a792f4ac9c99dd605f9d69b3aae8ccdebaf1fa5bcf4869b9b19190c0d2c4bc',
 }
 target_funcs = {
  '_build_real_finalized_history_fixture': _build_real_finalized_history_fixture,
  '_cj': _cj,
  '_custody': _custody,
  '_git': _git,
  '_w0600': _w0600,
  'simulated_candidate_runner': simulated_candidate_runner,
  'test_finalization_fixture_requires_explicit_runner': test_finalization_fixture_requires_explicit_runner,
  'test_real_finalization_actor_and_git_faults': test_real_finalization_actor_and_git_faults,
 }
 assert set(target_funcs.keys()) == set(golden_ast_expected.keys())
 for fn_name in sorted(golden_ast_expected.keys()):
  fn = target_funcs[fn_name]
  src = inspect.getsource(fn)
  assert len(src) <= 2280384
  mod_ast = ast.parse(src)
  fn_node = next(n for n in mod_ast.body if isinstance(n, ast.FunctionDef))
  dumped = ast.dump(fn_node, include_attributes=False)
  digest = hashlib.sha256(dumped.encode('utf-8')).hexdigest()
  assert digest == golden_ast_expected[fn_name]

 fx=_build_real_finalized_history_fixture(broker_repo,candidate_runner=simulated_candidate_runner)
 assert fx['external_test_isolation'] ==_SIMULATED_ISOLATION
 assert fx['receipt_metadata']['state'] =='PASSED'
 assert fx['receipt_metadata']['returncode'] ==0
 assert fx['enriched_transition']['head_changed']is True
 assert fx['enriched_transition']['head_before'] ==fx['head_before']
 assert fx['enriched_transition']['head_after'] ==fx['child_head']
 assert fx['ack']['schema_version'] =='1.0'
 revs=_git(fx['root'],'rev-list','--parents','-n','1',fx['child_head']).split()
 assert len(revs) ==2 and revs[0] ==fx['child_head']and(revs[1] ==fx['head_before'])
 custody_before=_custody(fx['root'])
 with exclusive_supervisor_lock(fx['autopilot_dir'] /'autopilot.lock'):
  re_read_ack=read_final_ack_record_candidate(fx['root'],fx['original_controller_run_id'],fx['continuation_controller_run_id'],fx['enriched_transition'],fx['context'],fx['upstream'])
 assert re_read_ack==fx['ack']
 assert _custody(fx['root']) ==custody_before

 ack=fx['ack']
 expected_ack_keys = {
  'schema_version',
  'ack_kind',
  'original_controller_run_id',
  'continuation_controller_run_id',
  'resume_request_id',
  'terminal_receipt_sha256',
  'accepted_lineage_sha256',
  'result_sha256',
  'accepted_state',
  'head_after',
  'assignment_sha256',
  'acknowledged_at_utc',
 }
 assert set(ack.keys()) == expected_ack_keys
 assert ack['schema_version'] == '1.0'
 assert ack['ack_kind'] == 'CODEX_FINAL_ACCEPTANCE'
 assert ack['original_controller_run_id'] == fx['original_controller_run_id']
 assert ack['continuation_controller_run_id'] == fx['continuation_controller_run_id']
 assert ack['resume_request_id'] == fx['upstream']['resume_request_id']
 assert ack['accepted_state'] == 'CONTINUE'
 assert ack['head_after'] == fx['child_head']
 assert ack['assignment_sha256'] == fx['actual_scope'].sha256

 base_dir = fx['root'] / '.local/two-site-refactor'
 res_path = base_dir / 'autopilot/runs' / fx['continuation_controller_run_id'] / 'result.json'
 lin_path = base_dir / 'autopilot/runs' / fx['continuation_controller_run_id'] / 'accepted-lineage.json'
 term_path = base_dir / 'provider-broker/terminal-continuation' / f"{fx['upstream']['resume_request_id']}.json"
 wait_path = base_dir / 'autopilot/runs' / fx['original_controller_run_id'] / 'quota-wait.json'

 custody_before_reads = _custody(fx['root'])

 for p in (res_path, lin_path, term_path, wait_path):
  st = p.lstat()
  assert stat.S_ISREG(st.st_mode) and st.st_nlink == 1
  assert st.st_size <= 2280384

 res_bytes = res_path.read_bytes()
 lin_bytes = lin_path.read_bytes()
 term_bytes = term_path.read_bytes()
 wait_bytes = wait_path.read_bytes()

 assert hashlib.sha256(res_bytes).hexdigest() == ack['result_sha256']
 assert hashlib.sha256(lin_bytes).hexdigest() == ack['accepted_lineage_sha256']
 assert hashlib.sha256(term_bytes).hexdigest() == ack['terminal_receipt_sha256']

 wait_obj = json.loads(wait_bytes.decode('utf-8'))
 next_check_utc_str = wait_obj['schedule']['next_check_at_utc']
 next_check_dt = datetime.fromisoformat(next_check_utc_str.replace('Z', '+00:00'))
 expected_ack_utc = (next_check_dt + timedelta(seconds=12)).strftime('%Y-%m-%dT%H:%M:%SZ')
 assert ack['acknowledged_at_utc'] == expected_ack_utc

 assert _custody(fx['root']) == custody_before_reads

@pytest.mark.parametrize('fault_mode', ['same_conversation','content_tamper'])
def test_real_finalization_actor_and_git_faults(broker_repo,fault_mode):
 fx=_build_real_finalized_history_fixture(broker_repo,candidate_runner=simulated_candidate_runner)
 custody_before=_custody(fx['root'])
 if fault_mode=='same_conversation':
  stat_p=fx['reviewer_status_path']
  orig_bytes=stat_p.read_bytes()
  w_conv=json.loads(fx['worker_status_path'].read_text(encoding='utf-8'))['conversation_id']
  r_dict=json.loads(orig_bytes.decode('utf-8'))
  r_dict['conversation_id'] =w_conv
  stat_p.write_text(_cj(r_dict),encoding='utf-8')
  try:
   with pytest.raises(AutopilotError,match='Gemini review did not use an independent fresh session'):
    validate_scoped_gemini_chain(fx['root'],fx['head_before'],fx['raw_result'],fx['actual_scope'])
  finally:
   stat_p.write_bytes(orig_bytes)
  assert stat_p.read_bytes() ==orig_bytes
  chain=validate_scoped_gemini_chain(fx['root'],fx['head_before'],fx['raw_result'],fx['actual_scope'])
  assert chain['status'] =='PROPOSAL_READY'
 elif fault_mode=='content_tamper':
  accepted_head=fx['child_head']
  _git(fx['root'],'checkout','-b','temp-tamper-branch',fx['head_before'])
  try:
   (fx['root'] /'tests').mkdir(exist_ok=True)
   (fx['root'] /'tests/test_new.py').write_text('def test_tampered():\n    assert False\n',encoding='utf-8')
   _git(fx['root'],'add','tests/test_new.py')
   _git(fx['root'],'commit','-qm','tampered commit')
   tampered_head=_git(fx['root'],'rev-parse','HEAD')
   proposal=validate_scoped_gemini_chain(fx['root'],fx['head_before'],fx['raw_result'],fx['actual_scope'])
   with pytest.raises(AutopilotError,match='committed bytes differ from the Gemini proposal'):
    validate_scoped_committed_proposal(fx['root'],fx['head_before'],tampered_head,proposal,fx['actual_scope'])
  finally:
   _git(fx['root'],'checkout',BRANCH)
   _git(fx['root'],'branch','-D','temp-tamper-branch')
  current_head=_git(fx['root'],'rev-parse','HEAD')
  assert current_head==accepted_head
  facts=validate_repository(fx['root'])
  assert facts['protected_refs'] ==fx['before_repository']['protected_refs']
 assert _custody(fx['root']) ==custody_before

@pytest.mark.parametrize('bad_runner', [None,0])
def test_finalization_fixture_requires_explicit_runner(bad_runner):
 with pytest.raises(TypeError,match='candidate_runner must be an explicit callable'):
  _build_real_finalized_history_fixture((),candidate_runner=bad_runner)


# Additive multi-history fixture. Provider/Reviewer messages are declared synthetic;
# actual Pipeline semantic reservation, file/Git/Scope/ACK readers are unpatched.
def _history_custody(root:Path) ->dict:
 result={}
 for path in sorted((root/'.local/two-site-refactor').rglob('*')):
  if path.is_file() and not path.name.endswith('.lock'):
   st=path.lstat()
   assert stat.S_ISREG(st.st_mode) and st.st_nlink==1 and st.st_size<=2280384
   result[path.relative_to(root).as_posix()]=(path.read_bytes(),st.st_dev,st.st_ino,st.st_mode,st.st_uid,st.st_nlink,st.st_mtime_ns,st.st_ctime_ns)
 return result


def _bootstrap_quota_history_lab(repo:tuple,*,test_paths:tuple[str,...]) ->dict:
 from tests.test_two_site_quota_full_tree_repository_facts import _populate_full_tree_repo
 from tests.test_two_site_assignment_scope_core import _write_assignment,_write_worktrees
 from scripts.two_site_assignment_scope import load_active_assignment_scope
 root,template,_,env=repo
 assert len(set(test_paths))==len(test_paths) and all(p.startswith('tests/test_') and p.endswith('.py') for p in test_paths)
 support=('config/unit-test.env.example','scripts/two_site_pre_review_pytest_gate.py',
          'scripts/two_site_autopilot_monitor_preflight.py')
 gap='docs/refactor/two-server/ae03-history-fixture-gap.md'
 for relative in support:
  target=root/relative
  target.parent.mkdir(parents=True,exist_ok=True)
  target.write_bytes((SOURCE_ROOT/relative).read_bytes())
 (root/gap).parent.mkdir(parents=True,exist_ok=True)
 (root/gap).write_text('# gap\n\nBounded quota history fixture.\n',encoding='utf-8')
 _git(root,'add',*support,gap)
 _git(root,'commit','-qm','fixture: history support before bootstrap')
 _populate_full_tree_repo(root)
 _write_worktrees(root,path=str(root),branch=BRANCH,active_assignment_id='WA-BROKER-TEST')
 _write_assignment(root,assignment_id='WA-BROKER-TEST',integration_branch=BRANCH,
                   allowed_paths=list(test_paths),forbidden_paths=['scripts/**'])
 base=root/'.local/two-site-refactor'
 os.chmod(base,0o700)
 for relative in ('tasks','proposals','autopilot'):
  directory=base/relative
  directory.mkdir(exist_ok=True)
  os.chmod(directory,0o700)
 scope=load_active_assignment_scope(root)
 return {'root':root,'template':json.loads(template.read_text()),'environment':dict(env),
         'scope':scope,'gap':gap+'#gap','test_paths':test_paths,'prior':[],'acknowledged':[],
         'protected_refs':validate_repository(root)['protected_refs']}


def _prepare_quota_history_wait(lab:dict,*,label:str,original_cid:str,
                                continuation_cid:str,test_path:str,unknown:bool=False,reuse_pending:bool=False) ->dict:
 from datetime import timezone
 from unittest.mock import patch
 from scripts import two_site_codex_autopilot as ap
 from scripts import two_site_provider_broker as broker
 from scripts import two_site_proposal_pipeline as pipeline
 from scripts.two_site_semantic_task_builder import build_task_identity
 from scripts.two_site_semantic_attempts import MARKER,read_existing_semantic_attempt
 from tests.test_two_site_provider_broker import _make_quota_payload,_make_quota_response,_write_runner_status
 root=lab['root']
 assert test_path in lab['test_paths'] and not (root/test_path).exists()
 retained=_quota_revalidated_history(lab) if reuse_pending else None
 if not reuse_pending:
  assert all(_history_custody(root).get(path)==value for prior in lab['prior'] for path,value in prior.items())
 facts=validate_repository(root)
 assert facts['protected_refs']==lab['protected_refs'] and facts['assignment_scope']==lab['scope']
 head=facts['head']
 identity=build_task_identity(root,lab['gap'],'quota.history.'+label,['source.py'],[test_path],[])
 task=dict(lab['template'],task_id='AE03-HISTORY-'+label,allowed_files=[test_path],context_files=['source.py'],test_selectors=[test_path])
 task['requirements']=['Preserve source.VALUE == 1.','PROPOSAL_UTF8_BUDGET_V1:'+_cj({'file_limits':{test_path:24000},'total_limit':24000,'version':'1.0'})]
 task['goal']='WA-BROKER-TEST: characterize immutable history.'+MARKER+_cj({
     'head':head,'gap_reference':lab['gap'],'excerpts':[],'semantic_identity':identity})
 task_path=root/'.local/two-site-refactor/tasks'/('history-'+label+'.json')
 assert not task_path.exists()
 _w0600(task_path,_cj(task))
 env=dict(lab['environment'],TWO_SITE_AUTOPILOT_RUN_ID=original_cid,
          TWO_SITE_ASSIGNMENT_ID=lab['scope'].assignment_id,TWO_SITE_ASSIGNMENT_SHA256=lab['scope'].sha256)
 request=broker.make_request(root,task_path,'history-'+label,300,env)
 ap_dir=root/'.local/two-site-refactor/autopilot'
 _,run=_setup_cycle_root(str(root),original_cid)
 _write_startup_json(run,cycle_id=original_cid,source_head=head,assignment_sha256=lab['scope'].sha256)
 now=datetime.now(timezone.utc).replace(microsecond=0)
 with exclusive_supervisor_lock(ap_dir/'autopilot.lock'):
  epoch=ap.ensure_rescope_epoch_dir(ap_dir,ap.validate_accepted_repository(facts))
  if reuse_pending:
   from scripts.two_site_quota_wait_intent import read_pending_rescope_intent
   current_epoch,attempt,current=read_pending_rescope_intent(root)
   assert current_epoch==epoch.name and current==ap.validate_accepted_repository(facts)
  else:
   attempt=next((n for n in (1,2,3) if not (epoch/f'rescope_intent_{n}').exists()),None)
   assert attempt is not None
   ap.publish_rescope_intent(epoch,attempt)
  broker.publish_cycle_reservation(root,request)
 state={'state':'TASK_BLOCKED','task_id':'','source_head':'','final_worker_run_id':None,
        'final_reviewer_run_id':None,'proposal_sha256':None,'changed_paths':[],'proposal_path':None,
        'reviewer_path':None,'correction_count':0,'blockers':[],'pre_review_test_path':None,
        'pre_review_test_sha256':None,'pre_review_test_state':None,'semantic_task_fingerprint':None}
 reached=[]
 def external_runner(root_arg,task_arg,role,run_id,timeout,**kwargs):
  assert root_arg==root and task_arg==task_path and role=='worker' and run_id=='history-'+label+'-worker-0'
  reached.append(run_id)
  payload=_make_quota_payload(root,task['task_id'],head,run_prefix=request['run_prefix'],
                             kind='QUOTA_RESET_UNKNOWN' if unknown else 'QUOTA_RESET_KNOWN')
  evidence=dict(payload['quota_evidence'])
  if not unknown:
   evidence['reset_at_utc']=(now-timedelta(seconds=100)).strftime('%Y-%m-%dT%H:%M:%SZ')
  evidence['model']=expected_role_model('worker',task['task_id'])
  quota_status,_=_write_runner_status(root,run_id,role='worker',head=head,model=evidence['model'],
                       task_id=task['task_id'],quota_evidence=evidence,conversation_id=evidence['conversation_id'])
  os.chmod(quota_status.parent,0o700)
  os.chmod(quota_status,0o600)
  raise pipeline.ProposalError('declared synthetic quota at real Runner boundary')
 with patch.dict(os.environ,env),patch.object(pipeline,'runner_run',external_runner):
  try:
   pipeline.run_pipeline(root,task_path,request['run_prefix'],300,None,state)
  except pipeline.QuotaBlockError as exc:
   state['quota_evidence']=exc.quota_evidence
   state['quota_attempt']=exc.quota_attempt
   state['blockers']=['observational quota, no replay authority']
  else:
   raise AssertionError('real Pipeline did not recognize the bound quota status')
 assert reached==['history-'+label+'-worker-0']
 semantic=read_existing_semantic_attempt(root,root/'.local/two-site-refactor/pipeline',task,request['run_prefix'])
 assert semantic['fingerprint']==identity['fingerprint']==state['semantic_task_fingerprint']
 assert not (root/'.local/two-site-refactor/semantic-attempts'/ (identity['fingerprint']+'.json')).exists()
 response=_make_quota_response(request,state)
 broker.publish_terminal_quota_record(root,request,response)
 assert broker.read_terminal_quota_record(root,request['request_id'])['response']==response
 return {'root':root,'task_path':task_path,'actual_scope':lab['scope'],'repo_facts':facts,
         'original_ctrl_id':original_cid,'continuation_ctrl_id':continuation_cid,'autopilot_dir':ap_dir,
         'head':head,'identity':identity,'now_utc':now,'request':request,'intent_epoch':epoch.name,'intent_attempt':attempt,
         'retained_custody':retained}


def _append_quota_history(lab:dict,*,label:str,original_cid:str,continuation_cid:str,
                           test_path:str,candidate_runner:Callable,finalize:bool=True,
                           unknown:bool=False,commit_change:bool=True,reuse_pending:bool=False) ->dict:
 sc=_prepare_quota_history_wait(lab,label=label,original_cid=original_cid,
       continuation_cid=continuation_cid,test_path=test_path,unknown=unknown,reuse_pending=reuse_pending)
 root,task_path=sc['root'],sc['task_path']
 if not finalize:
  with exclusive_supervisor_lock(sc['autopilot_dir']/'autopilot.lock'):
   wait=admit_initial_quota_wait_locked(root,original_cid,clock=lambda:sc['now_utc']-timedelta(minutes=16) if unknown else sc['now_utc']-timedelta(seconds=120))
  if sc['retained_custody'] is not None:
   assert all(_history_custody(root).get(path)==value for path,value in sc['retained_custody'].items())
   assert validate_repository(root)==sc['repo_facts']
  return dict(sc,wait=wait,original_controller_run_id=original_cid)
 from scripts.two_site_quota_continuation_admission import normal_pipeline_context
 with normal_pipeline_context(root):
  result=_finalize_configured_history(sc,test_path=test_path,candidate_runner=candidate_runner,commit_change=commit_change)
 lab['acknowledged'].append((original_cid,_cj(result['ack'])))
 lab['prior'].append({p:v for p,v in _history_custody(root).items() if not p.endswith('/worktrees.yaml')})
 return result


def _finalize_configured_history(sc:dict,*,test_path:str,candidate_runner:Callable,commit_change:bool) ->dict:
 root,task_path=sc['root'],sc['task_path']
 scope,facts= (sc['actual_scope'],sc['repo_facts'])
 orig_cid,cont_cid= (sc['original_ctrl_id'],sc['continuation_ctrl_id'])
 ap_dir=sc['autopilot_dir']
 with exclusive_supervisor_lock(ap_dir/'autopilot.lock'):
  wait=admit_initial_quota_wait_locked(root,orig_cid,clock=lambda:sc['now_utc'] -timedelta(seconds=120))
  next_check=datetime.fromisoformat(wait['schedule']['next_check_at_utc'].replace('Z','+00:00'))
  claim_dt=next_check+timedelta(seconds=10)
  claim_utc_str=claim_dt.strftime('%Y-%m-%dT%H:%M:%SZ')
  resp_dt=claim_dt+timedelta(seconds=1)
  ack_utc_str= (resp_dt+timedelta(seconds=1)).strftime('%Y-%m-%dT%H:%M:%SZ')
  from scripts.two_site_quota_wait_sources import collect_initial_wait_source_expected
  from scripts.two_site_quota_wait_storage import read_initial_wait_record_bytes
  from scripts.two_site_semantic_attempts import read_existing_semantic_attempt
  expected=collect_initial_wait_source_expected(root,orig_cid,sc['intent_epoch'],sc['intent_attempt'])
  raw=read_initial_wait_record_bytes(root,orig_cid)
  loaded,_=load_task(root,task_path)
  semantic=read_existing_semantic_attempt(root,root/'.local/two-site-refactor/pipeline',loaded,sc['request']['run_prefix'])
  ctx={'wait_record':wait,'expected_wait':expected,'raw_wait_bytes':raw,'semantic_attempt':semantic,'expected_task_id':loaded['task_id']}
  resume_req_id=hashlib.sha256(cont_cid.encode('utf-8')).hexdigest()[:32]
  sub=compose_and_publish_known_reset_resume_request_locked(root,orig_cid,ctx['wait_record'],ctx['expected_wait'],ctx['raw_wait_bytes'],ctx['semantic_attempt'],ctx['expected_task_id'],cont_cid,resume_req_id,claim_utc_str,300,claim_dt.timestamp())
  publish_expected_lineage_snapshot(root,orig_cid,ctx['wait_record'],ctx['expected_wait'],sub['claim'],sub['expected_claim'],sub['request'])
  publish_quota_resume_submission_intent(root,sub['request'],now_epoch=claim_dt.timestamp(),pipeline_dir=root/'.local/two-site-refactor/pipeline')
 resume_req=sub['request']
 ready_artifacts=_quota_history_ready_artifacts(sc,resume_req,test_path,candidate_runner)
 cont_runs=ready_artifacts['cont_runs'];t_rel=ready_artifacts['t_rel'];t_content=ready_artifacts['t_content']
 w_run=ready_artifacts['w_run'];r_run=ready_artifacts['r_run'];prop_sha=ready_artifacts['prop_sha']
 v_receipt=ready_artifacts['v_receipt'];w_stat_path=ready_artifacts['w_stat_path'];r_stat_path=ready_artifacts['r_stat_path']
 if commit_change:
  target_file=root/t_rel
  target_file.parent.mkdir(parents=True,exist_ok=True)
  target_file.write_text(t_content,encoding='utf-8')
  _git(root,'add',t_rel)
  _git(root,'commit','-qm','feat: characterize source.VALUE')
 child_head=_git(root,'rev-parse','HEAD')
 raw_result= {'schema_version':'1.0','state':'CONTINUE' if commit_change else 'COMPLETE','disposition':'NONE','category':'NONE','summary':'Accepted characterization test commit','commit':child_head if commit_change else None,'next_action':'Proceed to next cycle','blockers': [],'gemini_worker_run_id':w_run if commit_change else None,'gemini_reviewer_run_id':r_run if commit_change else None,'proposal_sha256':prop_sha if commit_change else None}
 validated_res=validate_result(raw_result)
 _w0600(cont_runs/'result.json',_cj(validated_res))
 with exclusive_supervisor_lock(ap_dir/'autopilot.lock'):
  enriched_transition=validate_transition(root,facts,validated_res)
  ack_record=finalize_accepted_quota_continuation_locked(root=root,original_controller_run_id=orig_cid,continuation_controller_run_id=cont_cid,before_repository=facts,expected_transition_result=enriched_transition,acknowledged_at_utc=ack_utc_str)
 context= {'controller_run_id':cont_cid,'protected_refs':facts['protected_refs'],'assignment_id':scope.assignment_id,'assignment_sha256':scope.sha256,'current_head':child_head}
 custody_before=_history_custody(root)
 with exclusive_supervisor_lock(ap_dir/'autopilot.lock'):
  upstream=collect_final_acceptance_upstream_lineage_from_snapshot_locked(root,orig_cid)
  accepted_lineage=read_accepted_lineage_record_candidate(root,cont_cid,enriched_transition,context,upstream)
  read_ack=read_final_ack_record_candidate(root,orig_cid,cont_cid,enriched_transition,context,upstream)
 assert read_ack==ack_record
 assert _history_custody(root) ==custody_before
 assert validate_repository(root)['protected_refs'] ==facts['protected_refs']
 assert _git(root,'status','--porcelain=v1') ==''
 return{'task_path':task_path,'request':sc['request'],'wait':wait,'root':root,'original_controller_run_id':orig_cid,'continuation_controller_run_id':cont_cid,'actual_scope':scope,'before_repository':facts,'enriched_transition':enriched_transition,'upstream':upstream,'context':context,'raw_result':validated_res,'raw_proposal_sha256':prop_sha,'receipt_metadata':v_receipt,'ack':ack_record,'child_head':child_head,'head_before':sc['head'],'external_test_isolation':v_receipt['isolation'],'accepted_lineage':accepted_lineage,'autopilot_dir':ap_dir,'worker_status_path':w_stat_path,'reviewer_status_path':r_stat_path}


# Finite reusable builders for collected C4c proofs; no execution at import.
from contextlib import contextmanager
from datetime import timezone
import threading
import time
_RETIREMENT_B=Path('.local/two-site-refactor')
_RETIREMENT_O='cycle-0001-20261020T120000Z-901'
_RETIREMENT_C='cycle-0001-20261020T120000Z-902'
_RETIREMENT_ASSIGNMENT='WA-ROOT-C4C-HERMETIC-RELEASE'
_RETIREMENT_FP=('dev','ino','mode','uid','nlink','size','mtime_ns','ctime_ns')

def _retirement_digest(raw): return hashlib.sha256(raw).hexdigest()
def _retirement_canonical(value): return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()


def _retirement_fp(path):
    info=path.lstat()
    return dict(zip(_RETIREMENT_FP,(info.st_dev,info.st_ino,info.st_mode,info.st_uid,info.st_nlink,
                        info.st_size,info.st_mtime_ns,info.st_ctime_ns)))


def _retirement_business_rows(fx):
    root=fx['root'];wait=fx['wait'];result=fx['raw_result']
    paths={
      'STARTUP':_RETIREMENT_B/'autopilot/runs'/_RETIREMENT_O/'preparation-startup.json',
      'RESERVATION':_RETIREMENT_B/'autopilot/runs'/_RETIREMENT_O/'broker-reservation.json',
      'WAIT':_RETIREMENT_B/'autopilot/runs'/_RETIREMENT_O/'quota-wait.json',
      'CLAIM':_RETIREMENT_B/'autopilot/runs'/_RETIREMENT_O/'quota-resume-claim.json',
      'SNAPSHOT':_RETIREMENT_B/'autopilot/runs'/_RETIREMENT_O/'quota-resume-lineage-snapshot.json',
      'SUBMISSION':_RETIREMENT_B/'autopilot/runs'/_RETIREMENT_O/'quota-resume-submission.json',
      'RESULT':_RETIREMENT_B/'autopilot/runs'/_RETIREMENT_C/'result.json',
      'LINEAGE':_RETIREMENT_B/'autopilot/runs'/_RETIREMENT_C/'accepted-lineage.json',
      'ACK':_RETIREMENT_B/'autopilot/runs'/_RETIREMENT_C/'final-ack.json',
      'TASK':fx['task_path'].relative_to(root),
      'ASSIGNMENT':_RETIREMENT_B/'assignments/WA-BROKER-TEST.md',
      'QUOTA_STATUS':_RETIREMENT_B/'proposals'/wait['quota_attempt']['run_id']/'status.json',
      'SEMANTIC_PIPELINE':_RETIREMENT_B/'pipeline/semantic-attempts'/(wait['semantic_task_fingerprint']+'.json')}
    suffixes={'TASK':'task.json','RESULT':'result.json','STATUS':'status.json','PROMPT':'prompt.txt',
              'STREAM':'stream.jsonl','STDERR':'stderr.txt','SCHEMA':'response-schema.json',
              'COMPLETION':'transport-completion.json'}
    for role,run in [('W',result['gemini_worker_run_id']),('V',result['gemini_reviewer_run_id'])]:
        if run is None: continue
        for suffix,filename in suffixes.items(): paths[role+'_'+suffix]=_RETIREMENT_B/'proposals'/run/filename
        delta=_RETIREMENT_B/'proposals'/run/'delta-result.json'
        if (root/delta).exists(): paths[role+'_DELTA']=delta
    return [{'role':role,'relative_path':path.as_posix(),'sha256':_retirement_digest((root/path).read_bytes())}
            for role,path in paths.items()]


def _retirement_seed_authority(fx,*,transition_bad=False):
    from scripts import two_site_codex_autopilot as ap
    from scripts.two_site_assignment_scope import load_active_assignment_scope
    from tests.test_two_site_assignment_scope_core import _write_assignment,_write_worktrees
    root=fx['root'];reviews=root/_RETIREMENT_B/'reviews';reviews.mkdir(mode=0o700,exist_ok=True)
    os.chmod(reviews,0o700)
    state=root/_RETIREMENT_B/'autopilot'
    stop=state/'STOP';archive=reviews/'AE_REQUIRED_COMPLETION_OWNED_STOP_ARCHIVE_20261020'
    _w0600(stop,b'STOP\n')
    fd=os.open(stop,os.O_RDONLY|os.O_NOFOLLOW)
    try: os.fsync(fd)
    finally: os.close(fd)
    os.rename(stop,archive)
    for directory in (state,reviews):
        fd=os.open(directory,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try: os.fsync(fd)
        finally: os.close(fd)
    archived={'relative_path':archive.relative_to(root).as_posix(),'sha256':_retirement_digest(archive.read_bytes()),
              'fingerprint':_retirement_fp(archive)}
    st=archive.lstat()
    marks=[str(n) for n in (st.st_dev,st.st_ino,st.st_mode,st.st_uid,st.st_gid,
                            st.st_nlink,st.st_size,st.st_mtime_ns,st.st_ctime_ns)]
    observed={'kind':'ROOT_AE_CLOSEOUT_REAL_MAINTENANCE_EFFECTS_OBSERVED','date_utc':'2026-10-20',
      'head':fx['child_head'],'decision_sha256':'d'*64,'independent_review':'hermetic-peer.json',
      'archive':{'actual_exit_code':0,'actual_chunk':'hermetic-real-rename-fsync','path':archive.name,
        'sha256':archived['sha256'],'mark_strings':marks,'source_absent':not transition_bad,
        'leaf_and_both_directory_fsync':True},
      'broker_start':{},'a2_first_build':{},'preserved':{},'readonly_invoker_failures_preserved':[]}
    observation=reviews/'AE_REQUIRED_COMPLETION_ROOT_MAINTENANCE_OBSERVED_20261020.json'
    _w0600(observation,json.dumps(observed,indent=2))
    core=_retirement_business_rows(fx)
    core.extend([{'role':'STOP_ARCHIVE',**{k:archived[k] for k in ('relative_path','sha256')}},
                 {'role':'STOP_TRANSITION','relative_path':observation.relative_to(root).as_posix(),
                  'sha256':_retirement_digest(observation.read_bytes())}])
    core.sort(key=lambda row:(row['role'],row['relative_path']))
    base_sha=_retirement_digest(_retirement_canonical(core))
    ack=fx['ack']
    ack_raw=(state/'runs'/_RETIREMENT_C/'final-ack.json').read_bytes()
    stamp=datetime.fromisoformat(ack['acknowledged_at_utc'].replace('Z','+00:00'))+timedelta(days=10,seconds=2)
    ids={'original_controller_run_id':_RETIREMENT_O,'continuation_controller_run_id':_RETIREMENT_C,
         'original_request_id':fx['request']['request_id'],'resume_request_id':ack['resume_request_id'],
         'semantic_task_fingerprint':fx['wait']['semantic_task_fingerprint']}
    release={'schema_version':'1.0','record_kind':'ROOT_QUOTA_RETIREMENT_RELEASE',**ids,
             'final_ack_sha256':_retirement_digest(ack_raw),'retained_anchor_sha256':base_sha,
             'archived_stop':archived,'incident_disposition':'CLEAR_NO_EXISTING_HOLD',
             'authorized_at_utc':(stamp-timedelta(seconds=2)).strftime('%Y-%m-%dT%H:%M:%SZ')}
    release_path=reviews/'hermetic-root-quota-retirement-release.json'
    _w0600(release_path,_retirement_canonical(release))
    pre={'schema_version':'1.0','record_kind':'ROOT_QUOTA_RETIREMENT_PREACTION',
         'assignment_id':_RETIREMENT_ASSIGNMENT,'maintenance_head':fx['child_head'],**ids,
         'final_ack_sha256':_retirement_digest(ack_raw),'retained_anchor_sha256':base_sha,
         'release':{'relative_path':release_path.relative_to(root).as_posix(),
                    'sha256':_retirement_digest(release_path.read_bytes())},
         'authorized_at_utc':(stamp-timedelta(seconds=1)).strftime('%Y-%m-%dT%H:%M:%SZ')}
    pre_path=reviews/'hermetic-root-quota-retirement-preaction.json'
    _w0600(pre_path,_retirement_canonical(pre))
    _write_assignment(root,assignment_id=_RETIREMENT_ASSIGNMENT,integration_branch=ap.BRANCH,
                      allowed_paths=['tests/test_quota_retained.py'],forbidden_paths=['scripts/**'],
                      base_sha=fx['child_head'])
    assignment=root/_RETIREMENT_B/'assignments'/(_RETIREMENT_ASSIGNMENT+'.md')
    marker={'relative_path':pre_path.relative_to(root).as_posix(),'sha256':_retirement_digest(pre_path.read_bytes())}
    with assignment.open('ab') as handle:
        handle.write(('\nROOT_QUOTA_RETIREMENT_PREACTION_V1:'+_retirement_canonical(marker).decode()+'\n').encode())
    _write_worktrees(root,path=str(root),branch=ap.BRANCH,active_assignment_id=_RETIREMENT_ASSIGNMENT)
    scope=load_active_assignment_scope(root)
    authority=[
      {'role':'RELEASE','relative_path':release_path.relative_to(root).as_posix(),'sha256':_retirement_digest(release_path.read_bytes())},
      {'role':'RETIREMENT_ASSIGNMENT','relative_path':assignment.relative_to(root).as_posix(),'sha256':_retirement_digest(assignment.read_bytes())},
      {'role':'RETIREMENT_PREACTION','relative_path':pre_path.relative_to(root).as_posix(),'sha256':_retirement_digest(pre_path.read_bytes())}]
    retained=sorted(core+authority,key=lambda row:(row['role'],row['relative_path']))
    assert len(retained)<=48 and _retirement_digest(_retirement_canonical(core))==base_sha
    receipts=[]
    for role,family,rid in [('ORIG_RECEIPT','terminal-quota',ids['original_request_id']),
                            ('RESUME_RECEIPT','terminal-continuation',ids['resume_request_id'])]:
        path=root/_RETIREMENT_B/'provider-broker'/family/(rid+'.json')
        value=json.loads(path.read_bytes())
        receipts.append({'role':role,'relative_path':path.relative_to(root).as_posix(),
          'sha256':_retirement_digest(path.read_bytes()),'response_sha256':value['response_sha256'],'fingerprint':_retirement_fp(path)})
    intent={'schema_version':'1.0','record_kind':'QUOTA_HISTORY_RETIREMENT_INTENT',**ids,
      'acknowledged_at_utc':ack['acknowledged_at_utc'],'certified_at_utc':stamp.strftime('%Y-%m-%dT%H:%M:%SZ'),
      'release_sha256':_retirement_digest(release_path.read_bytes()),'retained_anchor_sha256':base_sha,
      'accepted_binding':{'source_head':fx['head_before'],'head_after':fx['child_head'],
        'task_sha256':fx['wait']['reservation']['task_sha256'],'assignment_sha256':fx['actual_scope'].sha256,
        'accepted_state':ack['accepted_state']},'retained':retained,'collectible':receipts,
      'superseded_edges':[
       ['WAIT','/terminal_receipt_sha256','ORIG_RECEIPT.raw'],['WAIT','/response_sha256','ORIG_RECEIPT.response'],
       ['SNAPSHOT','/expected_wait/terminal_receipt_sha256','ORIG_RECEIPT.raw'],
       ['SNAPSHOT','/expected_wait/response_sha256','ORIG_RECEIPT.response'],
       ['LINEAGE','/upstream/terminal_receipt_sha256','RESUME_RECEIPT.raw'],
       ['LINEAGE','/upstream/response_sha256','RESUME_RECEIPT.response'],
       ['ACK','/terminal_receipt_sha256','RESUME_RECEIPT.raw']],
      'retirement_authority':{'assignment_id':_RETIREMENT_ASSIGNMENT,'assignment_sha256':scope.sha256}}
    ap.write_supervisor_state(state,'STOPPED',cycle=1,head=fx['child_head'],blockers=[])
    (root/_RETIREMENT_B/'monitor').mkdir(mode=0o700,exist_ok=True)
    return {'intent':intent,'stamp':stamp,'release':release_path,'preaction':pre_path,
            'assignment':assignment,'archive':archive,'observation':observation}


@contextmanager
def _retirement_idle_broker(root,*,quota_enabled=False):
    from scripts import two_site_provider_broker as broker
    state=broker.broker_paths(root)[0]
    with broker.exclusive_broker_lock(state/'client.lock'):
        pass
    prior_stop=broker._STOP
    errors=[];executed=[]
    def executor(*args,**kwargs):
        executed.append(args);raise AssertionError('retired paths must never reach Provider')
    def owner():
        try: broker.serve(root,executor=executor,install_signal_handlers=False,quota_continuation_enabled=quota_enabled)
        except BaseException as exc: errors.append(exc)
    thread=threading.Thread(target=owner,daemon=True)
    try:
        thread.start()
        deadline=time.monotonic()+10
        while time.monotonic()<deadline:
            status_path=root/_RETIREMENT_B/'provider-broker/status.json'
            if status_path.exists():
                status=broker._read_broker_status(root,status_path)
                if status.get('state')=='IDLE': break
            if errors: raise errors[0]
            time.sleep(.01)
        else: raise AssertionError('real ordinary Broker did not become IDLE')
        yield executed
    finally:
        broker._STOP=True
        try:
            if thread.ident is not None:
                thread.join(10)
            assert not thread.is_alive()
            assert executed==[]
        finally:
            if not thread.is_alive():
                broker._STOP=prior_stop
    assert errors==[]


def _retirement_history(broker_repo,*,commit_change=True):
    lab=_bootstrap_quota_history_lab(broker_repo,test_paths=('tests/test_quota_retained.py',))
    return _append_quota_history(lab,label='retained',original_cid=_RETIREMENT_O,continuation_cid=_RETIREMENT_C,
        test_path=lab['test_paths'][0],candidate_runner=simulated_candidate_runner,commit_change=commit_change)




@contextmanager
def _quota_publication_fault(monkeypatch, root, original, filename, stage):
    """Inject one real codec operation, forwarding every unrelated operation."""
    from scripts import two_site_quota_wait_storage as storage
    run=root/'.local/two-site-refactor/autopilot/runs'/original
    state={'reached':[]}
    operations={name:getattr(os,name) for name in ('write','fsync','link','close')}
    readback=storage._read_cycle_record_bytes
    def fd_path(fd):
        try: return os.readlink('/proc/self/fd/'+str(fd))
        except OSError: return ''
    def matches(fd):
        return fd_path(fd).startswith(str(run/('.'+filename+'.tmp.')))
    def write(fd,data):
        if stage=='write' and matches(fd):
            state['reached'].append(stage);raise OSError('injected '+stage)
        return operations['write'](fd,data)
    def fsync(fd):
        selected=(stage=='file_fsync' and matches(fd) or
                  stage=='directory_fsync' and fd_path(fd)==str(run) and (run/filename).exists() or
                  stage=='cleanup_directory_fsync' and fd_path(fd)==str(run) and
                  (run/filename).exists() and (run/filename).stat().st_nlink==1)
        if selected:
            state['reached'].append(stage);raise OSError('injected '+stage)
        return operations['fsync'](fd)
    def link(src,dst,*args,**kwargs):
        if stage=='link' and dst==filename:
            state['reached'].append(stage);raise OSError('injected '+stage)
        result=operations['link'](src,dst,*args,**kwargs)
        if stage=='link_after_effect' and dst==filename:
            state['reached'].append(stage);raise OSError('injected '+stage)
        return result
    def close(fd):
        selected=stage=='close' and matches(fd)
        result=operations['close'](fd)
        if selected:
            state['reached'].append(stage);raise OSError('injected '+stage)
        return result
    def read(*args,**kwargs):
        if stage in ('readback','readback_oserror') and args[2]==filename and (run/filename).exists():
            state['reached'].append(stage)
            error=OSError if stage=='readback_oserror' else storage.WaitStorageError
            raise error('injected '+stage)
        return readback(*args,**kwargs)
    with monkeypatch.context() as scoped:
        for name,callback in [('write',write),('fsync',fsync),('link',link),('close',close)]:
            scoped.setattr(storage.os,name,callback)
        scoped.setattr(storage,'_read_cycle_record_bytes',read)
        yield state


def _quota_history_ready_artifacts(sc,resume_req,test_path,candidate_runner,*,publish_terminal=True):
 """Synthetic external Worker/Reviewer outcomes; real files, SDK receipt and bindings."""
 root,task_path=sc['root'],sc['task_path']
 scope=sc['actual_scope'];cont_cid=resume_req['controller_run_id']
 resp_dt=datetime.fromisoformat(resume_req['claimed_at_utc'].replace('Z','+00:00'))+timedelta(seconds=1)
 _,cont_runs=_setup_cycle_root(str(root),cont_cid)
 _write_startup_json(cont_runs,cycle_id=cont_cid,source_head=sc['head'],assignment_sha256=scope.sha256)
 t_data,sources=load_task(root,task_path)
 props=root/'.local/two-site-refactor/proposals'
 w_run,r_run= (f'{resume_req["run_prefix"]}-worker-0',f'{resume_req["run_prefix"]}-reviewer-0')
 w_dir,r_dir= (props/w_run,props/r_run)
 for d in(w_dir,r_dir):
  d.mkdir(parents=True,exist_ok=True)
  os.chmod(d,448)
 t_rel,t_content= (test_path,'from source import VALUE\n\ndef test_source_value():\n    assert VALUE == 1\n')
 w_result= {'status':'PROPOSAL_READY','summary':'Characterize source.VALUE in test_new.py','covered_requirements': list(range(1,len(t_data['requirements'])+1)),'findings': [],'changes': [{'path':t_rel,'original_sha256':None,'content':t_content}]}
 w_txt=_cj(w_result)
 w_res_path=w_dir/'result.json'
 _w0600(w_res_path,w_txt)
 prop_sha=hashlib.sha256(w_txt.encode('utf-8')).hexdigest()
 _w0600(w_dir/'task.json',_cj({'source_head':sc['head'],'task':t_data,'sources':sources}))
 w_stat= {'run_id':w_run,'role':'worker','head':sc['head'],'model':expected_role_model('worker',t_data['task_id']),'state':'PROPOSAL_READY','task_id':t_data['task_id'],'result_sha256':prop_sha,'conversation_id':f'conv-worker-{cont_cid}'}
 w_stat_path=w_dir/'status.json'
 _w0600(w_stat_path,_cj(w_stat))
 receipt=candidate_runner(root,t_data,w_result,prop_sha,timeout=1200)
 if candidate_runner is not simulated_candidate_runner:
  assert receipt['isolation'] !=_SIMULATED_ISOLATION
 assert receipt['selectors'] == [t_rel]
 v_receipt=validate_test_receipt(receipt,prop_sha,require_passed=True)
 rec_path=w_dir/'pre-review-test-receipt.json'
 rec_bytes=_w0600(rec_path,_cj(v_receipt))
 rec_sha=hashlib.sha256(rec_bytes).hexdigest()
 r_result= {'status':'VERIFIED','summary':'Review verified proposal.','covered_requirements': list(range(1,len(t_data['requirements'])+1)),'findings': [],'changes': []}
 r_txt=_cj(r_result)
 r_res_path=r_dir/'result.json'
 _w0600(r_res_path,r_txt)
 r_sha=hashlib.sha256(r_txt.encode('utf-8')).hexdigest()
 _w0600(r_dir/'task.json',_cj({'source_head':sc['head'],'task':t_data,'sources':sources,'proposal':w_txt}))
 r_stat= {'run_id':r_run,'role':'verifier','head':sc['head'],'model':GEMINI_MODEL,'state':'VERIFIED','task_id':t_data['task_id'],'input_worker_run_id':w_run,'input_proposal_sha256':prop_sha,'input_test_evidence_sha256':rec_sha,'result_sha256':r_sha,'conversation_id':f'conv-reviewer-{cont_cid}'}
 r_stat_path=r_dir/'status.json'
 _w0600(r_stat_path,_cj(r_stat))
 seal_completion_fixture(root,w_dir)
 seal_completion_fixture(root,r_dir)
 for directory in (w_dir,r_dir):
  for filename in ('task.json','result.json','status.json','prompt.txt','stream.jsonl',
                   'stderr.txt','response-schema.json','transport-completion.json'):
   os.chmod(directory/filename,0o600)
 inner_ready= {'state':'READY_FOR_CODEX','task_id':t_data['task_id'],'source_head':sc['head'],'final_worker_run_id':w_run,'final_reviewer_run_id':r_run,'proposal_sha256':prop_sha,'changed_paths': [t_rel],'proposal_path':str(w_res_path.relative_to(root)),'reviewer_path':str(r_res_path.relative_to(root)),'correction_count':0,'blockers': [],'pre_review_test_path':str(rec_path.relative_to(root)),'pre_review_test_sha256':rec_sha,'pre_review_test_state':'PASSED','semantic_task_fingerprint':sc['identity']['fingerprint']}
 continuation_resp=build_continuation_broker_response(resume_req,inner_ready,now_epoch=resp_dt.timestamp())
 if publish_terminal:
  publish_terminal_continuation_record(root,resume_req,continuation_resp)
 return {'cont_runs':cont_runs,'t_rel':t_rel,'t_content':t_content,'w_run':w_run,'r_run':r_run,
         'prop_sha':prop_sha,'v_receipt':v_receipt,'w_stat_path':w_stat_path,'r_stat_path':r_stat_path,
         'ready':inner_ready,'response':continuation_resp}


def _retirement_case(repo,monkeypatch):
 from scripts import two_site_quota_retention as subject
 fx=_retirement_history(repo);root=fx['root'];authority=_retirement_seed_authority(fx)
 expected=authority['intent'];stamp=expected['certified_at_utc']
 monkeypatch.setattr(subject,'_now_utc',lambda:authority['stamp'])
 receipts=[root/row['relative_path'] for row in expected['collectible']]
 return fx,authority,root,expected,stamp,receipts


def _quota_effect_frame(frame,allowed):
 files,head,refs,status=frame
 parents={str(Path(path).parent) for path in allowed}
 projected={}
 for path,row in files.items():
  if path in allowed:continue
  projected[path]=row[:6] if path in parents and row[0] is None else row
 return projected,head,refs,status


def _retirement_public_inputs(repo):
 from scripts import two_site_provider_broker as broker
 from scripts import two_site_quota_continuation_terminal_storage as terminal
 fx=_retirement_history(repo,commit_change=False);root=fx['root']
 snapshot=json.loads((root/_RETIREMENT_B/'autopilot/runs'/_RETIREMENT_O/'quota-resume-lineage-snapshot.json').read_bytes())
 resume=snapshot['expected_request'];request=fx['request'];task=json.loads(fx['task_path'].read_bytes())
 qrec=broker.read_terminal_quota_record(root,request['request_id'])
 crec=terminal.read_terminal_continuation_record(root,resume['request_id'],expected_request=resume)
 clock=datetime.fromisoformat(resume['claimed_at_utc'].replace('Z','+00:00')).timestamp()+1
 return fx,root,resume,request,task,qrec,crec,clock


@contextmanager
def _quota_lock_fault(monkeypatch,root,kind):
 import fcntl
 from types import SimpleNamespace
 path=root/'.local/two-site-refactor/monitor/monitor.lock'
 backup=path.with_name('.owned-monitor-lock-backup');link=path.with_name('.owned-monitor-lock-link')
 touched=[]
 actual_fstat=os.fstat;actual_stat=os.stat
 try:
  with monkeypatch.context() as scoped:
   if kind=='losing_open':
    fd=os.open(path,os.O_WRONLY|os.O_TRUNC|os.O_NOFOLLOW)
    try:
     os.fchmod(fd,0o600)
     try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
     except BlockingIOError:touched.append(kind)
     else:raise AssertionError('existing real monitor flock not held')
    finally:os.close(fd)
   elif kind=='replacement':
    path.rename(backup);_w0600(path,b'');touched.append(kind)
   elif kind=='mode':
    os.chmod(path,0o644);touched.append(kind)
   elif kind=='hardlink':
    os.link(path,link);touched.append(kind)
   elif kind=='owner':
    def wrong(info):
     touched.append(kind)
     return SimpleNamespace(st_dev=info.st_dev,st_ino=info.st_ino,st_mode=info.st_mode,
        st_uid=info.st_uid+1,st_nlink=info.st_nlink)
    def fstat(fd):
     info=actual_fstat(fd)
     return wrong(info) if os.readlink('/proc/self/fd/'+str(fd))==str(path) else info
    def lstat(name,*args,**kwargs):
     info=actual_stat(name,*args,**kwargs)
     return wrong(info) if Path(name)==path else info
    scoped.setattr(os,'fstat',fstat);scoped.setattr(os,'stat',lstat)
   else:raise AssertionError('unknown lock fault')
   yield touched
 finally:
  if kind=='replacement':
   path.unlink();backup.rename(path)
  elif kind=='mode':os.chmod(path,0o600)
  elif kind=='hardlink':link.unlink()


def _quota_external_marker():
 class Reached(BaseException):pass
 observed=[]
 def external(*args,**kwargs):
  observed.append(True);raise Reached()
 return Reached,observed,external


@contextmanager
def _quota_receipt_unlink_trace(root,paths):
 from unittest.mock import patch
 from scripts import two_site_quota_retention as subject
 actual=os.unlink;effects=[]
 def track(name,*args,**kwargs):
  if name in {p.name for p in paths}:effects.append(name)
  return actual(name,*args,**kwargs)
 with patch.object(subject.os,'unlink',track):
  yield effects


def _quota_request_alias(request):
 return dict(request,request_id='e'*32,controller_run_id='cycle-0001-20261020T120000Z-999',
             run_prefix='alias-same-exact-semantic-task')


def _quota_unknown_case(repo,label,original,continuation,monkeypatch):
 from scripts.two_site_quota_wait_sources import collect_initial_wait_source_expected
 path='tests/test_'+label.replace('-','_')+'.py'
 lab=_bootstrap_quota_history_lab(repo,test_paths=(path,))
 root=lab['root']
 assert not os.path.lexists(root/'STOP') and not os.path.lexists(root/'.local/two-site-refactor/autopilot/STOP')
 from scripts import two_site_provider_broker as broker
 # Owned hermetic signal initialization; pytest restores incoming state, including older tests' True.
 monkeypatch.setattr(broker,'_STOP',False)
 fx=_append_quota_history(lab,label=label,original_cid=original,continuation_cid=continuation,
     test_path=path,candidate_runner=simulated_candidate_runner,finalize=False,unknown=True)
 root=lab['root'];state=fx['autopilot_dir'];run=state/'runs'/original
 expected=collect_initial_wait_source_expected(root,original,fx['intent_epoch'],fx['intent_attempt'])
 stamp=(datetime.fromisoformat(fx['wait']['schedule']['created_at_utc'].replace('Z','+00:00'))+
        timedelta(minutes=16)).strftime('%Y-%m-%dT%H:%M:%SZ')
 semantic=root/'.local/two-site-refactor/pipeline/semantic-attempts'/(fx['wait']['semantic_task_fingerprint']+'.json')
 immutable={p:p.read_bytes() for p in (run/'quota-wait.json',run/'broker-reservation.json',
             run/'preparation-startup.json',semantic)}
 return lab,fx,root,state,run,expected,stamp,immutable


@contextmanager
def _quota_fifo_swap(monkeypatch,path):
 actual=os.open;backup=path.with_name('.owned-before-fifo');seen=[];fds=[]
 def swap(name,flags,*args,**kwargs):
  selected=str(name) in (str(path),path.name) and not seen
  if selected:
   seen.append(flags)
   if not flags & os.O_NONBLOCK:raise AssertionError('new reader open can block on FIFO')
   path.rename(backup);os.mkfifo(path,0o600)
  fd=actual(name,flags,*args,**kwargs)
  if selected:fds.append(fd)
  return fd
 try:
  with monkeypatch.context() as scoped:
   scoped.setattr(os,'open',swap)
   yield seen,fds
 finally:
  if backup.exists():
   path.unlink();backup.rename(path)


def _frame(root):
    files={}
    for path in sorted((root/'.local/two-site-refactor').rglob('*')):
        st=path.lstat()
        if path.name.endswith('.lock') or path.relative_to(root).as_posix()=='.local/two-site-refactor/provider-broker/status.json': continue
        payload=(os.readlink(path) if stat.S_ISLNK(st.st_mode) else
                 path.read_bytes() if stat.S_ISREG(st.st_mode) else None)
        if path.relative_to(root).as_posix()=='.local/two-site-refactor/provider-broker':
            files[path.relative_to(root).as_posix()]=(None,st.st_dev,st.st_ino,st.st_mode,st.st_uid,st.st_nlink)
            continue
        files[path.relative_to(root).as_posix()]=(payload,st.st_dev,st.st_ino,st.st_mode,
              st.st_uid,st.st_nlink,st.st_size,st.st_mtime_ns,st.st_ctime_ns)
    return (files,_git(root,'rev-parse','HEAD'),
            _git(root,'show-ref'),_git(root,'status','--porcelain=v1','--untracked-files=all'))


def _quota_forbid_downstream(monkeypatch):
 from scripts import two_site_codex_autopilot as ap
 from scripts import two_site_provider_broker as broker
 from scripts import two_site_quota_broker_route as route
 from scripts import two_site_quota_supervisor_resume_submit as submit
 observed=[]
 def forbidden(*args,**kwargs):
  observed.append(True);raise AssertionError('UNKNOWN reached an execution boundary')
 for module,name in ((ap,'run_cycle'),(broker,'execute_pipeline'),
       (route,'execute_intent_bound_continuation'),(submit,'submit_known_reset_resume_once_locked')):
  monkeypatch.setattr(module,name,forbidden)
 return observed



def _quota_unknown_launch(root,stamp):
 import sys,time
 from unittest.mock import patch
 from scripts import two_site_codex_autopilot as ap
 instant=datetime.fromisoformat(stamp.replace('Z','+00:00')).timestamp()
 actual=time.gmtime
 with patch.object(time,'gmtime',lambda seconds=None:actual(instant if seconds is None else seconds)):
  return ap.run_loop(root,Path(sys.executable),1,300,True,
                     preparation_route='one_shot',quota_wait_enabled=True)


def _quota_standing_external_outcomes(monkeypatch,scenario,test_path,observe):
 from scripts import two_site_quota_resume_role_dispatch as dispatch
 from scripts import two_site_proposal_runner as runner
 from scripts import two_site_proposal_pipeline as pipeline
 produced={};seen=[]
 def worker(root,request,cli):
  observe(root)
  produced.update(_quota_history_ready_artifacts(scenario,request,test_path,
                   simulated_candidate_runner,publish_terminal=False))
  ready=produced['ready']
  r_status_path=produced['r_stat_path'];r_dir=r_status_path.parent
  original_receipt_path=root/ready['pre_review_test_path']
  receipt=validate_test_receipt(json.loads(original_receipt_path.read_bytes()),produced['prop_sha'],require_passed=True)
  assert request['role']=='worker' and type(ready['correction_count']) is int and ready['correction_count']==0
  assert ready['final_worker_run_id']==request['run_prefix']+'-worker-0'
  assert ready['final_reviewer_run_id']==request['run_prefix']+'-reviewer-0'
  assert receipt['proposal_sha256']==ready['proposal_sha256']==produced['prop_sha']
  assert receipt['selectors']==[test_path] and ready['pre_review_test_state']==receipt['state']=='PASSED'
  evidence_dir=root/'.local/two-site-refactor/pipeline'/request['run_prefix']
  assert evidence_dir.parent.is_dir() and not os.path.lexists(evidence_dir)
  evidence_dir.mkdir(mode=0o700)
  os.chmod(evidence_dir,0o700)
  receipt_path=evidence_dir/'pre-review-tests-0.json'
  assert not os.path.lexists(receipt_path) and not os.path.lexists(receipt_path.with_suffix('.json.tmp'))
  runner.write_json(receipt_path,receipt)
  os.chmod(receipt_path,0o600)
  receipt_bytes=receipt_path.read_bytes()
  assert receipt_bytes==runner.serialize_json(receipt)
  assert stat.S_IMODE(evidence_dir.stat().st_mode)==0o700 and stat.S_IMODE(receipt_path.stat().st_mode)==0o600
  receipt_sha=hashlib.sha256(receipt_bytes).hexdigest()
  snapshot=json.loads((r_dir/'task.json').read_bytes())
  status=json.loads(r_status_path.read_bytes())
  worker_status=json.loads(produced['w_stat_path'].read_bytes())
  snapshot['test_evidence']=receipt
  status['input_test_evidence_sha256']=receipt_sha
  status['input_worker_conversation_id']=worker_status['conversation_id']
  ready['pre_review_test_path']=receipt_path.relative_to(root).as_posix()
  ready['pre_review_test_sha256']=receipt_sha
  produced['response']=build_continuation_broker_response(request,ready,
                          now_epoch=produced['response']['finished_at'])
  _w0600(r_dir/'task.json',runner.serialize_json(snapshot))
  _w0600(r_status_path,runner.serialize_json(status))
  seal_completion_fixture(root,r_dir)
  for filename in ('task.json','result.json','status.json','prompt.txt','stream.jsonl',
                   'stderr.txt','response-schema.json','transport-completion.json'):
   os.chmod(r_dir/filename,0o600)
  assert receipt_sha==runner.sha(runner.serialize_json(snapshot['test_evidence']))
  assert receipt_path.read_bytes()==receipt_bytes
  assert pipeline._load_verifier_pre_review_receipt(root,dict(ready,original_run_prefix=request['run_prefix']))==receipt
  seen.append('external-worker')
  result={key:ready[key] for key in dispatch._EXPECTED_RESULT_STATE_KEYS}
  result['state']='WORKER_PROPOSAL_READY'
  return result
 def reviewer(root,request,intermediate,cli):
  observe(root);seen.append('external-reviewer');return produced['ready']
 monkeypatch.setattr(dispatch,'execute_worker_zero_once',worker)
 monkeypatch.setattr(dispatch,'execute_worker_zero_post_result_remainder',reviewer)
 return produced,seen


def _recover(root):
    from scripts import two_site_codex_autopilot as ap
    from scripts.two_site_quota_wait_recovery import recover_durable_initial_wait_locked
    with ap.exclusive_supervisor_lock(root/'.local/two-site-refactor/autopilot/autopilot.lock'):
        return recover_durable_initial_wait_locked(root)



def _restore(path,old):
    path.write_bytes(old[0])
    os.chmod(path,stat.S_IMODE(old[1]))
    os.utime(path,ns=(old[2],old[3]))



def _quota_revalidated_history(lab):
 """Only the late two-history fixture branch, after deliberate restored faults.

 Original constructor fingerprints stay untouched. Real history validation
 authenticates each ctor-captured CID/ACK before a new current custody frame.
 """
 from scripts.two_site_quota_retention import read_final_ack_history_locked
 root=lab['root']
 assert len(lab['acknowledged'])==2
 with exclusive_supervisor_lock(root/'.local/two-site-refactor/autopilot/autopilot.lock'):
  for cid,expected_ack in lab['acknowledged']:
   actual=read_final_ack_history_locked(root,cid)
   assert actual['state']=='ACKNOWLEDGED' and _cj(actual['ack'])==expected_ack
  return _history_custody(root)


def _quota_frame_abi(monkeypatch,root,state,repo,stamp,downstream):
 """Reached UNKNOWN normalized-frame negatives, then restore the healthy owner."""
 import copy,sys
 from scripts import two_site_codex_autopilot as ap
 from scripts import two_site_quota_wait_intent as intent
 from scripts import two_site_quota_supervisor_bridge as bridge
 from scripts import two_site_quota_wait_recovery,two_site_quota_supervisor_due_inspect
 status=state/'status.json';saved=status.read_bytes();mode=stat.S_IMODE(status.stat().st_mode)
 actual_reader=intent.read_pending_rescope_intent
 instant=datetime.fromisoformat(stamp.replace('Z','+00:00')).timestamp()
 gmtime=time.gmtime
 with exclusive_supervisor_lock(state/'autopilot.lock'):
  epoch,attempt,frame=actual_reader(root)
  assert type(frame['assignment_scope']) is dict
  assert type(repo['assignment_scope']) is not dict
  assert frame==ap.validate_accepted_repository(repo) and frame!=repo
  for field in ('assignment_scope','protected_refs','control_blobs'):
   bad=copy.deepcopy(frame)
   if field=='assignment_scope':bad[field]['sha256']='0'*64
   else:bad[field][next(iter(bad[field]))]='0'*40
   reached=[]
   def injected(root_arg):
    assert root_arg==root and actual_reader(root_arg)==(epoch,attempt,frame)
    reached.append(field);return epoch,attempt,bad
   before=_frame(root)
   try:
    with monkeypatch.context() as scoped:
     scoped.setattr(intent,'read_pending_rescope_intent',injected)
     scoped.setattr(time,'gmtime',lambda seconds=None:gmtime(instant if seconds is None else seconds))
     assert bridge.resume_due_quota_cycle_locked(root,Path(sys.executable),state,repo,300,'one_shot')==2
    observed=json.loads(status.read_bytes())
    assert reached==[field] and observed['state']=='BLOCKED'
    assert observed['head']==repo['head'] and observed['ambiguous_change'] is True
    assert observed['blockers']==['quota resume hard stop: QuotaSupervisorBridgeError: repository drift before UNKNOWN health check']
    allowed={status.relative_to(root).as_posix()}
    assert _quota_effect_frame(_frame(root),allowed)==_quota_effect_frame(before,allowed)
    assert not list((state/'runs').glob('*/quota-unknown-health-*.json'))
    assert not list((state/'runs').glob('*/quota-resume-claim.json')) and downstream==[]
   finally:
    status.write_bytes(saved);os.chmod(status,mode)
   assert intent.read_pending_rescope_intent is actual_reader
   assert actual_reader(root)==(epoch,attempt,frame) and ap.validate_repository(root)==repo
 assert status.read_bytes()==saved and downstream==[]


def _status_abi(root,fx):
 """Real producer status ABI, never a mode waiver for immutable actor evidence."""
 from scripts import two_site_codex_autopilot as ap
 from scripts import two_site_quota_retention as retention
 state=root/'.local/two-site-refactor/autopilot'
 ap.write_supervisor_state(state,'STOPPED',cycle=1,head=fx['child_head'],blockers=[])
 status=state/'status.json';raw=status.read_bytes();mode=stat.S_IMODE(status.stat().st_mode)
 assert mode in (0o600,0o644)
 def read(path):
  with retention._Pins() as pins:
   return pins.read(root,path.relative_to(root).as_posix(),187000,canonical=False)[0]
 try:
  for good in (0o600,0o644):
   os.chmod(status,good);before=_frame(root)
   assert read(status)==raw and _frame(root)==before
  os.chmod(status,0o622);before=_frame(root)
  with pytest.raises(retention.QuotaRetentionError,match='unsafe'):
   read(status)
  assert _frame(root)==before
 finally:os.chmod(status,mode)
 actor=fx['worker_status_path'];actor_raw=actor.read_bytes();actor_mode=stat.S_IMODE(actor.stat().st_mode)
 assert actor_mode==0o600
 try:
  os.chmod(actor,0o644);before=_frame(root)
  with pytest.raises(retention.QuotaRetentionError,match='0600'):
   read(actor)
  assert _frame(root)==before
 finally:os.chmod(actor,actor_mode)
 before=_frame(root)
 assert read(actor)==actor_raw and read(status)==raw and _frame(root)==before


def _quota_cold_base_intent(root,raw,bad):
 """Owned cold-fault projection: preserve release/preaction/ACK, derive real Scope."""
 from scripts.two_site_assignment_scope import load_active_assignment_scope
 value=json.loads(raw)
 scope=load_active_assignment_scope(root)
 authority=value['retirement_authority']
 assert scope.assignment_id==authority['assignment_id']
 path=root/'.local/two-site-refactor/assignments'/(scope.assignment_id+'.md')
 assert path.read_bytes()==bad
 authority['assignment_sha256']=scope.sha256
 row=next(row for row in value['retained'] if row['role']=='RETIREMENT_ASSIGNMENT')
 assert row['relative_path']==path.relative_to(root).as_posix()
 row['sha256']=_retirement_digest(bad)
 return value


@contextmanager
def _quota_base_guard(subject):
 """Reached forwarding spy; the real base predicate and error remain unchanged."""
 from unittest.mock import patch
 actual=subject._validate_maintenance_base
 reached=[]
 def forwarding(raw,assignment_id,head):
  reached.append(raw)
  return actual(raw,assignment_id,head)
 try:
  with patch.object(subject,'_validate_maintenance_base',forwarding):
   yield reached
 finally:
  assert subject._validate_maintenance_base is actual
