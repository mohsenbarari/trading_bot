"""Real unittest collection modes on owned copies; never run imported test bodies.

This models the observed tests.package and discover modes without sys.path edits,
application/config import or any network/SQL/UID-child/Docker operation.
"""
from hashlib import sha256
from pathlib import Path
from types import ModuleType,SimpleNamespace
import errno,fcntl,json,os,stat,subprocess,tempfile,time,unittest

ROOT=Path(__file__).resolve().parents[1]
TARGETS=('test_market_ci_evidence_lifetime','test_npm_browser_ci_fixture_contract',
 'test_sqlite_testfixture_harness','test_sqlite_vendor_process_tree','test_sqlite_vendor_test_compatibility')
SUPPORT=('tests/__init__.py','tests/test_market_ci_controller_integration.py',
 'tests/test_npm_backend_collector_failure_diagnostics.py','tests/test_prepare_sqlite_wal_ci_context.py',
 'tests/test_sqlite_vendor_privileges.py','tests/market_security_source_fixtures.py',
 'tests/fixtures/sqlite_testfixture_harness.json','tests/fixtures/sqlite_vendor_test_compatibility.json',
 'scripts/prepare_market_capture2_image.py','scripts/prepare_market_product_source.py',
 'scripts/prepare_sqlite_wal_ci_context.py','scripts/prepare_sqlite_ubuntu_gold2_ci_context.py',
 'deploy/security/sqlite-wal-backport/sourcebuild.py','deploy/security/sqlite-ubuntu-gold2/noble/sourcebuild.py',
 'deploy/security/sqlite-ubuntu-gold2/resolute/sourcebuild.py')
PROGRAM=r'''
import json,os,sys,unittest
from pathlib import Path
root=Path.cwd();mode=sys.argv[1];names=json.loads(sys.argv[2]);want_missing=sys.argv[3]=='1'
loader=unittest.TestLoader();suites=[]
for name in names:
 if mode=='qualified':suite=loader.loadTestsFromName('tests.'+name)
 elif mode=='packaged_discover':suite=loader.discover('tests',pattern=name+'.py',top_level_dir='.')
 elif mode=='bare_discover':suite=loader.discover('tests',pattern=name+'.py')
 else:raise AssertionError('unknown owned collection mode')
 suites.append(suite)
def flatten(suite):
 for row in suite:
  if isinstance(row,unittest.TestSuite):yield from flatten(row)
  else:yield row
rows=[row for suite in suites for row in flatten(suite)]
failed=[row for row in rows if isinstance(row,unittest.loader._FailedTest)]
if want_missing:
 assert failed and loader.errors
 print(json.dumps({'mode':mode,'expected_missing_refused':True,'failed_collection_count':len(failed),'tests_executed':0}));raise SystemExit(0)
assert not failed and not loader.errors and len(rows)>0
prefix=''if mode=='bare_discover'else'tests.'
for name in names:
 module=sys.modules[prefix+name]
 assert Path(module.__file__).resolve()==root/'tests'/(name+'.py')
 assert any(row.id().startswith(prefix+name+'.')for row in rows)
 for alias in('F','P','C','fixtures'):
  fixture=getattr(module,alias,None)
  if fixture is not None and getattr(fixture,'__file__',None):
   p=Path(fixture.__file__).resolve();assert p.parent==root/'tests'
print(json.dumps({'mode':mode,'modules':len(names),'collected_test_cases':len(rows),'tests_executed':0,'package_siblings_bound_to_owned_tests':True}))
'''
CHILDREN=[];SOURCE_READERS=[]
def frame(s):return(s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
def public_source(relative):
 path=ROOT/relative;parents=[(p,frame(p.lstat())[:5])for p in path.parents]
 fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC)
 before=frame(os.fstat(fd));flags=(fcntl.fcntl(fd,fcntl.F_GETFL),fcntl.fcntl(fd,fcntl.F_GETFD))
 def guard():
  assert path.resolve(strict=True)==path and frame(path.lstat())==frame(os.fstat(fd))==before
  assert(fcntl.fcntl(fd,fcntl.F_GETFL),fcntl.fcntl(fd,fcntl.F_GETFD))==flags and os.lseek(fd,0,os.SEEK_CUR)==0
  assert all(p.resolve(strict=True)==p and frame(p.lstat())[:5]==v for p,v in parents)
 try:
  assert stat.S_ISREG(before[2])and before[5]==1 and not before[2]&0o022 and 0<before[6]<=1024**2
  guard();raw=os.pread(fd,before[6]+1,0);assert len(raw)==before[6];guard()
 finally:os.close(fd)
 try:os.fstat(fd);raise AssertionError('original Source still open')
 except OSError as error:assert error.errno==errno.EBADF
 assert frame(path.lstat())==before and all(p.resolve(strict=True)==p and frame(p.lstat())[:5]==v for p,v in parents)
 SOURCE_READERS.append({'relative':relative,'bytes':len(raw),'sha256':sha256(raw).hexdigest(),'original_full9_flags_offset_parents_closed_EBADF':True});return raw

