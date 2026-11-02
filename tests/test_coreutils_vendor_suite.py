"""OWNED SYNTHETIC transcript/parser fixtures; never actual vendor/C test proof.

No compiler, make, package, Git, Docker, network or application is executed.
All identities, modes, links, timestamps and late-close mutations are genuine
filesystem observations; no UID/stat/clock/result mask is used.
"""
from contextlib import ExitStack
from collections import Counter
from pathlib import Path
from types import ModuleType
from unittest.mock import patch
import json,os,subprocess,sys,tempfile,time,unittest

HERE=Path(__file__).resolve().parents[1]/'deploy/maintenance/coreutils_acl_build'
M=ModuleType('vendor_parser');M.__file__=str(HERE/'verify_vendor_suite.py');exec(compile(Path(M.__file__).read_bytes(),M.__file__,'exec'),M.__dict__)

class VendorTests(unittest.TestCase):
 def setUp(self):
  self.stack=ExitStack();self.addCleanup(self.stack.close)
  self.tmp=Path(self.stack.enter_context(tempfile.TemporaryDirectory(dir=HERE)));self.tmp.chmod(0o755)
  self.root=self.tmp/'source';self.root.mkdir(mode=0o755);self.root.chmod(0o755)
  self.started=time.time_ns()-10**9;self.calls=0;self.status={'coreutils':'PASS','gnulib':'PASS'};self.raw={};self.rebuild()
 def put(self,relative,raw):
  path=self.root/relative;path.parent.mkdir(mode=0o755,parents=True,exist_ok=True)
  for parent in(path.parent,*path.parent.parents):
   if parent==self.tmp:break
   if parent.is_relative_to(self.root):parent.chmod(0o755)
  path.write_bytes(raw);path.chmod(0o600);return path
 def rebuild(self):
  for name,(base,suite)in M.SCOPES.items():
   prefix=base+'/'if base else'';test='tests/ordinary.sh'if name=='coreutils'else'test-ordinary.sh'
   log='tests/ordinary.log'if name=='coreutils'else'test-ordinary.sh.log';ext='.sh'if name=='coreutils'else'.test'
   self.raw[name]=('GOLD_VENDOR_SELECTION_V2\nsuite_log='+suite+'\ncollect_skipped_logs=yes\nextension='+ext+'\ntest='+test+'\nlog='+log+'\n').encode()
   result=self.status[name];code={'PASS':0,'SKIP':77,'XFAIL':1,'FAIL':1,'XPASS':0,'ERROR':99}[result]
   body=(':test-result: '+result+'\n:global-test-result: '+result+'\n:recheck: '+('no'if result in{'PASS','SKIP','XFAIL'}else'yes')+'\n:copy-in-global-log: '+('no'if result=='PASS'else'yes')+'\n').encode()
   self.put(prefix+log[:-4]+'.trs',body)
   message=b'OWNED SYNTHETIC VENDOR OUTPUT; NOT ACTUAL CI\n'
   if result=='SKIP':message+=b'ordinary.sh: skipped test: fixture requires optional owned platform feature\n'
   if result=='XFAIL':message+=b'ordinary.sh: expected failure in this SYNTHETIC transcript\n'
   self.put(prefix+log,message+(result+' '+test+' (exit status: '+str(code)+')\n').encode())
   counts=Counter({k:0 for k in M.RESULTS});counts[result]=1
   self.put(prefix+suite,('OWNED SYNTHETIC SUMMARY; NEVER ACTUAL VENDOR PASS\n# TOTAL: 1\n'+''.join('# '+k+': '+str(counts[k])+'\n'for k in M.RESULTS)).encode())
 def guard(self):self.calls+=1
 def collect(self,**kw):
  args={'driver_binding':{'bytes':4879,'sha256':'08ff8986c3a0ce8bf32d25ec7e4752ffbce9c89589f018836c97a08b6573ac2c'},'owner':os.geteuid(),'guard':self.guard,'end':time.monotonic()+30,'started_at_ns':self.started};args.update(kw)
  return M._collect_at(self.root,self.raw,**args)
 def test_both_exact_scopes_gnulib_sh_suffix_counts_and_false_authority(self):
  value=self.collect();self.assertEqual(set(value['scopes']),{'coreutils','gnulib'})
  self.assertEqual(value['scopes']['gnulib']['tests'][0]['log']['path'],'gnulib-tests/test-ordinary.sh.log')
  self.assertEqual(value['scopes']['coreutils']['summary_file']['path'],'tests/test-suite.log')
  self.assertEqual(value['observed_skip_count'],0);self.assertGreater(self.calls,20)
  for key in('actual_unfiltered_command_authority_implemented','actual_vendor_process_completion_proven','all_configured_default_tests_proven_by_parser',
   'zero_skip_coverage_claimed','owned_c_fixtures_included','all_cves_fixed','security_accepted'):self.assertIs(value[key],False)
 def test_actual_skip_xfail_statuses_and_verbatim_reasons_are_retained(self):
  self.status.update(coreutils='SKIP',gnulib='XFAIL');self.rebuild();value=self.collect()
  self.assertEqual((value['observed_skip_count'],value['observed_xfail_count']),(1,1))
  a=value['scopes']['coreutils']['tests'][0];self.assertEqual(a['actual_driver_exit_status'],77)
  self.assertTrue(any('requires optional owned platform'in r['verbatim']for r in a['reason_excerpts']))
  b=value['scopes']['gnulib']['tests'][0];self.assertEqual(b['status'],'XFAIL');self.assertEqual(b['actual_driver_exit_status'],1)
 def test_fail_xpass_error_are_never_accepted(self):
  for status in('FAIL','XPASS','ERROR'):
   self.status['coreutils']=status;self.rebuild()
   with self.subTest(status=status),self.assertRaisesRegex(RuntimeError,'vendor_failed_test'):self.collect()
 def test_empty_missing_or_duplicate_scope_selection_refuses(self):
  original=dict(self.raw)
  for bad in({}, {'coreutils':original['coreutils']},{**original,'foreign':b'raw'}):
   self.raw=bad
   with self.assertRaisesRegex(RuntimeError,'vendor_scopes'):self.collect()
  self.raw=original
 def test_selection_paths_duplicates_mapping_and_summary_scope_are_closed(self):
  original=self.raw['coreutils']
  for a,b in((b'tests/ordinary.log',b'../outside.log'),(b'suite_log=tests/test-suite.log',b'suite_log=test-suite.log'),
   (b'log=tests/ordinary.log',b'log=tests/other.log'),(b'extension=.sh',b'extension=.xpl'),
   (b'GOLD_VENDOR_SELECTION_V2',b'READY_TRUE')):
   self.raw['coreutils']=original.replace(a,b)
   with self.subTest(change=b),self.assertRaises(RuntimeError):self.collect()
  self.raw['coreutils']=original+b'log=tests/ordinary.log\n'
  with self.assertRaisesRegex(RuntimeError,'vendor_selection_duplicate'):self.collect()
 def test_selection_line_and_input_are_bounded_before_split_accumulation(self):
  with self.assertRaisesRegex(RuntimeError,'vendor_selection_bound'):M.selection(b'X'*(M.MAX_SELECTION+1),'coreutils')
  with self.assertRaisesRegex(RuntimeError,'vendor_selection_line_bound'):M.selection(b'GOLD_VENDOR_SELECTION_V2\n'+b'X'*4097,'coreutils')
 def test_old_manual_global_key_and_duplicate_or_extra_trs_fields_refuse(self):
  path=self.root/'tests/ordinary.trs';original=path.read_bytes()
  for body in(original.replace(b'global-test-result',b'test-global-result'),original+b':test-result: PASS\n',original+b':ready: yes\n'):
   path.write_bytes(body)
   with self.assertRaises(RuntimeError):self.collect()
 def test_summary_counts_and_driver_exit_suffix_are_independent(self):
  path=self.root/'tests/test-suite.log';raw=path.read_bytes();path.write_bytes(raw.replace(b'# PASS: 1',b'# PASS: 0'))
  with self.assertRaisesRegex(RuntimeError,'vendor_summary_counts'):self.collect()
  self.rebuild();path=self.root/'tests/ordinary.log';path.write_bytes(path.read_bytes().replace(b'exit status: 0',b'exit status: 77'))
  with self.assertRaisesRegex(RuntimeError,'vendor_log_exit'):self.collect()
 def test_missing_pair_is_not_silently_skipped(self):
  (self.root/'gnulib-tests/test-ordinary.sh.trs').unlink()
  with self.assertRaises(FileNotFoundError):self.collect()
 def test_log_driver_name_must_match_the_actual_configured_test(self):
  path=self.root/'tests/ordinary.log';path.write_bytes(path.read_bytes().replace(b'PASS tests/ordinary.sh',b'PASS tests/different.sh'))
  with self.assertRaisesRegex(RuntimeError,'vendor_log_driver_suffix'):self.collect()
 def test_hardlink_refuses_before_aliased_payload_read(self):
  path=self.root/'tests/ordinary.log';os.link(path,self.tmp/'alias');inode=path.stat().st_ino;actual=os.read
  def read(fd,*args):
   self.assertNotEqual(os.fstat(fd).st_ino,inode,'hardlink payload must not be opened/read');return actual(fd,*args)
  with patch.object(M.os,'read',read),self.assertRaisesRegex(RuntimeError,'vendor_output_metadata'):self.collect()
 def test_symlink_file_or_parent_never_escapes_owned_source(self):
  path=self.root/'tests/ordinary.log';outside=self.tmp/'owned-outside';outside.write_bytes(b'owned outside never read');path.unlink();path.symlink_to(outside)
  with self.assertRaisesRegex(RuntimeError,'vendor_output_metadata'):self.collect()
  path.unlink();self.rebuild();directory=self.root/'tests';directory.rename(self.root/'retained-tests');directory.symlink_to(self.root/'retained-tests',target_is_directory=True)
  with self.assertRaisesRegex(RuntimeError,'vendor_parent'):self.collect()
 def test_stale_previous_run_file_metadata_is_refused(self):
  path=self.root/'tests/ordinary.trs';os.utime(path,ns=(1,1))
  with self.assertRaisesRegex(RuntimeError,'vendor_output_metadata'):self.collect()
 def test_no_ready_boolean_or_expired_guard_can_open_outputs(self):
  with self.assertRaisesRegex(RuntimeError,'vendor_authority_result'):self.collect(guard=lambda:True)
  with self.assertRaisesRegex(RuntimeError,'vendor_scope'):self.collect(end=time.monotonic()-1)
 def test_actual_last_file_close_timestamp_change_refuses(self):
  path=self.root/'tests/ordinary.trs';inode=path.stat().st_ino;actual=os.close;changed=[]
  def close(fd):
   selected=os.fstat(fd).st_ino==inode;actual(fd)
   if selected and not changed:path.chmod(0o604);changed.append(True)
  with patch.object(M.os,'close',close),self.assertRaisesRegex(RuntimeError,'vendor_output_closed_changed'):self.collect()
  self.assertTrue(changed)
 def test_actual_last_directory_close_cannot_change_an_already_closed_file(self):
  path=self.root/'tests/ordinary.log';inode=self.root.stat().st_ino;actual=os.close;changed=[]
  def close(fd):
   selected=os.fstat(fd).st_ino==inode;actual(fd)
   if selected and not changed:path.chmod(0o604);changed.append(True)
  with patch.object(M.os,'close',close),self.assertRaisesRegex(RuntimeError,'vendor_output_closed_changed'):self.collect()
  self.assertTrue(changed)
 def test_parent_open_fd_registered_before_real_postopen_race(self):
  target=self.root/'tests';actual=os.open;opened=[]
  def opening(path,*args,**kw):
   fd=actual(path,*args,**kw)
   if Path(path)==target and not opened:target.chmod(0o700);opened.append(fd)
   return fd
  with patch.object(M.os,'open',opening),self.assertRaisesRegex(RuntimeError,'vendor_parent_changed'):self.collect()
  self.assertEqual(len(opened),1)
  with self.assertRaises(OSError):os.fstat(opened[0])
 def test_receipt_budget_refuses_before_unbounded_result_accumulation(self):
  with patch.object(M,'MAX_RECEIPT',65536),self.assertRaisesRegex(RuntimeError,'vendor_receipt_bound'):self.collect()
 def test_genuine_nonroot_production_identity_and_fixed_path_are_not_overridable(self):
  import inspect
  self.assertNotIn('root',inspect.signature(M.collect).parameters);self.assertNotIn('owner',inspect.signature(M.collect).parameters)
  self.assertEqual(M.ROOT,Path('/build/coreutils-acl/coreutils/source'))
  if os.geteuid()!=10001 or os.getegid()!=10001:
   with self.assertRaisesRegex(RuntimeError,'vendor_actual_unprivileged_user'):M.collect({},driver_binding={'bytes':4879,'sha256':'08ff8986c3a0ce8bf32d25ec7e4752ffbce9c89589f018836c97a08b6573ac2c'},guard=lambda:None,end=time.monotonic()+5,started_at_ns=self.started)
 def test_default_cli_and_import_do_not_read_or_execute(self):
  result=subprocess.run([sys.executable,'-I','-S','-B',str(HERE/'verify_vendor_suite.py')],check=True,capture_output=True,timeout=5)
  value=json.loads(result.stdout);self.assertEqual(value['status'],'UNARMED');self.assertFalse(value['files_read']);self.assertFalse(value['command_executed'])
 def test_nested_gnulib_unicode_family_paths_are_confined_and_fully_collected(self):
  # Names are from the actual 9.7 gnulib source families, never a relaxed
  # absolute or outside namespace. Bodies are explicit synthetic transcripts.
  names=('unictype/test-ctype_alnum','unistr/test-u8-mbtoucr','uniwidth/test-uc_width2.sh')
  self.raw['gnulib']=('GOLD_VENDOR_SELECTION_V2\nsuite_log=test-suite.log\ncollect_skipped_logs=no\nextension=.test\n'+''.join('test='+n+'\n'for n in names)+''.join('log='+n+'.log\n'for n in names)).encode()
  for name in names:
   self.put('gnulib-tests/'+name+'.trs',b':test-result: PASS\n:global-test-result: PASS\n:recheck: no\n:copy-in-global-log: no\n')
   self.put('gnulib-tests/'+name+'.log',('OWNED SYNTHETIC nested transcript\nPASS '+name+' (exit status: 0)\n').encode())
  self.put('gnulib-tests/test-suite.log',('OWNED SYNTHETIC SUMMARY\n# TOTAL: 3\n'+''.join('# '+k+': '+str(3 if k=='PASS'else 0)+'\n'for k in M.RESULTS)).encode())
  value=self.collect();rows=value['scopes']['gnulib']['tests'];self.assertEqual([r['test']for r in rows],list(names));self.assertEqual([r['log']['path']for r in rows],['gnulib-tests/'+n+'.log'for n in names])
 def test_nested_gnulib_canonical_path_rules_still_reject_escape_and_ambiguity(self):
  for name in('../outside','/outside','unistr/../outside','unistr//test','./unistr/test','unistr/./test','unistr\\test','unistr/test\x00','unistr/تست'):
   raw=('GOLD_VENDOR_SELECTION_V2\nsuite_log=test-suite.log\ncollect_skipped_logs=no\ntest='+name+'\nlog='+name+'.log\n').encode()
   with self.subTest(name=name),self.assertRaises(RuntimeError):M.selection(raw,'gnulib')
 def test_actual_automake17_skip_no_copy_matches_real_make_policy(self):
  self.status['gnulib']='SKIP';self.rebuild();self.raw['gnulib']=self.raw['gnulib'].replace(b'collect_skipped_logs=yes',b'collect_skipped_logs=no')
  path=self.root/'gnulib-tests/test-ordinary.sh.trs';path.write_bytes(path.read_bytes().replace(b'copy-in-global-log: yes',b'copy-in-global-log: no'))
  driver={'bytes':5215,'sha256':'05d9c1057354db27b965fcd154860f938186324ccd72fdb8c5767083194c8b5a'}
  self.assertEqual(self.collect(driver_binding=driver)['observed_skip_count'],1)
  path.write_bytes(path.read_bytes().replace(b'copy-in-global-log: no',b'copy-in-global-log: yes'))
  with self.assertRaisesRegex(RuntimeError,'vendor_trs_driver_contract'):self.collect(driver_binding=driver)
 def test_original_driver_skip_copy_stays_yes_even_when_new_option_policy_no(self):
  self.status['gnulib']='SKIP';self.rebuild();self.raw['gnulib']=self.raw['gnulib'].replace(b'collect_skipped_logs=yes',b'collect_skipped_logs=no')
  self.assertEqual(self.collect()['observed_skip_count'],1)
  path=self.root/'gnulib-tests/test-ordinary.sh.trs';path.write_bytes(path.read_bytes().replace(b'copy-in-global-log: yes',b'copy-in-global-log: no'))
  with self.assertRaisesRegex(RuntimeError,'vendor_trs_driver_contract'):self.collect()
 def test_both_reviewed_drivers_require_xfail_copy_yes(self):
  self.status['gnulib']='XFAIL';self.rebuild();self.assertEqual(self.collect()['observed_xfail_count'],1)
  driver={'bytes':5215,'sha256':'05d9c1057354db27b965fcd154860f938186324ccd72fdb8c5767083194c8b5a'}
  self.assertEqual(self.collect(driver_binding=driver)['observed_xfail_count'],1)
  path=self.root/'gnulib-tests/test-ordinary.sh.trs';path.write_bytes(path.read_bytes().replace(b'copy-in-global-log: yes',b'copy-in-global-log: no'))
  for binding in(driver,{'bytes':4879,'sha256':'08ff8986c3a0ce8bf32d25ec7e4752ffbce9c89589f018836c97a08b6573ac2c'}):
   with self.assertRaisesRegex(RuntimeError,'vendor_trs_driver_contract'):self.collect(driver_binding=binding)
 def test_driver_identity_unknown_wrong_types_or_partial_binding_refuses_before_open(self):
  for binding in({}, {'bytes':5215,'sha256':'0'*64},{'bytes':True,'sha256':'0'*64},{'bytes':5215,'sha256':False}):
   with patch.object(M.os,'open',side_effect=AssertionError('unknown driver cannot open outputs')),self.assertRaisesRegex(RuntimeError,'vendor_driver_source'):self.collect(driver_binding=binding)
 def test_protocol_requires_one_actual_skip_copy_policy_and_current_version(self):
  original=self.raw['gnulib']
  for raw in(original.replace(b'GOLD_VENDOR_SELECTION_V2',b'GOLD_VENDOR_SELECTION_V1'),original.replace(b'collect_skipped_logs=yes\n',b''),original.replace(b'collect_skipped_logs=yes',b'collect_skipped_logs=maybe'),original+b'collect_skipped_logs=no\n'):
   with self.assertRaises(RuntimeError):M.selection(raw,'gnulib')

if __name__=='__main__':unittest.main(verbosity=2)
