"""Native HOST bwrap/FIFO, full SDK snapshot and explicit disposable Git identity.
Only outer /work uid65534 adapts namespaces; only tool-free Provider is faked.
No fake reports/output/receipts/PASSED. Root owns genuine Gemini review.
"""
from __future__ import annotations
from collections import UserDict
import copy
import hashlib
import inspect
import json
import os
from pathlib import Path
import shutil
import subprocess
import pytest
from scripts import two_site_agent_sandbox as sdk
from scripts import two_site_pre_review_receipt as m
from scripts import two_site_proposal_pipeline as p
from scripts import two_site_proposal_runner as r
from tests.test_two_site_agent_sandbox_overlay import create_test_repo,install_native_namespace_adapter
raises=pytest.raises
clone=copy.deepcopy
SE=sdk.SandboxError
file_sha=r.file_sha256
save=r.write_json
readbytes=r.bounded_file_bytes
parse=r.unique_json
validate=m.validate_test_receipt
facts=p.test_failure_facts
sig=p.test_failure_signature
branch=p._branch_root_failure_factor
gate=p.test_gate_feedback
RS,S,RC,PS,TS='records','state','returncode','proposal_sha256','selectors'
PATH='tests/test_existing.py'
N,F,O,X,A,Q=('nodeid','phase','outcome','exception','assertion','partial')
D,E,V=('pytest_diagnostics','pytest_diagnostics_error','receipt_version')
ROOT=Path(sdk.__file__).resolve().parents[1]
TEN={S,PS,TS,'unsupported_specs',RC,'stdout_tail','stderr_tail','snapshot_files','snapshot_bytes','isolation'}
V2=TEN|{V,D,E}
SHAPE="Module(body=[Assert(test=Compare(left=Name(id='_', ctx=Load()), ops=[Eq()], comparators=[Constant(value=1)]))], type_ignores=[])"
BRANCH='scripts.two_site_codex_autopilot.AutopilotError: autopilot requires branch refactor/two-site-architecture'
CAUSE="import warnings\ndef test_subject():\n  print('FAILURES\\nE scripts.two_site_codex_autopilot.AutopilotError: autopilot requires branch refactor/two-site-architecture')\n  warnings.warn('private-warning-sentinel')\n  observed = 0\n  assert observed == 1, 'private-assertion-sentinel'\n"
BAD_CHANNEL="import os\ndef test_subject():\n    fd=os.open(os.environ['TWO_SITE_PYTEST_DIAGNOSTICS_FIFO'],os.O_WRONLY|os.O_NONBLOCK)\n    os.write(fd,b'{')\n    os.close(fd)\n    raise ValueError('private')\n"
FACTS={'tests':['call: tests/test_existing.py::test_subject'],'exceptions':['builtins.AssertionError'],'statements':[SHAPE]}

@pytest.fixture(autouse=True)
def native_route(monkeypatch):
  return install_native_namespace_adapter(monkeypatch)

@pytest.fixture
def repo(tmp_path):
  value=create_test_repo(tmp_path)
  assert value==tmp_path/'repo'
  try: yield value
  finally: shutil.rmtree(value)

def native(rt,src=CAUSE,sha='a'*64,*,enabled=True):
  worker={'changes':[{'path':PATH,'original_sha256':file_sha(rt/PATH),'content':src}]}
  return m.run_candidate_tests(rt,{'test_selectors':[PATH]},worker,sha,timeout=30,structured_diagnostics=enabled)

@pytest.fixture
def obs(repo):
  value=native(repo)
  assert value[S]=='FAILED' and value[RC]==1
  assert value[E]is None
  assert value[D]=={'version':1,Q:False,RS:[{N:'tests/test_existing.py::test_subject',F:'call',O:'failed',X:'builtins.AssertionError',A:SHAPE,Q:False}]}
  return value

