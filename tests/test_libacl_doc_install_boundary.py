"""Pure actual-source contracts and owned missing-file diagnostics; no install."""
from fnmatch import fnmatchcase
from hashlib import sha256
from pathlib import Path
from types import ModuleType
import ast,errno,json,stat,tempfile,unittest
ROOT=Path(__file__).resolve().parents[1]
def load(name):
 path=ROOT/'scripts'/name;m=ModuleType('owned_'+path.stem);m.__file__=str(path);exec(compile(path.read_bytes(),str(path),'exec'),m.__dict__);return m
A=load('prepare_app_security_os_cache.py');H=load('prepare_coreutils_acl_os_cache.py');V=load('compose_security_os_candidate.py')
class BoundaryTests(unittest.TestCase):
 def paths(self):return tuple(str(A.CAPSULE/r['path'])for r in json.loads((ROOT/'scripts/app_security_os_packages.lock.json').read_bytes())['packages'])
 def vendor_argv(self,flag):
  candidates=[n for n in ast.walk(ast.parse(V.INSTALLER))if isinstance(n,ast.Call)and isinstance(n.func,ast.Name)and n.func.id=='run'and n.args and isinstance(n.args[0],ast.List)and any(isinstance(v,ast.Constant)and v.value==flag for v in n.args[0].elts)];self.assertEqual(len(candidates),1)
  return eval(compile(ast.Expression(candidates[0].args[0]),'<authenticated vendor argv>','eval'),{'paths':list(self.paths())})
 def boundary(self,argv):
  tree=ast.parse((ROOT/'scripts/prepare_app_security_os_cache.py').read_bytes());cls=next(n for n in tree.body if isinstance(n,ast.ClassDef)and n.name=='InstallerCommands');run=next(n for n in cls.body if isinstance(n,ast.FunctionDef)and n.name=='run');self.assertIsInstance(run.body[1],ast.If)
  values={'argv':argv,'self':A.InstallerCommands(A.approved_installer_commands(self.paths())),'need':A.need,'LIBACL_DOC_APT_OPTIONS':A.LIBACL_DOC_APT_OPTIONS};exec(compile(ast.Module(body=run.body[:2],type_ignores=[]),'<actual authenticated install boundary>','exec'),values);return values['argv']
 def test_actual_DEB_required_payload_and_exact_pinned_slim_policy_exclusion(self):
  path=ROOT/'tests/fixtures/libacl1-approved-deb-payload.json';raw=path.read_bytes();self.assertEqual((len(raw),sha256(raw).hexdigest()),(1881,'5942f05d78f6b7b8bdef19db8c7215c2bb418c3768d0e024429913a7720aabb4'));value=json.loads(raw);self.assertEqual(value['payload'],json.loads((ROOT/'scripts/coreutils_acl_package.lock.json').read_bytes())['runtime_payload']['approved_libacl'])
  raw=(ROOT/'deploy/maintenance/app_6de_smoke/dpkg-docker-policy.original.txt').read_bytes();self.assertEqual((len(raw),sha256(raw).hexdigest()),(3501,'050ba34b74480e1cc00dd774153e729e76553b0af595e0d517fddc95b855d4ce'));rules=[(line.split()[0]=='path-include',line.split()[1])for line in raw.decode().splitlines()if line and not line.startswith('#')]
  def kept(path,extra=()):
   result=True
   for include,pattern in [*rules,*extra]:
    if fnmatchcase(path,pattern):result=include
   return result
  excluded=[r['path']for r in value['payload']['regular_files']if not kept(r['path'])];self.assertEqual(excluded,['/usr/share/doc/libacl1/changelog.gz','/usr/share/doc/libacl1/changelog.Debian.gz'])
  extra=[(True,arg.partition('--path-include=')[2])for arg in A.LIBACL_DOC_APT_OPTIONS if arg.startswith('DPkg::Options::=')];self.assertTrue(all(kept(r['path'],extra)for r in value['payload']['regular_files']));self.assertFalse(kept('/usr/share/doc/unrelated/changelog.gz',extra));self.assertFalse(kept('/usr/share/doc/libacl1-extra/changelog.gz',extra))
 def test_actual_vendor_15_package_install_only_gets_exact_libacl1_doc_options(self):
  argv=self.vendor_argv('--yes');expected=['/usr/bin/apt-get','-o','DPkg::Options::=--path-include=/usr/share/doc/libacl1','-o','DPkg::Options::=--path-include=/usr/share/doc/libacl1/*',*argv[1:]]
  self.assertEqual(self.boundary(argv),expected);self.assertEqual(self.boundary(tuple(argv)),expected);self.assertEqual(argv[4:],list(self.paths()));self.assertEqual(len(self.paths()),15)
 def test_simulation_unchanged_and_broad_or_extra_options_refuse_before_boundary(self):
  argv=self.vendor_argv('--simulate');self.assertEqual(self.boundary(argv),argv);install=self.vendor_argv('--yes')
  for bad in([install[0],'-o','DPkg::Options::=--path-include=/usr/share/doc/*',*install[1:]],install+['/capsule/unrelated.deb'],[install[0],*install[1:4],*reversed(install[4:])]):
   with self.assertRaisesRegex(RuntimeError,'^installer_command$'):self.boundary(bad)
 def test_original_vendor_INSTALLER_recipe_lock_and_package_artifacts_remain_exact(self):
  raw=(ROOT/'scripts/compose_security_os_candidate.py').read_bytes();self.assertEqual((len(raw),sha256(raw).hexdigest()),(54258,'a7ba6515937e7ae8193be4d550547651fe5b313399ec3a1f7634efe513e151c1'));lock=json.loads((ROOT/'scripts/app_security_os_packages.lock.json').read_bytes());raw=V.INSTALLER.encode();self.assertEqual({'bytes':len(raw),'sha256':sha256(raw).hexdigest()},lock['installer']);self.assertEqual(len(lock['packages']),15)
 def test_only_boundary_and_closed_diagnostics_change_entire_original_helpers(self):
  raw=(ROOT/'scripts/prepare_app_security_os_cache.py').read_text();constant="LIBACL_DOC_APT_OPTIONS=('-o','DPkg::Options::=--path-include=/usr/share/doc/libacl1','-o','DPkg::Options::=--path-include=/usr/share/doc/libacl1/*')\n";block="        # Only the already authenticated 15-package install argv gets these\n        # exact libacl1 doc includes; approved INSTALLER bytes remain unchanged.\n        if tuple(argv[:4])==('/usr/bin/apt-get','--yes','--no-remove','install'):\n            argv=[argv[0],*LIBACL_DOC_APT_OPTIONS,*argv[1:]]\n";self.assertEqual(raw.count(constant),1);self.assertEqual(raw.count(block),1);restored=raw.replace(constant,'').replace(block,'').encode();self.assertEqual((len(restored),sha256(restored).hexdigest()),(27505,'4f35aa785ea53f18ad42f5fdd2ab69362daf3af3e96efe3945fa8910dc8ffcb9'))
  p=ROOT/'tests/market_security_source_fixtures.py';fixture=ModuleType('owned_historical_coreutils_fixture');exec(compile(p.read_bytes(),str(p),'exec'),fixture.__dict__);raw=fixture.historical_gold1_coreutils_helper(ROOT).decode();a=raw.index('def failure(error):');b=raw.index('def main(argv=None):',a)
  current=(ROOT/'scripts/prepare_coreutils_acl_os_cache.py').read_text();ca=current.index('def failure(error):');cb=current.index('def main(argv=None):',ca);self.assertEqual(current[ca:cb],raw[a:b])
  raw=raw[:a]+raw[b:];new="  result=failure(error);print(json.dumps(result,sort_keys=True));return 1";old="  code=error.args[0] if type(error) is RuntimeError and len(error.args)==1 and type(error.args[0]) is str and error.args[0] in CODES else 'unexpected_refusal'\n  result={'status':'refused','code':code,'security_accepted':False};print(json.dumps(result,sort_keys=True));return 1";self.assertEqual(raw.count(new),1);restored=raw.replace(new,old).encode();self.assertEqual((len(restored),sha256(restored).hexdigest()),(36934,'6a6610085ace5e20b73f75984366c408664647cd0a2ae7714968875ba0e5c899'))
 def test_actual_owned_missing_file_reports_closed_class_errno_and_public_frames_only(self):
  with tempfile.TemporaryDirectory(prefix='owned-libacl-diag-',dir=ROOT)as directory:
   path=Path(directory)/'OWNED_SUPPRESSED_FILENAME'
   try:H.PublicFile(path,64)
   except FileNotFoundError as error:value=H.failure(error)
   else:self.fail('missing owned file accepted')
  raw=json.dumps(value);self.assertEqual(value['exception_class'],'FileNotFoundError');self.assertEqual(value['errno'],errno.ENOENT);self.assertEqual(value['code'],'unexpected_refusal');self.assertTrue(value['public_source_frames']);self.assertNotIn('OWNED_SUPPRESSED_FILENAME',raw);self.assertNotIn(directory,raw)
  for row in value['public_source_frames']:self.assertEqual(set(row),{'function','line'});self.assertNotIn('test_',row['function']);self.assertIs(type(row['line']),int)
 def test_unknown_error_details_and_args_are_never_diagnostic_output(self):
  class Unknown(Exception):pass
  for error in(Unknown('OWNED_SUPPRESSED_DETAIL'),OSError(99999,'OWNED_SUPPRESSED_DETAIL'),RuntimeError('OWNED_SUPPRESSED_DETAIL')):
   value=H.failure(error);self.assertNotIn('OWNED_SUPPRESSED_DETAIL',json.dumps(value));self.assertEqual(value['errno'],None);self.assertLessEqual(len(value['public_source_frames']),24);self.assertFalse(value['security_accepted'])
  self.assertEqual(H.failure(Unknown())['exception_class'],'unclassified');self.assertEqual(H.failure(RuntimeError('package_payload'))['code'],'package_payload')
if __name__=='__main__':unittest.main()
