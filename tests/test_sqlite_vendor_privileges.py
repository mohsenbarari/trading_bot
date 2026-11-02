"""Real UID-drop/owned-tree and acquisition-prefix controls; no SQL or build."""
from contextlib import ExitStack
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from types import ModuleType
from unittest.mock import patch
import ast,errno,fcntl,json,os,signal,stat,subprocess,tempfile,time,unittest
ROOT=Path(__file__).absolute().parent.parent
def load(path):
 m=ModuleType('_owned_privilege_'+path.stem+'_'+path.parent.name);m.__file__=str(path)
 raw=path.read_bytes();assert 0<len(raw)<=131072
 exec(compile(raw,str(path),'exec'),m.__dict__);return m
R={name:load(ROOT/'deploy/security'/suffix/'sourcebuild.py')for name,suffix in(
 ('debian',Path('sqlite-wal-backport')),('noble',Path('sqlite-ubuntu-gold2/noble')),('resolute',Path('sqlite-ubuntu-gold2/resolute')))}
A={name:load(ROOT/'scripts'/leaf)for name,leaf in(('debian','prepare_sqlite_wal_ci_context.py'),('ubuntu','prepare_sqlite_ubuntu_gold2_ci_context.py'))}
REAL_POPEN=subprocess.Popen
CHILDREN=[]
def ordinary_program(text):return "import os,sys; assert os.getuid()==os.geteuid()==os.getgid()==os.getegid()==10001 and os.getgroups()==[]; "+text
def phase_fixture():
 observed={'uid':10001,'euid':10001,'gid':10001,'egid':10001,'groups':[],'home':'/build/sqlite-wal-backport/vendor-test-home',
  'home_mode':0o700,'home_uid':10001,'home_gid':10001,'home_writable':True,'source_writable':True}
 r=R['debian'];raw=r.encoded(observed);inventory=b'OWNED SYNTHETIC UNIT TREE ONLY\n'
 bound={'bytes':len(inventory),'sha256':sha256(inventory).hexdigest()}
 return{'schema':'sqlite_vendor_unprivileged_phase/1','identity_probe':{'argv':r.vendor_identity_argv(),'actual_returncode':0,'observed':observed,'raw_output':{'bytes':len(raw),'sha256':sha256(raw).hexdigest()}},
  'initial_tree':dict(bound),'restored_tree':dict(bound),'initial_entries':1,'final_entries':1,'all_original_bindings_and_modes_restored':True,
  'all_final_owners_root':True,'no_symlink_targets_followed':True,'work_mode_before':0o700,'work_mode_during':0o711,'work_mode_restored':0o700,
  'private_home_root_restored':True,'phase_seconds_limit':1560,'restore_reserve_seconds':60,'process_tree':{'OWNED_UNIT_ONLY':True},'security_accepted':False}

def real_uid_namespace():
 if os.geteuid()!=0 or os.getegid()!=0:return False
 with open('/proc/self/uid_map','rb')as stream:raw=stream.read(4097)
 if len(raw)>4096:return False
 return any(int(parts[0])<=10001<int(parts[0])+int(parts[2])for parts in(row.split()for row in raw.splitlines())if len(parts)==3)