def test_public_api(repo,monkeypatch):
  real,calls=(sdk.run_tests,[])
  lost=()

  def record(*args,**kwargs):
    calls.append(kwargs.copy())
    result=real(*args,**kwargs)
    for key in lost: result.pop(key)
    return result
  monkeypatch.setattr(sdk,'run_tests',record)
  legacy=native(repo,enabled=False)
  assert set(legacy)==TEN==m.EXPECTED_RECEIPT_KEYS
  assert 'structured_diagnostics' not in calls[-1]
  assert validate(UserDict(legacy),'a'*64)==legacy
  value=native(repo)
  assert set(value)==V2 and type(value[V])is int and(value[V]==2)
  assert calls[-1]['structured_diagnostics']is True
  lost=(D,E)
  absent=native(repo)
  assert set(absent)==V2 and absent[D]is None and absent[E]=='diagnostic unavailable or invalid'
  assert facts(absent)=={'tests':[],'exceptions':['UNKNOWN'],'statements':[]}
  lost=(D,)
  with raises(SE,match='asymmetric'): native(repo)
  lost=()
  assert p.run_candidate_tests is m._run_candidate_tests_structured
  assert list(inspect.signature(p.run_candidate_tests).parameters)==['root','task','worker_result',PS,'timeout']
  for invalid in(None,0,1,'true',[]):
    count=len(calls)
    with raises(SE,match='boolean'):
      m.run_candidate_tests(repo,{},{},'a'*64,structured_diagnostics=invalid)
    assert len(calls)==count
  for src,isolation in(('x'*24001,'static-byte-budget'),('def test_a():\n    assert True \n','static-new-test-whitespace')):
    count=len(calls)
    static=p.run_candidate_tests(repo,{'test_selectors':['tests/test_new.py']},{'changes':[{'path':'tests/test_new.py','original_sha256':None,'content':src}]},'b'*64)
    assert set(static)==TEN and static['isolation']==isolation and(static[RC]==1)
    assert len(calls)==count

def test_privacy(repo,obs):
  assert facts(obs)==FACTS
  feedback=gate(obs,'a'*64)
  encoded=json.dumps(feedback)
  assert 'private-warning-sentinel' in obs['stdout_tail']
  assert 'FAILURES' in obs['stdout_tail']
  assert 'private' not in encoded and 'FAILURES' not in encoded and(BRANCH not in encoded)
  assert branch(obs)is None
  renamed=native(repo,'\n\n'+CAUSE.replace('observed','renamed').replace("warnings.warn('private-warning-sentinel')","warnings.warn('different-noise'*2000)"),'b'*64)
  assert len(renamed['stdout_tail'].encode())<=8192 and 'assert renamed == 1' not in renamed['stdout_tail']
  assert facts(renamed)==FACTS
  assert sig(renamed)==sig(obs)
  changed=native(repo,CAUSE.replace('== 1','== 2'),'c'*64)
  assert sig(changed)!=sig(obs)
  assert sig(obs)=='70747c9991a95ceb7b364a08c65e1307f1b09310b08291fb8802732252b06665'
  with raises(SE,match='requires returncode 0'):
    validate(dict(obs,state='PASSED'),'a'*64)
  with raises(SE,match='require_passed'):
    validate(obs,'a'*64,require_passed=True)
  passed=native(repo,'def test_subject():\n    assert True\n')
  assert passed[S]=='PASSED' and passed[RC]==0
  assert passed[D]=={'version':1,RS:[],Q:False}
  assert validate(passed,'a'*64,require_passed=True)==passed
  with raises(p.ProposalError,match='FAILED'):
    gate(passed,'a'*64)

