"""Normal source/signature/input contracts; no image/build/deploy actions."""
from hashlib import sha256
from pathlib import Path
from types import ModuleType
import ast,json,tempfile,unittest
ROOT=Path(__file__).resolve().parents[1];FIXTURES=ROOT/'tests/fixtures/tar_acl_cache'
P=ROOT/'scripts/prepare_canonical_app_image.py';C=ModuleType('_owned_canonical');C.__file__=str(P);exec(compile(P.read_bytes(),str(P),'exec'),C.__dict__)


def fingerprint(node):
 for item in ast.walk(node):
  if hasattr(item,'type_params'):
   if item.type_params:raise AssertionError('unexpected type parameters')
   item._fields=tuple(name for name in item._fields if name!='type_params')
 return sha256(ast.dump(node,include_attributes=False).encode()).hexdigest()


class BuildIntegrationTests(unittest.TestCase):
 def test_normal_docker_installs_old15_sqlite_then_tar_once_without_baking_cache(self):
  for name,prefix in(('Dockerfile','/app/scripts'),('Dockerfile.iran','/app/scripts'),('deploy/market-data/Dockerfile','/usr/local/libexec')):
   text=(ROOT/name).read_text()
   with self.subTest(name=name):
    positions=[text.index(prefix+'/'+leaf)for leaf in('prepare_app_security_os_cache.py','prepare_sqlite_wal_os_cache.py','prepare_tar_acl_os_cache.py')]
    self.assertEqual(positions,sorted(positions));self.assertEqual(text.count(prefix+'/prepare_tar_acl_os_cache.py'),1)
    self.assertIn('source=pip_packages/tar-acl-capsule,target=/tar-capsule,ro',text)
    self.assertNotIn('COPY pip_packages/tar-acl-capsule',text)
    self.assertIn('"--no-new-privs", "/usr/local/bin/python3", "-I", "-B", "'+prefix+'/prepare_tar_acl_os_cache.py"',text)
 def test_only_selected_three_canonical_methods_change(self):
  record=json.loads((FIXTURES/'canonical-baseline-functions.json').read_bytes());nodes={n.name:fingerprint(n)for n in ast.parse(P.read_bytes()).body if isinstance(n,(ast.FunctionDef,ast.ClassDef))}
  self.assertEqual({name:nodes[name]for name in record['unchanged_functions']},record['unchanged_functions'])
  self.assertEqual(set(nodes)-set(record['unchanged_functions']),{'public_input_names','prepare','build'})
 def inputs(self,root):
  values={'scripts/app_security_os_packages.lock.json':(FIXTURES/'baseline-app-security-lock.json').read_bytes(),
   'scripts/sqlite_wal_package.lock.json':(FIXTURES/'baseline-sqlite-lock.json').read_bytes(),
   'scripts/tar_acl_package.lock.json':(ROOT/'scripts/tar_acl_package.lock.json').read_bytes()}
  rows=[]
  for name,raw in values.items():
   path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw);rows.append({'path':name,'identity':C.identity(path.lstat()),'sha256':sha256(raw).hexdigest()})
  return{'files':rows}
 def test_new_cache_has_exact_three_generated_inputs_and_source_binding(self):
  with tempfile.TemporaryDirectory(dir=ROOT)as tmp:
   root=Path(tmp);source=self.inputs(root);names=C.public_input_names(root,source)
   tar={n for n in names if 'tar-acl-capsule'in n}
   self.assertEqual(tar,{'pip_packages/.tar-acl-capsule.lock','pip_packages/tar-acl-capsule/manifest.json','pip_packages/tar-acl-capsule/tar_1.35+dfsg-5~trixie+gold2_amd64.deb'})
   (root/'scripts/tar_acl_package.lock.json').write_bytes(b'CHANGED')
   with self.assertRaisesRegex(RuntimeError,'source_changed'):C.public_input_names(root,source)
 def test_all_normal_shell_cache_callers_prepare_or_check_tar(self):
  expected={'deploy.sh':1,'scripts/bootstrap_ci_backend.sh':1,'scripts/deploy_staging.sh':1,'scripts/production_deploy_online.sh':6}
  for name,count in expected.items():
   source=(ROOT/name).read_text()
   with self.subTest(name=name):self.assertEqual(source.count('scripts/prepare_tar_acl_os_cache.py'),count)
  text=(ROOT/'scripts/production_deploy_online.sh').read_text()
  self.assertEqual(text.count('scripts/tar_acl_package.lock.json'),2)
  self.assertIn('--check --cache-dir "$output_dir/tar-acl-capsule"',text)
 def test_helper_and_lock_are_bound_in_both_market_git_input_and_parcel_lists(self):
  text=(ROOT/'scripts/production_deploy_online.sh').read_text()
  signature=text.split('market_pipeline_image_input_signature() {',1)[1].split('\n}',1)[0]
  archive=text.split('prepare_market_pipeline_control_payload() {',1)[1].split('\n}',1)[0]
  for body in(signature,archive):
   self.assertIn('scripts/prepare_tar_acl_os_cache.py',body);self.assertIn('scripts/tar_acl_package.lock.json',body)


if __name__=='__main__':unittest.main()
