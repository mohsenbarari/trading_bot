"""Actual owned retained-FD/fork/session and close-uncertainty regressions."""
from contextlib import ExitStack
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch
import errno,json,os,selectors,signal,stat,subprocess,tempfile,time,unittest
if __package__:
 from . import test_sqlite_vendor_privileges as P
 from . import test_prepare_sqlite_wal_ci_context as F
else:
 import test_sqlite_vendor_privileges as P
 import test_prepare_sqlite_wal_ci_context as F
R=P.R;A=P.A;REAL_OPEN=os.open;REAL_CLOSE=os.close
def leaves(error):
 return sum((leaves(e)for e in error.exceptions),[])if isinstance(error,BaseExceptionGroup)else[error]
def dispose_fixture_scope(r):
 """Independent disposal of this test's failed scope; never qualify its receipt."""
 if r.VENDOR_PROCESS_SCOPE is not None:
  end=time.monotonic()+5
  while r.vendor_process_children():
   assert time.monotonic()<end
   for pid in r.vendor_process_children():
    fd=os.pidfd_open(pid,0)
    try:
     raw=r.vendor_process_bytes('/proc/'+str(pid)+'/status',16384)
     f={line.split(b':',1)[0]:line.split(b':',1)[1].split()for line in raw.splitlines()if b':'in line}
     assert f[b'PPid']==[str(os.getpid()).encode()]and f[b'Uid']==[b'10001']*4
     ready=os.waitid(os.P_PIDFD,fd,os.WEXITED|os.WNOHANG|os.WNOWAIT)
     if ready is None:signal.pidfd_send_signal(fd,signal.SIGKILL,None,0)
     os.waitid(os.P_PIDFD,fd,os.WEXITED)
    finally:REAL_CLOSE(fd)
  r.vendor_process_empty();r.vendor_subreaper(0)
 r.VENDOR_PROCESS_SCOPE=None;r.VENDOR_RETIREMENT_UNPROVEN=False
def fork_program(*,escape=False,code=0,timeout=False,exited=False,ready=None):
 # Child closes inherited pipes, keeps an actual writable source FD, and would
 # modify it after the leader exit unless the entire owned process tree retires.
 return '''import os,time
assert os.getuid()==os.geteuid()==os.getgid()==os.getegid()==10001 and os.getgroups()==[]
fd=os.open('input',os.O_WRONLY|os.O_APPEND)
pid=os.fork()
if pid==0:
 '''+('os.setsid()'if escape else'pass')+'''
 os.close(0);os.close(1);os.close(2)
 '''+('os.close(fd);os._exit(0)'if exited else ("with open('descendant.pid','w')as output:output.write(str(os.getpid()))\n os.write("+str(ready[0])+",str(os.getpid()).encode()+b'\\n');os.close("+str(ready[0])+")\n os.read("+str(ready[1])+",1);os.close("+str(ready[1])+")\n os.write(fd,b'UNSAFE RETAINED WRITE');os.close(fd);os._exit(0)"if ready is not None else"time.sleep(.25);os.write(fd,b'UNSAFE RETAINED WRITE');os.close(fd);os._exit(0)"))+'''
os.close(fd)
'''+("os.close("+str(ready[0])+");os.close("+str(ready[1])+")\n"if ready is not None else"with open('descendant.pid','w')as output:output.write(str(pid))\n")+('time.sleep(.06)\n'if exited else'')+('time.sleep(2)\n'if timeout else'')+"print('0 errors out of 1 tests on OWNED FORK UNIT ONLY',flush=True)\nraise SystemExit("+str(code)+')\n'

