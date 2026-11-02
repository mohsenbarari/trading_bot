"""Owned real GNU make/patch graph controls; no C compiler, SQLite or Docker."""
from contextlib import ExitStack
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from types import ModuleType,SimpleNamespace
from unittest.mock import patch
import ast,base64,errno,json,os,subprocess,tempfile,time,unittest

ROOT=Path(__file__).resolve().parent.parent
BASE=Path('deploy/security/sqlite-ubuntu-gold2')
CHILD_FINALS=[]
AUTHENTIC_NOBLE_EDITS=[['  $(TOP)/ext/intck/test_intck.c  \\\n  $(TOP)/ext/intck/sqlite3intck.c \\\n', ''], ['\t$(TCLSH_CMD) $(TOP)/tool/mksqlite3c.tcl $(AMALGAMATION_LINE_MACROS) $(EXTRA_SRC)\n', '\t$(TCLSH_CMD) $(TOP)/tool/mksqlite3c.tcl $(AMALGAMATION_LINE_MACROS)\n'], ['\t$(TCLSH_CMD) $(TOP)/tool/mksqlite3c.tcl --enable-recover $(AMALGAMATION_LINE_MACROS) $(EXTRA_SRC)\n', '\t$(TCLSH_CMD) $(TOP)/tool/mksqlite3c.tcl --enable-recover $(AMALGAMATION_LINE_MACROS)\n'], ['# Source and header files that shell.c depends on\nSHELL_DEP = \\\n    $(TOP)/src/shell.c.in \\\n    $(TOP)/ext/consio/console_io.c \\\n    $(TOP)/ext/consio/console_io.h \\\n    $(TOP)/ext/expert/sqlite3expert.c \\\n    $(TOP)/ext/expert/sqlite3expert.h \\\n    $(TOP)/ext/intck/sqlite3intck.c \\\n    $(TOP)/ext/intck/sqlite3intck.h \\\n    $(TOP)/ext/misc/appendvfs.c \\\n    $(TOP)/ext/misc/base64.c \\\n    $(TOP)/ext/misc/base85.c \\\n    $(TOP)/ext/misc/completion.c \\\n    $(TOP)/ext/misc/decimal.c \\\n    $(TOP)/ext/misc/fileio.c \\\n    $(TOP)/ext/misc/ieee754.c \\\n    $(TOP)/ext/misc/memtrace.c \\\n    $(TOP)/ext/misc/pcachetrace.c \\\n    $(TOP)/ext/misc/regexp.c \\\n    $(TOP)/ext/misc/series.c \\\n    $(TOP)/ext/misc/shathree.c \\\n    $(TOP)/ext/misc/sqlar.c \\\n    $(TOP)/ext/misc/uint.c \\\n    $(TOP)/ext/misc/zipfile.c \\\n    $(TOP)/ext/recover/dbdata.c \\\n    $(TOP)/ext/recover/sqlite3recover.c \\\n    $(TOP)/ext/recover/sqlite3recover.h \\\n    $(TOP)/src/test_windirent.c \\\n    $(TOP)/src/test_windirent.h\n', '# Source files that go into making shell.c\nSHELL_SRC = \\\n\t$(TOP)/src/shell.c.in \\\n\t$(TOP)/ext/consio/console_io.c \\\n\t$(TOP)/ext/consio/console_io.h \\\n\t$(TOP)/ext/misc/appendvfs.c \\\n\t$(TOP)/ext/misc/completion.c \\\n\t$(TOP)/ext/misc/decimal.c \\\n\t$(TOP)/ext/misc/basexx.c \\\n\t$(TOP)/ext/misc/base64.c \\\n\t$(TOP)/ext/misc/base85.c \\\n\t$(TOP)/ext/misc/fileio.c \\\n\t$(TOP)/ext/misc/ieee754.c \\\n\t$(TOP)/ext/misc/regexp.c \\\n\t$(TOP)/ext/misc/series.c \\\n\t$(TOP)/ext/misc/shathree.c \\\n\t$(TOP)/ext/misc/sqlar.c \\\n\t$(TOP)/ext/misc/uint.c \\\n\t$(TOP)/ext/expert/sqlite3expert.c \\\n\t$(TOP)/ext/expert/sqlite3expert.h \\\n\t$(TOP)/ext/misc/zipfile.c \\\n\t$(TOP)/ext/misc/memtrace.c \\\n\t$(TOP)/ext/misc/pcachetrace.c \\\n\t$(TOP)/ext/recover/dbdata.c \\\n\t$(TOP)/ext/recover/sqlite3recover.c \\\n\t$(TOP)/ext/recover/sqlite3recover.h \\\n\t$(TOP)/src/test_windirent.c\n'], ['shell.c:\t$(SHELL_DEP) $(TOP)/tool/mkshellc.tcl has_tclsh84\n', 'shell.c:\t$(SHELL_SRC) $(TOP)/tool/mkshellc.tcl has_tclsh84\n'], ['coretestprogs:\ttestfixture$(BEXE) sqlite3$(BEXE)\n', 'coretestprogs:\t$(TESTPROGS)\n'], ['testprogs:\t$(TESTPROGS) srcck1$(BEXE) fuzzcheck$(TEXE) sessionfuzz$(TEXE)\n', 'testprogs:\tcoretestprogs srcck1$(BEXE) fuzzcheck$(TEXE) sessionfuzz$(TEXE)\n']]
def pin(raw):return(len(raw),sha256(raw).hexdigest())
def load(relative,name):
 p=ROOT/relative;m=ModuleType(name);m.__file__=str(p)
 exec(compile(p.read_bytes(),str(p),'exec'),m.__dict__);return m