class CIFixtureImportModes(unittest.TestCase):
 def project(self):
  temporary=tempfile.TemporaryDirectory(prefix='.owned-test-imports-',dir=ROOT/'tests');self.addCleanup(temporary.cleanup);root=Path(temporary.name)
  for relative in(*SUPPORT,*(f'tests/{name}.py'for name in TARGETS)):
   raw=public_source(relative);target=root/relative;target.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
   with target.open('xb')as stream:stream.write(raw)
   target.chmod(0o600)
  return root
 def collect(self,root,mode,*,missing=False):
  # Same ordinary CLI import semantics as the actual required CI commands;
  # clean environment/current owned directory, no PYTHONPATH/sys.path override.
  transport='deploy/maintenance/glibc_backport_native239/host_io_original.py'
  raw=public_source(transport);self.assertEqual((len(raw),sha256(raw).hexdigest()),(53351,'fc9fc8e4ae7fe1af75fd0b4c63fedb0ebfa5820148bb311c5eaafcaaa5bbbb82'))
  M=ModuleType('_owned_public_import_mode_transport');M.__file__=str(ROOT/transport);exec(compile(raw,M.__file__,'exec'),M.__dict__)
  children=[]
  def spawn(argv,**kwargs):
   child=subprocess.Popen(argv,cwd=root,**kwargs);children.append((child,child.stdout.fileno()));return child
  M.subprocess=SimpleNamespace(Popen=spawn,DEVNULL=subprocess.DEVNULL,PIPE=subprocess.PIPE,STDOUT=subprocess.STDOUT)
  stdout=M.run(['/usr/bin/python3','-B','-c',PROGRAM,mode,json.dumps(TARGETS),str(int(missing))],
   env={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8','LC_ALL':'C.UTF-8'},deadline=time.monotonic()+20,maximum=65536)
  self.assertEqual(len(children),1);child,fd=children[0]
  self.assertEqual(child.returncode,0);self.assertTrue(child.stdout.closed)
  with self.assertRaises(OSError)as closed:os.fstat(fd)
  self.assertEqual(closed.exception.errno,errno.EBADF)
  value=json.loads(stdout);self.assertEqual(value['tests_executed'],0)
  CHILDREN.append({'mode':mode,'actual_exit0_reaped_original_pipes_closed_EBADF':True,'expected_missing':missing});return value
 def test_exact_qualified_unittest_collection_resolves_real_package_siblings(self):
  value=self.collect(self.project(),'qualified');self.assertEqual(value['modules'],5);self.assertGreater(value['collected_test_cases'],0)
 def test_exact_coverage_discovery_top_level_root_resolves_real_package_siblings(self):
  self.collect(self.project(),'packaged_discover')
 def test_legacy_bare_directory_discovery_still_collects_real_test_modules(self):
  self.collect(self.project(),'bare_discover')
 def test_missing_sibling_remains_an_error_without_a_masking_import_fallback(self):
  root=self.project();(root/'tests/test_prepare_sqlite_wal_ci_context.py').unlink()
  for mode in('qualified','packaged_discover'):
   with self.subTest(mode=mode):self.assertTrue(self.collect(root,mode,missing=True)['expected_missing_refused'])
 def test_foreign_root_bare_helper_cannot_shadow_real_package_sibling(self):
  root=self.project();(root/'test_prepare_sqlite_wal_ci_context.py').write_text("raise AssertionError('OWNED FOREIGN ROOT HELPER MUST NOT LOAD')\n")
  for mode in('qualified','packaged_discover','bare_discover'):
   with self.subTest(mode=mode):self.collect(root,mode)

if __name__=='__main__':unittest.main()
