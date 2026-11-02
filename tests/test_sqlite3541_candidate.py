"""Source/configuration tests and tiny public fixtures, no SQLite/CI/build work."""
from copy import deepcopy
from hashlib import sha3_256
from pathlib import Path
from types import ModuleType
from unittest.mock import patch
import ast,io,json,os,subprocess,tempfile,unittest,zipfile

ROOT=Path(__file__).resolve().parents[1]
AREA=ROOT/'deploy/security/sqlite3541'
B=ModuleType('owned_sqlite3541_builder');B.__file__=str(AREA/'build.py')
exec(compile((AREA/'build.py').read_bytes(),B.__file__,'exec'),B.__dict__)

class CandidateSourceTests(unittest.TestCase):
 def test_three_distro_bases_stay_pinned_and_actual_consumer_versions_distinct(self):
  self.assertEqual(set(B.PROFILES),{'trixie','noble','resolute'})
  for profile in B.PROFILES.values():self.assertRegex(profile['base'],r'@sha256:[0-9a-f]{64}$')
  self.assertEqual(B.PROFILES['noble']['python'],'/usr/bin/python3.12')
  self.assertEqual(B.PROFILES['resolute']['python'],'/usr/bin/python3.14')
 def test_canonical_parser_and_legacy_SONAME_flags_preserve_feature_and_limits(self):
  old=['COMPILER=gcc-14.2.0','ENABLE_FTS3','ENABLE_FTS4','ENABLE_FTS5','ENABLE_SESSION','ENABLE_RTREE',
   'ENABLE_UPDATE_DELETE_LIMIT','MAX_FUNCTION_ARG=127','MAX_VARIABLE_NUMBER=250000','MAX_DEFAULT_PAGE_SIZE=32768','TEMP_STORE=1']
  configure,flags=B.options(old)
  self.assertIn('--enable-update-limit',configure);self.assertIn('--soname=legacy',configure);self.assertIn('--disable-carray',configure)
  self.assertIn('-DSQLITE_MAX_FUNCTION_ARG=127',flags);self.assertIn('-DSQLITE_MAX_DEFAULT_PAGE_SIZE=32768',flags)
  self.assertEqual(set(flags),{'-DSQLITE_'+v for v in old if not v.startswith('COMPILER=')})
  self.assertNotIn('--disable-session',configure);self.assertNotIn('--disable-fts5',configure)
 def test_malformed_or_missing_legacy_feature_macros_fail_before_commands(self):
  for rows in ([],['ENABLE_UPDATE_DELETE_LIMIT']*2,['ENABLE_UPDATE_DELETE_LIMIT','BAD=1;execute'],['ENABLE_FTS5'],[True]):
   with self.subTest(rows=rows),self.assertRaises(RuntimeError):B.options(rows)
 def test_new_exports_are_allowed_old_API_SONAME_or_dependencies_cannot_disappear(self):
  before={'soname':'libsqlite3.so.0','needed':['libc.so.6','libm.so.6'],'exports':['sqlite3_open','sqlite3_close']}
  after=deepcopy(before);after['exports'].append('sqlite3_result_str');B.compatible_abi(before,after)
  for field,value in [('soname','libsqlite3.so.1'),('needed',['libc.so.6']),('exports',['sqlite3_open'])]:
   changed=deepcopy(after);changed[field]=value
   with self.subTest(field=field),self.assertRaisesRegex(RuntimeError,'backward_abi'):B.compatible_abi(before,changed)
 def test_ELF_loader_paths_are_not_added_to_the_installed_library(self):
  dynamic=b' (SONAME) Library soname: [libsqlite3.so.0]\n (NEEDED) Shared library: [libc.so.6]\n'
  symbols=b'sqlite3_open T 123 10\n'
  with patch.object(B,'run',side_effect=[dynamic,symbols]):self.assertEqual(B.elf(Path('/OWNED-PUBLIC-NOT-OPENED'),ROOT,'owned')['soname'],'libsqlite3.so.0')
  for kind in('RPATH','RUNPATH'):
   bad=dynamic+(' ('+kind+') Library path: [/UNADMITTED]\n').encode()
   with patch.object(B,'run',side_effect=[bad,symbols]),self.assertRaisesRegex(RuntimeError,'no_loader_override'):B.elf(Path('/OWNED-PUBLIC-NOT-OPENED'),ROOT,'owned')
 def test_legacy_initializer_uses_official_per_connection_builtin_hook(self):
  raw=(AREA/'legacy_fp.c').read_text();builder=(AREA/'build.py').read_text()
  self.assertIn('sqlite3_db_config(db, SQLITE_DBCONFIG_FP_DIGITS, 15, &digits)',raw)
  self.assertIn('rc == SQLITE_OK && digits == 15 ? SQLITE_OK : SQLITE_ERROR',raw)
  self.assertNotIn('sqlite3_auto_extension',raw);self.assertIn('SQLITE_EXTRA_AUTOEXT=sqlite3_trading_legacy_fp_init',builder)
  self.assertNotIn('SQLITE_EXTRA_INIT_MUTEXED=',builder)
  self.assertLess(builder.index("out/'lifecycle.log'"),builder.index("out/'candidate.log'"))
  self.assertNotIn('db->nFpDigit',raw);self.assertNotIn('LD_LIBRARY_PATH=',builder)
 def test_owned_wrong_source_and_unsafe_ZIP_members_are_rejected(self):
  with tempfile.TemporaryDirectory(prefix='sqlite3541-public-',dir=ROOT/'tests')as folder:
   root=Path(folder);archive=root/'source.zip';archive.write_bytes(b'not a canonical archive')
   with self.assertRaisesRegex(RuntimeError,'source_size'):B.extract_source(archive,root/'bad-size')
   for index,name in enumerate(['sqlite-src-3540100/../escape','/sqlite-src-3540100/absolute','sqlite-src-3540100/back\\slash']):
    buf=io.BytesIO()
    with zipfile.ZipFile(buf,'w')as z:z.writestr(name,b'PUBLIC SYNTHETIC')
    raw=buf.getvalue();archive.write_bytes(raw)
    with patch.object(B,'SOURCE_BYTES',len(raw)),patch.object(B,'SOURCE_SHA3',sha3_256(raw).hexdigest()),self.assertRaisesRegex(RuntimeError,'source_member'):
     B.extract_source(archive,root/('bad-member-'+str(index)))
   self.assertFalse((root/'escape').exists())
 def test_small_authentic_shape_source_extract_checks_VERSION_and_manifest(self):
  with tempfile.TemporaryDirectory(prefix='sqlite3541-public-',dir=ROOT/'tests')as folder:
   root=Path(folder);buf=io.BytesIO()
   with zipfile.ZipFile(buf,'w')as z:
    for name,value in [('VERSION','3.54.1\n'),('manifest.uuid',B.SOURCE_ID+'\n')]:
     entry=zipfile.ZipInfo('sqlite-src-3540100/'+name);entry.external_attr=0o100644<<16;z.writestr(entry,value)
   raw=buf.getvalue();archive=root/'source.zip';archive.write_bytes(raw)
   with patch.object(B,'SOURCE_BYTES',len(raw)),patch.object(B,'SOURCE_SHA3',sha3_256(raw).hexdigest()):B.extract_source(archive,root/'source')
   self.assertEqual((root/'source/VERSION').read_text(),'3.54.1\n')
 def test_official_upstream_tests_are_not_claimed_as_production15_or_business_acceptance(self):
  tree=ast.parse((AREA/'build.py').read_bytes());source=(AREA/'build.py').read_text()
  self.assertIn("['/usr/bin/make','-j2','test']",source)
  self.assertIn("'same_library_as_production15':False",source);self.assertIn("'business81_or_App32_run':False",source)
  self.assertIn("'security_accepted':False",source)
  self.assertEqual([n.name for n in tree.body if isinstance(n,ast.FunctionDef)and n.name=='stage'],['stage'])
 def test_no_import_side_effect_and_local_build_or_stage_is_denied(self):
  with self.assertRaisesRegex(RuntimeError,'disposable_builder_only'):B.build('trixie')
  with patch.dict(os.environ,{},clear=True),self.assertRaisesRegex(RuntimeError,'hosted_CI_only'):B.stage('trixie',Path('/UNUSED_PUBLIC'))
 def test_lifecycle_and_process_tests_have_multiple_real_connection_boundaries(self):
  lifecycle=(AREA/'lifecycle.c').read_text();verify=(AREA/'verify.py').read_text()
  self.assertIn('pthread_create',lifecycle);self.assertIn('sqlite3_shutdown()',lifecycle);self.assertIn('sqlite3_initialize()',lifecycle)
  self.assertIn('SQLITE_DBCONFIG_FP_DIGITS, 0, &digits',lifecycle);self.assertIn('cycle < 4',lifecycle)
  self.assertIn('legacy_CAST_numeric_parity',verify);self.assertIn('quote_JSON_numeric_roundtrip',verify)
  self.assertIn('require_numeric_compatibility(baseline,numeric_observation(fresh))',verify)
  self.assertIn("sqlite3.connect(root/'candidate-backup.db')",verify);self.assertIn("connection.set_authorizer",verify)
 def test_vendor_shell_probe_gets_a_separate_writable_source_copy(self):
  with tempfile.TemporaryDirectory(prefix='sqlite3541-vendor-copy-',dir=ROOT/'tests')as folder:
   root=Path(folder);source=root/'canonical';(source/'test').mkdir(parents=True)
   (source/'test/shellB.test').write_bytes(b'PUBLIC SYNTHETIC source fixture')
   (source/'VERSION').write_bytes(b'3.54.1\n')
   before={str(p.relative_to(source)):p.read_bytes()for p in source.rglob('*')if p.is_file()}
   with patch.object(B.os,'chown')as ownership:result=B.vendor_source_copy(source,root/'vendor-source')
   self.assertEqual(result,root/'vendor-source')
   self.assertEqual({str(p.relative_to(source)):p.read_bytes()for p in source.rglob('*')if p.is_file()},before)
   (result/'test/probe.txt').write_bytes(b'xyzzy')
   self.assertFalse((source/'test/probe.txt').exists())
   self.assertEqual({call.args[0]for call in ownership.call_args_list},{result,*result.rglob('*')}-{result/'test/probe.txt'})
   self.assertTrue(all(call.args[1:]==(10001,10001)for call in ownership.call_args_list))
 def test_pinned_snapshot_bootstrap_keeps_TLS_verification_and_finite_retry(self):
  from hashlib import sha256
  bootstrap=(AREA/'bootstrap.sh').read_text();certificate=(AREA/'snapshot-public-isrg-root-x1.crt').read_bytes()
  self.assertEqual(sha256(certificate).hexdigest(),'22b557a27055b33606b6559f37703928d3e4ad79f110b407d04986e1843543d1')
  self.assertIn('Acquire::https::CaInfo=$ca',bootstrap);self.assertIn('Acquire::Retries=3',bootstrap)
  self.assertIn('timeout "$apt_limit" apt-get',bootstrap)
  self.assertIn('Acquire::AllowInsecureRepositories=false',bootstrap);self.assertIn('APT::Get::AllowUnauthenticated=false',bootstrap)
  self.assertNotIn('Verify-Peer=false',bootstrap);self.assertNotIn('Verify-Host=false',bootstrap)
  self.assertNotIn('http://snapshot.',bootstrap)
 def test_read_only_APT_retry_wrapper_runs_under_real_POSIX_shell(self):
  raw=(AREA/'bootstrap.sh').read_text();start=raw.index('apt_read() {');end=raw.index('\n}',start)+2
  with tempfile.TemporaryDirectory(prefix='sqlite3541-shell-',dir=ROOT/'tests')as folder:
   root=Path(folder);stub=root/'timeout';stub.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n');stub.chmod(0o755)
   script=raw[start:end]+'\nca=/PUBLIC-PINNED-CA\napt_read 300 -o APT::Update::Error-Mode=any update\n'
   result=subprocess.run(['/bin/sh','-eu','-c',script],env={'PATH':str(root)},capture_output=True,timeout=3,check=True)
   argv=result.stdout.decode().splitlines()
   self.assertEqual(argv[:4],['300','apt-get','-o','Acquire::https::CaInfo=/PUBLIC-PINNED-CA'])
   self.assertEqual(argv[-3:],['-o','APT::Update::Error-Mode=any','update']);self.assertEqual(result.stderr,b'')
 def test_vendor_failure_prints_real_upstream_errors_and_preserves_primary_failure(self):
  primary=RuntimeError('command_failed:vendor-standard.log')
  for diagnostic in (b'PUBLIC REAL ERROR OUTPUT',RuntimeError('diagnostic_failed')):
   with self.subTest(diagnostic=type(diagnostic).__name__),patch.object(B,'run',side_effect=[primary,diagnostic])as call:
    with self.assertRaises(RuntimeError)as caught:B.vendor_tests(Path('/PUBLIC-SOURCE'),Path('/PUBLIC-BUILD'),Path('/PUBLIC-OUT'))
    self.assertIs(caught.exception,primary)
    self.assertEqual(call.call_args_list[0].args[0],['/usr/bin/make','-j2','test'])
    self.assertEqual(call.call_args_list[1].args[0],['/usr/bin/tclsh8.6','/PUBLIC-SOURCE/test/testrunner.tcl','errors','--verbose'])

 def test_actual_CI_336_public_exports_are_retained_without_freezing_1091_private_internals(self):
  fixture=json.loads((ROOT/'tests/fixtures/sqlite3541-ci-41418809295-abi.json').read_bytes())
  before,after=fixture['before'],fixture['after']
  self.assertEqual(len(before['exports']),1427);self.assertEqual(len(after['exports']),353)
  self.assertEqual(len(B.public_exports(before)),336);self.assertEqual(len(B.public_exports(after)),353)
  self.assertEqual(B.public_exports(before)-B.public_exports(after),set())
  B.compatible_abi(before,after)
  for public in ('sqlite3_open','sqlite3session_create','sqlite3changeset_apply','sqlite3changegroup_new','sqlite3rebaser_create'):
   broken=deepcopy(after);broken['exports'].remove(public)
   with self.subTest(symbol=public),self.assertRaisesRegex(RuntimeError,'backward_abi'):B.compatible_abi(before,broken)
 def test_no_public_API_cannot_pass_and_private_changes_remain_disclosed(self):
  private={'soname':'libsqlite3.so.0','needed':['libc.so.6'],'exports':['sqlite3BtreeOpen']}
  with self.assertRaisesRegex(RuntimeError,'backward_abi'):B.compatible_abi(private,private)
  builder=(AREA/'build.py').read_text()
  self.assertIn("'removed_private_exports':",builder)
  self.assertIn("'old_required_public_exports_retained':True",builder)
  self.assertNotIn("'old_required_exports_retained':True",builder)
 def test_full_vendor_suite_remains_mandatory_after_fast_failure_checks(self):
  tree=ast.parse((AREA/'build.py').read_bytes());build=next(n for n in tree.body if isinstance(n,ast.FunctionDef)and n.name=='build')
  calls=[(n.lineno,n.func.id)for n in ast.walk(build)if isinstance(n,ast.Call)and isinstance(n.func,ast.Name)]
  self.assertEqual(sum(name=='vendor_tests'for _,name in calls),1)
  self.assertLess(next(line for line,name in calls if name=='compatible_abi'),next(line for line,name in calls if name=='vendor_tests'))

 def test_baseline_package_hashes_and_versions_are_fixed_before_any_candidate_build(self):
  expected={'trixie':(914584,'0a459adaffd901109f7811ab65f58e7a957b4907d05539cf3d1184efdcde0468'),
   'noble':(701602,'b1190bb72359f5fcc47406aa46065eaf4f1ca208085c51224a52b04bedc0b4bb'),
   'resolute':(719538,'ff4b5a1e7c028a4bcc30076e4d8197aad1a910df44f4dc8dcf253e9ee9a3a367')}
  for variant,(size,digest)in expected.items():
   self.assertEqual(B.PROFILES[variant]['baseline_pin'],{'bytes':size,'sha256':digest})
  builder=(AREA/'build.py').read_text()
  self.assertIn("need(pin(baseline_deb)==profile['baseline_pin'],'baseline_package_identity')",builder)
 def test_all_real_bootstrap_embedded_Python_parses_and_build_input_identity_is_exported(self):
  bootstrap=(AREA/'bootstrap.sh').read_text()
  for delimiter in ('BASELINE','VERIFY_BASELINE','BUILD_INPUTS'):
   code=bootstrap.split("<<'"+delimiter+"'\n",1)[1].split('\n'+delimiter+'\n',1)[0]
   compile(code,delimiter,'exec')
  builder=(AREA/'build.py').read_text()
  self.assertIn("shutil.copytree(work/'build-inputs',out/'build-inputs')",builder)
  self.assertIn("'build_inputs':pin(out/'build-inputs/provenance.json')",builder)
  self.assertIn("'actual_downloaded_build_packages'",bootstrap);self.assertIn("'actual_indexes'",bootstrap)
 def test_real_shell_archive_fallback_preserves_signed_TLS_sources_and_failure_status(self):
  raw=(AREA/'bootstrap.sh').read_text();start=raw.index('mode=immutable_snapshot\n')
  block=raw[start:raw.index('if [ "$mode" = immutable_snapshot ]; then',start)]
  for variant,first,second,success,expected_mode,calls in (
   ('noble',42,0,True,'signed_archive_fallback',2),('resolute',42,0,True,'signed_archive_fallback',2),
   ('noble',0,0,True,'immutable_snapshot',1),('trixie',42,0,False,None,1),('noble',42,43,False,None,2)):
   with self.subTest(variant=variant,first=first,second=second),tempfile.TemporaryDirectory(prefix='sqlite3541-fallback-',dir=ROOT/'tests')as folder:
    root=Path(folder);(root/'lists/partial').mkdir(parents=True);(root/'sources').write_text('old snapshot')
    local=block.replace('/etc/apt/sources.list',str(root/'sources')).replace('/var/lib/apt/lists',str(root/'lists'))
    self.assertNotIn('/etc/apt',local)
    script='set -eu\nvariant='+variant+'\nsuites="'+variant+' '+variant+'-updates '+variant+'-security"\nkey=/PUBLIC-UBUNTU-KEY\ncount=0\n'
    script+='apt_read() { count=$((count+1)); printf "%s\\n" "$*" >> "'+str(root/'calls')+'"; if [ "$count" = 1 ]; then return '+str(first)+'; else return '+str(second)+'; fi; }\n'
    script+=local+'\nprintf "%s:%s\\n" "$mode" "$snapshot_status"\n'
    result=subprocess.run(['/bin/sh','-c',script],capture_output=True,text=True,timeout=3)
    self.assertEqual(result.returncode==0,success)
    self.assertEqual(len((root/'calls').read_text().splitlines()),calls)
    if success:self.assertEqual(result.stdout.strip(),expected_mode+':'+str(first))
    if expected_mode=='signed_archive_fallback':
     sources=(root/'sources').read_text()
     self.assertIn('https://archive.ubuntu.com/ubuntu',sources);self.assertIn('signed-by=/PUBLIC-UBUNTU-KEY',sources)
     self.assertNotIn('check-valid-until=no',sources);self.assertNotIn('trusted=yes',sources)