def test_associations(repo,monkeypatch):
  first=native(repo,"def test_a():\n    raise ValueError('private')\ndef test_b():\n    raise RuntimeError('private')\n")
  crossed=native(repo,"def test_a():\n    raise RuntimeError('private')\ndef test_b():\n    raise ValueError('private')\n",'b'*64)
  assert facts(first)==facts(crossed)

  def witness():
    assert sig(first)!=sig(crossed)
  witness()
  real=p._structured_failure_records

  def flatten(value):
    records,available,complete=real(value)
    output=clone(records)
    output.sort(key=lambda record:record[N])
    for record,exc in zip(output,sorted((r[X]for r in output))):
      record[X]=exc
    return(output,available,complete)
  with monkeypatch.context()as fault:
    fault.setattr(p,'_structured_failure_records',flatten)
    with raises(AssertionError):
      witness()
  witness()
  setup=native(repo,"import pytest\n@pytest.fixture\ndef broken():\n    raise ValueError('private')\ndef test_subject(broken): pass\n")
  teardown=native(repo,"import pytest\n@pytest.fixture\ndef broken():\n    yield\n    raise ValueError('private')\ndef test_subject(broken): pass\n")
  assert facts(setup)['tests']==['setup: tests/test_existing.py::test_subject']
  assert facts(teardown)['tests']==['teardown: tests/test_existing.py::test_subject']
  assert sig(setup)!=sig(teardown)
  collect=native(repo,"raise ValueError('private collect')\n")
  assert collect[RC]==2
  assert facts(collect)=={'tests':['collect: tests/test_existing.py'],'exceptions':['builtins.ValueError'],'statements':[]}

@pytest.mark.parametrize('kind',[Q,'absent','invalid','overflow','empty'])
def test_unknown(repo,kind):
  sources={Q:"class Unknown(Exception): pass\ndef test_subject():\n    print('E private-forged')\n    raise Unknown('private')\n",'absent':'import os\ndef test_subject():\n    os._exit(1)\n','invalid':BAD_CHANNEL,'overflow':"import os,time\ndef test_subject():\n    fd=os.open(os.environ['TWO_SITE_PYTEST_DIAGNOSTICS_FIFO'],os.O_WRONLY|os.O_NONBLOCK)\n    os.write(fd,b'x'*8192)\n    time.sleep(.2)\n    os.write(fd,b'y')\n    os.close(fd)\n    raise ValueError('private')\n",'empty':'# no tests\n'}
  result=native(repo,sources[kind])
  assert result[S]=='FAILED' and result[RC]!=0
  if kind in('absent','invalid','overflow'):
    assert result[D]is None
    assert result[E]in{'diagnostic unavailable or invalid','diagnostic overflow'}
    assert facts(result)=={'tests':[],'exceptions':['UNKNOWN'],'statements':[]}
  else:
    assert result[E]is None
    assert 'UNKNOWN' in facts(result)['exceptions']
  feedback=gate(result,'a'*64)
  assert 'UNKNOWN' in json.dumps(feedback)and 'private' not in json.dumps(feedback)
  other=clone(result)
  other[PS]='b'*64
  assert sig(result)!=sig(other)

def test_schema(obs,monkeypatch):
  mutations=[(V,True),(V,1),(V,2.0),(E,'private arbitrary prose'),(E,'x'*100000),(E,'diagnostic overflow'),(D,None)]
  for key,value in mutations:
    bad=clone(obs)
    bad[key]=value
    with raises(SE):
      validate(bad,'a'*64)
  for key,value in(('version',True),('version',2),(Q,1),(RS,{})):
    bad=clone(obs)
    bad[D][key]=value
    with raises(SE):
      validate(bad,'a'*64)
  for key,value in((N,'tests/../test_x.py'),(N,'tests/test_\ud800.py'),(F,'unknown'),(O,'passed'),(Q,0),(X,'private.TypeError'),(X,None),(A,SHAPE.replace("id='_'","id='private'")),(A,SHAPE.replace('Constant(value=1)',"Constant(value='private')"))):
    bad=clone(obs)
    bad[D][RS][0][key]=value
    with raises(SE):
      validate(bad,'a'*64)
  for target in('root','record','mixed','extra'):
    bad=clone(obs)
    if target=='root':
      bad[D]['extra']=1
    elif target=='record':
      bad[D][RS][0]['extra']=1
    elif target=='mixed':
      del bad[E]
    else:
      bad['extra']=1
    with raises(SE):
      validate(bad,'a'*64)
  duplicate=r.serialize_json(obs).decode().replace('"receipt_version": 2','"receipt_version": 2,"receipt_version": 2')
  with raises(r.ProposalError):
    parse(duplicate)
  cycle=clone(obs)
  cycle[D][RS].append(cycle[D])
  with raises(SE):
    validate(cycle,'a'*64)
  large=clone(obs)
  record=large[D][RS][0]
  record[N]='tests/test_'+'x'*450+'.py::test_subject'
  large[D][RS]=[clone(record)for _ in range(16)]
  calls,real=([],m.json.dumps)

  def serialization(*args,**kwargs):
    calls.append(True)
    return real(*args,**kwargs)
  with monkeypatch.context()as scope:
    scope.setattr(m.json,'dumps',serialization)
    with raises(SE,match='8192'):
      validate(large,'a'*64)
  assert calls==[]

