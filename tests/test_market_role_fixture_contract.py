"""Finite role fixture proposal/lineage guards; no image or approval facades."""
from pathlib import Path
from types import ModuleType
from hashlib import sha256
from copy import deepcopy
from unittest.mock import patch
import ast,json,unittest
ROOT=Path(__file__).absolute().parents[1];T=ROOT
def load(p):
 m=ModuleType(p.stem);m.__file__=str(p);exec(compile(p.read_bytes(),str(p),'exec'),m.__dict__);return m
C=load(T/'deploy/market-data/business81/role_fixture_contract.py');B=load(T/'scripts/market_business81.py');M=load(T/'scripts/prepare_market_capture2_image.py');O=load(T/'scripts/market_business81_outer.py')
class Tests(unittest.TestCase):
 def fixture(self,name):return(ROOT/'deploy/market-data/business81/fixtures'/name/'tests/test_market_pipeline_stage4_capture.py').read_bytes()
 def ids(self,raw):
  tree=ast.parse(raw);return{c.name+'.'+m.name for c in tree.body if isinstance(c,ast.ClassDef)for m in c.body if isinstance(m,ast.FunctionDef)and m.name.startswith('test_')}
 def methods(self,raw):
  tree=ast.parse(raw);return{c.name+'.'+m.name:ast.dump(m,include_attributes=False)for c in tree.body if isinstance(c,ast.ClassDef)for m in c.body if isinstance(m,ast.FunctionDef)and m.name.startswith('test_')}
 def test_exact_original_80_plus_replay_IDs_and_all_unchanged_methods(self):
  for component,spec in C.COMPONENTS.items():
   allowed={C.CHANGES[k]['class']+'.'+C.CHANGES[k]['method']for k in spec['changes']}
   for name in('base','replay'):
    before=self.fixture(name);after,used=C.propose(before,component,fixture=name);self.assertEqual(self.ids(before),self.ids(after));self.assertEqual(C.restore(after,component,fixture=name),before)
    old,new=self.methods(before),self.methods(after)
    self.assertEqual({k:v for k,v in old.items()if k not in allowed},{k:v for k,v in new.items()if k not in allowed})
 def test_F79_bytes_never_receive_a_role_derivative(self):
  for name in('base','replay'):self.assertEqual(C.propose(self.fixture(name),None,fixture=name),(self.fixture(name),[]))
 def test_exact_approved_contract_and_unapproved_source_preflight(self):
  expected='b1ceeb9eb95de2696a55781c8e47267b2c5597ac126a6e8e0663622b94cc37be'
  self.assertEqual(B.ROLE_FIXTURE_CONTRACT_APPROVED_SHA256, expected)
  self.assertEqual(sha256((T/'deploy/market-data/business81/role-fixture-contract.PROPOSAL.json').read_bytes()).hexdigest(), expected)
  self.assertEqual(B.role_fixture_helper((T/'deploy/market-data/business81/role_fixture_contract.py').read_bytes()).CONTRACT_SHA256, expected)
  original=(T/'scripts/market_business81.py').read_bytes();literal=("ROLE_FIXTURE_CONTRACT_APPROVED_SHA256='"+expected+"'").encode()
  self.assertEqual(original.count(literal),1)
  # Explicitly inert negative source derivative; no production constant is patched.
  unapproved=original.replace(literal,b'ROLE_FIXTURE_CONTRACT_APPROVED_SHA256=None',1)
  raws={name:(T/name).read_bytes()for name in M.controller_files('native/coin-estimator')};raws[M.BUSINESS]=unapproved
  with self.assertRaisesRegex(RuntimeError,'separate_role_fixture_review_required'):M.preflight_assertion_sources(raws)
 def test_git_source_admits_only_exact_new_helper_and_refuses_unknown_before_git(self):
  helper='deploy/market-data/business81/role_fixture_contract.py';raw=(T/helper).read_bytes();calls=[]
  def inert_git(repo,*argv,maximum):
   calls.append((argv,maximum));self.assertEqual(argv[0],'cat-file');self.assertEqual(argv[-1],'b'*40+':'+helper)
   return (str(len(raw))+'\n').encode()if argv[1]=='-s'else raw
  with patch.object(M,'git',side_effect=inert_git):self.assertEqual(M.git_source(Path('/OWNED-NOT-OPENED'),'b'*40,helper),raw)
  self.assertEqual(len(calls),2)
  for unknown in('deploy/market-data/business81/OWNED-UNKNOWN.py',helper+'/extra','/absolute/'+helper):
   with patch.object(M,'git',side_effect=AssertionError('unknown source must stop before Git')):
    with self.assertRaisesRegex(RuntimeError,'controller_source'):M.git_source(Path('/OWNED-NOT-OPENED'),'b'*40,unknown)
  self.assertEqual(len(M.controller_files(None)),30)
  for role in M.ROLE_MAP:self.assertEqual(len(M.controller_files(role)),33)
 def test_wrong_helper_unknown_component_and_method_drift_refuse(self):
  with self.assertRaisesRegex(RuntimeError,'role_fixture_helper_pin'):B.role_fixture_helper(b'OWNED_UNKNOWN_HELPER')
  with self.assertRaisesRegex(ValueError,'role_fixture_component'):C.propose(self.fixture('base'),'OWNED_UNKNOWN_COMPONENT',fixture='base')
  key=next(k for k,v in C.COMPONENTS.items()if v['facts']['schema']==9);raw=self.fixture('base').replace(b'fixture_transport_interrupted',b'OWNED_UNKNOWN_INTERRUPTION',1)
  with self.assertRaisesRegex(ValueError,'role_fixture_method_pin'):C.propose(raw,key,fixture='base')
 def test_exact_transitive_runtime_reader_and_outer_pins(self):
  p=T/'deploy/market-data/business81/run_business81.py';self.assertEqual(B.HELPERS['run_business81.py'],(p.stat().st_size,sha256(p.read_bytes()).hexdigest()))
  p=T/'scripts/prepare_market_capture2_image.py';self.assertEqual(O.NORMAL_PIN,(p.stat().st_size,sha256(p.read_bytes()).hexdigest()))
  self.assertNotIn('deploy/market-data/business81/role_fixture_contract.py',M.controller_files(None))
  for role in M.ROLE_MAP:self.assertIn('deploy/market-data/business81/role_fixture_contract.py',M.controller_files(role))
 def test_F79_original_fixture_bundle_has_no_role_contract_change(self):
  # Existing labelled bundle-codec DTO only: real fixture bytes, no image claim.
  H=load(ROOT/'tests/test_market_role_normal_builder.py');H.RoleBuilderTests.setUpClass();prepared=H.RoleBuilderTests();_,_,old_contents,old_manifest,_=prepared.historical(None)
  selection=json.loads((ROOT/'deploy/market-data/business81/f79-business-test-selection.json').read_bytes());base={r['path']:(ROOT/'deploy/market-data/business81/fixtures/base'/r['path']).read_bytes()for r in selection['historical_fixture_files']};replay=(ROOT/'deploy/market-data/business81/fixtures/replay'/B.CAPTURE).read_bytes();recipe=(ROOT/'deploy/market-data/Dockerfile.capture2').read_bytes();helpers={name:(T/'deploy/market-data/business81'/name).read_bytes()if(T/'deploy/market-data/business81'/name).exists()else(ROOT/'deploy/market-data/accepted-f79-business.manifest.json').read_bytes()if name=='accepted-f79-business-manifest.json'else(ROOT/'deploy/market-data/business81'/name).read_bytes()for name in B.HELPERS};proposal,_=B.security_assertion_proposal(base[B.FOUNDATION])
  new_contents,new_manifest,_=B.prepare_bundle(selection,base,replay,image='sha256:'+'a'*64,release_commit='b'*40,two_git_signature='c'*64,release_recipe=recipe,recipe_spec={'path':'deploy/market-data/Dockerfile.capture2','source_commit':'b'*40,'bytes':len(recipe),'sha256':sha256(recipe).hexdigest(),'git_blob':__import__('hashlib').sha1(b'blob '+str(len(recipe)).encode()+b'\0'+recipe).hexdigest()},helper_sources=helpers,approved_assertion_source_sha256=proposal['after']['sha256'])
  self.assertEqual(len(new_contents),40);self.assertEqual(set(new_contents),set(old_contents));self.assertEqual(new_manifest['files'],old_manifest['files']);self.assertNotIn('role_fixture_update',new_manifest)
  for name in old_contents:
   if name not in{'suite-manifest.json','run_business81.py'}:self.assertEqual(old_contents[name],new_contents[name],name)
 def test_original_fixture_files_and_business_sources_remain_outside_approved_editset(self):
  proposal=json.loads((T/'deploy/market-data/business81/role-fixture-contract.PROPOSAL.json').read_bytes());self.assertFalse(proposal['approved']);self.assertFalse(proposal['business_code_modified']);self.assertFalse(proposal['F79_fixture_bytes_modified'])
if __name__=='__main__':unittest.main(verbosity=2)
