"""Published role metadata + labelled synthetic protocol/TAR tests; no real image claims."""
from pathlib import Path
from types import ModuleType,SimpleNamespace
from hashlib import sha256
from copy import deepcopy
from unittest.mock import patch
import ast,gzip,io,json,os,tarfile,tempfile,unittest
ROOT=Path(__file__).resolve().parents[1]
REPO=next(p for p in(ROOT,*ROOT.parents)if(p/'docs/memory/manifest.md').is_file())
def path(name):return ROOT/name if(ROOT/name).is_file()else REPO/name
def load(name,source):
 m=ModuleType(name);m.__file__=str(source);exec(compile(source.read_bytes(),str(source),'exec'),m.__dict__);return m
def literal(source,name):
 nodes=[n for n in ast.parse(source).body if isinstance(n,ast.Assign)and any(isinstance(t,ast.Name)and t.id==name for t in n.targets)];assert len(nodes)==1;return ast.literal_eval(nodes[0].value)
A=load('_new_catalog_assessor',path('scripts/assess_image_security_evidence.py'))
D=load('_new_parcel_driver',path('scripts/prepare_market_capture2_image.py'))
H=load('_owned_protocol_packets',REPO/'tests/test_assess_image_security_evidence.py')
CATALOG='deploy/market-data/role-business-components.json'
CONSUMERS=('scripts/prepare_market_product_source.py','scripts/prepare_market_capture2_image.py','scripts/market_business81.py','scripts/verify_market_role_product_source.py','deploy/market-data/business81/run_business81.py')
PUBLISHED=literal(path(CONSUMERS[0]).read_bytes(),'ROLE_MAP')
CATALOG_RAW=path(CATALOG).read_bytes();CATALOG_SHA=sha256(CATALOG_RAW).hexdigest()
def packet(role):
 # Crucially seed from the separate real publisher, never constants under test.
 # Build/image/completion headers remain explicitly OWNED SYNTHETIC fixtures.
 publisher=ModuleType('_publisher_role_fixture_authority');publisher.__dict__.update(A.__dict__);publisher.MARKET_ROLES=deepcopy(PUBLISHED);publisher.MARKET_CATALOG_SHA=CATALOG_SHA
 old=H.A
 try:H.A=publisher;p=H.Packet();H.install_market_product(p,role);return p
 finally:H.A=old
def rebind(p,change):
 b=json.loads(p.inputs[H.PRODUCT_NAMES['build']]);r=b['source_inputs'];change(b,r);sig=sha256(A.encoded(r)).hexdigest();p.scope['input_signature']=sig
 c=b['controller_inputs'];c['two_git_input_signature']=sig;cs=sha256(A.encoded(c)).hexdigest();b.update(two_git_input_signature=sig,controller_input_signature=cs)
 source=json.loads(p.inputs[H.PRODUCT_NAMES['source']]);source.update(inputs=r,input_signature=sig,controller=c,controller_signature=cs)
 manifest=json.loads(p.inputs[H.PRODUCT_NAMES['manifest']]);manifest.update(two_git_input_signature=sig,controller_input_signature=cs)
 for k,v in(('build',b),('source',source),('manifest',manifest)):p.inputs[H.PRODUCT_NAMES[k]]=A.encoded(v)
def profile(p):
 raw,_,_,inputs=p.buffers();return A.product_profile(A.document(raw),inputs)
