"""Genuine inert children/owned memfds only; no Docker or operational inputs."""
from pathlib import Path
from types import ModuleType
from unittest.mock import patch
import json,os,subprocess,sys,tempfile,unittest
ROOT=Path(__file__).resolve().parents[1];P=ROOT/'scripts/prepare_canonical_app_image.py'
M=ModuleType('_owned_app_diagnostic');M.__file__=str(P);exec(compile(P.read_bytes(),str(P),'exec'),M.__dict__)
class BuildOutputTests(unittest.TestCase):
 def setUp(self):
  t=tempfile.TemporaryDirectory(dir=Path(__file__).parent);self.addCleanup(t.cleanup);self.root=Path(t.name);(self.root/'reports').mkdir(mode=0o700)
 def args(self,code):return[sys.executable,'-I','-S','-B','-c',code]
 def report(self):return json.loads((self.root/'reports/docker-build-output.json').read_bytes())
 def test_default_stderr_contract_and_caller_owned_target_fd_are_preserved(self):
  code="import os;os.write(1,b'OUT');os.write(2,b'ERR')"
  self.assertEqual(M.command(self.args(code),maximum=128,seconds=2,environment=M.env(self.root)),b'OUT')
  fd=os.memfd_create('owned-output',os.MFD_CLOEXEC)
  try:
   self.assertIsNone(M.command(self.args(code),maximum=128,seconds=2,environment=M.env(self.root),target=fd,combine_stderr=True))
   self.assertEqual(os.pread(fd,128,0),b'OUTERR');self.assertGreater(os.fstat(fd).st_size,0)
  finally:os.close(fd)
 def test_actual_success_saves_both_streams_without_acceptance(self):
  M.build_output(self.root,self.args("import os;os.write(1,b'OUT\\n');os.write(2,b'ERR\\n')"),seconds=2,maximum=128)
  v=self.report();self.assertEqual(v['output_prefix'],'OUT\nERR\n');self.assertEqual(v['actual_command_exit'],0)
  self.assertTrue(v['complete_output']);self.assertFalse(v['output_truncated']);self.assertFalse(v['build_accepted']);self.assertFalse(v['security_accepted'])
 def test_true_nonzero_is_saved_and_same_command_failure_survives_no_postreap_signal(self):
  with patch.object(M.os,'killpg',side_effect=AssertionError('postreap signal denied')),self.assertRaisesRegex(RuntimeError,'^command_failed$'):
   M.build_output(self.root,self.args("import os;os.write(2,b'OWNED FAILURE');raise SystemExit(7)"),seconds=2,maximum=128)
  v=self.report();self.assertEqual(v['actual_command_exit'],7);self.assertEqual(v['output_prefix'],'OWNED FAILURE')
 def test_output_limit_is_enforced_before_read_and_prefix_is_truthfully_truncated(self):
  with self.assertRaisesRegex(RuntimeError,'^command_output_bound$'):
   M.build_output(self.root,self.args("import os;os.write(1,b'X'*4096)"),seconds=2,maximum=32)
  v=self.report();self.assertEqual(v['actual_command_exit'],'UNKNOWN');self.assertLessEqual(v['observed_bytes'],33)
  self.assertEqual(v['captured_bytes'],32);self.assertEqual(v['output_prefix'],'X'*32);self.assertTrue(v['output_truncated']);self.assertFalse(v['complete_output'])
 def test_large_actual_output_retains_final_error_with_two_bounded_nonoverlap_reads(self):
  actual=os.pread;reads=[];ending='FINAL_OWNED_DOCKER_ERROR'
  def read(fd,count,offset):reads.append((count,offset));return actual(fd,count,offset)
  with patch.object(M.os,'pread',side_effect=read),self.assertRaisesRegex(RuntimeError,'^command_failed$'):
   M.build_output(self.root,self.args("import os;os.write(1,b'P'*400000);os.write(2,b'FINAL_OWNED_DOCKER_ERROR');raise SystemExit(7)"),seconds=2)
  v=self.report();self.assertTrue(v['output_tail'].endswith(ending));self.assertEqual(v['actual_command_exit'],7)
  self.assertEqual(reads,[(128*1024,0),(128*1024,v['observed_bytes']-128*1024)])
  self.assertEqual(v['prefix_offset'],0);self.assertEqual(v['tail_offset'],v['observed_bytes']-128*1024)
  self.assertGreater(v['tail_offset'],v['prefix_bytes']);self.assertEqual(v['captured_bytes'],256*1024)
  self.assertTrue(v['output_truncated']);self.assertFalse(v['complete_output'])
 def test_timeout_never_turns_cleanup_returncode_into_actual_build_exit(self):
  with self.assertRaisesRegex(RuntimeError,'^command_timeout$'):
   M.build_output(self.root,self.args('import time;time.sleep(2)'),seconds=.02,maximum=128)
  self.assertEqual(self.report()['actual_command_exit'],'UNKNOWN');self.assertFalse(self.report()['complete_output'])
 def test_exclusive_report_failure_never_masks_primary_command_error(self):
  path=self.root/'reports/docker-build-output.json';path.write_bytes(b'OWNED ORIGINAL')
  with self.assertRaisesRegex(RuntimeError,'^command_failed$'):
   M.build_output(self.root,self.args('raise SystemExit(17)'),seconds=2,maximum=128)
  self.assertEqual(path.read_bytes(),b'OWNED ORIGINAL')
if __name__=='__main__':unittest.main()
