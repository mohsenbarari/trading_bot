"""Fixed historical fixture admission only; no historical program is executed."""
from contextlib import ExitStack
from copy import deepcopy
from hashlib import sha1,sha256
from pathlib import Path
from types import ModuleType
from unittest.mock import patch
import errno,fcntl,json,os,stat,tempfile,unittest
ROOT=Path(__file__).resolve().parents[1]
FIXTURE_ROOT=ROOT/'tests/fixtures'
OLD='10ccbe216fef04e5f7a7ce2ad31ac30eca7eeae090dfaaf8074fa3cc5c789d44'
NEW='a7ba6515937e7ae8193be4d550547651fe5b313399ec3a1f7634efe513e151c1'
PROVENANCE={'schema':'historical_security_helper_fixture/1','purpose':'Historical fixture only; not a live helper or operational authority','repository':'mohsenbarari/trading_bot','commit':'89f1d6cf542fc344591a1f103f75f69d50f5f570','source_path':'scripts/compose_security_os_candidate.py','git_blob_sha1':'a76d02f40064847b44928f34567b9aac35916ef9','bytes':54267,'sha256':OLD,'fixture_path':'tests/fixtures/security_os_candidate_before_f2a4.py','licensing':'Exact existing repository Source copied for self-contained tests; original bytes and comments preserved; no added license or relicensing claim.'}
FENCES=('subprocess.Popen','subprocess.run','subprocess.call','subprocess.check_call','subprocess.check_output','os.system','os.popen','os.fork','os.kill','os.killpg','socket.socket','socket.create_connection','socket.getaddrinfo','urllib.request.urlopen','ctypes.CDLL','ctypes.PyDLL')
def _need(value,code):
 if not value:raise RuntimeError(code)
def _frame(s):return(s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
def validate_historical_helper(raw,provenance):
 _need(type(raw)is bytes and len(raw)==54267 and sha256(raw).hexdigest()==OLD,'historical_fixture_source_pin')
 _need(type(provenance)is dict and json.dumps(provenance,sort_keys=True,separators=(',',':'),allow_nan=False)==json.dumps(PROVENANCE,sort_keys=True,separators=(',',':')),'historical_fixture_provenance')
 _need(sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()==PROVENANCE['git_blob_sha1'],'historical_fixture_Git_blob')
 return raw
def historical_helper_bytes():
 """Read only the two fixed tracked fixture leaves, with their original FDs."""
 rows=[];body=None;errors=[];result=None
 def check(row):
  p,fd,b,flags,parents=row
  _need(b==_frame(os.fstat(fd))==_frame(p.lstat()),'historical_fixture_changed')
  _need((fcntl.fcntl(fd,fcntl.F_GETFL),fcntl.fcntl(fd,fcntl.F_GETFD))==flags and os.lseek(fd,0,os.SEEK_CUR)==0,'historical_fixture_OFD')
  _need(all(q.resolve(strict=True)==q and _frame(q.lstat())[:5]==old for q,old in parents),'historical_fixture_parent')
 def read(name,maximum):
  p=FIXTURE_ROOT/name;_need(p.resolve(strict=True)==p,'historical_fixture_path')
  parents=tuple((q,_frame(q.lstat())[:5])for q in p.parents)
  _need(all(stat.S_ISDIR(b[2])and b[3]in(0,os.geteuid())and not b[2]&0o022 for q,b in parents),'historical_fixture_parent')
  fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC);row=[p,fd,None,None,parents];rows.append(row)
  row[2]=b=_frame(os.fstat(fd));row[3]=(fcntl.fcntl(fd,fcntl.F_GETFL),fcntl.fcntl(fd,fcntl.F_GETFD))
  _need(stat.S_ISREG(b[2])and b[3:6]==(os.geteuid(),os.getegid(),1)and stat.S_IMODE(b[2])in(0o600,0o644)and 0<b[6]<=maximum,'historical_fixture_metadata')
  check(row);raw=os.pread(fd,b[6]+1,0);_need(len(raw)==b[6],'historical_fixture_read_bound');check(row);return raw
 try:
  raw=read('security_os_candidate_before_f2a4.py',54267);metadata=read('security_os_candidate_before_f2a4.json',2048)
  def unique(rows):
   value={}
   for key,item in rows:_need(key not in value,'historical_fixture_duplicate_key');value[key]=item
   return value
  provenance=json.loads(metadata,object_pairs_hook=unique,parse_constant=lambda _:(_ for _ in()).throw(RuntimeError('historical_fixture_json_constant')))
  result=validate_historical_helper(raw,provenance)
 except BaseException as error:body=error
 finally:
  for row in reversed(rows):
   p,fd,b,flags,parents=row
   if b is not None and flags is not None:
    try:check(row)
    except BaseException as error:errors.append(error)
   try:os.close(fd)
   except BaseException as error:errors.append(error)
   else:
    try:os.fstat(fd)
    except OSError as error:
     if error.errno!=errno.EBADF:errors.append(error)
    else:errors.append(RuntimeError('historical_fixture_close_unknown'))
   if b is not None:
    try:_need(_frame(p.lstat())==b and all(q.resolve(strict=True)==q and _frame(q.lstat())[:5]==old for q,old in parents),'historical_fixture_named_postclose')
    except BaseException as error:errors.append(error)
 if errors:raise BaseExceptionGroup('historical fixture body and original finalizers',([body]if body is not None else[])+errors)
 if body is not None:raise body
 return result
