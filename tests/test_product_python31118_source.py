"""Owned source-only microrelease checks. No image, Git, DB or network calls."""
from contextlib import redirect_stdout
from hashlib import sha256
from pathlib import Path
from types import ModuleType
from unittest.mock import patch
import ast,io,json,struct,sys,unittest
ROOT=Path(__file__).resolve().parents[1]
BASE='python:3.11.18-slim-trixie@sha256:0451b9eeed3e5a9523d8958cc33262eb5843c7af3114c50f45d99f56e2b883ac'
def load(path):
 p=ROOT/path;m=ModuleType('owned_'+p.stem);m.__file__=str(p);exec(compile(p.read_bytes(),str(p),'exec'),m.__dict__);return m
class ProductSourceTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):cls.P=load('scripts/probe_python_micro_release.py');cls.B=load('scripts/market_business81.py');cls.O=load('scripts/market_business81_outer.py');cls.M=load('scripts/prepare_market_capture2_image.py');cls.S=load('scripts/prepare_market_product_source.py');cls.F=load('tests/market_security_source_fixtures.py')
 def test_all_four_actual_product_recipes_exact_amd64_base_and_real_probe(self):
  for name in ('Dockerfile','Dockerfile.iran','deploy/market-data/Dockerfile','deploy/market-data/Dockerfile.capture2'):
   text=(ROOT/name).read_text();self.assertEqual(text.count(BASE),1,name);self.assertNotIn('3.11.17',text)
   self.assertIn('RUN --network=none python -I -S -B ',text);self.assertIn('probe_python_micro_release.py --image-self-check',text)
   for gate in ('verify_image_trust.py','verify_psycopg2_runtime.py','verify_sqlite_runtime.py','remove_runtime_pip.py'):self.assertIn(gate,text)
   self.assertIn('upgrade -y --no-install-recommends',text);self.assertIn('FROM patched-base AS runtime',text)
 def test_all_four_recipes_share_exact_SQLite18_guard_without_other_deltas(self):
  raw=(ROOT/'scripts/verify_sqlite_runtime.py').read_bytes();self.assertEqual((len(raw),sha256(raw).hexdigest()),(15034,'dd029d2fc54385f226030c65c7e4fb3bc2b0328a89dbb961c7cac716cd67f049'))
  tree=ast.parse(raw);function=next(n for n in tree.body if isinstance(n,ast.FunctionDef)and n.name=='verify');guard=function.body[0].value
  self.assertEqual(ast.unparse(guard.func),'require');self.assertEqual(ast.unparse(guard.args[0].left),'sys.version_info[:3]');self.assertEqual(ast.literal_eval(guard.args[0].comparators[0]),(3,11,18));self.assertEqual(ast.literal_eval(guard.args[1]),'python_version_changed')
  original=raw.replace(b'(3, 11, 18)',b'(3, 11, 17)',1);self.assertEqual(sha256(original).hexdigest(),'e5f5d981a84e1680e93891b96eb02ad210d18ce36933a2ecc254df12e6e28631')
  for name in('Dockerfile','Dockerfile.iran','deploy/market-data/Dockerfile','deploy/market-data/Dockerfile.capture2'):self.assertIn('verify_sqlite_runtime.py',(ROOT/name).read_text())
  # Numeric protocol only: no actual sys.version_info override or SQLite run.
  for version,expected in(((3,11,18),True),((3,11,17),False),((3,11,19),False)):
   expr=ast.Expression(ast.Compare(left=ast.Tuple(elts=[ast.Constant(v)for v in version],ctx=ast.Load()),ops=guard.args[0].ops,comparators=guard.args[0].comparators));ast.fix_missing_locations(expr)
   self.assertIs(eval(compile(expr,'<owned numeric SQLite runtime guard>','eval'),{'__builtins__':{}}),expected)
 def test_release_closure_has_exact_new_probe_and_twenty_base_sources(self):
  self.assertEqual(len(self.S.RELEASE_FILES),20);self.assertIn('scripts/probe_python_micro_release.py',self.S.RELEASE_FILES)
  self.assertIn('COPY scripts/probe_python_micro_release.py /usr/local/libexec/probe_python_micro_release.py',self.S.RELEASE_COPY_LINES)
  self.assertEqual(len(self.S.release_files()),20);self.assertEqual(self.M.source_parameters()['count'],93)
  for role,spec in self.M.ROLE_MAP.items():
   self.assertEqual(len(self.S.release_files(role)),21);self.assertEqual(len(self.M.controller_files(role)),len(self.M.CONTROLLER_FILES)+3)
 def test_owned_unarmed_driver_policy_refuses_without_operations(self):
  with patch.object(self.M,'EXECUTION_ARMED',False),patch.object(self.M,'normal_build',side_effect=AssertionError('no operation')):
   with redirect_stdout(io.StringIO())as out:self.assertEqual(self.M.main([]),0)
   self.assertEqual(json.loads(out.getvalue())['status'],'UNARMED')
   with self.assertRaisesRegex(RuntimeError,'normal_main_integration_unarmed'):self.M.main(['--execute'])
 def test_owned_unapproved_assertion_refuses_without_changing_real_records(self):
  folder=ROOT/'deploy/market-data/business81';actual=(folder/'python31118-root-assertion-approval.json').read_bytes();approval=self.F.unapproved_assertion(actual);proposal=(folder/'python31118-security-base-assertion-proposal.json').read_bytes();delta=(folder/'python31118-security-base-assertion.patch').read_bytes()
  self.assertFalse(json.loads(approval)['approved']);self.assertEqual(json.loads(proposal)['after']['sha256'],'e224b0cf69a574c97a07f7315ce1182c0f01254448394df8f360e96f18d4a450')
  with patch.object(self.O,'APPROVAL_PIN',self.F.binding(approval)),self.assertRaisesRegex(RuntimeError,'^business81_assertion_approval$'):self.O.assertion_approval(self.B,approval,proposal,delta,{})
  self.assertEqual((folder/'python31118-root-assertion-approval.json').read_bytes(),actual)
  self.assertIn(BASE.encode(),self.B.NEW_ASSERTION);self.assertEqual(delta.count(b'\n+        self.assertIn('),1)
 def test_actual_assertion_record_obeys_its_pin_bound_validator_stage(self):
  raws,_=self.historical81_rawset();self.F.assert_actual_assertion_stage(self,self.M,raws)
 def test_actual_Root_approval_is_only_the_exact_security_base_source_assertion(self):
  raw=(ROOT/'deploy/market-data/business81/python31118-root-assertion-approval.json').read_bytes();self.assertEqual((len(raw),sha256(raw).hexdigest()),(1031,'3bbc244f4e044975a8f3e9761e8690a4f48f8051454134e66ef26332c3d2627f'))
  approval=json.loads(raw);self.assertTrue(approval['approved']);self.assertEqual(approval['patch'],{'bytes':333,'sha256':'88d5b46f2726fb570bb6727fe7955f9b5c399f370bf61fc81601c26c40f8da8c'})
  for key in('business_tests_executed','deployment_authority','other_business_test_bodies_changed','security_accepted'):self.assertIs(approval[key],False)
  self.assertEqual(self.O.APPROVAL_PIN,{'bytes':1031,'sha256':sha256(raw).hexdigest()});raws,_=self.historical81_rawset();self.M.preflight_assertion_sources(raws)
 def test_current_exact_helper_and_normal_source_pins(self):
  raw=(ROOT/'deploy/market-data/business81/run_business81.py').read_bytes();self.assertEqual((len(raw),sha256(raw).hexdigest()),self.B.HELPERS['run_business81.py'])
  raw=(ROOT/'scripts/prepare_market_capture2_image.py').read_bytes();self.assertEqual((len(raw),sha256(raw).hexdigest()),self.O.NORMAL_PIN)
  for key,name in [('APPROVAL_PIN','python31118-root-assertion-approval.json'),('PROPOSAL_PIN','python31118-security-base-assertion-proposal.json'),('PATCH_PIN','python31118-security-base-assertion.patch')]:
   raw=(ROOT/'deploy/market-data/business81'/name).read_bytes();self.assertEqual({'bytes':len(raw),'sha256':sha256(raw).hexdigest()},getattr(self.O,key))
 def historical81_rawset(self):
  # Actual frozen public fixtures and real prepare_bundle, without Git/image
  # commands or any completed-test/approval authority. No old approval key.
  names=(self.M.BUSINESS,self.M.OUTER,*(self.M.BUSINESS_DIR+'/'+name for name in self.M.BUSINESS_SUPPORT))
  raws={name:(ROOT/name).read_bytes()for name in names};rows=[]
  for name in('deploy/market-data/Dockerfile.capture2','deploy/market-data/accepted-f79-business.manifest.json'):
   raw=(ROOT/name).read_bytes();rows.append({'path':name,'bytes':len(raw),'sha256':sha256(raw).hexdigest(),'git_blob':self.M.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()})
  return raws,{'release_commit':'b'*40,'release_files':rows}
 def test_current_only_rawset_really_prepares_all81_sources_with_new_approval_key(self):
  raws,receipt=self.historical81_rawset();old=self.M.BUSINESS_DIR+'/root-assertion-approval.json';new=self.M.BUSINESS_DIR+'/python31118-root-assertion-approval.json'
  self.assertNotIn(old,raws);self.assertIn(new,raws)
  B,O,contents,manifest,plans=self.M.historical81_sources(raws,ROOT,receipt,'sha256:'+'a'*64,'c'*64,ROOT)
  self.assertEqual(sum(plan['expected_test_count']for plan in plans.values()),81);self.assertFalse(manifest['security_accepted'])
  self.assertEqual(manifest['security_assertion_update']['approved_source_delta_sha256'],json.loads(raws[new])['after']['sha256'])
  for prefix in('base','replay'):
   raw=contents[prefix+'/'+B.FOUNDATION];self.assertIn(B.NEW_ASSERTION,raw);self.assertNotIn(B.OLD_ASSERTION,raw)
   self.assertEqual(contents[prefix+'/deploy/market-data/Dockerfile'],(ROOT/'deploy/market-data/Dockerfile.capture2').read_bytes())
 def test_old_named_approval_cannot_replace_missing_current_approval(self):
  raws,receipt=self.historical81_rawset();new=self.M.BUSINESS_DIR+'/python31118-root-assertion-approval.json'
  raws[self.M.BUSINESS_DIR+'/root-assertion-approval.json']=raws.pop(new)
  with self.assertRaises(KeyError)as error:self.M.historical81_sources(raws,ROOT,receipt,'sha256:'+'a'*64,'c'*64,ROOT)
  self.assertEqual(error.exception.args,(new,))
 def test_runtime_installer_scope_is_exact_new_version_and_same_two_known_wheels(self):
  source=(ROOT/'scripts/remove_runtime_pip.py').read_text();self.assertIn('sys.version_info[:3] == (3, 11, 18)',source);self.assertIn("sys.prefix == sys.base_prefix == '/usr/local'",source)
  module=load('scripts/remove_runtime_pip.py');self.assertEqual(module.BUNDLED,{'pip-24.0-py3-none-any.whl','setuptools-79.0.1-py3-none-any.whl'})
 def test_probe_default_never_runs_checks(self):
  with patch.object(self.P,'observe',side_effect=AssertionError('no runtime execution')),redirect_stdout(io.StringIO())as out:self.assertEqual(self.P.main([]),0)
  self.assertEqual(json.loads(out.getvalue())['status'],'UNARMED')
 def test_actual_host_result_never_forges_the_required_runtime(self):
  with redirect_stdout(io.StringIO())as out:
   # PASS writes to stdout.buffer; capture binary and text in one real stream.
   buffer=io.BytesIO();stream=io.TextIOWrapper(buffer,encoding='utf-8',write_through=True)
   with redirect_stdout(stream):code=self.P.main(['--image-self-check'])
   value=json.loads(buffer.getvalue());stream.detach()
  self.assertFalse(value['security_accepted'])
  if tuple(sys.version_info[:3])!=self.P.EXPECTED:
   self.assertEqual(code,1);self.assertEqual(value['reason'],'runtime_microrelease')
  elif code==0:
   self.assertEqual(value['status'],'PASS');self.assertEqual(value['actual_runtime']['version'],list(sys.version_info[:3]));self.assertEqual(value['actual_expat'],'expat_2.9.0')
  else:
   # A genuine expected-version host may still correctly lack the admitted
   # image layout, flags, architecture, inode metadata or bundled Expat.
   self.assertEqual(code,1);self.assertEqual(value['status'],'FAIL');self.assertTrue(value['reason'].startswith(('runtime_','benign_','negative_')))
 def test_finite_elf_parser_rejects_other_architecture_and_class(self):
  raw=bytearray(64);raw[:7]=b'\x7fELF\x02\x01\x01';struct.pack_into('<H',raw,16,3);struct.pack_into('<H',raw,18,62);self.assertEqual(self.P.elf_header(bytes(raw))['class'],64)
  for index,value in ((4,1),(5,2),(18,3)):
   bad=bytearray(raw);bad[index]=value
   with self.assertRaises(RuntimeError):self.P.elf_header(bytes(bad))
  with self.assertRaises(RuntimeError):self.P.elf_header(bytes(raw)+b'x')
 def test_probe_has_no_extraction_network_or_application_import_api(self):
  tree=ast.parse((ROOT/'scripts/probe_python_micro_release.py').read_bytes());called={n.func.attr for n in ast.walk(tree)if isinstance(n,ast.Call)and isinstance(n.func,ast.Attribute)}
  self.assertTrue({'extract','extractall','urlopen','connect','run','Popen','write_bytes','unlink'}.isdisjoint(called))
  self.assertIn("pyexpat.EXPAT_VERSION=='expat_2.9.0'",(ROOT/'scripts/probe_python_micro_release.py').read_text())
 def test_current_app_receipt_separately_names_actual_microrelease_build_gate(self):
  self.assertIn("['trust','coreutils_acl','python_micro_release','psycopg2','sqlite_libmagic','pip_removed']",(ROOT/'scripts/prepare_canonical_app_image.py').read_text())
 def test_python_sources_have_no_trailing_whitespace_and_parse(self):
  for p in (ROOT/'scripts').glob('*.py'):
   raw=p.read_bytes();ast.parse(raw,filename=str(p));self.assertTrue(all(line.rstrip()==line for line in raw.splitlines()),p.name)
if __name__=='__main__':unittest.main()
