"""Real normal-C parity with owned public inputs; no daemon or normal Env reads."""
from contextlib import ExitStack
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from types import ModuleType,SimpleNamespace
from unittest.mock import patch
import ast,io,json,os,tempfile,time,unittest

ROOT=Path(__file__).resolve().parents[1]
DENIED=('subprocess.Popen','subprocess.run','os.system','os.popen','os.fork','socket.socket','socket.create_connection','urllib.request.urlopen','ctypes.CDLL')
def module(name,path):
 m=ModuleType(name);m.__file__=str(path);exec(compile(path.read_bytes(),str(path),'exec'),m.__dict__);return m
with ExitStack() as fence:
 for target in DENIED:fence.enter_context(patch(target,side_effect=AssertionError('ambient denied')))
 A=module('_public_admission',ROOT/'deploy/maintenance/normal_app_restore_admission.py')
 C=module('_public_admission_contract',ROOT/'deploy/maintenance/canonical_app_activation_contract.py')

class AdmissionTests(unittest.TestCase):
 def setUp(self):
  self.assertEqual((os.geteuid(),os.getegid()),(0,0));self.stack=ExitStack();self.addCleanup(self.stack.close)
  for target in DENIED:self.stack.enter_context(patch(target,side_effect=AssertionError('ambient denied')))
  self.root=Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='.restore-admission-',dir=ROOT)));self.root.chmod(0o700)
  tree=ast.parse((ROOT/'tests/test_canonical_app_activation.py').read_bytes());f=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='fixture')
  ns={'C':C,'SimpleNamespace':SimpleNamespace,'deepcopy':deepcopy};exec(compile(ast.Module(body=[f],type_ignores=[]),'public_normal_fixture','exec'),ns);self.build=ns['fixture']
 def public_file(self,name,raw):
  p=self.root/name;p.write_bytes(raw);p.chmod(0o600);return p
 def case(self,site='web'):
  now=int(time.time());control={'schema':'normal_app_restore_admission/1','operation':A.OP,'nonce':'a'*32,'site':site,'machine_id':'b'*32,
   'issued_at':now,'deadline':now+120,'helpers':{name:{'path':str(path),'sha256':A.PINS[name]} for name,path in (
    ('contract',ROOT/'deploy/maintenance/canonical_app_activation_contract.py'),('utilities',ROOT/'deploy/maintenance/canonical_app_activation_site.py'),
    ('capture',ROOT/'deploy/maintenance/bounded_operational_capture.py'))},'roles':{},'datastores':{'db':'d'*64,'redis':'e'*64}}
  fixtures={};models={};images={};inspections={};roles=A.ROLES[site]
  target=self.public_file('shared-public-unit-env',b'PUBLIC_FIXTURE_NEVER_READ_BY_COLLECTOR');default=self.root/'.env'
  if not default.exists():default.symlink_to(target)
  reference={'path':str(default),'target':str(target),'identity':A.identity(target.stat()),
   'aliases':[{'path':str(default),'identity':A.identity(default.lstat())}],'cli':False}
  for i,role in enumerate(roles):
   f=self.build(role,site);cfg=f.old_row['config'];spec=f.old['services'][role];identifier=format(i+1,'064x')
   command=['sh','-c','exec uvicorn main:app --host 0.0.0.0 --port 8000 --workers 4'] if role=='app' else ['python','run_bot.py'] if role=='bot' else ['python','-m','core.sync_worker']
   cfg['Cmd']=spec['command']=command;cfg['Hostname']=identifier[:12];f.old_row['id']=identifier
   spec['environment'].update(DATABASE_URL='postgresql+asyncpg://u:PRIVATE_SENTINEL@db:5432/business',
    SYNC_DATABASE_URL='postgresql+psycopg2://u:PRIVATE_SENTINEL@db:5432/business',REDIS_URL='redis://redis:6379/0')
   cfg['Env']=[*f.old_image['Config']['Env'],*(k+'='+v for k,v in spec['environment'].items())]
   files=[]
   for j in range(2):
    p=self.public_file(role+str(j)+'.yml',b'services: {}\n');files.append({'path':str(p),'bytes':p.stat().st_size,'sha256':sha256(p.read_bytes()).hexdigest()})
   f.scope['workdir']=str(self.root);cfg['Labels']['com.docker.compose.project.working_dir']=str(self.root)
   cfg['Labels']['com.docker.compose.project.config_files']=','.join(r['path'] for r in files)
   f.scope['compose_version']='2.40.3';cfg['Labels']['com.docker.compose.version']='2.40.3'
   descriptor={k:f.scope[k] for k in ('project','workdir','environment_file_label','compose_version')}
   descriptor.update(compose_files=files,container_id=identifier,env_files=[deepcopy(reference)])
   control['roles'][role]=descriptor;models[role]=f.old;images[f.old_image['Id']]=f.old_image
   inspections[identifier]={'Id':identifier,'Name':f.old_row['name'],'Image':f.old_row['image'],'Config':cfg,'HostConfig':f.old_row['host'],'RestartCount':0,
    'Mounts':[],'NetworkSettings':{'Networks':f.old_row['networks']},'State':{'Running':True,'Pid':1200+i,'StartedAt':'2026-10-25T00:00:00Z','Restarting':False,'Paused':False,'Dead':False}}
   fixtures[role]=f
  for kind,identifier in control['datastores'].items():
   inspections[identifier]={'Id':identifier,'Name':'/trading_bot_'+kind,'Image':'sha256:'+'0'*64,'Config':{'public':'normal store'},'HostConfig':{},'Mounts':[],'RestartCount':0,
    'State':{'Running':True,'Pid':4321,'StartedAt':'2026-10-25T00:00:00Z','Restarting':False,'Paused':False,'Dead':False},
    'NetworkSettings':{'Networks':{'fixture_net':{'Aliases':[kind,'trading_bot_'+kind]}}}}
  calls=[]
  def capture(argv,seconds,environment):
   calls.append(argv);self.assertGreater(seconds,0);self.assertLessEqual(seconds,60);self.assertEqual(environment['LC_ALL'],'C.UTF-8')
   self.assertTrue(Path(environment['DOCKER_CONFIG']).is_dir());self.assertEqual(list(Path(environment['DOCKER_CONFIG']).iterdir()),[])
   self.assertEqual(argv[:2],['/usr/bin/docker','--host=unix:///var/run/docker.sock'])
   if argv[2:]==['compose','version','--short']:return 0,b'v2.40.9\n'
   if argv[2:4]==['container','inspect']:return 0,json.dumps([inspections[argv[4]]]).encode()
   if argv[2:4]==['image','inspect']:return 0,json.dumps([images[argv[4]]]).encode()
   if argv[2]=='compose':
    first=argv[argv.index('--file')+1];role=next(r for r,s in control['roles'].items() if s['compose_files'][0]['path']==first)
    if argv[-1]=='--no-env-resolution':
     self.assertEqual(argv[-4:],['config','--format','json','--no-env-resolution']);raw=deepcopy(models[role])
     raw['services'][role]['env_file']=[{'path':str(default),'required':True}];return 0,json.dumps(raw).encode()
    self.assertEqual(argv[-3:],['config','--format','json']);return 0,json.dumps(models[role]).encode()
   raise AssertionError('unexpected client')
  return control,fixtures,inspections,capture,calls
 def invoke(self,control,capture):
  real=open
  def opened(path,*a,**k):return io.BytesIO((control['machine_id']+'\n').encode()) if str(path)=='/etc/machine-id' else real(path,*a,**k)
  pin=sha256(Path(A.__file__).read_bytes()).hexdigest()
  previous=Path.cwd()
  try:
   os.chdir(self.root)
   with patch('builtins.open',side_effect=opened),ExitStack() as stack:return A.collect(control,stack,pin,capture=capture)
  finally:os.chdir(previous)
 def test_real_normal_C_dependency_all_roles_preserve_historical_version_independent_of_current(self):
  for site in ('bot','web'):
   control,fixtures,rows,capture,calls=self.case(site);public,private=self.invoke(control,capture)
   self.assertEqual(len(public['normal_role_eligibility']),len(A.ROLES[site]));self.assertEqual(public['current_compose_version'],'2.40.9')
   self.assertTrue(all(r['creation_compose_version']=='2.40.3' for r in public['normal_role_eligibility']))
   self.assertNotIn('PRIVATE_SENTINEL',json.dumps(public));self.assertIn('PRIVATE_SENTINEL',' '.join(private['roles']['app']['actual']['Config']['Env']))
   self.assertFalse(public['api_clone_started']);self.assertFalse(public['network_or_firewall_changed'])
   for argv in calls:self.assertFalse(set(argv)&{'create','start','run','up','stop','kill','load','rm','build'})
 def test_historical_replace_label_keeps_exact_normal_name_validation(self):
  control,fixtures,rows,capture,calls=self.case()
  identifier=control['roles']['app']['container_id'];labels=rows[identifier]['Config']['Labels']
  labels['com.docker.compose.replace']=fixtures['app'].old['services']['app']['container_name']
  public,_=self.invoke(control,capture)
  self.assertTrue(all(r['normal_command_valid'] for r in public['normal_role_eligibility']))
  for wrong in ('foreign_container','',False,0,None):
   with self.subTest(wrong=wrong):
    labels['com.docker.compose.replace']=wrong
    with self.assertRaises(RuntimeError):self.invoke(control,capture)
 def test_normal_no_deps_creation_keeps_declared_model_and_rejects_partial_or_missing_label(self):
  c,f,rows,capture,calls=self.case();spec=f['app'].old['services']['app']
  spec['depends_on']={'migration':{'condition':'service_completed_successfully'},'redis':{'condition':'service_started'}}
  original=deepcopy(spec['depends_on']);label='com.docker.compose.depends_on';labels=rows[c['roles']['app']['container_id']]['Config']['Labels']
  public,private=self.invoke(c,capture)
  self.assertEqual(private['roles']['app']['model']['services']['app']['depends_on'],original)
  self.assertEqual(next(r for r in public['normal_role_eligibility'] if r['role']=='app')['creation_dependency_label'],'empty')
  labels[label]='migration:service_completed_successfully:false,redis:service_started:false'
  public,_=self.invoke(c,capture)
  self.assertEqual(next(r for r in public['normal_role_eligibility'] if r['role']=='app')['creation_dependency_label'],'declared')
  for value in ('redis:service_started:false','foreign:service_started:false',False,None):
   labels[label]=value
   with self.subTest(value=value),self.assertRaises(RuntimeError):self.invoke(c,capture)
  labels.pop(label)
  with self.assertRaisesRegex(RuntimeError,'compose_labels'):self.invoke(c,capture)
 def test_real_Normal_C_rejects_complete_config_env_command_and_host_mismatch(self):
  for field,bad in (('Env',['SECRET=PRIVATE_SENTINEL']),('Cmd',['python','special_probe.py']),('User','10001')):
   c,f,rows,capture,calls=self.case();rows[c['roles']['app']['container_id']]['Config'][field]=bad
   with self.assertRaises(RuntimeError):self.invoke(c,capture)
  c,f,rows,capture,calls=self.case();rows[c['roles']['app']['container_id']]['HostConfig']['Privileged']=True
  with self.assertRaises(RuntimeError):self.invoke(c,capture)
 def test_actual_sized_public_json_rollback_prefix_keeps_full_model_and_normal_policy(self):
  c,f,rows,capture,calls=self.case();app=f['app'];identifier=c['roles']['app']['container_id']
  model=app.old['services']['app'];model.update(image=app.old_image['Id'],pull_policy='never')
  rows[identifier]['Config']['Image']=app.old_image['Id']
  value={'services':{'app':{'image':app.old_image['Id'],'pull_policy':'never','restart':'always'}}}
  raw=json.dumps(value,separators=(',',':')).encode();self.assertLessEqual(len(raw),153);raw+=b' '*(153-len(raw))
  path=self.public_file('rollback-app.override.json',raw);ref={'path':str(path),'bytes':153,'sha256':sha256(raw).hexdigest()}
  c['roles']['app']['compose_files'].append(ref);rows[identifier]['Config']['Labels']['com.docker.compose.project.config_files']+=','+str(path)
  public,private=self.invoke(c,capture);self.assertEqual(private['roles']['app']['model']['services']['app']['pull_policy'],'never')
  self.assertEqual(len(public['normal_role_eligibility']),2)
  A.public_compose_source(ref,raw)
  for extra in ({'environment':{'SECRET':'PRIVATE_SENTINEL'}},{'command':['python','probe.py']},{'pull_policy':True}):
   bad=deepcopy(value);bad['services']['app'].update(extra)
   with self.assertRaises(RuntimeError):A.public_compose_source(ref,json.dumps(bad).encode())
 def test_creation_version_is_bound_and_missing_shape_cannot_reach_any_client(self):
  c,f,rows,capture,calls=self.case();c['roles']['app']['compose_version']='2.40.9'
  with self.assertRaisesRegex(RuntimeError,'compose_labels'):self.invoke(c,capture)
  c,f,rows,capture,calls=self.case();del c['roles']['app']['compose_version']
  with self.assertRaisesRegex(RuntimeError,'normal_prefix'):self.invoke(c,capture)
 def test_absent_or_wrong_implicit_default_env_refuses_before_every_client(self):
  for wrong in (False,True):
   c,f,rows,capture,calls=self.case()
   if wrong:
    path=self.public_file('wrong-default-env',b'PUBLIC');c['roles']['app']['env_files']=[{'path':str(path),'target':str(path),'identity':A.identity(path.stat()),'aliases':[],'cli':False}]
   else:c['roles']['app']['env_files']=[]
   with self.assertRaisesRegex(RuntimeError,'(?:default_env_reference|env_references)'):self.invoke(c,capture)
   self.assertEqual(calls,[])
 def test_explicit_cli_paths_order_and_label_are_bound_without_inventing_flags(self):
  c,f,rows,capture,calls=self.case();spec=c['roles']['app'];paths=[]
  for name in ('cli-one.env','cli-two.env'):
   path=self.public_file(name,b'PUBLIC NORMAL CLI INPUT');paths.append(str(path));spec['env_files'].append({'path':str(path),'target':str(path),'identity':A.identity(path.stat()),'aliases':[],'cli':True})
  spec['environment_file_label']=','.join(paths);rows[spec['container_id']]['Config']['Labels']['com.docker.compose.project.environment_file']=spec['environment_file_label']
  self.invoke(c,capture)
  rendered=[argv for argv in calls if '--project-name' in argv and argv[argv.index('--file')+1]==spec['compose_files'][0]['path']]
  self.assertTrue(rendered)
  for argv in rendered:self.assertEqual([argv[i+1] for i,v in enumerate(argv) if v=='--env-file'],paths)
  for variant in ('order','label','unrecorded'):
   changed=deepcopy(c);bad=changed['roles']['app'];calls.clear()
   if variant=='order':bad['env_files']=[bad['env_files'][0],bad['env_files'][2],bad['env_files'][1]]
   elif variant=='label':bad['environment_file_label']=paths[0]
   else:bad['environment_file_label']=None
   with self.assertRaisesRegex(RuntimeError,'env_cli_binding'):self.invoke(changed,capture)
   self.assertEqual(calls,[])
 def test_service_env_file_reference_requires_declared_opaque_coverage(self):
  c,f,rows,capture,calls=self.case()
  def unknown(argv,*a,**kw):
   rc,raw=capture(argv,*a,**kw)
   if argv[-1]=='--no-env-resolution':
    value=json.loads(raw);role='app' if '--file' in argv and argv[argv.index('--file')+1]==c['roles']['app']['compose_files'][0]['path'] else 'sync_worker'
    value['services'][role]['env_file'].append({'path':str(self.root/'UNDECLARED.env')});return rc,json.dumps(value).encode()
   return rc,raw
  with self.assertRaisesRegex(RuntimeError,'unbound_env_reference'):self.invoke(c,unknown)
 def test_every_foreign_project_service_env_is_covered_before_any_full_render(self):
  for covered in (False,True):
   c,f,rows,capture,calls=self.case();full=[]
   def foreign(argv,*a,**kw):
    rc,raw=capture(argv,*a,**kw)
    if argv[-1]=='--no-env-resolution':
     value=json.loads(raw);value['services']['foreign_service']={'env_file':[{'path':str(self.root/('.env' if covered else 'UNDECLARED.env'))}]}
     return rc,json.dumps(value).encode()
    if argv[-3:]==['config','--format','json']:full.append(argv)
    return rc,raw
   if covered:
    public,_=self.invoke(c,foreign);self.assertEqual(len(public['normal_role_eligibility']),2);self.assertEqual(len(full),2)
   else:
    with self.assertRaisesRegex(RuntimeError,'unbound_env_reference'):self.invoke(c,foreign)
    self.assertEqual(full,[])
 def test_loader_plugin_literal_destination_and_source_mount_fail_closed(self):
  c,f,rows,capture,calls=self.case();actual=rows[c['roles']['app']['container_id']];aliases={'db':{'db'},'redis':{'redis'}}
  for key,value in (('LD_PRELOAD','/tmp/unknown.so'),('PYTHONPATH','/source'),('HTTPS_PROXY','https://PRIVATE_SENTINEL')):
   changed=deepcopy(actual);changed['Config']['Env'].append(key+'='+value)
   with self.assertRaisesRegex(RuntimeError,'loader_environment'):A.loader_scope(C,'app',changed,aliases)
  for url in ('postgresql+unknown://u:p@db/db','postgresql+asyncpg://u:p@192.0.2.1/db','postgresql+asyncpg://u:p@db/db?hostaddr=192.0.2.1'):
   changed=deepcopy(actual);changed['Config']['Env']=[x for x in changed['Config']['Env'] if not x.startswith('DATABASE_URL=')]+['DATABASE_URL='+url]
   with self.assertRaises(RuntimeError):A.loader_scope(C,'app',changed,aliases)
  changed=deepcopy(actual);changed['Mounts']=[{'Type':'bind','Destination':'/app/core','Source':'/source'}]
  with self.assertRaisesRegex(RuntimeError,'source_mounts'):A.loader_scope(C,'app',changed,aliases)
 def test_opaque_env_never_read_or_hashed_by_actual_reference_guard(self):
  c,f,rows,capture,calls=self.case();protected={Path(ref['target']) for r in c['roles'].values() for ref in r['env_files']};real=os.pread
  def pread(fd,*a):
   target=Path(os.readlink('/proc/self/fd/'+str(fd)));self.assertNotIn(target,protected);return real(fd,*a)
  with patch.object(os,'pread',side_effect=pread):self.invoke(c,capture)
 def test_final_same_id_config_host_network_and_lifecycle_drift_prevents_green(self):
  for kind in ('Config','HostConfig','Networks','Pid','StartedAt','RestartCount'):
   c,f,rows,capture,calls=self.case();identifier=c['datastores']['redis'];changed=[False]
   def late(argv,*a,**kw):
    rc,raw=capture(argv,*a,**kw)
    if argv[2:4]==['image','inspect'] and not changed[0]:
     changed[0]=True;r=rows[identifier]
     if kind=='Config':r['Config']['public']='different'
     elif kind=='HostConfig':r['HostConfig']['Memory']=123
     elif kind=='Networks':r['NetworkSettings']['Networks']['fixture_net']['Aliases'].append('foreign')
     elif kind=='RestartCount':r['RestartCount']=1
     else:r['State'][kind]=12345 if kind=='Pid' else '2026-10-26T00:00:00Z'
    return rc,raw
   with self.subTest(kind=kind),self.assertRaisesRegex(RuntimeError,'normal_runtime_changed'):self.invoke(c,late)
 def test_actual_normal_C_failure_reaches_closed_cli_code_without_env_or_traceback(self):
  c,f,rows,capture,calls=self.case();rows[c['roles']['app']['container_id']]['Config']['Env']=['PRIVATE_SENTINEL=secret']
  path=self.public_file('public-control.json',json.dumps(c).encode());pin=sha256(path.read_bytes()).hexdigest();own=sha256(Path(A.__file__).read_bytes()).hexdigest()
  original=A.collect;out=io.StringIO();real=open
  def opened(path,*a,**k):return io.BytesIO(b'b'*32+b'\n') if str(path)=='/etc/machine-id' else real(path,*a,**k)
  previous=Path.cwd()
  try:
   os.chdir(self.root)
   with patch('builtins.open',side_effect=opened),patch.object(A,'collect',side_effect=lambda v,stack,pin:original(v,stack,pin,capture=capture)),patch('sys.stdout',out):
    rc=A.main(['--control',str(path),'--control-sha256',pin,'--source-sha256',own])
  finally:os.chdir(previous)
  self.assertEqual(rc,1);self.assertEqual(json.loads(out.getvalue())['code'],'normal_environment');self.assertNotIn('PRIVATE_SENTINEL',out.getvalue())
 def test_closed_cli_preserves_literal_normal_C_cause_without_private_value(self):
  self.assertIn('compose_labels',A.C_CODES);self.assertIn('normal_environment',A.C_CODES)
  out=io.StringIO();args=['--control','/public/not-read','--control-sha256','a'*64,'--source-sha256','b'*64]
  with patch.object(A,'cold',side_effect=RuntimeError('normal_environment')),patch('sys.stdout',out):self.assertEqual(A.main(args),1)
  result=json.loads(out.getvalue());self.assertEqual(result['code'],'normal_environment');self.assertNotIn('PRIVATE',out.getvalue())
 def test_source_classifier_is_exact_static_literal_projection_of_pinned_normal_sources(self):
  exact=set()
  for path in ('canonical_app_activation_contract.py','canonical_app_activation_site.py'):
   for node in ast.walk(ast.parse((ROOT/'deploy/maintenance'/path).read_bytes())):
    if isinstance(node,ast.Call) and len(node.args)>1 and ((isinstance(node.func,ast.Name) and node.func.id=='require') or
     (isinstance(node.func,ast.Attribute) and node.func.attr=='require')) and isinstance(node.args[1],ast.Constant) and type(node.args[1].value) is str:exact.add(node.args[1].value)
  self.assertEqual(A.C_CODES,frozenset(exact))
 def test_unrelated_actual_cwd_refuses_before_any_client(self):
  c,f,rows,capture,calls=self.case();pin=sha256(Path(A.__file__).read_bytes()).hexdigest()
  self.assertNotEqual(Path.cwd().resolve(),self.root.resolve())
  with ExitStack() as stack,self.assertRaisesRegex(RuntimeError,'normal_cwd'):A.collect(c,stack,pin,capture=capture)
  self.assertEqual(calls,[])
 def restart_case(self,site='web'):
  # Explicitly synthetic portable rows. Positive integration injects only this
  # public witness read boundary; no operational host receipt is copied/read.
  c,fixtures,rows,capture,calls=self.case(site);c['machine_id']=A.RESTART_WITNESSES[site]['machine_id']
  path=self.public_file('synthetic-restart.json',b'SYNTHETIC PUBLIC FIXTURE ONLY')
  c['runtime_restart_evidence']={'path':str(path),'bytes':path.stat().st_size,'sha256':sha256(path.read_bytes()).hexdigest()}
  document={'schema':A.RESTART_WITNESSES[site]['schema'],'containers':[]}
  if site=='web':document['machine_id']=c['machine_id']
  for role,f in fixtures.items():
   f.old['services'][role]['restart']='no';f.old['services'][role]['container_name']='trading_bot_'+role;actual=rows[c['roles'][role]['container_id']];actual['Name']='/trading_bot_'+role
   actual['HostConfig']['RestartPolicy']={'Name':'always','MaximumRetryCount':0}
   document['containers'].append({'id':actual['Id'],'image':actual['Image'],'name':actual['Name'],'project':c['roles'][role]['project'],
    'restart_policy':{'Name':'always','MaximumRetryCount':0}})
  def base_render(argv,*args,**kw):
   rc,raw=capture(argv,*args,**kw)
   if argv[2]=='compose' and '--file' in argv and argv.count('--file')==1 and argv[-3:]==['config','--format','json']:
    model=json.loads(raw)
    for r in set(fixtures)&set(model['services']):model['services'][r]['restart']='always'
    return rc,json.dumps(model).encode()
   return rc,raw
  return c,fixtures,rows,base_render,calls,document
 def test_restart_uses_independent_synthetic_witness_and_base_only_without_mutating_historical_model(self):
  for site in ('bot','web'):
   c,fixtures,rows,capture,calls,witness=self.restart_case(site);before={r:deepcopy(f.old) for r,f in fixtures.items()}
   with patch.object(A,'load_restart_witness',return_value=witness):public,private=self.invoke(c,capture)
   self.assertTrue(all(r['runtime_restart_policy']=={'Name':'always','MaximumRetryCount':0} and r['restart_witness_instance']=='same_instance'
    for r in public['normal_role_eligibility']))
   self.assertEqual({r:f.old for r,f in fixtures.items()},before)
   self.assertTrue(all(private['roles'][r]['model']['services'][r]['restart']=='no' for r in fixtures))
   base=[a for a in calls if a[2]=='compose' and '--file' in a and a.count('--file')==1]
   self.assertEqual(len(base),2*len(fixtures));self.assertNotIn('PRIVATE_SENTINEL',json.dumps(public))
 def test_only_web_app_may_use_earlier_normal_recreation_witness(self):
  for site,role,allowed in (('web','app',True),('web','sync_worker',False),('bot','app',False)):
   c,fixtures,rows,capture,calls,witness=self.restart_case(site);actual=rows[c['roles'][role]['container_id']]
   prior=next(r for r in witness['containers'] if r['name']==actual['Name']);prior['id']='f'*64
   actual['Config']['Labels']['com.docker.compose.replace']=fixtures[role].old['services'][role]['container_name']
   with patch.object(A,'load_restart_witness',return_value=witness):
    if allowed:
     public,_=self.invoke(c,capture);self.assertEqual(next(r for r in public['normal_role_eligibility'] if r['role']==role)['restart_witness_instance'],'earlier_normal_app_recreation')
    else:
     with self.assertRaisesRegex(RuntimeError,'runtime_restart_identity'):self.invoke(c,capture)
 def test_base_business_fields_are_not_adopted_and_historical_image_override_remains(self):
  c,fixtures,rows,capture,calls,witness=self.restart_case();before=deepcopy(fixtures['app'].old)
  def distinct_base(argv,*args,**kw):
   rc,raw=capture(argv,*args,**kw)
   if '--file' in argv and argv.count('--file')==1 and argv[-3:]==['config','--format','json']:
    v=json.loads(raw)
    for spec in v['services'].values():spec.update(image='ordinary-base-image-reference',environment={'BASE_ONLY':'NOT_ADOPTED'},
     command=['base-command-not-adopted'],volumes=[],depends_on={})
    return rc,json.dumps(v).encode()
   return rc,raw
  with patch.object(A,'load_restart_witness',return_value=witness):public,private=self.invoke(c,distinct_base)
  self.assertEqual(private['roles']['app']['model'],before);self.assertNotIn('NOT_ADOPTED',json.dumps(public))
 def test_earlier_web_witness_does_not_grant_replacement_classification(self):
  for label in (None,'wrong-container-name'):
   c,fixtures,rows,capture,calls,witness=self.restart_case();actual=rows[c['roles']['app']['container_id']]
   witness['containers'][0]['id']='f'*64
   if label is not None:actual['Config']['Labels']['com.docker.compose.replace']=label
   with patch.object(A,'load_restart_witness',return_value=witness),self.assertRaises(RuntimeError):self.invoke(c,capture)
 def test_native_real_normal_C_accepts_same_list_equals_vs_runtime_colon_host_mapping(self):
  c,fixtures,rows,capture,calls=self.case('bot');actual=rows[c['roles']['app']['container_id']]
  fixtures['app'].old['services']['app']['extra_hosts']=['coin.gold-trade.ir=65.109.220.59']
  actual['HostConfig']['ExtraHosts']=['coin.gold-trade.ir:65.109.220.59']
  public,private=self.invoke(c,capture)
  self.assertEqual(len(public['normal_role_eligibility']),3)
  self.assertEqual(private['roles']['app']['model']['services']['app']['extra_hosts'],['coin.gold-trade.ir=65.109.220.59'])
  self.assertEqual(private['roles']['app']['actual']['HostConfig']['ExtraHosts'],['coin.gold-trade.ir:65.109.220.59'])
 def test_restart_witness_wrong_source_and_machine_refuse_before_payload_or_clients(self):
  c,*_=self.case();path=self.public_file('not-a-host-receipt',b'SYNTHETIC PUBLIC')
  c['runtime_restart_evidence']={'path':str(path),'bytes':path.stat().st_size,'sha256':sha256(path.read_bytes()).hexdigest()}
  with patch.object(A.os,'pread',side_effect=AssertionError('no witness payload read')),ExitStack() as stack,self.assertRaisesRegex(RuntimeError,'runtime_restart_source'):
   A.load_restart_witness(c,stack,[])
  for site in ('bot','web'):
   v={'schema':A.RESTART_WITNESSES[site]['schema'],'machine_id':'f'*32,'containers':[]}
   with self.assertRaisesRegex(RuntimeError,'runtime_restart_source'):A.restart_document(v,site,'b'*32)
   if site=='web':
    with self.assertRaisesRegex(RuntimeError,'runtime_restart_source'):A.restart_document(v,site,A.RESTART_WITNESSES[site]['machine_id'])
 def test_restart_witness_identity_base_policy_and_nonrestart_drift_still_refuse(self):
  for bad in ('image','project','name','count_bool','base','Env','Cmd'):
   c,fixtures,rows,capture,calls,witness=self.restart_case();actual=rows[c['roles']['app']['container_id']];prior=witness['containers'][0]
   if bad in ('image','project','name'):prior[bad]=('sha256:'+'f'*64) if bad=='image' else 'wrong'
   elif bad=='count_bool':prior['restart_policy']['MaximumRetryCount']=False
   elif bad in ('Env','Cmd'):actual['Config'][bad]=['PRIVATE_SENTINEL']
   original=capture
   def wrong_base(argv,*args,**kw):
    rc,raw=original(argv,*args,**kw)
    if bad=='base' and '--file' in argv and argv.count('--file')==1 and argv[-3:]==['config','--format','json']:
     v=json.loads(raw);v['services']['app']['restart']='on-failure';return rc,json.dumps(v).encode()
    return rc,raw
   with patch.object(A,'load_restart_witness',return_value=witness),self.assertRaises(RuntimeError):self.invoke(c,wrong_base)
 def test_first_base_all_service_env_coverage_still_precedes_full_base_render(self):
  c,fixtures,rows,capture,calls,witness=self.restart_case();full=[]
  def foreign(argv,*args,**kw):
   rc,raw=capture(argv,*args,**kw)
   if '--file' in argv and argv.count('--file')==1:
    if argv[-1]=='--no-env-resolution':
     v=json.loads(raw);v['services']['base_foreign']={'env_file':[{'path':str(self.root/'UNBOUND.env')}]};return rc,json.dumps(v).encode()
    if argv[-3:]==['config','--format','json']:full.append(argv)
   return rc,raw
  with patch.object(A,'load_restart_witness',return_value=witness),self.assertRaisesRegex(RuntimeError,'unbound_env_reference'):self.invoke(c,foreign)
  self.assertEqual(full,[])

if __name__=='__main__':unittest.main()
