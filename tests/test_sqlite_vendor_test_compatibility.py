"""Owned public source/oracle/failed-command fixtures; no compile, SQL or GDB."""
from contextlib import ExitStack,redirect_stdout
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from types import ModuleType
from unittest.mock import patch
import ast,base64,io,json,os,tempfile,unittest
import test_prepare_sqlite_wal_ci_context as F

ROOT=Path(__file__).absolute().parent.parent
FIX=json.loads((ROOT/'tests/fixtures/sqlite_vendor_test_compatibility.json').read_bytes())
def load(path):
 m=ModuleType('_owned_compat_'+path.parent.name);m.__file__=str(path)
 exec(compile(path.read_bytes(),str(path),'exec'),m.__dict__);return m
RECIPES={'Debian':F.R,**{n:load(ROOT/'deploy/security/sqlite-ubuntu-gold2'/n.lower()/'sourcebuild.py')for n in('Noble','Resolute')}}
def pin(raw):return(len(raw),sha256(raw).hexdigest())

class CompatibilityTests(unittest.TestCase):
 def setUp(self):
  self.stack=ExitStack();self.addCleanup(self.stack.close)
  self.root=Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='.owned-vendor-compat-',dir=ROOT/'tests')))
  for name in('subprocess.Popen','subprocess.run','socket.socket','urllib.request.urlopen','os.system','os.popen'):
   self.stack.enter_context(patch(name,side_effect=AssertionError('no ambient operation')))

 def source(self,r,mode=0o644):
  src=self.root/r.BASE_VERSION;src.mkdir()
  spec=deepcopy(r.VENDOR_TEST_COMPAT)
  rows={'test/io.test':base64.b64decode(FIX['io_test']),
   'src/sqliteLimit.h':base64.b64decode(FIX['limit_header']).replace(b'# define SQLITE_MAX_DEFAULT_PAGE_SIZE 8192',b'# define SQLITE_MAX_DEFAULT_PAGE_SIZE 32768'),
   'src/pager.c':b'/* OWNED SYNTHETIC FULL PAGER WITH AUTHENTIC CLAMP ONLY */\n'+base64.b64decode(FIX['pager_clamp']),
   spec['distro_patch']:base64.b64decode(FIX['distro31'])}
  spec['production']['src/pager.c']=pin(rows['src/pager.c'])
  if spec['fuzzcheck']is not None:
   old=b'/* OWNED SYNTHETIC FUZZCHECK DECLARATIONS ONLY */\n'+b''.join(('extern int '+n+'(sqlite3*,char***,void*);\n').encode()for n in('sqlite3_vt02_init','sqlite3_randomjson_init'))
   new=old
   for n in('sqlite3_vt02_init','sqlite3_randomjson_init'):
    new=new.replace(('extern int '+n+'(sqlite3*,char***,void*);').encode(),('extern int '+n+'(sqlite3*,char**,const sqlite3_api_routines*);').encode())
   rows['test/fuzzcheck.c']=old;spec['fuzzcheck']['before']=pin(old);spec['fuzzcheck']['after']=pin(new)
   for path,n in(('test/vt02.c','sqlite3_vt02_init'),('ext/misc/randomjson.c','sqlite3_randomjson_init')):
    rows[path]=('/* OWNED SYNTHETIC IMPLEMENTATION INTERFACE ONLY */\nint '+n+'(sqlite3 *db, char **pzErrMsg, const sqlite3_api_routines *pApi){return 0;}\n').encode()
    spec['fuzzcheck']['implementations'][path]=pin(rows[path])
  for name,raw in rows.items():
   p=src/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(raw);p.chmod(mode)
  self.stack.enter_context(patch.object(r,'VENDOR_TEST_COMPAT',spec))
  return src,rows

 def test_authenticated_matrix_and_unchanged_clamp_require_only_fifth_nonatomic_change(self):
  raw=base64.b64decode(FIX['io_test']);header=base64.b64decode(FIX['limit_header'])
  self.assertEqual(pin(raw),(18788,'941b60a0ca67ee9ace46b73deecb31998c5fca9944e1405637257ef4503e7f35'))
  self.assertEqual(pin(header),(6698,'146e9167129425c4dbbb45573404f5f4766f9325524f1d046881903cdc3f867c'))
  clamp=base64.b64decode(FIX['pager_clamp'])
  self.assertIn(b'if( pPager->sectorSize>SQLITE_MAX_DEFAULT_PAGE_SIZE )',clamp)
  self.assertIn(b'szPageDflt = SQLITE_MAX_DEFAULT_PAGE_SIZE;',clamp)
  self.assertIn(b'szPageDflt = (u32)pPager->sectorSize;',clamp)
  # Independent explicit semantic oracle for the exact source clamp. These
  # values are not derived from the replacement or producer receipt.
  sectors=[512,1024,2048,8192,16384]
  old=[max(1024,min(s,8192))for s in sectors]
  distro=[max(1024,min(s,32768))for s in sectors]
  self.assertEqual(old,[1024,1024,2048,8192,8192])
  self.assertEqual(distro,[1024,1024,2048,8192,16384])
  self.assertEqual([i for i,pair in enumerate(zip(old,distro),1)if pair[0]!=pair[1]],[5])
  self.assertEqual(raw.count(F.R.IO_ORACLE_BEFORE),1)
  after=raw.replace(F.R.IO_ORACLE_BEFORE,F.R.IO_ORACLE_AFTER)
  self.assertEqual(pin(after),(18788,'b7ae8d3def6ab27074b21cf51df2e50dd50d34eb00ca671f32882f7033ce559e'))
  self.assertEqual(raw.count(b'do_test '),after.count(b'do_test '))

 def test_real_owned_producer_preserves_production_bytes_modes_and_inodes_all_profiles(self):
  for name,r in RECIPES.items():
   with self.subTest(profile=name):
    src,before=self.source(r,0o600 if name=='Noble'else 0o644)
    frames={n:(src/n).stat()for n in before}
    receipt=r.prepare_vendor_test_compatibility(src)
    self.assertEqual(receipt,r.vendor_test_compatibility_expected())
    self.assertIs(r.vendor_test_compatibility_contract(receipt),receipt)
    self.assertFalse(receipt['official_security_or_production_fix']);self.assertFalse(receipt['Ubuntu_crashes_resolved'])
    for n,raw in before.items():
     actual=(src/n).read_bytes();f=(src/n).stat()
     self.assertEqual((f.st_dev,f.st_ino,f.st_mode,f.st_uid,f.st_gid),(frames[n].st_dev,frames[n].st_ino,frames[n].st_mode,frames[n].st_uid,frames[n].st_gid))
     if n=='test/io.test':self.assertEqual(actual,raw.replace(r.IO_ORACLE_BEFORE,r.IO_ORACLE_AFTER))
     elif n=='test/fuzzcheck.c':
      expected=raw
      for symbol in('sqlite3_vt02_init','sqlite3_randomjson_init'):
       expected=expected.replace(('extern int '+symbol+'(sqlite3*,char***,void*);').encode(),('extern int '+symbol+'(sqlite3*,char**,const sqlite3_api_routines*);').encode())
      self.assertEqual(actual,expected)
     else:self.assertEqual(actual,raw)
    self.assertIs(r.verify_vendor_test_compatibility(src,receipt),receipt)
    with self.assertRaises(RuntimeError):r.prepare_vendor_test_compatibility(src)

 def test_each_authenticated_operand_refuses_before_any_test_rewrite(self):
  r=RECIPES['Noble']
  for i,name in enumerate(['src/pager.c','src/sqliteLimit.h',r.VENDOR_TEST_COMPAT['distro_patch'],'test/io.test','test/fuzzcheck.c','test/vt02.c','ext/misc/randomjson.c']):
   with self.subTest(path=name):
    with tempfile.TemporaryDirectory(prefix='operand-',dir=self.root)as directory:
     original=self.root;self.root=Path(directory)
     try:src,rows=self.source(r)
     finally:self.root=original
     path=src/name;path.write_bytes(rows[name]+b'\nOWNED MUTANT\n')
     with self.assertRaises(RuntimeError):r.prepare_vendor_test_compatibility(src)
     for leaf,raw in rows.items():self.assertEqual((src/leaf).read_bytes(),raw+(b'\nOWNED MUTANT\n'if leaf==name else b''))

 def test_post_vendor_guard_rejects_production_or_modified_test_drift(self):
  r=F.R;src,rows=self.source(r);receipt=r.prepare_vendor_test_compatibility(src)
  for name in ['src/pager.c','src/sqliteLimit.h',r.VENDOR_TEST_COMPAT['distro_patch'],'test/io.test']:
   path=src/name;original=path.read_bytes();path.write_bytes(original+b'\nMUTANT\n')
   with self.assertRaises(RuntimeError):r.verify_vendor_test_compatibility(src,receipt)
   path.write_bytes(original);self.assertEqual(r.verify_vendor_test_compatibility(src,receipt),receipt)

 def test_actual_producer_receipt_reaches_strict_artifact_consumer_and_mutants_refuse(self):
  r=F.R;src,_=self.source(r);receipt=r.prepare_vendor_test_compatibility(src)
  # The surrounding package/ABI/log rows stay explicitly synthetic fixtures.
  rows,proofs=F.fixtures();rows['provenance.json']['test_only_compatibility']=receipt
  self.assertTrue(F.A.verify_artifacts(r,rows,proofs)['native_abi_checked'])
  for key,value in [('production_flags_changed',True),('test_selection_changed',True),('security_accepted',True),('Ubuntu_crashes_resolved',True),('phase','BEFORE_BUILD')]:
   changed=deepcopy(rows);changed['provenance.json']['test_only_compatibility'][key]=value
   changed_proofs=deepcopy(proofs);changed_proofs['provenance.json']=F.proof(F.A.encoded(changed['provenance.json']))
   with self.assertRaisesRegex(RuntimeError,'vendor_test_compatibility_contract'):F.A.verify_artifacts(r,changed,changed_proofs)
  changed=deepcopy(rows);del changed['provenance.json']['test_only_compatibility']
  with self.assertRaises(RuntimeError):F.A.verify_artifacts(r,changed,proofs)

 def test_short_writes_and_body_plus_physical_close_failure_keep_truth(self):
  r=F.R;src,rows=self.source(r);path=src/'test/io.test';write=os.pwrite;calls=[]
  def short(fd,raw,offset):calls.append(offset);return write(fd,raw[:1000],offset)
  with patch.object(r.os,'pwrite',side_effect=short):r.prepare_vendor_test_compatibility(src)
  self.assertGreater(len(calls),1)
  path.write_bytes(rows['test/io.test']);closed=[];close=r.vendor_close_fd
  class BodyFailure(RuntimeError):pass
  class CloseFailure(RuntimeError):pass
  def close_then_raise(fd):closed.append(fd);close(fd);raise CloseFailure('owned-close-error')
  with patch.object(r.os,'pwrite',side_effect=BodyFailure('owned-body-error')),patch.object(r,'vendor_close_fd',side_effect=close_then_raise):
   with self.assertRaises(BaseExceptionGroup)as caught:r.prepare_vendor_test_compatibility(src)
  self.assertEqual([type(e)for e in caught.exception.exceptions],[BodyFailure,CloseFailure]);self.assertEqual(len(closed),1)
  with self.assertRaises(OSError):os.fstat(closed[0])

 def test_build_order_standard_selection_and_production_flags_remain_strict(self):
  for name,r in RECIPES.items():
   tree=ast.parse(Path(r.__file__).read_bytes());build=next(n for n in tree.body if isinstance(n,ast.FunctionDef)and n.name=='build')
   calls=[n for n in ast.walk(build)if isinstance(n,ast.Call)and isinstance(n.func,ast.Name)]
   where=lambda fn:next(n.lineno for n in calls if n.func.id==fn)
   package=next(n.lineno for n in calls if n.func.id=='command'and n.args and isinstance(n.args[0],ast.List)and any(isinstance(x,ast.Constant)and x.value=='/usr/bin/dpkg-buildpackage'for x in n.args[0].elts))
   install=next(n.lineno for n in calls if n.func.id=='command'and n.args and isinstance(n.args[0],ast.List)and any(isinstance(x,ast.Constant)and x.value=='--install'for x in n.args[0].elts))
   self.assertLess(package,where('prepare_vendor_test_compatibility'));self.assertLess(where('prepare_vendor_test_compatibility'),where('vendor_tests'))
   self.assertLess(where('vendor_tests'),where('verify_vendor_test_compatibility'));self.assertLess(where('verify_vendor_test_compatibility'),install)
   self.assertEqual(r.VENDOR_COMMANDS,{'vendor-standard.log':['/usr/bin/make','test'],'vendor-fts5-regression.log':['./testfixture','ext/fts5/test/fts5prefix2.test']})
   self.assertEqual(r.VENDOR_PHASE_SECONDS,1560);self.assertEqual(r.VENDOR_RESTORE_SECONDS,60)

 def test_noble_exact_complete_prototypes_match_authenticated_implementations(self):
  spec=RECIPES['Noble'].VENDOR_TEST_COMPAT['fuzzcheck']
  self.assertEqual(spec['before'],(81518,'c8fa9f37b70d07cb4445c0910f079da6ce497f4095eeacec9d6e512fcd930704'))
  for name,pinvalue in spec['implementations'].items():
   actual=FIX['actual_member_pins']['3.45.1/'+name];self.assertEqual(pinvalue,(actual['bytes'],actual['sha256']))
  self.assertIsNone(RECIPES['Debian'].VENDOR_TEST_COMPAT['fuzzcheck']);self.assertIsNone(RECIPES['Resolute'].VENDOR_TEST_COMPAT['fuzzcheck'])

