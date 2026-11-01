"""Owned source-binding/launcher controls; no SQL, compiler, GDB or UID child."""
from contextlib import ExitStack,redirect_stdout
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from types import ModuleType
from unittest.mock import patch
import ast,io,json,os,tempfile,unittest

ROOT=Path(__file__).resolve().parent.parent
PREFIX=ROOT/'deploy/security/sqlite-ubuntu-gold2/noble'
def load(path,name):
 m=ModuleType(name);m.__file__=str(path);exec(compile(path.read_bytes(),str(path),'exec'),m.__dict__);return m
R=load(PREFIX/'sourcebuild.py','owned_noble_resolver_recipe')
def pin(raw):return(len(raw),sha256(raw).hexdigest())

class CoreSourceTests(unittest.TestCase):
 def setUp(self):
  self.stack=ExitStack();self.addCleanup(self.stack.close)
  self.root=Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='.owned-rowid-source-',dir=ROOT/'tests')))
  self.recipe=self.root/'recipe';self.recipe.mkdir();self.src=self.root/'source';self.src.mkdir()
  self.stack.enter_context(patch.object(R,'RECIPE',self.recipe))
  spec=deepcopy(R.ROWID_NESTED_JOIN);raw=(PREFIX/spec['name']).read_bytes();(self.recipe/spec['name']).write_bytes(raw)
  self.rows={n:('/* OWNED SYNTHETIC '+n+' BINDING FIXTURE; NOT SQLITE EXECUTION */\n').encode()for n in spec['after']}
  for name,raw in self.rows.items():
   p=self.src/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(raw);p.chmod(0o644);spec['after'][name]=pin(raw)
  self.stack.enter_context(patch.object(R,'ROWID_NESTED_JOIN',spec))
  for name in('subprocess.Popen','subprocess.run','os.system','os.popen'):
   self.stack.enter_context(patch(name,side_effect=AssertionError('no process operation')))

 def test_actual_producer_requires_all_four_sources_and_exact_authentic_patch(self):
  expected=R.rowid_nested_join_expected();self.assertEqual(set(expected['after']),{'src/expr.c','src/resolve.c','src/select.c','src/sqliteInt.h'})
  self.assertEqual(expected['patch'],{'bytes':8073,'sha256':'fc7990f7375b8e8d63abc04a5c2fd66a2a6ed880e53342d37a370526e11ff018'})
  self.assertEqual(R.verify_rowid_nested_join_source(self.src),expected)
  self.assertFalse(expected['vendor_test_bodies_changed']);self.assertFalse(expected['actual_sql874_resolution_claimed_from_source_only'])
  self.assertFalse(expected['test_selection_changed']);self.assertFalse(expected['production_flags_changed'])
  self.assertFalse(expected['new_public_config_api']);self.assertFalse(expected['security_accepted'])
  for name,raw in self.rows.items():
   with self.subTest(source=name):
    p=self.src/name;p.write_bytes(raw+b'OWNED MUTANT\n')
    with self.assertRaisesRegex(RuntimeError,'input_pin'):R.verify_rowid_nested_join_source(self.src)
    p.write_bytes(raw);self.assertEqual(R.verify_rowid_nested_join_source(self.src),expected)
  p=self.recipe/R.ROWID_NESTED_JOIN['name'];old=p.read_bytes();p.write_bytes(old+b'\n')
  with self.assertRaisesRegex(RuntimeError,'input_pin'):R.verify_rowid_nested_join_source(self.src)
  p.write_bytes(old);self.assertEqual(R.verify_rowid_nested_join_source(self.src),expected)

 def test_contract_rejects_missing_foreign_or_inflated_claim_before_consumer_use(self):
  expected=R.verify_rowid_nested_join_source(self.src)
  for key in expected:
   with self.subTest(key=key):
    bad=deepcopy(expected);del bad[key]
    with self.assertRaisesRegex(RuntimeError,'rowid_nested_join_contract'):R.rowid_nested_join_contract(bad)
  for key in('security_accepted','test_selection_changed','production_flags_changed','vendor_test_bodies_changed','new_public_config_api','actual_sql874_resolution_claimed_from_source_only'):
   bad=deepcopy(expected);bad[key]=True
   with self.assertRaisesRegex(RuntimeError,'rowid_nested_join_contract'):R.rowid_nested_join_contract(bad)
  bad=deepcopy(expected);bad['after']['src/select.c']['sha256']='0'*64
  with self.assertRaisesRegex(RuntimeError,'rowid_nested_join_contract'):R.rowid_nested_join_contract(bad)
  self.assertIs(R.rowid_nested_join_contract(expected),expected)

 def test_build_orders_before_pins_quilt_after_pins_package_and_full_vendor_without_filter(self):
  tree=ast.parse(Path(R.__file__).read_bytes());f=next(n for n in tree.body if isinstance(n,ast.FunctionDef)and n.name=='build')
  calls=[(n.func.id,n.lineno)for n in ast.walk(f)if isinstance(n,ast.Call)and isinstance(n.func,ast.Name)]
  checks=[line for name,line in calls if name=='verify_rowid_nested_join_source']
  vendor=next(line for name,line in calls if name=='vendor_tests');compat=next(line for name,line in calls if name=='prepare_vendor_test_compatibility')
  self.assertEqual(len(checks),3);self.assertLess(checks[0],checks[1]);self.assertLess(checks[1],compat)
  self.assertLess(compat,vendor);self.assertGreater(checks[2],vendor)
  self.assertEqual(R.VENDOR_COMMANDS,{'vendor-standard.log':['/usr/bin/make','test'],'vendor-fts5-regression.log':['./testfixture','ext/fts5/test/fts5prefix2.test']})
  self.assertEqual(R.ENV['DEB_BUILD_OPTIONS'],'nocheck parallel=2')

