"""Offline gold2 protocol/wire controls; no compiler/vendor/build/Host authority."""
from contextlib import ExitStack,redirect_stdout
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from types import ModuleType
from unittest.mock import patch
import ast,base64,io,json,os,tempfile,unittest

ROOT=Path(__file__).absolute().parent.parent
def load(relative):
 p=ROOT/relative;m=ModuleType('_owned_gold2_'+p.stem);m.__file__=str(p)
 exec(compile(p.read_bytes(),str(p),'exec'),m.__dict__);return m
R=load('deploy/security/sqlite-wal-backport/sourcebuild.py')
A=load('scripts/prepare_sqlite_wal_ci_context.py')
F=load('tests/test_prepare_sqlite_wal_ci_context.py')
REAL_POPEN=R.subprocess.Popen

def standard():return b'0 errors out of 15000 tests on OWNED UNIT ONLY\n'
def regression():
 return b'\n'.join(f'fts5prefix2-4-{d}-{n}... Ok'.encode()for d in('full','col','none')for n in(1,2))+b'\n0 errors out of 40 tests on OWNED UNIT ONLY\n'

class Gold2SourceTests(unittest.TestCase):
 def setUp(self):
  self.stack=ExitStack();self.addCleanup(self.stack.close)
  self.stack.enter_context(patch.dict(os.environ,{'APP_ENV_FILE':'config/unit-test.env.example'},clear=True))
  for name in ('subprocess.Popen','subprocess.run','socket.socket','urllib.request.urlopen','os.system','os.popen'):
   self.stack.enter_context(patch(name,side_effect=AssertionError('no ambient operation')))
  self.root=Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='.owned-gold2-',dir=ROOT/'tests')))
 def reject(self,rows,proofs):
  with self.assertRaises(RuntimeError):A.verify_artifacts(R,rows,proofs)
 def source_fixture(self):
  for n in ('Makefile','libtool','debian/rules','src/wal.c','ext/fts5/fts5_index.c','ext/fts5/test/fts5prefix2.test',*R.VENDOR_HELPERS):
   p=self.root/n;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(('OWNED PUBLIC UNIT SOURCE '+n).encode());p.chmod(0o600)
  harness=F.fixture_harness()
  (self.root/'Makefile').write_bytes(R.testfixture_decode(harness['generated_after'],131072))
  (self.root/'Makefile.in').write_bytes(R.testfixture_decode(harness['generated_before'],131072))
  # Explicit UNIT source admission seam; production requires exact signed-vendor helper bytes.
  self.stack.enter_context(patch.dict(R.VENDOR_HELPERS,{n:(len((self.root/n).read_bytes()),sha256((self.root/n).read_bytes()).hexdigest())for n in R.VENDOR_HELPERS},clear=True))
  return self.root
 def test_six_recipe_pins_join_all_actual_source_files(self):
  self.assertEqual(set(A.PINS),set(R.RECIPE_FILES));self.assertEqual(len(A.PINS),6)
  for name,pin in A.PINS.items():
   raw=(ROOT/'deploy/security/sqlite-wal-backport'/name).read_bytes()
   self.assertEqual((len(raw),sha256(raw).hexdigest()),pin)
 def test_active_gold2_lock_matches_accepted_producer_and_binds_both_vendor_logs(self):
  lock=json.loads((ROOT/'scripts/sqlite_wal_package.lock.json').read_bytes())
  self.assertEqual(lock['package']['version'],'3.46.1-7+deb13u2+gold2')
  self.assertTrue(lock['armed']);self.assertEqual(A.VERSION,lock['package']['version'])
  self.assertEqual(lock['source']['recipe_sha256'],A.PINS['sourcebuild.py'][1])
  self.assertEqual(set(lock['outputs']),set(A.OUTPUTS));self.assertEqual(lock['artifact']['members'],9)
 def test_original_WAL_patch_and_ABI_verifier_stay_pinned(self):
  self.assertEqual(R.PATCH_PIN,(6746,'ab1d8c75b8ed521cc5d1d0c64da66be26ca3b72d53326c28b6a86473f42ddc85'))
  self.assertEqual(R.VERIFY_PIN,(16402, '53a1a5f9b2742910ef7bad0ccd5c5199d1b66fbeb83252e9e8dcc3d1583eec24'))
  self.assertEqual(R.SOURCE_FILES['sqlite3_3.46.1.orig.tar.xz'],(8456776,'d0cdd2ece271b29e7ce18095745d892517ee26d0f270065b3a25c2e9eb11639c'))
 def test_only_official_three_production_statements_in_FTS_patch(self):
  p=ROOT/'deploy/security/sqlite-wal-backport'/R.FTS_PATCH;raw=p.read_text()
  self.assertIn(R.UPSTREAM_FTS_COMMIT,raw);self.assertEqual(R.UPSTREAM_FTS_COMMIT,'e1c9934f9f11a499029abb020ea715a08970da80')
  changed=[l for l in raw.splitlines()if l.startswith(('+','-'))and not l.startswith(('+++','---'))]
  self.assertEqual(len(changed),6);self.assertIn('+  sqlite3Fts5BufferSize(&p->rc, &out, p1->n + p2->n + 9);',changed)
  self.assertTrue(all('fts5' in l.lower() or 'iRowid' in l for l in changed))
  self.assertNotIn('src/wal.c',raw);self.assertNotIn('SQLITE_OMIT',raw)
 def test_regression_keeps_exact_upstream_negative_docid_and_all_three_detail_modes(self):
  raw=(ROOT/'deploy/security/sqlite-wal-backport'/R.FTS_TEST_PATCH).read_text()
  self.assertIn('foreach_detail_mode $::testprefix-4',raw)
  self.assertIn("SELECT count(*) FROM t1('a*')",raw)
  self.assertIn('VALUES(-9223372036854775808,\'abc\')',raw)
  self.assertNotIn('+ifcapable',raw);self.assertNotIn('+return',raw)
 def test_default_CLI_stays_inert(self):
  for module in (R,A):
   out=io.StringIO()
   with redirect_stdout(out):self.assertEqual(module.main([]),0)
   self.assertFalse(json.loads(out.getvalue())['build_performed'])
 def test_standard_target_is_the_unfiltered_vendor_default(self):
  self.assertEqual(R.VENDOR_COMMANDS['vendor-standard.log'],['/usr/bin/make','test'])
  self.assertEqual(R.VENDOR_COMMANDS['vendor-fts5-regression.log'],['./testfixture','ext/fts5/test/fts5prefix2.test'])
  self.assertEqual(R.VENDOR_STANDARD_SCOPE,{'standard_target':'test',
   'standard_prerequisites':['srctree-check','fuzztest','sourcetest','$(TESTPROGS)','tcltest'],
   'executed_standard_subtargets':['srctree-check','fuzztest','sourcetest','tcltest'],
   'default_tcl_suite':'test/veryquick.test',
   'vendor_default_omissions_scope':'upstream_veryquick_excludes_malloc_and_io_error_simulations',
   'all_fault_permutations_run':False})
  for args in R.VENDOR_COMMANDS.values():
   self.assertNotIn('TESTOPTS',repr(args));self.assertNotIn('fulltest',args);self.assertNotIn('quicktest',args)
 def test_actual_build_body_calls_vendor_before_candidate_install_and_binds_result(self):
  tree=ast.parse((ROOT/'deploy/security/sqlite-wal-backport/sourcebuild.py').read_bytes())
  build=next(n for n in tree.body if isinstance(n,ast.FunctionDef)and n.name=='build')
  calls=[n for n in ast.walk(build)if isinstance(n,ast.Call)]
  vendor=[n for n in calls if isinstance(n.func,ast.Name)and n.func.id=='vendor_tests']
  self.assertEqual(len(vendor),1)
  install=[n for n in calls if isinstance(n.func,ast.Name)and n.func.id=='command'and n.args and isinstance(n.args[0],ast.List)and any(isinstance(x,ast.Constant)and x.value=='--install'for x in n.args[0].elts)]
  self.assertEqual(len(install),1);self.assertLess(vendor[0].lineno,install[0].lineno)
  report=next(n.value for n in ast.walk(build)if isinstance(n,ast.Assign)and any(isinstance(t,ast.Name)and t.id=='report'for t in n.targets))
  row=next(v for k,v in zip(report.keys,report.values)if isinstance(k,ast.Constant)and k.value=='vendor_tests')
  self.assertIsInstance(row,ast.Name);self.assertEqual(row.id,'vendor')
  fault=next(v for k,v in zip(report.keys,report.values)if isinstance(k,ast.Constant)and k.value=='upstream_fault_suites_run')
  self.assertIsInstance(fault,ast.Constant);self.assertIs(fault.value,False)
 def test_actual_vendor_boundary_same_source_no_flags_filter_and_bounded_tee(self):
  src=self.source_fixture();logs={'vendor-standard.log':standard(),'vendor-fts5-regression.log':regression()}
  privilege=F.fixture_vendor_privilege()
  with patch.object(R,'vendor_unprivileged_commands',return_value=(logs,privilege))as phase:
   facts,actual=R.vendor_tests(src,F.fixture_harness())
  launcher=(src/'libtool').read_bytes()
  phase.assert_called_once_with(src,{'bytes':len(launcher),'sha256':sha256(launcher).hexdigest()});self.assertEqual(actual,logs)
  self.assertEqual(facts['unprivileged_execution'],privilege)
  self.assertEqual(facts['standard_and_official_regression'],{n:R.vendor_log_contract(n,logs[n])for n in R.VENDOR_COMMANDS})
  self.assertTrue(facts['no_new_filters_or_exclusions']);self.assertFalse(facts['full_all_permutations_claimed'])
  self.assertEqual(facts['standard_scope'],R.VENDOR_STANDARD_SCOPE)
 def test_vendor_primary_nonzero_refuses_before_any_fake_log_acceptance(self):
  src=self.source_fixture()
  with patch.object(R,'vendor_unprivileged_commands',side_effect=RuntimeError('vendor_test_command_refused')),patch.object(R,'vendor_log_contract',side_effect=AssertionError('must refuse first')):
   with self.assertRaisesRegex(RuntimeError,'vendor_test_command_refused'):R.vendor_tests(src,F.fixture_harness())
 def test_real_public_child_stdout_is_captured_and_teed_before_nonzero_returns(self):
  emitted=[]
  # Actual owned Python/pipe transport, fixed public bytes only; no vendor binary.
  with patch.object(R.subprocess,'Popen',REAL_POPEN),patch.object(R,'forward',side_effect=lambda raw,end:emitted.append(raw)):
   code,raw=R.bounded_command(['/usr/bin/python3','-I','-B','-c',"import sys;sys.stdout.write('OWNED_PUBLIC_STREAM\\n');sys.stdout.flush();raise SystemExit(7)"],cwd=self.root,timeout=10,maximum=128,capture=True,with_code=True,tee=True)
  self.assertEqual(code,7);self.assertEqual(raw,b'OWNED_PUBLIC_STREAM\n');self.assertEqual(b''.join(emitted),raw)
 def test_real_public_child_overflow_refuses_before_capture_or_tee_accumulation(self):
  emitted=[]
  with patch.object(R.subprocess,'Popen',REAL_POPEN),patch.object(R,'forward',side_effect=lambda raw,end:emitted.append(raw)):
   with self.assertRaisesRegex(RuntimeError,'command_output_bound'):
    R.bounded_command(['/usr/bin/python3','-I','-B','-c',"import sys;sys.stdout.write('P'*129);sys.stdout.flush()"],cwd=self.root,timeout=10,maximum=128,capture=True,with_code=True,tee=True)
  self.assertEqual(emitted,[])
 def test_vendor_same_source_mutation_refuses(self):
  src=self.source_fixture()
  def phase(_,diagnostic_libtool):
   self.assertEqual(diagnostic_libtool,{'bytes':(src/'libtool').stat().st_size,'sha256':sha256((src/'libtool').read_bytes()).hexdigest()})
   (src/'Makefile').write_bytes((src/'Makefile').read_bytes()+b'\nOWNED MUTATION')
   return {'vendor-standard.log':standard(),'vendor-fts5-regression.log':regression()},F.fixture_vendor_privilege()
  with patch.object(R,'vendor_unprivileged_commands',side_effect=phase):
   with self.assertRaisesRegex(RuntimeError,'vendor_source_or_flags_changed'):R.vendor_tests(src,F.fixture_harness())
 def test_vendor_standard_summary_and_default_omissions_are_observations(self):
  row=R.vendor_log_contract('vendor-standard.log',standard()+b'Omitted test cases:\n.  capability_default compiled feature absent\n')
  self.assertEqual(row['zero_error_summaries'],[{'errors':0,'tests':15000}])
  self.assertEqual(len(row['vendor_default_omissions']),1);self.assertFalse(row['omissions_accepted']);self.assertFalse(row['security_accepted'])
 def test_vendor_error_empty_zero_count_or_known_errors_refuse(self):
  for raw in(b'',b'0 errors out of 0 tests\n',b'1 errors out of 99 tests\n',b'0 new errors and 1 known errors out of 99 tests\n',b'0 errors out of 99 tests\n!Failures on these tests: UNIT\n'):
   with self.subTest(raw=raw),self.assertRaises(RuntimeError):R.vendor_log_contract('vendor-standard.log',raw)
 def test_every_official_regression_marker_is_mandatory_and_once(self):
  raw=regression()
  for detail in('full','col','none'):
   for case in(1,2):
    marker=f'fts5prefix2-4-{detail}-{case}... Ok'.encode()
    with self.subTest(marker=marker),self.assertRaisesRegex(RuntimeError,'official_fts5_cases_missing'):R.vendor_log_contract('vendor-fts5-regression.log',raw.replace(marker,b'OMITTED',1))
  with self.assertRaisesRegex(RuntimeError,'official_fts5_cases_missing'):R.vendor_log_contract('vendor-fts5-regression.log',raw+b'fts5prefix2-4-none-2... Ok\n')
 def test_authentic_vendor_hyphen_wire_refuses_old_mock_dot_wire(self):
  # SQLite3.46.1 foreach_detail_mode sets prefix-DETAIL; fix_testname appends -CASE.
  markers=['fts5prefix2-4-full-1... Ok','fts5prefix2-4-full-2... Ok',
   'fts5prefix2-4-col-1... Ok','fts5prefix2-4-col-2... Ok',
   'fts5prefix2-4-none-1... Ok','fts5prefix2-4-none-2... Ok']
  body=('\n'.join(markers)+'\n0 errors out of 40 tests on OWNED UNIT ONLY\n').encode()
  self.assertEqual(R.vendor_log_contract('vendor-fts5-regression.log',body)['official_regression_markers'],markers)
  with self.assertRaisesRegex(RuntimeError,'official_fts5_cases_missing'):
   R.vendor_log_contract('vendor-fts5-regression.log',body.replace(b'full-1...',b'full.1...'))
 def test_nine_artifact_fixture_runs_real_consumer_and_logs(self):
  rows,proofs=F.fixtures();self.assertEqual(len(rows),9);verdict=A.verify_artifacts(R,rows,proofs);self.assertTrue(verdict['native_abi_checked']);self.assertFalse(verdict['security_accepted'])
 def test_missing_truncated_swapped_and_forged_vendor_logs_refuse(self):
  for change in('missing','swapped','truncated','forged'):
   rows,proofs=F.fixtures()
   if change=='missing':del rows['vendor-standard.log']
   elif change=='swapped':rows['vendor-standard.log'],rows['vendor-fts5-regression.log']=rows['vendor-fts5-regression.log'],rows['vendor-standard.log']
   elif change=='truncated':rows['vendor-fts5-regression.log']=b'0 errors out of 40 tests on OWNED UNIT ONLY\n'
   else:rows['provenance.json']['vendor_tests']['standard_and_official_regression']['vendor-standard.log']['actual_returncode']=1
   self.reject(rows,proofs)
 def test_any_vendor_waiver_filter_or_false_fullsuite_claim_refuses(self):
  for key,value in(('no_new_filters_or_exclusions',False),('full_all_permutations_claimed',True),('default_omissions_are_not_waivers',False)):
   rows,proofs=F.fixtures();rows['provenance.json']['vendor_tests'][key]=value;self.reject(rows,proofs)
  rows,proofs=F.fixtures();rows['provenance.json']['upstream_fault_suites_run']=True;self.reject(rows,proofs)
 def test_truthful_standard_default_scope_accepts_without_broad_fault_claim_or_omission_waiver(self):
  # Synthetic artifact bytes exercise the real consumer, not vendor/package acceptance.
  rows,proofs=F.fixtures();raw=rows['vendor-standard.log']+b'Omitted test cases:\n.  malloc_io_default_omission OWNED UNIT ONLY\n'
  rows['vendor-standard.log']=raw;proofs['vendor-standard.log']=F.proof(raw)
  p=rows['provenance.json'];p['vendor_tests']['standard_and_official_regression']['vendor-standard.log']=R.vendor_log_contract('vendor-standard.log',raw)
  proofs['provenance.json']=F.proof(A.encoded(p))
  self.assertIs(p['upstream_fault_suites_run'],False)
  self.assertIs(p['vendor_tests']['standard_scope']['all_fault_permutations_run'],False)
  verdict=A.verify_artifacts(R,rows,proofs)
  self.assertTrue(verdict['native_abi_checked']);self.assertFalse(verdict['security_accepted'])
  observed=p['vendor_tests']['standard_and_official_regression']['vendor-standard.log']
  self.assertEqual(observed['vendor_default_omissions'],['.  malloc_io_default_omission OWNED UNIT ONLY'])
  self.assertFalse(observed['omissions_accepted']);self.assertFalse(observed['security_accepted'])
 def test_missing_standard_scope_altered_defaults_new_custom_skip_or_fault_overclaim_refuse(self):
  for action in('missing_scope','missing_target','different_target','missing_subtargets','different_tcl_suite','fault_overclaim','numeric_false','custom_skip'):
   rows,proofs=F.fixtures();v=rows['provenance.json']['vendor_tests'];scope=v['standard_scope']
   if action=='missing_scope':del v['standard_scope']
   elif action=='missing_target':del scope['standard_target']
   elif action=='different_target':scope['standard_target']='quicktest'
   elif action=='missing_subtargets':scope['executed_standard_subtargets']=[]
   elif action=='different_tcl_suite':scope['default_tcl_suite']='test/quick.test'
   elif action=='fault_overclaim':scope['all_fault_permutations_run']=True
   elif action=='numeric_false':scope['all_fault_permutations_run']=0
   else:scope['custom_skips']=['fts5']
   with self.subTest(action=action):self.reject(rows,proofs)
 def test_truthful_standard_scope_cannot_mask_false_ABI_claim(self):
  rows,proofs=F.fixtures();rows['provenance.json']['elf_soname_exports_dependencies_equal']=False
  self.reject(rows,proofs)
 def test_FTS_source_patch_commit_and_version_drift_refuse(self):
  for key,value in(('fts5_patch',{'bytes':1240,'sha256':'0'*64}),('fts5_source_after',{'bytes':283845,'sha256':'0'*64}),('official_fts5_commit','0'*40),('local_package_version',A.BASE+'+gold1')):
   rows,proofs=F.fixtures();rows['provenance.json'][key]=value;self.reject(rows,proofs)
 def test_same_configured_source_or_flags_drift_refuses(self):
  for key in('debian/rules','src/wal.c','ext/fts5/fts5_index.c','ext/fts5/test/fts5prefix2.test'):
   rows,proofs=F.fixtures();rows['provenance.json']['vendor_tests']['same_configured_source'][key]['sha256']='0'*64;self.reject(rows,proofs)
 def test_raw_upstream_Makefile_receipt_cannot_masquerade_as_post_Debian(self):
  rows,proofs=F.fixtures()
  rows['provenance.json']['vendor_tests']['same_configured_source']['Makefile.in']={'bytes':52641,'sha256':'78ebd7e89c5c95d9a8e30c39a5da99155e8d0d8cc901e36e74c4e6b20ddb0074'}
  self.reject(rows,proofs)