class FailureDiagnosticTests(unittest.TestCase):
 def run_diagnostic(self,raw,*,remaining=200,result=(0,b'OWNED PUBLIC BACKTRACE ONLY\n'),available=True,retire_error=None):
  r=RECIPES['Noble'];calls=[];retired=[];output=io.StringIO();scope={'retire_end':remaining}
  owned=ExitStack();self.addCleanup(owned.close);src=Path('/OWNED/source')
  if b'tcltest] Segmentation fault'in raw:
   src=Path(owned.enter_context(tempfile.TemporaryDirectory(prefix='.owned-libtool-diagnostic-',dir=ROOT/'tests')))
   for name in('libtool','testfixture'):
    path=src/name;path.write_bytes(b'#!/bin/sh\n# OWNED SYNTHETIC LAUNCHER; NEVER EXECUTED\n');path.chmod(0o755);os.chown(path,10001,10001)
   scope['diagnostic_libtool']={'bytes':(src/'libtool').stat().st_size,'sha256':sha256((src/'libtool').read_bytes()).hexdigest()}
  def command(argv,**kw):
   calls.append((argv,kw))
   if isinstance(result,BaseException):raise result
   return result
  def retire(s,label,argv):
   retired.append((s,label,argv))
   if retire_error:raise retire_error
   return {'owned_unit_fixture_only':True}
  with patch.object(r.time,'monotonic',return_value=100),patch.object(Path,'is_file',return_value=available),patch.object(r,'bounded_command',side_effect=command),patch.object(r,'vendor_process_retire',side_effect=retire),redirect_stdout(output):
   try:r.vendor_failure_diagnostic(src,raw,scope)
   except BaseException as e:error=e
   else:error=None
  return calls,retired,[json.loads(line)for line in output.getvalue().splitlines()],error

 def test_known_fuzz_case_uses_own_binary_exact_parser_and_bounded_original_clock(self):
  raw=b'./fuzzcheck /OWNED/source/test/fuzzdata1.db (sqlid=874,dbid=1): segfault\n'
  calls,retired,rows,error=self.run_diagnostic(raw);self.assertIsNone(error);self.assertEqual(len(calls),1)
  argv,kw=calls[0]
  self.assertEqual(argv[-7:],['--args','./fuzzcheck','--dbid','1','--sqlid','874','/OWNED/source/test/fuzzdata1.db'])
  self.assertIn('set startup-with-shell off',argv);self.assertIn('set disable-randomization off',argv)
  self.assertIn('set auto-load off',argv);self.assertIn('set debuginfod enabled off',argv)
  self.assertEqual(kw,{'cwd':Path('/OWNED/source'),'timeout':60,'maximum':65536,'capture':True,'with_code':True,'tee':True,'unprivileged':True,'absolute_end':160})
  self.assertEqual(retired[0][1],'failure-diagnostic');self.assertEqual(rows[-1]['actual_returncode'],0)
  self.assertTrue(rows[-1]['original_make_failure_retained']);self.assertFalse(rows[-1]['security_accepted'])
  self.assertNotIn('inferior_success',rows[-1]);self.assertEqual(rows[-1]['status'],'RETURNED')

 def test_tcl_startup_trace_has_separate_diagnostic_owned_output(self):
  calls,_,rows,error=self.run_diagnostic(b'make: *** [Makefile:1364: tcltest] Segmentation fault (core dumped)\n',remaining=130)
  self.assertIsNone(error);argv,kw=calls[0]
  self.assertEqual(argv[:3],['./libtool','--mode=execute','/usr/bin/gdb'])
  self.assertEqual(argv[-4],'./testfixture');self.assertEqual(Path(argv[-3]).name,'veryquick.test')
  self.assertEqual(argv[-2:],['--verbose=file','--output=diagnostic-test-out.txt'])
  self.assertEqual(kw['timeout'],25);self.assertTrue(rows[-1]['completed_output_available'])

 def test_unavailable_expired_unrecognized_or_ambiguous_diagnostics_never_spawn(self):
  good=b'./fuzzcheck /OWNED/source/test/fuzzdata1.db (sqlid=874,dbid=1): segfault\n'
  for raw,options in [(good,{'available':False}),(good,{'remaining':105}),(b'unrecognized failed make',{}),(good+good,{}),(good.replace(b'/OWNED/source',b'/FOREIGN'),{})]:
   calls,retired,rows,error=self.run_diagnostic(raw,**options)
   self.assertFalse(calls);self.assertFalse(retired);self.assertIsNone(error)
   self.assertIn(rows[-1]['status'],('NOT_APPLICABLE','UNAVAILABLE_DEADLINE_OR_GDB'));self.assertTrue(rows[-1]['original_make_failure_retained'])

 def test_diagnostic_body_and_retirement_failure_preserve_both_and_no_completed_output(self):
  calls,retired,rows,error=self.run_diagnostic(b'make: *** [Makefile:1364: tcltest] Segmentation fault\n',result=TimeoutError('owned timeout'),retire_error=RuntimeError('owned retirement'))
  self.assertEqual(len(calls),1);self.assertEqual(len(retired),1);self.assertIsInstance(error,BaseExceptionGroup)
  self.assertEqual([type(e)for e in error.exceptions],[TimeoutError,RuntimeError])
  self.assertFalse(rows[-1]['completed_output_available']);self.assertIsNone(rows[-1]['actual_public_output'])
  self.assertTrue(rows[-1]['bounded_prefix_tee_to_CI_log']);self.assertTrue(rows[-1]['original_make_failure_retained'])

 def test_actual_failed_run_guard_retains_original_error_even_if_diagnostic_returns_zero(self):
  r=RECIPES['Noble'];tree=ast.parse(Path(r.__file__).read_bytes())
  phase=next(n for n in tree.body if isinstance(n,ast.FunctionDef)and n.name=='vendor_unprivileged_commands')
  run=next(n for n in ast.walk(phase)if isinstance(n,ast.FunctionDef)and n.name=='run')
  calls=[];scope={'fault':False};ns={'bounded_command':lambda *a,**k:(1,b'OWNED PUBLIC FAILED STANDARD OUTPUT'),
   'src':Path('/OWNED/source'),'timeout':lambda n:n,'vendor_process_retire':lambda *a:{'retired':[]},
   'scope':scope,'os':os,'vendor_failure_diagnostic':lambda *a:calls.append(a)}
  exec(compile(ast.fix_missing_locations(ast.Module(body=[run],type_ignores=[])),'<actual-frozen-nested-run>','exec'),ns)
  with self.assertRaisesRegex(RuntimeError,'vendor_test_command_refused'):ns['run']('vendor-standard.log',['/usr/bin/make','test'],1200,65536,True)
  self.assertEqual(len(calls),1)
  calls.clear();ns['vendor_process_retire']=lambda *a:{'retired':[{'signal':9,'wait':{'si_code':os.CLD_KILLED,'si_status':9}}]}
  with self.assertRaises(BaseExceptionGroup):ns['run']('vendor-standard.log',['/usr/bin/make','test'],1200,65536,True)
  self.assertEqual(calls,[])

