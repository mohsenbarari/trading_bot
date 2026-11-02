"""Synthetic public provider/transport wiring; no network, installer or CI truth."""
from contextlib import redirect_stdout
from copy import deepcopy
from hashlib import sha256
import io,json,os,runpy,tempfile,unittest
from pathlib import Path
from types import ModuleType,SimpleNamespace
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
def load(path):
 m=ModuleType('owned_'+path.stem);m.__file__=str(path);exec(compile(path.read_bytes(),str(path),'exec'),m.__dict__);return m
M=load(ROOT/'scripts/prepare_ci_security_caches.py')
def encoded(v):return(json.dumps(v,sort_keys=True,separators=(',',':'))+'\n').encode()
def need(ok,code):
 if not ok:raise RuntimeError(code)
def metadata():
 p={'artifact':{'id':101,'run_id':202,'run_attempt':1,'job_id':303,'head_sha':'a'*40,'workflow_id':404,'name':'sqlite3541-library-trixie-202-1','bytes':20,'sha256':'b'*64}}
 s=p['artifact'];base='https://api.github.com/repos/'+M.REPO+'/actions/artifacts/101'
 values={'actions/runs/202/attempts/1':{'id':202,'run_attempt':1,'workflow_id':404,'path':'.github/workflows/sqlite3541-library.yml','head_sha':'a'*40,'event':'workflow_dispatch','repository':{'id':1065680631},'head_repository':{'id':1065680631},'status':'completed','conclusion':'failure'},
 'actions/jobs/303':{'id':303,'run_id':202,'run_attempt':1,'name':'package (trixie)','head_sha':'a'*40,'status':'completed','conclusion':'success'},
 'actions/artifacts/101':{'id':101,'size_in_bytes':20,'name':s['name'],'digest':'sha256:'+s['sha256'],'expired':False,'expires_at':'2099-01-01T00:00:00Z','url':base,'archive_download_url':base+'/zip','workflow_run':{'id':202,'repository_id':1065680631,'head_repository_id':1065680631,'head_branch':'main','head_sha':'a'*40}}}
 return p,values

class MetadataTests(unittest.TestCase):
 def validate(self,profile,wire):
  calls=[]
  def api(key,maximum):calls.append(key);self.assertEqual(maximum,65536);return encoded(wire[key])
  result=M.validate_canonical_artifact(api,json.loads,need,profile);return result,calls
 def test_successful_exact_profile_job_does_not_require_unrelated_matrix_success(self):
  p,w=metadata();result,calls=self.validate(p,w);self.assertEqual(result,w['actions/artifacts/101']);self.assertEqual(calls,['actions/runs/202/attempts/1','actions/jobs/303','actions/artifacts/101'])
  w['actions/runs/202/attempts/1']['status']='in_progress';self.validate(p,w)
 def test_bad_run_or_job_refuses_before_artifact_read(self):
  p,original=metadata()
  for key,field,value in [('actions/runs/202/attempts/1','head_sha','b'*40),('actions/runs/202/attempts/1','repository',{'id':1}),('actions/runs/202/attempts/1','run_attempt',True),('actions/runs/202/attempts/1','status','queued'),('actions/jobs/303','conclusion','failure'),('actions/jobs/303','name','package (noble)'),('actions/jobs/303','run_id',1),('actions/jobs/303','run_attempt',True),('actions/jobs/303','head_sha','b'*40)]:
   w=deepcopy(original);w[key][field]=value;calls=[]
   def api(name,maximum):calls.append(name);return encoded(w[name])
   with self.subTest(key=key,field=field),self.assertRaisesRegex(RuntimeError,'^release_lineage$'):M.validate_canonical_artifact(api,json.loads,need,p)
   self.assertNotIn('actions/artifacts/101',calls)
 def test_artifact_pin_expiry_boolean_shape_and_repository_cannot_be_substituted(self):
  p,original=metadata()
  changes=[('id',True),('size_in_bytes',True),('digest','sha256:'+'0'*64),('name','other'),('expired',True),('expired',0),('expires_at','2000-01-01T00:00:00Z'),('expires_at','NOT-ISO-Z'),('url','https://example.invalid'),('workflow_run',{'id':202,'repository_id':1,'head_repository_id':1065680631,'head_branch':'main','head_sha':'a'*40})]
  for field,value in changes:
   w=deepcopy(original);w['actions/artifacts/101'][field]=value
   with self.subTest(field=field,value=value),self.assertRaisesRegex(RuntimeError,'^release_sqlite_artifact$'):self.validate(p,w)
  for bad in (None,[],True):
   w=deepcopy(original);w['actions/artifacts/101']=bad
   with self.subTest(shape=bad),self.assertRaisesRegex(RuntimeError,'^release_sqlite_artifact$'):self.validate(p,w)

