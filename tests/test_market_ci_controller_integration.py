"""Owned source contracts, files and archives; no hosted/image/business execution."""
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from types import ModuleType
from unittest.mock import patch
import ast,gzip,io,json,os,tarfile,tempfile,unittest
ROOT=Path(__file__).resolve().parents[1]
def load(name,path):
 m=ModuleType(name);m.__file__=str(path);exec(compile(path.read_bytes(),str(path),'exec'),m.__dict__);return m
M=load('owned_integrated_market',ROOT/'scripts/prepare_market_capture2_image.py')
S=load('owned_integrated_sealer',ROOT/'scripts/prepare_market_product_source.py')
class IntegrationTests(unittest.TestCase):
 def own(self):
  temp=tempfile.TemporaryDirectory(prefix='.owned-market-integration-',dir=Path(__file__).parent);self.addCleanup(temp.cleanup);return Path(temp.name)
 def write(self,p,raw):p.parent.mkdir(mode=0o755,parents=True,exist_ok=True);p.write_bytes(raw);p.chmod(0o644)
 def pin(self,raw):return{'bytes':len(raw),'sha256':sha256(raw).hexdigest()}
 def test_explicit_20_release_30_controller_inventory_and_old_product_stays93(self):
  self.assertEqual(len(S.RELEASE_FILES),20);self.assertEqual(len(M.CONTROLLER_FILES),30);self.assertEqual(len(set(M.CONTROLLER_FILES)),30)
  self.assertIn('scripts/prepare_tar_acl_os_cache.py',S.RELEASE_FILES);self.assertIn(M.TAR_LOCK,S.RELEASE_FILES)
  self.assertEqual(len(S.PRODUCT_COPY_LINES),5);self.assertEqual(len(S.PRODUCT_COPY_LINES|S.RELEASE_COPY_LINES),17)
  self.assertEqual(M.MAX_SOURCE,131072);self.assertEqual(M.MAX_CONTROLLER_SOURCE,262144)
  self.assertTrue(any(name.endswith('audit_production_market_catchup.py')for name in M.CONTROLLER_FILES))
 def test_fixed_capture2_recipe_isolated_from_ordinary_main_callers(self):
  self.assertIn('deploy/market-data/Dockerfile.capture2',S.RELEASE_FILES);self.assertNotIn('deploy/market-data/Dockerfile',S.RELEASE_FILES)
  self.assertTrue((ROOT/'deploy/market-data/Dockerfile.capture2').is_file())
  ordinary=ROOT/'deploy/market-data/Dockerfile'
  if ordinary.exists():self.assertNotIn('COPY --from=business-source',ordinary.read_text())
  root=Path('/OWNED-NOT-OPENED');args={'SOURCE_SHA':'a'*40,'SOURCE_TREE':'b'*40,'IMAGE_INPUT_SIGNATURE':'c'*64,'IMAGE_VERSION':'production-private-primary',
   'BUSINESS_SOURCE_COMMIT':'d'*40,'BUSINESS_SOURCE_TREE':'e'*40,'BUSINESS_SOURCE_MANIFEST_SHA256':M.MANIFEST_SHA}
  contract={'named_contexts':{'business-source':str(root/'sealed/product')},'default_context':str(root/'sealed/release'),'dockerfile':'deploy/market-data/Dockerfile','build_args':args}
  with self.assertRaisesRegex(RuntimeError,'build_contract'):M.build_argv(root,contract,'f'*64,1792972800,'123','1')
 def test_historical_fixture_name_truthfully_binds_fixed_actual_capture2_recipe(self):
  raws={n:(ROOT/n).read_bytes()for n in M.CONTROLLER_FILES};rows=[]
  for n in('deploy/market-data/Dockerfile.capture2','deploy/market-data/accepted-f79-business.manifest.json'):
   raw=(ROOT/n).read_bytes();rows.append({'path':n,'bytes':len(raw),'sha256':sha256(raw).hexdigest(),'git_blob':M.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()})
  B,O,contents,manifest,plans=M.historical81_sources(raws,ROOT,{'release_commit':'b'*40,'release_files':rows},'sha256:'+'a'*64,'c'*64,ROOT)
  self.assertEqual(manifest['schema'],'normal_market_business81_sources/2');self.assertEqual(manifest['release_recipe_path'],'deploy/market-data/Dockerfile.capture2')
  for prefix in('base','replay'):self.assertEqual(contents[prefix+'/deploy/market-data/Dockerfile'],(ROOT/'deploy/market-data/Dockerfile.capture2').read_bytes())
  self.assertEqual(sum(plan['expected_test_count']for plan in plans.values()),81)
 def test_real_cache_temp_lifecycles_finish_before_context_freeze_without_metadata_waiver(self):
  root=self.own();context=root/'release';context.mkdir();(context/'pip_packages').mkdir();rows=[];expected={};calls=[]
  for name in S.RELEASE_FILES:
   raw=('OWNED RELEASE:'+name).encode();self.write(context/name,raw);pin=self.pin(raw);rows.append({'path':name,**pin});expected[name]=pin
  def command(argv,**options):
   options['guard']();cache=Path(argv[argv.index('--cache-dir')+1]);calls.append('--check'in argv)
   with tempfile.TemporaryDirectory(prefix='.owned-cache-temp-',dir=cache.parent):options['guard']()
   if '--check'not in argv:
    self.write(cache/'owned',b'OWNED CACHE');expected[(cache/'owned').relative_to(context).as_posix()]=self.pin(b'OWNED CACHE')
   options['guard']();return b''
  with patch.dict(M.os.environ,{'GH_TOKEN':'OWNED_SYNTHETIC_NOT_SENT'}),patch.object(M,'command',side_effect=command):M.prepare_checked_caches(root,context,{'release_files':rows},lambda:None)
  self.assertEqual(calls,[False,False,False,False,True,True,True,True]);M.restamp(context,expected,1792972800);snap=M.ContextSnapshot(context,expected);snap.check()
  with tempfile.TemporaryDirectory(prefix='.late-owned-cache-temp-',dir=context/'pip_packages'):pass
  with self.assertRaisesRegex(RuntimeError,'context_changed'):snap.check()
  with self.assertRaisesRegex(RuntimeError,'context_changed'):snap.close()
  function=next(n for n in ast.parse((ROOT/'scripts/prepare_market_capture2_image.py').read_bytes()).body if isinstance(n,ast.FunctionDef)and n.name=='normal_build')
  order={}
  for node in ast.walk(function):
   if isinstance(node,ast.Call)and isinstance(node.func,ast.Name)and node.func.id in('prepare_checked_caches','restamp','ContextSnapshot'):order.setdefault(node.func.id,node.lineno)
  self.assertLess(order['prepare_checked_caches'],order['restamp']);self.assertLess(order['restamp'],order['ContextSnapshot'])
 def test_four_actual_public_locks_derive26_cache_entries(self):
  root=self.own();rows=[]
  for name in(M.APT_LOCK,M.SQLITE_LOCK,M.TAR_LOCK,M.COREUTILS_LOCK):
   raw=(ROOT/name).read_bytes();self.write(root/name,raw);rows.append({'path':name,**self.pin(raw)})
  expected,sqlite=M.cache_inputs(root,{'release_files':rows})
  self.assertEqual(len(expected),26);self.assertEqual(sum(n.endswith('.deb')for n in expected),18)
  tar=json.loads((ROOT/M.TAR_LOCK).read_bytes());helper=load('owned_tar_definition',ROOT/'scripts/prepare_tar_acl_os_cache.py')
  self.assertEqual(expected[M.CACHE_ROOTS[2]+'/manifest.json'],self.pin(helper.cache_manifest(tar)))
  self.assertEqual(expected['pip_packages/.tar-acl-capsule.lock'],self.pin(b''))
  changed=deepcopy(tar);changed['artifact']['id']+=1
  self.assertEqual(helper.cache_manifest(tar),helper.cache_manifest(changed))
  core=json.loads((ROOT/M.COREUTILS_LOCK).read_bytes());core_helper=load('owned_coreutils_definition',ROOT/'scripts/prepare_coreutils_acl_os_cache.py')
  self.assertEqual(expected[M.CACHE_ROOTS[3]+'/manifest.json'],self.pin(core_helper.cache_manifest(core)))
  self.assertEqual(core['package']['version'],'9.7-3+gold2')
 def fixture(self):
  root=self.own();rows=[];controllers=[];products=[];cache={}
  def row(prefix,name,raw):
   self.write(root/prefix/name,raw);return{'path':name,'mode':'100644','git_blob':sha256(raw).hexdigest()[:40],**self.pin(raw)}
  for name in S.RELEASE_FILES:rows.append(row('sealed/release',name,('OWNED RELEASE:'+name).encode()))
  for i in range(93):products.append(row('sealed/product','owned/public-'+str(i)+'.py',('OWNED PRODUCT:'+str(i)).encode()))
  for name in M.CONTROLLER_FILES:controllers.append(row('controller-source',name,('OWNED CONTROLLER:'+name).encode()))
  for i in range(26):
   name='pip_packages/owned-cache-'+str(i);raw=('OWNED CACHE:'+str(i)).encode();self.write(root/'sealed/release'/name,raw);cache[name]=self.pin(raw)
  receipt={'release_commit':'a'*40,'release_tree':'b'*40,'business_commit':'c'*40,'business_tree':'d'*40,'release_files':rows,'product_files':products}
  return root,receipt,controllers,cache
 def test_full_declared_controller_parcel_roundtrip_no_extract_or_authority(self):
  root,receipt,controllers,cache=self.fixture();value=M.controller_parcel(root,receipt,controllers,cache,'e'*64,'f'*64,1792972800,lambda:None)
  self.assertEqual((value['sources'],value['members']),(169,170));self.assertTrue(value['complete_ci_controller_payload'])
  self.assertFalse(value['complete_deployment_control_payload']);self.assertFalse(value['activation_authority']);self.assertFalse(value['security_accepted'])
  with tarfile.open(root/'controller-sources.tar.gz','r:gz')as archive:
   items=archive.getmembers();self.assertEqual(len(items),170);self.assertEqual(len({x.name for x in items}),170)
   self.assertTrue(all(x.isfile()and x.uid==x.gid==0 and not x.pax_headers for x in items));self.assertTrue(any(len(x.name)>100 for x in items))
   index=json.load(archive.extractfile('controller-manifest.json'));self.assertEqual(len(index['files']),169)
 def test_missing_duplicate_unknown_binding_and_hardlink_refuse(self):
  root,receipt,controllers,cache=self.fixture()
  with self.assertRaisesRegex(RuntimeError,'controller_parcel_inventory'):M.controller_parcel_inputs(root,{**receipt,'release_files':receipt['release_files'][:-1]},controllers,cache)
  with self.assertRaisesRegex(RuntimeError,'controller_parcel_inventory'):M.controller_parcel_inputs(root,{**receipt,'product_files':[receipt['product_files'][0]]*93},controllers,cache)
  first=root/'sealed/product'/receipt['product_files'][0]['path'];os.link(first,root/'OWNED-HARDLINK')
  with self.assertRaisesRegex(RuntimeError,'source_file'):M.controller_parcel(root,receipt,controllers,cache,'e'*64,'f'*64,1792972800,lambda:None)
 def test_payload_drift_before_write_or_during_physical_closes_cannot_complete(self):
  root,receipt,controllers,cache=self.fixture();first=root/'sealed/product'/receipt['product_files'][0]['path'];first.write_bytes(b'OWNED CHANGED')
  with self.assertRaisesRegex(RuntimeError,'source_binding'):M.controller_parcel(root,receipt,controllers,cache,'e'*64,'f'*64,1792972800,lambda:None)
  self.assertFalse((root/'controller-sources.json').exists())
 def test_parcel_original_writer_close_append_cannot_be_adopted(self):
  root,receipt,controllers,cache=self.fixture();target=root/'controller-sources.tar.gz';actual=os.close;changed=[]
  def close(fd):
   frame=os.fstat(fd);actual(fd)
   if target.exists()and(frame.st_dev,frame.st_ino)==(target.stat().st_dev,target.stat().st_ino)and not changed:
    changed.append(True)
    with target.open('ab')as out:out.write(b'OWNED_NOT_ADMITTED_TRAILING_BYTES')
  with patch.object(os,'close',side_effect=close),self.assertRaisesRegex(RuntimeError,'source_binding'):
   M.controller_parcel(root,receipt,controllers,cache,'e'*64,'f'*64,1792972800,lambda:None)
  self.assertTrue(changed);self.assertFalse((root/'controller-sources.json').exists())
 def test_completion_writer_checks_earlier_named_files_after_physical_close(self):
  root=self.own();old=root/'source';old.write_bytes(b'OWNED SOURCE');old.chmod(0o644);held=M.Held(old);held.close();proof=root/'proof';actual=os.close;changed=[]
  def close(fd):
   frame=os.fstat(fd);actual(fd)
   if proof.exists()and(frame.st_dev,frame.st_ino)==(proof.stat().st_dev,proof.stat().st_ino)and not changed:changed.append(True);old.write_bytes(b'CHANGED')
  with patch.object(os,'close',side_effect=close),self.assertRaisesRegex(RuntimeError,'source_changed'):M.publish_known(proof,b'{"owned":true}\n',[held.named])
  self.assertTrue(changed)
 def test_container_exit_evidence_is_captured_before_cleanup_and_never_invented(self):
  outer=(ROOT/'scripts/market_business81_outer.py').read_text();driver=(ROOT/'scripts/prepare_market_capture2_image.py').read_text()
  self.assertIn("'exit_code':state['ExitCode']",outer);self.assertLess(outer.index("profile_root/'container-closed.json'"),outer.index("['container','rm',cid]"))
  self.assertIn("'actual_container_exit_code':state['exit_code']",driver)
  self.assertNotIn("'actual_container_exit_code':0",driver)
 def business_fixture(self,exit_code=0,client_rc=0):
  root=self.own();B=load('owned_business_definitions',ROOT/'scripts/market_business81.py');selection=json.loads((ROOT/M.BUSINESS_DIR/'f79-business-test-selection.json').read_bytes())
  image='sha256:'+'a'*64;release='b'*40;signature='c'*64;csig='d'*64;manifest='e'*64
  plans=B.profiles(selection,image=image,release_commit=release,two_git_signature=signature);work=root/('business81-'+'f'*32);outputs={};observations={}
  for profile,plan in plans.items():
   value={'schema':'normal_market_business81_profile_result/1',**{k:plan[k]for k in('image_id','release_commit','business_commit','business_tree','two_git_input_signature')},
    'test_sources_sha256':manifest,'profile':profile,'execution_uid':plan['uid'],'execution_gid':plan['gid'],'discarded_fixture_stdout_bytes':0,
    'selected_test_ids':sorted(plan['test_ids']),'tests':len(plan['test_ids']),'skips':0,'expected_failures':0,'unexpected_successes':0,'failures':0,'errors':0,
    'status':'passed','actual_candidate_source93_verified':True,'runtime_data_used':False,'business_data_freshness_proven':False,'security_accepted':False}
   raw=M.encoded(value);objects={'intent.json':b'{"owned":true}\n','tests.stderr':b'OWNED TEST DETAIL\n','profile.json':raw,
    'received.json':M.encoded({'schema':'normal_market_business81_start_observation/1','actual_client_returncode':client_rc,'stderr_bytes':18,'stdout_bytes':len(raw)}),
    'container-closed.json':M.encoded({'schema':'normal_market_business81_closed_container/1','profile':profile,'container_id':'a'*64,'image_id':image,'running':False,'paused':False,'pid':0,'exit_code':exit_code})}
   for leaf,data in objects.items():name=profile+'/'+leaf;self.write(work/name,data);outputs[name]=self.pin(data)
   observations[profile]={'actual_outer_returncode':0,'actual_container_exit_code':0,'observation':value}
  value=B.join_actual_results(selection,plans,observations,expected_manifest_sha256=manifest)
  value.update(actual_outer_authentication_and_cleanup_still_required=False,actual_profile_containers_removed=3,source_and_output_finalizers_completed=True,
   actual_same_candidate_image_verified=True,controller_input_signature=csig,run_directory=str(work),profile_outputs=outputs)
  self.write(work/'completed.json',M.encoded(value))
  return root,{'historical81':value,'image_id':image,'source_inputs':{'release_commit':release},'two_git_input_signature':signature,'controller_input_signature':csig},B,selection,plans
 def test_real_captured_nonzero_container_exit_cannot_be_relabelled_zero(self):
  root,build,B,selection,plans=self.business_fixture(exit_code=17);value,observations=M.business81_outputs(root,build,lambda:None)
  self.assertEqual(observations['base-root']['actual_container_exit_code'],17)
  with self.assertRaisesRegex(RuntimeError,'actual_profile_exit'):B.join_actual_results(selection,plans,observations,expected_manifest_sha256=value['test_sources_sha256'])
 def test_boolean_client_exit_and_missing_closed_state_evidence_refuse(self):
  root,build,B,selection,plans=self.business_fixture(client_rc=True)
  with self.assertRaisesRegex(RuntimeError,'business81_actual'):M.business81_outputs(root,build,lambda:None)
  build['historical81']['profile_outputs'].pop('base-root/container-closed.json')
  with self.assertRaisesRegex(RuntimeError,'business81_actual'):M.business81_outputs(root,build,lambda:None)
 def test_unarmed_is_enforced_before_any_live_phase(self):
  with patch.object(M,'EXECUTION_ARMED',False),patch.object(M,'normal_build',side_effect=AssertionError('live action denied')),self.assertRaisesRegex(RuntimeError,'normal_main_integration_unarmed'):M.main(['--execute'])
 def test_new_finite_diagnostics_never_export_unknown_exception_operands(self):
  for code in('business81_actual','controller_parcel_binding','tar_lock'):self.assertEqual(M.closed_failure(RuntimeError(code))['code'],code)
  self.assertNotIn('OWNED_PRIVATE_DETAIL',json.dumps(M.closed_failure(ValueError('OWNED_PRIVATE_DETAIL'))))
if __name__=='__main__':unittest.main()
