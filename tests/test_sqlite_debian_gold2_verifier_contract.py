"""Real CLI parser/capture controls before SQL, ctypes or a package operation.

Only the exact verifier constants/main AST run in owned Python children. Their
verify() seam returns explicitly synthetic binary bindings; no SQLite is loaded.
"""
from contextlib import ExitStack
from hashlib import sha256
from pathlib import Path
from types import ModuleType
from unittest.mock import patch
import ast,json,os,subprocess,sys,tempfile,unittest

ROOT=Path(__file__).resolve().parent.parent
DIR=ROOT/'deploy/security/sqlite-wal-backport'
RAW=(DIR/'verify_positive.py').read_bytes()
OLD=RAW.replace(b"FIXED_PACKAGE = BASE_PACKAGE + '+gold2'\nALLOWED_PACKAGES = (BASE_PACKAGE, BASE_PACKAGE + '+gold1', FIXED_PACKAGE)",
                b"FIXED_PACKAGE = BASE_PACKAGE + '+gold1'\nALLOWED_PACKAGES = (BASE_PACKAGE, FIXED_PACKAGE)")
def pin(b):return(len(b),sha256(b).hexdigest())
def load(p,name):
 m=ModuleType(name);m.__file__=str(p);exec(compile(p.read_bytes(),str(p),'exec'),m.__dict__);return m
R=load(DIR/'sourcebuild.py','owned_verifier_recipe')
A=load(ROOT/'scripts/prepare_sqlite_wal_ci_context.py','owned_verifier_consumer')
CHILDREN=[];REAL_POPEN=subprocess.Popen

def parser_program(raw,payload):
 tree=ast.parse(raw);names={'BASE_PACKAGE','FIXED_PACKAGE','ALLOWED_PACKAGES','PUBLIC_FAILURE_CODES'}
 constants=[n for n in tree.body if isinstance(n,ast.Assign)and any(isinstance(t,ast.Name)and t.id in names for t in n.targets)]
 main=next(n for n in tree.body if isinstance(n,ast.FunctionDef)and n.name=='main')
 imports=ast.parse('import argparse,json,signal,sys\n').body
 # The caller/source version is the only changed input in matched cases.
 stub=ast.parse("def verify(expected):\n assert 'sqlite3'not in sys.modules and 'ctypes'not in sys.modules\n value="+repr(payload)+"\n value['sqlite_package']=expected\n value['owned_parser_stub_reached']=True\n return value\n").body
 end=ast.parse('raise SystemExit(main())\n').body
 return(ast.unparse(ast.fix_missing_locations(ast.Module(body=imports+constants+stub+[main]+end,type_ignores=[])))+'\n').encode()

