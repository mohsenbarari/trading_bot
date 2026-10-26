"""Portable pure normal-admission/lifecycle operands; no clients or host data."""
from contextlib import ExitStack
from copy import deepcopy
from pathlib import Path
from types import ModuleType,SimpleNamespace
from unittest.mock import patch
import ast,json,time,unittest

ROOT=Path(__file__).resolve().parents[1]
def load(name):
 p=ROOT/'deploy/maintenance'/name;m=ModuleType('_portable_'+p.stem);m.__file__=str(p);exec(compile(p.read_bytes(),str(p),'exec'),m.__dict__);return m
N=load('capture_conflict_normal.py');H=load('capture_conflict_diagnostic.py');C=load('canonical_app_activation_contract.py');M=load('normal_market_activation.py')
def fixture():
 tree=ast.parse((ROOT/'tests/test_canonical_app_activation.py').read_bytes());node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='fixture')
 ns={'C':C,'SimpleNamespace':SimpleNamespace,'deepcopy':deepcopy};exec(compile(ast.Module(body=[node],type_ignores=[]),'public_synthetic_operands','exec'),ns);f=ns['fixture']()
 role=H.SERVICE;spec=f.old['services'].pop('app');f.old['services'][role]=spec
 name=H.PROJECT+'-'+role+'-1';base='/public/normal/base.yml';extra='/public/normal/web.yml';env='/public/normal/normal.env'
 spec.update(image=H.IMAGE,container_name=name,command=['service','--role',role],profiles=['web'],user='10001:10001',read_only=True,
  cap_drop=['ALL'],security_opt=['no-new-privileges:true'],init=True,pids_limit=64,mem_limit=384*1024**2,memswap_limit=768*1024**2,
  cpus=1.0,shm_size=128*1024**2,tmpfs=['/tmp:size=16m,mode=1777,noexec,nosuid,nodev'],pull_policy='never',
  secrets=[{'source':'first','target':'first'},{'source':'second','target':'second'}],
  healthcheck={'test':['CMD','public-normal-health',role],'interval':'10s','timeout':'3s','retries':3},
  environment={'MARKET_PIPELINE_RELEASE_SHA':'a'*40,'PUBLIC_BUSINESS':'PRIVATE_RAM_SENTINEL'})
 spec['volumes']=[{'type':'bind','source':src,'target':dst,'read_only':False} for src,dst in
  (('/public/capture','/var/lib/market-data/capture'),(H.SESSION,'/var/lib/market-data/session'),('/public/state','/var/lib/market-data/state'))]
 f.old['secrets']={k:{'name':k,'file':'/public/'+k} for k in ('first','second')}
 image=deepcopy(f.old_image);image['Id']=H.IMAGE;image['Config'].update(User='10001:10001',ArgsEscaped=True)
 row=deepcopy(f.old_row);row.update(id=H.OLD_CID,image=H.IMAGE,name='/'+name,pid=123,running=True,restarting=False,
  started='2026-10-25T01:00:00Z',restarts=7)
 cfg=row['config'];cfg.update(Image=H.IMAGE,Cmd=spec['command'],User='10001:10001',ArgsEscaped=True,Hostname=H.OLD_CID[:12],
  Env=[*image['Config']['Env'],*(k+'='+v for k,v in spec['environment'].items())],Healthcheck=C.healthcheck(spec['healthcheck']))
 cfg['Labels'].update({'com.docker.compose.project':H.PROJECT,'com.docker.compose.service':role,'com.docker.compose.image':H.IMAGE,
  'com.docker.compose.project.working_dir':'/public/normal','com.docker.compose.project.config_files':base+','+extra,
  'com.docker.compose.project.environment_file':env,'com.docker.compose.version':'2.40.3','com.docker.compose.depends_on':''})
 for key in C.PROVENANCE_LABELS:cfg['Labels'].pop(key,None)
 host=row['host'];host.update(ReadonlyRootfs=True,CapDrop=['ALL'],SecurityOpt=['no-new-privileges:true'],Init=True,PidsLimit=64,
  Memory=384*1024**2,MemorySwap=768*1024**2,NanoCpus=10**9,ShmSize=128*1024**2,Tmpfs={'/tmp':'size=16m,mode=1777,noexec,nosuid,nodev'})
 row['mounts']=[{'Type':'bind','Source':r['source'],'Destination':r['target'],'RW':True,'Propagation':'rprivate'} for r in spec['volumes']]+[
  {'Type':'bind','Source':'/public/'+k,'Destination':'/run/secrets/'+k,'RW':False,'Propagation':'rprivate'} for k in ('first','second')]
 for options in row['networks'].values():options['Aliases']=[name,role]
 refs={'secret_files':[{'path':'/public/'+k,'target':'/public/'+k} for k in ('first','second')]}
 control={'nonce':'a'*32,'compose':{'directory':'/public/normal','files':[{'path':base},{'path':extra}],'env_reference':env},
  'new_source':{'path':str(H.ROOTS['web']/('a'*32)/'private_capture.py')},'normal':{'creation_compose_version':'2.40.3'}}
 return SimpleNamespace(model=f.old,image=image,row=row,refs=refs,control=control)