def test_callbacks(obs,monkeypatch):
  calls=[]

  def callback(*args,**kwargs):
    calls.append(True)
    raise AssertionError('unexpected callback')

  class Text(str):
    encode=__eq__=__ne__=__hash__=callback

  class Dictionary(dict):
    keys=items=get=__getitem__=__iter__=callback

  class Sequence(list):
    __iter__=__getitem__=callback
  for key,value in((D,Dictionary(obs[D])),('stdout_tail',Text('private')),(E,Text('private'))):
    bad=clone(obs)
    bad[key]=value
    with raises(SE):
      validate(bad,'a'*64)
  for field in(RS,'record',X):
    bad=clone(obs)
    data=bad[D]
    if field==RS:
      data[RS]=Sequence(data[RS])
    elif field=='record':
      data[RS][0]=Dictionary(data[RS][0])
    else:
      data[RS][0][X]=Text('builtins.AssertionError')
    with raises(SE):
      validate(bad,'a'*64)
  with raises(SE):
    validate(obs,Text('a'*64))
  with raises(SE):
    validate(UserDict(obs),'a'*64)
  armed=False
  class Key(str):
    __hash__=lambda self:hash(PS)
    __eq__=lambda self,other:callback()if armed else str.__eq__(self,other)
  class Box:
    __class__=property(callback)
  bad={Key('collision'):None,**clone(obs)}
  armed=True
  for value in(bad,dict(obs,proposal_sha256=Box())):
    for consumer in(facts,sig,branch):
      with raises(SE): consumer(value)
  assert calls==[]

  def witness():
    old=clone(obs)
    norm=validate(old,'a'*64)
    norm[TS].append('tests/test_extra.py')
    norm[D][RS][0][X]='builtins.ValueError'
    norm[D][RS].append({})
    assert old[TS]==[PATH]
    assert old[D][RS]==[{N:'tests/test_existing.py::test_subject',F:'call',O:'failed',X:'builtins.AssertionError',A:SHAPE,Q:False}]
  witness()
  real=m._screen_diagnostics

  def alias(artifact,error):
    real(artifact,error)
    return artifact
  with monkeypatch.context()as fault:
    fault.setattr(m,'_screen_diagnostics',alias)
    with raises(AssertionError):
      witness()
  witness()

def test_no_raw(obs,monkeypatch):
  original_facts=p.test_failure_facts

  def witness():
    value=dict(obs,isolation='static-byte-profile')
    encoded=json.dumps(gate(value,'a'*64))
    assert 'private' not in encoded and 'FAILURES' not in encoded and(BRANCH not in encoded)
  witness()

  def console(value):
    legacy={key:value[key]for key in TEN}
    return original_facts(legacy)
  with monkeypatch.context()as fault:
    fault.setattr(p,'test_failure_facts',console)
    with raises(AssertionError):
      witness()
  witness()
  legacy={key:obs[key]for key in TEN}
  assert BRANCH in json.dumps(gate(legacy,'a'*64))
  assert branch(legacy)is not None