@unittest.skipUnless(real_uid_namespace(),'actual UID10001 phase requires Root in a namespace mapping that UID')
class VendorPrivilegeTests(unittest.TestCase):
 def setUp(self):
  self.stack=ExitStack();self.addCleanup(self.stack.close)
  self.stack.enter_context(patch.dict(os.environ,{},clear=True))
  self.top=Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='owned-sqlite-vendor-',dir='/tmp')));self.top.chmod(0o711)
  self.work=self.top/'work';self.work.mkdir(mode=0o700);self.src=self.work/'source';self.src.mkdir(mode=0o755)
  self.input=self.src/'input';self.input.write_bytes(b'OWNED PUBLIC SOURCE');self.input.chmod(0o600)
  self.external=self.top/'outside';self.external.write_bytes(b'OWNED OUTSIDE TARGET');self.external.chmod(0o600)
  (self.src/'outside-link').symlink_to(self.external)
  self.before_outside=R['debian'].identity(self.external.lstat());self.before_input=(self.input.stat().st_ino,self.input.stat().st_mode)
  for name in('subprocess.run','socket.socket','urllib.request.urlopen','os.system','os.popen'):
   self.stack.enter_context(patch(name,side_effect=AssertionError('no ambient operation')))
  def permitted(argv,*args,**kw):
   assert type(argv)is list and argv[:4]==['/usr/bin/python3','-I','-B','-c']
   assert kw.get('user')==kw.get('group')==10001 and kw.get('extra_groups')==[] and kw.get('start_new_session')is True
   assert Path(kw['cwd']).is_relative_to(self.top)and kw['env']['HOME']==str(self.work/'vendor-test-home')
   assert 'sqlite3'not in argv[4]and 'socket'not in argv[4]
   child=REAL_POPEN(argv,*args,**kw);CHILDREN.append(child);return child
  self.stack.enter_context(patch.object(subprocess,'Popen',side_effect=permitted))
 def configured(self,r,standard=None):
  self.stack.enter_context(patch.object(r,'WORK',self.work))
  self.stack.enter_context(patch.object(r,'PYTHON','/usr/bin/python3',create=True))
  self.stack.enter_context(patch.object(r,'forward',side_effect=lambda raw,end:None))
  std=ordinary_program("print('0 errors out of 1 tests on OWNED DROP UID UNIT ONLY')")if standard is None else standard
  reg=ordinary_program("print('0 errors out of 1 tests on OWNED DROP UID UNIT ONLY')")
  self.stack.enter_context(patch.object(r,'VENDOR_COMMANDS',{'vendor-standard.log':['/usr/bin/python3','-I','-B','-c',std],
   'vendor-fts5-regression.log':['/usr/bin/python3','-I','-B','-c',reg]}))
 def restored(self):
  self.assertEqual(stat.S_IMODE(self.work.stat().st_mode),0o700)
  for path in(self.src,self.input,self.work/'vendor-test-home',self.src/'outside-link'):
   s=path.lstat();self.assertEqual((s.st_uid,s.st_gid),(0,0))
  self.assertEqual((self.input.stat().st_ino,self.input.stat().st_mode),self.before_input)
  self.assertEqual(R['debian'].identity(self.external.lstat()),self.before_outside)
 def test_actual_identity_private_HOME_modes_and_Root_restore_each_profile(self):
  for name,r in R.items():
   if name!='debian':
    # Each profile gets a fresh phase, never reuse its once-only Home.
    import shutil
    shutil.rmtree(self.work/'vendor-test-home')
   with ExitStack()as local:
    old=self.stack;self.stack=local;self.configured(r)
    try:
     logs,receipt=r.vendor_unprivileged_commands(self.src);self.assertEqual(set(logs),set(r.VENDOR_COMMANDS))
     self.assertEqual(r.vendor_privilege_contract(receipt),receipt);self.assertEqual(receipt['identity_probe']['observed']['groups'],[])
     self.assertTrue(receipt['identity_probe']['observed']['home_writable']);self.restored()
    finally:self.stack=old
 def test_test_failure_remains_failure_after_clean_Root_restoration(self):
  r=R['debian'];self.configured(r,ordinary_program('raise SystemExit(7)'))
  with self.assertRaisesRegex(RuntimeError,'vendor_test_command_refused'):r.vendor_unprivileged_commands(self.src)
  self.restored()
 def test_actual_dropped_child_timeout_is_reaped_and_ownership_restored(self):
  r=R['debian'];self.configured(r,ordinary_program('import time; time.sleep(2)'))
  original=r.bounded_command
  def shortened(argv,**kw):
   if argv==r.VENDOR_COMMANDS['vendor-standard.log']:kw['timeout']=0.05
   return original(argv,**kw)
  with patch.object(r,'bounded_command',side_effect=shortened),self.assertRaisesRegex(RuntimeError,'command_timeout'):r.vendor_unprivileged_commands(self.src)
  self.restored();self.assertIsNotNone(CHILDREN[-1].returncode);self.assertTrue(CHILDREN[-1].stdout.closed)
 def test_uncertain_dropped_child_cleanup_blocks_Root_tree_restore(self):
  r=R['debian'];self.configured(r,ordinary_program('import time; time.sleep(2)'));original=r.bounded_command;cleanup=r.cleanup_owned
  def shortened(argv,**kw):
   if argv==r.VENDOR_COMMANDS['vendor-standard.log']:kw['timeout']=0.05
   return original(argv,**kw)
  try:
   with patch.object(r,'bounded_command',side_effect=shortened),patch.object(r,'cleanup_owned',side_effect=PermissionError('OWNED KILL DENIAL')),patch.object(r.signal,'pidfd_send_signal',side_effect=PermissionError('OWNED PIDFD KILL DENIAL')),self.assertRaises(BaseExceptionGroup):r.vendor_unprivileged_commands(self.src)
   self.assertTrue(r.VENDOR_RETIREMENT_UNPROVEN);self.assertEqual(self.input.stat().st_uid,10001)
   self.assertEqual(stat.S_IMODE(self.work.stat().st_mode),0o700)
  finally:
   if CHILDREN[-1].returncode is None:cleanup(CHILDREN[-1])
   r.vendor_process_empty();r.vendor_subreaper(0);r.VENDOR_PROCESS_SCOPE=None;r.VENDOR_RETIREMENT_UNPROVEN=False
 def test_partial_ownership_loan_failure_restores_only_owned_tree_and_Home(self):
  r=R['debian'];self.configured(r);real=os.chown;count=0
  def fail_once(*a,**kw):
   nonlocal count
   count+=1
   if count==2:raise OSError(errno.EIO,'OWNED LOAN PREFIX INJECTION')
   return real(*a,**kw)
  with patch.object(r.os,'chown',side_effect=fail_once),self.assertRaises(OSError):r.vendor_unprivileged_commands(self.src)
  self.restored()
 def test_preexisting_mode_mutation_is_detected_and_never_chmod_masked(self):
  r=R['debian'];self.configured(r,ordinary_program("os.chmod('input',0o644); print('owned changed mode')"))
  with self.assertRaises(RuntimeError):r.vendor_unprivileged_commands(self.src)
  self.assertEqual(stat.S_IMODE(self.input.stat().st_mode),0o644);self.assertEqual(stat.S_IMODE(self.work.stat().st_mode),0o700)
  self.assertEqual(R['debian'].identity(self.external.lstat()),self.before_outside)
 def test_external_hardlink_refuses_before_loan_or_a_child(self):
  r=R['debian'];self.configured(r);os.link(self.external,self.src/'outside-hardlink');count=len(CHILDREN)
  with self.assertRaisesRegex(RuntimeError,'vendor_tree_hardlink'):r.vendor_unprivileged_commands(self.src)
  self.assertEqual(len(CHILDREN),count);self.assertEqual((self.external.stat().st_uid,self.external.stat().st_gid),(0,0))
  self.assertEqual(stat.S_IMODE(self.work.stat().st_mode),0o700)
 def test_identity_receipt_cannot_forge_uid_groups_home_or_Root_restoration(self):
  r=R['debian'];self.configured(r);_,genuine=r.vendor_unprivileged_commands(self.src)
  for field,value in(('uid',0),('euid',0),('gid',0),('egid',0),('groups',[0]),('home_writable',False),('home_mode',0o755)):
   receipt=deepcopy(genuine);receipt['identity_probe']['observed'][field]=value;raw=r.encoded(receipt['identity_probe']['observed'])
   receipt['identity_probe']['raw_output']={'bytes':len(raw),'sha256':sha256(raw).hexdigest()}
   with self.assertRaises(RuntimeError):r.vendor_privilege_contract(receipt)
  for field in('all_original_bindings_and_modes_restored','all_final_owners_root','private_home_root_restored'):
   receipt=deepcopy(genuine);receipt[field]=False
   with self.assertRaises(RuntimeError):r.vendor_privilege_contract(receipt)
 def test_shared_helper_functions_and_unchanged_commands_match_all_three_profiles(self):
  names=('vendor_identity_argv','vendor_privilege_contract','vendor_close_fd','vendor_tree','vendor_unprivileged_commands')
  funcs=[]
  for name,r in R.items():
   tree=ast.parse(Path(r.__file__).read_bytes());funcs.append({n.name:ast.dump(n,include_attributes=False)for n in tree.body if isinstance(n,ast.FunctionDef)and n.name in names})
   self.assertEqual(r.VENDOR_COMMANDS,{'vendor-standard.log':['/usr/bin/make','test'],'vendor-fts5-regression.log':['./testfixture','ext/fts5/test/fts5prefix2.test']})
  self.assertEqual(funcs[0],funcs[1]);self.assertEqual(funcs[1],funcs[2])
 def tearDown(self):
  self.assertTrue(all(c.returncode is not None and c.stdout.closed for c in CHILDREN))