class VerifierContractTests(unittest.TestCase):
 def setUp(self):
  self.stack=ExitStack();self.addCleanup(self.stack.close)
  self.tmp=Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='.owned-verifier-parser-',dir=ROOT/'tests')))
  self.recipe=self.tmp/'recipe';self.recipe.mkdir();self.binary=self.tmp/'owned-binary';self.binary.write_bytes(b'OWNED SYNTHETIC BINARY BYTES; NOT AN ELF OR SQLITE\n')
  b=self.binary.read_bytes();binding={'path':str(self.binary),'bytes':len(b),'sha256':sha256(b).hexdigest(),'mapped_inode_verified':True}
  self.payload={'schema':'sqlite_ordinary_compatibility/1','status':'PASS','sqlite_package':'OWNED_ARGUMENT_ONLY',
   'production_data_accessed':False,'wal_reset_reproduction_performed':False,'security_accepted':False,
   'compile_options':['THREADSAFE=1'],'system_library':str(self.binary),'sqlite_system_library':dict(binding),'sqlite_stdlib_extension':dict(binding)}
  def spawn(argv,**kwargs):
   self.assertEqual(argv[:3],['/usr/local/bin/python3','-I','-B']);self.assertEqual(Path(argv[3]),self.recipe/'verify_positive.py')
   self.assertEqual(kwargs['stdin'],subprocess.DEVNULL);self.assertIs(kwargs['stderr'],subprocess.STDOUT)
   self.assertTrue(kwargs['close_fds']);self.assertTrue(kwargs['start_new_session']);self.assertNotIn('user',kwargs)
   # The Debian image path is asserted above. Use this test interpreter for
   # the same pure-parser program on hosts without /usr/local/bin/python3.
   child=REAL_POPEN([sys.executable,*argv[1:]],**kwargs);CHILDREN.append(child);return child
  self.stack.enter_context(patch.object(R,'RECIPE',self.recipe))
  self.stack.enter_context(patch.object(R,'current_environment',return_value=dict(R.ENV)))
  self.stack.enter_context(patch.object(R.subprocess,'Popen',side_effect=spawn))
  self.addCleanup(self.children_closed)
 def children_closed(self):
  self.assertTrue(all(c.returncode is not None and c.stdout.closed for c in CHILDREN))
 def install(self,raw):
  p=self.recipe/'verify_positive.py';b=parser_program(raw,self.payload);p.write_bytes(b);p.chmod(0o600)
  self.stack.enter_context(patch.object(R,'VERIFY_PIN',pin(b)));return p
 def capture(self,version):
  return R.bounded_command(['/usr/local/bin/python3','-I','-B',str(self.recipe/'verify_positive.py'),'--expect-package-version',version],cwd=None,timeout=10,maximum=65536,capture=True,with_code=True)

 def test_source03_counterfactual_rejects_gold2_before_verify_and_current_parser_accepts_it(self):
  self.assertEqual(pin(OLD),(16377,'623384cc7e63c04834d1042c63064b85a83c6966d0cc95da172f5e719f8f06ab'))
  self.install(OLD);code,raw=self.capture(R.VERSION)
  self.assertEqual(code,2);self.assertIn(b'invalid choice',raw);self.assertNotIn(b'owned_parser_stub_reached',raw)
  with self.assertRaises(json.JSONDecodeError):json.loads(raw)
  # Actual positive() retains the original parse failure: no stripping or fallback.
  with self.assertRaises(json.JSONDecodeError):R.positive(R.VERSION)
  self.install(RAW);value=R.positive(R.VERSION)
  self.assertEqual(value['sqlite_package'],'3.46.1-7+deb13u2+gold2');self.assertTrue(value['owned_parser_stub_reached'])
  self.assertFalse(value['security_accepted']);self.assertEqual(value['compile_options'],['THREADSAFE=1'])

 def test_base_legacy_gold1_current_gold2_valid_and_every_other_version_rejected_before_verify(self):
  self.install(RAW)
  for version in(R.BASE_VERSION,R.BASE_VERSION+'+gold1',R.VERSION):
   with self.subTest(version=version):
    code,raw=self.capture(version);self.assertEqual(code,0);value=json.loads(raw)
    self.assertEqual(value['sqlite_package'],version);self.assertTrue(value['owned_parser_stub_reached'])
  for version in(R.BASE_VERSION+'+gold3','3.46.1','FOREIGN',R.VERSION+'\n'):
   with self.subTest(version=version):
    code,raw=self.capture(version);self.assertEqual(code,2);self.assertNotIn(b'owned_parser_stub_reached',raw)

 def test_platform_package_uses_same_closed_allowlist_before_any_metadata_or_SQL(self):
  tree=ast.parse(RAW);names={'BASE_PACKAGE','FIXED_PACKAGE','ALLOWED_PACKAGES'}
  nodes=[n for n in tree.body if isinstance(n,ast.Assign)and any(isinstance(t,ast.Name)and t.id in names for t in n.targets)]
  nodes.extend(n for n in tree.body if isinstance(n,ast.FunctionDef)and n.name in('require','platform_package'))
  def forbidden(*a,**k):raise AssertionError('metadata or SQL must not be reached')
  ns={'Path':forbidden,'command':forbidden,'sqlite3':None}
  exec(compile(ast.fix_missing_locations(ast.Module(body=nodes,type_ignores=[])),'<actual-platform-package-allowlist>','exec'),ns)
  self.assertEqual(ns['FIXED_PACKAGE'],R.VERSION)
  self.assertEqual(ns['ALLOWED_PACKAGES'],(R.BASE_VERSION,R.BASE_VERSION+'+gold1',R.VERSION))
  with self.assertRaisesRegex(ValueError,'package_scope'):ns['platform_package'](R.BASE_VERSION+'+gold3')

 def test_exact_verifier_functions_ABI_semantics_and_consumer_pins_are_preserved(self):
  funcs=lambda raw:{n.name:ast.dump(n,include_attributes=False)for n in ast.parse(raw).body if isinstance(n,ast.FunctionDef)}
  self.assertEqual(funcs(OLD),funcs(RAW));self.assertEqual(R.VERIFY_PIN,pin(RAW))
  self.assertEqual(A.PINS['verify_positive.py'],pin(RAW));self.assertEqual(A.PINS['sourcebuild.py'],pin((DIR/'sourcebuild.py').read_bytes()))
  self.assertEqual(set(A.PINS),set(R.RECIPE_FILES));self.assertEqual(len(A.PINS),6)
  self.assertEqual(R.VENDOR_COMMANDS,{'vendor-standard.log':['/usr/bin/make','test'],'vendor-fts5-regression.log':['./testfixture','ext/fts5/test/fts5prefix2.test']})
  self.assertEqual(R.ENV['DEB_BUILD_OPTIONS'],'nocheck parallel=2')

if __name__=='__main__':unittest.main()
