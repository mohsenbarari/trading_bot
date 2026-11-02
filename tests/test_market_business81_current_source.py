"""Exact current source joins and fail-fast order only; never hosted/build81.

Every executed input is public Source in newly owned fixtures. The old pin is
an explicit negative source derivative, not a production constant override.
"""
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from types import ModuleType
from unittest.mock import patch
import ast,errno,json,os,sys,tempfile,unittest

ROOT=Path(__file__).resolve().parents[1]
def load(name):
 path=ROOT/name;value=ModuleType('owned_current_source_'+path.stem);value.__file__=str(path)
 exec(compile(path.read_bytes(),str(path),'exec'),value.__dict__);return value
M=load('scripts/prepare_market_capture2_image.py');O=load('scripts/market_business81_outer.py')
F=load('tests/market_security_source_fixtures.py')
OLD_ACTUAL_PIN=(122815, '2ceab68cfc7830a4a59ba46acb4b0a3bccf9995142c9cc73350d5addc6884909')
def pin(raw):return{'bytes':len(raw),'sha256':sha256(raw).hexdigest()}

class CurrentSourceTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory(prefix='.owned-current-business-source-',dir=ROOT/'tests');self.addCleanup(self.temp.cleanup)
  self.root=Path(self.temp.name);self.raws={name:(ROOT/name).read_bytes()for name in(M.SELF,M.OUTER,M.BUSINESS)}
  self.rows=[]
  for name,raw in self.raws.items():
   path=self.root/'controller-source'/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw);path.chmod(0o644)
   self.rows.append({'path':name,**pin(raw)})
 def current_preflight(self,raws=None,rows=None):
  original=M.Held(Path(M.__file__),binding=pin(self.raws[M.SELF]));held=M.Inputs([(self.root/'controller-source'/row['path'],{k:row[k]for k in('bytes','sha256')},False)for row in self.rows])
  sources=[original,*held.files];fds=[row.fd for row in sources]
  try:
   original.check();held.check()
   with patch.object(M.subprocess,'Popen',side_effect=AssertionError('No process may start in this Source preflight')):
    result=M.preflight_business81_sources(self.root,self.raws if raws is None else raws,self.rows if rows is None else rows)
   original.check();held.check();return result
  finally:
   held.close();original.close()
   for fd in fds:
    with self.assertRaises(OSError)as caught:os.fstat(fd)
    self.assertEqual(caught.exception.errno,errno.EBADF)
   for row in sources:row.named()
 def objects(self):
  release=self.root/'controller-source'
  business=M.module(self.raws[M.BUSINESS],release/M.BUSINESS);outer=M.module(self.raws[M.OUTER],release/M.OUTER)
  api=ModuleType('_owned_current_normal');api.__dict__.update(M.__dict__)
  return api,business,outer,M.business81_source_paths(self.root,self.rows)
 def test_final_fixed_outer_pin_is_exact_current_controller_bytes(self):
  raw=(ROOT/M.SELF).read_bytes();self.assertEqual(O.NORMAL_PIN,(len(raw),sha256(raw).hexdigest()));self.assertNotEqual(O.NORMAL_PIN,OLD_ACTUAL_PIN)
 def test_real_current_source_join_with_four_actual_held_FDs_passes_without_any_process(self):
  self.assertIsNone(self.current_preflight())
 def test_old_actual_pin_source_counterfactual_refuses_before_any_process(self):
  raw=self.raws[M.OUTER];old=('NORMAL_PIN='+repr(O.NORMAL_PIN)).encode();new=('NORMAL_PIN='+repr(OLD_ACTUAL_PIN)).encode();self.assertEqual(raw.count(old),1)
  negative=dict(self.raws);negative[M.OUTER]=raw.replace(old,new,1)
  phase='business81_source_preflight'
  with patch.object(M,'PHASE',phase),self.assertRaisesRegex(RuntimeError,'^business81_source$')as caught:self.current_preflight(raws=negative)
  with patch.object(M,'PHASE',phase):reported=M.closed_failure(caught.exception)
  self.assertEqual(reported['phase'],phase);self.assertEqual(reported['code'],'business81_source');self.assertIs(reported['security_accepted'],False)
  # Same actual stale-pin failure under the predecessor formatter silently
  # became bootstrap. This owned code counterfactual invokes no live phase.
  current=(ROOT/M.SELF).read_bytes();needle=b"'controller_source','business81_source_preflight','git_product_seal'";self.assertEqual(current.count(needle),1)
  legacy=ModuleType('_owned_predecessor_failure_formatter');legacy.__file__=M.__file__
  exec(compile(current.replace(needle,b"'controller_source','git_product_seal'"),M.__file__,'exec'),legacy.__dict__);legacy.PHASE=phase
  prior=legacy.closed_failure(caught.exception);self.assertEqual(prior['phase'],'bootstrap');self.assertEqual(prior['code'],reported['code'])
  with patch.object(M,'PHASE','OWNED_PRIVATE_PHASE_MUST_NOT_LEAK'):
   unknown=M.closed_failure(caught.exception);self.assertEqual(unknown['phase'],'bootstrap');self.assertNotIn('OWNED_PRIVATE_PHASE',json.dumps(unknown))
 def test_changed_current_normal_size_or_digest_is_not_admitted(self):
  for key,value in(('bytes',len(self.raws[M.SELF])+1),('sha256','0'*64)):
   rows=deepcopy(self.rows);next(row for row in rows if row['path']==M.SELF)[key]=value
   with self.assertRaisesRegex(RuntimeError,'^business81_source$'):self.current_preflight(rows=rows)
 def test_producer_source_helper_uses_exact_existing_callwire_paths(self):
  api,business,outer,paths=self.objects();self.assertEqual(set(paths),{'normal','business','outer'})
  self.assertEqual(paths['normal'],{'path':Path(M.__file__).resolve(),'binding':pin(self.raws[M.SELF])})
  self.assertEqual(paths['outer']['path'],Path(outer.__file__).resolve());self.assertEqual(paths['business']['path'],Path(business.__file__).resolve())
  self.assertIsNone(outer.source_contract(api,business,paths))
 def test_wrong_normal_outer_or_business_path_still_refuses(self):
  api,business,outer,original=self.objects()
  for key in('normal','business','outer'):
   paths=deepcopy(original);paths[key]['path']=self.root/'owned-wrong-path'
   with self.assertRaisesRegex(RuntimeError,'^business81_source$'):outer.source_contract(api,business,paths)
 def test_extra_missing_or_nondict_source_inventory_still_refuses(self):
  api,business,outer,original=self.objects();extra=deepcopy(original);extra['unapproved']=deepcopy(original['normal']);missing=deepcopy(original);missing.pop('business')
  for paths in(extra,missing,[],None):
   with self.assertRaisesRegex(RuntimeError,'^business81_source$'):outer.source_contract(api,business,paths)
 def test_old_pin_or_boolean_size_cannot_be_a_current_source_binding(self):
  api,business,outer,original=self.objects()
  for binding in({'bytes':OLD_ACTUAL_PIN[0],'sha256':OLD_ACTUAL_PIN[1]},{'bytes':True,'sha256':O.NORMAL_PIN[1]}):
   paths=deepcopy(original);paths['normal']['binding']=binding
   with self.assertRaisesRegex(RuntimeError,'^business81_source$'):outer.source_contract(api,business,paths)
 def test_same_exact_source_predicate_is_called_again_by_genuine_producer_before_work(self):
  tree=ast.parse((ROOT/M.OUTER).read_bytes());producer=next(n for n in tree.body if isinstance(n,ast.FunctionDef)and n.name=='produce')
  calls=[n for n in ast.walk(producer)if isinstance(n,ast.Call)]
  check=next(n for n in calls if isinstance(n.func,ast.Name)and n.func.id=='source_contract')
  self.assertEqual([ast.unparse(n)for n in check.args],['M','B','source_paths'])
  self.assertLess(check.lineno,next(n.lineno for n in calls if isinstance(n.func,ast.Attribute)and n.func.attr=='mkdir'))
  self.assertTrue(any(isinstance(n.func,ast.Attribute)and ast.unparse(n.func)=='M.hosted'for n in calls))
 def test_cheap_join_is_guarded_before_seal_cache_and_expensive_build(self):
  tree=ast.parse((ROOT/M.SELF).read_bytes());normal=next(n for n in tree.body if isinstance(n,ast.FunctionDef)and n.name=='normal_build')
  calls=[n for n in ast.walk(normal)if isinstance(n,ast.Call)];preflight=next(n for n in calls if isinstance(n.func,ast.Name)and n.func.id=='preflight_business81_sources')
  self.assertEqual([ast.unparse(n)for n in preflight.args],['root','raws','controllers'])
  for target in('sealer.seal','prepare_glibc_runtime','prepare_checked_caches','build_argv'):
   self.assertLess(preflight.lineno,min(n.lineno for n in calls if ast.unparse(n.func)==target))
  source=(ROOT/M.SELF).read_text().splitlines();self.assertIn('original.check();controller_hold.check()',source[preflight.lineno-2]);self.assertIn('original.check();controller_hold.check()',source[preflight.lineno])
 def test_actual81_and_preflight_share_the_same_closed_source_path_builder(self):
  tree=ast.parse((ROOT/M.SELF).read_bytes())
  for name in('actual_business81','preflight_business81_sources'):
   fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef)and n.name==name)
   self.assertEqual(sum(isinstance(n,ast.Call)and isinstance(n.func,ast.Name)and n.func.id=='business81_source_paths'for n in ast.walk(fn)),1)
 def test_exact54_controller_inventory_not_len_any_and_all_role57_sets(self):
  self.assertEqual(len(M.CONTROLLER_FILES),54)
  self.assertEqual(sha256(M.encoded(sorted(M.CONTROLLER_FILES))).hexdigest(),'27d46d231cced4fc1c6fb82aceae6b7c5cbcadb8a3bcabd5a7359a458b3968dd')
  for role in M.ROLE_MAP:
   self.assertEqual(set(M.controller_files(role)),set(M.CONTROLLER_FILES)|{M.ROLE_READER,M.ROLE_CATALOG_PATH,M.BUSINESS_DIR+'/role_fixture_contract.py'})
   self.assertEqual(len(M.controller_files(role)),57)
  controllers=[{'path':name,'mode':'100644','git_blob':'e'*40,**pin((ROOT/name).read_bytes())}for name in sorted(M.CONTROLLER_FILES)]
  receipt={'schema':'normal_market_git_component_inputs/1','signature_format':'normal-market-two-git-sources-v1','accepted_manifest_sha256':M.MANIFEST_SHA,'repository_identity':{'host':'github.com','repository':M.REPOSITORY},'release_commit':'a'*40,'release_tree':'b'*40,'business_commit':'c'*40,'business_tree':'d'*40}
  signature=sha256(M.encoded(receipt)).hexdigest();cache={'pip_packages/OWNED-codec-only-'+str(i):pin(b'X')for i in range(32)}
  runtime=F.owned_glibc_runtime(self,M,self.root)
  value,_=M.receipt_contract(receipt,signature,controllers,cache,1793491200,runtime)
  self.assertIs(value['activation_authority'],False);self.assertIs(value['security_accepted'],False);self.assertIs(value['business_tests_actual'],False)
  extra=[*controllers,{**controllers[0],'path':'scripts/OWNED-UNKNOWN.py'}]
  replacement=deepcopy(controllers);replacement[0]['path']='scripts/OWNED-UNKNOWN.py';replacement.sort(key=lambda row:row['path'])
  duplicate=deepcopy(controllers);duplicate[0]=deepcopy(duplicate[1]);duplicate.sort(key=lambda row:row['path'])
  for rows in(controllers[:-1],extra,replacement,duplicate):
   with self.assertRaisesRegex(RuntimeError,'^controller_inventory$'):M.receipt_contract(receipt,signature,rows,cache,1793491200,runtime)
 def test_private_unit_runtime_patch_restores_and_unpatched_production_rejects_same_bytes(self):
  private=load('scripts/prepare_market_capture2_image.py');old=(private.GLIBC_FILES,private.GLIBC_ROOT_PIN,private.GLIBC_NAMED)
  isolated=unittest.TestCase();root=self.root/'owned-mock-only';root.mkdir()
  try:
   runtime=F.owned_glibc_runtime(isolated,private,root)
   self.assertNotEqual(private.GLIBC_ROOT_PIN,old[1]);self.assertIs(runtime['named_context']['security_accepted'],False)
   self.assertTrue(all(value.startswith(b'OWNED UNIT MOCK ONLY')for name,value in F.owned_glibc_inputs(private).items()if name!='Root-TrixieSelected01-qualification.json'))
   actual=ModuleType('_normal_market_unpatched_control');actual.__file__=M.__file__
   exec(compile((ROOT/M.SELF).read_bytes(),M.__file__,'exec'),actual.__dict__)
   with self.assertRaisesRegex(RuntimeError,'^source_binding$'):actual.RuntimeCapsule(root/'glibc-capsule')
   with self.assertRaisesRegex(RuntimeError,'^glibc_runtime_record$'):actual.glibc_runtime_record(runtime)
   with self.assertRaisesRegex(AssertionError,'^owned_private_unit_module_required$'):F.owned_glibc_runtime(isolated,actual,self.root/'never-created')
   self.assertFalse((self.root/'never-created').exists())
  finally:isolated.doCleanups()
  self.assertEqual((private.GLIBC_FILES,private.GLIBC_ROOT_PIN,private.GLIBC_NAMED),old)
  with self.assertRaisesRegex(RuntimeError,'^source_binding$'):private.RuntimeCapsule(root/'glibc-capsule')

if __name__=='__main__':unittest.main(verbosity=2)
