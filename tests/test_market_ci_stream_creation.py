"""Real inert stdout/selector-close creation regressions; no Docker or Git."""
from hashlib import sha256
from pathlib import Path
from types import ModuleType
from unittest.mock import patch
import os,selectors,subprocess,sys,tempfile,unittest
ROOT=Path(__file__).resolve().parents[1];P=ROOT/'scripts/prepare_market_capture2_image.py'
M=ModuleType('_owned_stream_creation');M.__file__=str(P);exec(compile(P.read_bytes(),str(P),'exec'),M.__dict__)
class StreamCreationTests(unittest.TestCase):
 def own(self):
  t=tempfile.TemporaryDirectory(dir=Path(__file__).parent);self.addCleanup(t.cleanup);return Path(t.name)
 def command(self,fd,complete):
  M.command([sys.executable,'-I','-S','-B','-c',"import os;os.write(1,b'OWNED STREAM')"],environment={'PATH':os.defpath},seconds=3,maximum=65536,target=fd,stream_complete=complete)
 def test_real_stdout_close_mutations_cannot_be_adopted_as_first_binding(self):
  actual=subprocess.Popen;raw=b'OWNED STREAM'
  for mode in('payload','same_bytes_new_inode','mode','mtime'):
   with self.subTest(mutation=mode):
    root=self.own();path=root/'owned-output';fd=os.open(path,os.O_RDWR|os.O_CREAT|os.O_EXCL,0o600);completion={};events=[]
    class Pipe:
     def __init__(self,pipe):self.pipe=pipe
     def fileno(self):return self.pipe.fileno()
     def close(self):
      self.pipe.close();events.append('pipe_close');self_outer.assertTrue(completion)
      if mode=='payload':path.write_bytes(b'X'*len(raw))
      elif mode=='same_bytes_new_inode':path.rename(root/'original');path.write_bytes(raw);path.chmod(0o600)
      elif mode=='mode':path.chmod(0o600)
      else:
       info=path.stat();os.utime(path,ns=(info.st_atime_ns,info.st_mtime_ns+1))
    self_outer=self
    def spawn(*args,**kwargs):
     child=actual(*args,**kwargs);child.stdout=Pipe(child.stdout);return child
    def complete(binding,frame):completion.update(binding=binding,frame=frame);events.append('complete')
    try:
     with patch.object(M.subprocess,'Popen',side_effect=spawn):self.command(fd,complete)
     self.assertEqual(events,['complete','pipe_close']);self.assertEqual(completion['binding'],{'bytes':len(raw),'sha256':sha256(raw).hexdigest()})
     with self.assertRaisesRegex(RuntimeError,'source_changed|source_file'):
      M.hold_written(path,fd,65536,binding=completion['binding'],expected_frame=completion['frame'])
    finally:os.close(fd)
 def test_completion_precedes_even_the_real_selector_physical_close(self):
  root=self.own();path=root/'owned-output';fd=os.open(path,os.O_RDWR|os.O_CREAT|os.O_EXCL,0o600);completion={};events=[];actual=selectors.DefaultSelector.close
  def close(selector):
   actual(selector);events.append('selector_close');self.assertTrue(completion);path.write_bytes(b'X'*len(b'OWNED STREAM'))
  def complete(binding,frame):completion.update(binding=binding,frame=frame);events.append('complete')
  try:
   with patch.object(M.selectors.DefaultSelector,'close',new=close):self.command(fd,complete)
   self.assertEqual(events,['complete','selector_close'])
   with self.assertRaisesRegex(RuntimeError,'source_changed'):
    M.hold_written(path,fd,65536,binding=completion['binding'],expected_frame=completion['frame'])
  finally:os.close(fd)
 def test_actual_unmodified_stream_retains_known_hash_and_original_frame(self):
  root=self.own();path=root/'owned-output';fd=os.open(path,os.O_RDWR|os.O_CREAT|os.O_EXCL,0o600);completion={};row=None
  try:
   self.command(fd,lambda binding,frame:completion.update(binding=binding,frame=frame))
   row=M.hold_written(path,fd,65536,binding=completion['binding'],expected_frame=completion['frame']);self.assertEqual(row.before,completion['frame'])
  finally:os.close(fd)
  M.finish_evidence([row])
 def test_stream_completion_requires_a_target_before_any_process(self):
  with patch.object(M.subprocess,'Popen',side_effect=AssertionError('ambient denied')),self.assertRaisesRegex(RuntimeError,'command_contract'):
   M.command(['OWNED-NOT-EXECUTED'],environment={},stream_complete=lambda *_:None)
if __name__=='__main__':unittest.main()