@pytest.fixture
def full(repo):
  if ROOT==Path('/work')and os.geteuid()==65534:
    shutil.copytree(ROOT,repo,dirs_exist_ok=True,ignore=shutil.ignore_patterns('.git'))
  else:
    for name in sdk.source_files(ROOT,[]):
      dest=repo/name
      dest.parent.mkdir(parents=True,exist_ok=True)
      shutil.copyfile(ROOT/name,dest)
  assert(repo/'scripts/two_site_codex_autopilot.py').read_bytes()==(ROOT/'scripts/two_site_codex_autopilot.py').read_bytes()
  (repo/'.gitignore').write_text('.local/\n__pycache__/\n.pytest_cache/\n',encoding='utf-8')
  git_fixture(repo,'add','--all')
  git_fixture(repo,'commit','-qm','full tracked canonical SDK snapshot')
  git_fixture(repo,'checkout','-qb',p.BRANCH)
  assert git_fixture(repo,'config','--get','user.name')=='Consumer Fixture'
  assert git_fixture(repo,'config','--get','user.email')=='consumer-fixture@test.invalid'
  return repo

def git_fixture(rt,*args):
  for key,value in(('user.name','Consumer Fixture'),('user.email','consumer-fixture@test.invalid')):
    subprocess.run(['git','-C',str(rt),'config',key,value],check=True,capture_output=True)
  result=subprocess.run(['git','-C',str(rt),*args],check=True,capture_output=True,text=True)
  return result.stdout.strip()

def test_branch(full,monkeypatch):
  src="from scripts.two_site_codex_autopilot import AutopilotError\ndef test_subject():\n    raise AutopilotError('autopilot requires branch refactor/two-site-architecture')\n"
  value=native(full,src)
  assert facts(value)=={'tests':['call: tests/test_existing.py::test_subject'],'exceptions':[BRANCH],'statements':[]}
  oracle=b'{"branch":"refactor/two-site-architecture","factor":"repository_branch","selectors":["tests/test_existing.py"]}'
  assert branch(value)==hashlib.sha256(oracle).hexdigest()
  wrong=native(full,src.replace('autopilot requires branch refactor/two-site-architecture','private wrong message'))
  assert branch(wrong)is None
  assert facts(wrong)['exceptions']==['scripts.two_site_codex_autopilot.AutopilotError']

  def witness():
    real=native(full,src)
    assert branch(real)==hashlib.sha256(oracle).hexdigest()
  witness()
  fds,create,read=([],sdk._DiagnosticFIFO.__init__,sdk.os.read)

  def created(channel):
    create(channel)
    fds.append(channel.reader)

  def fail(fd,size):
    if fds and fd==fds[-1]:
      raise OSError('injected reader fault')
    return read(fd,size)
  with monkeypatch.context()as fault:
    fault.setattr(sdk._DiagnosticFIFO,'__init__',created)
    fault.setattr(sdk.os,'read',fail)
    with raises(AssertionError):
      witness()
  witness()

def test_persistence(repo,monkeypatch):
  ev=repo/'.local/two-site-refactor/pipeline/persist'
  ev.mkdir(parents=True)
  worker={'changes':[{'path':PATH,'content':CAUSE,'original_sha256':file_sha(repo/PATH)}]}
  serial=0

  def witness():
    nonlocal serial
    location=ev/str(serial)
    serial+=1
    location.mkdir()
    state,checked=({},[])
    value,path,sha=p.persist_pre_review_tests(repo,location,{'test_selectors':[PATH]},worker,'a'*64,0,lambda:checked.append(True),state)
    raw=readbytes(repo,path,r.MAX_CONTEXT,'proof')
    assert hashlib.sha256(raw).hexdigest()==sha==state['pre_review_test_sha256']
    assert state['pre_review_test_path']==path.relative_to(repo).as_posix()
    assert state['pre_review_test_state']=='FAILED' and checked==[True]
    reread=validate(parse(raw.decode()),'a'*64)
    assert set(reread)==V2 and reread==value
    assert facts(reread)==FACTS
    assert 'private' not in json.dumps(gate(reread,'a'*64))
  witness()
  with monkeypatch.context()as fault:
    fault.setattr(p,'run_candidate_tests',m.run_candidate_tests)
    with raises(AssertionError):
      witness()
  witness()
  write=p.write_json

  def drop(path,value):
    altered=clone(value)
    if path.name.startswith('pre-review-tests-'):
      del altered[D]
    write(path,altered)
  with monkeypatch.context()as fault:
    fault.setattr(p,'write_json',drop)
    with raises(p.ProposalError,match='byte mismatch'):
      witness()
  witness()

