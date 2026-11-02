"""Labelled synthetic protocol/proof fixtures; no real image or CVE acceptance."""
from copy import deepcopy
from pathlib import Path
from types import ModuleType
from urllib.parse import quote
from hashlib import sha256
import json,unittest,zipfile,tempfile
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
REPO=next(p for p in Path(__file__).resolve().parents if(p/'docs/memory/manifest.md').exists())
def module(name,path):
 m=ModuleType(name);m.__file__=str(path);exec(compile(path.read_bytes(),str(path),'exec'),m.__dict__);return m
A=module('_owned_canonical_assessor',ROOT/'scripts/assess_image_security_evidence.py')
N=module('_owned_canonical_normalizer',ROOT/'scripts/normalize_normal_market_security_evidence.py')
R=module('_owned_canonical_collector',ROOT/'scripts/collect_normal_market_runtime.py')
G=module('_owned_canonical_runtime_verifier',ROOT/'scripts/verify_sqlite_runtime.py')
F=module('_owned_canonical_package_fixtures',ROOT/'tests/test_sqlite3541_cache_integration.py')
H=module('_owned_original_assessor_fixtures',REPO/'tests/test_assess_image_security_evidence.py');H.A=A
MEDIUM={'CVE-2026-119378','CVE-2026-119591','CVE-2026-50812','CVE-2026-50813'}
def packet():
 p=H.Packet();lock,contents=F.fixture();profile=F.C.validate_lock(lock);version=profile['version'];library=profile['installed_library']
 p.inputs.update({'sqlite-canonical-package.py':(ROOT/'scripts/sqlite3541_package.py').read_bytes(),
  'sqlite-canonical-provenance.json':contents['trixie'][1]['provenance.json'],'sqlite-lock.json':F.C.encoded(lock),
  'sqlite-probe.json':A.encoded({'status':'PASS','sqlite_package':version,'production_data_accessed':False,'all_cves_attested':False,
   'canonical_release_identity_checked':True,'legacy_cast15_verified':True,'numeric_contract':F.C.NUMERIC_CONTRACT,'canonical_source_id':F.C.SOURCE['source_id'],
   'sqlite_system_library':{**library,'mapped_inode_verified':True}})})
 purl='pkg:deb/debian/libsqlite3-0@'+quote(version,safe='')+'?arch=amd64&distro=debian-13.7'
 p.sbom['components'].append({'name':'libsqlite3-0','version':version,'purl':purl,'bom-ref':purl,
  'properties':[{'name':'aquasecurity:trivy:'+key,'value':value}for key,value in {'SrcName':'sqlite3','SrcVersion':'3.54.1','SrcRelease':version.split('-',1)[1],'PkgType':'debian','LayerDiffID':'sha256:'+'e'*64}.items()]})
 for cve in sorted(A.SQLITE_ENGINE_CVES|{'CVE-2025-70873','CVE-2021-45346'}):
  severity='HIGH'if cve=='CVE-2026-119373'else'MEDIUM'if cve in MEDIUM else'UNKNOWN'if cve in A.SQLITE_ENGINE_CVES else'LOW'
  p.scan['Results'][0]['Vulnerabilities'].append({'VulnerabilityID':cve,'PkgName':'libsqlite3-0','InstalledVersion':version,
   'PkgIdentifier':{'PURL':purl},'Severity':severity,'Status':'affected','Layer':{'DiffID':'sha256:'+'e'*64}})
 file={**library,'mode':0o100644,'uid':0,'gid':0}
 native={'package':'libsqlite3-0','installed_version':version,'source_package':'sqlite3','source_version':'3.54.1',
  'source_release':version.split('-',1)[1],'deb_sha256':profile['package']['sha256'],'expected_files':[file],'actual_files':[deepcopy(file)]}
 p.proofs['payload.json']['observations']['packages'].append(native)
 p.proofs['advisories.json']['observations']['advisory_ids']+=sorted(A.SQLITE_ENGINE_CVES)
 facts=N.canonical_source(A,p.inputs,{'packages':[native]});p.proof('sqlite-canonical.json','upstream_release',facts)
 originals=p.descriptors['sqlite-canonical.json']['original_inputs']
 for name in('sqlite-canonical-package.py','sqlite-canonical-provenance.json','sqlite-lock.json','sqlite-probe.json'):
  originals.append(name);p.proofs['sqlite-canonical.json']['original_inputs'][name]=H.pin(p.inputs[name])
 rows,_=A.findings(p.scan,p.sbom,p.scope)
 p.lock['findings'] += [{'row':row,'disposition':'fixed','proofs':['payload.json','sqlite-canonical.json']}for row in rows if row['source_package']=='sqlite3']
 return p
