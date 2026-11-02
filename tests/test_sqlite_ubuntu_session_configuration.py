"""Genuine GNU source application/configuration and strict public proof joins.

The small session fixture is an authentic context/function subset. It never
pretends to be a whole signed source or a completed package/vendor build.
"""
from contextlib import ExitStack
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from types import ModuleType,SimpleNamespace
from unittest.mock import patch
import ast,errno,json,os,selectors,subprocess,tempfile,time,unittest
ROOT=Path(__file__).resolve().parents[1];BASE='deploy/security/sqlite-ubuntu-gold2/'
CHILDREN=[]
def load(name,tag):
 p=ROOT/name;m=ModuleType('owned_'+tag);m.__file__=str(p);exec(compile(p.read_bytes(),str(p),'exec'),m.__dict__);return m
def pin(b):return{'bytes':len(b),'sha256':sha256(b).hexdigest()}
def actual(argv,cwd):
 p=None;fd=None;end=time.monotonic()+10;parts=[];count=0
 try:
  p=subprocess.Popen(argv,cwd=cwd,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
   env={'PATH':'/usr/bin:/bin','HOME':str(cwd),'LANG':'C','LC_ALL':'C'},close_fds=True,start_new_session=True)
  fd=p.stdout.fileno()
  with selectors.DefaultSelector()as s:
   os.set_blocking(fd,False);s.register(fd,selectors.EVENT_READ)
   while s.get_map():
    assert time.monotonic()<end,'owned GNU deadline'
    for event,_ in s.select(.1):
     raw=os.read(event.fd,min(8192,65537-count))
     if not raw:s.unregister(event.fd);continue
     count+=len(raw);assert count<=65536,'owned GNU output bound';parts.append(raw)
  return p.wait(timeout=max(.01,end-time.monotonic())),b''.join(parts)
 finally:
  if p is not None:
   if p.poll()is None:p.kill();p.wait(timeout=3)
   p.stdout.close()
   try:os.fstat(fd)
   except OSError as e:assert e.errno==errno.EBADF
   else:raise AssertionError('owned GNU pipe open')
   CHILDREN.append({'argv':argv,'returncode':p.returncode,'original_pipe_EBADF':True,'reaped':True})
