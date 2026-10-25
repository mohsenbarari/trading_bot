"""Genuine-root owned public fixtures; no host, network or image operations."""
from contextlib import ExitStack,redirect_stdout
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from types import ModuleType,SimpleNamespace
from unittest.mock import Mock,patch
import ast,fcntl,importlib.util,inspect,io,json,os,sys,tempfile,time,unittest

ROOT=Path(__file__).resolve().parents[1]
PUBLIC_EVIDENCE=ROOT/'tests/fixtures/canonical_app'
PUBLIC_EVIDENCE_PINS={
 'canonical-build.zip':('canonical-build-665220a2-run40191605131.zip',336678,'eac66d11595ae9bd7a4478f5b126ac77e4d36b81dab25613f0d951c833d7b653'),
 'canonical-smoke.zip':('canonical-smoke-665220a2-run40196208692.zip',392398,'5cd1ab49b517bcd04c7adc1e883ecf351020f96b16ccfec70278037c5c013e99'),
}
DENIED=('subprocess.Popen','subprocess.run','os.system','os.popen','os.fork','os.kill','os.killpg',
 'socket.socket','socket.create_connection','socket.getaddrinfo','urllib.request.urlopen','ctypes.CDLL',
 'resource.setrlimit','signal.signal','signal.alarm')
def fence(stack):
 for target in DENIED:stack.enter_context(patch(target,side_effect=AssertionError('ambient operation forbidden')))
def load(name,path):
 spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
with ExitStack() as guard:
 fence(guard);A=load('_canonical_app_stores01',ROOT/'deploy/maintenance/canonical_app_candidate_images.py')

