"""Hermetic pure operands; actual ten-artifact integration is Root-only."""
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from types import ModuleType
import json,unittest
ROOT=Path(__file__).resolve().parents[1]
def module(path):
 m=ModuleType('_pure_'+path.stem);m.__file__=str(path);exec(compile(path.read_bytes(),str(path),'exec'),m.__dict__);return m
X=module(ROOT/'deploy/maintenance/normal_market_image_delta.py');D=module(ROOT/'deploy/maintenance/datastore_candidate_contract.py')
def fixture():
 # All IDs and records here are synthetic parser operands, with a disjoint
 # old/candidate layer model. No LOAD/admission receipt is produced or saved.
 oldcfg={'Env':['PATH=/usr/bin','PYTHON_VERSION=3.11.0'],'Cmd':['service','--role','market-processor'],
  'User':'10001:10001','WorkingDir':'/app','Labels':{'public.business':'UNCHANGED'}}
 newcfg={**deepcopy(oldcfg),'Env':['PATH=/usr/bin','PYTHON_VERSION=3.11.17'],
  'Labels':{**oldcfg['Labels'],**{key:'PUBLIC_BUILD_PROOF' for key in X.PROFILE_PROVENANCE_KEYS}}}
 encode=lambda x:(json.dumps(x,sort_keys=True)+'\n').encode()
 oldid='sha256:'+sha256(encode(oldcfg)).hexdigest();newid='sha256:'+sha256(encode(newcfg)).hexdigest()
 before=['sha256:'+sha256(b'OLD_LAYER').hexdigest()];after=['sha256:'+sha256(b'REBUILT_LAYER').hexdigest()]
 spec={'candidate_config_id':newid,'candidate_rootfs':after,'profile':'678aaf5cd8e9','role':'market-processor','ci_run_id':123,
  'image_artifact':{'digest':'sha256:'+'9'*64},'archive':{'uncompressed_bytes':5120},'tar_sha256':'8'*64}
 data={'spec':spec,'baseline_image_id':oldid,'baseline_rootfs':before,'old_nonenv_config':{k:v for k,v in oldcfg.items() if k!='Env'},
  'expected_candidate_config':newcfg,'raw_candidate_config':newcfg}
 old={'Id':oldid,'RootFS':{'Type':'layers','Layers':before},'Config':oldcfg};new={'Id':newid,'RootFS':{'Type':'layers','Layers':after},'Config':newcfg}
 return data,old,new
class ImageDeltaTests(unittest.TestCase):
 def test_disjoint_rebuild_and_authenticated_provenance_defaults_have_explicit_semantics(self):
  data,old,new=fixture();value=X.image_delta(D,data,old,new,new['Id'])
  self.assertNotEqual(old['RootFS']['Layers'],new['RootFS']['Layers']);self.assertEqual(value['image_default_change_count'],1)
  self.assertTrue(value['business_image_config_equal_in_ram']);self.assertFalse(value['serving_equivalence_proven'])
  self.assertNotIn('Env',json.dumps(value));self.assertNotIn('3.11.17',json.dumps(value))
 def test_business_defaults_execution_or_unreviewed_identity_drift_refuse(self):
  for change in (lambda o,n:n['Config'].update(Cmd=['UNREVIEWED']),lambda o,n:n['Config'].update(User='0'),
   lambda o,n:o['Config']['Env'].append('BUSINESS_PASSWORD=PRIVATE_SENTINEL'),lambda o,n:o['Config'].update(Labels={}),
   lambda o,n:n.update(Id='sha256:'+'0'*64)):
   data,old,new=fixture();change(old,new)
   with self.assertRaises(RuntimeError):X.image_delta(D,data,old,new,data['spec']['candidate_config_id'])
 def test_real_serializer_distinguishes_bool_int_and_rootfs_order(self):
  data,old,new=fixture();new['Config']['ArgsEscaped']=1
  with self.assertRaises(RuntimeError):X.image_delta(D,data,old,new,new['Id'])
  data,old,new=fixture();new['RootFS']['Layers']=[*new['RootFS']['Layers'],'sha256:'+'0'*64]
  with self.assertRaises(RuntimeError):X.image_delta(D,data,old,new,new['Id'])
 def test_even_authenticated_candidate_business_label_change_is_not_build_provenance(self):
  # The expected authenticated candidate operand shares this synthetic Config;
  # this checks policy beyond the independent Config/report equality barrier.
  for change in (lambda v:v.update({'public.business':'CHANGED'}),lambda v:v.update({'public.unreviewed':'ADDED'}),
   lambda v:v.pop('io.gold-trade.security.recipe')):
   data,old,new=fixture();change(new['Config']['Labels'])
   with self.assertRaisesRegex(RuntimeError,'market_image_provenance'):X.image_delta(D,data,old,new,new['Id'])
 def test_public_source_catalog_cannot_be_replaced_by_a_synthetic_positive_fixture(self):
  with self.assertRaisesRegex(RuntimeError,'market_catalog'):X.lineage(b'{}',b'NOT_AN_ARTIFACT',b'{}','market-processor')
 def test_source_namespace_has_no_client_or_artifact_mutation_entrypoint(self):
  source=(ROOT/'deploy/maintenance/normal_market_image_delta.py').read_text()
  for value in ('subprocess','os.environ','image load','def main(','Descriptor','open('):self.assertNotIn(value,source)
if __name__=='__main__':unittest.main()
