"""Genuine owned inert-child diagnostics; no image/hosted/Docker operation."""
from pathlib import Path
from types import ModuleType,SimpleNamespace
from unittest.mock import patch
import json,os,selectors,subprocess,sys,tempfile,time,unittest
HERE=Path(__file__).resolve().parent

def load(path,name):
 m=ModuleType(name);m.__file__=str(path);exec(compile(path.read_bytes(),str(path),'exec'),m.__dict__);return m
C=load(HERE/'controller.py','probe_controller')
CID='a'*64
class Tests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(dir=HERE);self.root=Path(self.tmp.name);(self.root/'reports').mkdir(mode=0o700)
  self.B=load(HERE/'compose_security_os_candidate.py','probe_keeper');self.keeper=self.B.ProofKeeper(self.root);self.B.PROOF_KEEPER=self.keeper
  self.ctx=SimpleNamespace(root=self.root,end=time.monotonic()+10,C=self.B,check=self.keeper.check)
  self.old=SimpleNamespace(DOCKER=['/usr/bin/docker','--host=unix:///var/run/docker.sock']);self.children=[]
 def tearDown(self):
  try:
   if not self.keeper.closed:
    try:self.keeper.close()
    except RuntimeError:pass # Only deliberately refused, owned mutation fixtures.
   for child in self.children:
    self.assertIsNotNone(child.returncode)
    with self.assertRaises(ChildProcessError):os.waitpid(child.pid,os.WNOHANG)
  finally:self.tmp.cleanup()
 def path(self,kind='probe_native_payload.py'):return self.root/'reports'/('public-probe-start-'+kind[:-3]+'.json')
 def spawn(self,body,after=None):
  actual=subprocess.Popen;outer=self
  class Pipe:
   def __init__(self,pipe,label):self.pipe=pipe;self.label=label
   def fileno(self):return self.pipe.fileno()
   def close(self):
    self.pipe.close()
    if after:after(self.label)
  def launch(argv,**kwargs):
   outer.assertEqual(argv,[*self.old.DOCKER,'start','--attach',CID]);outer.assertEqual(set(kwargs['env']),{'PATH','LC_ALL','DOCKER_CONFIG'})
   outer.assertTrue(kwargs['close_fds']);outer.assertTrue(kwargs['start_new_session'])
   child=actual([sys.executable,'-I','-S','-B','-c',body],**kwargs);self.children.append(child)
   child.stdout=Pipe(child.stdout,'stdout');child.stderr=Pipe(child.stderr,'stderr');return child
  return patch.object(C.subprocess,'Popen',side_effect=launch)
 def test_nonzero_safe_refusal_is_retained_before_selector_and_both_pipes_close(self):
  events=[];actual=selectors.DefaultSelector.close
  def check_close(label):
   self.assertTrue(self.path().exists());self.assertTrue(self.keeper.records);self.keeper.check();events.append(label)
  def close(selector):check_close('selector');actual(selector)
  body='import os;os.write(1,b\'{"status":"REFUSED","code":"native_parent"}\\n\');os.write(2,b"owned public stderr\\n");raise SystemExit(2)'
  with self.spawn(body,check_close),patch.object(C.selectors.DefaultSelector,'close',new=close),self.assertRaisesRegex(RuntimeError,'public_probe_start_nonzero'):
   C.public_probe_start(self.ctx,self.old,CID,'probe_native_payload.py')
  r=json.loads(self.path().read_bytes());self.assertEqual(r['actual_client_returncode'],2);self.assertIn('native_parent',r['stdout_prefix_text']);self.assertEqual(r['stderr_text'],'owned public stderr\n');self.assertEqual(events,['selector','stdout','stderr']);self.assertTrue(r['capture_before_collector_physical_closes'])
 def test_all_three_fixed_probes_return_unchanged_full_success_bytes(self):
  body='import os;os.write(1,b\'{"owned_public":true}\\n\')'
  for kind in C.PUBLIC_PROBE_LIMITS:
   with self.subTest(kind=kind),self.spawn(body):self.assertEqual(C.public_probe_start(self.ctx,self.old,CID,kind),b'{"owned_public":true}\n')
   r=json.loads(self.path(kind).read_bytes());self.assertEqual(r['actual_client_returncode'],0);self.assertEqual(r['closed_code'],'success');self.assertFalse(r['stdout_truncated'])
 def test_prefix_tail_are_bounded_and_middle_truncation_is_honest(self):
  body='import os;os.write(1,b"PREFIX"+b"X"*10000+b"TAIL")'
  with self.spawn(body):result=C.public_probe_start(self.ctx,self.old,CID,'probe_native_payload.py')
  r=json.loads(self.path().read_bytes());self.assertEqual(len(result),10010);self.assertLessEqual(len(r['stdout_prefix_text']),4096);self.assertLessEqual(len(r['stdout_tail_text']),4096);self.assertTrue(r['stdout_truncated']);self.assertTrue(r['stdout_tail_text'].endswith('TAIL'));self.assertEqual(r['stdout_tail_offset'],len(result)-4096)
 def test_stdout_and_stderr_overflow_refuse_with_capped_capture_and_reaped_child(self):
  for stream in(1,2):
   # Separate fixed probe names keep every failed attempt's EXCL receipt.
   kind='verify_image_component_presence.py'if stream==1 else'verify_acl_consumer_presence.py';maximum=C.PUBLIC_PROBE_LIMITS[kind]if stream==1 else 8192
   body='import os,time;os.write('+str(stream)+',b"X"*'+str(maximum+1024)+');time.sleep(10)'
   with self.subTest(stream=stream),self.spawn(body),self.assertRaisesRegex(RuntimeError,'public_probe_stdout_bound|public_probe_stderr_bound'):C.public_probe_start(self.ctx,self.old,CID,kind)
   r=json.loads(self.path(kind).read_bytes());self.assertLessEqual(r['stdout_bytes_captured'],C.PUBLIC_PROBE_LIMITS[kind]);self.assertLessEqual(len(r['stderr_text']),8192);self.assertIsInstance(r['actual_client_returncode'],int)
 def test_timeout_has_actual_outcome_and_capture_without_source_or_config_dump(self):
  self.ctx.end=time.monotonic()+.1
  with self.spawn('import time;time.sleep(10)'),self.assertRaisesRegex(RuntimeError,'public_probe_start_timeout'):C.public_probe_start(self.ctx,self.old,CID,'probe_native_payload.py')
  r=json.loads(self.path().read_bytes());self.assertIsInstance(r['actual_client_returncode'],int);self.assertEqual(r['closed_code'],'public_probe_start_timeout');self.assertNotIn('env',r);self.assertNotIn('argv',r)
 def test_unknown_contracts_refuse_before_child_or_diagnostic_adoption(self):
  for kind,cid in(('unknown.py',CID),('probe_native_payload.py','not-a-cid')):
   with self.subTest(kind=kind,cid=cid),patch.object(C.subprocess,'Popen',side_effect=AssertionError('ambient denied')),self.assertRaisesRegex(RuntimeError,'public_probe_start_contract'):C.public_probe_start(self.ctx,self.old,cid,kind)
  self.assertEqual(list((self.root/'reports').iterdir()),[])
 def test_real_diagnostic_mutation_during_pipe_close_refuses_after_all_closes(self):
  changed=[]
  def after(label):
   if not changed:self.path().write_bytes(b'{"owned_changed":true}\n');changed.append(label)
  with self.spawn('print("owned public")',after),self.assertRaisesRegex(RuntimeError,'proof_changed'):C.public_probe_start(self.ctx,self.old,CID,'probe_native_payload.py')
  self.assertEqual(changed,['stdout']);self.assertTrue(all(child.stdout.pipe.closed and child.stderr.pipe.closed for child in self.children))
 def test_unknown_private_exception_operand_is_not_serialized_even_if_unhashable(self):
  calls=[]
  def check():
   if not calls:calls.append(1);raise RuntimeError(['OWNED_PRIVATE_EXCEPTION_SENTINEL'])
   self.keeper.check()
  self.ctx.check=check
  with self.assertRaises(RuntimeError):C.public_probe_start(self.ctx,self.old,CID,'probe_native_payload.py')
  raw=self.path().read_bytes();self.assertNotIn(b'OWNED_PRIVATE_EXCEPTION_SENTINEL',raw);self.assertEqual(json.loads(raw)['closed_code'],'unclassified');self.assertEqual(self.children,[])
if __name__=='__main__':unittest.main()