@unittest.skipUnless(P.real_uid_namespace(),'actual fork/UID scope requires mapped Root')
class ProcessTreeTests(unittest.TestCase):
 setUp=P.VendorPrivilegeTests.setUp
 configured=P.VendorPrivilegeTests.configured
 restored=P.VendorPrivilegeTests.restored
 def tearDown(self):
  for r in R.values():dispose_fixture_scope(r)
  P.VendorPrivilegeTests.tearDown(self)
 def vector(self,*,escape=False,code=0,timeout=False):
  r=R['debian'];ready=None;readiness=[]
  if timeout:
   ready_read,ready_write=os.pipe2(os.O_CLOEXEC|os.O_NONBLOCK)
   release_read,release_write=os.pipe2(os.O_CLOEXEC)
   for fd in(ready_read,ready_write,release_read,release_write):self.stack.callback(REAL_CLOSE,fd)
   ready=(ready_write,release_read)
  self.configured(r,fork_program(escape=escape,code=code,timeout=timeout,ready=ready));rows=[];original=r.vendor_process_retire;bound=r.bounded_command
  launch=r.subprocess.Popen
  def ready_launch(argv,**kw):
   if not timeout or argv!=r.VENDOR_COMMANDS['vendor-standard.log']:return launch(argv,**kw)
   self.assertNotIn('pass_fds',kw);kw['pass_fds']=ready
   child=launch(argv,**kw)
   try:
    # Popen's owned-fixture startup handoff is bounded separately, INSIDE
    # the existing phase deadline. The production command timer has not
    # started yet; neither it nor the enclosing phase clock is reset.
    end=min(time.monotonic()+3,r.VENDOR_PROCESS_SCOPE['retire_end']);raw=b''
    with selectors.DefaultSelector()as selector:
     selector.register(ready_read,selectors.EVENT_READ)
     while not raw.endswith(b'\n'):
      left=end-time.monotonic();self.assertGreater(left,0,'owned descendant readiness deadline')
      self.assertTrue(selector.select(left),'owned descendant did not become ready')
      part=os.read(ready_read,33-len(raw));self.assertTrue(part);raw+=part
      self.assertLessEqual(len(raw),32)
    self.assertLess(time.monotonic(),end,'owned descendant readiness deadline')
    self.assertTrue(raw[:-1].isdigit());pid=int(raw[:-1]);self.assertGreater(pid,1)
    self.assertEqual((self.src/'descendant.pid').read_text(),str(pid))
    self.assertIsNone(child.poll());readiness.append(pid);return child
   except BaseException:
    # Startup failure cannot loan an untracked Popen: retire the leader,
    # then the unchanged production scope retires any adopted descendant.
    child.kill();child.wait(timeout=3);child.stdout.close();raise
  def capture(*args):row=original(*args);rows.append(row);return row
  def short(argv,**kw):
   if timeout and argv==r.VENDOR_COMMANDS['vendor-standard.log']:kw['timeout']=.05
   return bound(argv,**kw)
  with patch.object(r,'vendor_process_retire',side_effect=capture),patch.object(r,'bounded_command',side_effect=short),patch.object(r.subprocess,'Popen',side_effect=ready_launch):
   with self.assertRaises(BaseException)as caught:r.vendor_unprivileged_commands(self.src)
  self.assertTrue(any(isinstance(e,RuntimeError)for e in leaves(caught.exception)))
  if code:self.assertTrue(any('vendor_test_command_refused'in str(e)for e in leaves(caught.exception)))
  if timeout:self.assertTrue(any('command_timeout'in str(e)for e in leaves(caught.exception)))
  self.restored();self.assertIsNone(r.VENDOR_PROCESS_SCOPE);self.assertFalse(r.VENDOR_RETIREMENT_UNPROVEN);self.assertEqual(r.vendor_subreaper(),0)
  r.vendor_process_empty();events=[e for row in rows for e in row['retired']];self.assertTrue(events)
  child=int((self.src/'descendant.pid').read_text());self.assertIn(child,[e['pid']for e in events]);self.assertTrue(all(e['pidfd_closed']for e in events))
  if timeout:self.assertEqual(readiness,[child])
  time.sleep(.3);self.assertEqual(self.input.read_bytes(),b'OWNED PUBLIC SOURCE')
  self.assertTrue(all(c.returncode is not None and c.stdout.closed for c in P.CHILDREN))
 def test_closed_pipes_retained_source_FD_after_success_cannot_outlive_restore(self):self.vector()
 def test_escaped_session_retained_source_FD_after_success_is_retired_and_phase_fails(self):self.vector(escape=True)
 def test_parent_nonzero_and_escaped_descendant_errors_are_both_retained(self):self.vector(escape=True,code=7)
 def test_parent_timeout_and_original_group_descendant_retire_before_Root_restore(self):self.vector(timeout=True)
 def test_timeout_escaped_session_descendant_uses_owned_pidfd(self):self.vector(timeout=True,escape=True)
 def test_actual_exited_orphan_kernel_receipt_reaches_consumers_all_three_profiles(self):
  import shutil
  for name,r in R.items():
   if (self.work/'vendor-test-home').exists():shutil.rmtree(self.work/'vendor-test-home')
   for leaf in('descendant.pid',):
    if (self.src/leaf).exists():(self.src/leaf).unlink()
   with ExitStack()as local:
    prior=self.stack;self.stack=local
    try:
     self.configured(r,fork_program(exited=True));_,receipt=r.vendor_unprivileged_commands(self.src)
     self.assertEqual(r.vendor_privilege_contract(receipt),receipt);self.restored()
     process=receipt['process_tree'];events=process['commands']['vendor-standard.log']['retired'];self.assertTrue(events)
     self.assertTrue(all(e['signal']==0 and e['wait']['si_status']==0 and e['pidfd_closed']for e in events))
     # Pass the actual kernel/identity/loan receipt to the artifact consumer.
     # Only native Python/cmd source-fixture bindings are explicitly overridden;
     # synthetic package/log/ABI rows never claim a real native build.
     if name=='debian':
      with patch.object(F.R,'WORK',self.work),patch.object(F.R,'PYTHON','/usr/bin/python3',create=True),patch.object(F.R,'VENDOR_COMMANDS',r.VENDOR_COMMANDS):
       rows,proofs=F.fixtures();rows['provenance.json']['vendor_tests']['unprivileged_execution']=receipt
       self.assertTrue(F.A.verify_artifacts(F.R,rows,proofs)['native_abi_checked'])
     bad=deepcopy(process);bad['commands']['vendor-standard.log']['retired'][0]['pidfd_closed']=False
     with self.assertRaises(RuntimeError):r.vendor_process_contract(bad)
    finally:self.stack=prior
 def test_required_scope_types_owned_parent_and_all_events_are_joined(self):
  r=R['debian'];self.configured(r,fork_program(exited=True));_,receipt=r.vendor_unprivileged_commands(self.src);genuine=receipt['process_tree']
  mutations=[lambda p:p.__setitem__('original_subreaper',True),lambda p:p.__setitem__('final_subreaper',False),
   lambda p:p.__setitem__('initial_thread_ids',[True]),lambda p:p.__setitem__('final_child_pids',[123]),
   lambda p:p.__setitem__('signal_scope','host_UID_scan'),lambda p:p['commands'].__delitem__('identity'),
   lambda p:p['commands']['vendor-standard.log']['retired'][0].__setitem__('ppid',123),
   lambda p:p['commands']['vendor-standard.log']['retired'][0].__setitem__('signal',9),
   lambda p:p['commands']['vendor-standard.log']['retired'][0]['wait'].__setitem__('si_uid',0)]
  for mutate in mutations:
   value=deepcopy(genuine);mutate(value)
   with self.assertRaises(RuntimeError):r.vendor_process_contract(value)
  missing=deepcopy(receipt);del missing['process_tree']
  with self.assertRaises(RuntimeError):r.vendor_privilege_contract(missing)
 def test_unknown_signal_blocks_Root_restore_and_does_not_heal(self):
  r=R['debian'];self.configured(r,fork_program(escape=True))
  with patch.object(r.signal,'pidfd_send_signal',side_effect=PermissionError('OWNED PIDFD DENIAL')),self.assertRaises(BaseException):r.vendor_unprivileged_commands(self.src)
  self.assertTrue(r.VENDOR_RETIREMENT_UNPROVEN);self.assertIsNotNone(r.VENDOR_PROCESS_SCOPE);self.assertEqual(self.input.stat().st_uid,10001)
  self.assertEqual(stat.S_IMODE(self.work.stat().st_mode),0o700)
 def test_uncertain_pidfd_close_latches_no_Root_restore_even_after_child_reaped(self):
  r=R['debian'];self.configured(r,fork_program(exited=True));pidfds=set();count=0;realpidfd=os.pidfd_open
  def opened(*args):fd=realpidfd(*args);pidfds.add(fd);return fd
  def uncertain(fd):
   nonlocal count
   if fd in pidfds:
    count+=1;REAL_CLOSE(fd);raise OSError(errno.EIO,'OWNED PIDFD CLOSE AFTER PHYSICAL')
   return REAL_CLOSE(fd)
  with patch.object(r.os,'pidfd_open',side_effect=opened),patch.object(r.os,'close',side_effect=uncertain),self.assertRaises(BaseException):r.vendor_unprivileged_commands(self.src)
  self.assertEqual(count,1);self.assertTrue(r.VENDOR_RETIREMENT_UNPROVEN);self.assertEqual(self.input.stat().st_uid,10001)
 def test_existing_unrelated_owned_child_is_neither_adopted_nor_killed(self):
  r=R['debian'];self.configured(r)
  child=P.REAL_POPEN(['/usr/bin/python3','-I','-B','-c','import time;time.sleep(2)'],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
   user=10001,group=10001,extra_groups=[],start_new_session=True,env={'PATH':'/usr/bin:/bin'})
  try:
   with self.assertRaises(BaseException):r.vendor_unprivileged_commands(self.src)
   self.assertIsNone(child.poll());self.assertEqual(r.vendor_subreaper(),0);self.assertEqual(self.input.stat().st_uid,0)
  finally:child.kill();child.wait(timeout=2);r.VENDOR_RETIREMENT_UNPROVEN=False
 def test_dropped_command_refuses_before_spawn_without_armed_local_scope(self):
  r=R['debian'];self.configured(r);before=len(P.CHILDREN)
  with self.assertRaisesRegex(RuntimeError,'vendor_process_scope_required'):
   r.bounded_command(['/usr/bin/python3','-I','-B','-c','print(1)'],cwd=self.src,timeout=1,maximum=4096,capture=True,unprivileged=True)
  self.assertEqual(len(P.CHILDREN),before);self.assertEqual(self.input.stat().st_uid,0)