class SessionConfigurationTests(unittest.TestCase):
 def setUp(self):
  self.stack=ExitStack();self.addCleanup(self.stack.close)
  self.root=Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='.owned-session-source-',dir=ROOT/'tests')))
  self.F=load('tests/test_sqlite_ubuntu_testfixture_fullcore.py','fullcore_fixture')
 def recipe(self,profile):return load(BASE+profile+'/sourcebuild.py','session_'+profile)
 def test_real_same_official_patch_applies_to_authenticated_function_subset_both_profiles(self):
  fixture=json.loads((ROOT/'tests/fixtures/sqlite_session_prerequisite.json').read_bytes());self.assertEqual(fixture['original_changeset_hex'],'540101740017000003ffffffff')
  self.assertEqual(len(bytes.fromhex(fixture['original_changeset_hex'])),13)
  patches=[]
  for profile in('noble','resolute'):
   r=self.recipe(profile);path=self.root/profile/'ext/session/sqlite3session.c';path.parent.mkdir(parents=True)
   path.write_bytes(fixture['before'].encode());source=path.read_bytes();patchpath=self.root/(profile+'.patch');patchpath.write_bytes(r.SESSION_INPUT_PATCH)
   self.assertEqual(pin(r.SESSION_INPUT_PATCH),r.SESSION_INPUT_PROOF['patch']);patches.append(r.SESSION_INPUT_PATCH)
   code,out=actual(['/usr/bin/patch','--batch','--forward','--fuzz=0','--no-backup-if-mismatch','--reject-file=-','-p1','--input',str(patchpath)],self.root/profile)
   self.assertEqual(code,0,out);self.assertEqual(path.read_bytes(),fixture['after'].encode())
   # The unchanged production full-source pin refuses this smaller fixture.
   with self.assertRaisesRegex(RuntimeError,'input_pin|input_size'):r.verify_session_input_source(self.root/profile)
   private=deepcopy(r.SESSION_INPUT_PROOF);private['before']=pin(source);private['after']=pin(path.read_bytes())
   with patch.object(r,'SESSION_INPUT_PROOF',private):self.assertEqual(r.verify_session_input_source(self.root/profile),private)
   raw=path.read_bytes();path.write_bytes(raw[:-1]+bytes([raw[-1]^1]))
   with patch.object(r,'SESSION_INPUT_PROOF',private),self.assertRaisesRegex(RuntimeError,'input_pin'):r.verify_session_input_source(self.root/profile)
  self.assertEqual(patches[0],patches[1]);self.assertEqual(len(patches[0]),1936)
 def test_production_proof_rejects_missing_extra_wrong_source_or_oracle_claims(self):
  for profile in('noble','resolute'):
   r=self.recipe(profile);self.assertIs(r.session_input_contract(r.SESSION_INPUT_PROOF),r.SESSION_INPUT_PROOF)
   for kind in('missing','extra','old-after','oracle','claim'):
    bad=deepcopy(r.SESSION_INPUT_PROOF)
    if kind=='missing':del bad['before']
    elif kind=='extra':bad['unknown']=False
    elif kind=='old-after':bad['after']=deepcopy(bad['before'])
    elif kind=='oracle':bad['official_expected']='SQLITE_NOMEM'
    else:bad['security_accepted']=True
    with self.subTest(profile=profile,kind=kind),self.assertRaisesRegex(RuntimeError,'^session_input_contract$'):r.session_input_contract(bad)
 def test_real_noble_gnu_target_uses_one_test_cli_core_dbpage_and_last_rowid_undef(self):
  r=self.recipe('noble');before=self.F.authentic('noble');after=r.testfixture_after(before)
  self.assertEqual(after.count(b'TESTFIXTURE_FLAGS += -USQLITE_ALLOW_ROWID_IN_VIEW\n'),1)
  self.assertIn(r.TEST_CLI_BEFORE,before);self.assertNotIn(r.TEST_CLI_BEFORE,after);self.assertIn(r.TEST_CLI_AFTER,after)
  self.assertIn(b'SHELL_OPT += -DSQLITE_ENABLE_DBPAGE_VTAB\n',after)
  # Expand the AUTHENTIC changed recipe with a named inert compiler. It is
  # never executed; GNU make proves CFLAGS precede the test-only undef flag.
  prog=(b'CFLAGS:=-DSQLITE_ALLOW_ROWID_IN_VIEW -DPRODUCTION_UNCHANGED\nTEXE:=\nlibdir:=/PUBLIC\n'
   b'LTLINK:=NEVER_EXECUTE_COMPILER $(CFLAGS)\nREADLINE_FLAGS:=-DPUBLIC_READLINE\n'
   b'SHELL_OPT:=-DSQLITE_ENABLE_DBPAGE_VTAB\nLIBREADLINE:=-lPUBLIC_READLINE\nTLIBS:=-lm\n'
   +r.TEST_CLI_AFTER+b'shell.c sqlite3.c: ;\n')
  make=self.root/'Makefile';make.write_bytes(prog)
  code,out=actual(['/usr/bin/make','--no-print-directory','--dry-run','-rR','-f','Makefile','sqlite3'],self.root)
  self.assertEqual(code,0,out);self.assertEqual(out.count(b'NEVER_EXECUTE_COMPILER'),1)
  self.assertLess(out.index(b'-DSQLITE_ALLOW_ROWID_IN_VIEW'),out.index(b'-USQLITE_ALLOW_ROWID_IN_VIEW'))
  self.assertIn(b'-DSQLITE_ENABLE_DBPAGE_VTAB',out);self.assertIn(b'shell.c sqlite3.c',out);self.assertNotIn(b'libsqlite3.la',out)
  self.assertNotIn(b'CFLAGS +=',after[len(before):]);self.assertNotIn(b'TESTOPTS=',r.TESTFIXTURE_LOCAL_PATCH)
 def test_resolute_test_configuration_is_unchanged_except_previously_accepted_core_link(self):
  r=self.recipe('resolute');before=self.F.authentic('resolute');after=r.testfixture_after(before)
  self.assertNotIn(b'-USQLITE_ALLOW_ROWID_IN_VIEW',after);self.assertIn(b'sqlite3$(TEXE):\tshell.c libsqlite3.la',after)
  self.assertEqual(r.VENDOR_COMMANDS,{'vendor-standard.log':['/usr/bin/make','test'],'vendor-fts5-regression.log':['./testfixture','ext/fts5/test/fts5prefix2.test']})
 def test_production_core_and_package_hash_are_checked_across_unfiltered_vendor_phase(self):
  for profile in('noble','resolute'):
   r=self.recipe(profile);tree=ast.parse(Path(r.__file__).read_bytes());build=next(n for n in tree.body if isinstance(n,ast.FunctionDef)and n.name=='build')
   text=ast.unparse(build)
   self.assertIn('SESSION_INPUT_PATCH if leaf is None',text);self.assertIn("'session_patch_pin'",text)
   self.assertIn('package_before_vendor',text);self.assertIn("'vendor_package_changed'",text)
   self.assertEqual(sum(isinstance(n,ast.Call)and isinstance(n.func,ast.Name)and n.func.id=='verify_session_input_source'for n in ast.walk(build)),2)
   self.assertIn("'session_input_validation': session_input",text)
   self.assertNotIn('TESTOPTS',r.ENV);self.assertEqual(r.ENV['DEB_BUILD_OPTIONS'],'nocheck parallel=2')
 def test_candidate_production_view_positive_reaches_actual_function_and_closes_on_negative(self):
  v=load(BASE+'noble/verify_positive.py','view_positive')
  class Connection:
   def __init__(self,wrong=None):self.wrong=wrong;self.calls=[];self.closed=False
   def executescript(self,sql):self.calls.append(('setup',sql))
   def execute(self,sql):
    self.calls.append(('query',sql));row=[(1,)]if'rowid IS NULL'in sql else[(1,2,3)]
    if self.wrong==len(self.calls):row=[]
    return SimpleNamespace(fetchall=lambda:row)
   def close(self):self.closed=True
  for wrong,reason in((None,None),(2,'rowid_view_contract'),(3,'nested_join_contract')):
   c=Connection(wrong)
   with patch.object(v.sqlite3,'connect',return_value=c)as connect:
    if reason:
     with self.assertRaisesRegex(ValueError,'^'+reason+'$'):v.candidate_view_checks()
    else:self.assertEqual(v.candidate_view_checks(),{'rowid_in_view':True,'nested_join_rows':[[1,2,3]],'production_library_only':True,'security_accepted':False})
    connect.assert_called_once_with(':memory:')
   self.assertTrue(c.closed)
  tree=ast.parse(Path(v.__file__).read_bytes());verify=next(n for n in tree.body if isinstance(n,ast.FunctionDef)and n.name=='verify')
  self.assertEqual(sum(isinstance(n,ast.Call)and isinstance(n.func,ast.Name)and n.func.id=='candidate_view_checks'for n in ast.walk(verify)),1)
 def test_current_consumer_rejects_missing_or_foreign_candidate_view_proof(self):
  a=load('scripts/prepare_sqlite_ubuntu_gold2_ci_context.py','view_consumer');f=load('tests/test_prepare_sqlite_wal_ci_context.py','public_positive_fixture');a.select_variant('noble')
  def dto(version):
   value=f.positive(version);value.update(python_version=a.SPEC['python'],sqlite_runtime=a.SPEC['upstream'],python_no_site=True,system_library=a.SPEC['library'])
   value['sqlite_system_library']['path']=a.SPEC['library'];value['sqlite_stdlib_extension']['path']=a.SPEC['extension'];value['candidate_view_checks']=None if version==a.BASE else {'rowid_in_view':True,'nested_join_rows':[[1,2,3]],'production_library_only':True,'security_accepted':False};return value
  for version in(a.BASE,a.VERSION):
   value=dto(version);self.assertIs(a.positive(value,version),value)
   for kind in('missing','wrong','claim'):
    bad=deepcopy(value)
    if kind=='missing':del bad['candidate_view_checks']
    elif kind=='wrong':bad['candidate_view_checks']=None if version==a.VERSION else{}
    else:bad['candidate_view_checks']={'rowid_in_view':True,'nested_join_rows':[[1,2,3]],'production_library_only':True,'security_accepted':True}
    with self.subTest(version=version,kind=kind),self.assertRaises(RuntimeError):a.positive(bad,version)

if __name__=='__main__':unittest.main(verbosity=2)
