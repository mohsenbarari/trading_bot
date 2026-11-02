"""Actual Go1.27.3 partial-field JSON from failed CI41339411764, not a model.

The public fixture is age-whole-package-projection.log from artifact13096407230
at reviewed head4938199a2c71f1080c8fa05926bbda792050f027. Its selected zero
fields are omitted by the real producer. No new SDK, build or network runs.
"""
from contextlib import ExitStack,contextmanager
import copy,errno,fcntl,hashlib,json,os
from pathlib import Path
from types import ModuleType
import unittest

ROOT=Path(__file__).absolute().parents[1]
WIRE=(69030,'e8acd3bb5c676866cf5b93673097bd295cd21a76ab3e56fcd76786af2a69b48f')
def frame(s):return tuple(getattr(s,n)for n in('st_dev','st_ino','st_mode','st_uid','st_gid','st_nlink','st_size','st_mtime_ns','st_ctime_ns'))
@contextmanager
def held(path,maximum,pin=None):
 fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC)
 try:
  before=frame(os.fstat(fd));flags=(fcntl.fcntl(fd,fcntl.F_GETFL),fcntl.fcntl(fd,fcntl.F_GETFD));raw=os.pread(fd,maximum+1,0)
  if path.resolve()!=path or not 0<len(raw)==before[6]<=maximum or(pin and(len(raw),hashlib.sha256(raw).hexdigest())!=pin):raise AssertionError('public fixture identity')
  yield raw
 finally:
  try:
   if frame(os.fstat(fd))!=before or frame(path.lstat())!=before or flags!=(fcntl.fcntl(fd,fcntl.F_GETFL),fcntl.fcntl(fd,fcntl.F_GETFD))or os.lseek(fd,0,os.SEEK_CUR)!=0:raise AssertionError('original public fixture drift')
  finally:os.close(fd)
  try:os.fstat(fd)
  except OSError as exc:
   if exc.errno!=errno.EBADF:raise
  else:raise AssertionError('original public fixture close')
  if frame(path.lstat())!=before:raise AssertionError('public fixture postclose')

class ActualGoListWire(unittest.TestCase):
 def setUp(self):
  self.scope=ExitStack();self.addCleanup(self.scope.close)
  raw=self.scope.enter_context(held(ROOT/'scripts/security_age132_tool/contracts.py',65536))
  self.c=ModuleType('owned_age_go_list_contract');self.c.__file__=str(ROOT/'scripts/security_age132_tool/contracts.py');exec(compile(raw,self.c.__file__,'exec'),self.c.__dict__)
  self.wire=self.scope.enter_context(held(ROOT/'tests/fixtures/age132_go_list_41339411764_age_whole.jsonseq',131072,WIRE))
  self.lock=json.loads(self.scope.enter_context(held(ROOT/'scripts/security_age132_tool/inputs.json',65536)))
  self.rows=self.c.documents(self.wire,8*1024**2)
 def project(self,rows=None):
  raw=self.wire if rows is None else b'\n'.join(json.dumps(x).encode()for x in rows)
  return self.c.package_projection(raw,'filippo.io/age',self.lock['age_modules'],'./...',('filippo.io/age','filippo.io/age/cmd/age','filippo.io/age/cmd/age-keygen'))
 def test_actual217_package_wire_is_not_required_to_serialize_zero_fields(self):
  result=self.project();self.assertEqual(result['package_count'],217);self.assertEqual(result['raw_sha256'],WIRE[1]);self.assertEqual(result['tests'],False);self.assertTrue(result['complete_and_only_reachable_import_closure'])
 def test_explicit_documented_zero_fields_have_identical_projection(self):
  rows=copy.deepcopy(self.rows)
  for row in rows:
   for key,value in {'Error':None,'DepsErrors':None,'Incomplete':False,'ForTest':'','Imports':None,'ImportMap':None,'Match':None,'Standard':False,'DepOnly':False,'Module':None}.items():row.setdefault(key,value)
  a=self.project();b=self.project(rows)
  for key in ('raw_bytes','raw_sha256'):a.pop(key);b.pop(key)
  self.assertEqual(a,b)
 def test_missing_identity_or_unknown_selected_field_refuses(self):
  for key in ('ImportPath','Name','unexpected_wire_field'):
   with self.subTest(key=key):
    rows=copy.deepcopy(self.rows)
    if key.startswith('unexpected'):rows[0][key]='untrusted'
    else:del rows[0][key]
    with self.assertRaisesRegex(RuntimeError,'package_projection_fields'):self.project(rows)
 def test_explicit_wrong_false_or_error_shapes_are_not_defaults(self):
  for key,value in [('Standard',None),('DepOnly',0),('Incomplete',None),('ForTest',None),('Error',{}),('DepsErrors',[{}])]:
   with self.subTest(key=key):
    rows=copy.deepcopy(self.rows);rows[0][key]=value
    with self.assertRaises(RuntimeError):self.project(rows)
 def test_replacement_or_version_drift_still_refuses(self):
  target=next(i for i,row in enumerate(self.rows)if row.get('Module',{}).get('Version'))
  for field,value in [('Replace',{}),('Version','v0.0.0-unadmitted')]:
   with self.subTest(field=field):
    rows=copy.deepcopy(self.rows);rows[target]['Module'][field]=value
    with self.assertRaises(RuntimeError):self.project(rows)
 def test_missing_required_cli_and_duplicate_packages_refuse(self):
  for rows in ([row for row in self.rows if row['ImportPath']!='filippo.io/age/cmd/age-keygen'],self.rows+[self.rows[0]]):
   with self.assertRaises(RuntimeError):self.project(rows)

if __name__=='__main__':unittest.main()
