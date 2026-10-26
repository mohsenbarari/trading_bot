"""Owned public files/schema fixtures; no actual Git, network or Docker."""
from contextlib import ExitStack,redirect_stdout
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from types import ModuleType,SimpleNamespace
from unittest.mock import patch
import ast,io,json,os,tempfile,unittest

HERE=Path(__file__).absolute().parent;MAIN=HERE.parent
def load(path):
 m=ModuleType('_owned_ci_'+path.stem);m.__file__=str(path);exec(compile(path.read_bytes(),str(path),'exec'),m.__dict__);return m
A=load(MAIN/'scripts/prepare_sqlite_wal_ci_context.py')
R=load(MAIN/'deploy/security/sqlite-wal-backport/sourcebuild.py')
def proof(raw):return {'bytes':len(raw),'sha256':sha256(raw).hexdigest()}
def positive(version):
 options=['ENABLE_FTS5','THREADSAFE=1'];mapped={'path':'/usr/lib/x86_64-linux-gnu/libsqlite3.so.0.8.6','bytes':1024,'sha256':'a'*64,'mapped_inode_verified':True}
 extension={'path':'/usr/local/lib/python3.11/lib-dynload/_sqlite3.so','bytes':512,'sha256':'b'*64,'mapped_inode_verified':True}
 return {'schema':'sqlite_ordinary_compatibility/1','status':'PASS','python_version':'3.11.17','sqlite_package':version,'sqlite_runtime':'3.46.1',
  'system_library':mapped['path'],'sqlite_system_library':mapped,'sqlite_stdlib_extension':extension,'python_environment_ignored':True,
  'compile_options':options,'compile_options_sha256':sha256((json.dumps(options,separators=(',',':'))+'\n').encode()).hexdigest(),
  'compatibility':{'wal_snapshot':True,'rollback_upsert':True,'json_fts':True,'backup_readonly_reopen':True,'lookup_index':True,
   'query_result_sha256':'c'*64,'query_plan':['USING INDEX quote_lookup']},'production_data_accessed':False,'wal_reset_reproduction_performed':False,'all_cves_attested':False,'security_accepted':False}
def fixtures():
 package=b'OWNED SYNTHETIC PACKAGE ONLY';p=proof(package)
 buildinfo=('Format: 1.0\nChecksums-Sha256:\n '+p['sha256']+' '+str(p['bytes'])+' '+A.PACKAGE+'\n').encode();b=proof(buildinfo)
 changes=('Format: 1.8\nChecksums-Sha256:\n '+p['sha256']+' '+str(p['bytes'])+' '+A.PACKAGE+'\n '+b['sha256']+' '+str(b['bytes'])+' '+A.BUILDINFO+'\n').encode()
 rows={A.PACKAGE:package,A.CHANGES:changes,A.BUILDINFO:buildinfo,'positive-baseline.json':positive(A.BASE),'positive-candidate.json':positive(A.VERSION),
  'abi-contract.json':{'soname':'libsqlite3.so.0','needed':['libc.so.6'],'exports':sorted(['sqlite3_open_v2','sqlite3_prepare_v2','sqlite3_backup_init','sqlite3_wal_checkpoint_v2']),'glibc_versions':['GLIBC_2.2.5']}}
 proofs={k:proof(A.encoded(v)if type(v)is dict else v)for k,v in rows.items()}
 provenance={'schema':'sqlite_wal_local_debian_package/1','package':'libsqlite3-0','base_package_version':A.BASE,'local_package_version':A.VERSION,
  'upstream_version_unchanged':'3.46.1','architecture':'amd64','repository_snapshot':R.SNAPSHOT,'repository_release':{'bytes':R.INRELEASE[0],'sha256':R.INRELEASE[1]},
  'source_files':{k:{'bytes':v[0],'sha256':v[1]}for k,v in R.SOURCE_FILES.items()},'source_maintainer_signature_verified':True,
  'debian_rules':{'bytes':512,'sha256':'d'*64},'production_patch':{'bytes':R.PATCH_PIN[0],'sha256':R.PATCH_PIN[1]},
  'wal_before':{'bytes':R.WAL_BEFORE[0],'sha256':R.WAL_BEFORE[1]},'wal_after':{'bytes':R.WAL_AFTER[0],'sha256':R.WAL_AFTER[1]},
  'positive_verifier':{'bytes':R.VERIFY_PIN[0],'sha256':R.VERIFY_PIN[1]},'source_date_epoch':R.SOURCE_EPOCH,'build_options':'nocheck parallel=2',
  'upstream_fault_suites_run':False,'wal_reset_reproduction_performed':False,'full_upstream_suite_pass_claimed':False,
  'baseline_library':{k:rows['positive-baseline.json']['sqlite_system_library'][k]for k in ('bytes','sha256')},
  'candidate_library':{k:rows['positive-candidate.json']['sqlite_system_library'][k]for k in ('bytes','sha256')},
  'package_file':{'name':A.PACKAGE,**p},'local_build_metadata':{A.CHANGES:proofs[A.CHANGES],A.BUILDINFO:proofs[A.BUILDINFO]},
  'local_build_metadata_signed':False,'compile_options_equal':True,'elf_soname_exports_dependencies_equal':True,'python_binary_unchanged':True,
  'sqlite_stdlib_extension_unchanged':True,'mapped_installed_libraries_verified':True,'build_toolchain_packages':['gcc 1.0'],
  'full_sqlite_documentation_restored':True,'dpkg_path_includes':list(R.DOC_INCLUDES),'runtime_loader_override_used':False,
  'production_data_accessed':False,'app_ci_restore_gate_required':True,'security_accepted':False}
 rows['provenance.json']=provenance;proofs['provenance.json']=proof(A.encoded(provenance));return rows,proofs

