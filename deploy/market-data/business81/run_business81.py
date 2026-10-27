"""UNARMED sealed unittest capsule for the exact normal Market candidate.

The outer producer MUST attest the actual immutable image and exact readonly
public evidence mount. No pytest or application test files are installed into
the final image. This program alone grants no release or activation authority.
"""
from contextlib import ExitStack,redirect_stdout
from hashlib import sha1,sha256
from pathlib import Path
from types import ModuleType
import argparse,io,json,os,re,signal,stat,sys,threading,unittest


# Closed published source expectations, never runtime-identity substitutes.
from hashlib import sha256 as _role_sha256
ROLE_CATALOG_PATH = 'deploy/market-data/role-business-components.json'
ROLE_CATALOG_SHA = '33c092cafd2cd301bd1f7b92a73aeca7c685ea189ddc44e8f98bd8d7c09c2831'
ROLE_MAP = {'native/coin-estimator': {'business_commit': 'f4871edbe507546a0a6544c56bc7d6b34bf770a6', 'business_tree': '9b049e5031a33f416b6c6a2a45d89c3ba334fc3e', 'bytes': 1928470, 'component': 'market-role-sources-20261027-fc622107fadf1de9-v1', 'count': 94, 'manifest_sha256': '6ddbb7459a9b69c20accb2e277c8e25c701b95958b8219cfd38a08e998f1b1ca', 'rows_sha256': 'fc622107fadf1de9adec789425c1d3816271829e1310b75347a524c5c104bbcd'}, 'native/estimator-snapshot-sender': {'business_commit': '6c7bfb3fe3714f1b9ecb375e68bbb0b1263ef328', 'business_tree': 'bc90e1224cda5bd2c58be81ea2aa12d1c6fbcadd', 'bytes': 1848102, 'component': 'market-role-sources-20261027-971dc9e57b1a4220-v1', 'count': 93, 'manifest_sha256': '3ce2f0f0b6ed975c2665f68ca0dc53198b9c00c9673fde3979c094c4ae05ec5c', 'rows_sha256': '971dc9e57b1a4220b3604af0ed101377a62ae7b9f6510340b0cc9c6b9a3cb33f'}, 'native/market-fact-receiver': {'business_commit': 'c9f8ada468c94af99a30ede8cdcbd9a220e9b917', 'business_tree': '08d9d82693f8a42a49da89067c4def1862ce1e83', 'bytes': 1934716, 'component': 'market-role-sources-20261027-83c61098e5581bcb-v1', 'count': 95, 'manifest_sha256': '384995da7738128ca902737e7324cd8fc1abff5a581047e41533b06c14683f16', 'rows_sha256': '83c61098e5581bcb9795859444df9730888c46cac911046f293a8056ef3bc3c1'}, 'native/market-store-adapter': {'business_commit': '6cad2c7e4a4b4622ec3610ddb91cce32cd04f2c0', 'business_tree': 'c3a25a925927802f413dc1eda59ca87b3af3e6b7', 'bytes': 1937533, 'component': 'market-role-sources-20261027-0c440e791bde7779-v1', 'count': 95, 'manifest_sha256': '88a982cf6c6da9f736fa11708be694a444ee7d6b3ffcfaea81b43e1c6c52f133', 'rows_sha256': '0c440e791bde77790179ef65b30b9f5abda5434287c09bf87648b61f19011157'}, 'web/estimator-snapshot-receiver': {'business_commit': '89b64c47c952c6f6a6ba75df2b2f40d0208de196', 'business_tree': '9ba0c52b9a9d3a1cbdb01d0417eaa9891b2585e2', 'bytes': 1934784, 'component': 'market-role-sources-20261027-45ccb288a62edc6a-v1', 'count': 95, 'manifest_sha256': 'bacda97ab7e51af550207a9930977be8e12d37d28ed5354d60cc3c078decedc9', 'rows_sha256': '45ccb288a62edc6a5f08042ca33060ce81ca45e34a2a2bf4e18b55f2de73a884'}, 'web/market-capture-account1': {'business_commit': '4ee3fd21003ebb3038a2e4b572ca2eb01cb45d2f', 'business_tree': 'c63f575f2a67287076a2188a72494ee236b2109c', 'bytes': 1896295, 'component': 'market-role-sources-20261027-12cac77ad8481fa6-v1', 'count': 93, 'manifest_sha256': '1693511310372cb9e8906141bfcb0818d19cd32ed4ca5066acf61d8719013394', 'rows_sha256': '12cac77ad8481fa6d11f7885c97ab4e8593290ed70cf388a7b15e86c4139dbad'}, 'web/market-capture-external': {'business_commit': '6c7bfb3fe3714f1b9ecb375e68bbb0b1263ef328', 'business_tree': 'bc90e1224cda5bd2c58be81ea2aa12d1c6fbcadd', 'bytes': 1848102, 'component': 'market-role-sources-20261027-971dc9e57b1a4220-v1', 'count': 93, 'manifest_sha256': '3ce2f0f0b6ed975c2665f68ca0dc53198b9c00c9673fde3979c094c4ae05ec5c', 'rows_sha256': '971dc9e57b1a4220b3604af0ed101377a62ae7b9f6510340b0cc9c6b9a3cb33f'}, 'web/market-fact-sync-worker': {'business_commit': '064451c17c9f2f1b72f3f33fbe68f15fc1384f60', 'business_tree': '7f7f209b17b06984b2ac2fa989177277ed876b24', 'bytes': 1935251, 'component': 'market-role-sources-20261027-4a848dcf6a46d6f5-v1', 'count': 95, 'manifest_sha256': '83a42c0507c28c40574538c61382034a6f376001cf5cb76a1aed97148416de36', 'rows_sha256': '4a848dcf6a46d6f5a0f0e2784f537f6deb2cd6722fcb6ae580a12dfdc7cbf53d'}, 'web/market-processor': {'business_commit': 'db0f8eba339b830ab7ae5fb1c810f149992458b3', 'business_tree': 'e956bcc0ae619fd7495bb4ba41d8a1aeaa7885c1', 'bytes': 1946368, 'component': 'market-role-sources-20261027-5cc7196abb5cfafe-v1', 'count': 95, 'manifest_sha256': 'a5b587cc382563735bb66df783e8def505d12509a9133a08a2537e562cbaf2c9', 'rows_sha256': '5cc7196abb5cfafeb5ca00aab2d34579c94d493c933768253bfba7a34d995c30'}}

