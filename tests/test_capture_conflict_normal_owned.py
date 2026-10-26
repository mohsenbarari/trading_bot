"""Genuine Root-owned public fixtures; no real client, data or serving calls."""
from contextlib import ExitStack,redirect_stdout
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from types import ModuleType,SimpleNamespace
from unittest.mock import patch
import io,json,os,tempfile,time,unittest

ROOT=Path(__file__).resolve().parents[1]
def load(path):
 m=ModuleType('_owned_'+path.stem);m.__file__=str(path);exec(compile(path.read_bytes(),str(path),'exec'),m.__dict__);return m
P=load(ROOT/'tests/test_capture_conflict_normal.py');N=P.N
DENIED=('subprocess.Popen','subprocess.run','os.system','os.popen','os.fork','os.kill','os.killpg','socket.socket',
 'socket.create_connection','socket.getaddrinfo','urllib.request.urlopen','resource.setrlimit','signal.signal','signal.alarm')
class OwnedTests(unittest.TestCase):
 def setUp(self):
  self.assertEqual((os.geteuid(),os.getegid()),(0,0));self.stack=ExitStack();self.addCleanup(self.stack.close)
  for name in DENIED:self.stack.enter_context(patch(name,side_effect=AssertionError('ambient denied')))
  self.temp=Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='.capture-normal-',dir=ROOT)));self.temp.chmod(0o700)
 def public(self,name,raw=b'PUBLIC FIXTURE',mode=0o600):
  p=self.temp/name;p.parent.mkdir(parents=True,exist_ok=True,mode=0o700);p.write_bytes(raw);p.chmod(mode);return p
 def test_preopen_metadata_rejects_wrong_mode_hardlink_and_foreign_owner_without_payload_access(self):
  p=self.public('source.py',b'PUBLIC PYTHON');pin=sha256(p.read_bytes()).hexdigest()
  p.chmod(0o644)
  with patch.object(N.os,'open',side_effect=AssertionError('source opened')),self.assertRaisesRegex(RuntimeError,'normal_source'):
   with N.cold(p,pin):pass
  p.chmod(0o600);alias=p.with_name('linked.py');os.link(p,alias)
  with patch.object(N.os,'open',side_effect=AssertionError('source opened')),self.assertRaisesRegex(RuntimeError,'normal_source'):
   with N.cold(p,pin):pass
  alias.unlink();os.chown(p,1,1)
  try:
   with patch.object(N.os,'open',side_effect=AssertionError('source opened')),self.assertRaisesRegex(RuntimeError,'normal_source'):
    with N.cold(p,pin):pass
  finally:os.chown(p,0,0)
 def test_retained_fd_is_bound_to_preopen_full9_before_any_pread(self):
  p=self.public('sealed.py',b'PUBLIC A');pin=sha256(p.read_bytes()).hexdigest();original=os.open;original_pread=os.pread
  def opening(path,*args,**kwargs):
   if Path(path)==p:
    p.rename(p.with_name('old.py'));self.public('sealed.py',b'PUBLIC B')
   return original(path,*args,**kwargs)
  with (patch.object(N.os,'open',side_effect=opening),patch.object(N.os,'pread',side_effect=AssertionError('replacement payload read')),
   self.assertRaisesRegex(RuntimeError,'normal_source')):
   with N.cold(p,pin):pass
 def test_late_source_replacement_refuses_and_primary_error_is_preserved(self):
  p=self.public('sealed.py',b'PUBLIC');pin=sha256(p.read_bytes()).hexdigest()
  with self.assertRaisesRegex(RuntimeError,'normal_source_changed'):
   with N.cold(p,pin):p.rename(p.with_name('prior.py'));self.public('sealed.py',b'PUBLIC')
  primary=ValueError('PRIMARY PUBLIC FIXTURE')
  try:
   with N.cold(p,pin):
    p.rename(p.with_name('second.py'));self.public('sealed.py',b'PUBLIC');raise primary
  except ValueError as error:self.assertIs(error,primary)
 def test_bootstrap_uses_lstat_and_same_fd_before_pread_and_checks_after_body(self):
  p=self.public('bootstrap.py',b'PUBLIC_BODY_RAN=True\n');original=os.pread;calls=[]
  def reading(fd,*args):calls.append(os.fstat(fd).st_ino);return original(fd,*args)
  with patch.object(N.sys,'argv',['bootstrap',str(p),sha256(p.read_bytes()).hexdigest()]),patch.object(os,'pread',side_effect=reading):
   exec(compile(N.BOOTSTRAP,'public_bootstrap','exec'),{})
  self.assertGreaterEqual(len(calls),3)
  p.chmod(0o644)
  with (patch.object(N.sys,'argv',['bootstrap',str(p),'0'*64]),patch.object(os,'open',side_effect=AssertionError('source opened')),
   self.assertRaises(AssertionError)):exec(compile(N.BOOTSTRAP,'public_bootstrap','exec'),{})
 def test_permanent_fences_never_read_or_hash_payload_and_closed_owned_guard_detaches_only_itself(self):
  H=P.load('capture_conflict_diagnostic.py');N.install_structure(H);p=self.public('permanent',b'OPAQUE NEVER READ');expected=N.identity(p.stat());checks=[]
  with ExitStack() as stack:
   with patch.object(os,'pread',side_effect=AssertionError('permanent payload read')):
    fence=H.MetadataFence(p,expected,stack);fence.check()
   checks.append(fence.check);owner=self.temp/'temporary-owned.guard';guard=H.OwnedGuard(owner,'a'*32,'b'*64,stack);checks.append(guard.check)
   N.detach_closed_guard(H,guard,checks,stack)
   for check in checks:check()
   self.assertFalse(owner.exists());self.assertEqual(os.fstat(guard.fd).st_nlink,0);self.assertIn(fence.check,checks)
  self.assertEqual(p.stat().st_ino,expected[1]);self.assertEqual(p.stat().st_size,expected[6])
 def test_outer_permanent_fd_checkpoint_runs_after_inner_source_finalizers(self):
  H=P.load('capture_conflict_diagnostic.py');p=self.public('permanent-final',b'OPAQUE NEVER READ');expected=N.identity(p.stat())
  with self.assertRaisesRegex(RuntimeError,'fence_changed'):
   with ExitStack() as outer,ExitStack() as inner:
    N.install_structure(H,fence_stack=outer);H.MetadataFence(p,expected,inner)
    source=self.public('public-final.py',b'PUBLIC=True\n');inner.enter_context(N.cold(source,sha256(source.read_bytes()).hexdigest()))
    inner.callback(p.chmod,0o644)
 def test_closed_guard_fd_and_named_absence_remain_checked_after_inner_child_exit(self):
  H=P.load('capture_conflict_diagnostic.py');path=self.temp/'temporary-final.guard';checks=[]
  with self.assertRaisesRegex(RuntimeError,'owner_changed'):
   with ExitStack() as outer,ExitStack() as inner:
    N.install_structure(H,fence_stack=outer);guard=H.OwnedGuard(path,'a'*32,'b'*64,inner);checks.append(guard.check)
    N.detach_closed_guard(H,guard,checks,inner)
    inner.callback(lambda:self.public('temporary-final.guard',b'PUBLIC NEW OWNER RECORD'))
 def native_case(self):
  H=P.load('capture_conflict_diagnostic.py');N.install_structure(H);root=self.temp/'native-peer';root.mkdir(mode=0o700)
  self.stack.enter_context(patch.object(H,'RESERVATION',self.temp/'absent-reservation'))
  now=int(time.time());control={'nonce':'a'*32,'issued_at':now-1,'deadline':now+850,'worker_sha256':'b'*64,
   'public_profile_sha256':'f'*64,'compose':{'directory':'/public/normal','env_reference':'/public/normal.env'},
   'normal':{'scope':None,'current_compose_version':'5.6.1','creation_compose_version':'2.40.3','helpers':{}}}
  for key,leaf in N.PRIMITIVE_NAMES.items():
   p=ROOT/'deploy/maintenance'/('normal_market_activation.py' if key=='market' else leaf);raw=p.read_bytes()
   target=root/leaf;target.write_bytes(raw);target.chmod(0o600)
   control['normal']['helpers'][key]={'path':str(H.ROOTS['web']/control['nonce']/leaf),'bytes':len(raw),'sha256':sha256(raw).hexdigest()}
  N.literal_codes((ROOT/'deploy/maintenance/capture_conflict_normal.py').read_bytes())
  args=SimpleNamespace(nonce=control['nonce'],control_sha256='c'*64,apply=None,observe=True)
  return H,root,control,args
 def test_native_retains_only_strict_closed_peer_failure_and_spends_request_once(self):
  H,root,control,args=self.native_case();failure=N.refusal(RuntimeError('normal_compose_version'));calls=[]
  def capture(argv,seconds,environment):
   calls.append(argv);self.assertEqual(argv[:len(H.SSH)],H.SSH);self.assertIn('--chdir=/public/normal',argv[-1]);return 1,H.encode(failure)
  with ExitStack() as stack:
   value=N.native_run(H,args,root,control,[],stack,capture=capture)
   self.assertEqual(value['peer_failure'],failure);self.assertFalse(value['request_issued']);self.assertFalse(value['retry_allowed'])
   with self.assertRaisesRegex(RuntimeError,'owner_exists'):N.native_run(H,args,root,control,[],stack,capture=capture)
  self.assertEqual(len(calls),1);self.assertFalse(H.RESERVATION.exists())
 def test_nonzero_late_bootstrap_cannot_adopt_an_earlier_printed_green(self):
  H,root,control,args=self.native_case();green=H.result(control,args.control_sha256,'preflight_passed_not_applied')
  with ExitStack() as stack,self.assertRaises(H.Unknown):N.native_run(H,args,root,control,[],stack,capture=lambda *a,**kw:(1,H.encode(green)))
  self.assertTrue((root/'native-observe-request.json').exists());self.assertFalse((root/'native-observe-outcome.json').exists())
 def test_actual_returncode_unknown_does_not_relabel_printed_observation_as_success(self):
  H,root,control,args=self.native_case()
  with ExitStack() as stack,self.assertRaises(H.Unknown):N.native_run(H,args,root,control,[],stack,capture=lambda *a,**kw:(125,H.encode({'status':'client_cleanup_unproved'})))
  self.assertTrue((root/'native-observe-request.json').exists());self.assertFalse(H.RESERVATION.exists())
 def test_actual_wrapper_main_cold_holds_base_then_real_load_admits_wrapper_self_and_opaque_fences(self):
  folder=self.temp/'main';folder.mkdir(mode=0o700);native=folder/'native';web=folder/'web';nonce='a'*32;root=native/nonce;root.mkdir(parents=True,mode=0o700)
  wrapper=folder/'capture_conflict_diagnostic.py';raw=(ROOT/'deploy/maintenance/capture_conflict_normal.py').read_bytes();wrapper.write_bytes(raw);wrapper.chmod(0o600);worker=sha256(raw).hexdigest()
  base=folder/'capture_conflict_diagnostic_base.py';base.write_bytes((ROOT/'deploy/maintenance/capture_conflict_diagnostic.py').read_bytes());base.chmod(0o600)
  marker=self.public('main-marker',b'OPAQUE NEVER READ');source=self.public('main-source',b'');now=int(time.time());future=web/nonce
  binding=lambda name,pin,size=1:{'path':str(future/name),'sha256':pin,'bytes':size}
  control={'schema':'capture_conflict_control/4','operation':P.H.OP,'nonce':nonce,'source_commit':'b'*40,'worker_sha256':worker,
   'issued_at':now-1,'deadline':now+800,'native_machine_id':'c'*32,'web_machine_id':'d'*32,
   'fences':{'native':{'marker':N.identity(marker.stat()),'source':N.identity(source.stat())},'web':{'marker':N.identity(marker.stat())}},
   'compose':{'directory':'/root/.local/security-capture2-compose-parity-20261024-04',
    'files':[{'path':'/root/.local/public-normal/base.yml','sha256':'e'*64},{'path':'/root/.local/public-normal/web.yml','sha256':'f'*64}],
    'env_reference':'/srv/trading-bot/market-pipeline-releases/'+'b'*40+'/web.release.env'},
   'old_source':{'kind':'image-baked','target':P.H.DEST,'sha256':P.H.OLD_SHA,'bytes':111954},
   'foundation_source':{'kind':'image-baked','target':P.H.FOUNDATION_DEST,'sha256':P.H.FOUNDATION_SHA,'bytes':37187},
   'new_source':{'kind':'bind','path':str(future/'private_capture.py'),'target':P.H.DEST,'sha256':P.H.NEW_SHA,'bytes':P.H.NEW_BYTES},
   'public_profile_sha256':'f'*64,'baked_source_worker':binding('rebased_f79_sources.py',N.REBASE_SHA),
   'baked_source_validator':binding('rebased_f79_proof_contract.py',N.WIRE_SHA),
   'historical_source_worker':binding('normal_market_activation.py',N.HISTORICAL_SHA),
   'baked_source_proof':binding('rebased-sources.json','f'*64),
   'source_evidence':{k:binding(name,'f'*64) for k,name in [('load_receipt','load-receipt.json'),('load_control','load-control.json'),('archive_admission','archive-admission.json')]},
   'normal':{'scope':None,'current_compose_version':'5.6.1','creation_compose_version':'2.40.3',
    'helpers':{k:binding(leaf,N.PRIMITIVE_PINS[k]) for k,leaf in N.PRIMITIVE_NAMES.items()}}}
  control['source_evidence']['catalog']=binding('market_candidate_images.json',P.H.CATALOG_SHA)
  control['source_evidence']['profile_evidence']=binding('profile-evidence.zip','faa1ccede53c7c2243aeba4c4ab6f9fa170ee0b8d05441be2c6027cba7d08b42',276161)
  body=P.H.encode(control);path=root/'control.json';path.write_bytes(body);path.chmod(0o600);pin=sha256(body).hexdigest();original=N.module;called=[]
  def modules(raw,path):
   value=original(raw,path)
   if Path(path).name=='capture_conflict_diagnostic_base.py':
    value.ROOTS={'native':native,'web':web};value.MARKER=marker;value.SOURCE=source;value.RESERVATION=folder/'absent-reservation'
   return value
  opening=io.open;pread=os.pread
  def reading(fd,*a):
   self.assertNotIn(os.fstat(fd).st_ino,{marker.stat().st_ino,source.stat().st_ino});return pread(fd,*a)
  def endpoint(H,args,root,control,checks,stack):
   called.append(Path(H.__file__));self.assertEqual(Path(H.__file__),wrapper)
   return H.result(control,pin,'preflight_passed_not_applied')
  with (patch.object(N,'__file__',str(wrapper)),patch.object(N,'module',side_effect=modules),patch.object(N,'native_run',side_effect=endpoint),
   patch.object(io,'open',side_effect=lambda p,*a,**k:io.BytesIO(b'c'*32+b'\n') if str(p)=='/etc/machine-id' else opening(p,*a,**k)),
   patch.object(os,'pread',side_effect=reading),redirect_stdout(io.StringIO()) as output):
   rc=N.main(['--site','native','--nonce',nonce,'--control-sha256',pin,'--observe'])
  self.assertEqual(rc,0,output.getvalue());self.assertEqual(called,[wrapper]);self.assertEqual(json.loads(output.getvalue())['status'],'preflight_passed_not_applied')
 def worker(self,*,apply=False,foreign_env=False,reject_candidate=False):
  H=P.load('capture_conflict_diagnostic.py');N.install_structure(H);f=P.fixture();root=self.temp/('a'*32);root.mkdir(mode=0o700)
  self.stack.enter_context(patch.dict(H.ROOTS,{'web':self.temp,'native':self.temp/'native'}));self.stack.enter_context(patch.object(H,'WEB_OWNER',self.temp/'owned.guard'))
  env=self.public('normal/normal.env',b'OPAQUE_ENV_SENTINEL');secrets=[self.public('normal/'+k,b'OPAQUE_SECRET_SENTINEL') for k in ('first','second')]
  self.stack.enter_context(patch.object(N,'SECRET_PATHS',tuple(map(str,secrets))))
  files=[self.public('normal/base.yml',b'services: {}\n'),self.public('normal/web.yml',b'services: {}\n')]
  now=int(time.time());control={'nonce':'a'*32,'worker_sha256':'b'*64,'issued_at':now,'deadline':now+850,
   'web_machine_id':'c'*32,'compose':{'directory':str(env.parent),'files':[{'path':str(p)} for p in files],'env_reference':str(env)},
   'new_source':{'path':str(root/'private_capture.py')},'normal':{'scope':None,'current_compose_version':'5.6.1','creation_compose_version':'2.40.3','helpers':{}}}
  for key,leaf in N.PRIMITIVE_NAMES.items():
   source=ROOT/'deploy/maintenance'/('normal_market_activation.py' if key=='market' else leaf);raw=source.read_bytes()
   self.assertEqual(sha256(raw).hexdigest(),N.PRIMITIVE_PINS[key]);p=root/leaf;p.write_bytes(raw);p.chmod(0o600)
   control['normal']['helpers'][key]={'path':str(p),'bytes':len(raw),'sha256':sha256(raw).hexdigest()}
  f.control=control;spec=f.model['services'][H.SERVICE]
  for key,p in zip(('first','second'),secrets):f.model['secrets'][key]['file']=str(p)
  for mount in f.row['mounts']:
   if mount['Destination'].startswith('/run/secrets/'):mount['Source']=str(secrets[0] if mount['Destination'].endswith('first') else secrets[1])
  f.row['config']['Labels'].update({'com.docker.compose.project.working_dir':str(env.parent),
   'com.docker.compose.project.config_files':','.join(map(str,files)),'com.docker.compose.project.environment_file':str(env)})
  f.image.update(RootFS={'Type':'layers','Layers':['sha256:'+'f'*64]},Os='linux',Architecture='amd64')
  rows={f.row['id']:f.row}
  for i in range(26):
   row=deepcopy(f.row);cid=('%064x'%(i+1));row.update(id=cid,name='/inert-public-'+str(i),pid=0,running=False);rows[cid]=row
  control['public_profile_sha256']=H.public_profile(rows);calls=[];external=[];mutations=[];reject=[reject_candidate]
  # Only the fixed production namespace is translated into this owned tree;
  # actual C/M still validates the full labels/model/Host/Env below.
  def provenance(row,paths,envref):
   labels=row['config']['Labels'];self.assertEqual(labels['com.docker.compose.project.working_dir'],control['compose']['directory'])
   self.assertEqual(labels['com.docker.compose.project.config_files'],','.join(paths));self.assertEqual(labels['com.docker.compose.project.environment_file'],envref)
  self.stack.enter_context(patch.object(H,'provenance',side_effect=provenance))
  def bounded(argv,clock,*,stdin=None,mutating=False,**kwargs):
   args=argv[argv.index('/usr/bin/docker')+2:];calls.append(args)
   if args==['ps','-aq','--no-trunc']:return ('\n'.join(rows)+'\n').encode()
   if args[:2]==['inspect','--format']:return H.encode(rows[args[-1]])
   if args==['compose','version','--short']:return b'5.6.1\n'
   if args[:2]==['version','--format']:return b'32.0.0\n'
   if args[:2]==['image','inspect']:return H.encode(f.image)
   if args[0]=='compose':
    new='-' in args or str(root/'diagnostic-source.yml') in args
    if 'config' in args:
     if '-' in args:
      raw=os.read(stdin,65537);self.assertEqual(json.loads(raw),{'services':{H.SERVICE:{'volumes':[P.new_model(f)['services'][H.SERVICE]['volumes'][-1]]}}})
     if '--no-env-resolution' in args:return H.encode({'services':{H.SERVICE:{'env_file':[{'path':'/UNBOUND' if foreign_env else str(env)}]}}})
     return H.encode(P.new_model(f) if new else f.model)
    target=next(r for r in rows.values() if r['name']==f.row['name']);mutations.append('stop' if 'stop' in args else 'create')
    if 'stop' in args:target.update(pid=0,running=False,restarting=False);return b''
    if 'up' in args:
     replacement=P.new_row(f,bound=new);replacement['id']=('d' if new else 'e')*64;replacement['config']['Hostname']=replacement['id'][:12]
     if reject[0] and new:replacement['config']['Cmd']=['REJECTED_OWNED_CANDIDATE'];reject[0]=False
     rows.pop(target['id']);rows[replacement['id']]=replacement;return b''
   if args[0]=='start':
    target=rows[args[-1]];target.update(pid=456,running=True,started='2026-10-26T00:00:00Z',restarts=0);mutations.append('start');return b''
   raise AssertionError('unexpected public fixture command')
  self.stack.enter_context(patch.object(H,'bounded',side_effect=bounded));factory=N.normal_backend
  def backend(*a):
   cls=factory(*a);cls.legacy_owners=lambda self:external.append('legacy')
   cls.bind_cgroup=lambda self,row:lambda:None;cls.observe=lambda self,cid:None;return cls
  self.stack.enter_context(patch.object(N,'normal_backend',side_effect=backend));self.stack.enter_context(patch.object(N,'verify_rebase',side_effect=lambda *a:external.append('rebase')))
  original=Path.cwd();os.chdir(env.parent)
  try:
   with ExitStack() as stack:
    checks=[];proof=N.web_run(H,control,'b'*64,root,stack,checks,observe=True)
    if apply:
     control['normal']['scope']=proof['normal_scope'];proof=N.web_run(H,control,'b'*64,root,stack,checks,True)
    for check in checks:check()
    return proof,mutations,calls,external
  finally:os.chdir(original)
 def test_actual_normal_worker_observation_has_no_guard_no_mutation_and_never_reads_opaque_payload(self):
  original=os.pread
  def reading(fd,*a):
   s=os.fstat(fd)
   for p in self.temp.glob('normal/*'):
    if p.name in {'normal.env','first','second'}:self.assertNotEqual(s.st_ino,p.stat().st_ino)
   return original(fd,*a)
  with patch.object(os,'pread',side_effect=reading):proof,mutations,calls,external=self.worker()
  self.assertEqual(mutations,[]);self.assertIn('rebase',external);self.assertFalse(proof['diagnostic_applied']);self.assertFalse(proof['activation_authority'])
  self.assertNotIn('PRIVATE_RAM_SENTINEL',json.dumps(proof));self.assertFalse((self.temp/'owned.guard').exists())
 def test_foreign_service_env_stops_before_full_render_or_any_mutation(self):
  with self.assertRaisesRegex(RuntimeError,'normal_unbound_env'):self.worker(foreign_env=True)
 def test_complete_sealed_lifecycle_closes_owned_guard_then_all_final_checkers_still_pass(self):
  proof,mutations,_,_=self.worker(apply=True);self.assertEqual(proof['status'],'diagnostic_active_incident_open')
  self.assertEqual(mutations,['stop','create','start']);self.assertTrue(proof['web_guard_closed']);self.assertFalse((self.temp/'owned.guard').exists())
 def test_known_never_started_rejected_candidate_rolls_back_to_five_baked_mounts(self):
  proof,mutations,_,_=self.worker(apply=True,reject_candidate=True);self.assertEqual(proof['status'],'rolled_back_incident_open')
  self.assertEqual(mutations,['stop','create','stop','create','start']);self.assertTrue(proof['web_guard_closed'])
if __name__=='__main__':unittest.main()