def historical_helper_module():
 raw=historical_helper_bytes();module=ModuleType('_historical_security_os_common_before_f2a4')
 module.__file__=str(FIXTURE_ROOT/'security_os_candidate_before_f2a4.py')
 exec(compile(raw,module.__file__,'exec'),module.__dict__);return module

def _load(name):
 p=ROOT/'scripts'/name;module=ModuleType('fixture_'+p.stem);module.__file__=str(p)
 exec(compile(p.read_bytes(),str(p),'exec'),module.__dict__);return module

class HistoricalFixtureTests(unittest.TestCase):
 def setUp(self):
  self.stack=ExitStack();self.addCleanup(self.stack.close)
  self.root=Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='.owned-historical-fixture-',dir=ROOT/'tests')))
  for target in FENCES:self.stack.enter_context(patch(target,side_effect=AssertionError('historical code must remain inert')))
  self.stack.enter_context(patch.dict(os.environ,{'PATH':'/usr/bin:/bin','GITHUB_SHA':'a'*40,'GITHUB_ACTIONS':'true','RUNNER_ENVIRONMENT':'github-hosted','GITHUB_REPOSITORY':'mohsenbarari/trading_bot','RUNNER_TEMP':str(self.root),'GITHUB_RUN_ID':'123','GITHUB_RUN_ATTEMPT':'1'},clear=True))
 def write(self,p,b):
  p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b);p.chmod(0o600)
 def test_exact_Git_era_bytes_and_unchanged_historical_pins_are_required(self):
  raw=historical_helper_bytes();self.assertIs(validate_historical_helper(raw,PROVENANCE),raw)
  self.assertEqual(sha256(raw).hexdigest(),OLD)
  for name in('compose_market_security_os_candidate.py','compose_market_security_profile.py','compose_app_mountpoint_image.py'):
   self.assertEqual(_load(name).HELPERS['compose_security_os_candidate.py'],OLD)
  self.assertEqual(_load('prepare_market_security_payload.py').COMMAND_HELPERS_SHA256,OLD)
  self.assertEqual(_load('run_app_image_ci_smoke.py').HELPERS['compose_security_os_candidate.py'][1],OLD)
  self.assertEqual(sha256((ROOT/'scripts/compose_security_os_candidate.py').read_bytes()).hexdigest(),NEW)
 def test_fixed_reader_refuses_missing_symlink_hardlink_or_tampered_owned_fixture(self):
  original=historical_helper_bytes();metadata=(json.dumps(PROVENANCE,sort_keys=True)+'\n').encode()
  for kind in('missing','symlink','hardlink','tampered'):
   p=self.root/kind;p.mkdir();source=p/'security_os_candidate_before_f2a4.py';self.write(p/'security_os_candidate_before_f2a4.json',metadata)
   if kind=='tampered':self.write(source,original[:-1]+b'X')
   elif kind in('symlink','hardlink'):
    target=p/'other';self.write(target,original)
    if kind=='symlink':source.symlink_to(target)
    else:os.link(target,source)
   with self.subTest(kind=kind),patch.dict(globals(),{'FIXTURE_ROOT':p}),self.assertRaises((RuntimeError,OSError,BaseExceptionGroup)):historical_helper_bytes()
 def test_mixed_era_unknown_missing_duplicate_or_changed_metadata_never_admits(self):
  raw=historical_helper_bytes();current=(ROOT/'scripts/compose_security_os_candidate.py').read_bytes();variants=[]
  for key,value in(('commit','f2a4baf9d3c8cde62bedbd55a29398357eae3df8'),('sha256',NEW),('bytes',54258),('source_path','UNRELATED'),('extra',True)):
   bad=deepcopy(PROVENANCE);bad[key]=value;variants.append(bad)
  bad=deepcopy(PROVENANCE);del bad['git_blob_sha1'];variants.append(bad)
  for bad in variants:
   with self.subTest(fields=sorted(bad)),self.assertRaises(RuntimeError):validate_historical_helper(raw,bad)
  with self.assertRaises(RuntimeError):validate_historical_helper(current,PROVENANCE)
  p=self.root/'duplicate';p.mkdir();self.write(p/'security_os_candidate_before_f2a4.py',raw);self.write(p/'security_os_candidate_before_f2a4.json',b'{"commit":"ONE","commit":"TWO"}')
  with patch.dict(globals(),{'FIXTURE_ROOT':p}),self.assertRaisesRegex(RuntimeError,'historical_fixture_duplicate_key'):historical_helper_bytes()
 def test_actual_historical_loader_accepts_exact_fixture_and_rejects_current_helper(self):
  self.assertEqual(os.geteuid(),1001,'genuine hosted ownership fixture required')
  m=_load('run_app_image_ci_smoke.py');root=self.root/'security-app-ci-smoke-historical';root.mkdir(mode=0o700)
  for leaf in('source','reports','docker-config'):(root/leaf).mkdir(mode=0o700)
  for name,(relative,_)in m.HELPERS.items():self.write(root/'source'/name,historical_helper_bytes()if name=='compose_security_os_candidate.py'else(ROOT/relative).read_bytes())
  with ExitStack()as scope:
   ctx=m.Controller(root,scope);ctx.check();self.assertEqual(set(ctx.modules),set(m.HELPERS));self.assertFalse(hasattr(ctx.C,'cleanup_owned_child'))
  p=root/'source/compose_security_os_candidate.py';p.write_bytes((ROOT/'scripts/compose_security_os_candidate.py').read_bytes())
  with ExitStack()as scope,self.assertRaisesRegex(RuntimeError,'source_pin'):m.Controller(root,scope)
 def test_current_ordinary_cache_still_admits_only_current_accepted_helper(self):
  a=_load('prepare_app_security_os_cache.py');q=_load('prepare_sqlite_wal_os_cache.py')
  with ExitStack()as scope:
   held=a.Held(scope);current=a.load_recipe(held);self.assertTrue(callable(current.module.cleanup_owned_child));held.check();fds=[row.fd for row in held.bindings]
  for fd in fds:
   with self.assertRaises(OSError)as closed:os.fstat(fd)
   self.assertEqual(closed.exception.errno,errno.EBADF)
  self.assertEqual(current.lock['source']['sha256'],NEW);self.assertEqual(q.FETCHER[2],NEW)
  with self.assertRaises(RuntimeError):validate_historical_helper((ROOT/'scripts/compose_security_os_candidate.py').read_bytes(),PROVENANCE)
 def test_historical_module_import_is_inert_and_does_not_rewrite_ordinary_bindings(self):
  old=historical_helper_module();self.assertEqual(old.SOURCE,'19d4c2da778f8f1edc567db4c9616c2d153980c9')
  self.assertTrue(callable(old.command));self.assertFalse(hasattr(old,'cleanup_owned_child'))
  self.assertEqual(sha256((ROOT/'scripts/compose_security_os_candidate.py').read_bytes()).hexdigest(),NEW)

if __name__=='__main__':unittest.main()
