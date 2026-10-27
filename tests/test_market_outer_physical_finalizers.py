"""Owned Python FD/exception QA, never a hosted build/runtime success.

The inert main seam only registers genuine owned Held files and returns/raises.
Real Held and finish_evidence run unchanged. Faults physically close the actual
FD then mutate its actual metadata; no UID/stat/runtime identity is masked.
"""
from contextlib import redirect_stdout
from hashlib import sha256
from pathlib import Path
from types import ModuleType
from unittest.mock import patch
import ast,io,json,os,tempfile,unittest
ROOT=Path(__file__).resolve().parents[1];PATH=ROOT/'scripts/prepare_market_capture2_image.py'
M=ModuleType('owned_outer_physical');M.__file__=str(PATH);exec(compile(PATH.read_bytes(),str(PATH),'exec'),M.__dict__)
class PhysicalTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory(prefix='owned-outer-fds-',dir=ROOT/'tests');self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name);self.rows=[];self.addCleanup(self.cleanup)
 def cleanup(self):
  for row in self.rows:
   if row.fd is not None:
    try:row.close()
    except BaseException:pass
 def file(self,name):
  path=self.root/name;path.write_bytes(b'OWNED immutable public fixture\n');path.chmod(0o600);return path
 def held(self,name):
  row=M.Held(self.file(name),maximum=128);self.rows.append(row);return row
 def run_owned(self,*,primary=False,mutate=None,close_result=None,named=None,unknown=False):
  driver=self.held('driver-'+str(len(self.rows)));evidence=self.held('evidence-'+str(len(self.rows)));raw=RuntimeError('raw_high_critical_findings')if primary else None;events=[];actual_close=os.close
  def owned_main(argv=None,*,retained=None,final_checks=None):
   self.assertIs(M.OUTER_PHYSICAL_FINALIZERS_COMPLETED,False);self.assertIsNone(M.OUTER_FAILURE);retained.append(evidence);events.append('owned_main')
   if raw is not None:raise raw
   return 0
  def close_driver():
   events.append('close_driver')
   if close_result is False:return False
   driver.close()
   if unknown:raise ValueError('OWNED_UNDISCLOSED_DETAIL')
  def driver_named():
   events.append('driver_named')
   if named=='false':return False
   if named=='mutate':
    old=driver.path.stat();os.utime(driver.path,ns=(old.st_atime_ns,old.st_mtime_ns+1000000000))
   driver.named()
  chosen=driver.fd if mutate=='driver'else evidence.fd if mutate=='evidence'else None;changed=[]
  def physical_close(fd):
   actual_close(fd)
   if fd==chosen and not changed:
    path=driver.path if mutate=='driver'else evidence.path;old=path.stat();os.utime(path,ns=(old.st_atime_ns,old.st_mtime_ns+1000000000));changed.append(fd)
  error=None;code=None
  with patch.object(M,'main',owned_main),patch.object(M.os,'close',physical_close):
   try:code=M.outer_main([],close_driver=close_driver,driver_named=driver_named)
   except BaseException as failure:error=failure
  return {'driver':driver,'evidence':evidence,'primary':raw,'events':events,'error':error,'code':code,'changed':changed}
 def test_normal_owned_outer_only_marks_true_after_actual_source_and_evidence_closes(self):
  r=self.run_owned();self.assertEqual(r['code'],0);self.assertIsNone(r['error']);self.assertIs(M.OUTER_PHYSICAL_FINALIZERS_COMPLETED,True);self.assertIsNone(r['driver'].fd);self.assertIsNone(r['evidence'].fd);self.assertEqual(r['events'],['owned_main','close_driver','driver_named'])
 def test_primary_raw_error_with_actual_physical_success_is_reported_true(self):
  r=self.run_owned(primary=True);self.assertIs(r['error'],r['primary']);v=M.closed_failure(r['error']);self.assertIs(v['outer_physical_finalizers_completed'],True);self.assertEqual(v['code'],v['original_failure_code']);self.assertEqual(v['code'],'raw_high_critical_findings');self.assertEqual(v['physical_finalizer_failures'],[]);self.assertIsNone(r['driver'].fd);self.assertIsNone(r['evidence'].fd)
 def test_actual_driver_finalclose_mutation_overrides_primary_raw_and_preserves_cause(self):
  r=self.run_owned(primary=True,mutate='driver');self.assertTrue(r['changed']);self.assertEqual(str(r['error']),'source_changed');self.assertIs(r['error'].__cause__,r['primary']);v=M.closed_failure(r['error']);self.assertIs(v['outer_physical_finalizers_completed'],False);self.assertEqual(v['original_failure_code'],'raw_high_critical_findings');self.assertEqual([x['stage']for x in v['physical_finalizer_failures']],['close_driver','finish_evidence']);self.assertIsNone(r['driver'].fd);self.assertIsNone(r['evidence'].fd)
 def test_actual_retained_reader_finalclose_mutation_overrides_primary_raw(self):
  r=self.run_owned(primary=True,mutate='evidence');self.assertTrue(r['changed']);self.assertIs(r['error'].__cause__,r['primary']);v=M.closed_failure(r['error']);self.assertEqual(v['code'],'source_changed');self.assertIs(v['outer_physical_finalizers_completed'],False);self.assertEqual(v['original_failure_code'],'raw_high_critical_findings');self.assertEqual([x['stage']for x in v['physical_finalizer_failures']],['finish_evidence']);self.assertIsNone(r['evidence'].fd)
 def test_driver_named_final_mutation_cannot_mark_completed(self):
  r=self.run_owned(primary=True,named='mutate');self.assertIs(r['error'].__cause__,r['primary']);v=M.closed_failure(r['error']);self.assertEqual(v['code'],'source_changed');self.assertIs(v['outer_physical_finalizers_completed'],False);self.assertEqual(v['physical_finalizer_failures'][0]['stage'],'finish_evidence')
 def test_unknown_physical_error_keeps_original_raw_without_detail_leakage(self):
  r=self.run_owned(primary=True,unknown=True);self.assertIsInstance(r['error'],ValueError);self.assertIs(r['error'].__cause__,r['primary']);v=M.closed_failure(r['error']);self.assertEqual(v['code'],'unclassified');self.assertEqual(v['original_failure_code'],'raw_high_critical_findings');self.assertEqual(v['exception_class'],'ValueError');self.assertIs(v['outer_physical_finalizers_completed'],False);self.assertNotIn('OWNED_UNDISCLOSED_DETAIL',json.dumps(v))
 def test_false_driver_or_named_callback_is_not_physical_success(self):
  for options in({'close_result':False},{'named':'false'}):
   r=self.run_owned(primary=True,**options);v=M.closed_failure(r['error']);self.assertEqual(v['code'],'physical_finalizer');self.assertIs(v['outer_physical_finalizers_completed'],False);self.assertEqual(v['original_failure_code'],'raw_high_critical_findings')
 def test_unrelated_error_and_direct_main_cannot_borrow_prior_success(self):
  r=self.run_owned(primary=True);self.assertIs(M.closed_failure(r['error'])['outer_physical_finalizers_completed'],True);v=M.closed_failure(RuntimeError('source_changed'));self.assertIs(v['outer_physical_finalizers_completed'],False);self.assertIsNone(v['original_failure_code']);self.assertEqual(v['physical_finalizer_failures'],[])
  with redirect_stdout(io.StringIO()):self.assertEqual(M.main([]),0)
  self.assertIs(M.OUTER_PHYSICAL_FINALIZERS_COMPLETED,False);self.assertIsNone(M.OUTER_FAILURE);self.assertIs(M.closed_failure(r['error'])['outer_physical_finalizers_completed'],False)
 def test_each_outer_call_resets_prior_error_snapshot_and_completion(self):
  first=self.run_owned(primary=True);first_error=first['error'];second=self.run_owned();self.assertIsNone(second['error']);self.assertIsNone(M.OUTER_FAILURE);self.assertIs(M.OUTER_PHYSICAL_FINALIZERS_COMPLETED,True);self.assertIs(M.closed_failure(first_error)['outer_physical_finalizers_completed'],False)
 def test_strict_gates_evidence_finalizers_and_workflow_are_unchanged_and_pin_exact(self):
  wanted=dict({'finish_evidence': 'bdd938f18d63ee068701a788689ae85dee7ea0e40fe8e70cc442e80ffa26e8ce', 'raw_gate': 'fe4d54b4a825a405e8967706fc1d78e0050b6479b49589eb5a72390dbff98181', 'complete_evidence': '62fee48da4f736de16509df3e32e9e4a8678b4dcdf79fba2573c49b4b8b41e02', 'complete_gate': '4455dc1931d65ab324d6127011643a185009676f1c44906a94dfb06cb24f9225'})
  for n in ast.parse(PATH.read_bytes()).body:
   if isinstance(n,ast.FunctionDef)and n.name in wanted:self.assertEqual(sha256(ast.dump(n,include_attributes=False).encode()).hexdigest(),wanted.pop(n.name))
  self.assertEqual(wanted,{})
  raw=(ROOT/'.github/workflows/market-capture2-image.yml').read_bytes();self.assertEqual((len(raw),sha256(raw).hexdigest()),(18917,'e5571608e28b57484c8c541ed70664c619b33178a1832bc9819422a04a6bc3d7'))
  tree=ast.parse((ROOT/'scripts/market_business81_outer.py').read_bytes());pin=next(ast.literal_eval(n.value)for n in tree.body if isinstance(n,ast.Assign)and any(isinstance(t,ast.Name)and t.id=='NORMAL_PIN'for t in n.targets));raw=PATH.read_bytes();self.assertEqual(pin,(len(raw),sha256(raw).hexdigest()))
if __name__=='__main__':unittest.main()
