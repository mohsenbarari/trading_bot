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
