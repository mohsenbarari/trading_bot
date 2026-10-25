"""Genuine-root public fixtures for operation03; no host/network/image actions."""
from contextlib import ExitStack,redirect_stdout
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
import gzip,importlib.util,io,json,os,sys,tempfile,time,unittest,zipfile
from unittest.mock import Mock,patch

ROOT=Path(__file__).resolve().parents[1]
DENIED=('subprocess.Popen','subprocess.run','os.system','os.popen','os.kill','os.killpg',
 'socket.socket','socket.create_connection','socket.getaddrinfo','urllib.request.urlopen',
 'ctypes.CDLL','ctypes.PyDLL','signal.signal','signal.alarm','resource.setrlimit')
def fence(stack):
 for name in DENIED:stack.enter_context(patch(name,side_effect=AssertionError('ambient operation denied')))
def module(name,path):
 spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);sys.modules[name]=m;spec.loader.exec_module(m);return m
with ExitStack() as guard:
 fence(guard);A=module('_datastore_images03',ROOT/'deploy/maintenance/datastore_candidate_images_v2.py')
 from tests import test_datastore_candidate_stage as archive_fixtures
 from tests import test_datastore_candidate_contract_completion as evidence_fixtures

class DatastoreImageTests(unittest.TestCase):
 def setUp(self):
  self.assertEqual((os.geteuid(),os.getegid()),(0,0),'required genuine root fixture lane')
  self.stack=ExitStack();self.addCleanup(self.stack.close);fence(self.stack)
  self.root=Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='owned-datastore-images03-')));self.root.chmod(0o700)
  old=os.umask(0o077);self.addCleanup(os.umask,old);self.serial=0
 def private(self,path,raw):
  path.parent.mkdir(mode=0o700,parents=True,exist_ok=True);path.write_bytes(raw);path.chmod(0o600);return path
 def workspace(self):
  self.serial+=1;folder=self.root/str(self.serial);folder.mkdir(mode=0o700)
  for name in ('source','controls','attempts','evidence'):(folder/name).mkdir(mode=0o700)
  for src,name in [('deploy/maintenance/market_candidate_images_v2.py','source/market_candidate_images.py'),
                   ('deploy/maintenance/datastore_candidate_images_v2.py',A.WORKER),
                   ('deploy/maintenance/datastore_candidate_images_v2.json','datastore_candidate_images.json')]:
   self.private(folder/name,(ROOT/src).read_bytes())
  for src in A.HELPERS:self.private(folder/'source'/Path(src).name,(ROOT/src).read_bytes())
  self.stack.enter_context(patch.object(A,'ROOTS',{'bot':folder,'web':folder}))
  return folder,A.load_engine(self.stack,'bot')
 def basic(self,M,folder,phase='load-native',site='bot',profile='postgres'):
  ctx=M.Context.__new__(M.Context);ctx.engine=M;ctx.stack=self.stack;ctx.checks=list(M.bootstrap_checks)
  ctx.end=time.monotonic()+60;ctx.site=site;ctx.root=folder;ctx.pin='a'*64;ctx.args=SimpleNamespace(phase=phase)
  ctx.op={'phase':phase,'nonce':'b'*32,'admission_sha256':'c'*64};ctx.spec={'profile':profile,'site':'native' if phase=='load-native' else 'web'}
  ctx.D=M.runtime_contract;ctx.T=archive_fixtures.T;ctx.L=archive_fixtures.M;ctx.budget=0;ctx.requested=False
  ctx.work=folder/'attempts'/(profile+'-'+phase);ctx.capacity=lambda *a:ctx.check();return ctx
 def rows(self,site):
  return [{'id':format(i,'064x'),'running':i<(15 if site=='bot' else 16),'health':'unhealthy' if i in (0,1) else None}
          for i in range(15 if site=='bot' else 27)]
 def control(self,M,folder,profile='postgres',phase='load-native',site='bot',change=None):
  now=int(time.time());worker=folder/A.WORKER
  baseline={key:{'schema':'datastore_image_store_baseline/2','operation':A.OPERATION,'site':key,'observed_at':now,
   'controller_sha256':sha256(worker.read_bytes()).hexdigest(),'runtime_health_issues_open':True,'security_accepted':False,
   'containers':self.rows(key)} for key in ('bot','web')}
  op={'schema':'datastore_image_store_control/2','operation':A.OPERATION,'phase':phase,'profile':profile,'nonce':'d'*32,
   'not_before':now-1,'deadline':now+60,'worker_sha256':baseline['bot']['controller_sha256'],'baselines':baseline,
   'fences':{'bot':{'source':A.SOURCE_ID[:]},'web':{}},'admission':profile+'-admission.json','admission_sha256':'e'*64}
  if change:change(op)
  path=self.private(folder/'controls'/(op['nonce']+'.json'),M.encode(op))
  args=SimpleNamespace(site=site,phase='stream-web' if site=='web' and phase=='load-native' else phase,control=path,control_sha256=sha256(path.read_bytes()).hexdigest())
  scope=self.stack.enter_context(patch.object(M,'scope'))
  self.stack.enter_context(patch.object(M,'command',return_value=b'RuntimeMaxUSec=15min\nTimeoutStopUSec=5s\nKillMode=control-group\nSendSIGKILL=yes\n'))
  return A.context(args,self.stack,M),scope
 def test_catalog_is_exact_deterministic_contract_derivation_and_engine_is_isolated(self):
  folder,M=self.workspace();raw=(folder/'datastore_candidate_images.json').read_bytes()
  self.assertEqual(sha256(raw).hexdigest(),A.CATALOG_SHA);self.assertEqual(raw,M.encode(A.derive_catalog(M.runtime_contract)))
  self.assertEqual([(s['profile'],s['candidate_config_id']) for s in M.document(raw)['profiles']],[(n,p[-1]) for n,p in sorted(A.IMAGE_PINS.items())])
  self.assertEqual(M.OPERATION,A.OPERATION);self.assertEqual(M.CONFIRM,A.CONFIRM)
  original=module('_unmodified_market05_fixture',ROOT/'deploy/maintenance/market_candidate_images.py')
  self.assertEqual(original.OPERATION,'security-market-candidate-images-20261025-05');self.assertNotEqual(original.TOOLS,M.TOOLS)
  self.assertEqual(sha256((ROOT/'deploy/maintenance/datastore_candidate_images.py').read_bytes()).hexdigest(),
   'a52d7f6529b0e3c51255af08a3c7d744fc7ee488380153919b8b9e74b77dbf35')
  self.assertEqual(sha256((ROOT/'deploy/maintenance/datastore_candidate_images.json').read_bytes()).hexdigest(),
   '0488328336f8e40447aec6c754d62b6cb32521d54aeeed6d901eb6baa5e7becf')
  self.assertEqual(M.OPERATION,'security-datastore-candidate-images-20261025-03')
  wrong=deepcopy(M.runtime_contract.PROFILES);wrong['redis']['run_id']+=1
  with patch.object(M.runtime_contract,'PROFILES',wrong),self.assertRaisesRegex(RuntimeError,'^catalog_binding$'):A.derive_catalog(M.runtime_contract)
 def test_two_profiles_support_both_targets_and_one_shared_receive_directory(self):
  for profile in ('postgres','redis'):
   for phase,site,target in (('receive-web','web','web'),('load-web','web','web'),('load-native','bot','native'),('load-native','web','native')):
    with self.subTest(profile=profile,phase=phase,site=site):
     folder,M=self.workspace();ctx,scoped=self.control(M,folder,profile,phase,site)
     self.assertEqual(ctx.spec['site'],target);self.assertEqual(ctx.op['admission'],profile+'-admission.json')
     self.assertEqual(ctx.spec['candidate_config_id'],M.runtime_contract.PROFILES[profile]['config_id'])
     scoped.assert_called_once_with(site,ctx.args.phase,'d'*32);ctx.check()
     if phase!='receive-web':self.assertEqual(ctx.root/'attempts'/(profile+'-receive-web')/'source.tar',folder/'attempts'/(profile+'-receive-web')/'source.tar')
 def test_old_or_future_authorities_and_stale_or_incorrect_inventory_refuse(self):
  cases=[(lambda o:o.update(schema='datastore_candidate_operation/1'),'control_scope'),
   (lambda o:o.update(operation='security-datastore-candidate-images-20261025-02'),'control_scope'),
   (lambda o:o.update(operation='security-market-candidate-images-20261025-05'),'control_scope'),
   (lambda o:o.update(app_consumers_completion_sha256='f'*64),'control_fields'),
   (lambda o:o.update(profile='a37ec78ec097'),'profile_scope'),(lambda o:o.update(deadline=True),'deadline'),
   (lambda o:o['fences']['bot']['source'].__setitem__(1,1),'permanent_fence_identity'),
   (lambda o:o['baselines']['web'].update(observed_at=0),'baseline_freshness'),
   (lambda o:o['baselines']['bot']['containers'][0].update(running=False),'container_inventory'),
   (lambda o:o['baselines']['web']['containers'][0].update(running=1),'container_inventory')]
  for change,code in cases:
   folder,M=self.workspace()
   with self.subTest(code=code),self.assertRaisesRegex(RuntimeError,'^'+code+'$'):self.control(M,folder,change=change)
 def test_actual_sampled_unhealthy_status_is_preserved_without_new_health_gate(self):
  folder,M=self.workspace();ctx,_=self.control(M,folder)
  self.assertEqual([r['health'] for r in ctx.baseline['containers'][:2]],['unhealthy','unhealthy'])
  self.assertIs(ctx.baseline['runtime_health_issues_open'],True);self.assertIs(ctx.baseline['security_accepted'],False)
  before=ctx.baseline['containers'];after=deepcopy(before);after[0]['health']='healthy'
  self.assertIs(M.image_store_equivalence('bot',before,after,ctx.D.same,time.time()),False)
 def test_peer_uses_only_new_worker_finite_strict_ssh_and_never_legacy_tar(self):
  folder,M=self.workspace();ctx=self.basic(M,folder);argv=M.remote(ctx,'stream-web')
  self.assertIn(str(folder/A.WORKER),argv);self.assertNotIn(str(folder/'market_candidate_images.py'),argv)
  self.assertIn('-oStrictHostKeyChecking=yes',argv);self.assertIn('--property=MemoryMax=2G',argv)
  self.assertEqual(argv[-2:],['--apply',A.CONFIRM]);ctx.op['admission_sha256']=M.LEGACY_A37_RECEIPT
  with self.assertRaisesRegex(RuntimeError,'^source_admission$'):M.legacy_a37(ctx,{})
 def test_engine_source_final_proof_refuses_actual_parent_drift_and_preserves_primary(self):
  folder,M=self.workspace();path=folder/'source/market_candidate_images.py'
  with self.assertRaisesRegex(RuntimeError,'^protected_parent_changed$'):
   with A.engine_source(path):path.parent.chmod(0o770)
  path.parent.chmod(0o700)
  with self.assertRaisesRegex(RuntimeError,'^explicit_primary_fixture$'):
   with A.engine_source(path):
    path.parent.chmod(0o770);raise RuntimeError('explicit_primary_fixture')
  path.parent.chmod(0o700)
 def test_inherited_f79_restart_is_baseline_bound_once_and_never_security_acceptance(self):
  from tests import test_market_candidate_images as shared
  folder,M=self.workspace();helper=shared.MarketCandidateImageTests(methodName='runTest');helper.stack=self.stack;helper.root=folder
  helper.basic=lambda:self.basic(M,folder)
  with patch.object(shared,'M',M):ctx,before,after=helper.f79_fixture()
  for rows in (before,after,ctx.op['baselines']['web']['containers']):
   for row in rows:row.update(paused=False,dead=False)
  with patch.object(M,'snapshot',return_value={'containers':after,'image_map':[]}):M.stable(ctx);M.stable(ctx)
  self.assertIs(ctx.f79_restart_observation['capture_issue_open'],True);self.assertIs(ctx.f79_restart_observation['security_accepted'],False)
  self.assertEqual(ctx.op['operation'],A.OPERATION)
  for rows in (before,[dict(r,restarts=9) if r['id']==M.CAPTURE else r for r in after],
               [dict(r,health='unhealthy') if r['id']==M.CAPTURE else r for r in after]):
   with patch.object(M,'snapshot',return_value={'containers':rows,'image_map':[]}),self.assertRaisesRegex(RuntimeError,'^runtime_changed$'):M.stable(ctx)
 def warming_context(self,M):
  from tests import test_market_candidate_images_v2 as shared
  helper=shared.Market06Tests(methodName='runTest');helper.now=int(time.time());helper.stack=self.stack
  with patch.object(shared,'M',M):ctx=helper.ctx(health='unhealthy')
  ctx.end=time.monotonic()+60;ctx.D=M.runtime_contract;ctx.op.update(operation=A.OPERATION)
  return ctx
 def test_real_measured_path_forwards_admission_through_adapter_readiness_wrapper(self):
  folder,M=self.workspace();ctx=self.warming_context(M);ctx.requested=True
  new='sha256:'+'e'*64;admission={'source_identities':{new:{'kind':'Docker_config_identity'}}}
  warming=deepcopy(ctx.baseline);warming['image_map'].append({'id':new,'repository':'<none>','tag':'<none>'})
  warming['image_map'].sort(key=lambda r:(r['id'],r['repository'],r['tag']))
  next(r for r in warming['containers'] if r['id']==M.CAPTURE)['health']='starting'
  next(r for r in warming['containers'] if r['id']==M.PROCESSOR)['health']='healthy'
  healthy=deepcopy(warming);next(r for r in healthy['containers'] if r['id']==M.CAPTURE)['health']='healthy'
  expected={'schema':'public_owned_fixture','security_accepted':False}
  with patch.object(M,'_snapshot_once',side_effect=[warming,healthy]) as snapshot,patch.object(M.time,'sleep'), \
       patch.object(M,'image',return_value=expected) as image:
   self.assertEqual(M.measured(ctx,admission),expected)
  self.assertEqual(snapshot.call_count,2);image.assert_called_once_with(ctx,new,admission)
  self.assertEqual(ctx.processor_health_observation['operation_sha256'],ctx.pin)
  self.assertEqual(ctx.processor_health_observation['observed_health'],'healthy')
  self.assertIs(ctx.processor_health_observation['processor_health_issue_open'],True)
  self.assertIs(ctx.processor_health_observation['security_accepted'],False)
  self.assertEqual(ctx.baseline['image_map'],ctx.op['baselines']['web']['image_map'])
 def test_wrapper_never_accepts_unrequested_or_unbound_image_delta_during_wait(self):
  for requested,pass_admission in ((False,True),(True,False)):
   folder,M=self.workspace();ctx=self.warming_context(M);ctx.requested=requested;new='sha256:'+'e'*64
   value=deepcopy(ctx.baseline);value['image_map'].append({'id':new,'repository':'<none>','tag':'<none>'})
   next(r for r in value['containers'] if r['id']==M.CAPTURE)['health']='unhealthy'
   admission={'source_identities':{new:{'kind':'Docker_config_identity'}}} if pass_admission else None
   with self.subTest(requested=requested,pass_admission=pass_admission),patch.object(M,'_snapshot_once',return_value=value), \
        self.assertRaisesRegex(RuntimeError,'^image_map_changed$'):
    M.snapshot(ctx,admission)
 def evidence(self,profile,data=None,tar=None):
  helper=evidence_fixtures.ContractCompletionTests(methodName='runTest');raw,spec,members=helper.evidence(profile)
  if data is None:return raw,spec
  D=archive_fixtures.C;spec.update(config_id=data['spec']['config_id'],layers=data['spec']['layers'])
  config=data['image']['Config'];candidate=D.document(members['reports/candidate.json'])
  candidate.update(image_id=spec['config_id'],candidate_layers=spec['layers'],runtime_config=config)
  image=D.document(members['reports/candidate-image.json']);image.update(Id=spec['config_id'],Config=config,RootFS={'Type':'layers','Layers':spec['layers']},Comment=data['image']['Comment'])
  report=D.document(members['reports/vulnerabilities.json']);report['Metadata']['ImageID']=spec['config_id']
  for name,value in (('candidate',candidate),('candidate-image',image),('vulnerabilities',report)):members['reports/'+name+'.json']=D.encode(value)
  compressed=gzip.compress(tar,mtime=0);spec['archive']={**spec['archive'],'size_bytes':len(compressed),'sha256':sha256(compressed).hexdigest(),'uncompressed_bytes':len(tar)}
  for key,name in (('candidate_sha256','candidate'),('candidate_image_sha256','candidate-image'),('raw_sha256','vulnerabilities')):spec[key]=sha256(members['reports/'+name+'.json']).hexdigest()
  exported=D.document(members['image-export.json']);exported.update(image_id=spec['config_id'],archive=spec['archive'],candidate_receipt_sha256=spec['candidate_sha256'])
  members['image-export.json']=D.encode(exported);spec['export_bytes']=len(members['image-export.json']);spec['export_sha256']=sha256(members['image-export.json']).hexdigest()
  raw=helper.zip(members);spec.update(evidence_bytes=len(raw),evidence_sha256=sha256(raw).hexdigest());return raw,spec
 def specification(self,spec):
  return {'profile':spec['profile'],'role':spec['profile'],'site':'web','candidate_config_id':spec['config_id'],'candidate_rootfs':spec['layers'],
   'ci_run_id':spec['run_id'],'image_artifact':{'digest':'sha256:'+spec['image_zip_sha256'],'size_in_bytes':spec['image_zip_bytes']},
   'archive':spec['archive'],'public_export_bytes':spec['export_bytes'],'public_export_sha256':spec['export_sha256']}
 def test_selected_evidence_uses_real_datastore_scope_for_both_profiles_and_measurement(self):
  for profile in ('postgres','redis'):
   folder,M=self.workspace();ctx=self.basic(M,folder,profile=profile);raw,spec=self.evidence(profile)
   self.private(folder/'evidence'/(profile+'.zip'),raw);ctx.spec=self.specification(spec);ctx.baseline={'machine_id':'f'*32}
   with patch.dict(ctx.D.PROFILES,{profile:spec}):
    data=M.selected_evidence(ctx);self.assertEqual(data['export']['raw_counts'],{'UNKNOWN':1} if profile=='postgres' else {})
    actual={'id':spec['config_id'],'os':'linux','architecture':'amd64','rootfs':{'Type':'layers','Layers':spec['layers']},'config':data['image']['Config']}
    admission={'source_identities':{spec['config_id']:{'kind':'Docker_config_identity'}},'archive_sha256':'e'*64}
    with patch.object(M,'command',return_value=M.encode(actual)) as command:receipt=M.image(ctx,spec['config_id'],admission)
    self.assertEqual(receipt['schema'],'datastore_preloaded_image/2');self.assertIs(receipt['security_accepted'],False);self.assertEqual(receipt['serving_roles_activated'],[])
    self.assertIs(receipt['runtime_health_issues_open'],True);self.assertNotIn('PUBLIC_SYNTHETIC_FIXTURE',json.dumps(receipt))
    self.assertEqual(command.call_args.args[1][:5],[*M.DOCKER,'image','inspect','--format'])
    actual['config']={**actual['config'],'Env':['EXPLICIT_DIFFERENT_FIXTURE=1']}
    with patch.object(M,'command',return_value=M.encode(actual)),self.assertRaisesRegex(RuntimeError,'^daemon_config_or_rootfs$'):M.image(ctx,spec['config_id'],admission)
 def test_receive_parses_real_datastore_evidence_and_physical_archive_without_load(self):
  for profile in ('postgres','redis'):
   folder,M=self.workspace();ctx=self.basic(M,folder,'receive-web','web',profile)
   producer=archive_fixtures.DatastoreCandidateTests(methodName='runTest');tar,data,_=producer.archive()
   data['spec']['profile']=profile;raw,spec=self.evidence(profile,data,tar);self.private(folder/'evidence'/(profile+'.zip'),raw)
   exported=ctx.D.encode(ctx.D.evidence(raw,spec)['export']);body=io.BytesIO()
   with zipfile.ZipFile(body,'w') as z:z.writestr('image-export.json',exported);z.writestr('candidate-image.tar.gz',gzip.compress(tar,mtime=0))
   body=body.getvalue();spec.update(image_zip_bytes=len(body),image_zip_sha256=sha256(body).hexdigest());ctx.spec=self.specification(spec)
   ctx.baseline={'image_map':[]};ctx.begin();fd=os.memfd_create('owned-datastore-image-input',os.MFD_CLOEXEC);self.stack.callback(os.close,fd);os.write(fd,body);os.lseek(fd,0,os.SEEK_SET)
   with patch.dict(ctx.D.PROFILES,{profile:spec}),patch.object(M,'read_part',side_effect=lambda c,f,n:os.read(fd,n)),patch.object(M,'stable',return_value={'image_map':[]}):
    result=M.receive_web(ctx)
   self.assertEqual(result['status'],'archive_admitted_not_loaded');self.assertIs(result['security_accepted'],False)
   admission=ctx.D.document((ctx.work/'admission.json').read_bytes());self.assertEqual(admission['archive_sha256'],sha256(tar).hexdigest())
   self.assertEqual(admission['candidate_config_id'],data['spec']['config_id']);self.assertEqual(admission['rootfs'],data['spec']['layers'])
   self.assertEqual(set(admission['source_identities']),set(ctx.D.validate_archive(io.BytesIO(tar),len(tar),ctx.end,data,ctx.T,ctx.L)['source_identities']))
   self.assertFalse((ctx.work/'load-request.json').exists());ctx.check()
 def test_real_owned_guard_keeps_permanent_payload_unread_and_unknown_rpc_retained(self):
  folder,M=self.workspace();ctx=self.basic(M,folder);ctx.baseline={'daemon':{},'store':{},'machine_id':'a'*32,'image_map':[]}
  marker=self.private(folder/'marker',b'EXPLICIT PUBLIC MARKER FIXTURE');source=self.private(folder/'source-mutex',b'')
  reservation=folder/'reservation';before={name:M.identity(p.lstat()) for name,p in (('marker',marker),('source',source))};ctx.op['fences']={'bot':before}
  real_pread=os.pread
  def public_only(fd,*args):
   self.assertNotIn(os.fstat(fd).st_ino,[marker.stat().st_ino,source.stat().st_ino],'permanent payload read forbidden');return real_pread(fd,*args)
  machine=self.private(folder/'machine-id',b'a'*32);real_path=Path
  with patch.multiple(M,MARKER=marker,SOURCE=source,RESERVATION=reservation,PENDING=folder/'absent'),patch.object(M.os,'pread',side_effect=public_only), \
       patch.object(M,'Path',side_effect=lambda p:machine if p=='/etc/machine-id' else real_path(p)), \
       patch.object(M,'daemon_policy',return_value={}),patch.object(M,'store_view',return_value={}),patch.object(M,'stable',return_value={'image_map':[]}), \
       patch.object(M,'load_native',side_effect=RuntimeError('load_client_unknown')) as rpc:
   with self.assertRaisesRegex(RuntimeError,'^load_client_unknown$'):M.run(ctx,True)
   rpc.assert_called_once()
   self.assertTrue(reservation.exists());self.assertEqual(M.identity(source.lstat()),before['source']);self.assertEqual(M.identity(marker.lstat()),before['marker'])
   self.assertTrue((ctx.work/'intent.json').exists());ctx.check()
 def load_admission(self,M,ctx):
  body=b'EXPLICIT PUBLIC STREAM FIXTURE; NO ACTUAL ADMISSION CLAIM';profile=ctx.spec['profile']
  path=self.private(ctx.root/'attempts'/(profile+'-receive-web')/'source.tar',body)
  ctx.spec.update(candidate_config_id='sha256:'+'e'*64,candidate_rootfs=['sha256:'+'f'*64],archive={'uncompressed_bytes':len(body)})
  value={'schema':'market_candidate_archive/1','profile':profile,'candidate_config_id':ctx.spec['candidate_config_id'],
   'archive_bytes':len(body),'archive_sha256':sha256(body).hexdigest(),'tar_identity':M.identity(path.lstat()),'rootfs':ctx.spec['candidate_rootfs'],
   'source_identities':{ctx.spec['candidate_config_id']:{'kind':'Docker_config_identity'}},'expanded_layer_bytes':17,
   'prospective_import_bytes':2*len(body)+34,'security_accepted':False}
  raw=M.encode(value);ctx.op.update(admission=profile+'-admission.json',admission_sha256=sha256(raw).hexdigest())
  self.private(ctx.root/'controls'/ctx.op['admission'],raw);return path,value,body
 def test_configured_web_and_native_loads_use_real_admitted_fds_and_stream_boundaries(self):
  # Borrow only the existing inert client/pipe producer; controller modules stay isolated.
  from tests import test_market_candidate_images as shared
  for site in ('web','bot'):
   folder,M=self.workspace();ctx=self.basic(M,folder,'load-web' if site=='web' else 'load-native',site)
   helper=shared.MarketCandidateImageTests(methodName='runTest');helper.stack=self.stack;helper.root=folder
   helper.basic=lambda:ctx;helper.admission_fixture=lambda c:self.load_admission(M,c)
   with patch.object(shared,'M',M):
    if site=='web':helper.test_load_web_measures_exact_new_image_only_and_unknown_rpc_is_never_retried()
    else:helper.native_load_case()
 def test_main_has_closed_failure_and_never_calls_old_entrypoint_or_pollutes_stream(self):
  folder,M=self.workspace();M.main=Mock(side_effect=AssertionError('old entrypoint forbidden'))
  for phase in ('load-native','stream-web'):
   out=io.StringIO()
   with patch.object(A,'load_engine',return_value=M),patch.object(A,'context',side_effect=RuntimeError('EXPLICIT PRIVATE ERROR TEXT')),patch.object(M.signal,'alarm'),redirect_stdout(out):
    self.assertEqual(A.main(['--site','web','--phase',phase]),1)
   M.main.assert_not_called()
   if phase=='stream-web':self.assertEqual(out.getvalue(),'')
   else:
    value=json.loads(out.getvalue());self.assertEqual(value['code'],'refused_or_unknown');self.assertNotIn('PRIVATE',out.getvalue());self.assertIs(value['retry_allowed'],False)

if __name__=='__main__':unittest.main()