class AbsoluteDiagnosticClockTests(unittest.TestCase):
 def actual_transport(self,absolute_end,*,spawn_at=110,wait_at=None):
  # Real owned EOF pipe, actual selected bounded_command and original close;
  # Popen is the sole process-effect seam, so no UID/GDB/SQL child is started.
  r=RECIPES['Noble'];clock=[100.0];spawns=[];waits=[];cleanup=[]
  rd,wr=os.pipe();os.close(wr)
  class Child:
   returncode=None;pid=999999999
   def __init__(self):self.stdout=os.fdopen(rd,'rb')
   def wait(self,timeout):
    waits.append(timeout);self.returncode=0
    if wait_at is not None:clock[0]=wait_at
    return 0
  child=Child()
  def spawn(argv,**kwargs):spawns.append((argv,kwargs));clock[0]=spawn_at;return child
  scope={'armed':True,'fault':False,'controller_pid':os.getpid()}
  with patch.object(r,'current_environment',return_value=dict(r.ENV)),patch.object(r.time,'monotonic',side_effect=lambda:clock[0]),patch.object(r,'VENDOR_PROCESS_SCOPE',scope),patch.object(r,'VENDOR_RETIREMENT_UNPROVEN',False),patch.object(r.subprocess,'Popen',side_effect=spawn),patch.object(r,'cleanup_owned',side_effect=lambda c:cleanup.append(c)):
   try:result=r.bounded_command(['OWNED_GDB_SEAM'],cwd=None,timeout=60,maximum=64,capture=True,with_code=True,unprivileged=absolute_end is not None,absolute_end=absolute_end)
   except BaseException as e:result=None;error=e
   else:error=None
  if not child.stdout.closed:child.stdout.close()
  return result,error,spawns,waits,cleanup,child.stdout.closed

 def test_absolute_end_clamps_actual_wait_while_legacy_none_keeps_original_relative_semantics(self):
  result,error,spawns,waits,cleanup,closed=self.actual_transport(125)
  self.assertEqual(result,(0,b''));self.assertIsNone(error);self.assertEqual(waits,[15.0]);self.assertEqual(cleanup,[]);self.assertTrue(closed)
  self.assertEqual(spawns[0][1]['user'],10001);self.assertEqual(spawns[0][1]['group'],10001)
  self.assertEqual(spawns[0][1]['extra_groups'],[]);self.assertTrue(spawns[0][1]['start_new_session'])
  result,error,_,waits,cleanup,closed=self.actual_transport(None)
  self.assertEqual(result,(0,b''));self.assertIsNone(error);self.assertEqual(waits,[60.0]);self.assertEqual(cleanup,[]);self.assertTrue(closed)

 def test_expired_before_or_after_spawn_and_after_wait_never_returns_success(self):
  for absolute,spawn_at,wait_at,expected_spawns in [(99,110,None,0),(125,126,None,1),(125,110,126,1)]:
   with self.subTest(absolute=absolute,spawn_at=spawn_at,wait_at=wait_at):
    result,error,spawns,_,cleanup,closed=self.actual_transport(absolute,spawn_at=spawn_at,wait_at=wait_at)
    self.assertIsNone(result);self.assertIsInstance(error,RuntimeError);self.assertEqual(str(error),'command_absolute_deadline')
    self.assertEqual(len(spawns),expected_spawns);self.assertEqual(len(cleanup),expected_spawns);self.assertTrue(closed)

 def test_nonfinite_non_numeric_and_bool_absolute_deadline_refuse_before_process(self):
  for bad in (True,False,float('inf'),float('-inf'),float('nan'),0,-1,'125'):
   with self.subTest(value=repr(bad)):
    result,error,spawns,waits,cleanup,closed=self.actual_transport(bad)
    self.assertIsNone(result);self.assertIsInstance(error,RuntimeError);self.assertEqual(str(error),'command_absolute_end_type')
    self.assertEqual((spawns,waits,cleanup),([],[],[]));self.assertTrue(closed)

if __name__=='__main__':unittest.main()
