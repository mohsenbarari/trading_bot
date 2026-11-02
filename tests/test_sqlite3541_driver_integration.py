"""Synthetic cache/source contracts only; no CI, Docker, business or Host action."""
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from types import ModuleType
from unittest.mock import patch
import ast,json,os,subprocess,tempfile,unittest

ROOT=Path(__file__).resolve().parents[1]
# Source packet and ordinary repository both resolve without environment loading.
REPO=next(p for p in ROOT.parents if(p/'docs/memory/manifest.md').is_file())if not(ROOT/'docs/memory/manifest.md').is_file()else ROOT

def load(name,path):
 m=ModuleType(name);m.__file__=str(path);exec(compile(path.read_bytes(),str(path),'exec'),m.__dict__);return m
D=load('_owned_canonical_driver',ROOT/'scripts/prepare_market_capture2_image.py')
S=load('_owned_canonical_sealer',ROOT/'scripts/prepare_market_product_source.py')
F=load('_owned_canonical_fixture',ROOT/'tests/test_sqlite3541_cache_integration.py')

class DriverJoinTests(unittest.TestCase):
 def tearDown(self):D.configure_sqlite_family('gold2')
 def test_real_business81_preflight_accepts_coupled_outer_and_rejects_stale_driver_pin(self):
  # Execute the actual normal caller and actual Outer.source_contract together.
  # No fixture/image operation is reached; the real preflight authenticates
  # the driver bytes and checks both genuine glibc_runtime API signatures.
  raw_driver=(ROOT/D.SELF).read_bytes()
  raw_outer=(ROOT/D.OUTER).read_bytes()
  raw_business=(REPO/D.BUSINESS).read_bytes()
  raws={D.BUSINESS:raw_business,D.OUTER:raw_outer}
  controllers=[{'path':name,'bytes':len(raw),'sha256':sha256(raw).hexdigest()}
   for name,raw in((D.SELF,raw_driver),(D.OUTER,raw_outer),(D.BUSINESS,raw_business))]
  with tempfile.TemporaryDirectory(prefix='.canonical-business81-',dir=ROOT/'tests')as folder:
   root=Path(folder).resolve()
   D.preflight_business81_sources(root,raws,controllers)
   stale=raw_outer.replace(repr((len(raw_driver),sha256(raw_driver).hexdigest())).encode(),
    b"(148597, '6ae51c097f16b0c578b425083f323a5ac7f28307ed7e79b0551da20f93f365f8')")
   self.assertNotEqual(stale,raw_outer)
   with self.assertRaisesRegex(RuntimeError,'business81_source'):
    D.preflight_business81_sources(root,{**raws,D.OUTER:stale},controllers)
   changed=deepcopy(controllers);changed[0]['sha256']='0'*64
   with self.assertRaisesRegex(RuntimeError,'business81_source'):
    D.preflight_business81_sources(root,raws,changed)
 def contract(self):
  root=Path('/PUBLIC_UNIT_NOT_OPENED');args={'SOURCE_SHA':'a'*40,'SOURCE_TREE':'b'*40,'IMAGE_INPUT_SIGNATURE':'c'*64,'IMAGE_VERSION':'production-private-primary','BUSINESS_SOURCE_COMMIT':'d'*40,'BUSINESS_SOURCE_TREE':'e'*40,'BUSINESS_SOURCE_MANIFEST_SHA256':D.MANIFEST_SHA}
  return root,{'named_contexts':{'business-source':str(root/'sealed/product')},'default_context':str(root/'sealed/release'),'dockerfile':'deploy/market-data/Dockerfile.capture2','build_args':args}
 def test_explicit_family_switch_keeps_gold2_default_and_changes_only_canonical_cache_contract(self):
  D.configure_sqlite_family('gold2');self.assertEqual(D.NORMAL_CACHE_COUNT,32);self.assertEqual(D.SQLITE_LOCK,'scripts/sqlite_wal_package.lock.json')
  D.configure_sqlite_family('canonical3541');self.assertEqual(D.NORMAL_CACHE_COUNT,33);self.assertIn('sqlite3541',D.CACHE_ORIGINAL_GROUPS);self.assertNotIn('sqlite-wal',D.CACHE_ORIGINAL_GROUPS)
  self.assertEqual(D.SQLITE_LOCK,'scripts/sqlite3541_packages.lock.json');self.assertEqual(D.CACHE_ROOTS[1],'pip_packages/sqlite3541-capsule')
  with self.assertRaises(RuntimeError):D.configure_sqlite_family('UNKNOWN')
 def test_canonical_build_args_are_two_exact_added_values_and_no_secret_or_business_drift(self):
  root,contract=self.contract();D.configure_sqlite_family('gold2');old=D.build_argv(root,contract,'f'*64,1792972800,'123','1')
  D.configure_sqlite_family('canonical3541');new=D.build_argv(root,contract,'f'*64,1792972800,'123','1')
  selected=['--build-arg','SQLITE_CACHE=sqlite3541-capsule','--build-arg','SQLITE_TARGET=/sqlite3541-capsule']
  pos=new.index('SQLITE_CACHE=sqlite3541-capsule')-1;self.assertEqual(new[pos:pos+4],selected);self.assertEqual(new[:pos]+new[pos+4:],old)
 def test_canonical_cache_inputs_bind_three_payloads_original_zip_and_changed_lock_name(self):
  lock,_=F.fixture();lock['profiles']={'trixie':lock['profiles']['trixie']};profile=lock['profiles']['trixie']
  with tempfile.TemporaryDirectory(prefix='.canonical-driver-',dir=ROOT/'tests')as folder:
   release=Path(folder);(release/'scripts').mkdir();rows=[]
   for name in(D.APT_LOCK,D.TAR_LOCK,D.COREUTILS_LOCK):
    raw=(REPO/name).read_bytes();(release/name).write_bytes(raw);rows.append({'path':name,'bytes':len(raw),'sha256':sha256(raw).hexdigest()})
   for name,raw in(('scripts/sqlite3541_packages.lock.json',F.C.encoded(lock)),('scripts/sqlite3541_package.py',(ROOT/'scripts/sqlite3541_package.py').read_bytes())):
    (release/name).write_bytes(raw);rows.append({'path':name,'bytes':len(raw),'sha256':sha256(raw).hexdigest()})
   D.configure_sqlite_family('canonical3541');inventory,selected=D.cache_inputs(release,{'release_files':rows})
   self.assertEqual(selected,profile);self.assertEqual(len(inventory),33)
   self.assertEqual(inventory[D.CACHE_ROOTS[1]+'/provenance.json'],profile['provenance'])
   self.assertEqual(inventory[D.CACHE_ROOTS[1]+'/manifest.json'],F.C.content_pin(F.C.cache_manifest(profile)))
   self.assertIn(D.CACHE_ORIGINAL_ROOT+'/sqlite3541.zip',inventory);self.assertNotIn(D.CACHE_ORIGINAL_ROOT+'/sqlite-wal.zip',inventory)
 def test_release_copy_contract_includes_two_new_sources_and_all_immutable_business_roots(self):
  recipe=(ROOT/'deploy/market-data/Dockerfile.capture2').read_bytes();S.copy_contract(recipe)
  self.assertEqual(len(S.release_files()),22);self.assertEqual(len(S.release_files('native/coin-estimator')),23)
  self.assertIn('scripts/sqlite3541_package.py',S.release_files());self.assertIn('scripts/sqlite3541_packages.lock.json',S.release_files())
  self.assertEqual(D.GLIBC_RECIPE_PIN,{'bytes':len(recipe),'sha256':sha256(recipe).hexdigest()})
  bad=recipe.replace(b' scripts/sqlite3541_package.py scripts/sqlite3541_packages.lock.json',b'')
  with self.assertRaises(RuntimeError):S.copy_contract(bad)
 def test_real_recipe_shell_accepts_only_two_reviewed_mount_pairs(self):
  text=(ROOT/'deploy/market-data/Dockerfile.capture2').read_text();start=text.index('    case "$SQLITE_CACHE:$SQLITE_TARGET"')
  shell=text[start:text.index('\nRUN --network=none',start)].replace('/usr/bin/setpriv','owned_setpriv')
  for cache,target,ok,flag in(('sqlite-wal-capsule','/sqlite-capsule',True,False),('sqlite3541-capsule','/sqlite3541-capsule',True,True),('sqlite-wal-capsule','/sqlite3541-capsule',False,False),('UNREVIEWED','/sqlite-capsule',False,False)):
   with self.subTest(cache=cache,target=target):
    run=subprocess.run(['/bin/sh','-eu','-c','owned_setpriv() { printf "%s\\n" "$@"; }\n'+shell],env={'SQLITE_CACHE':cache,'SQLITE_TARGET':target},capture_output=True,text=True,timeout=3)
    self.assertEqual(run.returncode==0,ok)
    if ok:self.assertEqual('--canonical3541'in run.stdout.splitlines(),flag);self.assertIn('--no-new-privs',run.stdout)
 def test_all_workflow_python_blocks_compile_and_global_input_reaches_every_phase(self):
  raw=(ROOT/'.github/workflows/market-capture2-image.yml').read_text()
  self.assertIn("NORMAL_MARKET_SQLITE: ${{ inputs.sqlite_family || 'gold2' }}",raw);self.assertIn('default: gold2',raw)
  parts=raw.split("          python3 -I -S -B - <<'PY'\n");self.assertGreaterEqual(len(parts)-1,5)
  for number,part in enumerate(parts[1:]):
   code=part.split('          PY\n',1)[0];code='\n'.join(line[10:]if line.startswith('          ')else line for line in code.splitlines());compile(code,'workflow-'+str(number),'exec')
  driver=(ROOT/'scripts/prepare_market_capture2_image.py').read_text()
  self.assertIn("str(context),'--retain-normal-original-zips']+(['--canonical3541']if SQLITE_FAMILY=='canonical3541'else[])",driver)
  self.assertIn("originals['sqlite-canonical-package.py']",driver);self.assertIn("originals['sqlite-canonical-provenance.json']",driver)
 def test_family_is_selected_before_any_normal_operation_clock_is_opened(self):
  def denied(*args,**kwargs):self.assertEqual(D.SQLITE_FAMILY,'canonical3541');raise RuntimeError('OWNED_STOP_BEFORE_OPERATION')
  with patch.dict(os.environ,{'NORMAL_MARKET_SQLITE':'canonical3541'}),patch.object(D,'NORMAL_OPERATION',None),patch.object(D,'NORMAL_OPERATION_BOOTSTRAP',(1,'a'*64)),patch.object(D,'open_normal_operation',side_effect=denied),self.assertRaisesRegex(RuntimeError,'OWNED_STOP_BEFORE_OPERATION'):
   D.main(['--execute','--phase','build','--root','/PUBLIC','--repo','/PUBLIC'])

 def test_canonical_probe_requires_exact_source_precision_and_new_identity_without_f88_claim(self):
  lock,_=F.fixture();profile=F.C.validate_lock(lock)
  compat={'wal_snapshot':True,'rollback_upsert':True,'json_fts':True,'backup_readonly_reopen':True,'lookup_index':True,
   'query_result_sha256':sha256(json.dumps([['g2',3,'emami',62000000],['g1',1,'emami',61500000]],ensure_ascii=False).encode()).hexdigest(),'query_plan':['PUBLIC UNIT quote_lookup']}
  value={'status':'PASS','sqlite_package':profile['version'],'sqlite_runtime':'3.54.1','libmagic_checked':False,
   'sqlite_system_library':{**profile['installed_library'],'mapped_inode_verified':True},
   'sqlite_stdlib_extension':{'path':'/usr/local/lib/python3.11/lib-dynload/_sqlite3.UNIT.so','bytes':1,'sha256':'e'*64,'mapped_inode_verified':True},
   'python_environment_ignored':True,'compatibility':compat,'wal_reset_package_identity_checked':False,'wal_reset_reproduction_performed':False,
   'production_data_accessed':False,'all_cves_attested':False,'canonical_release_identity_checked':True,'legacy_cast15_verified':True,'numeric_contract':F.C.NUMERIC_CONTRACT,'canonical_source_id':F.C.SOURCE['source_id']}
  D.configure_sqlite_family('canonical3541');self.assertEqual(D.validate_sqlite_probe(value,profile),value)
  for key,bad in(('canonical_source_id','0'*64),('legacy_cast15_verified',False),('wal_reset_package_identity_checked',True),('canonical_release_identity_checked',False)):
   broken=deepcopy(value);broken[key]=bad
   with self.subTest(key=key),self.assertRaises(RuntimeError):D.validate_sqlite_probe(broken,profile)

if __name__=='__main__':unittest.main()