def _role_encoded(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode()

def role_data(raw, role):
    need(type(role) is str and role in ROLE_MAP, 'role_component')
    need(type(raw) is bytes and len(raw) == 179771 and
         _role_sha256(raw).hexdigest() == ROLE_CATALOG_SHA, 'role_catalog')
    value = json.loads(raw)
    spec = ROLE_MAP[role]
    manifest = value['components'][spec['rows_sha256']]
    need(value['schema'] == 'normal_market_published_role_components/1' and
         _role_sha256(_role_encoded(manifest)).hexdigest() == spec['manifest_sha256'] and
         role in manifest['roles'] and manifest['business_commit'] == spec['business_commit'] and
         manifest['business_tree'] == spec['business_tree'] and
         manifest['source_files'] == spec['count'] and manifest['source_bytes'] == spec['bytes'] and
         _role_sha256(_role_encoded(manifest['product_files'])).hexdigest() == spec['rows_sha256'] and
         all(manifest[key] is False for key in ('already_running_code_proven',
             'complete_effective_business_inventory_proven', 'full_outer_business_proven',
             'activation_authority', 'security_accepted')), 'role_manifest')
    return manifest

def receipt_role(receipt):
    need(type(receipt) is dict and receipt.get('schema') == 'normal_market_git_component_inputs/2'
         and type(receipt.get('role')) is str and receipt['role'] in ROLE_MAP, 'role_receipt')
    spec = ROLE_MAP[receipt['role']]
    locked = {'business_commit': spec['business_commit'], 'business_tree': spec['business_tree'],
              'repository_identity': {'host': 'github.com', 'repository': 'mohsenbarari/trading_bot'},
              'role': receipt['role'], 'catalog_sha256': ROLE_CATALOG_SHA,
              'source_policy': 'whole_git_component_no_main_fallback'}
    need(receipt.get('role_source') == spec and receipt.get('role_catalog_sha256') == ROLE_CATALOG_SHA and
         receipt.get('repository_identity') == locked['repository_identity'] and
         receipt.get('component') == spec['component'] and
         receipt.get('business_commit') == spec['business_commit'] and
         receipt.get('business_tree') == spec['business_tree'] and
         receipt.get('business_manifest_sha256') == spec['manifest_sha256'] and
         receipt.get('product_lock_sha256') == _role_sha256(_role_encoded(locked)).hexdigest() and
         type(receipt.get('product_files')) is list and len(receipt['product_files']) == spec['count'] and
         _role_sha256(_role_encoded(receipt['product_files'])).hexdigest() == spec['rows_sha256'],
         'role_receipt')
    return dict(spec)

def candidate_spec(value):
    need(type(value) is dict and type(value.get('role')) is str and value['role'] in ROLE_MAP,
         'role_candidate')
    role = value['role']
    need(value == {'role': role, 'role_source': ROLE_MAP[role],
                   'catalog_sha256': ROLE_CATALOG_SHA}, 'role_candidate')
    return dict(ROLE_MAP[role])


SELECTION_SHA='7a35410166254bb9e6f1d354fb9283550d7814bdc8455a2b070c633edbded833'
BUSINESS_SHA='fef6305b1d7e6db851d1d0b77f3aa9910aad535e83f64f012790165a3d62c81b'
BUSINESS_COMMIT='126d6b97eecdc7af4bd6415ae12079c84ca4f0c8'
BUSINESS_TREE='08606163f552f01e4ca64a2250f360a71edadd78'
PROFILES={'base-nonroot':('base',10001,79),'base-root':('base',0,1),'added-replay':('replay',10001,1)}
MODULES=('test_market_pipeline_stage3_foundation','test_market_pipeline_stage4_capture',
 'test_market_pipeline_stage5_coin_processor','test_market_pipeline_stage6_channel_processor','test_market_private_pipeline_contracts')


def need(value,code):
 if not value:raise RuntimeError(code)
def identity(s):return(s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
def encoded(value):return(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode()


class Held:
 def __init__(self):self.rows=[]
 def read(self,path,maximum):
  path=Path(path);parents=[]
  for parent in path.parents:
   frame=identity(parent.lstat());need(parent.resolve(strict=True)==parent and stat.S_ISDIR(frame[2])and not frame[2]&0o022,'parent')
   parents.append((parent,frame[:5]))
  before=identity(path.lstat());need(path.resolve(strict=True)==path and stat.S_ISREG(before[2])and before[5]==1
   and not before[2]&0o022 and 0<=before[6]<=maximum,'file_metadata')
  fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC);self.rows.append((path,fd,before,parents));need(identity(os.fstat(fd))==before,'file_open')
  raw=os.pread(fd,before[6]+1,0);need(len(raw)==before[6],'file_bound');self.check();return raw
 def check(self):
  for path,fd,before,parents in self.rows:
   need(identity(os.fstat(fd))==identity(path.lstat())==before and all(p.resolve(strict=True)==p and identity(p.lstat())[:5]==v for p,v in parents),'file_changed')
 def close(self):
  error=None
  for path,fd,before,parents in reversed(self.rows):
   try:need(identity(os.fstat(fd))==identity(path.lstat())==before,'file_changed')
   except BaseException as e:
    if error is None:error=e
   try:os.close(fd)
   except BaseException as e:
    if error is None:error=e
  for path,fd,before,parents in self.rows:
   try:need(identity(path.lstat())==before and all(p.resolve(strict=True)==p and identity(p.lstat())[:5]==v for p,v in parents),'file_closed_changed')
   except BaseException as e:
    if error is None:error=e
  if error is not None:raise error


def cases(suite):
 for item in suite:
  if isinstance(item,unittest.TestSuite):yield from cases(item)
  else:yield item


class FixtureStdout(io.TextIOBase):
 """Discard public fixture prints without polluting the one-result protocol."""
 def __init__(self):self.bytes=0
 def writable(self):return True
 def write(self,value):
  need(type(value)is str,'fixture_stdout_type');size=len(value.encode('utf-8'))
  need(size<=131072-self.bytes,'fixture_stdout_bound');self.bytes+=size;return len(value)
 def flush(self):pass


def inventory(root,expected):
 """Only declared, readonly public fixture leaves belong in this mount."""
 files=set();dirs=set();count=0
 expected_dirs={str(p)for name in expected for p in Path(name).parents if str(p)!='.'}
 def walk(path):
  nonlocal count
  row=path.lstat();need(path.resolve(strict=True)==path and stat.S_ISDIR(row.st_mode)and not row.st_mode&0o022,'evidence_directory')
  with os.scandir(path)as entries:
   for entry in entries:
    count+=1;need(count<=256 and not entry.is_symlink(),'evidence_inventory')
    child=Path(entry.path);name=child.relative_to(root).as_posix()
    if entry.is_dir(follow_symlinks=False):dirs.add(name);walk(child)
    else:need(entry.is_file(follow_symlinks=False),'evidence_inventory');files.add(name)
 walk(root);need(files==set(expected)and dirs==expected_dirs,'evidence_inventory')


def execute(args):
 fixture,uid,count=PROFILES[args.profile]
 need(os.getuid()==os.geteuid()==os.getgid()==os.getegid()==uid and sys.flags.isolated==sys.flags.no_site==sys.flags.dont_write_bytecode==1 and sys.flags.optimize==0,'actual_identity')
 need(re.fullmatch('sha256:[a-f0-9]{64}',args.image_id)and re.fullmatch('[a-f0-9]{64}',args.manifest_sha256),'scope')
 evidence=Path('/evidence');held=Held();value=None
 try:
  manifest_raw=held.read(evidence/'suite-manifest.json',4*1024**2);need(sha256(manifest_raw).hexdigest()==args.manifest_sha256,'manifest_pin');manifest=json.loads(manifest_raw)
  role_candidate=manifest.get('schema')=='normal_market_business81_sources/3'
  keys={'schema','image_id','release_commit','two_git_input_signature','business_commit','business_tree','selection_sha256','business_manifest_sha256','files','runner_sha256','release_recipe_path','security_assertion_update','security_accepted'}
  if role_candidate:keys=keys-{'business_commit','business_tree','business_manifest_sha256'}|{'candidate_product','fixture_business_provenance'}
  fixture_provenance=manifest['fixture_business_provenance']if role_candidate else{k:manifest[k]for k in('business_commit','business_tree','business_manifest_sha256')}
  need(set(fixture_provenance)=={'business_commit','business_tree','business_manifest_sha256'},'manifest')
  spec=candidate_spec(manifest['candidate_product'])if role_candidate else None
  need(set(manifest)==keys
   and manifest['release_recipe_path']=='deploy/market-data/Dockerfile.capture2'and manifest['schema']==('normal_market_business81_sources/3'if role_candidate else'normal_market_business81_sources/2')and manifest['image_id']==args.image_id
   and re.fullmatch('[a-f0-9]{40}',manifest['release_commit'])and re.fullmatch('[a-f0-9]{64}',manifest['two_git_input_signature'])
   and fixture_provenance['business_commit']==BUSINESS_COMMIT and fixture_provenance['business_tree']==BUSINESS_TREE
   and manifest['selection_sha256']==SELECTION_SHA and fixture_provenance['business_manifest_sha256']==BUSINESS_SHA and manifest['security_accepted']is False,'manifest')
  need(sha256(held.read(evidence/'run_business81.py',131072)).hexdigest()==manifest['runner_sha256'],'runner_pin')
  selection_raw=held.read(evidence/'f79-business-test-selection.json',32768);business_raw=held.read(evidence/'accepted-f79-business-manifest.json',65536)
  need(sha256(selection_raw).hexdigest()==SELECTION_SHA and sha256(business_raw).hexdigest()==BUSINESS_SHA,'baseline_pin')
  selection=json.loads(selection_raw);business=json.loads(business_raw);rows=manifest['files'];need(type(rows)is list and len(rows)==34,'fixture_inventory')
  by_path={r['path']:r for r in rows};need(len(by_path)==34 and all(type(k)is str and k.startswith(('base/','replay/'))and '..'not in Path(k).parts for k in by_path),'fixture_inventory')
  expected_paths={prefix+'/'+r['path']for prefix in('base','replay')for r in selection['historical_fixture_files']}|{prefix+'/deploy/market-data/Dockerfile'for prefix in('base','replay')}
  need(set(by_path)==expected_paths,'fixture_inventory')
  declared=expected_paths|{'run_business81.py','test_guard_source.py','product_check_source.py','f79-business-test-selection.json','accepted-f79-business-manifest.json','suite-manifest.json'}
  if role_candidate:declared.add('role-business-components.json')
  inventory(evidence,declared);sources={}
  for name,row in by_path.items():
   raw=held.read(evidence/name,4*1024**2);need(type(row)is dict and set(row)=={'path','bytes','sha256','original_bytes','original_sha256','git_blob','source_commit','transformation'}
    and len(raw)==row['bytes']and sha256(raw).hexdigest()==row['sha256'],'fixture_pin')
   if name.endswith('deploy/market-data/Dockerfile'):
    need(row['source_commit']==manifest['release_commit']and row['transformation']=='release_build_recipe'
     and row['original_bytes']==len(raw)and row['original_sha256']==sha256(raw).hexdigest()
     and row['git_blob']==sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest(),'release_recipe')
   else:
    original=raw
    if row['transformation']=='reviewed_security_base_assertion_only':
     update=manifest['security_assertion_update'];need(name.endswith('tests/test_market_pipeline_stage3_foundation.py')and type(update)is dict
      and set(update)=={'path','old_line','new_line','approved_source_delta_sha256'}
      and update['path']=='tests/test_market_pipeline_stage3_foundation.py'and update['approved_source_delta_sha256']==row['sha256']
      and update['old_line']==OLD_ASSERTION and update['new_line']==NEW_ASSERTION,'security_assertion')
     old=update['old_line'].encode();new=update['new_line'].encode();need(raw.count(new)==1,'security_assertion');original=raw.replace(new,old,1)
    else:need(row['transformation']=='none','fixture_transform')
    need((len(original),sha256(original).hexdigest(),sha1(b'blob '+str(len(original)).encode()+b'\0'+original).hexdigest())
     ==(row['original_bytes'],row['original_sha256'],row['git_blob']),'original_fixture_pin')
    path=name.split('/',1)[1]
    expected=selection['capture_added_replay_test_source']if name=='replay/tests/test_market_pipeline_stage4_capture.py'else next(r for r in selection['historical_fixture_files']if r['path']==path)
    need(row['source_commit']==expected['source_commit']and row['original_sha256']==expected['sha256']and row['git_blob']==expected['git_blob'],'fixture_origin')
   if name.startswith(fixture+'/')and name.endswith('.py'):sources[name.split('/',1)[1]]=raw
  root=evidence/fixture
  # These two exact, separately sealed helpers are reused from the previous
  # ordinary Market test capsule. They perform no imports of business code.
  guard_raw=held.read(evidence/'test_guard_source.py',32768);product_raw=held.read(evidence/'product_check_source.py',32768)
  need(sha256(guard_raw).hexdigest()==GUARD_SHA and sha256(product_raw).hexdigest()==PRODUCT_CHECK_SHA,'helper_pin')
  namespace={};exec(compile(guard_raw,'<sealed-market-test-fence>','exec'),namespace)
  exec(compile(product_raw,'<sealed-product-file-check>','exec'),namespace)
  expected_product=business['product_files']
  if role_candidate:
   catalog_raw=held.read(evidence/'role-business-components.json',1048576)
   expected_product=role_data(catalog_raw,manifest['candidate_product']['role'])['product_files']
  need(namespace['verify_product_files'](Path('/app'),expected_product,held.read),'actual_candidate_source')
  sys.path[:0]=['/app','/opt/market-python',str(root)]
  discarded=FixtureStdout()
  with ExitStack()as fence,redirect_stdout(discarded):
   namespace['apply_test_fence'](fence)
   scripts=ModuleType('scripts');scripts.__path__=[str(root/'scripts')];sys.modules['scripts']=scripts
   for name in ('manage_market_pipeline_stage3','audit_production_market_catchup','export_market_private_pipeline_schemas'):
    key='scripts.'+name;module=ModuleType(key);module.__file__=str(root/'scripts'/(name+'.py'));sys.modules[key]=module
    exec(compile(sources['scripts/'+name+'.py'],module.__file__,'exec'),module.__dict__);setattr(scripts,name,module)
   package=ModuleType('tests');package.__path__=[str(root/'tests')];package.__file__=str(root/'tests/__init__.py');sys.modules['tests']=package
   exec(compile(sources['tests/__init__.py'],package.__file__,'exec'),package.__dict__)
   suite=unittest.TestSuite()
   for name in MODULES:
    module=ModuleType('tests.'+name);module.__file__=str(root/'tests'/(name+'.py'));sys.modules[module.__name__]=module
    exec(compile(sources['tests/'+name+'.py'],module.__file__,'exec'),module.__dict__);suite.addTests(unittest.defaultTestLoader.loadTestsFromModule(module))
   all_tests=list(cases(suite));all_ids=sorted(t.id()for t in all_tests)
   original=selection['historical_test_ids'];added=selection['capture_added_replay_test_ids'];root_id=selection['root_fixture_test_id']
   need(all_ids==sorted(original+(added if fixture=='replay'else[])),'collected_test_ids')
   expected=added if args.profile=='added-replay'else[root_id]if uid==0 else[i for i in original if i!=root_id]
   selected=[t for t in all_tests if t.id()in expected];need(len(selected)==count and sorted(t.id()for t in selected)==sorted(expected),'selected_test_ids')
   results=[]
   def run():results.append(unittest.TextTestRunner(verbosity=2,stream=sys.stderr).run(unittest.TestSuite(selected)))
   worker=threading.Thread(target=run,name='normal-market-original-tests',daemon=True);worker.start();worker.join(180)
   need(not worker.is_alive()and len(results)==1,'business_test_deadline');result=results[0]
   ok=result.wasSuccessful()and result.testsRun==count and not(result.skipped or result.expectedFailures or result.unexpectedSuccesses)
   need(namespace['verify_product_files'](Path('/app'),expected_product,held.read),'actual_candidate_source_final')
  inventory(evidence,declared);held.check()
  value={'schema':'normal_market_business81_profile_result/1','image_id':args.image_id,'release_commit':manifest['release_commit'],
   'business_commit':BUSINESS_COMMIT,'business_tree':BUSINESS_TREE,'two_git_input_signature':manifest['two_git_input_signature'],
   'test_sources_sha256':args.manifest_sha256,'profile':args.profile,'execution_uid':os.getuid(),'execution_gid':os.getgid(),
   'discarded_fixture_stdout_bytes':discarded.bytes,
   'selected_test_ids':sorted(expected),'tests':result.testsRun,'skips':len(result.skipped),'expected_failures':len(result.expectedFailures),
   'unexpected_successes':len(result.unexpectedSuccesses),'failures':len(result.failures),'errors':len(result.errors),
   'status':'passed'if ok else'failed','actual_candidate_source93_verified':True,'runtime_data_used':False,
   'business_data_freshness_proven':False,'security_accepted':False}
  if role_candidate:
   value.update(schema='normal_market_business81_profile_result/2',business_commit=spec['business_commit'],business_tree=spec['business_tree'],
    actual_candidate_source_verified=value.pop('actual_candidate_source93_verified'),candidate_source_files=spec['count'],
    candidate_manifest_sha256=spec['manifest_sha256'],fixture_business_provenance=fixture_provenance)
 finally:held.close()
 return value


GUARD_SHA='eddfce484567bccb6cfe40f4587f89aad9d700104ca99fb09f0f6ebe5eca4340'
PRODUCT_CHECK_SHA='e022ec3d0847808167f2401fd5208f04506fa723f0433312ab6606f31a88a007'
OLD_ASSERTION='        self.assertIn("python:3.11-slim-bookworm@sha256:", dockerfile)\n'
NEW_ASSERTION='        self.assertIn("python:3.11.18-slim-trixie@sha256:0451b9eeed3e5a9523d8958cc33262eb5843c7af3114c50f45d99f56e2b883ac", dockerfile)\n'


def main(argv=None):
 parser=argparse.ArgumentParser();parser.add_argument('--profile',choices=tuple(PROFILES));parser.add_argument('--image-id');parser.add_argument('--manifest-sha256');args=parser.parse_args(argv)
 if args.profile is None:print(json.dumps({'status':'UNARMED','test_execution_performed':False}));return 0
 signal.signal(signal.SIGALRM,lambda *_:(_ for _ in ()).throw(RuntimeError('business_test_deadline')));signal.alarm(200)
 try:value=execute(args)
 finally:signal.alarm(0)
 raw=encoded(value);need(len(raw)<=32768,'result_bound');sys.stdout.buffer.write(raw);return 0 if value['status']=='passed'else 1


if __name__=='__main__':raise SystemExit(main())
