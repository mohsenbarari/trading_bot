"""Owned workflow/validator/collector controls; no configured App or operations."""
import ast
import builtins
from copy import deepcopy
from contextlib import redirect_stderr,redirect_stdout
from hashlib import sha256
import io
from itertools import permutations
import json
from pathlib import Path
import re
from types import SimpleNamespace
from typing import Optional
from urllib.parse import urlparse
import unittest
from unittest.mock import patch
import yaml
import test_npm_backend_collector_failure_diagnostics as C

ROOT=Path(__file__).absolute().parents[1]
WORKFLOW='.github/workflows/npm-security-backports.yml'
EXPECTED='https://ci-iran.invalid'
def workflow():
 raw=C.public_bytes(ROOT/WORKFLOW,32768);return yaml.load(raw,Loader=yaml.BaseLoader)
def browser_steps():return workflow()['jobs']['browsers']['steps']
def fixture_env():
 step=next(s for s in browser_steps()if s.get('name')=='Disposable local-only CI environment (existing merge-gate pattern)')
 block=re.search(r"cat > \.env <<'EOF'\n(.*?)\nEOF",step['run'],re.S)
 if block is None:raise AssertionError('exact isolated CI heredoc required')
 rows={}
 for line in block[1].splitlines():
  key,value=line.split('=',1)
  if key in rows:raise AssertionError('duplicate synthetic key')
  rows[key]=value
 return rows
def preflight():
 step=next(s for s in browser_steps()if s.get('name')=='Verify browser CI backend surface')
 code=re.search(r"docker compose exec -T app python - <<'PY'\n(.*?)\nPY(?:\n|$)",step['run'],re.S)
 if code is None:raise AssertionError('normal App Python preflight required')
 return ast.parse(code[1])
def validator(settings):
 # Execute genuine public helper/validator AST against owned attributes only.
 # No core.config, Settings construction, actual Env or application module import.
 ns={'settings':settings,'urlparse':urlparse,'Optional':Optional}
 sources=[('core/deployment_surface.py',{'extract_host','csv_hosts'},set()),
  ('core/server_routing.py',{'normalize_server'},{'SERVER_FOREIGN','SERVER_IRAN','KNOWN_SERVERS'}),
  ('core/public_webapp_url.py',{'_normalize_host','_environment_allows_http','_explicit_iran_hosts','_foreign_hosts','validate_public_webapp_url','public_webapp_url_for_links'},set())]
 for relative,names,constants in sources:
  tree=ast.parse(C.public_bytes(ROOT/relative,16384));selected=[]
  for node in tree.body:
   if isinstance(node,(ast.FunctionDef,ast.ClassDef))and(node.name in names or node.name=='PublicWebAppURLConfigurationError'):selected.append(node)
   if isinstance(node,ast.Assign)and any(isinstance(t,ast.Name)and t.id in constants for t in node.targets):selected.append(node)
  exec(compile(ast.Module(body=selected,type_ignores=[]),'<owned actual public validator AST>','exec'),ns)
 return ns
def run_preflight(values,*,function=None,fixture_bytes=None,mime_type='text/plain',tree=None,calls=None):
 settings=SimpleNamespace(**{k.lower():v for k,v in values.items()});namespace=validator(settings)
 if function is not None:namespace['public_webapp_url_for_links']=function
 fixture_name='scripts/npm_security_ci/public_document_fixture.json'
 if fixture_bytes is None:fixture_bytes=C.public_bytes(ROOT/fixture_name,512,(131,'8f1c0062f5c8bd157ce9145e4369bbc109f49607ac1f80a4029a170d70ba7f96'))
 if type(fixture_bytes)is not bytes or len(fixture_bytes)>512:raise AssertionError('owned public fixture bound')
 calls=[]if calls is None else calls
 class PublicFixtureBytes(io.BytesIO):
  def read(self,size=-1):
   if size!=132:raise AssertionError('public fixture read must remain bounded132')
   calls.append(('public_fixture_read',size));return super().read(size)
 class PublicFixturePath:
  def __init__(self,name):
   if type(name)is not str or name!=fixture_name:raise AssertionError('unknown owned public path')
  def open(self,mode):
   if mode!='rb':raise AssertionError('public fixture read-only mode')
   return PublicFixtureBytes(fixture_bytes)
 def public_mime(contents,*,mime):
  # Explicit owned double: no claim about the actual backend's libmagic.
  if mime is not True or type(contents)is not bytes or(len(contents),sha256(contents).hexdigest())!=(12582912,'b24434d88423fe82a69c5e4a8d30d52ced4d5f22a8073310f04da1e89ded21c4'):
   raise AssertionError('MIME double requires exact full12MiB public fixture')
  calls.append(('owned_magic_full_fixture',len(contents)));return mime_type
 namespace.update(Path=PublicFixturePath,sha256=sha256,json=json,magic=SimpleNamespace(from_buffer=public_mime,version=lambda:0))
 code=preflight()if tree is None else tree
 code.body=[n for n in code.body if not isinstance(n,(ast.Import,ast.ImportFrom))]
 real_import=builtins.__import__
 def blocked(name,*args,**kwargs):
  if name.startswith(('core.config','dotenv','pydantic_settings')):raise AssertionError('live settings imports forbidden')
  return real_import(name,*args,**kwargs)
 out=io.StringIO();err=io.StringIO();completed=False
 try:
  with patch.object(builtins,'__import__',side_effect=blocked),redirect_stdout(out),redirect_stderr(err):exec(compile(code,'<owned CI preflight>','exec'),namespace)
  completed=True
 finally:
  if err.getvalue():raise AssertionError('preflight emitted unexpected stderr')
  if out.getvalue():
   if not completed:raise AssertionError('failed preflight emitted output')
   if json.loads(out.getvalue())!={'public_document_mime_preflight':'PASS','bytes':12582912,'libmagic_version':0}:raise AssertionError('preflight emitted configuration')
  elif completed:raise AssertionError('public MIME preflight receipt missing')
 namespace['_owned_public_calls']=calls;namespace['_owned_mime_version_is_mock']=True
 return namespace