class AcquisitionPrefixTests(unittest.TestCase):
 def setUp(self):
  self.stack=ExitStack();self.addCleanup(self.stack.close)
  self.root=Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='.owned-prefix-',dir=ROOT/'tests')))
  self.path=self.root/'public';self.path.write_bytes(b'OWNED PUBLIC SOURCE');self.path.chmod(0o600)
 def prefix(self,which):
  for name,a in A.items():
   stack=ExitStack();saved=[];realopen=os.open;realfcntl=fcntl.fcntl;counter=0
   def opened(*args,**kw):fd=realopen(*args,**kw);saved.append(fd);return fd
   def metadata(fd,command,*args):
    nonlocal counter
    counter+=1
    if which in(1,2)and counter==which:raise OSError(errno.EIO,'OWNED FCNTL PREFIX INJECTION')
    return realfcntl(fd,command,*args)
   if which==3:
    def refuse(*args,**kwargs):raise OSError(errno.EIO,'OWNED CALLBACK PREFIX INJECTION')
    callback=patch.object(stack,'callback',side_effect=refuse)
   else:callback=ExitStack()
   with patch.object(a.os,'open',side_effect=opened),patch.object(a.fcntl,'fcntl',side_effect=metadata),callback:
    with self.assertRaises(OSError)as caught:a.held(self.path,stack,[],uid=os.geteuid())
   self.assertEqual(caught.exception.errno,errno.EIO);stack.close();self.assertEqual(len(saved),1)
   with self.assertRaises(OSError)as closed:os.fstat(saved[0])
   self.assertEqual(closed.exception.errno,errno.EBADF)
 def test_first_fcntl_failure_closes_original_FD(self):self.prefix(1)
 def test_second_fcntl_failure_closes_original_FD(self):self.prefix(2)
 def test_callback_registration_failure_closes_original_FD(self):self.prefix(3)
 def test_registered_then_raised_callback_never_closes_reused_descriptor(self):
  for name,a in A.items():
   stack=ExitStack();real=stack.callback
   def register_then_raise(*args,**kw):real(*args,**kw);raise OSError(errno.EIO,'OWNED AFTER CALLBACK INJECTION')
   with patch.object(stack,'callback',side_effect=register_then_raise),self.assertRaises(OSError):a.held(self.path,stack,[],uid=os.geteuid())
   fd=os.open(self.path,os.O_RDONLY|os.O_CLOEXEC)
   try:stack.close();self.assertEqual(os.read(fd,5),b'OWNED')
   finally:os.close(fd)
 def test_restored_flags_and_offset_drift_stay_latched_and_close_original(self):
  for name,a in A.items():
   for change in('fd_flags','status_flags','offset'):
    stack=ExitStack();fds=[];real=os.open
    def opened(*args,**kw):fd=real(*args,**kw);fds.append(fd);return fd
    with patch.object(a.os,'open',side_effect=opened):_,check=a.held(self.path,stack,[],uid=os.geteuid())
    fd=fds[0];descriptor=fcntl.fcntl(fd,fcntl.F_GETFD);status=fcntl.fcntl(fd,fcntl.F_GETFL);offset=os.lseek(fd,0,os.SEEK_CUR)
    if change=='fd_flags':fcntl.fcntl(fd,fcntl.F_SETFD,descriptor&~fcntl.FD_CLOEXEC)
    elif change=='status_flags':fcntl.fcntl(fd,fcntl.F_SETFL,status&~os.O_NONBLOCK)
    else:os.lseek(fd,0,os.SEEK_SET)
    with self.assertRaises(RuntimeError):check()
    fcntl.fcntl(fd,fcntl.F_SETFD,descriptor);fcntl.fcntl(fd,fcntl.F_SETFL,status);os.lseek(fd,offset,os.SEEK_SET)
    with self.assertRaises(RuntimeError):stack.close()
    with self.assertRaises(OSError)as caught:os.fstat(fd)
    self.assertEqual(caught.exception.errno,errno.EBADF)

if __name__=='__main__':unittest.main()
