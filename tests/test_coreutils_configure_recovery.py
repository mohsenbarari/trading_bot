"""Finite source/owned-Python tests; no configure/compiler/Docker/network."""
from pathlib import Path
from hashlib import sha256
from types import ModuleType,SimpleNamespace
from unittest.mock import patch
import ast,base64,contextlib,errno,fcntl,io,json,os,tempfile,unittest
HERE=Path(__file__).absolute().parent
DRIVER=HERE.parent/'deploy/maintenance/coreutils_acl_build/configure_recovery.py'
M=ModuleType('owned_configure_recovery');M.__file__=str(DRIVER);exec(compile(DRIVER.read_bytes(),str(DRIVER),'exec'),M.__dict__)
class Tests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory(dir=HERE);self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name);self.states=[];self.addCleanup(self.dispose)
 def dispose(self):
  seen=set()
  for s in self.states:
   for r in s.rows:
    fd=r.original_fd
    if r.closed or fd is None or fd in seen:continue
    seen.add(fd)
    try:os.close(fd)
    except OSError as e:self.assertEqual(e.errno,errno.EBADF)
 def state(self):s=M.State();self.states.append(s);return s
 def source(self,raw=b'OWNED input\n'):
  p=self.root/'input';p.write_bytes(raw);p.chmod(0o644);return p,(len(raw),sha256(raw).hexdigest())
 def test_default_unarmed_has_no_input_or_native_action(self):
  with patch.object(M,'execute',side_effect=AssertionError('ARMED')),patch.object(os,'open',side_effect=AssertionError('INPUT')),contextlib.redirect_stdout(io.StringIO())as out:self.assertEqual(M.main([]),0)
  self.assertEqual(json.loads(out.getvalue())['status'],'UNARMED')
 def test_embedded_headers_exact_measured_bytes_and_not_package_claim(self):
  rows=M.headers();self.assertEqual({p:(len(v),sha256(v).hexdigest())for p,v in rows.items()},{'/usr/include/sys/acl.h':(3915,'01efa41d8ee0061085ee590908ccdd65d57c64d3f84f96a473620658cadb581c'),'/usr/include/acl/libacl.h':(2703,'324dc3c9f6c6668d09b48f755aab7f00cc5d5d9e46780b18591e336875975962')})
  old=M.SDK_HEADERS;bad={p:dict(v)for p,v in old.items()};next(iter(bad.values()))['sha256']='0'*64
  with patch.object(M,'SDK_HEADERS',bad),self.assertRaisesRegex(RuntimeError,'embedded_header'):M.headers()
 def test_actual_fixed_buildinfo_dependency_inventory(self):
  raw=base64.b64decode(M.BUILDINFO);rows=M.installed_dependencies(raw);self.assertGreater(len(rows),100);self.assertEqual(len(rows),len(set(n for n,v in rows)));self.assertIn(('libacl1-dev','2.4.0-1~trixie+gold1'),rows)
 def test_fixed_configure_targets_have_no_package_or_vendor_branch(self):
  self.assertEqual(M.configure_commands(),[['/usr/bin/make','--no-print-directory','-f','debian/rules','override_dh_autoreconf'],['/usr/bin/make','--no-print-directory','-f','debian/rules','override_dh_auto_configure']])
  t=DRIVER.read_text();self.assertNotIn('S.build(',t);self.assertNotIn('dpkg-buildpackage',t);self.assertNotIn('dh_auto_test',t);self.assertNotIn('vendor_tests(',t)
 def test_exact_sources_and_historical_header_gate(self):
  self.assertEqual(len(M.SOURCE_PRODUCTS),4);self.assertEqual(M.SOURCE_PRODUCTS['coreutils_9.7.orig.tar.xz'],(6158960,'e8bb26ad0293f9b5a1fc43fb42ba970e312c66ce92c1b0b16713d7500db251bf'));self.assertEqual(M.HISTORICAL['lib/config.h'],M.HEADER_PIN);self.assertEqual(len(M.GENERATED),8);self.assertEqual(len(set(M.GENERATED.values())),8)
 def test_loan_real_read_full9_flags_and_physical_close(self):
  s=self.state();p,pin=self.source();r=M.Loan(s,p,pin);self.assertEqual(r.read(),p.read_bytes());fd=r.original_fd;M.finish(s)
  with self.assertRaises(OSError)as e:os.fstat(fd)
  self.assertEqual(e.exception.errno,errno.EBADF)
 def test_real_wrong_hash_prefix_is_HELD_without_raw_close(self):
  s=self.state();p,pin=self.source()
  with self.assertRaisesRegex(RuntimeError,'input_hash'):M.Loan(s,p,(pin[0],'0'*64))
  r=s.rows[0];self.assertFalse(r.admitted);self.assertTrue(r.uncertain)
  with self.assertRaises(M.Held):M.finish(s)
  os.fstat(r.original_fd);self.assertFalse(r.closed)
 def test_actual_fresh_entry_correct_self_pin_stops_before_legacy_delegate(self):
  barrier=RuntimeError('OWNED_PRE_LEGACY_BARRIER');args=SimpleNamespace(controller_sha256=sha256(DRIVER.read_bytes()).hexdigest(),inner_phase='prepare')
  with patch.object(Path,'cwd',return_value=Path('/build')),patch.object(Path,'is_file',return_value=True),patch.object(M,'load',side_effect=barrier),self.assertRaises(M.Held)as e:M.inner(args)
  s=e.exception.state;self.states.append(s);self.assertIs(s.errors[0],barrier);self.assertEqual(len(s.rows),1);self.assertTrue(s.rows[0].closed)
 def test_actual_fresh_entry_wrong_self_pin_never_delegates(self):
  args=SimpleNamespace(controller_sha256='0'*64,inner_phase='prepare')
  with patch.object(Path,'cwd',return_value=Path('/build')),patch.object(Path,'is_file',return_value=True),patch.object(M,'load',side_effect=AssertionError('NO_DELEGATE')),self.assertRaises(M.Held)as e:M.inner(args)
  s=e.exception.state;self.states.append(s);self.assertEqual(len(s.rows),1);self.assertEqual(str(s.errors[0]),'input_hash');self.assertFalse(s.rows[0].closed)
 def test_detected_flag_restore_cannot_heal_original_loan(self):
  s=self.state();p,pin=self.source();r=M.Loan(s,p,pin);old=r.fl;fcntl.fcntl(r.original_fd,fcntl.F_SETFD,0)
  with self.assertRaisesRegex(RuntimeError,'input_FD'):r.check()
  fcntl.fcntl(r.original_fd,fcntl.F_SETFD,old[1])
  with self.assertRaises(M.Held):M.finish(s)
  self.assertTrue(r.uncertain);self.assertFalse(r.closed);os.fstat(r.original_fd)
 def test_same_inode_same_flags_different_OFD_offset17_is_HELD(self):
  s=self.state();p,pin=self.source();r=M.Loan(s,p,pin);fd=r.original_fd;os.close(fd);new=os.open(p,r.fl[0]|os.O_CLOEXEC);self.assertEqual(new,fd);os.lseek(fd,17,os.SEEK_SET);self.assertEqual(M.flags(fd),r.fl)
  with self.assertRaisesRegex(RuntimeError,'input_FD'):r.check()
  with self.assertRaises(M.Held):M.finish(s)
  self.assertEqual(os.lseek(fd,0,os.SEEK_CUR),17)
 def test_actual_184650_output_RO_before_writer_close(self):
  s=self.state();raw=b'X'*184650;events=[];old=M.Writer.close
  def close(w):
   readers=[r for r in s.rows if type(r)is M.Loan and r.path==w.path];self.assertEqual(len(readers),1);r=readers[0];self.assertTrue(r.admitted);self.assertEqual(r.read(),raw);result=old(w);os.fstat(r.original_fd);events.append(True);return result
  with patch.object(M.Writer,'close',close):r=M.fixed_write(s,self.root/'body',raw)
  self.assertEqual(r['bytes'],184650);self.assertEqual(events,[True]);M.finish(s)
 def test_noop_writer_close_stays_HELD_and_never_raw_retried(self):
  s=self.state();w=M.Writer(s,self.root/'writer',0o644);fd=w.original_fd;calls=[]
  with patch.object(os,'close',side_effect=lambda n:calls.append(n)),self.assertRaisesRegex(RuntimeError,'writer_close_unknown'):w.close()
  with self.assertRaises(M.Held):M.finish(s)
  self.assertEqual(calls,[fd]);self.assertFalse(w.closed);os.fstat(fd)
 def test_caps_before_append_or_output_and_truthful_profile(self):
  with self.assertRaisesRegex(RuntimeError,'receipt_bound'):M.encode({'x':'x'*1000},64)
  with self.assertRaisesRegex(RuntimeError,'output_bound'):M.fixed_write(self.state(),self.root/'large',b'X'*(M.BODY_MAX+1))
  self.assertFalse((self.root/'large').exists());self.assertIn("SDK_package_identity_recovered':False",DRIVER.read_text());self.assertIn("config_log_is_fresh_observation':True",DRIVER.read_text())
 def test_actual_legacy_stage_generator_API_and_typed_modes(self):
  s=self.state();root=Path('/root/trading-bot/trading_bot/deploy/maintenance/coreutils_acl_build');H=M.load(s,root/'build_hosted.py',M.HOST_PIN);S=M.load(s,root/'sourcebuild.py',M.SOURCE_PIN);self.assertEqual(S.SOURCE_MANIFEST_PIN,(13045,'8e1dcd124814503895bbcb5762efadb500e59b0a65c9a5eea15f3c5366149784'))
  c=self.root/'context';c.mkdir(mode=0o700)
  for name,raw in(('context-manifest.json',b'{"OWNED":true}\n'),('.dockerignore',b'OWNED\n')):p=c/name;p.write_bytes(raw);p.chmod(0o600)
  code=H.stage_code(c,False);self.assertNotIn('pins=PINS;verify=VERIFY',code);self.assertIn('verify=False',code);self.assertIn('stat.S_IFREG|0o600',code);manifest=M.manifest_script(H);self.assertIn('generated-lib-config.h.txt',manifest);self.assertNotIn('coreutils_9.7-3+gold2_amd64.deb',manifest);self.assertIn("modes=(0o600,)",DRIVER.read_text());M.finish(s)
 def test_workflow_routes_recovery_separately_and_pins_controller(self):
  workflow=(HERE.parent/'.github/workflows/coreutils-acl-descriptor-package.yml').read_text();digest=sha256(DRIVER.read_bytes()).hexdigest();self.assertIn('configure_only:',workflow);self.assertIn('inputs.configure_only != true',workflow);self.assertIn('inputs.configure_only == true',workflow);self.assertIn('--controller-sha256 '+digest,workflow);self.assertIn('coreutils-configure-only-',workflow);self.assertIn('--source-sha256 fc9fc8e4ae7fe1af75fd0b4c63fedb0ebfa5820148bb311c5eaafcaaa5bbbb82',workflow)
 def test_actual_configure_loop_calls_both_original_targets(self):
  tree=ast.parse(DRIVER.read_bytes());inner=next(n for n in tree.body if isinstance(n,ast.FunctionDef)and n.name=='inner');loop=next(n for n in ast.walk(inner)if isinstance(n,ast.For)and isinstance(n.iter,ast.Call)and isinstance(n.iter.func,ast.Name)and n.iter.func.id=='configure_commands');calls=[];scope={'configure_commands':M.configure_commands,'source':Path('/owned-source'),'command':lambda argv,**kw:calls.append((argv,kw))};exec(compile(ast.Module(body=[loop],type_ignores=[]),'<actual configure target loop>','exec'),scope);self.assertEqual([r[0]for r in calls],M.configure_commands());self.assertTrue(all(r[1]['user']==10001 for r in calls))
 def test_actual_original_m4_guards_refuse_each_input_drift(self):
  tree=ast.parse(DRIVER.read_bytes());inner=next(n for n in tree.body if isinstance(n,ast.FunctionDef)and n.name=='inner');codes={'qcopy_m4_original_pin','qcopy_m4_declared_patch_pin','original_imported_m4_pin'};guards={}
  for node in ast.walk(inner):
   if isinstance(node,ast.Expr)and isinstance(node.value,ast.Call)and isinstance(node.value.func,ast.Name)and node.value.func.id=='need'and len(node.value.args)==2 and isinstance(node.value.args[1],ast.Constant)and node.value.args[1].value in codes:guards[node.value.args[1].value]=node
  self.assertEqual(set(guards),codes)
  before=(3,sha256(b'old').hexdigest());after=(3,sha256(b'new').hexdigest());original={'m4/owned.m4':{'bytes':3,'sha256':before[1]}};calls=[];actual={'value':{'bytes':before[0],'sha256':before[1]}}
  def observed(path,**kw):calls.append((path,kw));return actual['value']
  scope={'need':M.need,'S':SimpleNamespace(LINK_M4_BEFORE=before,LINK_M4_AFTER=after,observed=observed),'source':Path('/owned-source'),'manifest':{'regeneration_infrastructure':{'original_m4_pins':original}},'regenerated_before':dict(original)}
  for code in ('qcopy_m4_original_pin','qcopy_m4_declared_patch_pin'):
   pin=before if code=='qcopy_m4_original_pin'else after;actual['value']={'bytes':pin[0],'sha256':pin[1]};compiled=compile(ast.Module(body=[guards[code]],type_ignores=[]),'<actual original m4 guard>','exec');exec(compiled,scope);self.assertEqual(calls[-1],(Path('/owned-source/m4/acl.m4'),{'maximum':pin[0]}));actual['value']={'bytes':pin[0],'sha256':'0'*64}
   with self.assertRaisesRegex(RuntimeError,code):exec(compiled,scope)
  compiled=compile(ast.Module(body=[guards['original_imported_m4_pin']],type_ignores=[]),'<actual original imported m4 guard>','exec');exec(compiled,scope);scope['regenerated_before']={'m4/owned.m4':{'bytes':3,'sha256':'0'*64}}
  with self.assertRaisesRegex(RuntimeError,'original_imported_m4_pin'):exec(compiled,scope)
 def test_actual_report_never_promotes_input_binding_to_dynamic_provider_proof(self):
  text=DRIVER.read_text();tree=ast.parse(text);inner=next(n for n in tree.body if isinstance(n,ast.FunctionDef)and n.name=='inner');report=next(n.value for n in ast.walk(inner)if isinstance(n,ast.Assign)and any(isinstance(t,ast.Name)and t.id=='report'for t in n.targets)and isinstance(n.value,ast.Dict)and any(isinstance(k,ast.Constant)and k.value=='dynamic_provider_proof'for k in n.value.keys));wanted={'header_exact','dynamic_provider_proof','ordinary_configure_probe_observation','ordinary_configure_probes_only','actual_extra_provider_link_probe','SDK_package_identity_recovered','security_accepted'};selected=[(k,v)for k,v in zip(report.keys,report.values)if isinstance(k,ast.Constant)and k.value in wanted];expression=ast.fix_missing_locations(ast.Expression(body=ast.Dict(keys=[k for k,v in selected],values=[v for k,v in selected])));code=compile(expression,'<actual recovery proof limits>','eval');observation={'actual':{'bytes':17,'sha256':'0'*64}}
  for matched in (False,True):
   value=eval(code,{'matched':matched,'rows':{'config.log':observation}});self.assertEqual(set(value),wanted);self.assertIs(value['header_exact'],matched);self.assertEqual(value['dynamic_provider_proof'],'OPEN_NOT_ESTABLISHED_BY_THIS_RECOVERY');self.assertIs(value['ordinary_configure_probe_observation'],observation);self.assertIs(value['ordinary_configure_probes_only'],True);self.assertTrue(all(value[k]is False for k in ('actual_extra_provider_link_probe','SDK_package_identity_recovered','security_accepted')))
  self.assertNotIn('recovery-provider.c',text);self.assertNotIn('recovery-provider.mk',text);self.assertNotIn('/usr/bin/ldd',text);self.assertNotIn('/usr/bin/readelf',text)
 def test_normal_shared_link_is_checked_after_real_rebinding(self):
  s=self.state();a=self.root/'a';b=self.root/'b';a.write_bytes(b'A');b.write_bytes(b'B');link=self.root/'libacl.so';link.symlink_to(a.name);M.NormalLink(s,link,a);s.check();link.unlink();link.symlink_to(b.name)
  with self.assertRaisesRegex(RuntimeError,'normal_linker_binding'):s.check()
  with self.assertRaises(M.Held):M.finish(s)
 def test_failed_scope_cannot_acquire_write_or_forward_read(self):
  s=self.state();p,pin=self.source();r=M.Loan(s,p,pin);body=RuntimeError('OWNED failed');s.note(body)
  with patch.object(os,'open',side_effect=AssertionError('NO_NEW_ACQUISITION')),self.assertRaisesRegex(RuntimeError,'failed_scope'):M.Writer(s,self.root/'blocked',0o644)
  with patch.object(os,'pread',side_effect=AssertionError('NO_FORWARD_READ')),self.assertRaisesRegex(RuntimeError,'failed_or_closed_read'):r.read()
  with self.assertRaises(M.Held):M.finish(s)
  self.assertIs(s.errors[0],body)
 def test_genuine_UID10001_source_mode_and_wrong_owner(self):
  s=self.state();p,pin=self.source();os.chown(p,10001,10001);self.assertEqual((p.stat().st_uid,p.stat().st_gid),(10001,10001));r=M.Loan(s,p,pin,owners=(10001,));self.assertEqual(r.before[3:5],(10001,10001));M.finish(s)
  other=self.state()
  with self.assertRaisesRegex(RuntimeError,'input_type'):M.Loan(other,p,pin,owners=(0,))
  self.assertIsNone(other.rows[0].original_fd)
  with self.assertRaises(M.Held):M.finish(other)
 def test_expired_scope_refuses_before_new_original_FD(self):
  s=self.state();s.end=s.original_end=0;p,pin=self.source()
  with patch.object(os,'open',side_effect=AssertionError('NO_FD')),self.assertRaisesRegex(RuntimeError,'original_deadline'):M.Loan(s,p,pin)
  self.assertIsNone(s.rows[0].original_fd)
  with self.assertRaises(M.Held):M.finish(s)
 def test_actual_base_pull_progress_has_distinct_bound_and_digest_admission(self):
  host=SimpleNamespace(BASE='python:fixed@sha256:'+'a'*64);calls=[];progress=b'ordinary public pull progress\n'*30
  def cli(args,**kw):
   calls.append((args,kw));return progress if args[0]=='pull'else json.dumps(['python@sha256:'+'a'*64]).encode()
  self.assertEqual(M.admit_public_base(cli,host),progress);self.assertGreater(len(progress),128);self.assertEqual(calls[0][1],{'maximum':16*1024**2,'seconds':600});self.assertEqual(calls[1][0][:2],['image','inspect'])
 def test_actual_base_wrong_digest_or_untyped_reference_refuses(self):
  host=SimpleNamespace(BASE='python:fixed@sha256:'+'a'*64)
  for refs in [['python@sha256:'+'b'*64],[],[7],{'wrong':'type'}]:
   with self.subTest(refs=refs),self.assertRaisesRegex(RuntimeError,'base_digest'):M.admit_public_base(lambda args,**kw:b'pull\n'if args[0]=='pull'else json.dumps(refs).encode(),host)
 def test_actual_hosted_entry_preserves_pull_before_create_and_native_mount_gate(self):
  tree=ast.parse(DRIVER.read_text());execute=next(n for n in tree.body if isinstance(n,ast.FunctionDef)and n.name=='execute');calls=[n for n in ast.walk(execute)if isinstance(n,ast.Call)]
  pull=next(n for n in calls if isinstance(n.func,ast.Name)and n.func.id=='admit_public_base');create=next(n for n in calls if isinstance(n.func,ast.Attribute)and n.func.attr=='create_argv');self.assertLess(pull.lineno,create.lineno)
  self.assertTrue(any(isinstance(n.func,ast.Attribute)and n.func.attr=='validate_workspace_observation'for n in calls));self.assertTrue(any(isinstance(n,ast.Constant)and n.value=='hosted_disk_reserve'for n in ast.walk(execute)))
if __name__=='__main__':unittest.main(verbosity=2)
