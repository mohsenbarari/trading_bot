"""Owned SOURCE/cache fixtures only. No real package, provider or image operation.

All invented package/artifact/proof metadata stays in explicitly synthetic QA.
The authentic release lock is separately validated at its declared stage.
"""
from contextlib import redirect_stdout
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from types import ModuleType
from unittest.mock import patch
import ast,io,json,os,sys,tempfile,time,unittest,zipfile
TREE=Path(__file__).resolve().parents[1]
def load(name):
 p=TREE/name if '/'in name else TREE/'scripts'/name;m=ModuleType('owned_'+p.stem);m.__file__=str(p);exec(compile(p.read_bytes(),str(p),'exec'),m.__dict__);return m
H=load('prepare_coreutils_acl_os_cache.py');M=load('prepare_market_capture2_image.py');S=load('prepare_market_product_source.py');C=load('prepare_canonical_app_image.py');O=load('market_business81_outer.py')
F=load('tests/market_security_source_fixtures.py')
class Tests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(prefix='.owned-coreutils-capsule-',dir=Path(__file__).parent);self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
 def put(self,path,raw,mode=0o644):
  p=self.root/path;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(raw);p.chmod(mode);return p
 def pin(self,raw):return{'bytes':len(raw),'sha256':sha256(raw).hexdigest()}
 def test_owned_unarmed_lock_refuses_without_invented_receipt_or_artifact(self):
  lock=F.unarmed_lock((TREE/'scripts/coreutils_acl_package.lock.json').read_bytes());self.assertIs(lock['armed'],False)
  for k in('artifact','outputs','source','runtime_payload','installed_callers'):self.assertIsNone(lock[k])
  with self.assertRaisesRegex(RuntimeError,'lock_unarmed'):H.validate_lock(lock)
  self.assertEqual(lock['approved_libacl']['sha256'],'32d112de5fb4ec3124c7a4b859b738b45e4c4a9db3b3115f167625f082909dd1')
 def test_actual_current_lock_stage_runs_complete_authentic_validator(self):
  F.assert_actual_coreutils_stage(self,H)
 def test_real_cli_refuses_before_cache_or_image_or_provider_action(self):
  # The owned lock is deliberately unarmed, independently of release state.
  raw=F.encoded(F.unarmed_lock((TREE/'scripts/coreutils_acl_package.lock.json').read_bytes()));path=self.put('owned-negative-lock/'+H.LOCK_NAME,raw)
  for options in([],['--check'],['--install-image-cache']):
   with patch.object(H,'ROOT',path.parent),patch.object(H,'LOCK_SHA',sha256(raw).hexdigest()),patch.object(H,'prepare',side_effect=AssertionError('no cache action')),patch.object(H,'install',side_effect=AssertionError('no image action')),patch.object(H.subprocess,'Popen',side_effect=AssertionError('no command')),redirect_stdout(io.StringIO())as out:
    self.assertEqual(H.main(['--cache-dir',str(self.root/'coreutils-acl-capsule'),*options]),1)
   self.assertEqual(json.loads(out.getvalue())['code'],'lock_unarmed')
 def test_exact_four_python18_recipes_add_one_offline_same_capsule_mask(self):
  for name in('Dockerfile','Dockerfile.iran','deploy/market-data/Dockerfile','deploy/market-data/Dockerfile.capture2'):
   text=(TREE/name).read_text();self.assertEqual(text.count('source=pip_packages/coreutils-acl-capsule,target=/coreutils-capsule,ro'),1)
   self.assertEqual(text.count('"--bounding-set=-all,+chown,+fowner,+fsetid,+setuid,+setgid"'),4)
   self.assertIn('python:3.11.18-slim-trixie@sha256:0451b9eeed3e5a9523d8958cc33262eb5843c7af3114c50f45d99f56e2b883ac',text)
   self.assertLess(text.index('"/tar-capsule"]'),text.index('"/coreutils-capsule"]'));self.assertIn('probe_python_micro_release.py --image-self-check',text)
   for existing in('security-os-capsule','sqlite-wal-capsule','tar-acl-capsule','verify_image_trust.py','remove_runtime_pip.py','verify_psycopg2_runtime.py','verify_sqlite_runtime.py'):self.assertIn(existing,text)
 def test_source_release_twenty_sources_four_caches_and_exact_outer_pin(self):
  self.assertEqual(len(S.RELEASE_FILES),20);self.assertIn('scripts/prepare_coreutils_acl_os_cache.py',S.RELEASE_FILES);self.assertIn('scripts/coreutils_acl_package.lock.json',S.RELEASE_FILES)
  self.assertEqual(len(M.CACHE_ROOTS),4);self.assertEqual(M.CACHE_ROOTS[-1],'pip_packages/coreutils-acl-capsule')
  raw=(TREE/'scripts/prepare_market_capture2_image.py').read_bytes();self.assertEqual((len(raw),sha256(raw).hexdigest()),O.NORMAL_PIN)
  self.assertEqual(M.closed_failure(RuntimeError('coreutils_lock'))['code'],'coreutils_lock')
 def test_coreutils_only_package_family_fourteen_actual_recipe_outputs(self):
  self.assertEqual(len(H.OUTPUT_LIMITS),14);self.assertEqual([n for n in H.OUTPUT_LIMITS if n.endswith('.deb')],['coreutils_9.7-3+gold2_amd64.deb'])
  self.assertIn('vendor-suite.json',H.OUTPUT_LIMITS);self.assertIn('generated-build-proof.json',H.OUTPUT_LIMITS)
  self.assertEqual(H.CAPSULE,Path('/coreutils-capsule'));self.assertEqual(H.BASE_VERSION,'9.7-3')
 def test_only_coreutils_inventory_transition_preserves_library_and_other_packages(self):
  before={'coreutils':('9.7-3','installed'),'libacl1:amd64':('2.4.0-1~trixie+gold1','installed'),'tar':('1.35+dfsg-5~trixie+gold2','installed')};after={**before,'coreutils':('9.7-3+gold2','installed')};H.verify_transition(before,after)
  for changed in({**after,'libacl1:amd64':('SDK_WRONG','installed')},{**after,'new-package':('1','installed')},{**after,'tar':('OLD','installed')}):
   with self.assertRaisesRegex(RuntimeError,'package_inventory'):H.verify_transition(before,changed)
  lock={'approved_libacl':{'version':'2.4.0-1~trixie+gold1'}};H.baseline_inventory(before,lock)
  with self.assertRaisesRegex(RuntimeError,'package_baseline'):H.baseline_inventory({**before,'coreutils':('9.7-3+deb13u1','installed')},lock)
 def test_exact_installer_allowlist_has_no_apt_sdk_or_arbitrary_path(self):
  commands=H.commands(H.CAPSULE/H.PACKAGE);self.assertIn(('/usr/bin/dpkg','--verify','coreutils'),commands)
  installs=[a for a in commands if '--install'in a];self.assertEqual(len(installs),1);self.assertEqual(installs[0][-1],'/coreutils-capsule/'+H.PACKAGE)
  self.assertTrue(all('apt'not in a[0]and not any('libacl1-dev'in v for v in a)for a in commands))
  with self.assertRaisesRegex(RuntimeError,'installer_command'):H.commands(self.root/H.PACKAGE)
 def test_capabilities_exact_same_five_not_broadened(self):
  mask=sum(1<<bit for bit in(0,3,4,6,7));rows={'NoNewPrivs':'1','CapBnd':hex(mask),'CapEff':hex(mask),'CapPrm':hex(mask),'CapInh':'0','CapAmb':'0'};H.capability_contract(rows)
  for bad in({**rows,'CapBnd':hex(mask|1<<21)},{**rows,'CapAmb':'1'},{**rows,'NoNewPrivs':'0'}):
   with self.assertRaisesRegex(RuntimeError,'image_boundary'):H.capability_contract(bad)
 def cache(self):
  cache=self.root/'coreutils-acl-capsule';cache.mkdir();cache.chmod(0o755);raw=b'OWNED SYNTHETIC PACKAGE; NOT DEB';lock={'package':{'name':'coreutils','path':H.PACKAGE,'version':H.VERSION},'outputs':{H.PACKAGE:self.pin(raw),'runtime-payload.json':self.pin(b'OWNED SYNTHETIC PAYLOAD')},'source':{'recipe_sha256':'a'*64,'production_patch_sha256':'3c91b4386c7608b4620422daaf88ec561d3bc709140ae29dc904ed54a9833d1d','parent_patch_sha256':H.PARENT_PATCH['sha256'],'parent_cp':dict(H.PARENT_CP)}}
  (cache/H.PACKAGE).write_bytes(raw);(cache/H.PACKAGE).chmod(0o644);(cache/'manifest.json').write_bytes(H.cache_manifest(lock));(cache/'manifest.json').chmod(0o644);return cache,lock
 def test_actual_owned_two_member_cache_admission_and_unknown_member_refusal(self):
  cache,lock=self.cache()
  with H.Held()as held:row=H.verify_cache(cache,lock,held,(os.geteuid(),os.getegid()));self.assertEqual(row.binding,lock['outputs'][H.PACKAGE])
  (cache/'foreign').write_bytes(b'OWNED FOREIGN')
  with H.Held()as held,self.assertRaisesRegex(RuntimeError,'cache_inventory'):H.verify_cache(cache,lock,held)
 def test_hardlinked_cache_package_refuses_before_payload_read(self):
  cache,lock=self.cache();path=cache/H.PACKAGE;os.link(path,self.root/'alias');inode=path.stat().st_ino;real=os.read
  def read(fd,*a):self.assertNotEqual(os.fstat(fd).st_ino,inode);return real(fd,*a)
  with patch.object(H.os,'read',read),H.Held()as held,self.assertRaisesRegex(RuntimeError,'file_metadata'):H.verify_cache(cache,lock,held)
 def test_original_writer_full9_before_close_refuses_samebyte_timestamp_mutation(self):
  path=self.root/'created';actual=os.close;changed=[];held=H.Held()
  def close(fd):
   selected=path.exists()and os.fstat(fd).st_ino==path.stat().st_ino;actual(fd)
   if selected and not changed:st=path.stat();os.utime(path,ns=(st.st_atime_ns,st.st_mtime_ns+1000000));changed.append(True)
  try:
   with patch.object(H.os,'close',close),self.assertRaisesRegex(RuntimeError,'file_closed_changed'):H.write_new(path,b'OWNED known bytes',held=held)
  finally:
   with self.assertRaises(RuntimeError):held.__exit__(None,None,None)
  self.assertTrue(changed)
 def inert_download(self,mutation):
  raw=b'OWNED PUBLIC ZIP BYTES; NO ACTUAL PROVIDER';path=self.root/'download.zip';real_popen=H.subprocess.Popen;real_selector=H.selectors.DefaultSelector;held=H.Held();changed=[]
  def started(argv,**kw):
   self.assertEqual(argv[:4],['/usr/bin/gh','api','--method','GET']);self.assertEqual(kw['env']['GH_TOKEN'],'OWNED_FAKE_NOT_SENT');child=real_popen([sys.executable,'-I','-S','-c','import os;os.write(1,'+repr(raw)+')'],**kw)
   if mutation=='stdout':
    close=child.stdout.close
    def closing():
     close();st=path.stat();os.utime(path,ns=(st.st_atime_ns,st.st_mtime_ns+1000000));changed.append(True)
    child.stdout.close=closing
   return child
  class Selector:
   def __init__(obj):obj.actual=real_selector()
   def __enter__(obj):obj.actual.__enter__();return obj.actual
   def __exit__(obj,*args):
    obj.actual.__exit__(*args)
    if mutation=='selector':st=path.stat();os.utime(path,ns=(st.st_atime_ns,st.st_mtime_ns+1000000));changed.append(True)
  try:
   with patch.dict(H.os.environ,{'GH_TOKEN':'OWNED_FAKE_NOT_SENT'}),patch.object(H.subprocess,'Popen',started),patch.object(H.selectors,'DefaultSelector',Selector),self.assertRaisesRegex(RuntimeError,'file_closed_changed'):H.download({'artifact':{'id':1,**self.pin(raw)}},path,held)
  finally:
   with self.assertRaises(RuntimeError):held.__exit__(None,None,None)
  self.assertTrue(changed);self.assertEqual(path.read_bytes(),raw)
 def test_actual_inert_download_selector_close_cannot_adopt_late_timestamp(self):self.inert_download('selector')
 def test_actual_inert_download_stdout_close_cannot_adopt_late_timestamp(self):self.inert_download('stdout')
 def test_all_python_source_parses_and_does_not_load_local_siblings(self):
  for p in(TREE/'scripts').glob('*.py'):ast.parse(p.read_bytes(),filename=str(p));self.assertTrue(all(x.rstrip()==x for x in p.read_bytes().splitlines()),p.name)
  source=(TREE/'scripts/prepare_coreutils_acl_os_cache.py').read_text();self.assertNotIn('.local/',source);self.assertNotIn('normalize_tar_man_dirs',source)
  self.assertIn('verify_approved_libacl_payload(lock,held)',source);self.assertIn("'full_unfiltered_gnu_test_hook_actual_returncode'",source)
 def lock_context(self):
  receipt={'release_files':[]}
  for name in(M.APT_LOCK,M.SQLITE_LOCK,M.TAR_LOCK,M.COREUTILS_LOCK):
   raw=(TREE/name).read_bytes()
   if name==M.COREUTILS_LOCK:raw=F.encoded(F.unarmed_lock(raw))
   path=self.put(name,raw);receipt['release_files'].append({'path':name,**self.pin(raw)})
  return receipt
 def test_actual_market_cache_reader_refuses_owned_unarmed_coreutils_lock(self):
  receipt=self.lock_context()
  with self.assertRaisesRegex(RuntimeError,'coreutils_lock'):M.cache_inputs(self.root,receipt)
 def test_owned_metadata_cache_join_exact26_entries_without_real_artifact_claim(self):
  receipt=self.lock_context();path=self.root/M.COREUTILS_LOCK;lock=json.loads(path.read_bytes());lock.update(armed=True,outputs={H.PACKAGE:self.pin(b'OWNED SYNTHETIC NOT DEB'),'runtime-payload.json':self.pin(b'OWNED SYNTHETIC NOT PROOF')},source={'recipe_sha256':'a'*64,'production_patch_sha256':'3c91b4386c7608b4620422daaf88ec561d3bc709140ae29dc904ed54a9833d1d','parent_patch_sha256':H.PARENT_PATCH['sha256'],'parent_cp':dict(H.PARENT_CP)})
  # Explicitly tests only the metadata join, NOT validate_lock/admission or any
  # real artifact. Only the owned fixture lock changes.
  raw=M.encoded(lock);path.write_bytes(raw)
  for row in receipt['release_files']:
   if row['path']==M.COREUTILS_LOCK:row.update(self.pin(raw))
  entries,sqlite=M.cache_inputs(self.root,receipt);self.assertEqual(len(entries),26)
  self.assertEqual(entries['pip_packages/coreutils-acl-capsule/'+H.PACKAGE],self.pin(b'OWNED SYNTHETIC NOT DEB'));self.assertEqual(entries['pip_packages/.coreutils-acl-capsule.lock'],self.pin(b''))
  self.assertEqual(entries['pip_packages/coreutils-acl-capsule/manifest.json'],self.pin(H.cache_manifest(lock)))
 def test_current_market_metadata_join_authenticates_gold2_and_refuses_gold1(self):
  receipt=self.lock_context();path=self.root/M.COREUTILS_LOCK
  raw=(TREE/M.COREUTILS_LOCK).read_bytes();path.write_bytes(raw)
  selected=next(row for row in receipt['release_files']if row['path']==M.COREUTILS_LOCK);selected.update(self.pin(raw))
  lock=json.loads(raw);entries,_=M.cache_inputs(self.root,receipt)
  self.assertEqual(entries['pip_packages/coreutils-acl-capsule/'+H.PACKAGE],lock['outputs'][H.PACKAGE])
  self.assertEqual(entries['pip_packages/coreutils-acl-capsule/manifest.json'],self.pin(H.cache_manifest(lock)))
  # Only this owned metadata DTO is changed; it is never an actual gold1 proof.
  lock['package'].update(version='9.7-3+gold1',path='coreutils_9.7-3+gold1_amd64.deb')
  raw=M.encoded(lock);path.write_bytes(raw);selected.update(self.pin(raw))
  with self.assertRaisesRegex(RuntimeError,'^coreutils_lock$'):M.cache_inputs(self.root,receipt)
 def test_actual_app_public_inventory_refuses_unarmed_lock_and_all_prior_lock_bytes_bound(self):
  receipt=self.lock_context();rows=[]
  for row in receipt['release_files']:
   path=self.root/row['path'];rows.append({**row,'identity':C.identity(path.stat())})
  with self.assertRaisesRegex(RuntimeError,'input_inventory'):C.public_input_names(self.root,{'files':rows})
 def test_deployment_cache_preparation_and_closed_control_payload_include_new_sources(self):
  text=(TREE/'scripts/production_deploy_online.sh').read_text();self.assertEqual(text.count('python3 -I -B "$LOCAL_PROJECT_DIR/scripts/prepare_coreutils_acl_os_cache.py"'),text.count('python3 -I -B "$LOCAL_PROJECT_DIR/scripts/prepare_tar_acl_os_cache.py"'))
  self.assertEqual(text.count('scripts/coreutils_acl_package.lock.json'),text.count('scripts/tar_acl_package.lock.json'))
  self.assertIn('--check --cache-dir "$output_dir/tar-acl-capsule" \\\n        || die "Prepared Tar',text)
  self.assertIn('--check --cache-dir "$output_dir/coreutils-acl-capsule" \\\n        || die "Prepared Coreutils',text)
 def test_actual_controller_receipt_joins26_and_refuses_old23(self):
  receipt={'schema':'normal_market_git_component_inputs/1','signature_format':'normal-market-two-git-sources-v1','accepted_manifest_sha256':M.MANIFEST_SHA,'repository_identity':{'host':'github.com','repository':M.REPOSITORY},'release_commit':'a'*40,'release_tree':'b'*40,'business_commit':'c'*40,'business_tree':'d'*40}
  controllers=[{'path':n,'mode':'100644','git_blob':'e'*40,'bytes':1,'sha256':'f'*64}for n in sorted(M.CONTROLLER_FILES)];cache={'pip_packages/OWNED-'+str(i):self.pin(b'OWNED')for i in range(26)};signature=sha256(M.encoded(receipt)).hexdigest()
  value,digest=M.receipt_contract(receipt,signature,controllers,cache,1793059200);self.assertEqual(len(value['normal_cache_files']),26);self.assertFalse(value['security_accepted'])
  with self.assertRaisesRegex(RuntimeError,'controller_inventory'):M.receipt_contract(receipt,signature,controllers,dict(list(cache.items())[:23]),1793059200)
 def archive_fixture(self):
  # Entirely owned fabricated transcripts exercise the bounded codec and
  # joins, never a DEB or actual compile/vendor/native acceptance. Production
  # lock never changes, and no installer/prepare is called with this.
  lock=deepcopy(json.loads((TREE/'scripts/coreutils_acl_package.lock.json').read_bytes()));lock['armed']=True;package=b'OWNED SYNTHETIC PACKAGE; NOT DEB';binary=b'OWNED SYNTHETIC BINARY; NOT ELF';library={k:lock['approved_libacl'][k]for k in('path','bytes','sha256')}
  callers={name:{'path':'/usr/bin/'+name,**self.pin(binary)}for name in('cp','mv','install')};lock['installed_callers']=callers
  def group(package,version,digest,rows):return{'package':package,'installed_version':version,'deb_sha256':digest,'regular_files':rows,'symlinks':[],'regular_and_link_inventory_complete':True}
  regular=[{**r,'canonical_path':r['path'],'mode':0o100755,'uid':0,'gid':0}for r in callers.values()]
  librow={**library,'canonical_path':library['path'],'mode':0o100644,'uid':0,'gid':0}
  payload={'schema':'coreutils_acl_runtime_payload/1','coreutils':group('coreutils',H.VERSION,sha256(package).hexdigest(),regular),'approved_libacl':group('libacl1',lock['approved_libacl']['version'],'bbb12e190bfe19b876f73a669848ebd8d04293d95000babf51058c88879966b1',[librow]),'security_accepted':False};lock['runtime_payload']=payload
  native={'private_fd':{'actual_uid':10001,'actual_gid':10001,'actual_returncode':0,'skip_count':0,'NFSv4_real_filesystem_exercised':False,'actual_assertions':20},'gnu_fd0':{'schema':'ordinary_gnulib_regular_fd0/1','status':'PASS','actual_uid':10001,'actual_gid':10001,'actual_gnulib_qcopy_acl_called':True,'source_fd_zero':True},'parent_frontend':{'schema':'ordinary_cp_parent_frontend/1','status':'PASS','actual_uid':10001,'actual_gid':10001,'actual_case_count':4,'actual_EMFILE_partial_prefixes':2,'actual_assertions':223,'skip_count':0,'actual_frontend_and_copy_called':True,'actual_OPATH_pairs':True,'source_fd_zero':True,'physical_pair_cleanup_verified':True,'owned_cleanup_complete':True,'security_accepted':False}}
  for row in native.values():row['actual_fixture_binary']=self.pin(b'OWNED SYNTHETIC FIXTURE; NOT ELF')
  cli={'schema':'coreutils_owned_cli_compatibility/1','status':'PASS','actual_uid':10001,'actual_gid':10001,'skip_count':0,'security_accepted':False,'actual_case_count':23,'legacy_case_count':14,'parent_case_count':9,'cases':list(H.CLI_CASES),'all_commands_actual_zero_and_pipes_closed':True,'owned_cleanup_complete':True,'parent_ownership_and_timestamp_path_semantics_retained':True,'production_data_accessed':False,'exploit_or_race_reproduction_performed':False,'actual_installed_candidate_binaries':{n:{k:r[k]for k in('path','sha256')}for n,r in callers.items()}};required=['acl_delete_def_file_at','acl_set_file_at']
  abirows={n:{'path':r['path'],'candidate_binary':{k:r[k]for k in('bytes','sha256')},'libacl':library,'dynamic_approved_libacl_not_static_sdk':True,'actual_ldd_acl_target':library['path'],'security_accepted':False,'required_private_at_imports':required,'actual_versioned_acl_bindings':{k:'ACL_2.4'for k in required}}for n,r in callers.items()}
  source={'commit':'a'*40,**{k:'b'*64 for k in('recipe_sha256','controller_sha256','root_artifact_admission_sha256','root_static_verification_sha256','ci_result_sha256','ci_source_identity_sha256')},'production_patch_sha256':'3c91b4386c7608b4620422daaf88ec561d3bc709140ae29dc904ed54a9833d1d','parent_patch_sha256':H.PARENT_PATCH['sha256'],'parent_cp':dict(H.PARENT_CP)};lock['source']=source
  counts={'TOTAL':1,'PASS':1,'SKIP':0,'XFAIL':0,'FAIL':0,'XPASS':0,'ERROR':0}
  proof={
   'runtime-payload.json':payload,'ordinary-compatibility.json':{'schema':'coreutils_acl_ordinary_compatibility/1','required_owned_c_fixtures':native,'actual_cli':cli,'all_required_owned_cases_skip_count':0,'security_accepted':False},
   'caller-proof.json':{'schema':'coreutils_descriptor_caller_build/1','source_patch_sha256':source['production_patch_sha256'],'parent_source_patch_sha256':source['parent_patch_sha256'],'ordinary_coreutils_compatibility_passed':True,'whole_image_other_callers_assessed':False,'all_cves_attested':False,'security_accepted':False,'required_owned_fixtures':native,'compiled_caller_contract':abirows},
   'abi-contract.json':{'schema':'coreutils_dynamic_approved_acl_abi/1','security_accepted':False,'callers':abirows},
   'vendor-suite.json':{'schema':'coreutils_unfiltered_vendor_command/1','actual_command':['/usr/bin/dh_auto_test'],'actual_returncode':0,'actual_uid':10001,'actual_gid':10001,'actual_pipe_closed':True,'configured_default_selections_captured_before_test':True,'test_filter_override_used':False,'test_bodies_modified':False,'security_accepted':False,'observation':{'scopes':{n:{'actual_summary':dict(counts),'actual_configured_test_count':1}for n in('coreutils','gnulib')},'failed_unexpected_or_error_results':0,'security_accepted':False}},
   'source-proof.json':{'schema':'coreutils_acl_source_build/1','maintainer_dsc_signatures_authenticated':True,'patch':{'sha256':source['production_patch_sha256']},'actual_source_contract':{'parent_acl_patch':dict(H.PARENT_PATCH),'parent_cp_actual':dict(H.PARENT_CP)},'source_manifest':{'package_version':H.VERSION,'parent_acl_patch':dict(H.PARENT_PATCH),'files':{'src/cp.c':{'after':dict(H.PARENT_CP)}}},'security_accepted':False},
   'sdk-proof.json':{'schema':'coreutils_acl_compiler_sdk/1','compiler_only':True,'runtime_replacement_exported':False,'actual_approved_runtime_after_reinstall':library,'security_accepted':False},
   'generated-build-proof.json':{'schema':'coreutils_actual_generated_build_provenance/1','configured_descriptor_acl_feature':True,'debian_dh_autoreconf_noop_removed':True,'security_accepted':False},
   'provenance.json':{'schema':'coreutils_acl_package_provenance/1','normal_package_version':H.VERSION,'full_unfiltered_gnu_test_hook_actual_returncode':0,'vendor_test_selection':'configured_default_unchanged','vendor_zero_skip_coverage_claimed':False,'runtime_loader_override_used':False,'vendor_build_only_loader_mechanics_not_disabled':True,'uid10001_children_gone':True,'only_new_coreutils_runtime_exported':True,'production_installed':False,'security_accepted':False}}
  return lock,proof,package
 def encode_archive(self,lock,proof,package):
  rows={name:H.encoded(proof[name])if name.endswith('.json')else package if name==H.PACKAGE else b'OWNED SYNTHETIC BUILD RECORD'for name in H.OUTPUT_LIMITS};lock['outputs']={n:self.pin(raw)for n,raw in rows.items()};buffer=io.BytesIO()
  with zipfile.ZipFile(buffer,'w',compression=zipfile.ZIP_STORED)as archive:
   for n,raw in rows.items():archive.writestr(n,raw)
  raw=buffer.getvalue();lock['artifact']={'id':1,'run_id':1,'members':len(rows),**self.pin(raw)};return raw
 def test_owned_fourteen_member_codec_positive_not_runtime_authority(self):
  production_before=(TREE/'scripts/coreutils_acl_package.lock.json').read_bytes()
  lock,proof,package=self.archive_fixture();raw=self.encode_archive(lock,proof,package);H.validate_lock(lock);self.assertEqual(H.admit_archive(raw,lock),package)
  self.assertEqual((TREE/'scripts/coreutils_acl_package.lock.json').read_bytes(),production_before)
 def test_gold2_source_bindings_are_required_in_lock_and_cache_manifest(self):
  actual=json.loads((TREE/'scripts/coreutils_acl_package.lock.json').read_bytes());H.validate_lock(actual)
  self.assertEqual(actual['artifact']['run_id'],40835548335);self.assertEqual(actual['artifact']['id'],12880159950)
  self.assertEqual(actual['source']['root_artifact_admission_sha256'],'d5b2792d3504a1c712e63b511e0a5857dd1d4a2b497f8c1c704fbda0ad7fa8cc')
  self.assertEqual(actual['source']['root_static_verification_sha256'],'3e657ea52961d9c6b5249b3bb242767be3acd99613dbbe85b7d130e10ae467af')
  self.assertEqual(json.loads(H.cache_manifest(actual))['source'],{k:actual['source'][k]for k in('recipe_sha256','production_patch_sha256','parent_patch_sha256','parent_cp')})
  for field in('parent_patch_sha256','parent_cp'):
   with self.subTest(field=field):
    wrong=deepcopy(actual);wrong['source'].pop(field)
    with self.assertRaisesRegex(RuntimeError,'lock_contract'):H.validate_lock(wrong)
  wrong=deepcopy(actual);wrong['source']['parent_patch_sha256']='0'*64
  with self.assertRaisesRegex(RuntimeError,'lock_contract'):H.validate_lock(wrong)
 def test_synthetic_negative_gold2_parent_admission_cannot_be_waived_by_rebinding(self):
  # These mutations rebind invented ZIP metadata only. They are NOT new actual
  # CI reports and cannot change the authentic, source-pinned production lock.
  parent=lambda p:p['ordinary-compatibility.json']['required_owned_c_fixtures']['parent_frontend']
  cli=lambda p:p['ordinary-compatibility.json']['actual_cli']
  mutations={
   'missing_parent_fixture':lambda p:p['ordinary-compatibility.json']['required_owned_c_fixtures'].pop('parent_frontend'),
   'old_fourteen_cli':lambda p:cli(p).update(actual_case_count=14,parent_case_count=0,cases=list(H.CLI_CASES[:14])),
   'wrong_cli_order':lambda p:cli(p).update(cases=list(reversed(H.CLI_CASES))),
   'wrong_caller_parent_patch':lambda p:p['caller-proof.json'].update(parent_source_patch_sha256='0'*64),
   'wrong_source_parent_patch':lambda p:p['source-proof.json']['actual_source_contract']['parent_acl_patch'].update(sha256='0'*64),
   'wrong_manifest_parent_patch':lambda p:p['source-proof.json']['source_manifest']['parent_acl_patch'].update(sha256='0'*64),
   'wrong_actual_cp':lambda p:p['source-proof.json']['actual_source_contract']['parent_cp_actual'].update(sha256='0'*64),
   'wrong_manifest_cp':lambda p:p['source-proof.json']['source_manifest']['files']['src/cp.c']['after'].update(sha256='0'*64),
   'physical_cleanup_false':lambda p:parent(p).update(physical_pair_cleanup_verified=False),
   'owned_cleanup_false':lambda p:parent(p).update(owned_cleanup_complete=False),
   'source_fd_zero_false':lambda p:parent(p).update(source_fd_zero=False),
   'pressure_missing':lambda p:parent(p).update(actual_EMFILE_partial_prefixes=0),
   'pressure_boolean':lambda p:parent(p).update(actual_EMFILE_partial_prefixes=True),
   'partial_case_count':lambda p:parent(p).update(actual_case_count=3),
   'cli_cleanup_false':lambda p:cli(p).update(owned_cleanup_complete=False),
  }
  for name,mutate in mutations.items():
   with self.subTest(mutation=name):
    lock,proof,package=self.archive_fixture();mutate(proof);raw=self.encode_archive(lock,proof,package);H.validate_lock(lock)
    with self.assertRaisesRegex(RuntimeError,'archive_contract'):H.admit_archive(raw,lock)
 def test_rebound_synthetic_transcripts_cannot_waive_gnu_native_or_abi_contract(self):
  mutations=(lambda p:p['vendor-suite.json'].update(actual_returncode=1),lambda p:p['vendor-suite.json'].update(actual_returncode=False),lambda p:p['vendor-suite.json']['observation']['scopes']['coreutils']['actual_summary'].update(FAIL=1),lambda p:p['ordinary-compatibility.json']['required_owned_c_fixtures']['private_fd'].update(skip_count=1),lambda p:p['ordinary-compatibility.json']['required_owned_c_fixtures']['private_fd'].update(skip_count=False),lambda p:p['abi-contract.json']['callers']['cp'].update(dynamic_approved_libacl_not_static_sdk=False),lambda p:p['sdk-proof.json'].update(runtime_replacement_exported=True))
  for mutate in mutations:
   lock,proof,package=self.archive_fixture();mutate(proof);raw=self.encode_archive(lock,proof,package);H.validate_lock(lock)
   with self.assertRaisesRegex(RuntimeError,'archive_contract'):H.admit_archive(raw,lock)
if __name__=='__main__':unittest.main()
