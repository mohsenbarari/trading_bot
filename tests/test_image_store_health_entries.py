"""Public tracked-source fixtures; no ignored evidence, host APIs or image operations."""
from contextlib import ExitStack,redirect_stdout,contextmanager
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch,Mock
import ast,importlib.util,io,json,os,sys,tempfile,time,unittest

ROOT=Path(__file__).resolve().parents[1]
DENIED=('subprocess.Popen','subprocess.run','os.system','os.popen','os.fork','os.kill','os.killpg','socket.socket',
 'socket.create_connection','socket.getaddrinfo','urllib.request.urlopen','ctypes.CDLL','resource.setrlimit','signal.signal','signal.alarm')
def fence(stack):
 for name in DENIED:stack.enter_context(patch(name,side_effect=AssertionError('ambient operation forbidden')))
def module(name,path):
 spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
with ExitStack()as guard:
 fence(guard)
 APP=module('_new_app_health_entry',ROOT/'deploy/maintenance/canonical_app_candidate_images_v2.py')
 DB=module('_new_db_health_entry',ROOT/'deploy/maintenance/datastore_candidate_images_v3.py')
 P=module('_pure_health_entry_fixture',ROOT/'deploy/maintenance/runtime_health_observation_policy.py')

class HealthEntryTests(unittest.TestCase):
 def setUp(self):
  self.assertEqual((os.geteuid(),os.getegid()),(0,0),'genuine root public fixture lane required')
  self.stack=ExitStack();self.addCleanup(self.stack.close);fence(self.stack)
  self.root=Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='image-health-entry-public-')));self.root.chmod(0o700)
  old=os.umask(0o077);self.addCleanup(os.umask,old);self.serial=0;self.now=int(time.time())
 def private(self,path,raw):
  path.parent.mkdir(mode=0o700,parents=True,exist_ok=True);path.write_bytes(raw);path.chmod(0o600);return path
 def workspace(self,A):
  self.serial+=1;root=self.root/str(self.serial);root.mkdir(mode=0o700)
  for name in('source','controls','attempts','evidence'):(root/name).mkdir(mode=0o700)
  for path,name in [('deploy/maintenance/market_candidate_images_v2.py','source/market_candidate_images.py'),
    ('deploy/maintenance/'+A.WORKER,A.WORKER),
    ('deploy/maintenance/runtime_health_observation_policy.py','source/'+A.HEALTH_POLICY_NAME),
    ('deploy/maintenance/market_candidate_images_v3.py','source/'+A.HEALTH_ADAPTER_NAME)]:
   self.private(root/name,(ROOT/path).read_bytes())
  for path in A.HELPERS:self.private(root/'source'/Path(path).name,(ROOT/path).read_bytes())
  raw=A.CATALOG_SOURCE if A is APP else (ROOT/'deploy/maintenance/datastore_candidate_images_v2.json').read_bytes()
  self.private(root/('canonical_app_candidate_images.json'if A is APP else'datastore_candidate_images.json'),raw)
  self.assertEqual(sha256((root/'source'/A.HEALTH_POLICY_NAME).read_bytes()).hexdigest(),A.HEALTH_POLICY_SHA)
  self.assertEqual(sha256((root/'source'/A.HEALTH_ADAPTER_NAME).read_bytes()).hexdigest(),A.HEALTH_ADAPTER_SHA)
  self.stack.enter_context(patch.object(A,'ROOTS',{'bot':root,'web':root}));M=A.load_engine(self.stack,'bot')
  return root,M
 def rows(self,site,M):
  rows=[]
  for i in range(15 if site=='bot'else 27):
   running=i<(15 if site=='bot'else 16)
   rows.append({'id':format(i+1,'064x'),'image':'sha256:'+format(i+100,'064x'),'running':running,'restarting':False,
    'paused':False,'dead':False,'pid':1000+i if running else 0,'started':'2026-10-25T00:00:00Z','restarts':0,
    'health':'healthy'if running else None,'host':{'ReadonlyRootfs':True},'execution':{'user':'10001:10001'},'mounts':[],'networks':{}})
  if site=='web':
   rows[0].update(id=M.CAPTURE,image=M.F79_IMAGE)
   rows[0]['networks']={name:{'EndpointID':'a'*64,'MacAddress':'02:00:00:00:00:01','NetworkID':'b'*64,'IPAddress':'192.0.2.2'}for name in M.F79_NETWORKS}
  return rows
 def op(self,A,M,root,phase='load-native',nonce='d'*32):
  worker=sha256((root/A.WORKER).read_bytes()).hexdigest();app=A is APP
  baseline={site:{'schema':'canonical_app_image_store_baseline/2'if app else'datastore_image_store_baseline/3',
   'operation':A.OPERATION,'site':site,'observed_at':self.now,'initial_observed_at':self.now,'controller_sha256':worker,'health_policy_sha256':A.HEALTH_POLICY_SHA,
   'runtime_health_issues_open':True,'security_accepted':False,'containers':self.rows(site,M),'image_map':[],'machine_id':'e'*32}for site in('bot','web')}
  for b in baseline.values():b['initial_containers']=deepcopy(b['containers'])
  return {'schema':'canonical_app_image_store_control/2'if app else'datastore_image_store_control/3','operation':A.OPERATION,
   'phase':phase,'profile':'canonical_app'if app else'postgres','nonce':nonce,'not_before':self.now-1,'deadline':self.now+120,
   'worker_sha256':worker,'health_policy_sha256':A.HEALTH_POLICY_SHA,'baselines':baseline,
   'fences':{'bot':{'source':A.SOURCE_ID[:]},'web':{}},'admission':('canonical_app'if app else'postgres')+'-admission.json','admission_sha256':'f'*64}
 def context(self,A,M,root,value=None,site='bot'):
  op=self.op(A,M,root)if value is None else value;path=self.private(root/'controls'/(op['nonce']+'.json'),M.encode(op))
  args=SimpleNamespace(site=site,phase='stream-web'if site=='web'and op['phase']=='load-native'else op['phase'],control=path,control_sha256=sha256(path.read_bytes()).hexdigest())
  with patch.object(M,'scope'),patch.object(M,'command',return_value=b'RuntimeMaxUSec=15min\nTimeoutStopUSec=5s\nKillMode=control-group\nSendSIGKILL=yes\n'):
   return A.context(args,self.stack,M)
 def basic(self,A,M,root,site='bot'):
  ctx=M.Context.__new__(M.Context);ctx.engine=M;ctx.stack=self.stack;ctx.root=root;ctx.site=site;ctx.checks=list(M.bootstrap_checks)
  ctx.op=self.op(A,M,root);ctx.pin='a'*64;ctx.baseline=ctx.op['baselines'][site];ctx.D=M.runtime_contract
  ctx.end=time.monotonic()+120;ctx.budget=0;ctx.requested=False;ctx.args=SimpleNamespace(phase='load-native')
  ctx.catalog=A.catalog_contract()if A is APP else A.derive_catalog(ctx.D)
  ctx.spec={**next(p for p in ctx.catalog['profiles']if p['profile']==ctx.op['profile']),'site':'native'}
  ctx.work=root/'attempts'/(ctx.op['profile']+'-load-native-'+ctx.op['nonce']);ctx.capacity=lambda *a:ctx.check()
  return ctx

 def test_old_sources_catalogs_and_algorithms_remain_immutable_and_engines_private(self):
  expected={'canonical_app_candidate_images.py':'43029071a8e67d55d16a31b413d861a6f5ad446ad1f8e8723d4795e13044b4ae',
   'datastore_candidate_images_v2.py':'743169b3d9aa5c1941013493b868f9867a640071ffc8405310f9023da3d51e8e',
   'market_candidate_images_v2.py':'1041cc2d04100e2f6aebb95dca30c2339de41342b350ef44522c05277e9c146e'}
  for name,pin in expected.items():self.assertEqual(sha256((ROOT/'deploy/maintenance'/name).read_bytes()).hexdigest(),pin)
  for A in(APP,DB):
   root,M=self.workspace(A);self.assertEqual(M.OPERATION,A.OPERATION)
   source=module('_untouched_engine_'+str(self.serial),ROOT/'deploy/maintenance/market_candidate_images_v2.py')
   self.assertNotEqual(source.TOOLS,M.TOOLS)
   for name in('load_native','load_web','stream_web','measured','read_part'):
    self.assertEqual(M.__dict__[name].__code__.co_code,source.__dict__[name].__code__.co_code)
   self.assertEqual(sha256((root/'source/market_candidate_images.py').read_bytes()).hexdigest(),A.ENGINE_SHA)

 def test_fresh_control_and_baseline_policy_binding_reject_old_authorities(self):
  for A in(APP,DB):
   root,M=self.workspace(A);ctx=self.context(A,M,root);self.assertEqual(ctx.op['health_policy_sha256'],A.HEALTH_POLICY_SHA)
   self.assertTrue(ctx.work.name.endswith(ctx.op['nonce']));ctx.check()
   mutations=[lambda o:o.pop('health_policy_sha256'),lambda o:o.update(health_policy_sha256='0'*64),
    lambda o:o.update(schema='canonical_app_image_store_control/1'if A is APP else'datastore_image_store_control/2'),
    lambda o:o['baselines']['web'].update(health_policy_sha256='0'*64),lambda o:o['baselines']['web']['initial_containers'][1]['host'].update(Privileged=True)]
   for mutation in mutations:
    op=self.op(A,M,root);mutation(op)
    with self.subTest(adapter=A.WORKER,mutation=mutation),self.assertRaises((RuntimeError,KeyError)):self.context(A,M,root,op)

 def test_real_health_flaps_preserve_raw_rows_static_state_and_closed_observations(self):
  for A in(APP,DB):
   for site in('bot','web'):
    root,M=self.workspace(A);ctx=self.basic(A,M,root,site);before=deepcopy(ctx.baseline);after=deepcopy(before)
    after['containers'][1]['health']='unhealthy'
    with patch.object(M,'snapshot',return_value=after):returned=M.stable(ctx)
    self.assertEqual(returned['containers'][1]['health'],'unhealthy');self.assertEqual(ctx.baseline,before)
    detail=M.health_details(ctx);self.assertEqual(detail['runtime_health_observations'][0]['observed_health'],'unhealthy')
    self.assertEqual(detail['health_policy_sha256'],A.HEALTH_POLICY_SHA);self.assertIs(detail['runtime_health_issues_open'],True)
    self.assertIs(detail['runtime_health_observations'][0]['security_accepted'],False)
    for field,replacement in(('pid',99999),('started','2026-10-25T00:01:00Z'),('restarts',1),('image','sha256:'+'e'*64),('health','starting')):
     bad=deepcopy(after);bad['containers'][1][field]=replacement
     with self.subTest(field=field),patch.object(M,'snapshot',return_value=bad),self.assertRaises(RuntimeError):M.stable(ctx)

 def test_all_canonical_and_nonce_specific_prior_attempts_refuse_without_reading_or_adoption(self):
  for A in(APP,DB):
   for suffix in('', '-old-unknown', '-health-v1-'+'e'*32, '-'+ 'e'*32):
    root,M=self.workspace(A);op=self.op(A,M,root);prefix=op['profile']+'-'+op['phase'];old=root/'attempts'/(prefix+suffix)
    old.mkdir(mode=0o700);opaque=self.private(old/'intent.json',b'PRIVATE old attempt must not be read')
    real_open=os.open
    def opened(path,*args,**kwargs):
     if Path(path)==opaque:raise AssertionError('old attempt payload read')
     return real_open(path,*args,**kwargs)
    with patch.object(os,'open',side_effect=opened),self.assertRaisesRegex(RuntimeError,'previous_attempt_requires_reconciliation'):
     self.context(A,M,root,op)
    self.assertEqual(opaque.read_bytes(),b'PRIVATE old attempt must not be read')

 def test_disposition_is_durable_before_request_and_late_prior_attempt_prevents_begin(self):
  for A in(APP,DB):
   root,M=self.workspace(A);ctx=self.context(A,M,root);ctx.begin()
   value=M.document((ctx.work/'prior-attempt-disposition.json').read_bytes());self.assertTrue(value['prior_attempts_absent'])
   self.assertEqual(value['same_profile_phase_attempts'],0);self.assertEqual(value['health_policy_sha256'],A.HEALTH_POLICY_SHA)
   self.assertEqual(set(p.name for p in ctx.work.iterdir()),{'intent.json','prior-attempt-disposition.json'})
   with self.assertRaisesRegex(RuntimeError,'previous_attempt_requires_reconciliation'):self.context(A,M,root,self.op(A,M,root,nonce='e'*32))
   root,M=self.workspace(A);ctx=self.context(A,M,root);old=root/'attempts'/(ctx.op['profile']+'-'+ctx.op['phase']+'-other');old.mkdir(mode=0o700)
   with self.assertRaisesRegex(RuntimeError,'previous_attempt_requires_reconciliation'):ctx.begin()
   self.assertFalse(ctx.work.exists())

 def test_real_directory_mutation_during_prior_inventory_refuses_before_begin(self):
  for A in(APP,DB):
   root,M=self.workspace(A);ctx=self.basic(A,M,root);real_scan=os.scandir
   late=root/'attempts'/(ctx.op['profile']+'-'+ctx.op['phase']+'-late')
   @contextmanager
   def changed(fd):
    with real_scan(fd) as entries:yield entries
    late.mkdir(mode=0o700)
   with patch.object(os,'scandir',side_effect=changed),self.assertRaisesRegex(RuntimeError,'attempt_inventory'):A.prior_attempts(ctx)
   self.assertTrue(late.is_dir());self.assertFalse(ctx.work.exists())

 def test_collector_keeps_both_raw_health_states_and_all_other_runtime_fields_exact(self):
  for A in(APP,DB):
   root,M=self.workspace(A);machine=self.private(root/'machine-id',b'e'*32+b'\n');before={'containers':self.rows('web',M),'image_map':[]};after=deepcopy(before)
   after['containers'][1]['health']='unhealthy';pin=sha256((root/A.WORKER).read_bytes()).hexdigest()
   with patch.object(A,'MACHINE',machine),patch.object(M,'store_view',return_value={}),patch.object(M,'daemon_policy',return_value={}), \
     patch.object(M,'temp_metadata',return_value=[]),patch.object(M,'snapshot',side_effect=[before,after]):value=A.collect(M,'web',pin)
   self.assertEqual(value['containers'],after['containers']);self.assertEqual(value['initial_containers'],before['containers'])
   self.assertEqual(value['health_policy_sha256'],A.HEALTH_POLICY_SHA)
   after['containers'][1]['pid']+=1
   with patch.object(A,'MACHINE',machine),patch.object(M,'store_view',return_value={}),patch.object(M,'daemon_policy',return_value={}), \
     patch.object(M,'temp_metadata',return_value=[]),patch.object(M,'snapshot',side_effect=[before,after]),self.assertRaisesRegex(RuntimeError,'snapshot_runtime_unstable'):
    A.collect(M,'web',pin)

 def test_old_db_archive_admission_remains_valid_history_without_current_observation_adoption(self):
  root,M=self.workspace(DB);ctx=self.basic(DB,M,root)
  DB.historical_health(ctx,{'schema':'market_candidate_archive/1','operation_sha256':'f'*64})
  self.assertFalse(hasattr(ctx,'runtime_health_observations'))
  self.assertEqual(ctx.root/'attempts'/(ctx.spec['profile']+'-receive-web')/'source.tar',root/'attempts/postgres-receive-web/source.tar')
  M.need(ctx.op['admission_sha256']!=M.LEGACY_A37_RECEIPT,'source_admission')

 def test_historical_archive_health_uses_expired_producer_control_not_current_load_nonce(self):
  for A in(APP,DB):
   root,M=self.workspace(A);ctx=self.basic(A,M,root);producer=self.op(A,M,root,'admit-existing-web'if A is APP else'receive-web','e'*32)
   producer.update(not_before=self.now-120,deadline=self.now-60);raw=M.encode(producer);pin=sha256(raw).hexdigest()
   self.private(root/'controls'/(producer['nonce']+'.json'),raw);row=producer['baselines']['web']['containers'][1];after=dict(row,health='unhealthy')
   scope={'operation':A.OPERATION,'operation_sha256':pin,'health_policy_sha256':A.HEALTH_POLICY_SHA,'nonce':producer['nonce'],
    'site':'web','not_before':producer['not_before'],'deadline':producer['deadline']}
   record=P.observation(scope,row,after,self.now-90)
   receipt={'operation_sha256':pin,'health_policy_sha256':A.HEALTH_POLICY_SHA,'runtime_health_issues_open':True,'runtime_health_observations':[record]}
   A.historical_health(ctx,receipt);self.assertFalse(hasattr(ctx,'runtime_health_observations'));self.assertEqual(ctx.op['nonce'],'d'*32)
   bad=deepcopy(receipt);bad['runtime_health_observations'][0]['nonce']=ctx.op['nonce']
   with self.assertRaises((RuntimeError,FileNotFoundError)):A.historical_health(ctx,bad)
   bad=deepcopy(receipt);bad['runtime_health_observations'][0]['pid']+=1
   with self.assertRaisesRegex(RuntimeError,'runtime_health_observation'):A.historical_health(ctx,bad)

 def test_frames_keep_footer_fd_proof_and_bind_new_peer_observations(self):
  for A in(APP,DB):
   root,M=self.workspace(A);web=self.basic(A,M,root,'web');native=self.basic(A,M,root,'bot')
   admitted={'archive_bytes':100,'archive_sha256':'b'*64,'tar_identity':[1,2,0o100600,0,0,1,100,3,3]}
   frame={'schema':'market_image_stream/1','profile':web.spec['profile'],'operation_sha256':web.pin,'admission_sha256':web.op['admission_sha256'],**admitted}
   out=[]
   with patch.object(M,'write_all',side_effect=lambda c,fd,raw:out.append(raw)):M.frame(web,9,frame)
   header=M.document(out[-1][4:]);M.header(native,header,admitted)
   after=deepcopy(web.baseline);after['containers'][1]['health']='unhealthy'
   with patch.object(M,'snapshot',return_value=after):M.stable(web)
   footer={**frame,'schema':'market_image_stream_complete/1','source_fd_held_and_verified':True}
   with patch.object(M,'write_all',side_effect=lambda c,fd,raw:out.append(raw)):M.frame(web,9,footer)
   value=M.document(out[-1][4:]);M.header(native,value,admitted,complete=True)
   self.assertEqual(native.peer_runtime_health_observations[0]['observed_health'],'unhealthy')
   bad={**value,'source_fd_held_and_verified':False}
   with self.assertRaises(RuntimeError):M.header(native,bad,admitted,complete=True)
   bad={**value,'runtime_health_observations':[]}
   with self.assertRaises(RuntimeError):M.header(native,bad,admitted,complete=True)

 def test_app_preheader_refusal_preserves_actual_health_observation_and_primary_failure(self):
  root,M=self.workspace(APP);web=self.basic(APP,M,root,'web');native=self.basic(APP,M,root,'bot')
  after=deepcopy(web.baseline);after['containers'][1]['health']='unhealthy'
  with patch.object(M,'snapshot',return_value=after):M.stable(web)
  base={'operation':APP.OPERATION,'profile':'canonical_app','nonce':web.op['nonce'],'control_sha256':web.pin,'image_config_id':web.spec['candidate_config_id']}
  M.SUBPHASE='snapshot_image_inventory';binding=(base,(lambda:None,))
  value=APP.refusal_record(M,binding,RuntimeError('runtime_changed'),web)
  self.assertEqual(value['runtime_health_observations'][0]['observed_health'],'unhealthy')
  with self.assertRaisesRegex(RuntimeError,'app_peer_preheader_refused'):M.header(native,value,{})
  self.assertEqual(native.peer_runtime_health_observations,value['runtime_health_observations'])
  self.assertEqual(native.app_peer_preheader_refusal['code'],'runtime_changed')
  with patch.object(M,'health_details',side_effect=ValueError('PRIVATE diagnostic')):
   unavailable=APP.refusal_record(M,binding,RuntimeError('runtime_changed'),web)
  self.assertTrue(unavailable['runtime_health_diagnostic_unavailable']);self.assertNotIn('PRIVATE',M.encode(unavailable).decode())

 def test_new_load_receipts_keep_full_config_ram_measurement_and_explicit_provenance(self):
  for A in(APP,DB):
   root,M=self.workspace(A);ctx=self.basic(A,M,root);ident=ctx.spec['candidate_config_id'];cfg={'Env':['PRIVATE_FIXTURE=not-output'],'Cmd':['fixture']}
   admission={'source_identities':{ident:{'kind':'Docker_config_identity'}},'archive_sha256':'b'*64,'archive_bytes':100}
   observed={'id':ident,'os':'linux','architecture':'amd64','rootfs':{'Type':'layers','Layers':ctx.spec['candidate_rootfs']},'config':cfg}
   with patch.object(M,'command',return_value=M.encode(observed)),patch.object(M,'selected_evidence',return_value={'image':{'Config':cfg}}):value=M.image(ctx,ident,admission)
   self.assertEqual(value['health_policy_sha256'],A.HEALTH_POLICY_SHA);self.assertEqual(value['health_adapter_sha256'],A.HEALTH_ADAPTER_SHA)
   self.assertEqual(value['controller_sha256'],ctx.op['worker_sha256']);self.assertTrue(value['whole_typed_config_compared_in_ram'])
   self.assertFalse(value['security_accepted']);self.assertNotIn('PRIVATE_FIXTURE',M.encode(value).decode())

 def test_policy_and_adapter_fd_parent_changes_refuse_at_final_stack_close(self):
  for A in(APP,DB):
   with self.assertRaises(RuntimeError),ExitStack() as held:
    saved=self.stack;self.stack=held
    try:root,M=self.workspace(A)
    finally:self.stack=saved
    path=root/'source'/A.HEALTH_POLICY_NAME;path.parent.chmod(0o755)
   path.parent.chmod(0o700)

 def test_failure_diagnostic_doublefault_preserves_primary_and_never_calls_old_entrypoint(self):
  for A in(APP,DB):
   root,M=self.workspace(A);ctx=self.basic(A,M,root);M.main=Mock(side_effect=AssertionError('old entrypoint forbidden'))
   out=io.StringIO();target=patch.object(A,'selected_evidence',side_effect=RuntimeError('runtime_changed'))if A is APP else patch.object(M,'run',side_effect=RuntimeError('runtime_changed'))
   with patch.object(A,'load_engine',return_value=M),patch.object(A,'context',return_value=ctx), \
    patch.object(M.signal,'alarm'),patch.object(M,'failure_details',side_effect=ValueError('PRIVATE diagnostic')), \
    target,redirect_stdout(out):
    self.assertEqual(A.main(['--site','bot','--phase','load-native']),1)
   value=M.document(out.getvalue().encode());self.assertEqual(value['code'],'runtime_changed')
   self.assertTrue(value['runtime_health_diagnostic_unavailable']);self.assertFalse(value['security_accepted'])
   self.assertNotIn('PRIVATE',out.getvalue());M.main.assert_not_called()

 def test_snapshot_and_failure_outputs_keep_original_private_value_guard(self):
  for A in(APP,DB):
   root,M=self.workspace(A);out=io.StringIO()
   with patch.object(A,'load_engine',return_value=M),patch.object(A,'collect',return_value={'Env':['PRIVATE snapshot']}), \
    patch.object(M.signal,'alarm'),patch.object(M,'failure_details',return_value={'Env':['PRIVATE diagnostic']}),redirect_stdout(out):
    self.assertEqual(A.main(['--site','bot','--phase','snapshot','--source-sha256','a'*64]),1)
   value=M.document(out.getvalue().encode());self.assertEqual(value['code'],'private_output')
   self.assertTrue(value['runtime_health_diagnostic_unavailable']);self.assertNotIn('PRIVATE',out.getvalue())
   for bad in(None,[],{'Env':['PRIVATE']}):
    with patch.object(M,'failure_details',return_value=bad):self.assertEqual(A.safe_failure_details(M,None),{'runtime_health_diagnostic_unavailable':True})

 def test_remote_worker_is_new_entry_and_old_legacy_loader_never_selected(self):
  for A in(APP,DB):
   root,M=self.workspace(A);ctx=self.basic(A,M,root);argv=M.remote(ctx,'stream-web')
   self.assertIn(str(root/A.WORKER),argv);self.assertNotIn(str(root/'market_candidate_images.py'),argv)
   self.assertIn('-oStrictHostKeyChecking=yes',argv);self.assertIn('--property=MemoryMax=2G',argv)
   self.assertEqual(argv[-2:],['--apply',A.CONFIRM])

if __name__=='__main__':unittest.main()
