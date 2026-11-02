"""Labelled synthetic archives/owned files only; no package, Host or CI action."""
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from types import ModuleType,SimpleNamespace
from unittest.mock import patch
from zipfile import ZipFile,ZipInfo,ZIP_DEFLATED
import io,json,tempfile,unittest,warnings

ROOT=Path(__file__).resolve().parents[1]
def module(name,path):
 m=ModuleType(name);m.__file__=str(path);exec(compile(path.read_bytes(),str(path),'exec'),m.__dict__);return m
C=module('_owned_canonical',ROOT/'scripts/sqlite3541_package.py')
M=module('_owned_adapter',ROOT/'scripts/prepare_sqlite_wal_os_cache.py')

def fixture():
 profiles={};contents={}
 for variant in C.BASELINES:
  ver=C.version(variant);name='libsqlite3-0_'+ver+'_amd64.deb';package=b'PUBLIC SYNTHETIC DEB '+variant.encode();library=b'PUBLIC SYNTHETIC DSO '+variant.encode()
  profile={'version':ver,'package':{'path':name,**C.content_pin(package)},'installed_library':{'path':C.LIBRARY,**C.content_pin(library)},
   'artifact':{'id':101,'run_id':202,'run_attempt':1,'job_id':303,'head_sha':'a'*40,'workflow_id':404,
    'name':'sqlite3541-library-'+variant+'-202-1','bytes':1,'sha256':'b'*64,'members':3},
   'provenance':{'bytes':1,'sha256':'c'*64},'root_qualification':C.content_pin(b'PUBLIC SYNTHETIC ROOT QUALIFICATION; NOT SECURITY ACCEPTANCE')}
  proof={'schema':'sqlite3541_library_candidate/1','status':'BUILT_NOT_ACCEPTED','variant':variant,'source':C.SOURCE,
   'package':{'name':name,'version':ver,**C.content_pin(package)},'candidate_library':C.content_pin(library),
   'numeric_contract':C.NUMERIC_CONTRACT,'production15_targeted_compatibility':True,'old_new_database_reopen_passed':True,'thread_initialize_shutdown_passed':True,'old_required_public_exports_retained':True,
   'CLI_in_runtime_package':False,'upstream_core_source_modified':False,'production_data_accessed':False,'business81_or_App32_run':False,'host_installed':False,'security_accepted':False,
   'legacy_numeric_initializer':C.INITIALIZER,'production_cflags':'-O2 -DSQLITE_EXTRA_AUTOEXT=sqlite3_trading_legacy_fp_init',
   'baseline_package':{'version':C.BASELINES[variant],'bytes':100,'sha256':'d'*64},
   'vendor_default17_tests':{'argv':['make','-j2','test'],'actual_returncode':0,'raw_log':C.content_pin(b'PUBLIC SYNTHETIC VENDOR LOG'),'same_library_as_production15':False,'vendor_assertions_modified':False},
   'baseline_abi':{'soname':'libsqlite3.so.0'},'candidate_abi':{'soname':'libsqlite3.so.0'},
   'payload_regular_files':[{'path':C.LIBRARY.lstrip('/'),**C.content_pin(library)}]}
  raw=C.encoded(proof);profile['provenance']=C.content_pin(raw)
  members={name:package,'provenance.json':raw,'vendor-standard.log':b'PUBLIC SYNTHETIC VENDOR LOG'}
  archive=make_zip(members);profile['artifact'].update(C.content_pin(archive));profiles[variant]=profile;contents[variant]=(archive,members,proof)
 return{'schema':'sqlite3541_qualified_packages/1','armed':True,'source':C.SOURCE,'profiles':profiles},contents

def make_zip(members,edit=None):
 out=io.BytesIO()
 with ZipFile(out,'w',compression=ZIP_DEFLATED)as z:
  for name,body in members.items():
   row=ZipInfo(name);row.create_system=3;row.external_attr=(0o120777 if edit=='symlink'and name=='provenance.json'else 0o100644)<<16;row.compress_type=ZIP_DEFLATED;z.writestr(row,body)
  if edit=='traversal':z.writestr('../UNOWNED',b'PUBLIC SYNTHETIC')
  if edit=='duplicate':
   with warnings.catch_warnings():warnings.simplefilter('ignore',UserWarning);z.writestr('provenance.json',members['provenance.json'])
 return out.getvalue()

