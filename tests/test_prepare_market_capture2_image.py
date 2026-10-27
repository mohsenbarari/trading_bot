"""Owned public contracts/files/inert processes only; no Git/GH/Docker/native SQL."""
from contextlib import redirect_stdout
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from types import ModuleType
from unittest.mock import patch
import io,json,os,stat,subprocess,sys,tempfile,unittest
HERE=Path(__file__).resolve().parent
M=ModuleType('_owned_normal_market');M.__file__=str(HERE.parent/'scripts/prepare_market_capture2_image.py');exec(compile(Path(M.__file__).read_bytes(),M.__file__,'exec'),M.__dict__)
F=ModuleType('_owned_policy_fixtures');F.__file__=str(HERE/'market_security_source_fixtures.py');exec(compile(Path(F.__file__).read_bytes(),F.__file__,'exec'),F.__dict__)
class DriverTests(unittest.TestCase):
 def test_explicit_negative_policy_and_actual_armed_arguments_without_actions(self):
  with patch.object(M,'normal_build',side_effect=AssertionError('operations denied')),redirect_stdout(io.StringIO())as out:self.assertEqual(M.main([]),0)
  self.assertEqual(json.loads(out.getvalue())['status'],'UNARMED')
  with patch.object(M,'EXECUTION_ARMED',False),self.assertRaisesRegex(RuntimeError,'normal_main_integration_unarmed'):M.main(['--execute'])
  self.assertIs(M.EXECUTION_ARMED,True)
  with self.assertRaisesRegex(RuntimeError,'^arguments$'):M.main(['--execute'])
 def contract(self):
  root=Path('/OWNED-PUBLIC-NOT-OPENED')
  args={'SOURCE_SHA':'a'*40,'SOURCE_TREE':'b'*40,'IMAGE_INPUT_SIGNATURE':'c'*64,'IMAGE_VERSION':'production-private-primary',
   'BUSINESS_SOURCE_COMMIT':'d'*40,'BUSINESS_SOURCE_TREE':'e'*40,'BUSINESS_SOURCE_MANIFEST_SHA256':M.MANIFEST_SHA}
  return root,{'named_contexts':{'business-source':str(root/'sealed/product')},'default_context':str(root/'sealed/release'),'dockerfile':'deploy/market-data/Dockerfile.capture2','build_args':args}
 def test_exact_two_context_build_and_epoch_and_no_secret_args(self):
  root,contract=self.contract();argv=M.build_argv(root,contract,'f'*64,1792972800,'123','1')
  self.assertIn('business-source='+str(root/'sealed/product'),argv);self.assertEqual(argv[-1],str(root/'sealed/release'))
  self.assertIn('SOURCE_DATE_EPOCH=1792972800',argv);self.assertIn('SOURCE_SHA='+'a'*40,argv)
  self.assertIn('BUSINESS_SOURCE_COMMIT='+'d'*40,argv);self.assertNotIn('GH_TOKEN',' '.join(argv))
  contract['build_args']['PYTHON_BASE_IMAGE']='UNADMITTED'
  with self.assertRaisesRegex(RuntimeError,'build_contract'):M.build_argv(root,contract,'f'*64,1792972800,'123','1')
 def test_default_driver_uses_supported_exact_query_and_real_two_rows(self):
  def command(argv,**kwargs):
   self.assertEqual(argv[2:5],['buildx','ls','--format']);self.assertEqual(kwargs['maximum'],1024);return b'"docker"\n\n"docker"\n'
  with patch.object(M,'command',side_effect=command):self.assertEqual(M.default_driver(Path('/OWNED'))['json_rows'],2)
  for raw in (b'',b'"docker-container"\n',b'"docker"\nnull\n',b'docker\n',b'1e999\n',b'"docker"\n'*17):
   with patch.object(M,'command',return_value=raw),self.assertRaises((RuntimeError,ValueError)):M.default_driver(Path('/OWNED'))
 def test_live_main_fixed_gh_api_token_only_in_child_environment(self):
  sentinel='OWNED_SECRET_NOT_SENT';calls=[]
  def command(argv,**kwargs):calls.append((argv,kwargs));return b'a'*40+b'\n'
  with patch.dict(os.environ,{'GH_TOKEN':sentinel}),patch.object(M,'command',side_effect=command):
   value=M.live_main(Path('/OWNED'),{'host':'github.com','repository':M.REPOSITORY},'a'*40)
  self.assertEqual(calls[0][0],['/usr/bin/gh','api','repos/'+M.REPOSITORY+'/git/ref/heads/main','--jq','.object.sha'])
  self.assertEqual(calls[0][1]['environment']['GH_TOKEN'],sentinel);self.assertNotIn(sentinel,json.dumps(value));self.assertNotIn(sentinel,' '.join(calls[0][0]))
  with patch.dict(os.environ,{'GH_TOKEN':sentinel}),patch.object(M,'command',return_value=b'b'*40+b'\n'),self.assertRaisesRegex(RuntimeError,'live_main_changed'):M.live_main(Path('/OWNED'),{'host':'github.com','repository':M.REPOSITORY},'a'*40)
 def owned(self):
  temporary=tempfile.TemporaryDirectory(prefix='.owned-normal-market-',dir=HERE);self.addCleanup(temporary.cleanup);return Path(temporary.name)
 def test_real_owned_context_snapshot_and_restamp(self):
  root=self.owned();(root/'public').mkdir(mode=0o755);p=root/'public/file';p.write_bytes(b'OWNED PUBLIC');p.chmod(0o644)
  expected={'public/file':{'bytes':12,'sha256':sha256(b'OWNED PUBLIC').hexdigest()}}
  M.restamp(root,expected,1792972800);self.assertEqual(p.stat().st_mtime_ns,1792972800*10**9)
  snap=M.ContextSnapshot(root,expected);snap.inventory();snap.close()
 def test_real_extra_context_and_parent_swap_never_pass(self):
  root=self.owned();folder=root/'public';folder.mkdir();p=folder/'file';p.write_bytes(b'OWNED');p.chmod(0o644)
  expected={'public/file':{'bytes':5,'sha256':sha256(b'OWNED').hexdigest()}}
  extra=root/'UNADMITTED';extra.write_bytes(b'NEVER SELECTED')
  with self.assertRaisesRegex(RuntimeError,'context_inventory'):M.context_rows(root,expected)
  extra.unlink();snap=M.ContextSnapshot(root,expected);replacement=root/'replacement';replacement.mkdir();(replacement/'file').write_bytes(b'OTHER')
  folder.rename(root/'parked');replacement.rename(folder)
  with self.assertRaises(RuntimeError):snap.check()
  with self.assertRaises(RuntimeError):snap.close()
 def test_real_late_physical_close_payload_change_refuses(self):
  root=self.owned();p=root/'file';p.write_bytes(b'OWNED');p.chmod(0o644);held=M.Held(p,binding={'bytes':5,'sha256':sha256(b'OWNED').hexdigest()})
  old=M.identity(os.fstat(held.fd));close=os.close
  def physical(fd):
   selected=M.identity(os.fstat(fd))==old;close(fd)
   if selected:p.chmod(0o400)
  with patch.object(M.os,'close',side_effect=physical),self.assertRaisesRegex(RuntimeError,'source_changed'):held.close()
 def test_owned_four_lockset_refuses_explicit_unarmed_coreutils_cache(self):
  root=self.owned();(root/'scripts').mkdir();repo=HERE.parent;rows=[]
  for name in (M.APT_LOCK,M.SQLITE_LOCK,M.TAR_LOCK,M.COREUTILS_LOCK):
   raw=(repo/name).read_bytes()
   if name==M.COREUTILS_LOCK:raw=F.encoded(F.unarmed_lock(raw))
   (root/name).write_bytes(raw);(root/name).chmod(0o644);rows.append({'path':name,'bytes':len(raw),'sha256':sha256(raw).hexdigest()})
  with self.assertRaisesRegex(RuntimeError,'coreutils_lock'):M.cache_inputs(root,{'release_files':rows})
 def test_same_sqlite_bytes_refresh_provenance_does_not_change_stable_cache_manifest(self):
  repo=HERE.parent;old=json.loads((repo/M.SQLITE_LOCK).read_bytes());changed=deepcopy(old);changed['artifact']['id']+=100
  def manifest(lock):return M.encoded({'schema':'sqlite_wal_image_package_cache/1','package':lock['package'],'binding':lock['outputs'][lock['package']['path']],
   'source':{key:lock['source'][key]for key in ('recipe_sha256','production_patch_sha256')}})
  self.assertEqual(manifest(old),manifest(changed));self.assertNotEqual(M.encoded(old),M.encoded(changed))
 def test_candidate_image_config_preserves_final_user_entrypoint_and_two_sources(self):
  receipt={'release_commit':'a'*40,'release_tree':'b'*40,'business_commit':'c'*40,'business_tree':'d'*40}
  labels={'org.opencontainers.image.revision':'a'*40,'io.gold-trade.release.tree':'b'*40,'io.gold-trade.release.input-signature':'e'*64,
   'io.gold-trade.release.controller-input-signature':'f'*64,'io.gold-trade.business.source-revision':'c'*40,'io.gold-trade.business.source-tree':'d'*40,'io.gold-trade.business.source-manifest':M.MANIFEST_SHA}
  value={'Id':'sha256:'+'1'*64,'Os':'linux','Architecture':'amd64','Config':{'User':'10001:10001','WorkingDir':'/app',
   'Entrypoint':['python','-m','core.market_intelligence.private_pipeline_foundation'],'Cmd':['service','--role','market-processor'],'Labels':labels},'RootFS':{'Type':'layers','Layers':['sha256:'+'2'*64]}}
  M.image_contract(value,receipt,'e'*64,'f'*64)
  for kind in ('user','entrypoint','business'):
   changed=deepcopy(value)
   if kind=='user':changed['Config']['User']='0:0'
   elif kind=='entrypoint':changed['Config']['Entrypoint']=['python','OTHER']
   else:changed['Config']['Labels']['io.gold-trade.business.source-revision']='9'*40
   with self.assertRaises(RuntimeError):M.image_contract(changed,receipt,'e'*64,'f'*64)
 def test_raw_reports_join_same_image_and_preserve_high_rows(self):
  root=self.owned();(root/'reports').mkdir();image='sha256:'+'a'*64
  scan={'Metadata':{'ImageID':image},'Results':[{'Vulnerabilities':[{'Severity':'HIGH'},{'Severity':'CRITICAL'}]}]}
  sbom={'metadata':{'component':{'type':'container','name':image,'properties':[{'name':'aquasecurity:trivy:ImageID','value':image}]}}}
  for name,value in [('vulnerabilities.json',scan),('sbom.cdx.json',sbom)]:p=root/'reports'/name;p.write_bytes(M.encoded(value));p.chmod(0o600)
  counts,_=M.raw_reports(root,image);self.assertEqual((counts['HIGH'],counts['CRITICAL']),(1,1));self.assertEqual(json.loads((root/'reports/vulnerabilities.json').read_bytes()),scan)
  with self.assertRaisesRegex(RuntimeError,'report_identity'):M.raw_reports(root,'sha256:'+'b'*64)
 def test_owned_command_reaped_pid_never_signalled_and_live_timeout_reaped(self):
  env={'PATH':os.defpath}
  with patch.object(M.os,'killpg',side_effect=AssertionError('postreap denied')):
   self.assertEqual(M.command([sys.executable,'-I','-B','-c',"print('OWNED')"],environment=env,maximum=128),b'OWNED\n')
  children=[];original=subprocess.Popen
  def create(*a,**kw):child=original(*a,**kw);children.append(child);return child
  with patch.object(M.subprocess,'Popen',side_effect=create),self.assertRaisesRegex(RuntimeError,'command_timeout'):
   M.command([sys.executable,'-I','-B','-c','import time;time.sleep(5)'],environment=env,seconds=.03,maximum=128)
  self.assertIsNotNone(children[0].returncode)
 def test_failure_attribution_does_not_serialize_unknown_operands(self):
  self.assertNotIn('PRIVATE_OPERAND',json.dumps(M.closed_failure(ValueError('PRIVATE_OPERAND'))));self.assertFalse(M.closed_failure(RuntimeError('source_changed'))['security_accepted'])
 def test_source_and_sqlite_probe_plans_have_no_business_environment_override(self):
  plan=M.probe_plan(Path('/OWNED'),'sha256:'+'a'*64,'b'*64)
  for kind in ('source93','sqlite'):
   argv=plan[kind]['argv'];self.assertIn('--read-only',argv);self.assertIn('none',argv);self.assertNotIn('--env',argv);self.assertNotIn('-e',argv)
   self.assertNotIn('PYTHONPATH',' '.join(argv));self.assertIn('-S',argv)
  self.assertEqual(plan['source93']['user'],'10001:10001');self.assertTrue(plan['historical81_business_tests']['producer_implemented'])
 def test_typed_actual_source_probe_refuses_extra_false_and_boolean_count(self):
  value={'schema':'market_public_source93_readonly_observation/1','status':'match','expected_manifest_sha256':M.MANIFEST_SHA,'expected_rows_sha256':M.ROWS_SHA,
   'source_files':93,'excluded_cache_metadata_files':0,'all_source_rows_match':True,'source_only_nonatomic_observation':True,
   **{name:False for name in ('project_cache_bodies_read','unknown_noncache_files_admitted','project_imported','already_running_code_proven','business_data_freshness_proven','security_accepted')}}
  M.validate_source_probe(value)
  for key,item in [('source_files',True),('all_source_rows_match',False),('PRIVATE_DEBUG','must not be accepted')]:
   with self.assertRaisesRegex(RuntimeError,'source93_probe'):M.validate_source_probe({**value,key:item})
 def test_native_probe_requires_full_positive_contract_and_mapped_installed_bytes(self):
  lock=json.loads((HERE.parent/M.SQLITE_LOCK).read_bytes())
  value={'status':'PASS','sqlite_package':lock['package']['version'],'sqlite_runtime':'3.46.1','libmagic_checked':False,
   'sqlite_system_library':{**lock['installed_library'],'mapped_inode_verified':True},'sqlite_stdlib_extension':{'path':'/usr/local/lib/python3.11/lib-dynload/_sqlite3.OWNED.so','bytes':123,'sha256':'a'*64,'mapped_inode_verified':True},
   'python_environment_ignored':True,'wal_reset_package_identity_checked':True,'wal_reset_reproduction_performed':False,'production_data_accessed':False,'all_cves_attested':False,
   'compatibility':{**{key:True for key in ('wal_snapshot','rollback_upsert','json_fts','backup_readonly_reopen','lookup_index')},
    'query_result_sha256':sha256(json.dumps([['g2',3,'emami',62000000],['g1',1,'emami',61500000]],ensure_ascii=False).encode()).hexdigest(),'query_plan':['OWNED quote_lookup']}}
  M.validate_sqlite_probe(value,lock)
  for kind in ('mapping','hash','compat','extra'):
   bad=deepcopy(value)
   if kind=='mapping':bad['sqlite_system_library']['mapped_inode_verified']=False
   elif kind=='hash':bad['sqlite_system_library']['sha256']='f'*64
   elif kind=='compat':bad['compatibility']['wal_snapshot']=False
   else:bad['PRIVATE_EXTRA']='unaccepted'
   with self.assertRaisesRegex(RuntimeError,'sqlite_probe'):M.validate_sqlite_probe(bad,lock)
if __name__=='__main__':unittest.main()
