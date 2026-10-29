from pathlib import Path
from types import ModuleType
from hashlib import sha256
from unittest.mock import patch
import errno,fcntl,os,tempfile,unittest
HERE=Path(__file__).absolute().parent
S=ModuleType('candidate_sealer');S.__file__=str(HERE/'seal_and_run.py');exec(compile(Path(S.__file__).read_bytes(),S.__file__,'exec'),S.__dict__)
class Tests(unittest.TestCase):
 def fixture(self,root):
  path=Path(root)/'a';path.write_bytes(b'OWNED_PUBLIC_DATA');path.chmod(0o644)
  item=S.Held(path,(17,sha256(b'OWNED_PUBLIC_DATA').hexdigest()),owner=os.geteuid(),mode=0o644)
  self.addCleanup(lambda:self.close(item));return item
 def close(self,item):
  try:item.close()
  except BaseException:pass
 def test_one_pass_deduplicates_name_observations_only(self):
  with tempfile.TemporaryDirectory(dir=HERE)as td:
   item=self.fixture(td);seen=[];actual=Path.resolve
   def resolve(path,*args,**kwargs):seen.append(path);return actual(path,*args,**kwargs)
   with patch.object(Path,'resolve',resolve):
    observation=S.MetadataPass(lambda:None);item.check(observation);item.check(observation);observation.finish()
   self.assertEqual(seen.count(item.path),2);self.assertTrue(observation.closed);self.assertEqual(observation.rows,{})
 def test_an_entire_new_pass_is_fresh_after_payload_boundary(self):
  with tempfile.TemporaryDirectory(dir=HERE)as td:
   item=self.fixture(td);S.metadata_check([item],[],None,lambda:None);item.path.chmod(0o600)
   with self.assertRaisesRegex(RuntimeError,'sealed_changed'):S.metadata_check([item],[],None,lambda:None)
 def test_final_pass_observation_detects_name_replacement(self):
  with tempfile.TemporaryDirectory(dir=HERE)as td:
   item=self.fixture(td);observation=S.MetadataPass(lambda:None);item.check(observation);old=item.path.with_suffix('.old');item.path.rename(old);item.path.write_bytes(b'OWNED_PUBLIC_DATA');item.path.chmod(0o644)
   with self.assertRaisesRegex(RuntimeError,'sealed_changed'):observation.finish()
   self.assertEqual(observation.rows,{})
 def test_final_pass_observation_detects_parent_mode_drift(self):
  with tempfile.TemporaryDirectory(dir=HERE)as td:
   item=self.fixture(td);observation=S.MetadataPass(lambda:None);item.check(observation);os.chmod(td,0o711)
   with self.assertRaisesRegex(RuntimeError,'sealed_changed'):observation.finish()
 def test_per_fd_flags_do_not_share_cached_observations(self):
  with tempfile.TemporaryDirectory(dir=HERE)as td:
   item=self.fixture(td);observation=S.MetadataPass(lambda:None);item.check(observation);fcntl.fcntl(item.original_fd,fcntl.F_SETFD,0)
   with self.assertRaisesRegex(RuntimeError,'sealed_changed'):item.check(observation)
   observation.finish()
 def test_per_fd_fstat_remains_fresh_in_same_pass(self):
  with tempfile.TemporaryDirectory(dir=HERE)as td:
   item=self.fixture(td);observation=S.MetadataPass(lambda:None);item.check(observation);item.path.write_bytes(b'CHANGED')
   with self.assertRaisesRegex(RuntimeError,'sealed_changed'):item.check(observation)
   with self.assertRaisesRegex(RuntimeError,'sealed_changed'):observation.finish()
 def test_full9_source_metadata_retained(self):
  with tempfile.TemporaryDirectory(dir=HERE)as td:
   item=self.fixture(td);observation=S.MetadataPass(lambda:None);item.check(observation);os.utime(item.path,ns=(item.before[7]+1,item.before[7]+1))
   with self.assertRaisesRegex(RuntimeError,'sealed_changed'):observation.finish()
 def test_closed_pass_cannot_be_reused(self):
  with tempfile.TemporaryDirectory(dir=HERE)as td:
   item=self.fixture(td);observation=S.MetadataPass(lambda:None);item.check(observation);observation.finish()
   with self.assertRaisesRegex(RuntimeError,'sealed_changed'):item.check(observation)
   with self.assertRaisesRegex(RuntimeError,'sealed_changed'):observation.finish()
 def test_body_and_finish_errors_both_retained(self):
  with tempfile.TemporaryDirectory(dir=HERE)as td:
   item=self.fixture(td);body=RuntimeError('BODY');actual=item.check
   def changed(observation=None):actual(observation);item.path.chmod(0o600);raise body
   with patch.object(item,'check',changed):
    with self.assertRaises(S.IOFailure)as caught:S.metadata_check([item],[],None,lambda:None)
   self.assertIs(caught.exception.errors[0],body);self.assertEqual(caught.exception.errors[1].args,('sealed_changed',))
 def test_original_deadline_checked_before_and_after_retirement(self):
  with tempfile.TemporaryDirectory(dir=HERE)as td:
   item=self.fixture(td);calls=[]
   def clocks():
    calls.append(1)
    if len(calls)>=2:raise RuntimeError('original_deadline')
   with self.assertRaises(S.IOFailure)as caught:S.metadata_check([item],[],None,clocks)
   self.assertEqual(len(calls),3);self.assertTrue(all(e.args==('original_deadline',)for e in caught.exception.errors))
 def test_initial_deadline_refuses_before_any_names(self):
  def clocks():raise RuntimeError('original_deadline')
  with patch.object(Path,'resolve',side_effect=AssertionError('names touched')):
   with self.assertRaisesRegex(RuntimeError,'original_deadline'):S.metadata_check([],[],None,clocks)
 def test_keeper_callback_still_executed_and_original_error_kept(self):
  class Keeper:
   calls=0
   def check(self):self.calls+=1;raise RuntimeError('KEEPER')
  k=Keeper()
  with self.assertRaisesRegex(RuntimeError,'KEEPER'):S.metadata_check([],[],k,lambda:None)
  self.assertEqual(k.calls,1)
 def test_saved_original_reader_flags_checked_fresh(self):
  with tempfile.TemporaryDirectory(dir=HERE)as td:
   rows=[];S.save(Path(td)/'saved',b'OWNED_PUBLIC',owned=rows,check=lambda:None);item=rows[0];observation=S.MetadataPass(lambda:None);item.check(observation);fcntl.fcntl(item.original_reader,fcntl.F_SETFD,0)
   with self.assertRaisesRegex(RuntimeError,'saved_reader_changed'):item.check(observation)
   observation.finish()
   try:item.close()
   except BaseException:pass
   with self.assertRaises(OSError)as caught:os.fstat(item.original_reader)
   self.assertEqual(caught.exception.errno,errno.EBADF)
 def test_same_name_prefix5_then_full9_retains_full_final_expectation(self):
  with tempfile.TemporaryDirectory(dir=HERE)as td:
   item=self.fixture(td);observation=S.MetadataPass(lambda:None);observation.match(item.path,item.before[:5],'PREFIX');observation.match(item.path,item.before,'FULL');os.utime(item.path,ns=(item.before[7]+1,item.before[7]+1))
   with self.assertRaisesRegex(RuntimeError,'sealed_changed'):observation.finish()
 def test_same_name_prefix5_cannot_mask_wrong_full9_expectation(self):
  with tempfile.TemporaryDirectory(dir=HERE)as td:
   item=self.fixture(td);observation=S.MetadataPass(lambda:None);observation.match(item.path,item.before[:5],'PREFIX');wrong=item.before[:6]+(item.before[6]+1,)+item.before[7:]
   with self.assertRaisesRegex(RuntimeError,'FULL'):observation.match(item.path,wrong,'FULL')
   observation.finish()
 def test_removed_ancestor_is_observed_again_at_retirement(self):
  with tempfile.TemporaryDirectory(dir=HERE)as td:
   d=Path(td)/'parent';d.mkdir();item=self.fixture(d);observation=S.MetadataPass(lambda:None);item.check(observation);d.rename(d.with_name('moved'))
   with self.assertRaises(S.IOFailure)as caught:observation.finish()
   self.assertTrue(all(isinstance(e,FileNotFoundError)for e in caught.exception.errors));self.assertEqual(observation.rows,{})
 def test_replaced_ancestor_cannot_reuse_original_metadata_pass(self):
  with tempfile.TemporaryDirectory(dir=HERE)as td:
   d=Path(td)/'parent';d.mkdir();item=self.fixture(d);observation=S.MetadataPass(lambda:None);item.check(observation);d.rename(d.with_name('moved'));d.mkdir();(d/'a').write_bytes(b'OWNED_PUBLIC_DATA');(d/'a').chmod(0o644)
   with self.assertRaises(S.IOFailure)as caught:observation.finish()
   self.assertTrue(all(e.args==('sealed_changed',)for e in caught.exception.errors))
 def test_mid_pass_failure_still_rechecks_all_previously_observed_names(self):
  with tempfile.TemporaryDirectory(dir=HERE)as td:
   item=self.fixture(td);body=RuntimeError('MIDPASS')
   class Bad:
    fd=123
    def check(self,observation):item.path.chmod(0o600);raise body
   with self.assertRaises(S.IOFailure)as caught:S.metadata_check([item,Bad()],[],None,lambda:None)
   self.assertIs(caught.exception.errors[0],body);self.assertTrue(any(e.args==('sealed_changed',)for e in caught.exception.errors[1:]))
if __name__=='__main__':unittest.main(verbosity=2)