class CatalogBudgetTests(unittest.TestCase):
 def test_all_six_independent_consumers_match_the_actual_published_nine_role_catalog(self):
  self.assertEqual(len(PUBLISHED),9);catalog=json.loads(CATALOG_RAW)
  for name in CONSUMERS:
   raw=path(name).read_bytes();self.assertEqual(literal(raw,'ROLE_MAP'),PUBLISHED,name);self.assertEqual(literal(raw,'ROLE_CATALOG_SHA'),CATALOG_SHA,name)
  self.assertEqual(A.MARKET_ROLES,PUBLISHED);self.assertEqual(A.MARKET_CATALOG_SHA,CATALOG_SHA)
  for role,spec in PUBLISHED.items():
   m=catalog['components'][spec['rows_sha256']];rows=m['product_files']
   with self.subTest(role=role):
    self.assertIn(role,m['roles']);self.assertEqual(sha256(A.encoded(rows)).hexdigest(),spec['rows_sha256']);self.assertEqual(sha256(A.encoded(m)).hexdigest(),spec['manifest_sha256']);self.assertEqual(len(rows),spec['count']);self.assertEqual(sum(r['bytes']for r in rows),spec['bytes']);self.assertEqual(m['business_commit'],spec['business_commit']);self.assertEqual(m['business_tree'],spec['business_tree'])
 def test_all_nine_genuine_published_role_specs_derive_product_profile_with_synthetic_headers(self):
  for role in PUBLISHED:
   with self.subTest(role=role):value=profile(packet(role));self.assertEqual(value['profile'],'normal_market81');self.assertEqual(value['role'],role)
 def test_stale_assessor_catalog_cannot_be_hidden_by_self_referential_fixture_authority(self):
  for role in PUBLISHED:
   with self.subTest(role=role),patch.object(A,'MARKET_CATALOG_SHA','0'*64),self.assertRaisesRegex(A.Refused,'product_market_role'):profile(packet(role))
 def test_rebound_catalog_and_role_substitutions_are_strictly_rejected_for_every_role(self):
  roles=list(PUBLISHED)
  for index,role in enumerate(roles):
   for kind in('catalog','role','spec'):
    p=packet(role)
    def change(b,r):
     if kind=='catalog':r['role_catalog_sha256']='0'*64
     elif kind=='role':b['role']=roles[(index+1)%len(roles)]
     else:r['role_source']=deepcopy(PUBLISHED[roles[(index+1)%len(roles)]])
    rebind(p,change)
    with self.subTest(role=role,kind=kind),self.assertRaisesRegex(A.Refused,'product_market_role'):profile(p)
 def test_actual_normal_build_rejects_sixth_join_before_sealer_cache_network_or_image_work(self):
  raw=path('scripts/assess_image_security_evidence.py').read_bytes();tree=ast.parse(raw);assignment=next(n for n in tree.body if isinstance(n,ast.Assign)and any(isinstance(t,ast.Name)and t.id=='MARKET_ROLES'for t in n.targets));lines=raw.splitlines(True);start=sum(map(len,lines[:assignment.value.lineno-1]))+assignment.value.col_offset;end=sum(map(len,lines[:assignment.value.end_lineno-1]))+assignment.value.end_col_offset
  changed=deepcopy(PUBLISHED);changed['native/coin-estimator']['business_commit']='0'*40
  variants=[raw.replace(CATALOG_SHA.encode(),b'33c092cafd2cd301bd1f7b92a73aeca7c685ea189ddc44e8f98bd8d7c09c2831'),raw[:start]+repr(changed).encode()+raw[end:]]
  for bad in variants+[raw]:
   with tempfile.TemporaryDirectory(dir=ROOT/'tests')as folder:
    with patch.object(D,'hosted'),patch.object(D,'git',return_value=b'origin/main\n'),patch.dict(D.os.environ,{'GITHUB_SHA':'b'*40}),patch.object(D,'controller_sources',return_value=([],{D.SECURITY_ASSESSOR:bad,D.SEALER:b'# OWNED INERT SEALER SOURCE\n'})),patch.object(D,'module',side_effect=RuntimeError('OWNED_STOP_BEFORE_SEALER'))as sealer,patch.object(D,'prepare_checked_caches',side_effect=AssertionError('cache must not run'))as cache,patch.object(D,'live_main',side_effect=AssertionError('network must not run'))as network,patch.object(D,'command',side_effect=AssertionError('image must not run'))as image:
     with self.assertRaisesRegex(RuntimeError,'OWNED_STOP_BEFORE_SEALER'if bad==raw else'assessor_role_catalog_join'):D.normal_build(Path(folder),REPO)
     self.assertEqual(sealer.call_count,1 if bad==raw else 0);cache.assert_not_called();network.assert_not_called();image.assert_not_called()
  self.assertEqual(D.closed_failure(RuntimeError('assessor_role_catalog_join'))['code'],'assessor_role_catalog_join')
 def test_real_preflight_accepts_actual_new_driver_outer_join_and_rejects_stale_pin(self):
  raws={D.BUSINESS:path(D.BUSINESS).read_bytes(),D.OUTER:path(D.OUTER).read_bytes()};driver=path(D.SELF).read_bytes();rows=[{'path':n,'bytes':len(raw),'sha256':sha256(raw).hexdigest()}for n,raw in[(D.SELF,driver),*raws.items()]]
  with tempfile.TemporaryDirectory(dir=ROOT/'tests')as folder:
   D.preflight_business81_sources(Path(folder),raws,rows)
   bad=raws[D.OUTER].replace(repr((len(driver),sha256(driver).hexdigest())).encode(),b"(1, '0000000000000000000000000000000000000000000000000000000000000000')");self.assertNotEqual(bad,raws[D.OUTER])
   with self.assertRaisesRegex(RuntimeError,'business81_source'):D.preflight_business81_sources(Path(folder),{**raws,D.OUTER:bad},rows)
 def parcel(self,elapsed,*,corrupt=False,guard=None):
  payload=b'OWNED PUBLIC SOURCE\n';index={'files':[{'path':'public-source.py','bytes':len(payload),'sha256':sha256(payload).hexdigest(),'mode':0o644}],'source_date_epoch':1792972800}
  with tempfile.TemporaryDirectory(dir=ROOT/'tests')as folder:
   archive=Path(folder)/'owned.tar.gz'
   with tarfile.open(archive,'w:gz',format=tarfile.USTAR_FORMAT)as output:
    for name,raw in(('controller-manifest.json',D.encoded(index)),('public-source.py',payload.replace(b'OWNED',b'WRONG')if corrupt else payload)):
     item=tarfile.TarInfo(name);item.uid=item.gid=0;item.uname=item.gname='';item.mode=0o644;item.mtime=index['source_date_epoch'];item.size=len(raw);output.addfile(item,io.BytesIO(raw))
   fd=os.open(archive,os.O_RDONLY);calls=[];times=iter([0])
   def clock():return next(times,elapsed)
   try:
    with patch.object(D.time,'monotonic',side_effect=clock):return D.verify_controller_parcel(SimpleNamespace(fd=fd,check=lambda:calls.append('held')),index,guard or(lambda:calls.append('normal_operation'))),calls
   finally:os.close(fd)
 def test_longer_verification_budget_preserves_all_hash_inventory_and_original_operation_guards(self):
  value,calls=self.parcel(150);self.assertTrue(value);self.assertGreaterEqual(calls.count('normal_operation'),3)
  with self.assertRaisesRegex(RuntimeError,'controller_parcel_deadline'):self.parcel(361)
  with self.assertRaisesRegex(RuntimeError,'controller_parcel_binding'):self.parcel(150,corrupt=True)
  def original_operation_expired():raise RuntimeError('ORIGINAL_OPERATION_DEADLINE')
  with self.assertRaisesRegex(RuntimeError,'ORIGINAL_OPERATION_DEADLINE'):self.parcel(150,guard=original_operation_expired)
  module=ast.parse(path(D.SELF).read_bytes())
  for name,expected in(('controller_parcel',600),('verify_controller_parcel',360)):
   f=next(n for n in module.body if isinstance(n,ast.FunctionDef)and n.name==name);values=[n.value.right.value for n in f.body if isinstance(n,ast.Assign)and any(isinstance(t,ast.Name)and t.id=='end'for t in n.targets)and isinstance(n.value,ast.BinOp)];self.assertEqual(values,[expected])
if __name__=='__main__':unittest.main()
