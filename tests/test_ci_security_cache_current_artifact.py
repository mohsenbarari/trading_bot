"""Current public lineage and immutable release catalog, without downloads.

The durable tests execute the real entry and all source/lock/metadata guards.
The binary-transport boundary raises an explicit sentinel. Source QA separately
feeds the exact six retained public ZIPs through the SAME entry and all four
unchanged cache producers; no installer or real network is used in either case.
"""
from contextlib import ExitStack,redirect_stdout
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from types import ModuleType
from unittest.mock import patch
import ast,errno,io,json,os,runpy,socket,subprocess,tempfile,unittest

ROOT=Path(__file__).resolve().parents[1]
FILES=('prepare_app_security_os_cache.py','compose_security_os_candidate.py','app_security_os_packages.lock.json',
 'prepare_sqlite_wal_os_cache.py','sqlite_wal_package.lock.json','prepare_tar_acl_os_cache.py','tar_acl_package.lock.json',
 'prepare_coreutils_acl_os_cache.py','coreutils_acl_package.lock.json','prepare_ci_security_caches.py')
META=ROOT/'tests/fixtures/current_ci_cache_metadata.json'
PUBLIC_SQLITE_PIN=(994448,'3de13def5a52724060b429a99319e8d00f3538c09dcb64556648139bbce43e54')
def encoded(value):return(json.dumps(value,sort_keys=True,separators=(',',':'))+'\n').encode()
def load(path):
 value=ModuleType('owned_active_cache_'+path.stem);value.__file__=str(path)
 exec(compile(path.read_bytes(),str(path),'exec'),value.__dict__);return value
class BinaryBoundary(RuntimeError):pass

class CurrentArtifactTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory(prefix='.owned-current-cache-',dir=ROOT/'tests');self.addCleanup(self.temp.cleanup)
  self.root=Path(self.temp.name);(self.root/'scripts').mkdir();(self.root/'runner').mkdir()
  for name in FILES:
   path=self.root/'scripts'/name;path.write_bytes((ROOT/'scripts'/name).read_bytes());path.chmod(0o644)
  self.M=load(self.root/'scripts/prepare_ci_security_caches.py');self.metadata=json.loads(META.read_bytes())
  self.specs=dict(json.loads((self.root/'scripts/app_security_os_packages.lock.json').read_bytes())['artifacts'])
  for group,name in self.M.EXTRA_HELPERS.items():self.specs[group]=json.loads((self.root/'scripts'/(name+'_package.lock.json')).read_bytes())['artifact']
  self.wire={}
  for group,(workflow,name,head)in self.M.LINEAGE.items():
   spec=self.specs[group]
   self.wire['actions/runs/'+str(spec['run_id'])+'/attempts/1']={
    'id':spec['run_id'],'run_attempt':1,'workflow_id':workflow,'path':'.github/workflows/'+name,'head_sha':head,
    'event':'workflow_dispatch','status':'completed','conclusion':'success','repository':{'id':1065680631},'head_repository':{'id':1065680631}}
  sqlite=self.specs['sqlite-wal'];self.run_key='actions/runs/'+str(sqlite['run_id'])+'/attempts/1'
  # These two provider responses are genuine retained public API bytes, not a
  # synthetic passing replacement for the actual f88 producer identities.
  self.wire[self.run_key]=deepcopy(self.metadata['run'])
  self.artifact_key='actions/artifacts/'+str(sqlite['id']);self.binary_key=self.artifact_key+'/zip'
  self.wire[self.artifact_key]=deepcopy(self.metadata['artifact'])
  self.release_key='releases/'+str(self.M.RELEASE_ID);self.wire[self.release_key]=deepcopy(self.metadata['release'])
  self.calls=[];self.source_fds=[];self.loaded=[];self.results=[]
 def invoke(self,payloads=None,context=None):
  actual_run_path=runpy.run_path
  def transport(argv,*,seconds,maximum,environment):
   self.assertEqual(argv[:6],['/usr/bin/gh','api','--method','GET','-H','Accept: '+('application/octet-stream'if argv[-1].endswith('/zip')or '/releases/assets/'in argv[-1]else'application/vnd.github+json')])
   self.assertEqual(seconds,120);self.assertEqual(environment,{'GH_TOKEN':'PUBLIC-OWNED-NO-CREDENTIAL'})
   prefix='repos/'+self.M.REPO+'/';self.assertTrue(argv[-1].startswith(prefix));key=argv[-1][len(prefix):]
   self.calls.append((key,maximum,argv[5]));self.assertLessEqual(maximum,64*1024**2)
   if key in self.wire:
    self.assertEqual(maximum,65536);return encoded(self.wire[key])
   expected=self.specs['sqlite-wal']if key==self.binary_key else next(self.specs[g]for g,n in self.M.ASSET_IDS.items()if key=='releases/assets/'+str(n))
   self.assertEqual(maximum,expected['bytes'])
   if payloads is None:raise BinaryBoundary('OWNED_BINARY_TRANSPORT_BOUNDARY')
   raw=payloads[key];self.assertLessEqual(len(raw),maximum);return raw
  def source(path):
   namespace=actual_run_path(path);self.loaded.append(Path(path).name)
   if Path(path).name=='prepare_app_security_os_cache.py':
    actual=namespace['load_recipe']
    def recipe(held):
     try:value=actual(held)
     finally:self.source_fds.extend(row.fd for row in held.bindings)
     value.module.command=transport;return value
    namespace['load_recipe']=recipe
   else:
    actual=namespace['load_lock']
    def lock(held):
     try:return actual(held)
     finally:self.source_fds.extend(row.fd for row in held.files)
    namespace['load_lock']=lock
   return namespace
  output=io.StringIO()
  try:
   with patch.dict(os.environ,{'GH_TOKEN':'PUBLIC-OWNED-NO-CREDENTIAL','RUNNER_TEMP':str(self.root/'runner'),**getattr(self,'hosted_environment',{})},clear=True),\
    patch.object(self.M.runpy,'run_path',side_effect=source),\
    patch.object(subprocess,'Popen',side_effect=AssertionError('external process denied')),\
    patch.object(socket,'socket',side_effect=AssertionError('network denied')),redirect_stdout(output):
    self.M.main(context)
  finally:
   self.assertTrue(self.source_fds)
   for fd in self.source_fds:
    with self.assertRaises(OSError)as caught:os.fstat(fd)
    self.assertEqual(caught.exception.errno,errno.EBADF)
  return output.getvalue()
 def refuse(self,code):
  with self.assertRaisesRegex(RuntimeError,'^'+code+'$'):self.invoke()
  self.assertFalse(any(accept=='Accept: application/octet-stream'for _,_,accept in self.calls))
 def test_actual_entry_passes_genuine_f88_run_artifact_and_retained_six_asset_catalog_before_binary(self):
  self.assertEqual((self.specs['sqlite-wal']['bytes'],self.specs['sqlite-wal']['sha256']),PUBLIC_SQLITE_PIN)
  self.assertEqual(self.metadata['provenance']['Root_acceptance']['sha256'],self.M.SQLITE_ROOT_RECEIPT)
  with self.assertRaisesRegex(BinaryBoundary,'^OWNED_BINARY_TRANSPORT_BOUNDARY$'):self.invoke()
  self.assertEqual([key for key,_,_ in self.calls[:8]],[*['actions/runs/'+str(self.specs[g]['run_id'])+'/attempts/1'for g in self.M.LINEAGE],self.artifact_key,self.release_key])
  self.assertEqual(len(self.source_fds),5)
  self.assertEqual(self.loaded,['prepare_app_security_os_cache.py','prepare_sqlite_wal_os_cache.py','prepare_tar_acl_os_cache.py','prepare_coreutils_acl_os_cache.py'])
  self.assertNotEqual(self.M.RETAINED_SQLITE['sha256'],self.specs['sqlite-wal']['sha256'])
 def test_stale_or_foreign_run_and_boolean_provider_identity_refuse(self):
  original=deepcopy(self.wire)
  for key,value in [('id',40365945429),('head_sha','a32f0e187cea7aa11c0fac2977d31f31315093da'),('workflow_id',1),('run_attempt',True),('conclusion','failure'),('repository',{'id':1}),('head_repository',{'id':1})]:
   with self.subTest(field=key):
    self.wire=deepcopy(original);self.wire[self.run_key][key]=value;self.calls=[];self.source_fds=[];self.refuse('release_lineage')
 def test_active_artifact_missing_expired_foreign_substituted_metadata_refuses(self):
  original=deepcopy(self.wire)
  for key,value in [('id',True),('size_in_bytes',True),('name','sqlite-wal-package-old'),('digest','sha256:'+'0'*64),('expired',True),('expires_at','2000-01-01T00:00:00Z'),('url','https://example.invalid/artifact'),('archive_download_url','https://example.invalid/zip')]:
   with self.subTest(field=key):
    self.wire=deepcopy(original);self.wire[self.artifact_key][key]=value;self.calls=[];self.source_fds=[];self.refuse('release_sqlite_artifact')
  for key,value in [('id',True),('repository_id',1),('head_repository_id',1),('head_branch','other'),('head_sha','0'*40)]:
   with self.subTest(workflow_field=key):
    self.wire=deepcopy(original);self.wire[self.artifact_key]['workflow_run'][key]=value;self.calls=[];self.source_fds=[];self.refuse('release_sqlite_artifact')
  for value in([],{},None):
   with self.subTest(malformed=value):
    self.wire=deepcopy(original);self.wire[self.artifact_key]=value;self.calls=[];self.source_fds=[];self.refuse('release_sqlite_artifact')
 def test_original_six_asset_catalog_is_not_replaced_by_current_sqlite_or_waived(self):
  original=deepcopy(self.wire)
  for kind in('missing','extra','duplicate','new_sqlite','target'):
   with self.subTest(kind=kind):
    self.wire=deepcopy(original);release=self.wire[self.release_key]
    if kind=='missing':release['assets'].pop()
    if kind=='extra':release['assets'].append({'id':1,'name':'unexpected'})
    if kind=='duplicate':release['assets'][-1]=deepcopy(release['assets'][0])
    if kind=='new_sqlite':next(a for a in release['assets']if a['id']==self.M.ASSET_IDS['sqlite-wal'])['digest']='sha256:'+self.specs['sqlite-wal']['sha256']
    if kind=='target':release['target_commitish']='main'
    self.calls=[];self.source_fds=[];self.refuse('release_assets'if kind in('missing','extra','duplicate')else'release_identity'if kind=='target'else'release_asset_identity')
 def test_changed_locked_sources_fail_before_any_provider_or_binary(self):
  path=self.root/'scripts/sqlite_wal_package.lock.json';before=path.read_bytes()
  value=json.loads(before);value['source']['root_build_receipt_sha256']='0'*64;path.write_bytes(encoded(value))
  self.refuse('lock_contract')
 def test_otherwise_valid_current_source_must_match_explicit_Root_artifact_acceptance(self):
  with patch.object(self.M,'SQLITE_ROOT_RECEIPT','0'*64):self.refuse('release_sqlite_source')
 def test_all_active_ci_callers_already_have_actions_read_and_step_scoped_token(self):
  import yaml
  for name,job in [('npm-security-backports.yml','browsers'),('merge-gate.yml','frontend-browser-matrix'),('pre-release-gate.yml','frontend-browser-matrix')]:
   workflow=yaml.safe_load((ROOT/'.github/workflows'/name).read_bytes());candidate=workflow['jobs'][job]
   self.assertEqual(candidate['permissions']['actions'],'read');self.assertEqual(candidate['permissions']['contents'],'read');self.assertNotIn('GH_TOKEN',candidate.get('env',{}))
   prefetch=[s for s in candidate['steps']if 'prepare_ci_security_caches.py'in s.get('run','')or 'bootstrap_ci_backend.sh'in s.get('run','')]
   self.assertTrue(prefetch);self.assertTrue(all(s['env']['GH_TOKEN']=='${{ github.token }}'for s in prefetch))
  for name in('market-capture2-image.yml','canonical-app-image.yml'):
   value=yaml.safe_load((ROOT/'.github/workflows'/name).read_bytes());self.assertEqual(value['permissions'],{'contents':'read','actions':'read'})
 def test_context_guard_and_normal_caller_still_precede_cache_or_image_effects(self):
  with patch.object(self.M.os,'getuid',return_value=0),patch.object(self.M.runpy,'run_path',side_effect=AssertionError('Source loaded before hosted guard')):
   with self.assertRaisesRegex(RuntimeError,'^release_context_hosted$'):self.M.main(self.root)
  tree=ast.parse((ROOT/'scripts/prepare_market_capture2_image.py').read_bytes())
  fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef)and n.name=='prepare_checked_caches')
  text=ast.unparse(fn);self.assertIn("'--context-root'",text);self.assertIn("'-S'",text)
  self.assertIn('release.check()',text);self.assertIn("environment(root, token=True)",text)
  self.assertEqual(text.count("'prepare_sqlite_wal_os_cache.py'"),2)

if __name__=='__main__':unittest.main(verbosity=2)