class CanonicalEvidenceTests(unittest.TestCase):
 def test_canonical_engine12_bind_all_severities_but_never_erase_low_raw_rows(self):
  p=packet();result=p.run();self.assertEqual(result['status'],'PASS');self.assertEqual(result['unresolved_count'],0)
  self.assertEqual(result['raw_counts'],{'HIGH':45,'CRITICAL':0,'MEDIUM':4,'UNKNOWN':7,'LOW':2})
  self.assertFalse(result['security_accepted']);self.assertFalse(result['raw_gate_passed'])
  self.assertEqual(len([rule for rule in p.lock['findings']if rule['row']['source_package']=='sqlite3']),12)
  self.assertNotIn('CVE-2026-119586',A.SQLITE_ENGINE_CVES)
 def test_numeric_source_package_and_current_mapping_cannot_be_faked_by_status_PASS(self):
  for key,value in(('numeric_contract','OLD_UNQUALIFIED'),('legacy_cast15_verified',False),('canonical_source_id','0'*64),('sqlite_system_library',{}),('sqlite_package','3.54.1-OTHER')):
   p=packet();probe=json.loads(p.inputs['sqlite-probe.json']);probe[key]=value;p.inputs['sqlite-probe.json']=A.encoded(probe)
   p.proofs['sqlite-canonical.json']['original_inputs']['sqlite-probe.json']=H.pin(p.inputs['sqlite-probe.json'])
   with self.subTest(key=key),self.assertRaisesRegex(A.Refused,'sqlite_canonical_probe'):p.run()
 def test_upstream_suite_failure_is_not_hidden_by_a_qualified_lock_shape(self):
  p=packet();proof=json.loads(p.inputs['sqlite-canonical-provenance.json']);proof['vendor_default17_tests']['actual_returncode']=1
  raw=A.encoded(proof);lock=json.loads(p.inputs['sqlite-lock.json']);lock['profiles']['trixie']['provenance']=H.pin(raw)
  p.inputs['sqlite-lock.json']=A.encoded(lock);p.inputs['sqlite-canonical-provenance.json']=raw
  for name in('sqlite-lock.json','sqlite-canonical-provenance.json'):p.proofs['sqlite-canonical.json']['original_inputs'][name]=H.pin(p.inputs[name])
  with self.assertRaisesRegex(RuntimeError,'source_contract'):p.run()
 def test_caller_supplied_replacement_producer_is_rejected_before_exec(self):
  p=packet();raw=b'raise AssertionError("untrusted producer must never execute")\n';p.inputs['sqlite-canonical-package.py']=raw
  p.proofs['sqlite-canonical.json']['original_inputs']['sqlite-canonical-package.py']=H.pin(raw)
  with self.assertRaisesRegex(A.Refused,'sqlite_canonical_helper'):p.run()
 def test_old_f88_cannot_be_relabelled_as_canonical_upstream_proof(self):
  p=packet();native=next(row for row in p.proofs['payload.json']['observations']['packages']if row['package']=='libsqlite3-0')
  native['actual_files'][0]['sha256']='d115380cd2790ee09d493eb0af788745f26a18dcef03ad62a543feff363ba86b'
  native['expected_files']=deepcopy(native['actual_files'])
  with self.assertRaisesRegex(A.Refused,'sqlite_canonical_payload'):p.run()
 def test_canonical_collector_uses_new_declared_package_and_library_before_same_image_measurement(self):
  lock,_=F.fixture();profile=F.C.validate_lock(lock)
  with zipfile.ZipFile(REPO/'deploy/security/normal-market-assessment/stable-source-inputs.zip')as original:
   vector=original.read('runtime-vector.json')
  build={'image_id':'sha256:'+'a'*64,'two_git_input_signature':'d'*64,'run_id':123,'run_attempt':1,
   'source_inputs':{'release_commit':'b'*40,'release_tree':'c'*40}}
  component=(REPO/'scripts/verify_image_component_presence.py').read_bytes();acl=(REPO/'scripts/verify_acl_consumer_presence.py').read_bytes()
  expected,bundle=R.prepare_request(build,vector,F.C.encoded(lock),component,acl)
  sqlite=next(row for row in expected if row['package']=='libsqlite3-0')
  self.assertEqual(sqlite['installed_version'],profile['version']);self.assertEqual(sqlite['source_version'],'3.54.1')
  self.assertEqual(sqlite['deb_sha256'],profile['package']['sha256'])
  self.assertEqual({k:sqlite['expected_files'][0][k]for k in('path','bytes','sha256')},profile['installed_library'])
  self.assertEqual(len(expected),20);self.assertEqual(bundle['image_id'],build['image_id'])
  for variant in('unarmed','wrong_version'):
   bad=deepcopy(lock)
   if variant=='unarmed':bad['armed']=False
   else:bad['profiles']['trixie']['version']='3.54.1-UNREVIEWED'
   with self.subTest(variant=variant),self.assertRaisesRegex(RuntimeError,'runtime_sqlite_lock'):
    R.prepare_request(build,vector,F.C.encoded(bad),component,acl)
 def test_unarmed_lock_cannot_be_used_as_a_runtime_admission(self):
  with tempfile.TemporaryDirectory(prefix='.canonical-runtime-unarmed-',dir=ROOT)as folder:
   root=Path(folder)
   (root/'sqlite3541_package.py').write_bytes((ROOT/'scripts/sqlite3541_package.py').read_bytes())
   (root/'sqlite3541_packages.lock.json').write_bytes(F.C.encoded({'schema':'sqlite3541_qualified_packages/1','armed':False}))
   with patch.object(G,'__file__',str(root/'verify_sqlite_runtime.py')),self.assertRaisesRegex(RuntimeError,'lock_unarmed'):G.canonical_profile()
 def test_real_runtime_profile_is_the_exact_published_trixie_library(self):
  _,profile=G.canonical_profile()
  lock=json.loads((ROOT/'scripts/sqlite3541_packages.lock.json').read_bytes())
  self.assertEqual(profile,lock['profiles']['trixie'])
if __name__=='__main__':unittest.main()