class DriverTests(unittest.TestCase):
 def setUp(self):
  self.held=ExitStack();self.addCleanup(self.held.close)
  for name in ('subprocess.Popen','subprocess.run','socket.socket','urllib.request.urlopen','os.system','os.popen'):
   self.held.enter_context(patch(name,side_effect=AssertionError('no ambient operation')))
  self.root=Path(self.held.enter_context(tempfile.TemporaryDirectory(prefix='.owned-ci-',dir=HERE)))
 def rejects(self,rows,proofs):
  with self.assertRaises(RuntimeError):A.verify_artifacts(R,rows,proofs)
 def test_default_cli_is_unarmed_without_even_host_environment_or_operations(self):
  out=io.StringIO()
  with patch.object(A,'execute',side_effect=AssertionError('not entered')),redirect_stdout(out):self.assertEqual(A.main([]),0)
  self.assertFalse(json.loads(out.getvalue())['build_performed'])
 def test_hosted_admission_actual_contract_never_accepts_root_or_selfhosted(self):
  facts={'uid':1001,'euid':1001,'runner_environment':'github-hosted','machine':'x86_64','os_id':'ubuntu','os_version':'24.04',
   'cpus':4,'affinity_cpus':4,'mem_total_bytes':8*1024**3,'free_bytes':20*1024**3,'disk_bytes':40*1024**3}
  self.assertIs(A.validate_host(facts),facts)
  for key,value in [('uid',0),('euid',0),('runner_environment','self-hosted'),('os_version','22.04'),('machine','aarch64'),('cpus',True),('free_bytes',1),('mem_total_bytes',1024)]:
   v=deepcopy(facts);v[key]=value
   with self.subTest(key=key),self.assertRaises(RuntimeError):A.validate_host(v)
 def test_unique_root_tied_to_actual_run_attempt_and_never_adopted(self):
  env={'GITHUB_RUN_ID':'123','GITHUB_RUN_ATTEMPT':'2','RUNNER_TEMP':str(self.root)}
  args=SimpleNamespace(repo=str(self.root),commit='a'*40,root=str(self.root/'sqlite-wal-123-2'))
  self.assertEqual(A.layout(args,env)[1],Path(args.root));Path(args.root).mkdir()
  with self.assertRaises(RuntimeError):A.layout(args,env)
  args.root=str(self.root/'OTHER')
  with self.assertRaises(RuntimeError):A.layout(args,env)
 def test_actual_public_git_bytes_pinned_without_running_git(self):
  source=MAIN/'deploy/security/sqlite-wal-backport';calls=[]
  class Client:
   def metadata(self,argv,maximum):
    calls.append(argv);leaf=argv[-1].split(':',1)[1].split('/')[-1]
    return b'blob\n'if argv[-2]=='-t'else str(A.PINS[leaf][0]).encode()+b'\n'
   def bounded_command(self,argv,**kwargs):calls.append(argv);return(source/argv[-1].split('/')[-1]).read_bytes()
  blobs=A.git_blobs(Client(),self.root,'a'*40);self.assertEqual(set(blobs),set(A.PINS));self.assertEqual(len(calls),12)
  self.assertTrue(all(argv[:4]==['/usr/bin/git','-C',str(self.root),'cat-file']for argv in calls))
 def test_mutated_or_nonblob_git_object_rejected_before_context_write(self):
  fake=SimpleNamespace(metadata=lambda *a,**k:b'tree\n')
  with self.assertRaises(RuntimeError):A.git_blobs(fake,self.root,'a'*40)
 def test_real_owned_held_file_and_physical_close_drift(self):
  p=self.root/'public.py';p.write_bytes(b'PUBLIC OWNED DATA');p.chmod(0o600);frames=[]
  with ExitStack()as held:raw,check=A.held(p,held,frames,uid=os.geteuid(),pin=(17,sha256(p.read_bytes()).hexdigest()));check()
  A.named(frames);p.chmod(0o400)
  with self.assertRaises(RuntimeError):A.named(frames)
 def test_exclusive_public_publish_never_overwrites(self):
  p=self.root/'output';A.publish(p,b'OWNED PUBLIC',os.geteuid())
  with self.assertRaises(FileExistsError):A.publish(p,b'OTHER',os.geteuid())
  self.assertEqual(p.read_bytes(),b'OWNED PUBLIC')
 def test_context_exact_inventory_rejects_history_and_denyall_ignore(self):
  # Explicit synthetic fetch unit boundary; production R is fixed-SHA loaded.
  blobs={k:(MAIN/'deploy/security/sqlite-wal-backport'/k).read_bytes()for k in A.PINS}
  names=list(R.SOURCE_FILES);payloads={name:('OWNED PUBLIC '+name).encode()for name in names}
  fake=SimpleNamespace(SNAPSHOT=R.SNAPSHOT,SOURCE_FILES={name:(len(raw),sha256(raw).hexdigest())for name,raw in payloads.items()})
  fake.fetch=lambda url,target,pin:A.publish(target,payloads[target.name],os.geteuid())
  frames=[]
  with ExitStack()as stack:
   context,bindings=A.seal_context(fake,self.root,blobs,stack,frames,os.geteuid());self.assertEqual(len(bindings),8)
   self.assertTrue((context/'.dockerignore').read_text().startswith('**\n'))
   self.assertEqual(set(json.loads((context/'context-manifest.json').read_bytes())['files']),set(bindings))
   (context/'history-only.txt').write_text('OWNED EXTRA')
   with self.assertRaises(RuntimeError):A.context_inventory(context,fake)
  A.named(frames)
 def test_exact_seven_ordinary_artifacts_pass_without_install_or_package_execution(self):
  rows,proofs=fixtures();v=A.verify_artifacts(R,rows,proofs);self.assertTrue(v['baseline_pass']);self.assertFalse(v['security_accepted'])
 def test_extra_missing_or_symlink_like_output_inventory_cannot_be_accepted(self):
  rows,proofs=fixtures();rows['extra']=b'OWNED'
  self.rejects(rows,proofs);rows,proofs=fixtures();del rows[A.PACKAGE];self.rejects(rows,proofs)
 def test_any_security_claim_fault_execution_or_false_signature_refuses(self):
  for key,value in [('source_maintainer_signature_verified',False),('security_accepted',True),('production_data_accessed',True),('wal_reset_reproduction_performed',True),('mapped_installed_libraries_verified',1)]:
   rows,proofs=fixtures();rows['provenance.json'][key]=value;self.rejects(rows,proofs)
 def test_provenance_extra_or_source_patch_pin_drift_refuses(self):
  for key,value in [('extra',True),('production_patch',{'bytes':6746,'sha256':'0'*64}),('repository_snapshot','https://other.invalid')]:
   rows,proofs=fixtures();rows['provenance.json'][key]=value;self.rejects(rows,proofs)
 def test_positive_fail_wrong_interpreter_and_missing_mapping_refuse(self):
  for key,value in [('status','FAIL'),('python_version','3.12.0'),('sqlite_package',A.BASE),('security_accepted',True),('sqlite_system_library',{})]:
   rows,proofs=fixtures();rows['positive-candidate.json'][key]=value;self.rejects(rows,proofs)
 def test_compile_option_hash_and_extension_or_native_abi_drift_refuse(self):
  rows,proofs=fixtures();rows['positive-candidate.json']['compile_options_sha256']='0'*64;self.rejects(rows,proofs)
  rows,proofs=fixtures();rows['positive-candidate.json']['sqlite_stdlib_extension']['sha256']='0'*64;self.rejects(rows,proofs)
  for key,value in [('soname','OTHER'),('exports',['unrelated']),('needed',[])]:
   rows,proofs=fixtures();rows['abi-contract.json'][key]=value;self.rejects(rows,proofs)
 def test_package_and_buildinfo_sha_members_are_exact_not_substring(self):
  rows,proofs=fixtures();rows[A.CHANGES]=rows[A.CHANGES].replace(A.PACKAGE.encode(),(A.PACKAGE+'_other').encode());self.rejects(rows,proofs)
  rows,proofs=fixtures();rows[A.CHANGES]=rows[A.CHANGES].replace(proofs[A.BUILDINFO]['sha256'].encode(),b'0'*64);self.rejects(rows,proofs)
 def test_duplicate_checksums_and_duplicate_json_keys_refuse(self):
  rows,proofs=fixtures();raw=rows[A.BUILDINFO];raw+=raw.splitlines()[-1]+b'\n'
  with self.assertRaises(RuntimeError):A.debian_checksums(raw)
  with self.assertRaises(RuntimeError):A.document(b'{"schema":1,"schema":2}')
 def test_real_vm_disk_floor_and_growth_rules(self):
  A.check_disk(20*1024**3,15*1024**3)
  for free in (A.DISK_RESERVE-1,7*1024**3):
   with self.assertRaises(RuntimeError):A.check_disk(20*1024**3,free)
 def test_documented_runner_disk_budget_accepts_ten_not_sixteen_gib(self):
  self.assertEqual(A.DISK_RESERVE+A.DISK_GROWTH,10*1024**3)
  facts={'uid':1001,'euid':1001,'runner_environment':'github-hosted','machine':'x86_64','os_id':'ubuntu','os_version':'24.04',
   'cpus':2,'affinity_cpus':2,'mem_total_bytes':4*1024**3,'free_bytes':10*1024**3,'disk_bytes':14*1024**3}
  A.validate_host(facts);A.check_disk(10*1024**3,2*1024**3)
  with self.assertRaises(RuntimeError):A.check_disk(10*1024**3,2*1024**3-1)
 def test_both_separate_filesystem_scopes_are_checked_without_fake_host_statfs(self):
  # Pure scope checker with explicit owned observations, no global statfs waiver.
  scopes=[{'scope':'source','initial':10*1024**3,'current':9*1024**3},
          {'scope':'docker','initial':12*1024**3,'current':11*1024**3}]
  for scope in scopes:A.check_disk(scope['initial'],scope['current'])
  scopes[1]['current']=A.DISK_RESERVE-1
  with self.assertRaises(RuntimeError):
   for scope in scopes:A.check_disk(scope['initial'],scope['current'])
  tree=ast.parse((MAIN/'scripts/prepare_sqlite_wal_ci_context.py').read_bytes())
  self.assertTrue(any(isinstance(n,ast.Constant)and n.value=='{{json .DockerRootDir}}'for n in ast.walk(tree)))
  self.assertGreaterEqual(sum(isinstance(n,ast.Call)and isinstance(n.func,ast.Name)and n.func.id=='check_disk_scope'for n in ast.walk(tree)),2)
 def test_build_argv_and_source_do_not_claim_client_cgroup_or_mount_private_data(self):
  argv=A.build_argv(self.root/'context',self.root/'out');self.assertEqual(argv[argv.index('--builder')+1],'default')
  for bad in ('--secret','--ssh','--memory','--cpus','--volume','--privileged'):self.assertNotIn(bad,argv)
  tree=ast.parse((MAIN/'scripts/prepare_sqlite_wal_ci_context.py').read_bytes());attrs={n.func.attr for n in ast.walk(tree)if isinstance(n,ast.Call)and isinstance(n.func,ast.Attribute)}
  self.assertNotIn('stage_context',attrs);self.assertNotIn('inputs',attrs)
  self.assertTrue({'fetch','bounded_command'}<=attrs)
 def test_report_schema_closure_matches_real_frozen_producer_ast(self):
  tree=ast.parse((MAIN/'deploy/security/sqlite-wal-backport/sourcebuild.py').read_bytes())
  report=next(n.value for n in ast.walk(tree)if isinstance(n,ast.Assign)and any(isinstance(t,ast.Name)and t.id=='report'for t in n.targets))
  self.assertEqual(A.PROVENANCE_FIELDS,{k.value for k in report.keys})

if __name__=='__main__':unittest.main()