def authentic(profile):
 row=json.loads((ROOT/'tests/fixtures/sqlite_testfixture_harness.json').read_bytes())['receipt']['generated_before']
 raw=base64.b64decode(row['base64'],validate=True)
 if profile=='noble':
  for old,new in AUTHENTIC_NOBLE_EDITS:
   assert old and raw.count(old.encode())==1
   raw=raw.replace(old.encode(),new.encode())
 expected={'noble':(52789,'9090f59eb24e9f168cce0faf173011a210bac5cff2548a0683db7782ad7b1273'),
  'resolute':(53067,'28c0cd79217d77c89c0ca3ccd39ed3937285f006384f36737de26a1db4bdc7b7')}
 assert pin(raw)==expected[profile]
 return raw

class UbuntuFullCoreTests(unittest.TestCase):
 def setUp(self):
  self.stack=ExitStack();self.addCleanup(self.stack.close)
  self.tmp=Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='.owned-full-core-',dir=ROOT/'tests')))
  self.counter=0
 def source(self,profile):
  self.counter+=1;work=self.tmp/(profile+str(self.counter));work.mkdir(mode=0o700)
  src=work/'source';src.mkdir(mode=0o700);raw=authentic(profile)
  for name in('Makefile','Makefile.in'):(src/name).write_bytes(raw);(src/name).chmod(0o600)
  r=load(BASE/profile/'sourcebuild.py','owned_full_core_'+profile);r.WORK=work
  original=r.bounded_command;started=[]
  def popen(argv,**kwargs):
   self.assertIn(argv[0],('/usr/bin/make','/usr/bin/patch'))
   self.assertTrue(kwargs['close_fds']and kwargs['start_new_session']);self.assertNotIn('user',kwargs)
   if argv[0]=='/usr/bin/make':self.assertIn('--dry-run',argv);self.assertIn('-rR',argv)
   child=subprocess.Popen(argv,**kwargs);started.append((child,child.stdout.fileno()));return child
  r.subprocess=SimpleNamespace(Popen=popen,DEVNULL=subprocess.DEVNULL,PIPE=subprocess.PIPE,STDOUT=subprocess.STDOUT)
  def bounded(argv,**kwargs):
   cut=len(started)
   try:return original(argv,**kwargs)
   finally:
    for child,fd in started[cut:]:
     self.assertIsNotNone(child.returncode);self.assertTrue(child.stdout.closed)
     with self.assertRaises(OSError)as closed:os.fstat(fd)
     self.assertEqual(closed.exception.errno,errno.EBADF)
     CHILD_FINALS.append({'kind':Path(argv[0]).name,'code':child.returncode,'reaped':True,'original_pipe_closed_EBADF':True})
  r.bounded_command=bounded
  return r,src,raw
 def produce(self,profile):
  r,src,raw=self.source(profile);receipt=r.prepare_vendor_testfixture(src)
  self.assertIs(r.testfixture_harness_contract(receipt),receipt)
  return r,src,raw,receipt

 def test_actual_two_profile_make_patch_producer_selects_complete_core_both_modes(self):
  for profile in('noble','resolute'):
   with self.subTest(profile=profile):
    r,src,before,row=self.produce(profile);after=(src/'Makefile').read_bytes()
    self.assertEqual(after,r.testfixture_after(before));self.assertEqual((src/'Makefile.in').read_bytes(),before)
    self.assertIn(r.TESTFIXTURE_TARGET_AFTER,after);self.assertEqual(row['schema'],'LOCAL_generated_testfixture_full_core/3')
    new=[]
    for mode in('0','1'):
     words=r.testfixture_words(r.testfixture_decode(row['gnu_make'][mode]['after']['stdout'],65536));new.append(words)
     self.assertEqual(words.count(b'sqlite3.c'),1);self.assertNotIn(b'libsqlite3.la',words)
     self.assertNotIn(b'/OWNED_SOURCE/src/btree.c',words);self.assertNotIn(b'/OWNED_SOURCE/src/vdbe.c',words)
     self.assertEqual(words.count(b'/OWNED_SOURCE/ext/fts3/fts3_term.c'),1)
    self.assertEqual(new[0],new[1]);self.assertFalse(row['test_selection_changed']);self.assertFalse(row['official_or_production_fix'])

 def test_actual_make_target_requires_relink_after_owned_makefile_patch(self):
  for profile in('noble','resolute'):
   with self.subTest(profile=profile):
    r,src,before=self.source(profile)
    def program(raw):
     line=next(x for x in raw.splitlines(keepends=True)if x.startswith(b'testfixture$(TEXE):'))
     return (b'TOP:=source-members\nUSE_AMALGAMATION:=0\n'+r.testfixture_stanzas(raw)+
      b'TEXE:=\nLTLINK:=NEVER_EXECUTED_TEST_COMPILER\nLIBTCL:=-lPUBLIC_TCL\nTLIBS:=-lm\n'+
      b'TEMP_STORE:=-DTEMP_STORE=1\nTESTFIXTURE_FLAGS:=-DSQLITE_TEST=1 -DSQLITE_PRIVATE=\n'+line+
      b'\t$(LTLINK) -DSQLITE_NO_SYNC=1 $(TEMP_STORE) $(TESTFIXTURE_FLAGS) \\\n\t\t-o $@ $(TESTFIXTURE_SRC) $(LIBTCL) $(TLIBS)\n'+
      b'$(TESTFIXTURE_SRC) has_tclsh85: ;\n')
    for word in set(r.re.findall(rb'\$\(TOP\)/([A-Za-z0-9_./+-]+)',r.testfixture_stanzas(before))):
     p=src/'source-members'/word.decode();p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b'OWNED DEPENDENCY; NOT C SOURCE\n');os.utime(p,ns=(500_000_000,500_000_000))
    for name in('parse.c','fts5.c','sqlite3.c','libsqlite3.la','has_tclsh85'):
     p=src/name;p.write_bytes(b'OWNED DEPENDENCY; NEVER EXECUTED\n');os.utime(p,ns=(500_000_000,500_000_000))
    make=src/'Makefile';make.write_bytes(program(before));(src/'testfixture').write_bytes(b'OWNED EXISTING TARGET; NEVER EXECUTED\n')
    os.utime(make,ns=(1_000_000_000,1_000_000_000));os.utime(src/'testfixture',ns=(2_000_000_000,2_000_000_000))
    argv=['/usr/bin/make','--no-print-directory','--dry-run','-rR','-f','Makefile','testfixture']
    old=r.metadata(argv,cwd=src,maximum=65536);self.assertNotIn(b'NEVER_EXECUTED_TEST_COMPILER',old)
    make.write_bytes(program(r.testfixture_after(before)))
    new=r.metadata(argv,cwd=src,maximum=65536)
    self.assertEqual(new.count(b'NEVER_EXECUTED_TEST_COMPILER'),1);self.assertIn(b' sqlite3.c ',new)
    self.assertNotIn(b'libsqlite3.la',new);self.assertNotIn(b'/OWNED_SOURCE/src/btree.c',new)
    self.assertIn(b'-DSQLITE_TEST=1 -DSQLITE_PRIVATE=',new)

 def test_missing_duplicate_prior_dedup_or_modified_target_refuses_before_patch(self):
  for profile in('noble','resolute'):
   r,src,raw=self.source(profile)
   for label,changed in(('missing',raw.replace(r.TESTFIXTURE_CORE_BEFORE,b'')),
    ('duplicate',raw+r.TESTFIXTURE_CORE_BEFORE),('old-dedup',raw.replace(r.TESTFIXTURE_ASSIGNMENT_BEFORE,r.TESTFIXTURE_ASSIGNMENT_AFTER)),
    ('foreign-target',raw.replace(r.TESTFIXTURE_TARGET_BEFORE,r.TESTFIXTURE_TARGET_BEFORE.replace(b'has_tclsh85',b'FOREIGN')))):
    with self.subTest(profile=profile,case=label):
     (src/'Makefile').write_bytes(changed)
     with self.assertRaisesRegex(RuntimeError,'testfixture_exact_local_delta|testfixture_authentic_link_rule'):r.prepare_vendor_testfixture(src)
     self.assertEqual((src/'Makefile').read_bytes(),changed)

 def test_authenticated_source_mutant_and_unrelated_patch_write_are_rejected(self):
  for profile in('noble','resolute'):
   r,src,raw=self.source(profile);cut=raw.index(b'TESTSRC2 = ');changed=raw[:cut]+raw[cut:].replace(b'$(TOP)/src/btree.c',b'$(TOP)/src/OTHER.c',1);(src/'Makefile').write_bytes(changed)
   with self.assertRaisesRegex(RuntimeError,'testfixture_vendor_stanzas'):r.prepare_vendor_testfixture(src)
   (src/'Makefile').write_bytes(raw);original=r.command
   def extra(argv,**kwargs):
    original(argv,**kwargs)
    with(src/'Makefile').open('ab')as f:f.write(b'\nCFLAGS += OWNED_UNAUTHORIZED\n')
   with patch.object(r,'command',side_effect=extra):
    with self.assertRaisesRegex(RuntimeError,'testfixture_exact_local_delta'):r.prepare_vendor_testfixture(src)

 def test_full_core_contract_rejects_schema_missing_mode_flags_delta_and_raw_probe_mutants(self):
  for profile in('noble','resolute'):
   r,src,raw,row=self.produce(profile)
   for kind in('v2','missing-mode','partial-core','extra-link','other-byte','program','raw-order','bool-code','claim'):
    bad=deepcopy(row)
    if kind=='v2':bad['schema']='LOCAL_generated_testfixture_single_source/2'
    elif kind=='missing-mode':del bad['gnu_make']['0']
    elif kind=='partial-core':bad['generated_after']=r.testfixture_blob(raw.replace(r.TESTFIXTURE_ASSIGNMENT_BEFORE,r.TESTFIXTURE_ASSIGNMENT_AFTER))
    elif kind=='extra-link':bad['generated_after']=r.testfixture_blob(r.testfixture_after(raw).replace(b'$(LIBTCL) $(TLIBS)',b'$(LIBTCL) $(TLIBS) -lsqlite3'))
    elif kind=='other-byte':bad['generated_after']=r.testfixture_blob(r.testfixture_after(raw)+b'\nCFLAGS+=MUTANT\n')
    elif kind=='program':bad['gnu_make']['1']['after']['program']['sha256']='0'*64
    elif kind=='raw-order':
     b=r.testfixture_decode(bad['gnu_make']['0']['after']['stdout'],65536);a,z=b.split(b'=',1);words,tail=z.split(b'\n',1);bad['gnu_make']['0']['after']['stdout']=r.testfixture_blob(a+b'='+b' '.join(reversed(words.split()))+b'\n'+tail)
    elif kind=='bool-code':bad['gnu_make']['0']['after']['actual_returncode']=False
    else:bad['test_selection_changed']=True
    with self.subTest(profile=profile,kind=kind),self.assertRaises(RuntimeError):r.testfixture_harness_contract(bad)
   self.assertIs(r.testfixture_harness_contract(row),row)

 def test_genuine_harness_receipt_reaches_whole_two_profile_artifact_consumer(self):
  # Only this harness is produced by real make/patch; package/ABI/log rows are
  # explicit public synthetic metadata, never a package or SQL qualification.
  f=load(Path('tests/test_prepare_sqlite_wal_ci_context.py'),'owned_package_metadata_fixture')
  a=load(Path('scripts/prepare_sqlite_ubuntu_gold2_ci_context.py'),'owned_whole_ubuntu_consumer')
  for profile in('noble','resolute'):
   r,src,raw,harness=self.produce(profile);a.select_variant(profile);bound=lambda v:dict(zip(('bytes','sha256'),v))
   package=b'OWNED PUBLIC SYNTHETIC PACKAGE; NOT A DEB\n';rows={a.PACKAGE:package}
   package_pin=bound(pin(package))
   rows[a.BUILDINFO]=('Format: 1.0\nChecksums-Sha256:\n '+package_pin['sha256']+' '+str(len(package))+' '+a.PACKAGE+'\n').encode()
   bi=bound(pin(rows[a.BUILDINFO]));rows[a.CHANGES]=('Format: 1.8\nChecksums-Sha256:\n '+package_pin['sha256']+' '+str(len(package))+' '+a.PACKAGE+'\n '+bi['sha256']+' '+str(bi['bytes'])+' '+a.BUILDINFO+'\n').encode()
   for name,version in(('positive-baseline.json',a.BASE),('positive-candidate.json',a.VERSION)):
    value=f.positive(version);value.update({'python_version':a.SPEC['python'],'sqlite_runtime':a.SPEC['upstream'],'python_no_site':True,'system_library':a.SPEC['library']})
    value['sqlite_system_library']['path']=a.SPEC['library'];value['sqlite_stdlib_extension']['path']=a.SPEC['extension']
    if profile=='noble':value['candidate_view_checks']=None if version==a.BASE else {'rowid_in_view':True,'nested_join_rows':[[1,2,3]],'production_library_only':True,'security_accepted':False}
    rows[name]=value
   rows['abi-contract.json']={'soname':'libsqlite3.so.0','needed':['libc.so.6'],'exports':sorted(['sqlite3_open_v2','sqlite3_prepare_v2','sqlite3_backup_init','sqlite3_wal_checkpoint_v2']),'glibc_versions':['GLIBC_2.2.5']}
   rows['vendor-standard.log']=b'0 errors out of 15000 tests on OWNED PUBLIC FIXTURE\n'
   rows['vendor-fts5-regression.log']=b'\n'.join(f'fts5prefix2-4-{d}-{n}... Ok'.encode()for d in('full','col','none')for n in(1,2))+b'\n0 errors out of 30 tests on OWNED PUBLIC FIXTURE\n'
   p={'schema':'sqlite_wal_fts5_local_ubuntu_package/2','package':'libsqlite3-0','base_package_version':a.BASE,'local_package_version':a.VERSION,'upstream_version_unchanged':a.SPEC['upstream'],'architecture':'amd64',
    'repository_snapshot':r.SNAPSHOT,'repository_releases':{n:bound(v)for n,v in r.INRELEASES.items()},'source_date_epoch':r.SOURCE_EPOCH,'build_options':'nocheck parallel=2',
    'production_patch':bound(r.PATCH_PIN),'positive_verifier':bound(r.VERIFY_PIN),'wal_before':bound(r.WAL_BEFORE),'wal_after':bound(r.WAL_AFTER),
    'fts5_merge_patch':bound(r.FTS_PATCH_PIN),'fts5_upstream_regression_patch':bound(r.FTS_TEST_PATCH_PIN),'fts5_before':bound(r.FTS_BEFORE),'fts5_after':bound(r.FTS_AFTER),
    'official_fts5_commit':r.UPSTREAM_FTS_COMMIT,'base_image':r.BASE_IMAGE,'root_observed_consumer_profile':bound(r.PROFILE_PIN),
    'source_signer':{'fingerprint':r.SIGNER_FINGERPRINT,**bound(r.SIGNER_PIN)},'public_ca_bootstrap':r.CA_BOOTSTRAP,
    'package_doc_path_includes':['/usr/share/doc/libsqlite3-0','/usr/share/doc/libsqlite3-0/*'],'native_ca_certificates_package':'OWNED-1',
    'source_files':{n:bound(v)for n,v in r.SOURCE_FILES.items()},'test_only_compatibility':r.vendor_test_compatibility_expected(),'session_input_validation':dict(r.SESSION_INPUT_PROOF),
    'debian_rules':{'bytes':512,'sha256':'d'*64},'ubuntu_control':{'bytes':128,'sha256':'e'*64},'build_toolchain_packages':['OWNED gcc 1'],
    'package_file':{'name':a.PACKAGE,**package_pin},'local_build_metadata':{a.CHANGES:bound(pin(rows[a.CHANGES])),a.BUILDINFO:bi},
    'baseline_library':{k:rows['positive-baseline.json']['sqlite_system_library'][k]for k in('bytes','sha256')},'candidate_library':{k:rows['positive-candidate.json']['sqlite_system_library'][k]for k in('bytes','sha256')}}
   for key in('native_bundle_replaces_bootstrap_after_authenticated_install','source_maintainer_signature_verified','baseline_authenticated_reinstall_required','strict_full_package_payload_verification','compile_options_equal','elf_soname_exports_dependencies_equal','python_binary_unchanged','sqlite_stdlib_extension_unchanged','mapped_installed_libraries_verified','app_ci_restore_gate_required'):p[key]=True
   for key in('upstream_fault_suites_run','wal_reset_reproduction_performed','full_upstream_suite_pass_claimed','local_build_metadata_signed','runtime_loader_override_used','production_data_accessed','security_accepted'):p[key]=False
   with patch.object(f,'R',r):privilege=f.fixture_vendor_privilege()
   watched={'Makefile':{k:harness['generated_after'][k]for k in('bytes','sha256')},'debian/rules':p['debian_rules'],'debian/control':p['ubuntu_control'],'ext/session/sqlite3session.c':p['session_input_validation']['after'],'src/wal.c':p['wal_after'],'ext/fts5/fts5_index.c':p['fts5_after'],'ext/fts5/test/fts5prefix2.test':bound(r.FTS_TEST_AFTER),**{n:bound(v)for n,v in r.VENDOR_HELPERS.items()}}
   p['vendor_tests']={'same_configured_source':watched,'standard_and_official_regression':{n:r.vendor_log_contract(n,rows[n])for n in r.VENDOR_COMMANDS},'standard_scope':dict(r.VENDOR_STANDARD_SCOPE),'no_new_filters_or_exclusions':True,'no_new_filters_or_exclusions_scope':'test_selection_only','testfixture_harness':harness,'full_all_permutations_claimed':False,'default_omissions_are_not_waivers':True,'unprivileged_execution':privilege}
   if profile=='noble':p['rowid_nested_join_backport']=r.rowid_nested_join_expected()
   rows['provenance.json']=p
   def proofs(values):return{n:bound(pin(a.encoded(v)if n.endswith('.json')else v))for n,v in values.items()}
   self.assertTrue(a.verify_artifacts(r,rows,proofs(rows))['native_abi_checked'])
   for kind in('old-schema','missing','current-generated','altered-link'):
    bad=deepcopy(rows);vendor=bad['provenance.json']['vendor_tests']
    if kind=='old-schema':vendor['testfixture_harness']['schema']='LOCAL_generated_testfixture_single_source/2'
    elif kind=='missing':del vendor['testfixture_harness']
    elif kind=='current-generated':vendor['same_configured_source']['Makefile']['sha256']='0'*64
    else:vendor['testfixture_harness']['generated_after']=r.testfixture_blob(r.testfixture_after(raw)+b'\nLTLINK+=-lsqlite3\n')
    with self.subTest(profile=profile,kind=kind),self.assertRaises(RuntimeError):a.verify_artifacts(r,bad,proofs(bad))
   self.assertFalse(a.verify_artifacts(r,rows,proofs(rows))['security_accepted'])

 def test_pin_dag_joins_actual_both_recipes_contexts_runner_and_caller(self):
  a=load(Path('scripts/prepare_sqlite_ubuntu_gold2_ci_context.py'),'owned_fullcore_caller')
  runner=load(BASE/'runner/build_package.py','owned_fullcore_runner')
  manifest=json.loads((ROOT/BASE/'runner/manifest.json').read_bytes())
  self.assertEqual(len(a.PINS),23);self.assertEqual(manifest['shared_local_harness_schema'],'LOCAL_generated_testfixture_full_core/3')
  self.assertEqual(manifest['build_package'],dict(zip(('bytes','sha256'),pin((ROOT/BASE/'runner/build_package.py').read_bytes()))))
  for name,want in a.PINS.items():self.assertEqual(pin((ROOT/BASE/name).read_bytes()),want)
  for profile in('noble','resolute'):
   r=load(BASE/profile/'sourcebuild.py','owned_dag_'+profile);m=json.loads((ROOT/BASE/profile/'context-manifest.json').read_bytes())
   p=pin((ROOT/BASE/profile/'sourcebuild.py').read_bytes())
   self.assertEqual(m['files']['sourcebuild.py'],dict(zip(('bytes','sha256'),p)))
   self.assertEqual(runner.VARIANTS[profile]['pins']['sourcebuild.py'],p)
   self.assertEqual(manifest['variants'][profile]['pins']['sourcebuild.py'],list(p))
   a.select_variant(profile);self.assertEqual(a.source_inventory_counts()['context_regular_files'],15 if profile=='noble'else 14)

 def test_harness_phase_keeps_production_build_global_modes_and_all_vendor_commands(self):
  for profile in('noble','resolute'):
   r=load(BASE/profile/'sourcebuild.py','owned_orders_'+profile);tree=ast.parse(Path(r.__file__).read_bytes())
   build=next(n for n in tree.body if isinstance(n,ast.FunctionDef)and n.name=='build')
   calls=[n for n in ast.walk(build)if isinstance(n,ast.Call)and isinstance(n.func,ast.Name)]
   package=next(n.lineno for n in calls if n.func.id=='command'and isinstance(n.args[0],ast.List)and any(isinstance(x,ast.Constant)and x.value=='/usr/bin/dpkg-buildpackage'for x in n.args[0].elts))
   vendor=next(n.lineno for n in calls if n.func.id=='vendor_tests');self.assertLess(package,vendor)
   f=next(n for n in tree.body if isinstance(n,ast.FunctionDef)and n.name=='vendor_tests')
   calls=[(n.func.id,n.lineno)for n in ast.walk(f)if isinstance(n,ast.Call)and isinstance(n.func,ast.Name)]
   self.assertLess(next(l for n,l in calls if n=='prepare_vendor_testfixture'),next(l for n,l in calls if n=='vendor_unprivileged_commands'))
   self.assertEqual(r.VENDOR_COMMANDS,{'vendor-standard.log':['/usr/bin/make','test'],'vendor-fts5-regression.log':['./testfixture','ext/fts5/test/fts5prefix2.test']})
   self.assertEqual(r.ENV['DEB_BUILD_OPTIONS'],'nocheck parallel=2')
   self.assertNotIn(b'USE_AMALGAMATION=',r.TESTFIXTURE_LOCAL_PATCH);self.assertNotIn(b'TESTOPTS',r.TESTFIXTURE_LOCAL_PATCH)

if __name__=='__main__':unittest.main()