class CanonicalAppStoreTests(unittest.TestCase):
 def setUp(self):
  self.assertEqual((os.geteuid(),os.getegid()),(0,0),'genuine root public fixture required')
  self.stack=ExitStack();self.addCleanup(self.stack.close);fence(self.stack)
  self.root=Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='canonical-store-public-fixture-')));self.root.chmod(0o700)
  old=os.umask(0o077);self.addCleanup(os.umask,old);self.serial=0
 def private(self,path,raw):
  path.parent.mkdir(mode=0o700,parents=True,exist_ok=True);path.write_bytes(raw);path.chmod(0o600);return path
 def workspace(self):
  self.serial+=1;root=self.root/str(self.serial);root.mkdir(mode=0o700)
  for name in ('source','controls','attempts','evidence'):(root/name).mkdir(mode=0o700)
  self.private(root/'source/market_candidate_images.py',(ROOT/'deploy/maintenance/market_candidate_images_v2.py').read_bytes())
  for path in A.HELPERS:self.private(root/'source'/Path(path).name,(ROOT/path).read_bytes())
  self.private(root/A.WORKER,(ROOT/'deploy/maintenance'/A.WORKER).read_bytes())
  self.private(root/'canonical_app_candidate_images.json',A.CATALOG_SOURCE)
  for name,(fixture,size,pin) in PUBLIC_EVIDENCE_PINS.items():
   raw=(PUBLIC_EVIDENCE/fixture).read_bytes();self.assertEqual(len(raw),size);self.assertEqual(sha256(raw).hexdigest(),pin)
   self.private(root/'evidence'/name,raw)
  self.stack.enter_context(patch.object(A,'ROOTS',{'bot':root,'web':root}));M=A.load_engine(self.stack,'bot')
  ctx=M.Context.__new__(M.Context);ctx.engine=M;ctx.root=root;ctx.stack=self.stack;ctx.checks=list(M.bootstrap_checks)
  ctx.modules={}
  for path,pin in A.HELPERS.items():
   raw,check=M.seal(self.stack,root/'source'/Path(path).name,pin['sha256']);ctx.checks.append(check)
   mod=ModuleType('_private_fixture_'+Path(path).stem);exec(compile(raw,str(root/'source'/Path(path).name),'exec'),mod.__dict__);ctx.modules[Path(path).name]=mod
  ctx.D=ctx.modules['datastore_candidate_contract.py'];ctx.T=ctx.modules['runtime_image_archive_transport.py'];ctx.L=ctx.modules['admit_security_component_image_reuse.py']
  ctx.H=ctx.modules['run_canonical_app_ci_smoke.py'];ctx.D.legacy_configs=lambda config,moby:ctx.H.canonical_legacy_configs(config,moby,ctx.D)
  ctx.catalog=A.catalog_contract();ctx.spec={**ctx.catalog['profiles'][0],'site':'web'};ctx.site='web';ctx.end=time.monotonic()+60
  ctx.pin='a'*64;ctx.op={'operation':A.OPERATION,'phase':'admit-existing-web','nonce':'b'*32,'worker_sha256':'c'*64,
   'not_before':int(time.time())-1,'deadline':int(time.time())+60,'admission':'canonical_app-admission.json','admission_sha256':None}
  ctx.args=SimpleNamespace(phase='admit-existing-web');ctx.budget=0;ctx.requested=False;ctx.baseline={'image_map':[],'machine_id':'f'*32}
  ctx.capacity=lambda *args:ctx.check();ctx.work=root/'attempts/canonical_app-admit-existing-web'
  return M,ctx
 def test_fixed_actual_catalog_and_cold_isolated_engine_do_not_adopt_old_authorities(self):
  M,ctx=self.workspace();self.assertEqual(A.CATALOG_SOURCE,M.encode(A.catalog_contract()));self.assertEqual(sha256(A.CATALOG_SOURCE).hexdigest(),A.CATALOG_SHA)
  self.assertEqual([r['profile'] for r in ctx.catalog['profiles']],['canonical_app']);self.assertEqual(ctx.catalog['actual_ci']['run_id'],40196208692)
  self.assertEqual(ctx.catalog['existing_tar_identity'],[2049,414928,33152,0,0,1,452719104,1792933419468181620,1792933419468181620])
  self.assertEqual(M.PHASES,('admit-existing-web','load-web','load-native'));self.assertNotIn('receive-web',M.PHASES)
  self.assertEqual(sha256((ROOT/'deploy/maintenance/market_candidate_images_v2.py').read_bytes()).hexdigest(),A.ENGINE_SHA)
  old=load('_unmodified_market06',ROOT/'deploy/maintenance/market_candidate_images_v2.py');self.assertNotEqual(old.OPERATION,M.OPERATION)
  for name in ('load_native','stream_web','load_web','image','read_part'):
   if name=='image':continue
   self.assertEqual(ast.dump(ast.parse(__import__('inspect').getsource(getattr(M,name)))),ast.dump(ast.parse(__import__('inspect').getsource(getattr(old,name)))))
 def test_actual_authenticated_ci_and_build_provide_complete_config_in_ram_only(self):
  M,ctx=self.workspace();data=A.selected_evidence(ctx)
  self.assertEqual(set(data['image']['Config']),{'Cmd','Env','Labels','WorkingDir'});ctx.D.container_config(data['image']['Config'])
  self.assertEqual(data['spec']['config_id'],ctx.spec['candidate_config_id']);self.assertEqual(len(data['spec']['layers']),33)
  self.assertEqual(data['ci_proof']['member_count'],71);self.assertEqual(data['ci_proof']['legacy_compatibility_configs'],33)
  self.assertIs(data,A.selected_evidence(ctx));ctx.check()
  for name in ('canonical-build.zip','canonical-smoke.zip'):
   self.assertTrue((ctx.root/'evidence'/name).exists())
 def test_missing_or_tampered_actual_ci_refuses_without_any_attempt_or_image_call(self):
  for damage in ('missing','tampered','metadata'):
   M,ctx=self.workspace();path=ctx.root/'evidence/canonical-smoke.zip'
   if damage=='missing':path.unlink()
   elif damage=='tampered':path.write_bytes(b'PUBLIC changed fixture archive')
   else:path.chmod(0o644)
   with self.subTest(damage=damage),self.assertRaises((RuntimeError,FileNotFoundError)):A.selected_evidence(ctx)
   self.assertFalse(ctx.work.exists())
 def synthetic(self):
  from tests import test_canonical_app_ci_smoke as fixtures
  M,ctx=self.workspace();helper=fixtures.CanonicalSmokeTests(methodName='runTest');raw,control,config=helper.archive()
  self.assertNotEqual(control['image_id'],ctx.spec['candidate_config_id'],'disjoint image fixture required')
  path=self.private(ctx.root/'existing-public/source.tar',raw);self.stack.enter_context(patch.object(A,'EXISTING_TAR',path))
  ctx.catalog=deepcopy(ctx.catalog);ctx.catalog['existing_tar_identity']=M.identity(path.lstat());ctx.catalog['export']=control
  ctx.spec.update(candidate_config_id=control['image_id'],candidate_rootfs=control['rootfs']['Layers'],tar_sha256=sha256(raw).hexdigest(),archive={'uncompressed_bytes':len(raw)})
  data={'spec':{'profile':'canonical_app','config_id':control['image_id'],'layers':control['rootfs']['Layers'],'archive':{'uncompressed_bytes':len(raw)}},
   'image':{'Config':config['config'],'Comment':ctx.D.expected_inspect_comment(config),'Author':config.get('author',''),'DockerVersion':config.get('docker_version','')},
   'ci_archive_proof_sha256':'d'*64}
  data['ci_proof']=ctx.D.validate_archive(io.BytesIO(raw),len(raw),ctx.end,data,ctx.T,ctx.L);ctx.app_evidence=data
  ctx.catalog['actual_ci']['records']['reports/canonical-archive-admission.json']=data['ci_proof']
  ctx.work.mkdir(mode=0o700);return M,ctx,path,raw,data
 def test_real_33_logical_31_physical_71_member_archive_new_admission_without_copy_or_load(self):
  M,ctx,path,raw,data=self.synthetic();before=M.identity(path.lstat())
  with patch.object(M,'stable',return_value={'image_map':[]}):result=A.admit_existing_web(ctx)
  self.assertEqual(result['status'],'archive_admitted_not_loaded');self.assertIs(result['image_load_requested'],False)
  receipt=M.document((ctx.work/'admission.json').read_bytes());self.assertEqual(receipt['schema'],'canonical_app_archive_admission/1')
  self.assertEqual(receipt['source_identities'],data['ci_proof']['source_identities']);self.assertIs(receipt['whole_typed_config_compared_in_ram'],True)
  self.assertIs(receipt['physical_graph_closed'],True);self.assertIs(receipt['security_accepted'],False)
  self.assertNotIn('Config',receipt);self.assertNotIn('Env',receipt);self.assertEqual(M.identity(path.lstat()),before)
  self.assertFalse((ctx.work/'source.tar').exists());self.assertFalse((ctx.work/'source.zip').exists());ctx.check()
 def test_archive_config_is_compared_to_independent_report_not_to_tar_self(self):
  M,ctx,path,raw,data=self.synthetic();data['image']['Config']=deepcopy(data['image']['Config']);data['image']['Config']['Env']=['PUBLIC DIFFERENT REPORT FIXTURE']
  with patch.object(M,'stable',return_value={'image_map':[]}),self.assertRaises(RuntimeError):A.admit_existing_web(ctx)
  self.assertFalse((ctx.work/'admission.json').exists())
 def test_retained_tar_exact_full9_digest_and_parent_check_survive_direct_stack_close(self):
  M,ctx,path,raw,data=self.synthetic();held=ExitStack();self.addCleanup(held.close);ctx.stack=held
  seen=[];saved=[];original=M.check_parents
  def check(rows):
   if saved:os.fstat(saved[0]);seen.append(True)
   return original(rows)
  try:
   with patch.object(M,'check_parents',side_effect=check):
    fd,size=A.retained_tar(ctx);saved.append(fd);self.assertEqual(size,len(raw));path.parent.chmod(0o750)
    with self.assertRaises(RuntimeError):held.close()
  finally:path.parent.chmod(0o700)
  self.assertTrue(seen);self.assertRaises(OSError,os.fstat,fd)
 def admission(self,M,ctx):
  with patch.object(M,'stable',return_value={'image_map':[]}):A.admit_existing_web(ctx)
  raw=(ctx.work/'admission.json').read_bytes();self.private(ctx.root/'controls/canonical_app-admission.json',raw)
  ctx.op['admission_sha256']=sha256(raw).hexdigest();return raw
 def test_own_archive_schema_rejects_faked_market_receipt_and_changed_fixed_tar(self):
  for damage in ('schema','tar','ci'):
   M,ctx,path,raw,data=self.synthetic();body=self.admission(M,ctx);value=M.document(body)
   if damage=='schema':value['schema']='market_candidate_archive/1'
   elif damage=='tar':value['tar_identity'][1]+=1
   else:value['actual_ci_artifact_sha256']='f'*64
   body=M.encode(value);(ctx.root/'controls/canonical_app-admission.json').write_bytes(body);ctx.op['admission_sha256']=sha256(body).hexdigest()
   with self.subTest(damage=damage),self.assertRaises(RuntimeError):A.source_archive(ctx)
 def test_existing_image_comparison_uses_full_report_config_and_new_public_receipt(self):
  M,ctx=self.workspace();data=A.selected_evidence(ctx);ctx.op['phase']='load-web';admission={'source_identities':data['ci_proof']['source_identities'],
   'archive_sha256':ctx.spec['tar_sha256'],'archive_bytes':ctx.spec['archive']['uncompressed_bytes']}
  actual={'id':ctx.spec['candidate_config_id'],'os':'linux','architecture':'amd64','rootfs':{'Type':'layers','Layers':ctx.spec['candidate_rootfs']},'config':deepcopy(data['image']['Config'])}
  with patch.object(M,'command',return_value=M.encode(actual)):receipt=M.image(ctx,actual['id'],admission)
  self.assertEqual(receipt['schema'],'canonical_app_preloaded_image/1');self.assertIs(receipt['whole_typed_config_compared_in_ram'],True)
  self.assertIs(receipt['security_accepted'],False);self.assertEqual(receipt['serving_roles_activated'],[]);M.public(receipt)
  actual['config']['Env']=['EXPLICIT DIFFERENT PUBLIC CONFIG']
  with patch.object(M,'command',return_value=M.encode(actual)),self.assertRaisesRegex(RuntimeError,'^daemon_config_or_rootfs$'):M.image(ctx,actual['id'],admission)
 def test_native_admission_has_one_readonly_peer_rpc_without_gh_and_unknown_never_retries(self):
  M,ctx=self.workspace();ctx.site='bot';ctx.op['phase']='admit-existing-web';ctx.work=ctx.root/'attempts/native-admission'
  before={'image_map':[]};guard=Mock();machine=self.private(ctx.root/'public-machine-id',b'f'*32);real_path=Path
  with patch.object(A,'Path',side_effect=lambda p:machine if p=='/etc/machine-id' else real_path(p)), \
   patch.object(M,'daemon_policy',return_value={}),patch.object(M,'store_view',return_value={}),patch.object(M,'stable',return_value=before), \
   patch.object(M,'Guard',return_value=guard),patch.object(M,'command_result',return_value=(1,M.encode({'status':'refused_or_unknown'}))) as rpc:
   ctx.baseline.update(daemon={},store={})
   with self.assertRaisesRegex(RuntimeError,'^command_outcome_unknown$'):A.run(ctx,True)
  rpc.assert_called_once();argv=rpc.call_args.args[1];self.assertIn('admit-existing-web',argv);self.assertNotIn('/usr/bin/gh',argv)
  guard.finish.assert_not_called();self.assertTrue((ctx.work/'intent.json').exists());self.assertTrue((ctx.work/'web-request.json').exists())
 def test_noapply_admission_never_creates_guard_attempt_or_peer_rpc(self):
  M,ctx=self.workspace();ctx.baseline.update(daemon={},store={});machine=self.private(ctx.root/'public-machine-id',b'f'*32);real_path=Path
  with patch.object(A,'Path',side_effect=lambda p:machine if p=='/etc/machine-id' else real_path(p)), \
   patch.object(M,'daemon_policy',return_value={}),patch.object(M,'store_view',return_value={}),patch.object(M,'stable',return_value={'image_map':[]}), \
   patch.object(M,'Guard') as guard,patch.object(M,'command_result') as rpc:result=A.run(ctx,False)
  self.assertEqual(result['status'],'public_preflight_passed_not_requested');guard.assert_not_called();rpc.assert_not_called();self.assertFalse(ctx.work.exists())
 def test_post_admission_native_container_or_map_drift_retains_real_owned_guard(self):
  from tests import test_market_candidate_images_v2 as shared
  for damage in ('container','map'):
   M,ctx=self.workspace();ctx.site='bot';ctx.work=ctx.root/'attempts/native-admission'
   helper=shared.Market06Tests(methodName='runTest');helper.now=int(time.time());rows=helper.rows('bot')
   ctx.baseline.update(containers=rows,daemon={},store={});before={'containers':deepcopy(rows),'image_map':[]};after=deepcopy(before)
   if damage=='container':after['containers'][0]['pid']+=1
   else:after['image_map'].append({'id':'sha256:'+'f'*64,'repository':'public-drift-fixture','tag':'one'})
   marker=self.private(ctx.root/'M',b'PUBLIC PERMANENT MARKER FIXTURE MUST NOT BE READ');source=self.private(ctx.root/'SOURCE',b'')
   reservation=ctx.root/'reservation';saved={name:M.identity(p.lstat()) for name,p in (('marker',marker),('source',source))};ctx.op['fences']={'bot':saved}
   machine=self.private(ctx.root/'public-machine-id',b'f'*32);real_path=Path;real_pread=os.pread
   def public_only(fd,*args):
    self.assertNotIn(os.fstat(fd).st_ino,[marker.stat().st_ino,source.stat().st_ino],'permanent mutex payload read')
    return real_pread(fd,*args)
   result={'status':'archive_admitted_not_loaded','receipt_sha256':'e'*64,'image_load_requested':False,'security_accepted':False}
   with self.subTest(damage=damage),patch.object(A,'Path',side_effect=lambda p:machine if p=='/etc/machine-id' else real_path(p)), \
    patch.multiple(M,MARKER=marker,SOURCE=source,RESERVATION=reservation,PENDING=ctx.root/'absent'),patch.object(M.os,'pread',side_effect=public_only), \
    patch.object(M,'daemon_policy',return_value={}),patch.object(M,'store_view',return_value={}),patch.object(M,'_snapshot_once',side_effect=[before,after]), \
    patch.object(M,'command_result',return_value=(0,M.encode(result))) as rpc:
    with self.assertRaisesRegex(RuntimeError,'^'+('runtime_changed' if damage=='container' else 'native_runtime_changed')+'$'):A.run(ctx,True)
    rpc.assert_called_once();self.assertTrue(reservation.exists());self.assertTrue((ctx.work/'intent.json').exists())
    self.assertTrue((ctx.work/'web-request.json').exists());self.assertEqual(M.identity(marker.lstat()),saved['marker'])
    self.assertEqual(M.identity(source.lstat()),saved['source']);ctx.check()
 def test_real_control_intake_rejects_old_schema_receive_profile_and_source_drift(self):
  from tests import test_market_candidate_images_v2 as shared
  M,ctx=self.workspace();helper=shared.Market06Tests(methodName='runTest');helper.now=int(time.time());now=int(time.time())
  worker=sha256((ctx.root/A.WORKER).read_bytes()).hexdigest()
  op={'schema':'canonical_app_image_store_control/1','operation':A.OPERATION,'phase':'admit-existing-web',
   'profile':'canonical_app','nonce':'d'*32,'not_before':now-1,'deadline':now+60,'worker_sha256':worker,
   'fences':{'bot':{'source':A.SOURCE_ID[:]},'web':{}},'admission':'canonical_app-admission.json','admission_sha256':None,
   'baselines':{site:{'schema':'canonical_app_image_store_baseline/1','operation':A.OPERATION,'site':site,'observed_at':now,
    'controller_sha256':worker,'runtime_health_issues_open':True,'security_accepted':False,'containers':helper.rows(site)} for site in ('bot','web')}}
  path=ctx.root/'controls'/('d'*32+'.json');args=SimpleNamespace(site='bot',phase='admit-existing-web',control=path,control_sha256=None)
  properties=b'RuntimeMaxUSec=15min\nTimeoutStopUSec=5s\nKillMode=control-group\nSendSIGKILL=yes\n'
  cases=[None,lambda o:o.update(schema='market_image_store_control/1'),lambda o:o.update(phase='receive-web'),
   lambda o:o.update(profile='redis'),lambda o:o['fences']['bot']['source'].__setitem__(1,7),lambda o:o.update(deadline=True)]
  for damage in cases:
   value=deepcopy(op)
   if damage:damage(value)
   raw=M.encode(value);path.write_bytes(raw);path.chmod(0o600);args.control_sha256=sha256(raw).hexdigest()
   with patch.object(M,'scope'),patch.object(M,'command',return_value=properties):
    if damage:
     with self.assertRaises(RuntimeError):A.context(args,self.stack,M)
    else:
     admitted=A.context(args,self.stack,M);self.assertEqual(admitted.spec['site'],'web');admitted.check()
 def test_common_immutable_frames_bind_new_app_receipt_without_old_receipt_adoption(self):
  M,ctx,path,raw,data=self.synthetic();self.admission(M,ctx);admitted=A.source_archive(ctx)
  frame={'schema':'market_image_stream/1','profile':'canonical_app','operation_sha256':ctx.pin,
   'admission_sha256':ctx.op['admission_sha256'],'archive_bytes':admitted['archive_bytes'],
   'archive_sha256':admitted['archive_sha256'],'tar_identity':admitted['tar_identity']}
  M.header(ctx,frame,admitted)
  complete={**frame,'schema':'market_image_stream_complete/1','source_fd_held_and_verified':True};M.header(ctx,complete,admitted,complete=True)
  for change in ({'profile':'a37ec78ec097'},{'admission_sha256':'f'*64},{'source_fd_held_and_verified':False}):
   with self.subTest(change=change),self.assertRaisesRegex(RuntimeError,'^stream_binding$'):M.header(ctx,{**complete,**change},admitted,complete=True)
 def test_main_closes_private_errors_and_never_calls_engine_old_entrypoint(self):
  M,ctx=self.workspace();M.main=Mock(side_effect=AssertionError('old entrypoint forbidden'));out=io.StringIO()
  with patch.object(A,'load_engine',return_value=M),patch.object(A,'context',return_value=ctx), \
       patch.object(A,'selected_evidence',side_effect=RuntimeError('PRIVATE FIXTURE VALUE NEVER EMIT')),patch.object(M.signal,'alarm'),redirect_stdout(out):
   self.assertEqual(A.main(['--site','bot','--phase','admit-existing-web']),1)
  value=json.loads(out.getvalue());self.assertEqual(value['code'],'unexpected_failure');self.assertFalse(value['retry_allowed'])
  self.assertNotIn('PRIVATE',out.getvalue());self.assertNotIn('Env',out.getvalue());M.main.assert_not_called()

 def stream_args(self,M,ctx):
  ctx.op.update(schema='canonical_app_image_store_control/1',phase='load-native',profile='canonical_app',
   worker_sha256=sha256((ctx.root/A.WORKER).read_bytes()).hexdigest())
  raw=M.encode(ctx.op);path=self.private(ctx.root/'controls'/(ctx.op['nonce']+'.json'),raw)
  ctx.pin=sha256(raw).hexdigest();ctx.args=SimpleNamespace(phase='stream-web')
  return ['--site','web','--phase','stream-web','--control',str(path),'--control-sha256',ctx.pin,'--apply',A.CONFIRM]
 def refusal(self,M,ctx,error=None):
  base={'operation':A.OPERATION,'profile':'canonical_app','nonce':ctx.op['nonce'],'control_sha256':ctx.pin,
   'image_config_id':ctx.spec['candidate_config_id']}
  return A.refusal_record(M,(base,()),error or PermissionError(13,'PRIVATE ARGUMENT NEVER EMIT'))
 def test_real_cold_binding_context_failure_emits_closed_frame_received_before_any_native_load(self):
  M,ctx=self.workspace();args=self.stream_args(M,ctx);r,w=os.pipe();self.addCleanup(os.close,r)
  real_emit=A.emit_refusal;out=io.StringIO()
  with patch.object(A,'__file__',str(ctx.root/A.WORKER)),patch.object(A,'load_engine',return_value=M), \
   patch.object(A,'context',side_effect=PermissionError(13,'PRIVATE CONTEXT PATH NEVER EMIT')), \
   patch.object(A,'emit_refusal',side_effect=lambda engine,value:real_emit(engine,value,w)),patch.object(M.signal,'alarm'),redirect_stdout(out):
   self.assertEqual(A.main(args),1)
  os.close(w);packet=os.read(r,8192);self.assertEqual(out.getvalue(),'');self.assertLessEqual(len(packet),4096)
  size=int.from_bytes(packet[:4],'big');self.assertEqual(size,len(packet)-4);value=M.document(packet[4:])
  self.assertEqual(value['exception_class'],'PermissionError');self.assertEqual(value['os_errno'],13)
  self.assertNotIn(b'PRIVATE',packet);self.assertNotIn(b'Env',packet);self.assertEqual(value['control_sha256'],ctx.pin)
  self.assertEqual(value['nonce'],ctx.op['nonce']);self.assertFalse(value['security_accepted'])
  # Exercise the actual immutable Native load function, not a header mock.
  read,write=os.pipe();os.write(write,packet+b'PUBLIC_BODY_MUST_NOT_BE_READ');os.close(write);peek=os.dup(read);self.addCleanup(os.close,peek)
  input_read,input_write=os.pipe();os.close(input_read)
  class Child:
   stdout=os.fdopen(read,'rb');stdin=os.fdopen(input_write,'wb')
   def poll(self):return 1
   def wait(self,timeout=None):return 1
  ctx.site='bot';ctx.spec['site']='native';ctx.args.phase='load-native';ctx.work=ctx.root/'attempts/canonical_app-load-native';ctx.work.mkdir(mode=0o700)
  admitted={'source_identities':{ctx.spec['candidate_config_id']:{}},'prospective_import_bytes':0}
  with patch.object(M,'source_archive',return_value=admitted),patch.object(M,'stable',return_value={'image_map':[]}), \
   patch.object(M.subprocess,'Popen',return_value=Child()) as spawn:
   with self.assertRaisesRegex(RuntimeError,'^app_peer_preheader_refused$'):M.load_native(ctx)
  spawn.assert_called_once();self.assertNotIn('load',spawn.call_args.args[0]);self.assertFalse(ctx.requested)
  self.assertFalse((ctx.work/'load-request.json').exists());self.assertEqual(os.read(peek,64),b'PUBLIC_BODY_MUST_NOT_BE_READ')
  detail=M.failure_details(ctx)['app_peer_preheader_refusal'];self.assertEqual(detail,value);M.public(detail)
 def test_refusal_exact_binding_types_first_header_only_and_no_normal_header_acceptance(self):
  M,ctx=self.workspace();ctx.op['phase']='load-native';value=self.refusal(M,ctx)
  for key,bad in [('nonce','c'*32),('control_sha256','d'*64),('operation','other'),('profile','postgres'),
   ('image_config_id','sha256:'+'e'*64),('os_errno',True),('exception_class','PRIVATE CLASS'),('code','PRIVATE ERROR'),
   ('subphase','PRIVATE PHASE'),('retry_allowed',0),('security_accepted',0),('phase','load-native')]:
   with self.subTest(key=key):
    current=SimpleNamespace(engine=M,spec=ctx.spec,op=ctx.op,pin=ctx.pin)
    with self.assertRaisesRegex(RuntimeError,'^stream_binding$'):M.header(current,{**value,key:bad},{})
    self.assertFalse(hasattr(current,'app_peer_preheader_refusal'))
  for current in (SimpleNamespace(engine=M,D=ctx.D,spec=ctx.spec,op=ctx.op,pin=ctx.pin,app_header_attempted=True),
    SimpleNamespace(engine=M,D=ctx.D,spec=ctx.spec,op=ctx.op,pin=ctx.pin)):
   admitted={'archive_bytes':1,'archive_sha256':'e'*64,'tar_identity':[1]*9}
   with self.assertRaisesRegex(RuntimeError,'^stream_binding$'):M.header(current,value,admitted,complete=not getattr(current,'app_header_attempted',False))
   self.assertFalse(hasattr(current,'app_peer_preheader_refusal'))
 def test_no_diagnostic_after_header_attempt_partial_header_or_body_fault(self):
  for phase in ('before-byte','partial-header','body'):
   M,ctx=self.workspace();args=self.stream_args(M,ctx);r,w=os.pipe();chunks=[]
   def write(current,fd,raw):
    self.assertTrue(M.app_stream_frame_attempted,'flag must precede first frame write')
    if phase=='partial-header':os.write(w,raw[:1])
    if phase=='body' and not chunks:chunks.append(raw);return
    raise PermissionError(13,'PRIVATE WRITE FAILURE')
   def action(current,apply):
    M.frame(current,1,{'schema':'PUBLIC_HEADER'});M.write_all(current,1,b'PUBLIC_BODY')
   with patch.object(A,'__file__',str(ctx.root/A.WORKER)),patch.object(A,'load_engine',return_value=M),patch.object(A,'context',return_value=ctx), \
    patch.object(A,'selected_evidence'),patch.object(A,'run',side_effect=action),patch.object(M,'write_all',side_effect=write), \
    patch.object(A,'emit_refusal') as emit,patch.object(M.signal,'alarm'),redirect_stdout(io.StringIO()) as output:
    self.assertEqual(A.main(args),1);emit.assert_not_called();self.assertEqual(output.getvalue(),'')
   os.close(w);data=os.read(r,8192);os.close(r)
   self.assertEqual(len(data),1 if phase=='partial-header' else 0)
 def test_full_pipe_refusal_is_bounded_independent_of_original_stdout_flags_and_no_fd_leak(self):
  M,ctx=self.workspace();value=self.refusal(M,ctx);r,w=os.pipe();self.addCleanup(os.close,r);self.addCleanup(os.close,w)
  os.set_blocking(w,False);filled=0
  while True:
   try:filled+=os.write(w,b'P'*4096)
   except BlockingIOError:break
  os.set_blocking(w,True);before=fcntl.fcntl(w,fcntl.F_GETFL);fds=set(os.listdir('/proc/self/fd'));start=time.monotonic()
  self.assertFalse(A.emit_refusal(M,value,w));self.assertLess(time.monotonic()-start,1.4)
  self.assertEqual(fcntl.fcntl(w,fcntl.F_GETFL),before);self.assertEqual(set(os.listdir('/proc/self/fd')),fds)
  os.set_blocking(r,False);raw=bytearray()
  while True:
   try:raw.extend(os.read(r,8192))
   except BlockingIOError:break
  self.assertEqual(bytes(raw),b'P'*filled);self.assertFalse(A.emit_refusal(M,value,w),'only one attempt allowed')
 def test_refusal_binding_refuses_wrong_worker_or_control_sha_without_any_emission(self):
  for damage in ('worker','control'):
   M,ctx=self.workspace();args=self.stream_args(M,ctx)
   if damage=='worker':(ctx.root/A.WORKER).chmod(0o644)
   else:args[args.index('--control-sha256')+1]='f'*64
   with patch.object(A,'__file__',str(ctx.root/A.WORKER)),patch.object(A,'load_engine',return_value=M), \
    patch.object(A,'context') as context,patch.object(A,'emit_refusal') as emit,patch.object(M.signal,'alarm'),redirect_stdout(io.StringIO()):
    self.assertEqual(A.main(args),1);emit.assert_not_called();context.assert_not_called()
 def test_actual_context_missing_public_helper_emits_pre_guard_cause_without_host_or_guard_call(self):
  M,ctx=self.workspace();args=self.stream_args(M,ctx)
  (ctx.root/'source/run_canonical_app_ci_smoke.py').unlink();r,w=os.pipe();real_emit=A.emit_refusal
  try:
   with patch.object(A,'__file__',str(ctx.root/A.WORKER)),patch.object(A,'load_engine',return_value=M), \
    patch.object(A,'emit_refusal',side_effect=lambda engine,value:real_emit(engine,value,w)), \
    patch.object(M,'Guard') as guard,patch.object(M,'command') as command,patch.object(M.signal,'alarm'),redirect_stdout(io.StringIO()) as output:
    self.assertEqual(A.main(args),1);guard.assert_not_called();command.assert_not_called();self.assertEqual(output.getvalue(),'')
   os.close(w);w=None;packet=os.read(r,4097);self.assertLessEqual(len(packet),4096)
   self.assertEqual(int.from_bytes(packet[:4],'big'),len(packet)-4);value=M.document(packet[4:])
   self.assertEqual(value['exception_class'],'FileNotFoundError');self.assertEqual(value['os_errno'],2)
   self.assertEqual(A.admit_refusal(ctx,value),value);self.assertNotIn(b'run_canonical_app_ci_smoke',packet)
  finally:
   os.close(r)
   if w is not None:os.close(w)
 def test_old_engine_load_stream_and_frame_functions_remain_exact_ast(self):
  M,ctx=self.workspace();old=load('_frozen_original_frame_functions',ROOT/'deploy/maintenance/market_candidate_images_v2.py')
  for name in ('load_native','load_web','stream_web','read_frame','read_part','write_all'):
   self.assertEqual(ast.dump(ast.parse(inspect.getsource(getattr(M,name)))),ast.dump(ast.parse(inspect.getsource(getattr(old,name)))))
  for current,original,name in ((M.frame,old.frame,'original_frame'),(M.header,old.header,'original_header')):
   held=inspect.getclosurevars(current).nonlocals[name]
   self.assertEqual(ast.dump(ast.parse(inspect.getsource(held))),ast.dump(ast.parse(inspect.getsource(original))))

if __name__=='__main__':unittest.main()