class LauncherTests(unittest.TestCase):
 def diagnostic(self,r,*,mutate=None,result=(255,b'OWNED TRACE ONLY\n'),retire_error=None,bad_pin=False):
  with tempfile.TemporaryDirectory(prefix='.owned-configured-libtool-',dir=ROOT/'tests')as tmp:
   src=Path(tmp);before={};fds=[];calls=[];retired=[];output=io.StringIO()
   for name in('libtool','testfixture'):
    p=src/name;p.write_bytes(b'#!/bin/sh\n# OWNED TEST INPUT; NOT REAL LIBTOOL OR SQLITE\n');p.chmod(0o755);os.chown(p,10001,10001);before[name]=r.identity(p.stat())
   b=(src/'libtool').read_bytes();scope={'retire_end':200,'diagnostic_libtool':{'bytes':len(b),'sha256':sha256(b).hexdigest()}}
   if bad_pin:scope['diagnostic_libtool']['sha256']='0'*64
   actual_close=r.vendor_close_fd
   def close(fd):fds.append(fd);actual_close(fd)
   def command(argv,**kw):
    calls.append((argv,kw))
    if mutate:mutate(src)
    if isinstance(result,BaseException):raise result
    return result
   def retire(*a):
    retired.append(a)
    if retire_error:raise retire_error
    return {'owned_fixture_only':True}
   with patch.object(r.time,'monotonic',return_value=100),patch.object(Path,'is_file',return_value=True),patch.object(r,'bounded_command',side_effect=command),patch.object(r,'vendor_process_retire',side_effect=retire),patch.object(r,'vendor_close_fd',side_effect=close),redirect_stdout(output):
    try:r.vendor_failure_diagnostic(src,b'make: *** [Makefile:1: tcltest] Segmentation fault\n',scope)
    except BaseException as error:failure=error
    else:failure=None
   for fd in fds:
    with self.assertRaises(OSError)as caught:os.fstat(fd)
    self.assertEqual(caught.exception.errno,9)
   return calls,retired,[json.loads(line)for line in output.getvalue().splitlines()],failure,len(fds)

 def test_actual_three_recipe_producers_use_configured_libtool_and_same_original_clock(self):
  recipes=[R,load(ROOT/'deploy/security/sqlite-ubuntu-gold2/resolute/sourcebuild.py','owned_resolute'),load(ROOT/'deploy/security/sqlite-wal-backport/sourcebuild.py','owned_debian')]
  for r in recipes:
   with self.subTest(recipe=r.BASE_VERSION):
    calls,retired,rows,error,closed=self.diagnostic(r)
    self.assertIsNone(error);self.assertEqual(len(calls),1);argv,kw=calls[0]
    self.assertEqual(argv[:3],['./libtool','--mode=execute','/usr/bin/gdb'])
    self.assertEqual(argv[argv.index('--args')+1],'./testfixture');self.assertNotIn('.libs/testfixture',argv)
    self.assertEqual(kw['absolute_end'],160);self.assertEqual(kw['timeout'],60);self.assertTrue(kw['unprivileged'])
    self.assertNotIn('env',kw);self.assertEqual(retired[0][1],'failure-diagnostic');self.assertEqual(closed,2)
    self.assertEqual(rows[-1]['actual_returncode'],255);self.assertTrue(rows[-1]['original_make_failure_retained'])
    self.assertFalse(rows[-1]['security_accepted']);self.assertEqual(set(rows[-1]['configured_launcher_inputs']),{'libtool','testfixture'})

 def test_bad_original_launcher_pin_denies_before_process_and_closes_both_originals(self):
  calls,_,rows,error,closed=self.diagnostic(R,bad_pin=True)
  self.assertEqual(calls,[]);self.assertIsInstance(error,BaseExceptionGroup);self.assertEqual(closed,2)
  self.assertFalse(rows[-1]['completed_output_available']);self.assertIn('RuntimeError',rows[-1]['errors'])

 def test_same_byte_inode_swap_reached_after_call_retains_failure_and_closes_originals(self):
  def swap(src):
   p=src/'libtool';b=p.read_bytes();q=src/'replacement';q.write_bytes(b);q.chmod(0o755);os.chown(q,10001,10001);q.replace(p)
  calls,_,rows,error,closed=self.diagnostic(R,mutate=swap)
  self.assertEqual(len(calls),1);self.assertIsInstance(error,BaseExceptionGroup);self.assertEqual(closed,2)
  self.assertTrue(rows[-1]['completed_output_available']);self.assertIn('RuntimeError',rows[-1]['errors'])

 def test_real_body_and_retirement_errors_both_retained_after_original_input_close(self):
  calls,_,rows,error,closed=self.diagnostic(R,result=TimeoutError('owned deadline'),retire_error=RuntimeError('owned retirement'))
  self.assertEqual(len(calls),1);self.assertIsInstance(error,BaseExceptionGroup);self.assertEqual(closed,2)
  self.assertEqual([type(e)for e in error.exceptions],[TimeoutError,RuntimeError])
  self.assertFalse(rows[-1]['completed_output_available']);self.assertIsNone(rows[-1]['actual_public_output'])

if __name__=='__main__':unittest.main()
