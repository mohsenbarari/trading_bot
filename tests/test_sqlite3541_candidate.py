"""Source/configuration tests and tiny public fixtures, no SQLite/CI/build work."""
from copy import deepcopy
from hashlib import sha3_256
from pathlib import Path
from types import ModuleType
from unittest.mock import patch
import ast,io,json,os,tempfile,unittest,zipfile

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
 def test_legacy_initializer_uses_official_connection_API_and_mutexed_hook_only(self):
  raw=(AREA/'legacy_fp.c').read_text();builder=(AREA/'build.py').read_text()
  self.assertIn('sqlite3_db_config(db, SQLITE_DBCONFIG_FP_DIGITS, 15, &digits)',raw)
  self.assertIn('rc == SQLITE_OK && digits == 15 ? SQLITE_OK : SQLITE_ERROR',raw)
  self.assertIn('sqlite3_auto_extension',raw);self.assertIn('SQLITE_EXTRA_INIT_MUTEXED=sqlite3_trading_legacy_fp_init',builder)
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
  self.assertIn('legacy_CAST_JSON_numeric_parity',verify);self.assertIn('fresh_connection_numeric_parity',verify)
  self.assertIn("sqlite3.connect(root/'candidate-backup.db')",verify);self.assertIn("connection.set_authorizer",verify)

if __name__=='__main__':unittest.main()