class BrowserFixtureContractTests(unittest.TestCase):
 def test_exact_CI_staging_Iran_https_origin_passes_actual_validator_and_preflight(self):
  env=fixture_env();self.assertEqual(env['ENVIRONMENT'],'staging');self.assertEqual(env['SERVER_MODE'],'iran')
  self.assertEqual(env['FRONTEND_URL'],'http://127.0.0.1:4173');self.assertEqual(env['PUBLIC_WEBAPP_URL'],EXPECTED)
  self.assertEqual(env['IRAN_SERVER_DOMAIN'],'ci-iran.invalid');ns=run_preflight(env)
  self.assertEqual(ns['public_origin'],EXPECTED);self.assertEqual(ns['EXPECTED_CI_PUBLIC_WEBAPP_ORIGIN'],EXPECTED)
 def test_missing_HTTP_foreign_and_unconfigured_origins_fail_without_values(self):
  for change in({'PUBLIC_WEBAPP_URL':''},{'PUBLIC_WEBAPP_URL':'http://ci-iran.invalid'},
   {'IRAN_SERVER_DOMAIN':''},{'FOREIGN_SERVER_DOMAIN':'ci-iran.invalid'},
   {'PUBLIC_WEBAPP_URL':'https://wrong.invalid'},{'PUBLIC_WEBAPP_URL':'https://user:OWNED_CANARY@ci-iran.invalid'}):
   env={**fixture_env(),**change}
   with self.assertRaises(SystemExit)as failure:run_preflight(env)
   self.assertEqual(failure.exception.code,'invalid_ci_public_webapp_origin');self.assertNotIn('OWNED_CANARY',str(failure.exception))
 def test_other_valid_origin_environment_and_server_fail_with_fixed_codes(self):
  for change,expected in(({'PUBLIC_WEBAPP_URL':'https://other.invalid','IRAN_SERVER_DOMAIN':'other.invalid'},'unexpected_ci_public_webapp_origin'),
   ({'ENVIRONMENT':'OWNED_CANARY'},'unexpected_ci_environment'),({'SERVER_MODE':'OWNED_CANARY'},'unexpected_ci_server_mode')):
   with self.assertRaises(SystemExit)as failure:run_preflight({**fixture_env(),**change})
   self.assertEqual(failure.exception.code,expected);self.assertNotIn('OWNED_CANARY',str(failure.exception))
 def test_validator_exception_message_never_escapes_preflight(self):
  def fail(**kwargs):raise ValueError('OWNED_CANARY_PRIVATE_SETTING')
  with self.assertRaises(SystemExit)as error:run_preflight(fixture_env(),function=fail)
  self.assertEqual(str(error.exception),'invalid_ci_public_webapp_origin');self.assertIsNone(error.exception.__cause__);self.assertTrue(error.exception.__suppress_context__)
 def test_preflight_precedes_whole_browser_gate_and_keeps_all_mutation_guards(self):
  steps=browser_steps();names=[s.get('name')for s in steps]
  self.assertLess(names.index('Verify browser CI backend surface'),names.index('Existing complete browser gate'))
  self.assertLess(names.index('Verify browser CI backend surface'),names.index('Collect complete browser identities without executing tests'))
  raw=C.public_bytes(ROOT/WORKFLOW,32768).decode();self.assertIn('--max-failures=3',raw)
  for bad in('ignoreHTTPSErrors','NODE_TLS_REJECT_UNAUTHORIZED','curl -k','test.skip','test.only'):self.assertNotIn(bad,raw)
  imports=[n.module for n in preflight().body if isinstance(n,ast.ImportFrom)]
  self.assertEqual(imports,['core.config','core.public_webapp_url','hashlib','pathlib'])
  self.assertEqual([(n.module,[(a.name,a.asname)for a in n.names])for n in preflight().body if isinstance(n,ast.ImportFrom)],
   [('core.config',[('settings',None)]),('core.public_webapp_url',[('public_webapp_url_for_links',None)]),('hashlib',[('sha256',None)]),('pathlib',[('Path',None)])])
  self.assertEqual([[(a.name,a.asname)for a in n.names]for n in preflight().body if isinstance(n,ast.Import)],[[('json',None)],[('magic',None)]])
 def test_complete_actual_preflight_reads_one_owned_path_and_exact_full12MiB_magic_buffer(self):
  ns=run_preflight(fixture_env());self.assertEqual(ns['_owned_public_calls'],[('public_fixture_read',132),('owned_magic_full_fixture',12582912)])
  self.assertEqual((len(ns['document']),sha256(ns['document']).hexdigest()),(12582912,'b24434d88423fe82a69c5e4a8d30d52ced4d5f22a8073310f04da1e89ded21c4'));self.assertTrue(ns['_owned_mime_version_is_mock'])
  with self.assertRaisesRegex(AssertionError,'unknown owned public path'):ns['Path']('/OWNED_CANARY_PRIVATE_PATH')
  with self.assertRaisesRegex(AssertionError,'read-only'):ns['Path']('scripts/npm_security_ci/public_document_fixture.json').open('wb')
 def test_actual_preflight_rejects_short_long_and_changed_public_template_before_magic(self):
  raw=C.public_bytes(ROOT/'scripts/npm_security_ci/public_document_fixture.json',512)
  for body in(raw[:-1],raw+b'X',b'X'+raw[1:]):
   with self.subTest(bytes=len(body)):
    calls=[]
    with self.assertRaises(SystemExit)as error:run_preflight(fixture_env(),fixture_bytes=body,calls=calls)
    self.assertEqual(error.exception.code,'unexpected_ci_public_document_source');self.assertEqual(calls,[('public_fixture_read',132)])
 def test_actual_preflight_digest_guard_rejects_changed_generated_bytes_and_wrong_size(self):
  # Mutate only owned copies of the actual producer AST as counterfactuals.
  # The actual hash guard must refuse before the strict magic double is called.
  for target in('size','document'):
   with self.subTest(target=target):
    code=preflight();assignment=next(n for n in code.body if isinstance(n,ast.Assign)and any(isinstance(t,ast.Name)and t.id==target for t in n.targets))
    replacement='12582911'if target=='size'else"b'X'+document[1:]"
    if target=='document':assignment.value=ast.BinOp(left=ast.Constant(b'X'),op=ast.Add(),right=ast.Subscript(value=assignment.value,slice=ast.Slice(lower=ast.Constant(1)),ctx=ast.Load()))
    else:assignment.value=ast.parse(replacement,mode='eval').body
    ast.fix_missing_locations(code);calls=[]
    with self.assertRaises(SystemExit)as error:run_preflight(fixture_env(),tree=code,calls=calls)
    self.assertEqual(error.exception.code,'unexpected_ci_public_document_bytes');self.assertEqual(calls,[('public_fixture_read',132)])
 def test_actual_preflight_wrong_MIME_uses_fixed_code_without_private_value(self):
  for mime in('application/SIMH-tape-data','OWNED_CANARY_PRIVATE_MIME'):
   with self.subTest(mime=mime):
    calls=[]
    with self.assertRaises(SystemExit)as error:run_preflight(fixture_env(),mime_type=mime,calls=calls)
    self.assertEqual(error.exception.code,'unexpected_ci_public_document_mime');self.assertNotIn('OWNED_CANARY',str(error.exception));self.assertEqual(calls,[('public_fixture_read',132),('owned_magic_full_fixture',12582912)])
 def test_advertised_links_do_not_navigate_to_synthetic_external_origin(self):
  customer=C.public_bytes(ROOT/'frontend/e2e/customer-owner-flow.spec.ts',131072).decode()
  accountant=C.public_bytes(ROOT/'frontend/e2e/accountant-owner-flow.spec.ts',131072).decode()
  stage=C.public_bytes(ROOT/'frontend/e2e/registration-stage7.spec.ts',16384).decode()
  self.assertIn('return `${url.pathname}${url.search}`',customer)
  self.assertIn('await page.goto(toRelativeRegistrationPath(registrationLink))',customer)
  self.assertNotRegex(customer,r'page\.goto\(registrationLink\)')
  self.assertIn("expect(pendingRelation?.registration_link).toContain('/register')",accountant)
  self.assertNotRegex(accountant,r'page\.goto\([^\n]*(?:registration_link|registrationLink)')
  paths=re.findall(r"page\.goto\('([^']+)'\)",stage);self.assertTrue(paths);self.assertTrue(all(p.startswith('/')for p in paths))
  self.assertIn("page.route('**/api/invitations/lookup/",stage)

class MountCanonicalContractTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  C.CollectorFailureDiagnosticsTests.setUpClass();base=C.CollectorFailureDiagnosticsTests
  cls.D=base.D;cls.F=base.F;cls.M=base.M;cls.Formatter=base.Formatter
 setUp=C.CollectorFailureDiagnosticsTests.setUp
 write_before=C.CollectorFailureDiagnosticsTests.write_before
 command=C.CollectorFailureDiagnosticsTests.command
 collect=C.CollectorFailureDiagnosticsTests.collect
 formatted=C.CollectorFailureDiagnosticsTests.formatted
 def test_all_120_valid_mount_permutations_have_one_canonical_identity(self):
  original=deepcopy(self.scope);expected=self.D.app_scope(deepcopy(original),self.cid)
  self.assertEqual([r['Destination']for r in expected['Mounts']],sorted(r['Destination']for r in original['Mounts']))
  for order in permutations(original['Mounts']):
   value={**deepcopy(original),'Mounts':list(order)};self.assertEqual(self.D.app_scope(value,self.cid),expected)
  self.assertEqual(original,self.scope)
 def test_duplicates_missing_extra_and_each_mount_field_drift_are_rejected(self):
  variants=[]
  for transform in(lambda rows:rows[:-1],lambda rows:rows+[deepcopy(rows[0])],lambda rows:rows[:-1]+[deepcopy(rows[0])]):
   value=deepcopy(self.scope);value['Mounts']=transform(value['Mounts']);variants.append(value)
  for index in range(5):
   for field,new in(('Type','wrong'),('Source','/OWNED_WRONG'),('Destination','/OWNED_WRONG'),('Name','OWNED_WRONG'),('RW',None)):
    value=deepcopy(self.scope);value['Mounts'][index][field]=new;variants.append(value)
   value=deepcopy(self.scope);value['Mounts'][index]['RW']=not value['Mounts'][index]['RW'];variants.append(value)
   value=deepcopy(self.scope);value['Mounts'][index]['EXTRA']=True;variants.append(value)
  for value in variants:
   with self.assertRaises(ValueError):self.D.app_scope(value,self.cid)
 def test_CID_image_and_other_static_identity_fields_remain_exact(self):
  for field,value in(('Id','b'*64),('Image','wrong'),('Name','/wrong'),('Service','wrong'),('Project','wrong'),('WorkingDir','/wrong'),('ConfigFiles','/wrong')):
   altered=deepcopy(self.scope);altered[field]=value
   with self.assertRaises(ValueError):self.D.app_scope(altered,self.cid)
 def test_actual_collector_accepts_before_after_mount_reordering_and_redacts_traces(self):
  self.raw=self.formatted();original=self.command
  def command(argv,**kwargs):
   raw=original(argv,**kwargs)
   if argv[2:5]==['container','inspect','--format']and self.inspect_count==2:
    value=json.loads(raw);value['Mounts'].reverse();value['RestartCount']=1;return json.dumps(value).encode()
   return raw
  result=self.collect(command);self.assertEqual(result['status'],'OBSERVED_STRUCTURAL_TAIL');self.assertTrue(result['original_scope_readers_closed'])
  self.assertTrue(result['structure']['traces']);self.assertFalse(result['raw_log_retained']);self.assertEqual(len(self.calls),3)
  self.assertNotIn(C.CANARY,json.dumps(result));self.assertEqual(result['capture_state_after']['RestartCount'],1)
 def test_actual_collector_still_refuses_changed_source_after_valid_sort(self):
  self.raw=self.formatted();original=self.command
  def command(argv,**kwargs):
   raw=original(argv,**kwargs)
   if argv[2:5]==['container','inspect','--format']and self.inspect_count==2:
    value=json.loads(raw);value['Mounts'].reverse();value['Mounts'][0]['Source']='/OWNED_WRONG';return json.dumps(value).encode()
   return raw
  result=self.collect(command);self.assertEqual(result['status'],'UNAVAILABLE');self.assertEqual(result['failed_phase'],'after_inspect');self.assertEqual(result['failure_code'],'app_mount_source')
  self.assertFalse(result['original_scope_readers_closed'])

if __name__=='__main__':unittest.main()
