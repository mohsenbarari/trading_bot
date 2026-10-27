"""Source/owned-Python QA, including one actual UID10001 permission case.
No C/autotools/build, Docker, Git, network or Coreutils CLI fixtures.
"""
from contextlib import redirect_stdout
from hashlib import sha256
from pathlib import Path
from types import ModuleType,SimpleNamespace
from unittest.mock import patch
import ast,io,json,os,stat,subprocess,sys,tempfile,time,unittest

TREE=Path(__file__).absolute().parent.parent
ROOT=TREE/'deploy/maintenance/coreutils_acl_build'
def load(name):
 path=ROOT/name;module=ModuleType(name[:-3]);module.__file__=str(path)
 exec(compile(path.read_bytes(),str(path),'exec'),module.__dict__);return module
class SourceTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):cls.host=load('build_hosted.py');cls.source=load('sourcebuild.py');cls.cli=load('verify_ordinary.py');cls.vendor=load('verify_vendor_suite.py')
 def test_all_default_modes_unarmed(self):
  for module in (self.host,self.source,self.cli):
   with self.subTest(module=module.__name__),redirect_stdout(io.StringIO())as captured:self.assertEqual(module.main([]),0)
   result=json.loads(captured.getvalue());self.assertEqual(result['status'],'UNARMED');self.assertFalse(result['security_accepted'])
  with redirect_stdout(io.StringIO())as captured:self.assertEqual(self.vendor.main(),0)
  self.assertFalse(json.loads(captured.getvalue())['command_executed'])
 def test_pins_complete(self):
  expected=self.source.RECIPE_FILES|{'inputs/'+n for n in self.source.OLD_PACKAGES}
  self.assertEqual(set(self.host.PINS),expected)
  for name,pin in self.host.PINS.items():
   if not name.startswith('inputs/'):
    raw=(ROOT/name).read_bytes();self.assertEqual((len(raw),sha256(raw).hexdigest()),pin,name)
  self.assertEqual(self.host.OUTPUTS,self.source.OUTPUTS)
  self.assertEqual([n for n in self.host.OUTPUTS if n.endswith('.deb')],['coreutils_9.7-3+gold1_amd64.deb'])
 def test_immutable_historical_patch_and_fixtures(self):
  expected={'0001-coreutils-bound-acl-callers.patch':(60929,'3c91b4386c7608b4620422daaf88ec561d3bc709140ae29dc904ed54a9833d1d'),'ordinary_fd_acl.c':(9979,'5a716da021e7c0e72058f4bf1537b44fae28a5c15fa445d5d99905d9f6025936')}
  for name,pin in expected.items():raw=(ROOT/name).read_bytes();self.assertEqual((len(raw),sha256(raw).hexdigest()),pin)
 def test_bound_encoder_before_append(self):
  for module in (self.source,self.host):
   with self.assertRaisesRegex(RuntimeError,'bound'):module.encoded({'x':'a'*1000},maximum=64)
   self.assertEqual(json.loads(module.encoded({'x':1},maximum=16)),{'x':1})
 def test_source_manifest_contract(self):
  raw=(ROOT/'source-manifest.json').read_bytes();manifest=json.loads(raw)
  self.assertEqual((len(raw),sha256(raw).hexdigest()),self.source.SOURCE_MANIFEST_PIN)
  self.assertEqual(len(manifest['files']),23);self.assertEqual(len(manifest['attr26_lineage']),13)
  self.assertTrue(all(r['before']is None or type(r['before']['bytes'])is int for r in manifest['files'].values()))
  self.assertIn('after_test_hook',manifest['files']['debian/rules'])
  packet=json.loads((ROOT/'local-source-packet.json').read_bytes());self.assertEqual(len(packet['files']),76)
  self.assertFalse(packet['Root_source_accepted']);self.assertTrue(json.loads((ROOT/'root-source-approval.json').read_bytes())['source_only_approval_for_isolated_vendor_ci'])
 def test_official_source_exact_intake(self):
  source=self.source.SOURCE_FILES;self.assertEqual(len(source),9)
  self.assertEqual(source['coreutils_9.7-3.dsc'],(2122,'c3a207e3aaad165765c7a6fab89045f5fd20035fea6830b9f9ebbb92ebfbff89'))
  self.assertEqual(source['coreutils_9.7.orig.tar.xz'],(6158960,'e8bb26ad0293f9b5a1fc43fb42ba970e312c66ce92c1b0b16713d7500db251bf'))
  self.assertNotIn('tar_1.35+dfsg-5.dsc',source)
 def test_no_local_only_source_dependency(self):
  for name in self.source.RECIPE_FILES:
   if name.endswith(('.py','.mk')):
    text=(ROOT/name).read_text();self.assertNotIn('.local/',text,name)
  self.assertNotIn('check_source.py',self.source.RECIPE_FILES)
 def test_unfiltered_make_fragment(self):
  text=(ROOT/'print-vendor-selection.mk').read_text()
  for key in ('TESTS','TEST_LOGS','TEST_EXTENSIONS','TEST_SUITE_LOG'):self.assertIn('$('+key+')',text)
  self.assertNotRegex(text,r'(?m)^\s*(TESTS|TEST_LOGS|TEST_EXTENSIONS)\s*[:+?]?=')
  hook=(ROOT/'0002-coreutils-unfiltered-vendor-tests.patch').read_text();self.assertIn('--vendor-tests',hook)
  text=(ROOT/'sourcebuild.py').read_text();self.assertIn("command(['/usr/bin/dh_auto_test']",text);self.assertNotIn("'nocheck',",text)
 def test_genuine_gnulib_fd0_companion(self):
  text=(ROOT/'ordinary_gnulib_fd0.c').read_text();self.assertIn('qcopy_acl ("deliberately-missing-source-diagnostic", source_fd,',text);self.assertIn('source_fd == 0',text)
  self.assertNotIn('gold_acl_qcopy_fd (',text);self.assertIn('actual_gnulib_qcopy_acl_called',text)
  fragment=(ROOT/'gnulib-fd0-fixture.mk').read_text();self.assertIn('lib/libcoreutils.a',fragment);self.assertIn('$(copy_ldadd)',fragment)
 def test_host_refuses_nonhosted_actual_context(self):
  with self.assertRaisesRegex(RuntimeError,'hosted_job'):self.host.hosted_layout(SimpleNamespace(),{})
 def test_capacity_requires_real_full_suite_allowance(self):
  self.assertEqual(self.host.MEMORY,8*1024**3);self.assertEqual(self.host.CPUS,4*10**9);self.assertEqual(self.host.PIDS,512)
  self.assertEqual(self.host.RESERVE,8*1024**3);self.assertEqual(self.host.DISK_GROWTH,48*1024**3)
 def builder_metadata(self,state='running',profile='unconfined'):
  nonce='a'*32;cid='b'*64;context=Path('/owned-public-context')
  value={'id':cid,'name':'/security-hosted-coreutils-acl-package-build-'+nonce,'label':nonce,'image_reference':self.host.BASE,
   'memory':self.host.MEMORY,'memory_swap':self.host.MEMORY,'nano_cpus':self.host.CPUS,'pids':self.host.PIDS,
   'privileged':False,'init':True,'readonly_rootfs':False,'tmpfs':{'/tmp':'rw,exec,nosuid,nodev,mode=1777,size=268435456'},'network_mode':'bridge',
   'running':state=='running','paused':False,'state':state,'pid':123 if state=='running'else 0,'apparmor':profile,
   'started':'0001-01-01T00:00:00Z'if state=='created'else'2026-10-27T11:00:00Z',
   'caps_add':list(self.host.CAPS),'caps_drop':['ALL'],'security':['no-new-privileges:true','apparmor=unconfined'],
   'networks':{'bridge':{}},'mounts':[{'Type':'bind','Destination':'/public-context','Source':str(context),'RW':False}]}
  return value,nonce,cid,context
 def test_only_disposable_builder_has_exact_apparmor_override_and_original_bounds(self):
  args=self.host.create_argv('a'*32,Path('/owned-context'));options=[args[i+1]for i,v in enumerate(args[:-1])if v=='--security-opt']
  self.assertEqual(options,['no-new-privileges:true','apparmor=unconfined']);self.assertNotIn('--privileged',args);self.assertEqual(args.count('--mount'),1)
  self.assertEqual(args[args.index('--tmpfs')+1],'/tmp:rw,exec,nosuid,nodev,mode=1777,size=268435456')
 def test_created_empty_profile_is_unknown_not_poststart_applied_authority(self):
  value,nonce,cid,context=self.builder_metadata('created','');self.assertTrue(self.host.validate_inspect(value,nonce,cid,context,offline=False,running=False))
  with self.assertRaises(RuntimeError):self.host.validate_inspect(value,nonce,cid,context,offline=False,running=True)
  value['started']='2026-10-27T11:00:00Z'
  with self.assertRaisesRegex(RuntimeError,'apparmor_created_unknown'):self.host.validate_inspect(value,nonce,cid,context,offline=False,running=False)
 def test_running_and_previously_started_exited_require_actual_unconfined(self):
  for state in('running','exited'):
   for profile in('',None,'docker-default','unconfined'):
    value,nonce,cid,context=self.builder_metadata(state,profile)
    if profile=='unconfined':self.assertTrue(self.host.validate_inspect(value,nonce,cid,context,offline=False,running=state=='running'))
    else:
     with self.assertRaisesRegex(RuntimeError,'apparmor_applied_after_start'):self.host.validate_inspect(value,nonce,cid,context,offline=False,running=state=='running')
 def test_new_profile_does_not_widen_capability_network_mount_or_no_new_privileges(self):
  for key,wrong in(('privileged',True),('security',['apparmor=unconfined']),('caps_add',['SYS_ADMIN']),('network_mode','host'),('mounts',[])):
   value,nonce,cid,context=self.builder_metadata();value[key]=wrong
   with self.subTest(key=key),self.assertRaises(RuntimeError):self.host.validate_inspect(value,nonce,cid,context,offline=False)
 def test_public_lsm_typed_metadata_is_capability_context_only(self):
  value={'uid':10001,'euid':10001,'gid':10001,'egid':10001,'apparmor':'unconfined'}
  self.assertEqual(self.source.validate_public_lsm(value),value)
  for key,wrong in(('uid',0),('euid',True),('apparmor','docker-default (enforce)'),('path_max','8192\nprivate')):
   bad={**value,key:wrong}
   with self.subTest(key=key),self.assertRaises(RuntimeError):self.source.validate_public_lsm(bad)
 def test_root_public_path_max_reader_is_numeric_bounded_and_fixed_only(self):
  self.assertNotIn('path_max',self.source.PUBLIC_LSM)
  self.assertEqual(self.source.parse_public_path_max(b'8192\n'),8192)
  for raw in(b'',b'8192\nprivate',b'1',b'0',b'9'*33,b'8192\n\n'):
   with self.assertRaises(RuntimeError):self.source.parse_public_path_max(raw)
  text=(ROOT/'sourcebuild.py').read_text();tree=ast.parse(text);node=next(v for v in tree.body if isinstance(v,ast.FunctionDef)and v.name=='root_public_path_max');body=ast.get_source_segment(text,node)
  self.assertEqual(node.args.args,[]);self.assertIn("Path('/sys/module/apparmor/parameters/path_max')",body);self.assertIn('os.read(fd,33)',body);self.assertNotIn('chmod',body)
 def test_genuine_root0400_file_denies_actual_uid10001_and_child_lsm_avoids_it(self):
  # Genuine credentials, modes and child execution. No UID/stat/result masks.
  self.assertEqual((os.getuid(),os.geteuid()),(0,0))
  with tempfile.TemporaryDirectory(prefix='owned-public-lsm-',dir='/tmp')as directory:
   os.chmod(directory,0o755);path=Path(directory)/'public-path-max';path.write_bytes(b'8192\n');path.chmod(0o400)
   before=self.source.identity(path.lstat());self.assertEqual(before[2:6],(0o100400,0,0,1));fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
   try:
    self.assertEqual(os.pread(fd,33,0),b'8192\n')
    code='import os,json\np='+repr(str(path))+'\ntry:\n fd=os.open(p,os.O_RDONLY)\nexcept PermissionError:\n result="PERMISSION_DENIED"\nelse:\n os.close(fd);result="UNEXPECTED_READ"\nprint(json.dumps({"uid":os.getuid(),"euid":os.geteuid(),"gid":os.getgid(),"egid":os.getegid(),"result":result}))'
    r=subprocess.run([sys.executable,'-I','-S','-B','-c',code],stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,user=10001,group=10001,extra_groups=[],env={'PATH':'/usr/bin:/bin','LC_ALL':'C'},close_fds=True,timeout=5,check=True)
    value=json.loads(r.stdout);self.assertEqual(value,{'uid':10001,'euid':10001,'gid':10001,'egid':10001,'result':'PERMISSION_DENIED'})
    r=subprocess.run([sys.executable,'-I','-S','-B','-c',self.source.PUBLIC_LSM],stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,user=10001,group=10001,extra_groups=[],env={'PATH':'/usr/bin:/bin','LC_ALL':'C'},close_fds=True,timeout=5,check=True)
    value=json.loads(r.stdout);self.assertEqual({k:value[k]for k in('uid','euid','gid','egid')},{k:10001 for k in('uid','euid','gid','egid')});self.assertNotIn('path_max',value)
    self.assertEqual(self.source.identity(os.fstat(fd)),before)
   finally:os.close(fd)
   self.assertEqual(self.source.identity(path.lstat()),before)
 def test_early_capability_uses_unchanged_perl_setup_before_source_build(self):
  self.assertEqual(self.source.DEEP_SETUP,['/usr/bin/perl','-e','my $d = "x" x 200; foreach my $i (1..52)','-e','  { mkdir ($d, 0700) && chdir $d or die "$!" }'])
  text=(ROOT/'sourcebuild.py').read_text();tree=ast.parse(text);node=next(v for v in tree.body if isinstance(v,ast.FunctionDef)and v.name=='deep_path_preflight');body=ast.get_source_segment(text,node)
  self.assertIn('user=10001',body);self.assertIn('maximum=2048',body);self.assertIn('actual_deep_path_setup_required',body)
  calls=[v for v in ast.walk(node)if isinstance(v,ast.Call)and isinstance(v.func,ast.Name)and v.func.id=='run'and v.args and isinstance(v.args[0],ast.Name)and v.args[0].id=='DEEP_SETUP'];self.assertEqual(len(calls),1)
  configure=next(v for v in tree.body if isinstance(v,ast.FunctionDef)and v.name=='configure_repository');body=ast.get_source_segment(text,configure)
  self.assertLess(body.index('preflight=deep_path_preflight()'),body.index("downloads=Path('/build/source-inputs')"))
 def test_single_coreutils_runtime_only(self):
  self.assertEqual(set(self.source.OLD_PACKAGES),{'libacl1_2.4.0-1~trixie+gold1_amd64.deb'})
  self.assertFalse(any('libacl1-dev'in name for name in self.host.OUTPUTS))
  self.assertFalse(any(name.endswith('.deb')and not name.startswith('coreutils_')for name in self.host.OUTPUTS))
 def test_abi_scope_truthful(self):
  text=(ROOT/'sourcebuild.py').read_text();self.assertIn('retained_legacy_import_names_not_callsite_proof',text)
  self.assertIn('versioned_acl_abi',text);self.assertIn('dynamic_approved_libacl_not_static_sdk',text)
  self.assertNotIn("not LEGACY&undefined",text)
 def test_no_compile_or_product_image_commands_in_host(self):
  text=(ROOT/'build_hosted.py').read_text();self.assertNotIn("['build'",text);self.assertNotIn("'--privileged'",text)
  self.assertNotIn('RLIMIT_AS',text);self.assertNotIn("'npm'",text)
 def test_all_python_parse(self):
  for path in ROOT.glob('*.py'):ast.parse(path.read_bytes(),filename=str(path))
 def test_host_command_small_actual_output(self):
  calls=[]
  raw=self.host.run([sys.executable,'-I','-S','-c','import os;os.write(1,b"owned")'],env={'PATH':'/usr/bin:/bin'},deadline=time.monotonic()+10,maximum=5,guard=lambda:calls.append(True))
  self.assertEqual(raw,b'owned');self.assertGreaterEqual(len(calls),3)
 def test_host_command_rejects_excess_before_log_write(self):
  with tempfile.TemporaryFile()as log:
   with self.assertRaisesRegex(RuntimeError,'command_output_bound'):
    self.host.run([sys.executable,'-I','-S','-c','import os;os.write(1,b"x"*9)'],env={'PATH':'/usr/bin:/bin'},deadline=time.monotonic()+10,maximum=8,log=log.fileno())
   self.assertLessEqual(log.tell(),8)
 def test_source_command_rejects_excess_before_accumulation(self):
  with self.assertRaisesRegex(RuntimeError,'command_output_bound'):
   self.source.run([sys.executable,'-I','-S','-c','import os;os.write(1,b"x"*9)'],maximum=8,timeout=10)
 def test_known_log_original_frame_survives_writer_close_and_outer_final(self):
  with tempfile.TemporaryDirectory(dir=ROOT)as directory:
   path=Path(directory)/'owned.log';owners=[];item=self.host.save_known_log(path,b'owned known log\n',owners)
   self.assertEqual(owners,[item]);self.assertIsNotNone(item.fd);self.assertEqual(item.before,self.host.ident(path.lstat()))
   self.host.finalize_public_inputs(owners);self.assertIsNone(item.fd);item.named()
 def test_empty_known_log_does_not_weaken_normal_input_admission(self):
  with tempfile.TemporaryDirectory(dir=ROOT)as directory:
   path=Path(directory)/'owned.log';owners=[];item=self.host.save_known_log(path,b'',owners)
   with self.assertRaisesRegex(RuntimeError,'public_file'):self.host.Held(path,128)
   self.host.finalize_public_inputs(owners);item.named()
 def test_actual_writer_close_samebyte_timestamp_mutation_is_rejected(self):
  with tempfile.TemporaryDirectory(dir=ROOT)as directory:
   path=Path(directory)/'owned.log';owners=[];writer=[];real_open=os.open;real_close=os.close;changed=[]
   def opened(target,flags,*args,**kwargs):
    fd=real_open(target,flags,*args,**kwargs)
    if Path(target)==path and flags&os.O_WRONLY:writer.append(fd)
    return fd
   def closed(fd):
    real_close(fd)
    if writer and fd==writer[0]and not changed:
     before=path.stat();os.utime(path,ns=(before.st_atime_ns,before.st_mtime_ns+1000000));changed.append(True)
   try:
    with patch.object(self.host.os,'open',opened),patch.object(self.host.os,'close',closed):
     with self.assertRaisesRegex(RuntimeError,'public_changed'):self.host.save_known_log(path,b'identical bytes\n',owners)
    self.assertTrue(changed);self.assertEqual(path.read_bytes(),b'identical bytes\n')
   finally:
    for item in owners:
     if item.fd is not None:real_close(item.fd);item.fd=None
 def test_actual_outer_reader_close_samebyte_timestamp_mutation_is_rejected(self):
  with tempfile.TemporaryDirectory(dir=ROOT)as directory:
   path=Path(directory)/'owned.log';owners=[];item=self.host.save_known_log(path,b'identical bytes\n',owners);reader=item.fd;real_close=os.close;changed=[]
   def closed(fd):
    real_close(fd)
    if fd==reader and not changed:
     before=path.stat();os.utime(path,ns=(before.st_atime_ns,before.st_mtime_ns+1000000));changed.append(True)
   with patch.object(self.host.os,'close',closed):
    with self.assertRaisesRegex(RuntimeError,'public_closed_changed'):self.host.finalize_public_inputs(owners)
   self.assertTrue(changed);self.assertEqual(path.read_bytes(),b'identical bytes\n')
 def test_actual_failed_child_has_bounded_truthful_partial_evidence(self):
  try:self.host.run([sys.executable,'-I','-S','-c','import os;os.write(1,b"x"*40000);raise SystemExit(7)'],env={'PATH':'/usr/bin:/bin'},deadline=time.monotonic()+10,maximum=65536)
  except RuntimeError as error:
   evidence=error.public_command_failure;self.assertEqual(evidence['actual_returncode'],7);self.assertTrue(evidence['actual_pipe_closed']);self.assertEqual(evidence['observed_output_bytes'],40000)
   self.assertEqual(len(evidence['prefix']),16384);self.assertEqual(len(evidence['tail']),16384)
  else:self.fail('actual failed command accepted')
 def test_actual_stdout_close_source_timestamp_mutation_cannot_return_success(self):
  with tempfile.TemporaryDirectory(dir=ROOT)as directory:
   path=Path(directory)/'held-public-source';path.write_bytes(b'unchanged source bytes');item=self.host.Held(path,128)
   real_popen=self.host.subprocess.Popen;changed=[]
   def started(*args,**kwargs):
    child=real_popen(*args,**kwargs);real_close=child.stdout.close
    def closed():
     real_close();before=path.stat();os.utime(path,ns=(before.st_atime_ns,before.st_mtime_ns+1000000));changed.append(True)
    child.stdout.close=closed;return child
   try:
    with patch.object(self.host.subprocess,'Popen',started):
     with self.assertRaisesRegex(RuntimeError,'public_changed'):
      self.host.run([sys.executable,'-I','-S','-c','import os;os.write(1,b"actual public output")'],env={'PATH':'/usr/bin:/bin'},deadline=time.monotonic()+10,maximum=64,guard=item.check)
    self.assertTrue(changed);self.assertEqual(path.read_bytes(),b'unchanged source bytes')
   finally:
    if item.fd is not None:os.close(item.fd);item.fd=None
 def test_regeneration_guard_is_one_packaging_command_not_configure_waiver(self):
  raw=(ROOT/'0003-preserve-imported-gnulib-infrastructure.patch').read_bytes()
  self.assertEqual((len(raw),sha256(raw).hexdigest()),self.source.REGEN_PIN)
  text=raw.decode();self.assertEqual([line for line in text.splitlines()if line.startswith('+')and not line.startswith('+++')],['+\tAUTOPOINT=true dh_autoreconf'])
  self.assertEqual([line for line in text.splitlines()if line.startswith('-')and not line.startswith('---')],['-\tdh_autoreconf'])
  self.assertNotIn('m4_pattern_allow',text);self.assertNotIn('|| true',text)
  manifest=json.loads((ROOT/'source-manifest.json').read_text());self.assertIn('after_regeneration_guard',manifest['files']['debian/rules'])
  self.assertEqual(set(manifest['regeneration_infrastructure']['original_m4_pins']),{'m4/threadlib.m4','m4/wint_t.m4','m4/gnulib-comp.m4'})
 def test_imported_macro_and_po_inventory_retains_content_changes(self):
  with tempfile.TemporaryDirectory(dir=ROOT)as directory:
   source=Path(directory);(source/'m4').mkdir();(source/'po').mkdir();(source/'m4/owned.m4').write_bytes(b'owned current imported macro\n');(source/'po/Makefile.in.in').write_bytes(b'owned gettext template\n')
   with patch.object(self.source,'WORK',source):
    before=self.source.regeneration_inputs(source);self.assertEqual(set(before),{'m4/owned.m4','po/Makefile.in.in'})
    (source/'m4/owned.m4').write_bytes(b'older overwritten macro\n');after=self.source.regeneration_inputs(source)
   self.assertNotEqual(after,before);self.assertEqual(after['po/Makefile.in.in'],before['po/Makefile.in.in'])
 def test_imported_macro_symlink_is_not_admitted(self):
  with tempfile.TemporaryDirectory(dir=ROOT)as directory:
   source=Path(directory);(source/'m4').mkdir();(source/'po').mkdir();(source/'po/Makefile.in.in').write_bytes(b'owned template');(source/'m4/unexpected.m4').symlink_to('../po/Makefile.in.in')
   with patch.object(self.source,'WORK',source),self.assertRaisesRegex(RuntimeError,'imported_m4_inventory'):self.source.regeneration_inputs(source)
 def actual_failed(self,body,maximum):
  try:self.host.run([sys.executable,'-I','-S','-c',body],env={'PATH':'/usr/bin:/bin'},deadline=time.monotonic()+10,maximum=maximum)
  except RuntimeError as error:return error.public_command_failure
  self.fail('actual failure did not reject')
 def test_actual_exit7_middle_cause_survives_complete_known_failed_log(self):
  data=b'A'*25000+b'OWNED_MIDDLE_CAUSE\n'+b'Z'*25000
  observed=self.actual_failed('import os;os.write(1,b"A"*25000+b"OWNED_MIDDLE_CAUSE\\n"+b"Z"*25000);raise SystemExit(7)',65536)
  self.assertEqual(observed['actual_returncode'],7);self.assertEqual(observed['captured_output'],data);self.assertTrue(observed['stdout_eof_observed']);self.assertTrue(observed['actual_pipe_closed'])
  self.assertNotIn(b'OWNED_MIDDLE_CAUSE',observed['prefix']+observed['tail'])
  with tempfile.TemporaryDirectory(dir=ROOT)as directory:
   owners=[];path=Path(directory);self.host.retain_failed_command(path,'offline_build',observed,owners)
   self.assertEqual((path/'offline_build.failed.log').read_bytes(),data);metadata=json.loads((path/'offline_build.failed.json').read_bytes());self.assertTrue(metadata['complete_received_stdout']);self.assertFalse(metadata['command_accepted'])
   self.assertEqual(metadata['captured_output_sha256'],sha256(data).hexdigest());self.assertEqual(len(owners),2);self.host.finalize_public_inputs(owners)
 def test_actual_output_overflow_retains_only_bounded_received_prefix_truthfully(self):
  observed=self.actual_failed('import os;os.write(1,b"owned"*20000);raise SystemExit(7)',32768)
  self.assertLessEqual(len(observed['captured_output']),32768);self.assertLessEqual(observed['observed_output_bytes'],32769);self.assertFalse(observed['stdout_eof_observed']);self.assertTrue(observed['actual_pipe_closed'])
  with tempfile.TemporaryDirectory(dir=ROOT)as directory:
   owners=[];path=Path(directory);self.host.retain_failed_command(path,'offline_build',observed,owners)
   self.assertLessEqual((path/'offline_build.failed.log').stat().st_size,32768);metadata=json.loads((path/'offline_build.failed.json').read_bytes());self.assertFalse(metadata['complete_received_stdout']);self.assertFalse(metadata['command_accepted']);self.host.finalize_public_inputs(owners)
 def test_actual_failed_metadata_reader_close_cannot_change_prior_full_log(self):
  observed=self.actual_failed('import os;os.write(1,b"owned middle");raise SystemExit(7)',64)
  with tempfile.TemporaryDirectory(dir=ROOT)as directory:
   owners=[];path=Path(directory);self.host.retain_failed_command(path,'offline_build',observed,owners);last=owners[-1].fd;real_close=os.close;changed=[]
   def closed(fd):
    real_close(fd)
    if fd==last and not changed:
     target=path/'offline_build.failed.log';before=target.stat();os.utime(target,ns=(before.st_atime_ns,before.st_mtime_ns+1000000));changed.append(True)
   with patch.object(self.host.os,'close',closed):
    with self.assertRaisesRegex(RuntimeError,'public_changed'):self.host.finalize_public_inputs(owners)
   self.assertTrue(changed)
 def test_private_probe_patch_only_adds_declaring_header(self):
  raw=(ROOT/'0004-declare-fchownat-in-configure-probe.patch').read_bytes();self.assertEqual((len(raw),sha256(raw).hexdigest()),self.source.PROBE_PIN)
  text=raw.decode();added=[line for line in text.splitlines()if line.startswith('+')and not line.startswith('+++')];removed=[line for line in text.splitlines()if line.startswith('-')and not line.startswith('---')]
  self.assertEqual(added,['+#include <unistd.h>']);self.assertEqual(removed,[])
  manifest=json.loads((ROOT/'source-manifest.json').read_text());self.assertEqual(manifest['files']['configure.ac']['after']['bytes'],33382);self.assertEqual(manifest['files']['configure.ac']['after_probe_header']['bytes'],33402)
  self.assertIn(self.source.PROBE_PATCH,self.source.RECIPE_FILES)
 def test_probe_header_contract_does_not_bypass_actual_compile_or_feature_gates(self):
  text=(ROOT/'sourcebuild.py').read_text();self.assertIn('(PATCH,CHANGELOG,REGEN_PATCH,PROBE_PATCH)',text);self.assertIn("pins.get('after_probe_header'",text)
  self.assertIn("#define GOLD_DESCRIPTOR_ACL 1",text);self.assertIn("real_autotools_regeneration",text);self.assertNotIn('m4_pattern_allow',text);self.assertNotIn('ac_cv_',text)
 def test_actual_parser_refusal_retains_complete_public_selection_before_failure(self):
  raw=b'GOLD_VENDOR_SELECTION_V2\nsuite_log=test-suite.log\ncollect_skipped_logs=no\ntest=../owned-outside\nlog=../owned-outside.log\n'
  script='from pathlib import Path\nfrom types import ModuleType\n'
  for variable,name in(('S','sourcebuild.py'),('V','verify_vendor_suite.py')):
   script+=variable+'=ModuleType('+repr(variable)+');exec(compile(Path('+repr(str(ROOT/name))+').read_bytes(),'+repr(str(ROOT/name))+',"exec"),'+variable+'.__dict__)\n'
  script+='raw='+repr(raw)+'\nS.log_vendor_selection("gnulib",raw)\ntry:V.selection(raw,"gnulib")\nexcept RuntimeError:raise SystemExit(7)\nraise SystemExit(99)\n'
  observed=self.actual_failed(script,65536);data=observed['captured_output'];self.assertEqual(observed['actual_returncode'],7)
  self.assertIn(raw,data);self.assertIn(('bytes='+str(len(raw))+' sha256='+sha256(raw).hexdigest()).encode(),data)
  self.assertLess(data.index(b'GOLD_VENDOR_SELECTION_LOG_BEGIN'),data.index(raw));self.assertIn(b'GOLD_VENDOR_SELECTION_LOG_END gnulib\n',data)
 def test_selection_diagnostics_bound_before_forward_or_output(self):
  for raw in(b'',b'x'*(self.source.MAX_METADATA+1),'not bytes'):
   with patch.object(self.source,'forward',side_effect=AssertionError('must not write')),self.assertRaisesRegex(RuntimeError,'vendor_selection_log_bound'):self.source.log_vendor_selection('gnulib',raw)
  with patch.object(self.source,'forward',side_effect=AssertionError('must not write')),self.assertRaisesRegex(RuntimeError,'vendor_selection_log_bound'):self.source.log_vendor_selection('foreign',b'x')
 def test_selection_before_parser_and_real_driver_observation_remain_required(self):
  text=(ROOT/'sourcebuild.py').read_text();self.assertLess(text.index('log_vendor_selection(name,chosen[name])'),text.index('module.selection(chosen[name],name)'))
  self.assertIn('driver_binding=driver',text);self.assertIn("observed(Path.cwd()/'build-aux/test-driver',maximum=65536)==driver",text)
  fragment=(ROOT/'print-vendor-selection.mk').read_text();self.assertIn('$(if $(IGNORE_SKIPPED_LOGS),no,yes)',fragment)
  self.assertNotRegex(fragment,r'(?m)^\s*IGNORE_SKIPPED_LOGS\s*[:+?]?=')
if __name__=='__main__':unittest.main()
