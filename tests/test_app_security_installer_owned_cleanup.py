"""Actual owned inert fork/wait/signal tests; no package or privilege claims."""
from pathlib import Path
from types import ModuleType
from unittest.mock import patch
import ast,os,signal,time,unittest
ROOT=Path(__file__).resolve().parents[1]
P=ROOT/'scripts/prepare_app_security_os_cache.py';M=ModuleType('_owned_legacy_cleanup');M.__file__=str(P);exec(compile(P.read_bytes(),str(P),'exec'),M.__dict__)

class OwnedLegacyCleanupTests(unittest.TestCase):
 def run_owned(self,raw,*,wall=2,interrupted=False):
  fork=os.fork;kill=os.kill;killpg=os.killpg;waitpid=os.waitpid;spawned=[];signals=[];reaped=False
  def actual_fork():
   pid=fork()
   if pid>0:spawned.append(pid)
   return pid
  def signal_owned(pid,sig,group=False):
   self.assertIn(pid,spawned)
   # This real kernel check must succeed BEFORE every actual signal.
   os.waitid(os.P_PID,pid,os.WEXITED|os.WNOHANG|os.WNOWAIT)
   signals.append((pid,sig,group));return killpg(pid,sig) if group else kill(pid,sig)
  def actual_waitpid(pid,flags):
   nonlocal reaped
   result=waitpid(pid,flags)
   if interrupted and pid>0 and result[0]==pid and not reaped:
    reaped=True;raise InterruptedError('OWNED_WAIT_REAPED')
   return result
  with patch.object(M.os,'fork',actual_fork),patch.object(M.os,'kill',lambda pid,sig:signal_owned(pid,sig)),patch.object(M.os,'killpg',lambda pid,sig:signal_owned(pid,sig,True)),patch.object(M.os,'waitpid',actual_waitpid),patch.object(M,'INSTALL_WALL_SECONDS',wall):
   try:M.execute_installer(raw)
   finally:
    self.assertEqual(len(spawned),1)
    with self.assertRaises(ChildProcessError):os.waitid(os.P_PID,spawned[0],os.WEXITED|os.WNOHANG|os.WNOWAIT)
  return signals
 def test_actual_successful_reap_never_signals_pid_or_group(self):self.assertEqual(self.run_owned(b'pass'),[])
 def test_interrupted_wait_after_kernel_reap_never_signals_recycled_identity(self):
  with self.assertRaisesRegex(InterruptedError,'OWNED_WAIT_REAPED'):self.run_owned(b'pass',interrupted=True)
 def test_live_timeout_sends_only_owned_signal_and_reaps(self):
  with self.assertRaisesRegex(RuntimeError,'installer_timeout'):self.run_owned(b'import time;time.sleep(2)',wall=.05)
 def test_sigterm_ignoring_owned_child_is_killed_and_reaped(self):
  raw=b'import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(5)'
  with self.assertRaisesRegex(RuntimeError,'installer_timeout'):self.run_owned(raw,wall=.1)
 def test_kernel_already_reaped_child_is_refused_before_signal(self):
  pid=os.fork()
  if pid==0:os._exit(0)
  os.waitpid(pid,0)
  with patch.object(M.os,'kill',side_effect=AssertionError('unexpected signal')),patch.object(M.os,'killpg',side_effect=AssertionError('unexpected signal')):
   self.assertFalse(M.signal_owned_installer_child(pid,signal.SIGKILL,group=True))
 def test_existing_fifteen_lock_and_all_parent_signals_remain_ownership_bound(self):
  from hashlib import sha256
  lock=ROOT/'scripts/app_security_os_packages.lock.json'
  self.assertEqual(sha256(lock.read_bytes()).hexdigest(),M.LOCK_SHA)
  self.assertEqual(M.LOCK_SHA,'86d3fc5098a24320dfd7aad67a1df7d671e2bf1bdec2e5c0a66a308eb638f9f9')
  tree=ast.parse(P.read_bytes());parent=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='execute_installer')
  self.assertFalse(any(isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr in {'kill','killpg'} for n in ast.walk(parent)))
  helper=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='signal_owned_installer_child')
  self.assertTrue(any(isinstance(n,ast.Attribute) and n.attr=='WNOWAIT' for n in ast.walk(helper)))


if __name__=='__main__':unittest.main()
