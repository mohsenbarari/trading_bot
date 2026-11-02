"""Synthetic protocol regression packets; no build, Host or security acceptance."""
from copy import deepcopy
from pathlib import Path
from types import ModuleType
from hashlib import sha256
import json,unittest
ROOT=next(p for p in Path(__file__).resolve().parents if(p/'docs/memory/manifest.md').exists())
SOURCE=Path(__file__).resolve().parents[1]
def load(name,path):
 m=ModuleType(name);m.__file__=str(path);exec(compile(path.read_bytes(),str(path),'exec'),m.__dict__);return m
A=load('_owned_controller_v2_assessor',SOURCE/'scripts/assess_image_security_evidence.py')
H=load('_owned_existing_assessor_packets',ROOT/'tests/test_assess_image_security_evidence.py');H.A=A
D=load('_owned_original_driver_constants',ROOT/'scripts/prepare_market_capture2_image.py')
def reseal(p):
 for proof in p.proofs.values():
  for name in H.PRODUCT_NAMES.values():proof['original_inputs'][name]=H.pin(p.inputs[name])
def packet_v2():
 p=H.Packet();H.install_market_product(p)
 build=json.loads(p.inputs[H.PRODUCT_NAMES['build']]);source=json.loads(p.inputs[H.PRODUCT_NAMES['source']]);manifest=json.loads(p.inputs[H.PRODUCT_NAMES['manifest']])
 runtime={'schema':'normal_market_glibc_runtime_context/1','Root_qualification':deepcopy(D.GLIBC_ROOT_PIN),
  'named_context':deepcopy(D.GLIBC_NAMED),'context_signature':sha256(A.encoded(D.GLIBC_NAMED)).hexdigest(),
  'custody':{'owned_fixture_only':True,'Host_or_build_claim':False}}
 controller={'schema':'normal_market_controller_inputs/2','release_commit':p.scope['source_commit'],'release_tree':p.scope['source_tree'],
  'repository_identity':build['source_inputs']['repository_identity'],'two_git_input_signature':p.scope['input_signature'],
  'controller_files':[{'path':'scripts/OWNED-NOT-A-REAL-CONTROLLER.py','bytes':32,'sha256':sha256(b'OWNED SYNTHETIC').hexdigest()}],
  'normal_cache_files':{'pip_packages/OWNED-SYNTHETIC-CACHE':{'bytes':0,'sha256':sha256(b'').hexdigest()}},
  'glibc_runtime':runtime,'source_date_epoch':1793600000,'source_signature_format_unchanged':'normal-market-two-git-sources-v1',
  'business_tests_actual':False,'complete_control_payload':False,'activation_authority':False,'security_accepted':False}
 signature=sha256(A.encoded(controller)).hexdigest()
 build.update(controller_inputs=controller,controller_input_signature=signature,glibc_runtime=runtime)
 source.update(controller=controller,controller_signature=signature)
 manifest.update(schema='normal_market_two_git_glibc_context_inputs/1',controller_input_signature=signature,
  glibc_runtime=runtime,source_date_epoch=controller['source_date_epoch'])
 for key,value in(('build',build),('source',source),('manifest',manifest)):p.inputs[H.PRODUCT_NAMES[key]]=A.encoded(value)
 reseal(p);return p
class ControllerV2Tests(unittest.TestCase):
 def test_original_controller1_still_passes_its_original_manifest(self):
  p=H.Packet();H.install_market_product(p);self.assertEqual(p.run()['status'],'PASS')
 def test_genuine_v2_wire_shape_keeps_raw_rows_and_no_security_authority(self):
  p=packet_v2();result=p.run();self.assertEqual(result['status'],'PASS');self.assertEqual(result['raw_counts']['HIGH'],44)
  self.assertFalse(result['security_accepted']);self.assertFalse(result['raw_gate_passed'])
 def test_rehashed_controller_cannot_gain_activation_or_change_qualified_context(self):
  for key in('activation_authority','business_tests_actual','complete_control_payload','security_accepted'):
   p=packet_v2();build=json.loads(p.inputs[H.PRODUCT_NAMES['build']]);source=json.loads(p.inputs[H.PRODUCT_NAMES['source']]);manifest=json.loads(p.inputs[H.PRODUCT_NAMES['manifest']])
   build['controller_inputs'][key]=True;signature=sha256(A.encoded(build['controller_inputs'])).hexdigest();build['controller_input_signature']=signature
   source.update(controller=build['controller_inputs'],controller_signature=signature);manifest['controller_input_signature']=signature
   for name,value in(('build',build),('source',source),('manifest',manifest)):p.inputs[H.PRODUCT_NAMES[name]]=A.encoded(value)
   reseal(p)
   with self.subTest(key=key),self.assertRaisesRegex(A.Refused,'product_controller_signature'):p.run()
 def test_glibc_manifest_cannot_diverge_or_silently_fall_back_to_old_manifest(self):
  for key,value in(('schema','normal_market_two_context_inputs/1'),('glibc_runtime',{}),('source_date_epoch',1793600001)):
   p=packet_v2();manifest=json.loads(p.inputs[H.PRODUCT_NAMES['manifest']]);manifest[key]=value;p.inputs[H.PRODUCT_NAMES['manifest']]=A.encoded(manifest);reseal(p)
   with self.subTest(key=key),self.assertRaisesRegex(A.Refused,'product_market_manifest'):p.run()
 def test_current_build_glibc_and_source_identity_must_match_controller(self):
  for key,value in(('glibc_runtime',{}),('controller_input_signature','f'*64)):
   p=packet_v2();build=json.loads(p.inputs[H.PRODUCT_NAMES['build']]);build[key]=value;p.inputs[H.PRODUCT_NAMES['build']]=A.encoded(build);reseal(p)
   with self.subTest(key=key),self.assertRaisesRegex(A.Refused,'product_controller_signature'):p.run()
if __name__=='__main__':unittest.main()