class WiringTests(unittest.TestCase):
 def wire_main(self,root,canonical=True,break_retained=False):
  p,wire=metadata();payloads={};specs={};counter=1000;calls=[];prepares=[]
  for group in M.ASSET_IDS:
   if group=='sqlite-wal':continue
   raw=('PUBLIC SYNTHETIC WIRING '+group).encode();specs[group]={'id':counter,'run_id':counter+100,'bytes':len(raw),'sha256':sha256(raw).hexdigest()};counter+=1
   payloads['releases/assets/'+str(M.ASSET_IDS[group])]=raw
   workflow,name,head=M.LINEAGE[group]
   wire['actions/runs/'+str(specs[group]['run_id'])+'/attempts/1']={'id':specs[group]['run_id'],'run_attempt':1,'workflow_id':workflow,'path':'.github/workflows/'+name,'head_sha':head,'event':'workflow_dispatch','status':'completed','conclusion':'success','repository':{'id':1065680631},'head_repository':{'id':1065680631}}
  raw=b'PUBLIC SYNTHETIC NEW SQLITE ZIP';p['artifact'].update(bytes=len(raw),sha256=sha256(raw).hexdigest());wire['actions/artifacts/101'].update(size_in_bytes=len(raw),digest='sha256:'+sha256(raw).hexdigest());payloads['actions/artifacts/101/zip']=raw
  assets=[]
  for group,identifier in M.ASSET_IDS.items():
   s=M.RETAINED_SQLITE if group=='sqlite-wal'else specs[group]
   assets.append({'id':identifier,'name':'app-os-'+group+'.zip','size':s['bytes'],'digest':'sha256:'+s['sha256'],'state':'uploaded','url':'https://api.github.com/repos/'+M.REPO+'/releases/assets/'+str(identifier)})
  if break_retained:next(a for a in assets if a['id']==M.ASSET_IDS['sqlite-wal'])['digest']='sha256:'+'0'*64
  wire['releases/'+str(M.RELEASE_ID)]={'id':M.RELEASE_ID,'tag_name':M.RELEASE_TAG,'target_commitish':M.RELEASE_TARGET,'draft':False,'prerelease':True,'html_url':'https://github.com/'+M.REPO+'/releases/tag/'+M.RELEASE_TAG,'assets':assets}
  def transport(argv,*,seconds,maximum,environment):
   key=argv[-1].split('repos/'+M.REPO+'/',1)[1];calls.append((key,maximum,argv[5]))
   expected='application/octet-stream'if key.startswith('releases/assets/')else'application/vnd.github+json'
   self.assertEqual(argv[5],'Accept: '+expected);self.assertEqual(environment,{'GH_TOKEN':'PUBLIC-SYNTHETIC-TOKEN'})
   value=encoded(wire[key])if key in wire else payloads[key];self.assertLessEqual(len(value),maximum);return value
  class Held:
   def __init__(self,*args):pass
   def __enter__(self):return self
   def __exit__(self,*_):pass
   def check(self):pass
  def write_new(path,raw,mode):path.write_bytes(raw);path.chmod(mode)
  def prepare_app(cache,paths=None,check_only=False):
   prepares.append(('security-os',cache));self.assertEqual(set(paths),{'util','ncurses','acl-tar'});return {'status':'cache_prepared','packages':15,'security_accepted':False}
  def prepare_extra(group,cache,archive=None,check_only=False):
   prepares.append((group,cache));expected=payloads['actions/artifacts/101/zip']if group=='sqlite3541'else payloads['releases/assets/'+str(M.ASSET_IDS[group])]
   self.assertEqual(archive.read_bytes(),expected);return {'status':'cache_prepared','security_accepted':False}
  def namespace(path):
   name=Path(path).name
   if name=='prepare_app_security_os_cache.py':return {'need':need,'document':json.loads,'Held':Held,'load_recipe':lambda held:SimpleNamespace(lock={'artifacts':{g:specs[g]for g in ('util','ncurses','acl-tar')}},module=SimpleNamespace(command=transport)),'write_new':write_new,'prepare_cache':prepare_app}
   group=next(g for g,n in M.EXTRA_HELPERS.items()if name=='prepare_'+n+'_os_cache.py')
   if group=='sqlite-wal':
    def canonical_lock(held):return (None,p)
    return {'Held':Held,'load_canonical':canonical_lock,'load_lock':lambda held:(_ for _ in()).throw(AssertionError('oldSQLite load forbidden')),'canonical_prepare':lambda cache,archive=None,check_only=False:prepare_extra('sqlite3541',cache,archive,check_only)}
   return {'Held':Held,'load_lock':lambda held:{'artifact':specs[group]},'prepare':lambda cache,archive=None,check_only=False:prepare_extra(group,cache,archive,check_only)}
  (root/'pip_packages').mkdir();(root/'runner').mkdir()
  with patch.object(M,'context_root',return_value=root)as context,patch.object(M.runpy,'run_path',side_effect=namespace),patch.dict(os.environ,{'GH_TOKEN':'PUBLIC-SYNTHETIC-TOKEN','RUNNER_TEMP':str(root/'runner')},clear=True),redirect_stdout(io.StringIO()):
   try:M.main(root,retain_normal_original_zips=True,canonical3541=canonical)
   finally:self.calls=calls;self.prepares=prepares
  self.assertEqual(context.call_args.kwargs,{'canonical3541':True})
  return root/'pip_packages/security-assessment-originals'
 def test_real_main_new_branch_skips_old_sqlite_but_keeps_six_catalog_and_retains_new_original(self):
  with tempfile.TemporaryDirectory(prefix='.canonical-ci-wiring-',dir=ROOT/'tests')as folder:
   root=Path(folder);originals=self.wire_main(root)
   self.assertEqual({p.name for p in originals.iterdir()},{'util.zip','ncurses.zip','acl-tar.zip','sqlite3541.zip','tar-acl.zip','coreutils-acl.zip'})
   self.assertFalse(any(key.startswith('releases/assets/'+str(M.ASSET_IDS['sqlite-wal']))for key,_,_ in self.calls))
   self.assertFalse(any('397322674'in key for key,_,_ in self.calls))
   self.assertEqual([group for group,_ in self.prepares],['security-os','sqlite3541','tar-acl','coreutils-acl'])
   self.assertEqual(self.prepares[1][1],root/'pip_packages/sqlite3541-capsule')
 def test_new_branch_still_refuses_changed_old_six_asset_catalog_before_binary(self):
  with tempfile.TemporaryDirectory(prefix='.canonical-ci-retained-',dir=ROOT/'tests')as folder:
   with self.assertRaisesRegex(RuntimeError,'^release_asset_identity$'):self.wire_main(Path(folder),break_retained=True)
   self.assertFalse(any(key.endswith('/zip')or key.startswith('releases/assets/')for key,_,_ in self.calls))
 def test_wrong_explicit_selector_type_refuses_before_context_or_provider(self):
  for bad in ('yes',1,None):
   with self.subTest(bad=bad),patch.object(M,'context_root',side_effect=AssertionError('context entered')),self.assertRaisesRegex(RuntimeError,'^release_sqlite_source$'):M.main(canonical3541=bad)

if __name__=='__main__':unittest.main()
