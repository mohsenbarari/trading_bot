"""Owned public operands only; never a daemon, normal Env or provider call."""
from contextlib import ExitStack
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from hashlib import sha256
import ast,io,json,os,shlex,sys,tempfile,time,unittest

ROOT=Path(__file__).resolve().parents[1]
DENIED=('subprocess.Popen','subprocess.run','os.system','os.popen','os.fork','os.kill','os.killpg','socket.socket','socket.create_connection','urllib.request.urlopen')
class NormalActivationTests(unittest.TestCase):
 def setUp(self):
  self.assertEqual((os.geteuid(),os.getegid()),(0,0));self.stack=ExitStack();self.addCleanup(self.stack.close)
  for name in DENIED:self.stack.enter_context(patch(name,side_effect=AssertionError('ambient denied')))
  self.root=Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='.normal-activation-',dir=ROOT)));self.root.chmod(0o700)
  # Methods must use their actual module globals, rather than a copied namespace.
  from types import ModuleType
  def loaded(name):
   p=ROOT/'deploy/maintenance'/name;m=ModuleType('_normal_test_'+p.stem);m.__file__=str(p);exec(compile(p.read_bytes(),str(p),'exec'),m.__dict__);return m
  self.N,self.C,self.S,self.K,self.A,self.T=map(loaded,('normal_app_activation.py','canonical_app_activation_contract.py','canonical_app_activation_site.py','canonical_app_activation_coordinator.py','normal_app_restore_admission.py','bounded_operational_capture.py'))
  tree=ast.parse((ROOT/'tests/test_canonical_app_activation.py').read_bytes());f=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='fixture')
  values={'C':self.C,'SimpleNamespace':SimpleNamespace,'deepcopy':deepcopy};exec(compile(ast.Module(body=[f],type_ignores=[]),'public_synthetic_normal_operands','exec'),values);self.build=values['fixture']
 def file(self,name,raw=b'PUBLIC',mode=0o600):
  p=self.root/name;p.parent.mkdir(parents=True,exist_ok=True,mode=0o700);p.write_bytes(raw);p.chmod(mode);return p
 def installed(self):
  return self.N.install(self.C,self.S,self.K,self.A,self.T,fences=self.stack)
 def backend(self,site='web'):
  Site=self.installed();b=Site.__new__(Site);b.root=self.root;b.site=site;b.control={'nonce':'a'*32,'runtime_env_delta':{}}
  b.shared={'reader_units':{},'legacy_units':{},'public_image_env_delta':{}};b.current_version='5.6.1';b.original={};b.current={};b.role_ids={};b.images={};b.by_role={};b.admitted={};b.apply=False;b.recovering=False;b.guard=None
  for index,role in enumerate(self.C.SITES[site]['roles']):
   f=self.build(role,site);version='5.5.0' if site=='bot' else '5.6.1' if role=='app' else '2.40.3'
   f.old_row['id']=format(index+1,'064x');f.old_row['config']['Hostname']=f.old_row['id'][:12]
   for network in f.old_row['networks'].values():network['DNSNames']=[f.old['services'][role]['container_name'],f.old_row['id'][:12]]
   f.old_row['config']['Labels']['com.docker.compose.version']=f.scope['compose_version']=version
   f.old['services'][role]['restart']='no';f.old_row['host']['RestartPolicy']={'Name':'always','MaximumRetryCount':0}
   paths=['/public/'+site+'/docker-compose.yml','/public/'+site+'/normal.yml']
   if site=='web' and role=='app':paths+=['/public/web/rollback-app.override.json']
   f.old_row['config']['Labels']['com.docker.compose.project.config_files']=','.join(paths)
   f.scope['compose_files']=[{'path':p,'bytes':1,'sha256':'b'*64} for p in paths];f.scope['env_files']=[]
   actual={'Id':f.old_row['id'],'Name':f.old_row['name'],'Image':f.old_row['image'],'Config':f.old_row['config'],'HostConfig':f.old_row['host'],
    'Mounts':f.old_row['mounts'],'NetworkSettings':{'Networks':f.old_row['networks']},'RestartCount':f.old_row['restarts'],
    'State':{'Running':f.old_row['running'],'Pid':f.old_row['pid'],'StartedAt':f.old_row['started'],'Paused':False,'Restarting':False,'Dead':False}}
   values={'model':f.old,'actual':actual,'image':f.old_image,'scope':f.scope,'runtime_restart_policy':{'Name':'always','MaximumRetryCount':0}}
   b.admitted[role]=values;b.original[actual['Id']]=self.S.row(actual);b.current[actual['Id']]=deepcopy(self.S.row(actual));b.bind_role(role,values)
   b.shared['public_image_env_delta']=f.scope['public_image_env_delta'];b.new_image=f.new_image;b.candidate=f.new_image['Id']
  # Bind again with the shared independently measured public defaults.
  for role,values in b.admitted.items():b.bind_role(role,values)
  b.select(next(iter(b.admitted)));return b
 def parity_case(self,site):
  b=self.backend(site)
  for role,values in b.admitted.items():
   b.select(role);before=self.S.row(values['actual']);model=deepcopy(values['model']);model['services'][role].update(image=b.candidate,restart='always')
   after=deepcopy(before);after.update(id='e'*64,image=b.candidate,pid=0,running=False,started='0001-01-01T00:00:00Z',restarts=0)
   after['config'].update(Image=b.candidate,Hostname=after['id'][:12],Env=[*b.new_image['Config']['Env'],*(k+'='+v for k,v in model['services'][role]['environment'].items())])
   for network in after['networks'].values():
    for key in ('EndpointID','MacAddress','NetworkID','Gateway','IPAddress','IPv6Gateway','GlobalIPv6Address'):network[key]=''
    network.update(IPPrefixLen=0,GlobalIPv6PrefixLen=0,DNSNames=None)
   labels=after['config']['Labels'];labels.update(b.new_image['Config']['Labels']);labels.update({'com.docker.compose.version':'5.6.1','com.docker.compose.image':b.candidate,
    'com.docker.compose.replace':model['services'][role]['container_name'],'com.docker.compose.project.config_files':','.join([*b.paths,str(b.override)])})
   self.C.configuration_parity(values['model'],model,role,before,after,values['image'],b.new_image,b.scope,b.paths,[*b.paths,str(b.override)],{},stopped=True)
   for mutate in (lambda r:r['host'].update(Privileged=True),lambda r:r['config']['Env'].append('CHANGED=PUBLIC'),lambda r:r['config']['Labels'].update({'com.docker.compose.version':'2.40.3'}),
    lambda r:r['config']['Labels'].pop('com.docker.compose.replace'),lambda r:r['config']['Labels'].pop('com.docker.compose.depends_on')):
    bad=deepcopy(after);mutate(bad)
    with self.assertRaises(RuntimeError):self.C.configuration_parity(values['model'],model,role,before,bad,values['image'],b.new_image,b.scope,b.paths,[*b.paths,str(b.override)],{},stopped=True)
 def test_native_three_historical_labels_and_current_creation_are_independently_checked_by_real_C(self):self.parity_case('bot')
 def test_web_distinct_historical_labels_and_current_creation_are_independently_checked_by_real_C(self):self.parity_case('web')
 def test_per_role_prefix_and_explicit_runtime_restart_are_preserved_in_both_overrides(self):
  b=self.backend();prefixes={}
  for role in b.admitted:
   b.select(role);prefixes[role]=b.compose();self.assertEqual(len(b.paths),3 if role=='app' else 2)
   for candidate in (False,True):
    body=json.loads(b.override_bytes(candidate));self.assertEqual(set(body['services']),{role});self.assertEqual(body['services'][role]['restart'],'always')
    self.assertNotIn('environment',body['services'][role]);self.assertEqual(b.old_model['services'][role]['restart'],'no')
  self.assertIn('/public/web/rollback-app.override.json',prefixes['app']);self.assertNotIn('/public/web/rollback-app.override.json',prefixes['sync_worker'])
 def test_all_service_environment_references_are_admitted_before_any_full_render(self):
  b=self.backend();calls=[]
  def command(argv,**kw):
   calls.append(argv)
   if '--no-env-resolution' in argv:return self.C.encoded({'name':b.scope['project'],'services':{'other':{'env_file':[{'path':'/unknown/private.env'}]}}})
   raise AssertionError('full render must not run')
  b.cmd=command
  with self.assertRaisesRegex(RuntimeError,'unbound_env_reference'):b.render()
  self.assertEqual(len(calls),1)
 def test_exact_current_docker_and_compose_versions_never_replace_historical_scope(self):
  b=self.backend();b.shared['docker_version']='32.0.0';old=deepcopy(b.scope)
  b.docker=lambda *a,**k:b'v5.6.1\n' if a[:2]==('compose','version') else b'32.0.0\n';b.version_check();self.assertEqual(b.scope,old)
  b.docker=lambda *a,**k:b'5.6.2\n'
  with self.assertRaisesRegex(RuntimeError,'compose_version'):b.version_check()
 def test_pre_stop_full_normal_runtime_barrier_refuses_business_drift_before_command(self):
  b=self.backend();role=b.selected;cid=b.role_ids[role];b.inspect=lambda _:deepcopy(b.current[cid]);b.current[cid]['config']['Env'].append('CHANGED=PUBLIC')
  with self.assertRaisesRegex(RuntimeError,'normal_environment'):b.stop(role,cid)
 def test_start_reinspects_exact_never_started_candidate_before_any_start_request(self):
  b=self.backend();role=b.selected;row=deepcopy(b.current[b.role_ids[role]]);row.update(id='e'*64,pid=0,running=False,restarts=0,started='0001-01-01T00:00:00Z')
  changed=deepcopy(row);changed.update(pid=1200,running=True,started='2026-10-26T00:00:00Z');b.inspect=lambda _:changed
  with self.assertRaisesRegex(RuntimeError,'created_state_unproven'):b.start(role,row)
 def test_public_machine_444_and_644_have_real_retained_fd_and_late_metadata_check(self):
  machine='08457037af994cf4b39feab45b49ea34';p=self.file('machine-id',(machine+'\n').encode(),0o444)
  for mode in (0o444,0o644):
   p.chmod(mode)
   with ExitStack() as stack:m=self.N.Machine(p,stack,machine);m.check();fd=m.fd;self.assertEqual(os.fstat(fd).st_uid,0)
   with self.assertRaises(OSError):os.fstat(fd)
  with self.assertRaisesRegex(RuntimeError,'machine_changed'),ExitStack() as stack:
   self.N.Machine(p,stack,machine);p.chmod(0o600)
 def test_invalid_machine_metadata_and_replacement_refuse_before_payload_read(self):
  machine='08457037af994cf4b39feab45b49ea34';p=self.file('machine-id',(machine+'\n').encode(),0o600)
  with patch.object(self.N.os,'open',side_effect=AssertionError('invalid open')),self.assertRaisesRegex(RuntimeError,'machine_identity'),ExitStack() as stack:self.N.Machine(p,stack,machine)
  p.chmod(0o444);opening=os.open
  def swapped(path,*a,**kw):
   p.rename(self.root/'old-machine');self.file('machine-id',(machine+'\n').encode(),0o444);return opening(path,*a,**kw)
  with patch.object(self.N.os,'open',side_effect=swapped),patch.object(self.N.os,'pread',side_effect=AssertionError('replacement payload read')),self.assertRaisesRegex(RuntimeError,'machine_identity'),ExitStack() as stack:self.N.Machine(p,stack,machine)
 def test_outer_permanent_and_closed_guard_checks_outlive_inner_source_finalizers(self):
  self.installed();permanent=self.file('permanent',b'OPAQUE NEVER READ');guardpath=self.root/'guard';control={'nonce':'a'*32,'controller_commit':'b'*40}
  with self.assertRaisesRegex(RuntimeError,'held_changed'),ExitStack() as outer,ExitStack() as inner:
   with patch.object(self.S.os,'pread',side_effect=AssertionError('opaque payload read')):self.S.Held(permanent,outer,expected=self.S.identity(permanent.stat()),opaque=True,lock=True)
   guard=self.S.Guard(guardpath,control,outer);guard.close();inner.callback(permanent.chmod,0o644)
  with self.assertRaisesRegex(RuntimeError,'guard_cleanup'),ExitStack() as outer,ExitStack() as inner:
   guard=self.S.Guard(guardpath,control,outer);guard.close();inner.callback(lambda:self.file('guard',b'OWNED REPLACEMENT'))
  guardpath.unlink()
  with self.assertRaisesRegex(RuntimeError,'guard_cleanup'),ExitStack() as outer,ExitStack() as inner:
   guard=self.S.Guard(guardpath,control,outer);guard.close();inner.callback(os.fchmod,guard.held.fd,0o644)
 def test_source_cold_and_peer_bootstrap_retain_exact_fd_and_preserve_primary(self):
  p=self.file('wrapper.py',b'def main():return 0\n');pin=sha256(p.read_bytes()).hexdigest()
  with patch.object(sys,'argv',['bootstrap',str(p),pin]),self.assertRaises(SystemExit) as done:exec(compile(self.N.BOOTSTRAP,'public-bootstrap','exec'),{})
  self.assertEqual(done.exception.code,0)
  with self.assertRaisesRegex(RuntimeError,'source_changed'):
   with self.N.cold(p,pin):p.chmod(0o644)
  p.chmod(0o600)
  with self.assertRaisesRegex(ValueError,'primary'):
   with self.N.cold(p,pin):p.chmod(0o644);raise ValueError('primary')
 def test_successful_main_return_cannot_suppress_a_late_bootstrap_source_fault(self):
  p=self.file('late-wrapper.py',b'from pathlib import Path\ndef main():\n Path(__file__).chmod(0o644)\n return 0\n');pin=sha256(p.read_bytes()).hexdigest()
  with patch.object(sys,'argv',['bootstrap',str(p),pin]),self.assertRaises(AssertionError):exec(compile(self.N.BOOTSTRAP,'public-bootstrap','exec'),{})
 def test_missing_actual_evidence_never_reaches_a_normal_runtime_collector(self):
  self.installed()
  with self.assertRaises(Exception):self.C.evidence({'gates':{'canonical_smoke':{}}},lambda _:b'{}',int(time.time()))
  self.assertIsNone(self.N.unarmed()['image']);self.assertIsNone(self.N.unarmed()['gates'])
  self.assertFalse(self.N.unarmed()['security_accepted']);self.assertFalse(self.N.unarmed()['full_matrix_accepted'])
 def test_actual_peer_argv_propagates_only_explicit_Root_apply_and_confirmation(self):
  control={'nonce':'a'*32,'source_files':{self.N.ENTRY:{'sha256':'b'*64}}}
  for apply in (False,True):
   argv=self.N.peer_argv(self.K,self.C,control,apply=apply);self.assertEqual(argv[:-1],self.K.SSH);remote=shlex.split(argv[-1])
   self.assertEqual('--apply' in remote,apply);self.assertEqual('--confirm' in remote,apply)
   if apply:self.assertEqual(remote[-3:],['--apply','--confirm',self.N.CONFIRM])
   self.assertIn('--peer',remote);self.assertIn('--chdir='+self.C.SITES['web']['workdir'],remote)
 def test_check_peer_cannot_begin_or_issue_a_mutating_client_command(self):
  b=self.backend();control={'nonce':'a'*32,'recovery_deadline':int(time.time())+60};b.deadline=time.monotonic()+60
  request={'schema':'canonical_app_activation_request/1','operation':self.N.OP,'nonce':control['nonce'],'control_sha256':self.C.control_binding(control),
   'index':1,'action':'begin','service':None,'native_terminal_sha256':None}
  with patch.object(self.S,'Site',return_value=b),patch.object(self.K,'write'),patch.object(self.K,'read',return_value=request):
   with self.assertRaisesRegex(RuntimeError,'apply_not_authorized'):self.K.peer(control,[],self.stack,False)
  self.assertIsNone(b.guard);self.assertFalse(b.apply)
  with self.assertRaisesRegex(RuntimeError,'apply_not_authorized'):b.cmd(['/usr/bin/docker','stop','1'*64],mutation=True)
 def test_real_site_constructor_uses_fresh_per_role_RAM_and_retains_opaque_owned_fences(self):
  # This actual host-namespace gate is intentionally not mocked. Root runs
  # the module inside the established separate-init/CAP_CHOWN fixture lane.
  self.assertEqual(Path('/proc/self/ns/mnt').stat().st_ino,Path('/proc/1/ns/mnt').stat().st_ino)
  b=self.backend();Site=type(b);roots={site:self.root/site for site in ('bot','web')}
  for path in roots.values():path.mkdir(mode=0o700);(path/'attempts').mkdir(mode=0o700);(path/'controls').mkdir(mode=0o700)
  marker=self.file('permanent-marker',b'OPAQUE NEVER READ');machine=self.file('machine-id',b'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\n',0o444)
  now=int(time.time());control={**self.N.unarmed(),'nonce':'a'*32,'controller_commit':'b'*40,'issued_at':now,'forward_deadline':now+250,'recovery_deadline':now+500,
   'image':deepcopy(self.C.IMAGE),'runtime_env_delta':{},'source_files':{leaf:{'bytes':1,'sha256':pin} for leaf,pin in self.N.PINS.items()},
   'gates':{'canonical_smoke':{'name':'canonical-smoke.zip','bytes':1,'sha256':'c'*64},'host_loads':{s:{'name':s+'-load.json','bytes':1,'sha256':'c'*64} for s in ('bot','web')},
    'backup_restore':{'name':'backup.json','bytes':1,'sha256':'c'*64},'schema_compatibility':{'name':'schema.json','bytes':1,'sha256':'c'*64}}}
  control['source_files'][self.N.ENTRY]={'bytes':1,'sha256':'c'*64}
  for leaf in [*self.N.PINS,self.N.ENTRY]:
   body=(ROOT/'deploy/maintenance'/leaf).read_bytes();control['source_files'][leaf]={'bytes':len(body),'sha256':sha256(body).hexdigest()}
   for path in roots.values():dest=path/leaf;dest.write_bytes(body);dest.chmod(0o600)
  for site in ('bot','web'):
   roles={}
   for role in self.C.SITES[site]['roles']:
    scope=deepcopy(b.admitted.get(role,b.admitted['app'])['scope']);base=self.C.SITES[site]['workdir']+('/docker-compose.yml' if site=='bot' else '/docker-compose.iran.yml')
    scope.update(project=self.C.SITES[site]['project'],workdir=self.C.SITES[site]['workdir'],compose_files=[{'path':base,'bytes':1,'sha256':'c'*64},*scope['compose_files'][1:]])
    roles[role]={k:scope[k] for k in ('project','workdir','compose_files','env_files','environment_file_label','compose_version')};roles[role]['container_id']='1'*64
   admission={'schema':'normal_app_restore_admission/1','operation':self.A.OP,'nonce':control['nonce'],'site':site,'machine_id':'b'*32,'issued_at':now,'deadline':now+250,
    'roles':roles,'datastores':{'db':'d'*64,'redis':'e'*64},'helpers':{key:{'path':str(roots[site]/leaf),'sha256':self.N.PINS[leaf]} for key,leaf in
     (('contract','canonical_app_activation_contract.py'),('utilities','canonical_app_activation_site.py'),('capture','bounded_operational_capture.py'))}}
   control['sites'][site]={'machine_id':'b'*32,'normal_admission':admission,'fences':{'marker':self.N.identity(marker.stat()),**({'source':deepcopy(self.C.SOURCE_ID)} if site=='bot' else {})},
    'reader_units':{name:{'LoadState':'loaded','UnitFileState':'enabled','NeedDaemonReload':'no'} for name in (self.C.READER_UNITS if site=='bot' else ())},
    'legacy_units':{'public-legacy.service':{'ActiveState':'inactive','MainPID':'0','ControlPID':'0','UnitFileState':'disabled','NeedDaemonReload':'no'}},
    'public_image_env_delta':deepcopy(b.shared['public_image_env_delta']),'current_compose_version':'5.6.1','docker_version':'32.0.0'}
  body=json.dumps(control).encode();dest=roots['web']/'controls'/(control['nonce']+'.json');dest.write_bytes(body);dest.chmod(0o600)
  with patch.object(self.N,'ROOTS',roots),patch.object(self.N,'__file__',str(roots['web']/self.N.ENTRY)),ExitStack() as outer,ExitStack() as sources:
   loaded,checks,modules=self.N.load('web',control['nonce'],sources,outer);self.assertEqual(loaded,control)
   for check in checks:check()
   self.assertEqual(modules[0].OPERATION,self.N.OP)
  rawrows={r['actual']['Id']:deepcopy(r['actual']) for r in b.admitted.values()};images={r['image']['Id']:r['image'] for r in b.admitted.values()};images[b.candidate]=b.new_image;calls=[]
  def commands(owner,argv,**kw):
   calls.append(argv)
   if argv[:2]==['/usr/bin/systemctl','show']:return b'ActiveState=inactive\nMainPID=0\nControlPID=0\nUnitFileState=disabled\nNeedDaemonReload=no\n'
   if argv[:3]==['/usr/bin/docker','ps','--all']:return ('\n'.join(rawrows)+'\n').encode()
   if argv[:3]==['/usr/bin/docker','inspect','--type']:return json.dumps([rawrows[cid] for cid in argv[4:]]).encode()
   if argv[:3]==['/usr/bin/docker','image','inspect']:return json.dumps([images[argv[3]]]).encode()
   if argv[0]=='/usr/bin/docker' and argv[1:4]==['compose','version','--short']:return b'5.6.1\n'
   if argv[:2]==['/usr/bin/docker','version']:return b'32.0.0\n'
   if argv[:2]==['/usr/bin/docker','stop']:
    rawrows[argv[-1]]['State'].update(Running=False,Pid=0);return b''
   if argv[:2]==['/usr/bin/docker','start']:
    row=rawrows[argv[-1]];row['State'].update(Running=True,Pid=2400,StartedAt='2026-10-26T00:00:00Z')
    for net in row['NetworkSettings']['Networks'].values():
     net.update(NetworkID='3'*64,EndpointID=row['Id'],MacAddress='02:42:ac:12:00:04',Gateway='172.18.0.1',IPAddress='172.18.0.4',IPPrefixLen=16,
      DNSNames=[row['Name'].removeprefix('/'),row['Id'][:12]])
    return b''
   if 'compose' in argv:
    if 'up' in argv:
     role=argv[-1];candidate=str(owner.override) in argv;old=next(r for r in rawrows.values() if r['Config']['Labels']['com.docker.compose.service']==role)
     value=deepcopy(old);identifier=('e' if candidate else 'f')*64;iid=owner.candidate if candidate else b.admitted[role]['image']['Id'];cfg=value['Config']
     value.update(Id=identifier,Image=iid,RestartCount=0);value['State'].update(Running=False,Pid=0,StartedAt='0001-01-01T00:00:00Z')
     cfg.update(Image=iid,Hostname=identifier[:12]);defaults=images[iid]['Config'];spec=owner.new_model['services'][role] if candidate else owner.rollback_model['services'][role]
     cfg['Env']=[*defaults['Env'],*(k+'='+v for k,v in spec['environment'].items())]
     for key in self.C.PROVENANCE_LABELS:cfg['Labels'].pop(key,None)
     cfg['Labels'].update(defaults.get('Labels') or {});cfg['Labels'].update({'com.docker.compose.image':iid,'com.docker.compose.version':'5.6.1',
      'com.docker.compose.replace':spec['container_name'],'com.docker.compose.depends_on':'','com.docker.compose.project.config_files':','.join([*owner.paths,str(owner.override if candidate else owner.rollback_override)])})
     for net in value['NetworkSettings']['Networks'].values():
      for key in ('NetworkID','EndpointID','MacAddress','Gateway','IPAddress','IPv6Gateway','GlobalIPv6Address'):net[key]=''
      net.update(IPPrefixLen=0,GlobalIPv6PrefixLen=0,DNSNames=None)
     del rawrows[old['Id']];rawrows[identifier]=value;return b''
    if 'ps' in argv:return (next(cid for cid,row in rawrows.items() if row['Config']['Labels']['com.docker.compose.service']==argv[-1])+'\n').encode()
    model=deepcopy(owner.old_model)
    if '--no-env-resolution' in argv:return json.dumps(model).encode()
    if kw.get('stdin') is not None:
     for role,spec in json.loads(kw['stdin'])['services'].items():model['services'][role].update(spec)
    else:
     for path in (owner.override,owner.rollback_override):
      if str(path) in argv:
       for role,spec in json.loads(path.read_bytes())['services'].items():model['services'][role].update(spec)
    return json.dumps(model).encode()
   raise AssertionError('unexpected owned fixture endpoint')
  original=os.pread
  def reading(fd,*args):
   self.assertNotEqual(os.fstat(fd).st_ino,marker.stat().st_ino);return original(fd,*args)
  with (patch.object(self.N,'ROOTS',roots),patch.object(self.N,'MACHINE_ID',machine),patch.object(self.S,'MARKER',marker),patch.object(self.S,'RESERVATION',self.root/'absent'),
   patch.object(self.C,'evidence',return_value={'bot':b.candidate,'web':b.candidate}),
   patch.object(self.A,'collect',return_value=({'current_compose_version':'5.6.1'},{'roles':b.admitted,'datastores':{},'aliases':{}})),
   patch.object(Site,'functional',side_effect=lambda *a:None),patch.object(Site,'cgroup',return_value=lambda:None),
   patch.object(Site,'cmd',commands),patch.object(os,'pread',side_effect=reading),ExitStack() as inner):
   admitted=Site('web',control,inner,apply=False)
   self.assertIsNone(admitted.guard);self.assertIsNone(admitted.journal);self.assertEqual(admitted.current_version,'5.6.1')
   self.assertEqual(len(admitted.by_role['app']['paths']),3);self.assertEqual(len(admitted.by_role['sync_worker']['paths']),2)
   self.assertFalse(any(set(argv)&{'stop','start','up','run','kill','load'} for argv in calls))
   admitted.apply=True;admitted.begin();admitted.replace('app');self.assertEqual(admitted.completed,['app'])
   mutation_order=['stop' if argv[1:2]==['stop'] else 'start' if argv[1:2]==['start'] else 'create' for argv in calls if argv[1:2] in (['stop'],['start']) or 'up' in argv]
   self.assertEqual(mutation_order,['stop','create','start']);admitted.recovery();admitted.rollback('app');self.assertEqual(admitted.completed,[])
   self.assertEqual(admitted.inspect(admitted.role_ids['app'])['host']['RestartPolicy'],{'Name':'always','MaximumRetryCount':0})
   admitted.prepare_terminal('rolled_back');admitted.close();self.assertFalse((roots['web']/'activation.guard').exists())
if __name__=='__main__':unittest.main()