@pytest.mark.parametrize('scenario',['corrected','recurrence','budget','branch','unknown'])
def test_remainder(full,monkeypatch,scenario):
  rt=full
  head,refs=(p.git(rt,'rev-parse','HEAD'),p.ref_snapshot(rt))
  prefix='whole-'+scenario
  ev=rt/'.local/two-site-refactor/pipeline'/prefix
  ev.mkdir(parents=True)
  tp=rt/'.local/task.json'
  save(tp,{'task_id':'CONSUMER-FIXTURE','goal':'Native correction proof','context_files':[PATH],'allowed_files':[PATH],'requirements':['R1'],'test_selectors':[PATH]})
  task,sources=p.load_task(rt,tp)
  s0=CAUSE
  if scenario=='unknown':
    s0="class Unknown(Exception): pass\ndef test_subject():\n    raise Unknown('private')\n"
  if scenario=='branch':
    s0="from scripts.two_site_codex_autopilot import AutopilotError\ndef test_a():\n    raise AutopilotError('autopilot requires branch refactor/two-site-architecture')\n"
  s1=BAD_CHANNEL.replace("raise ValueError('private')",'assert True') if scenario=='corrected' else s0+'# distinct unknown proposal\n' if scenario=='unknown' else CAUSE.replace('observed','renamed')if scenario=='recurrence' else CAUSE.replace('== 1','== 2')if scenario=='budget' else s0.replace('test_a','test_b')
  contents=[s0,s1,CAUSE.replace('== 1','== 3')]
  if scenario=='unknown':
    contents[2]='def test_subject():\n    assert True\n'
  calls=[]

  def local_provider(_root,_task,role,run_id,timeout,feedback=None,proposal=None,cli=None,test_evidence=None):
    calls.append((role,run_id,feedback,proposal,test_evidence))
    output=rt/'.local/two-site-refactor/proposals'/run_id
    output.mkdir(parents=True)
    index=int(run_id.rsplit('-',1)[-1])
    result={'status':'PROPOSAL_READY' if role=='worker' else 'VERIFIED','summary':'Local tool-free Provider fixture','covered_requirements':[1],'findings':[],'changes':[{'path':PATH,'original_sha256':sources[PATH]['sha256'],'content':contents[index]}]if role=='worker' else[]}
    save(output/'result.json',result)
    status={'run_id':run_id,'role':role,'head':head,'model':p.expected_role_model(role,task['task_id']),S:result['status'],'conversation_id':'fixture-'+run_id,'result_sha256':file_sha(output/'result.json')}
    snap={'task':task,'sources':sources,'source_head':head}
    if proposal is not None:
      prior=parse((proposal.parent/'status.json').read_text())
      snap['proposal']=proposal.read_text()
      status.update(input_worker_run_id=prior['run_id'],input_worker_conversation_id=prior['conversation_id'],input_proposal_sha256=file_sha(proposal))
    if feedback is not None:
      status['input_feedback_sha256']=file_sha(feedback)
      saved=parse(feedback.read_text())
      assert saved['reviewed_proposal_sha256']==file_sha(proposal)
      assert 'private' not in json.dumps(saved)and 'FAILURES' not in json.dumps(saved)
    if test_evidence is not None:
      raw=readbytes(rt,test_evidence,r.MAX_CONTEXT,'native reviewer evidence')
      snap['test_evidence']=validate(parse(raw.decode()),file_sha(proposal),require_passed=True)
      assert set(snap['test_evidence'])==V2
      status['input_test_evidence_sha256']=hashlib.sha256(raw).hexdigest()
    save(output/'task.json',snap)
    save(output/'status.json',status)
    return output
  monkeypatch.setattr(p,'runner_run',local_provider)
  wid=prefix+'-worker-0'
  wd=local_provider(rt,tp,'worker',wid,60)
  convs=set()
  result,status,sha,text=p.validate_run(rt,wd,'worker',wid,head,task,sources,convs)
  state={S:'TASK_BLOCKED',PS:sha,'correction_count':0}

  def check():
    p.check_state(rt,head,refs,tp,task,sources)

  def remainder():
    p._run_post_worker_remainder(root=rt,task_candidate=tp,task=task,sources=sources,initial_head=head,initial_refs=refs,run_prefix=prefix,timeout=60,cli=None,state=state,evidence_dir=ev,state_check=check,worker_dir=wd,worker_res=result,current_worker=(wid,sha,text,status['conversation_id']),seen_convs=convs,correction_count=0,seen_norm_findings=set(),seen_test_signatures=set(),seen_bootstrap_signatures=set(),seen_branch_factors=set())
  if scenario in('corrected','unknown'):
    remainder()
    count=2 if scenario=='unknown' else 1
    assert state[S]=='READY_FOR_CODEX' and state['correction_count']==count
    assert[call[0]for call in calls]==['worker']*(count+1)+['verifier']
    meta=dict(state,original_run_prefix=prefix)
    passed=p.load_verifier_pre_review_receipt(rt,meta)
    assert set(passed)==V2 and passed[S]=='PASSED'
    if scenario=='corrected':
      assert passed[RC]==0 and passed[D]is None and(passed[E]=='diagnostic unavailable or invalid')
    from scripts.two_site_quota_ready_receipt_binding import verify_ready_pre_review_receipt
    ready={key:meta[key]for key in('original_run_prefix','correction_count','pre_review_test_path','pre_review_test_sha256','pre_review_test_state',PS)}
    ready['run_prefix']=ready.pop('original_run_prefix')
    assert verify_ready_pre_review_receipt(rt,ready,[PATH])['status']=='RECEIPT_BINDING_OBSERVED_NOT_ACCEPTED'
    altered=dict(meta,pre_review_test_sha256='0'*64)
    with raises(p.ProposalError,match='SHA mismatch'):
      p.load_verifier_pre_review_receipt(rt,altered)
    assert p.load_verifier_pre_review_receipt(rt,meta)[S]=='PASSED'
  else:
    expected='repeated test failure signature' if scenario=='recurrence' else 'correction exhaustion' if scenario=='budget' else 'repeated branch root factor'
    with raises(p.ProposalError,match=expected):
      remainder()
    assert state[S]=='TASK_BLOCKED' and all((call[0]=='worker' for call in calls))
    assert len(calls)==(3 if scenario=='budget' else 2)
  first_raw=readbytes(rt,ev/'pre-review-tests-0.json',r.MAX_CONTEXT,'first native receipt')
  first=validate(parse(first_raw.decode()),sha)
  assert set(first)==V2 and first[S]=='FAILED'
  saved=parse((ev/'feedback-correction-1.json').read_text())
  assert saved['reviewed_proposal_sha256']==sha and saved['reviewer']=='test-gate'
  assert saved['test_evidence'][2]=='normalized_failure_facts: '+('{"exceptions":["'+BRANCH+'"],"statements":[],"tests":["call: tests/test_existing.py::test_a"]}' if scenario=='branch' else '{"exceptions":["UNKNOWN"],"statements":[],"tests":["call: tests/test_existing.py::test_subject"]}' if scenario=='unknown' else '{"exceptions":["builtins.AssertionError"],"statements":["'+SHAPE+'"],"tests":["call: tests/test_existing.py::test_subject"]}')
  correction=parse((rt/'.local/two-site-refactor/proposals'/(prefix+'-worker-1')/'status.json').read_text())
  assert correction['input_proposal_sha256']==sha
  assert correction['input_feedback_sha256']==file_sha(ev/'feedback-correction-1.json')
  assert p.git(rt,'status','--porcelain=v1','--untracked-files=all')==''