def new_row(f,*,bound=True):
 row=H.add_source_bind(f.row,f.control['new_source']['path']) if bound else deepcopy(f.row)
 row.update(id='d'*64,pid=0,running=False,restarting=False,started='0001-01-01T00:00:00Z',restarts=0)
 row['config']['Hostname']=row['id'][:12];row['config']['Labels'].update({'com.docker.compose.version':'5.6.1',
  'com.docker.compose.replace':H.PROJECT+'-'+H.SERVICE+'-1','com.docker.compose.depends_on':''})
 if bound:row['config']['Labels']['com.docker.compose.project.config_files']+=','+str(Path(f.control['new_source']['path']).parent/'diagnostic-source.yml')
 return row
def new_model(f):
 model=deepcopy(f.model);model['services'][H.SERVICE]['volumes'].append({'type':'bind','source':f.control['new_source']['path'],
  'target':H.DEST,'read_only':True,'bind':{'create_host_path':False}});return model

class NormalTests(unittest.TestCase):
 def setUp(self):
  self.stack=ExitStack();self.addCleanup(self.stack.close)
  for name in ('subprocess.Popen','subprocess.run','os.system','os.popen','socket.socket','socket.create_connection','urllib.request.urlopen'):
   self.stack.enter_context(patch(name,side_effect=AssertionError('ambient denied')))
 def parity(self,f,model,row):return N.normal_parity(H,C,M,f.control,model,row,f.image,f.refs,current_version='5.6.1')
 def test_current_normal_validators_keep_historical_version_and_accept_only_the_one_bind(self):
  f=fixture();before=deepcopy((f.model,f.row));self.assertTrue(self.parity(f,f.model,f.row))
  model=new_model(f);row=new_row(f);self.assertTrue(self.parity(f,model,row))
  self.assertEqual(len(f.row['mounts']),5);self.assertEqual(len(row['mounts']),6);self.assertEqual(before,(f.model,f.row))
  self.assertNotIn(H.FOUNDATION_DEST,{r['Destination'] for r in row['mounts']})
 def test_current_version_no_deps_and_exact_full_replace_label_are_independent_checks(self):
  f=fixture();model=new_model(f)
  for key,value in (('com.docker.compose.version','2.40.3'),('com.docker.compose.depends_on','db:service_started:false'),
   ('com.docker.compose.replace','wrong-normal-name')):
   row=new_row(f);row['config']['Labels'][key]=value
   with self.assertRaisesRegex(RuntimeError,'compose_labels'):self.parity(f,model,row)
  row=new_row(f);row['config']['Labels'].pop('com.docker.compose.depends_on')
  with self.assertRaisesRegex(RuntimeError,'compose_labels'):self.parity(f,model,row)
 def test_only_never_pull_policy_and_complete_business_values_remain_admitted(self):
  for key,value in (('pull_policy','always'),('pull_policy',True),('command',['unreviewed'])):
   f=fixture();f.model['services'][H.SERVICE][key]=value
   with self.assertRaises(RuntimeError):self.parity(f,f.model,f.row)
  for change in (lambda r:r['config']['Env'].append('BUSINESS_CHANGED=PRIVATE'),lambda r:r['host'].update(Privileged=True),
   lambda r:r['mounts'][0].update(RW=False),lambda r:r['config']['Labels'].update({'public.business':'changed'})):
   f=fixture();change(f.row)
   with self.assertRaises(RuntimeError):self.parity(f,f.model,f.row)
 def test_original_lifecycle_comparator_receives_projection_only_after_real_current_parity(self):
  f=fixture();model=new_model(f);new=new_row(f);parity=lambda row:self.parity(f,model,row)
  N.guarded_source_change(H,H.expected_source_change,parity,f.row,new,f.control['new_source']['path'],stopped=True)
  new['config']['Labels']['public.unreviewed']='bad'
  with self.assertRaises(RuntimeError):N.guarded_source_change(H,H.expected_source_change,parity,f.row,new,f.control['new_source']['path'],stopped=True)
 def test_baked_rollback_removes_only_diagnostic_bind_under_current_creation_version(self):
  f=fixture();restored=new_row(f,bound=False)
  self.assertTrue(self.parity(f,f.model,restored));self.assertEqual(len(restored['mounts']),5)
  synthetic=H.add_source_bind(f.row,f.control['new_source']['path'])
  N.guarded_source_change(H,H.expected_source_change,lambda r:self.parity(f,f.model,r),synthetic,restored,f.control['new_source']['path'],stopped=True,removing=True)
 def test_never_started_and_running_lifecycle_states_are_not_conflated(self):
  f=fixture();N.lifecycle(H,f.row);candidate=new_row(f);N.lifecycle(H,candidate,stopped=True,never_started=True)
  for key,value in (('started','2026-10-26T00:00:00Z'),('started','NOT-A-TIMEZ'),('restarts',1),('restarts',False),('pid',1),('running',True)):
   row=deepcopy(candidate);row[key]=value
   with self.assertRaisesRegex(RuntimeError,'normal_lifecycle'):N.lifecycle(H,row,stopped=True,never_started=True)
 def test_pre_stop_runtime_or_business_drift_refuses_before_any_mutating_command(self):
  f=fixture();Web=N.normal_backend(H,C,None,M);backend=Web.__new__(Web);backend.requested=False;backend.admitted_snapshot={f.row['id']:deepcopy(f.row)}
  backend.snapshot=lambda:{f.row['id']:deepcopy(f.row)};backend.target=lambda rows:rows[f.row['id']]
  backend.parity_row=lambda row:self.parity(f,f.model,row);calls=[];backend.command=lambda *a,**k:calls.append(a);backend.compose=lambda *a,**k:['PUBLIC_MUTATION']
  f.row['pid']+=1
  with self.assertRaisesRegex(RuntimeError,'normal_before_stop_changed'):backend.stop()
  self.assertEqual(calls,[])
 def test_shared_and_historical_modules_are_purely_separate_dependencies(self):
  self.assertNotEqual(N.PRIMITIVE_PINS['market'],N.HISTORICAL_SHA)
  self.assertEqual(N.HISTORICAL_SHA,'24f0f2e49f2052c1425e60cbb72bfd24bc5d14af44a9646751effb0c4d019236')
  self.assertEqual(N.BASE_SHA,'9d2b9f2850e9ac129418c33777f863777e6ce5d3d27ccc0ec3fedaa852501860')
 def test_closed_refusal_never_copies_private_exception_arguments(self):
  N.CODES.add('normal_source');good=N.refusal(RuntimeError('normal_source'));self.assertEqual(N.peer_refusal(good),good)
  for error in (RuntimeError('PRIVATE_RAM_SENTINEL'),RuntimeError('normal_source','PRIVATE_RAM_SENTINEL'),KeyError('PRIVATE_RAM_SENTINEL')):
   value=N.refusal(error);self.assertEqual(value['code'],'unknown');self.assertNotIn('PRIVATE_RAM_SENTINEL',json.dumps(value))
  for key,bad in (('os_errno',True),('code','PRIVATE_RAM_SENTINEL'),('security_accepted',True)):
   value=deepcopy(good);value[key]=bad
   with self.assertRaises(RuntimeError):N.peer_refusal(value)
if __name__=='__main__':unittest.main()