class CloseAttemptTests(unittest.TestCase):
 def setUp(self):
  self.stack=ExitStack();self.addCleanup(self.stack.close)
  self.root=Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='.owned-close-',dir=P.ROOT/'tests')))
  self.path=self.root/'source';self.path.write_bytes(b'OWNED PUBLIC SOURCE');self.path.chmod(0o600)
  self.other=self.root/'foreign';self.other.write_bytes(b'OWNED FOREIGN FD FIXTURE');self.other.chmod(0o600)
 def fault(self,mode,*,samepath=False):
  for name,a in A.items():
   stack=ExitStack();original_callback=stack.callback;saved=[];attempts=0;finalized=len(a.FINALIZED)
   def opened(*args,**kwargs):fd=REAL_OPEN(*args,**kwargs);saved.append(fd);return fd
   def register(*args,**kwargs):original_callback(*args,**kwargs);raise OSError(errno.EPERM,'OWNED REGISTER AFTER')
   def close(fd):
    nonlocal attempts
    if saved and fd==saved[0]:
     attempts+=1
     if mode=='before':raise OSError(errno.EIO,'OWNED CLOSE BEFORE PHYSICAL')
     if mode=='noop':return
     REAL_CLOSE(fd)
     if mode=='after':raise OSError(errno.EIO,'OWNED CLOSE AFTER PHYSICAL')
     return
    return REAL_CLOSE(fd)
   with patch.object(a.os,'open',side_effect=opened),patch.object(a.os,'close',side_effect=close),patch.object(stack,'callback',side_effect=register):
    with self.assertRaises(BaseException)as primary:a.held(self.path,stack,[],uid=os.geteuid())
   self.assertTrue(any(isinstance(e,OSError)and e.errno==errno.EPERM for e in leaves(primary.exception)))
   target=saved[0]
   if mode in('before','noop'):
    self.assertEqual(os.fstat(target).st_ino,self.path.stat().st_ino);REAL_CLOSE(target) # independent owned-fixture disposal only
   victim=REAL_OPEN(self.path if samepath else self.other,os.O_RDONLY|os.O_CLOEXEC);self.assertEqual(victim,target)
   before=os.fstat(victim)
   try:
    with patch.object(a.os,'close',side_effect=close):
     if mode=='clean':stack.close()
     else:
      with self.assertRaises(BaseException):stack.close()
    self.assertEqual(os.fstat(victim),before);self.assertEqual(attempts,1)
    if mode!='clean':self.assertEqual(len(a.FINALIZED),finalized)
   finally:REAL_CLOSE(victim)
 def test_clean_close_callback_never_retries_reused_foreign_FD(self):self.fault('clean')
 def test_close_raises_before_physical_disposal_is_uncertain_and_never_retried(self):self.fault('before')
 def test_close_physically_closes_then_raises_never_closes_foreign_reuse(self):self.fault('after')
 def test_close_physically_closes_then_raises_never_closes_same_path_reopen(self):self.fault('after',samepath=True)
 def test_noop_close_stays_failed_and_never_retries_numeric_FD(self):self.fault('noop')

if __name__=='__main__':unittest.main()
