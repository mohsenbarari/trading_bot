"""Genuine owned metadata/normal parity; no daemon, private inputs or serving."""
from contextlib import ExitStack
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from types import ModuleType
from unittest.mock import patch
import io,json,os,time,unittest
ROOT=Path(__file__).resolve().parents[1]
def module(path):
 m=ModuleType('_fixture_'+path.stem);m.__file__=str(path);exec(compile(path.read_bytes(),str(path),'exec'),m.__dict__);return m
NM=module(ROOT/'tests/test_normal_market_activation.py')
A=module(ROOT/'deploy/maintenance/normal_market_role_admission.py')
class AdmissionTests(unittest.TestCase):
 def setUp(self):
  self.f=NM.NormalMarketTests('runTest');self.f.setUp();self.addCleanup(self.f.doCleanups)
  self.stack=self.f.stack;self.root=self.f.root;self.C=self.f.C;self.S=self.f.S;self.case=self.f.case();f=self.case
  f.new_image['RootFS']={'Type':'layers','Layers':['sha256:'+'7'*64,'sha256:'+'8'*64]}
  f.new_image['Config']['Labels']={key:'PUBLIC_AUTHENTICATED_BUILD' for key in module(ROOT/'deploy/maintenance/normal_market_image_delta.py').PROFILE_PROVENANCE_KEYS}
  f.new_image['Config']['Env']=[v if not v.startswith('PYTHON_VERSION=') else 'PYTHON_VERSION=3.11.17' for v in f.new_image['Config']['Env']]
  for image in (f.old_image,f.new_image):image.update(Os='linux',Architecture='amd64')
  role='market-processor';spec=f.old['services'][role];spec.update(pull_policy='never')
  spec['environment']['PUBLIC_TEST_PASSWORD']='PRIVATE_RUNTIME_SENTINEL'
  f.old_row['config']['Env']=[*f.old_image['Config']['Env'],*(k+'='+v for k,v in spec['environment'].items())]
  files=[]
  for i in range(2):
   path=self.f.private(self.root/('normal-'+str(i)+'.yml'),b'services: {}\n');files.append({'path':str(path),'bytes':path.stat().st_size,'sha256':sha256(path.read_bytes()).hexdigest()})
  env=self.f.private(self.root/'normal.env',b'OPAQUE_ENV_DO_NOT_READ');marker=self.f.private(self.root/'marker',b'OPAQUE_MARKER_DO_NOT_READ')
  self.stack.enter_context(patch.object(A,'MARKER',marker));self.opaque={env,marker,Path(f.scope['secret_files'][0]['target'])}
  f.scope.update(workdir=str(self.root),compose_files=files,env_files=[{'path':str(env),'target':str(env),'identity':self.S.identity(env.stat()),'aliases':[],'cli':True}],
   baseline={'container_id':f.old_row['id'],'image_id':f.old_row['image'],'pid':f.old_row['pid'],'started':f.old_row['started'],'restarts':0},
   environment_file_label=str(env),docker_version='32.0.0',image_model='authenticated_ci_rebuild')
  cfg=f.old_row['config'];cfg['Labels'].update({'com.docker.compose.project.working_dir':str(self.root),
   'com.docker.compose.project.config_files':','.join(v['path'] for v in files),'com.docker.compose.project.environment_file':str(env)})
  now=int(time.time());helpers={k:{'path':str(ROOT/'deploy/maintenance'/name),'sha256':A.PINS[k]} for k,name in A.HELPERS.items()}
  fields=('project','workdir','container_name','compose_files','profiles','env_files','secret_files','public_sources','compose_version','docker_version','environment_file_label','release','baseline','image_model')
  self.control={'schema':'normal_market_role_admission/1','operation':A.OP,'nonce':'a'*32,'site':'web','role':role,'machine_id':'b'*32,
   'issued_at':now,'deadline':now+180,'helpers':helpers,'scope':{k:f.scope[k] for k in fields},'fences':{'marker':self.S.identity(marker.stat())},'evidence':{}}
  spec={'profile':'678aaf5cd8e9','role':role,'candidate_config_id':f.new_image['Id'],'candidate_rootfs':f.new_image['RootFS']['Layers'],
   'ci_run_id':123,'image_artifact':{'digest':'sha256:'+'9'*64},'tar_sha256':'8'*64,'archive':{'uncompressed_bytes':5120}}
  self.data={'spec':spec,'baseline_image_id':f.old_image['Id'],'baseline_rootfs':f.old_image['RootFS']['Layers'],
   'old_nonenv_config':{k:v for k,v in f.old_image['Config'].items() if k!='Env'},'expected_candidate_config':f.new_image['Config'],
   'raw_candidate_config':f.new_image['Config']}
  ids={spec['candidate_config_id']:{'kind':'Docker_config_identity'},'sha256:'+'5'*64:{'kind':'OCI_manifest_to_config','media_type':'application/vnd.oci.image.manifest.v1+json','size':123},
   'sha256:'+'6'*64:{'kind':'OCI_index_to_manifest_to_config','media_type':'application/vnd.oci.image.index.v1+json','size':241}}
  ar={'schema':'market_candidate_archive/1','profile':spec['profile'],'candidate_config_id':spec['candidate_config_id'],'rootfs':spec['candidate_rootfs'],
   'archive_sha256':spec['tar_sha256'],'archive_bytes':5120,'tar_identity':[1,2,0o100600,0,0,1,5120,3,4],
   'source_identities':ids,'security_accepted':False,'configuration_values_disclosed':False}
  encoded=lambda v:(json.dumps(v,sort_keys=True)+'\n').encode();araw=encoded(ar)
  op={'operation':'security-market-candidate-images-20261025-06','schema':'market_image_store_control/2','worker_sha256':'5017b7546a3f117e74fdd6314582e19e39395ae1ac183149be924ba720d68aac',
   'profile':spec['profile'],'phase':'load-web','nonce':'c'*32,'admission_sha256':sha256(araw).hexdigest()};oraw=encoded(op)
  receipt={'schema':'market_preloaded_image/1','profile':spec['profile'],'role':role,'catalog_sha256':'c354498c3659fdf99613753d950416f134d81e0fe61fbdc80a32e7eec29b8d12',
   'site':'web','host_machine_id':'b'*32,'ci_run_id':123,'image_artifact_sha256':'9'*64,'candidate_config_id':spec['candidate_config_id'],'rootfs':spec['candidate_rootfs'],
   'operation_sha256':sha256(oraw).hexdigest(),'source_admission_sha256':sha256(araw).hexdigest(),'archive_and_config_association_verified':True,
   'whole_typed_config_compared_in_ram':True,'configuration_values_disclosed':False,'security_accepted':False,'serving_roles_activated':[],
   'source_archive_sha256':spec['tar_sha256'],'candidate_local_image_id':spec['candidate_config_id']}
  for key,raw in (('catalog',b'EXPLICIT_SYNTHETIC_EVIDENCE'),('profile_evidence',b'EXPLICIT_SYNTHETIC_EVIDENCE'),('stack',b'EXPLICIT_SYNTHETIC_EVIDENCE'),
   ('load_receipt',encoded(receipt)),('load_control',oraw),('archive_admission',araw)):
   path=self.f.private(self.root/(key+'.json'),raw);self.control['evidence'][key]={'path':str(path),'bytes':len(raw),'sha256':sha256(raw).hexdigest()}
  self.control['evidence']['rebased_proof']=None;self.current_compose_version='5.6.1'
  original=A.module
  def modules(raw,path):
   value=original(raw,path)
   if Path(path).name=='normal_market_image_delta.py':
    # Only external CI-data admission is stubbed for this synthetic owned
    # runtime case; actual ten-artifact integration runs separately on Root.
    value.lineage=lambda *args:deepcopy(self.data)
   return value
  self.stack.enter_context(patch.object(A,'module',side_effect=modules));self.calls=[]
  self.current={'Id':f.old_row['id'],'Name':f.old_row['name'],'Image':f.old_row['image'],'Config':cfg,'HostConfig':f.old_row['host'],
   'Mounts':f.old_row['mounts'],'NetworkSettings':{'Networks':f.old_row['networks']},'RestartCount':0,
   'State':{'Running':True,'Pid':f.old_row['pid'],'StartedAt':f.old_row['started'],'Restarting':False,'Paused':False,'Dead':False,'Health':{'Status':'unhealthy'}}}
  self.model=f.old;self.foreign_env=False
 def capture(self,argv,seconds,environment):
  self.calls.append(argv);self.assertEqual(argv[:2],['/usr/bin/docker','--host=unix:///var/run/docker.sock']);self.assertLessEqual(seconds,60)
  self.assertTrue(Path(environment['DOCKER_CONFIG']).is_dir());self.assertEqual(list(Path(environment['DOCKER_CONFIG']).iterdir()),[])
  args=argv[2:]
  if args==['compose','version','--short']:return 0,(self.current_compose_version+'\n').encode()
  if args==['version','--format','{{.Server.Version}}']:return 0,b'32.0.0\n'
  if args[:2]==['container','inspect']:
   self.assertEqual(args[2:4],['--format',A.CONTAINER_FORMAT]);self.assertNotIn('.Log',args[3]);self.assertNotIn('.Output',args[3]);return 0,json.dumps(self.current).encode()
  if args[:2]==['image','inspect']:
   self.assertEqual(args[2:4],['--format',A.IMAGE_FORMAT]);self.assertNotIn('Descriptor',args[3])
   image=self.case.old_image if args[-1]==self.case.old_image['Id'] else self.case.new_image
   return 0,json.dumps({k:image[k] for k in ('Id','Config','RootFS','Os','Architecture')}).encode()
  if args[0]=='compose':
   if args[-1]=='--no-env-resolution':return 0,json.dumps({'services':{'market-processor':{'env_file':[{'path':'/unbound' if self.foreign_env else self.control['scope']['env_files'][0]['path']}]}}}).encode()
   return 0,json.dumps(self.model).encode()
  raise AssertionError('unexpected client')
 def invoke(self,*,late_child=False):
  original=open
  def opening(path,*args,**kwargs):return io.BytesIO(b'b'*32+b'\n') if str(path)=='/etc/machine-id' else original(path,*args,**kwargs)
  prior=Path.cwd()
  try:
   os.chdir(self.root)
   with patch('builtins.open',side_effect=opening),ExitStack() as stack:
    value=A.collect(self.control,stack,sha256(Path(A.__file__).read_bytes()).hexdigest(),capture=self.capture)
    if late_child:stack.callback(lambda:self.f.private(self.root/'late-owned-child',b'PUBLIC'))
    return value
  finally:os.chdir(prior)
 def test_real_normal_M_and_D_admit_rebuild_without_reading_or_emitting_opaque_values(self):
  original=os.pread;opaque={p.stat().st_ino for p in self.opaque}
  def reading(fd,*args):
   self.assertNotIn(os.fstat(fd).st_ino,opaque);return original(fd,*args)
  with patch.object(os,'pread',side_effect=reading):public,private=self.invoke()
  self.assertEqual(public['current_compose_version'],'5.6.1');self.assertEqual(public['creation_compose_version'],'2.40.3')
  self.assertEqual(public['health_observations'],['unhealthy','unhealthy']);self.assertFalse(public['serving_replacement_permitted'])
  self.assertTrue(public['normal_prefix_and_complete_current_config_admitted']);self.assertNotIn('PRIVATE_RUNTIME_SENTINEL',json.dumps(public))
  self.assertIn('PRIVATE_RUNTIME_SENTINEL',' '.join(private['actual']['Config']['Env']))
  self.assertFalse(any(set(argv)&{'run','up','create','start','stop','kill','load','rm','build'} for argv in self.calls))
 def test_current_major_five_is_observed_separately_from_exact_historical_creation_labels(self):
  for historical in ('2.40.3','5.5.0'):
   self.control['scope']['compose_version']=historical
   self.current['Config']['Labels']['com.docker.compose.version']=historical
   public,_=self.invoke()
   self.assertEqual(public['current_compose_version'],'5.6.1');self.assertEqual(public['creation_compose_version'],historical)
  self.current['Config']['Labels']['com.docker.compose.version']='5.6.1'
  with self.assertRaisesRegex(RuntimeError,'compose_labels'):self.invoke()
  self.current['Config']['Labels']['com.docker.compose.version']='5.5.0';self.current_compose_version='5.6'
  with self.assertRaisesRegex(RuntimeError,'market_compose_version'):self.invoke()
  self.current_compose_version='5.6.1';self.control['scope']['compose_version']='5.5'
  with self.assertRaisesRegex(RuntimeError,'market_scope'):self.invoke()
 def test_business_environment_host_and_execution_drift_remain_real_failures(self):
  for key,bad in (('Env',['PRIVATE_RUNTIME_SENTINEL']),('Cmd',['unsafe']),('User','0')):
   saved=deepcopy(self.current['Config']);self.current['Config'][key]=bad
   with self.assertRaises(RuntimeError):self.invoke()
   self.current['Config']=saved
  self.current['HostConfig']['Privileged']=True
  with self.assertRaises(RuntimeError):self.invoke()
 def test_pull_policy_is_exact_never_and_historical_replace_label_is_exact_normal_name(self):
  service=self.model['services']['market-processor']
  for policy in ('always','build',True):
   service['pull_policy']=policy
   with self.assertRaisesRegex(RuntimeError,'normal_pull_policy'):self.invoke()
  service['pull_policy']='never';self.current['Config']['Labels']['com.docker.compose.replace']=self.case.scope['container_name']
  public,_=self.invoke();self.assertTrue(public['historical_recreate_metadata_observed'])
  self.current['Config']['Labels']['com.docker.compose.replace']='UNREVIEWED'
  with self.assertRaisesRegex(RuntimeError,'compose_labels'):self.invoke()
 def test_undeclared_env_is_rejected_before_resolved_config_is_requested(self):
  self.foreign_env=True
  with self.assertRaisesRegex(RuntimeError,'market_unbound_env'):self.invoke()
  self.assertFalse(any(argv[-3:]==['config','--format','json'] for argv in self.calls))
 def test_changed_lifecycle_or_candidate_report_config_cannot_become_admission_success(self):
  original=self.capture;count=[0]
  def changed(argv,*args,**kwargs):
   if argv[2:4]==['container','inspect']:
    count[0]+=1
    if count[0]==2:self.current['State']['Pid']+=1
   return original(argv,*args,**kwargs)
  self.capture=changed
  with self.assertRaisesRegex(RuntimeError,'market_runtime_changed'):self.invoke()
 def test_cwd_finalizer_catches_owned_child_exit_mutation_without_intermediate_check(self):
  with self.assertRaisesRegex(RuntimeError,'market_cwd'):self.invoke(late_child=True)
 def test_unrecognized_helper_path_refuses_before_any_payload_open(self):
  self.control['helpers']['market']['path']=str(self.root/'normal.env')
  with patch.object(os,'open',side_effect=AssertionError('payload opened')),self.assertRaisesRegex(RuntimeError,'market_control'):
   A.controls(self.control,time.time())
if __name__=='__main__':unittest.main()