class DebianVendorStageTests(unittest.TestCase):
 """Real public GNU-patch producer to unchanged production digest consumer.

 These controls reconstruct only authenticated source bytes. They never run
 make, compile, install, use Docker/network, or claim vendor/ABI qualification.
 """
 @classmethod
 def setUpClass(cls):
  cls.fixture=json.loads((ROOT/'tests/fixtures/sqlite_vendor_debian_stage.json').read_bytes())
  cls.raw=cls.decode(cls.fixture['upstream_Makefile'])
  cls.patches=[(row['name'],cls.decode(row))for row in cls.fixture['ordered_Debian_patches']]
 @staticmethod
 def decode(row):
  raw=base64.b64decode(row['base64'],validate=True)
  if (len(raw),sha256(raw).hexdigest())!=(row['bytes'],row['sha256']):raise AssertionError('authenticated fixture drift')
  return raw
 def setUp(self):
  self.scratch=tempfile.TemporaryDirectory(prefix='.owned-Debian-stage-',dir=ROOT/'tests');self.addCleanup(self.scratch.cleanup);self.root=Path(self.scratch.name)
 def derived(self,tag,order=None):
  root=self.root/tag;root.mkdir(mode=0o700);target=root/'Makefile.in';target.write_bytes(self.raw);target.chmod(0o600)
  for ordinal,(name,raw)in enumerate(self.patches if order is None else order):
   patchfile=root/('input-'+str(ordinal)+'.patch');patchfile.write_bytes(raw);patchfile.chmod(0o600)
   # Same quilt path strip and zero-fuzz policy, using exact signed-Debian files.
   # Output is bounded by the existing production finite-command transport.
   code,output=R.bounded_command(['/usr/bin/patch','--batch','--forward','--fuzz=0','--no-backup-if-mismatch','--reject-file=-','-p1','--input',str(patchfile)],cwd=root,timeout=10,maximum=16384,capture=True,with_code=True)
   if code:return target,code
  return target,0
 @staticmethod
 def stanza(raw,prefix):
  lines=raw.splitlines(keepends=True);starts=[i for i,line in enumerate(lines)if line.startswith(prefix)]
  if len(starts)!=1:raise AssertionError('vendor target ambiguous')
  index=starts[0];end=next((i for i in range(index+1,len(lines))if not lines[i].strip()),len(lines))
  return b''.join(lines[index:end])
 def test_fixture_joins_exact_source_chain_and_changed_stage_pin(self):
  self.assertEqual(self.fixture['schema'],'sqlite_public_Debian_Makefile_stage_fixture/1')
  self.assertEqual({name:tuple(pin)for name,pin in self.fixture['source_archives'].items()},R.SOURCE_FILES)
  self.assertEqual((len(self.raw),sha256(self.raw).hexdigest()),(52641,'78ebd7e89c5c95d9a8e30c39a5da99155e8d0d8cc901e36e74c4e6b20ddb0074'))
  self.assertEqual([name for name,_ in self.patches],['10-520478-squash-bad-deps.patch','30-cross.patch','32-dynamic_link.patch'])
  self.assertEqual(R.VENDOR_HELPERS['Makefile.in'],(53067,'28c0cd79217d77c89c0ca3ccd39ed3937285f006384f36737de26a1db4bdc7b7'))
  self.assertEqual(self.fixture['actual_dpkg_source_post_Debian_Makefile'],{'bytes':53067,'sha256':'28c0cd79217d77c89c0ca3ccd39ed3937285f006384f36737de26a1db4bdc7b7'})
 def test_real_Debian_producer_passes_consumer_and_old_raw_pin_fails(self):
  path,code=self.derived('correct');self.assertEqual(code,0)
  self.assertEqual(R.digest(path,R.VENDOR_HELPERS['Makefile.in']),{'bytes':53067,'sha256':'28c0cd79217d77c89c0ca3ccd39ed3937285f006384f36737de26a1db4bdc7b7'})
  with self.assertRaisesRegex(RuntimeError,'input_pin'):R.digest(path,(52641,'78ebd7e89c5c95d9a8e30c39a5da99155e8d0d8cc901e36e74c4e6b20ddb0074'))
 def test_raw_upstream_is_refused_at_real_post_Debian_digest_boundary(self):
  path=self.root/'Makefile.in';path.write_bytes(self.raw);path.chmod(0o600)
  with self.assertRaisesRegex(RuntimeError,'input_pin'):R.digest(path,R.VENDOR_HELPERS['Makefile.in'])
 def test_missing_or_reordered_Debian_patch_stage_cannot_pass(self):
  orders=[self.patches[:i]+self.patches[i+1:]for i in range(3)]+[list(reversed(self.patches))]
  for i,order in enumerate(orders):
   with self.subTest(order=[name for name,_ in order]):
    path,code=self.derived('missing-or-order-'+str(i),order)
    with self.assertRaisesRegex(RuntimeError,'input_pin'):R.digest(path,R.VENDOR_HELPERS['Makefile.in'])
 def test_Debian_stage_preserves_exact_unfiltered_standard_test_stanzas(self):
  path,code=self.derived('targets');self.assertEqual(code,0);post=path.read_bytes()
  for prefix in(b'test:\t',b'tcltest:\t',b'fuzztest:\t',b'sourcetest:\t',b'srctree-check:\t',b'testfixture$(TEXE):\t',b'TESTPROGS =',b'TESTOPTS ='):
   with self.subTest(prefix=prefix):self.assertEqual(self.stanza(self.raw,prefix),self.stanza(post,prefix))
  self.assertIn(b'test:\tsrctree-check fuzztest sourcetest $(TESTPROGS) tcltest',post)
  self.assertIn(b'./testfixture$(TEXE) $(TOP)/test/veryquick.test $(TESTOPTS)',post)
 def test_changed_target_filter_or_even_comment_cannot_pass_consumer(self):
  path,code=self.derived('mutations');self.assertEqual(code,0);post=path.read_bytes()
  changed=[post.replace(b'test:\tsrctree-check',b'test:\t# omitted srctree-check',1),post.replace(b'TESTOPTS =',b'TESTOPTS = --match=fts5',1),post+b'\n# unadmitted source mutation\n']
  for i,raw in enumerate(changed):
   with self.subTest(mutation=i):
    self.assertNotEqual(raw,post);path.write_bytes(raw)
    with self.assertRaisesRegex(RuntimeError,'input_pin'):R.digest(path,R.VENDOR_HELPERS['Makefile.in'])
 def test_fixture_byte_mutation_refuses_before_patch_execution(self):
  row=deepcopy(self.fixture['ordered_Debian_patches'][0]);raw=base64.b64decode(row['base64']);row['base64']=base64.b64encode(raw+b'\n# drift\n').decode()
  with self.assertRaisesRegex(AssertionError,'authenticated fixture drift'):self.decode(row)

if __name__=='__main__':unittest.main()