class NumericContractTests(unittest.TestCase):
 def module(self):
  m=ModuleType('owned_numeric_contract');m.__file__=str(AREA/'verify.py');exec(compile((AREA/'verify.py').read_bytes(),m.__file__,'exec'),m.__dict__);return m
 def test_unused_quote_JSON_spelling_does_not_override_exact_CAST_and_values(self):
  m=self.module();old={'numeric_contract':m.NUMERIC_CONTRACT,'numeric_digest':'a'*64,'numeric_format_digests':{'quote':'b'*64}}
  new={'numeric_contract':m.NUMERIC_CONTRACT,'numeric_digest':'a'*64,'numeric_format_digests':{'quote':'c'*64},'numeric_roundtrip_failures':{'quote':0,'json_array':0,'json_object':0}}
  m.require_numeric_compatibility(old,new)
  with self.assertRaisesRegex(RuntimeError,'legacy_CAST_numeric_parity'):m.require_numeric_compatibility(old,dict(new,numeric_digest='d'*64))
 def test_every_quote_JSON_numeric_roundtrip_failure_still_blocks(self):
  m=self.module();base={'numeric_contract':m.NUMERIC_CONTRACT,'numeric_digest':'a'*64}
  for key in ('quote','json_array','json_object'):
   failures={'quote':0,'json_array':0,'json_object':0};failures[key]=1
   with self.subTest(key=key),self.assertRaisesRegex(RuntimeError,'quote_JSON_numeric_roundtrip'):m.require_numeric_compatibility(base,dict(base,numeric_roundtrip_failures=failures))
 def test_old_or_missing_numeric_contract_cannot_be_accepted(self):
  m=self.module();base={'numeric_contract':m.NUMERIC_CONTRACT,'numeric_digest':'a'*64};new=dict(base,numeric_roundtrip_failures={'quote':0,'json_array':0,'json_object':0})
  for value in (None,'legacy_all_JSON_bytes_equal'):
   with self.subTest(value=value),self.assertRaisesRegex(RuntimeError,'numeric_contract'):m.require_numeric_compatibility(base,dict(new,numeric_contract=value))

if __name__=='__main__':unittest.main()