class CanonicalCacheTests(unittest.TestCase):
 def test_all_real_canonical_helper_and_initializer_pins_match_current_sources(self):
  helper=(ROOT/'scripts/sqlite3541_package.py').read_bytes()
  self.assertEqual(M.CANONICAL_MODULE,('sqlite3541_package.py',len(helper),sha256(helper).hexdigest()))
  repository=next(p for p in Path(__file__).resolve().parents if(p/'docs/memory/manifest.md').exists())
  initializer=(repository/'deploy/security/sqlite3541/legacy_fp.c').read_bytes()
  self.assertEqual(C.INITIALIZER,{'bytes':len(initializer),'sha256':sha256(initializer).hexdigest()})
  self.assertIn('int sqlite3_trading_legacy_fp_init(sqlite3 *db)',initializer.decode())
  self.assertNotIn('sqlite3_auto_extension(',initializer.decode())
 def test_unarmed_lock_cannot_fetch_or_install(self):
  value={'schema':'sqlite3541_qualified_packages/1','armed':False}
  with self.assertRaisesRegex(RuntimeError,'lock_unarmed'):C.validate_lock(value)
  with tempfile.TemporaryDirectory(prefix='.canonical-unarmed-',dir=ROOT)as folder,patch.object(M,'download',side_effect=AssertionError('network denied')),patch.object(M,'bounded',side_effect=AssertionError('installer denied')):
   root=Path(folder);raw=C.encoded(value)
   (root/'sqlite3541_package.py').write_bytes((ROOT/'scripts/sqlite3541_package.py').read_bytes())
   (root/'sqlite3541_packages.lock.json').write_bytes(raw)
   cache=root/'sqlite3541-capsule'
   with patch.object(M,'ROOT',root),patch.object(M,'CANONICAL_LOCK_SHA',sha256(raw).hexdigest()),self.assertRaisesRegex(RuntimeError,'lock_unarmed'):M.canonical_prepare(cache)
   self.assertFalse(cache.exists())
 def test_published_lock_pin_and_all_selected_root_qualified_profiles_join(self):
  raw=(ROOT/'scripts/sqlite3541_packages.lock.json').read_bytes()
  self.assertEqual(M.CANONICAL_LOCK_SHA,sha256(raw).hexdigest())
  lock=C.document(raw)
  for variant in C.BASELINES:
   selected=C.validate_lock(lock,variant)
   self.assertEqual(selected['artifact']['run_id'],41437637885)
   self.assertGreater(selected['root_qualification']['bytes'],0)
 def test_exact_profiles_versions_source_and_job_identity_are_required(self):
  lock,_=fixture()
  for variant in C.BASELINES:self.assertEqual(C.validate_lock(lock,variant),lock['profiles'][variant])
  partial=deepcopy(lock);partial['profiles']={'trixie':partial['profiles']['trixie']}
  self.assertEqual(C.validate_lock(partial),partial['profiles']['trixie'])
  with self.assertRaises(RuntimeError):C.validate_lock(partial,'noble')
  for change in('version','empty_profiles','source','bool_job','bool_bytes','artifact_name','extra'):
   wrong=deepcopy(lock);p=wrong['profiles']['trixie']
   if change=='version':p['version']='3.54.1-UNREVIEWED'
   elif change=='empty_profiles':wrong['profiles']={}
   elif change=='source':wrong['source']['source_id']='0'*64
   elif change=='bool_job':p['artifact']['job_id']=True
   elif change=='bool_bytes':p['package']['bytes']=True
   elif change=='artifact_name':p['artifact']['name']='OTHER'
   else:wrong['extra']=True
   with self.subTest(change=change),self.assertRaises(RuntimeError):C.validate_lock(wrong)
 def test_original_archive_deb_and_proof_are_admitted_without_reserialization(self):
  lock,contents=fixture();profile=C.validate_lock(lock);archive,members,_=contents['trixie']
  admitted=C.admit_archive(archive,profile)
  self.assertEqual(admitted,{'package':members[profile['package']['path']],'provenance':members['provenance.json']})
  manifest=C.document(C.cache_manifest(profile));self.assertEqual(manifest['provenance'],profile['provenance']);self.assertEqual(manifest['source_id'],C.SOURCE['source_id'])
  with patch.object(C,'ZipFile',side_effect=AssertionError('unmatched archive must not be opened')),self.assertRaisesRegex(RuntimeError,'archive_pin'):C.admit_archive(archive+b'X',profile)
 def test_failed_vendor_core_mutation_numeric_or_installed_payload_cannot_pass(self):
  lock,contents=fixture();profile=C.validate_lock(lock);_,_,proof=contents['trixie']
  for key,value in('vendor_default17_tests',None),('numeric_contract',None),('numeric_contract','OLD_UNQUALIFIED'),('production15_targeted_compatibility',False),('thread_initialize_shutdown_passed',False),('old_new_database_reopen_passed',False),('upstream_core_source_modified',True),('CLI_in_runtime_package',True),('security_accepted',True),('candidate_library',{'bytes':1,'sha256':'0'*64}):
   bad=deepcopy(proof)
   if key=='vendor_default17_tests':bad[key]['actual_returncode']=1
   else:bad[key]=value
   raw=C.encoded(bad);selected=deepcopy(profile);selected['provenance']=C.content_pin(raw)
   with self.subTest(key=key),self.assertRaises(RuntimeError):C.check_provenance(raw,selected)
  for raw in(b'{"a":1,"a":2}',b'{"a":NaN}'):
   with self.assertRaises(RuntimeError):C.document(raw)
 def test_all_zip_names_kinds_counts_and_selected_content_are_checked(self):
  lock,contents=fixture();profile=C.validate_lock(lock);_,members,_=contents['trixie']
  for edit in('symlink','traversal','duplicate','changed_deb'):
   body=deepcopy(members)
   if edit=='changed_deb':body[profile['package']['path']]=b'PUBLIC CHANGED'
   raw=make_zip(body,edit);selected=deepcopy(profile);selected['artifact'].update(C.content_pin(raw),members=len(body)+(1 if edit in('traversal','duplicate')else 0))
   with self.subTest(edit=edit),self.assertRaises(RuntimeError):C.admit_archive(raw,selected)
 def test_existing_publication_and_held_guards_preserve_three_original_cache_files(self):
  lock,contents=fixture();profile=C.validate_lock(lock);raw,members,_=contents['trixie']
  with tempfile.TemporaryDirectory(prefix='.canonical-owned-',dir=ROOT)as folder:
   root=Path(folder);archive=root/'public.zip';archive.write_bytes(raw);cache=root/'sqlite3541-capsule'
   with patch.object(M,'load_canonical',return_value=(C,profile)),patch.object(M,'download',side_effect=AssertionError('network denied')),patch.object(M,'bounded',side_effect=AssertionError('installer denied')):
    self.assertEqual(M.canonical_prepare(cache,archive)['status'],'cache_prepared')
    self.assertEqual({p.name for p in cache.iterdir()},{profile['package']['path'],'manifest.json','provenance.json'})
    self.assertEqual((cache/'provenance.json').read_bytes(),members['provenance.json'])
    self.assertEqual(M.canonical_prepare(cache,check_only=True)['status'],'cache_verified')
    with M.CacheLock(cache),self.assertRaisesRegex(RuntimeError,'cache_busy'):M.canonical_prepare(cache,check_only=True)
    (cache/'provenance.json').write_bytes(b'PUBLIC CHANGED PROOF')
    with self.assertRaisesRegex(RuntimeError,'file_pin'):M.canonical_prepare(cache,check_only=True)
 def test_old_defaults_and_new_exact_command_boundary_remain_separate(self):
  path=M.CAPSULE/M.PACKAGE;legacy=M.commands(path)
  self.assertIn(('/usr/bin/dpkg',*M.DOC_INCLUDES,'--install',str(path)),legacy)
  canonical=M.CANONICAL_CAPSULE/M.CANONICAL_PACKAGE;new=M.commands(canonical,canonical3541=True)
  self.assertIn(('/usr/bin/dpkg',*M.DOC_INCLUDES,'--install',str(canonical)),new)
  for wrong,selected in((path,True),(canonical,False)):
   with self.subTest(path=str(wrong)),self.assertRaisesRegex(RuntimeError,'installer_command'):M.commands(wrong,canonical3541=selected)
  self.assertFalse(any('apt-get'in row for row in new));self.assertEqual(M.VERSION,'3.46.1-7+deb13u2+gold2')
 def test_canonical_installer_changes_only_selected_sqlite_inventory_and_uses_doc_includes(self):
  lock,_=fixture();profile=C.validate_lock(lock);installed=[]
  class FakeHeld:
   def __enter__(self):return self
   def __exit__(self,*_):pass
   def check(self):pass
   def read(self,*args,**kwargs):return None
  def bounded(argv,allowed,**kwargs):
   row=tuple(argv);self.assertIn(row,allowed)
   if row==M.INVENTORY:
    version=profile['version']if installed else M.BASE_VERSION
    return ('libsqlite3-0:amd64\t'+version+'\tinstalled\nother\t1\tinstalled\n').encode()
   if argv[0]=='/usr/bin/dpkg-deb':return {'Package':b'libsqlite3-0','Version':profile['version'].encode(),'Architecture':b'amd64','Multi-Arch':b'same'}[argv[-1]]
   if '--install'in argv:installed.append(argv)
   return b''
  fake_lib=SimpleNamespace(prctl=lambda *args:0)
  with patch.object(M,'Held',FakeHeld),patch.object(M,'load_canonical',return_value=(C,profile)),patch.object(M,'verify_canonical_cache',return_value=SimpleNamespace(path=M.CANONICAL_CAPSULE/M.CANONICAL_PACKAGE)),patch.object(M,'bounded',side_effect=bounded),patch.object(M.ctypes,'CDLL',return_value=fake_lib):
   result=M.install_admitted(M.CANONICAL_CAPSULE,canonical3541=True)
  self.assertEqual(result['sqlite_package'],profile['version']);self.assertFalse(result['security_accepted']);self.assertEqual(len(installed),1)
  self.assertEqual(installed[0],['/usr/bin/dpkg',*M.DOC_INCLUDES,'--install',str(M.CANONICAL_CAPSULE/M.CANONICAL_PACKAGE)])

 def test_installer_refuses_unknown_baseline_and_unrelated_package_change(self):
  lock,_=fixture();profile=C.validate_lock(lock)
  class FakeHeld:
   def __enter__(self):return self
   def __exit__(self,*_):pass
   def check(self):pass
   def read(self,*args,**kwargs):pass
  for failure in ('unknown_baseline','unrelated_changed'):
   installed=[]
   def bounded(argv,allowed,**kwargs):
    self.assertIn(tuple(argv),allowed)
    if tuple(argv)==M.INVENTORY:
     version='UNKNOWN'if failure=='unknown_baseline'else profile['version']if installed else M.BASE_VERSION
     other='2'if installed else'1'
     return ('libsqlite3-0:amd64\t'+version+'\tinstalled\nother\t'+other+'\tinstalled\n').encode()
    if argv[0]=='/usr/bin/dpkg-deb':return {'Package':b'libsqlite3-0','Version':profile['version'].encode(),'Architecture':b'amd64','Multi-Arch':b'same'}[argv[-1]]
    if '--install'in argv:installed.append(argv)
    return b''
   with self.subTest(failure=failure),patch.object(M,'Held',FakeHeld),patch.object(M,'load_canonical',return_value=(C,profile)),patch.object(M,'verify_canonical_cache',return_value=SimpleNamespace(path=M.CANONICAL_CAPSULE/M.CANONICAL_PACKAGE)),patch.object(M,'bounded',side_effect=bounded),patch.object(M.ctypes,'CDLL',return_value=SimpleNamespace(prctl=lambda *args:0)),self.assertRaises(RuntimeError):
    M.install_admitted(M.CANONICAL_CAPSULE,canonical3541=True)
   self.assertEqual(len(installed),0 if failure=='unknown_baseline'else 1)

if __name__=='__main__':unittest.main()
