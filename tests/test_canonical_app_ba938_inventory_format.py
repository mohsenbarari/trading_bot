"""Hash-bound public image metadata and owned inert children only."""
from hashlib import sha256
from pathlib import Path
from types import ModuleType
from unittest.mock import patch
import copy,json,sys,tempfile,unittest

REPO=Path(__file__).resolve().parents[1]
HERE=REPO/'deploy/maintenance/ba938_smoke'
TEST_ROOT=Path(__file__).resolve().parent
FIXTURES=TEST_ROOT/'fixtures/ba938_inventory'


def load(leaf):
 path=HERE/leaf;module=ModuleType('_owned_format_'+leaf.replace('.','_'));module.__file__=str(path)
 exec(compile(path.read_bytes(),str(path),'exec'),module.__dict__);return module


I=load('inventory.py');B=load('compose_security_os_candidate.py')


class InventoryFormatTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.status=(FIXTURES/'dpkg-status.actual-ba938.txt').read_bytes()
  cls.sbom=(FIXTURES/'sbom.actual-ba938.cdx.json').read_bytes()
  assert(len(cls.status),sha256(cls.status).hexdigest())==(87976,'bc24b79515de50f3b39b2438651f325e494a930fce94094170814c46df7a1b52')
  assert(len(cls.sbom),sha256(cls.sbom).hexdigest())==(295871,'5e6a6930bad65191881b6c8366ef58a67c414318c14c0ca28f8dc0570952867c')
 def test_actual_public_status_and_sbom_have_99_exact_full_source_identities(self):
  packages=I.installed_packages(self.status);sbom=I.sbom_packages(self.sbom)
  self.assertEqual((len(packages),len(sbom)),(99,99));self.assertEqual(set(packages),set(sbom))
  for key,actual in packages.items():
   with self.subTest(package=key):
    self.assertEqual(actual,{name:sbom[key][name]for name in('package','installed_version','architecture','source_package','source_version')})
  self.assertEqual(sum(line==b'Conffiles:'for line in self.status.splitlines()),22)
  self.assertEqual(sbom['bsdutils:amd64']['installed_version'],'1:2.41.6-0+deb13u0+gold1')
  self.assertEqual(sbom['bsdutils:amd64']['source_version'],'2.41.6-0+deb13u0+gold1')
  self.assertEqual(sbom['diffutils:amd64']['source_version'],'1:3.10-4')
 def test_deb822_empty_fields_whitespace_and_strict_malformed_refusals(self):
  base=b'Package: owned-fixture\nStatus: install ok installed\nVersion: 1:2.3-4\nArchitecture: amd64\n'
  valid=base+b'Conffiles:\n /owned/public-fixture abc\nDescription:\t public fixture \t\n continuation\n'
  self.assertEqual(I.installed_packages(valid)['owned-fixture:amd64']['installed_version'],'1:2.3-4')
  malformed=(b' orphan\n'+base,base+b'pAcKaGe: duplicate\n',base+b'Bad Name: value\n',base+b': value\n',
   base+b'No-colon\n',base+b'-Bad: value\n',base+b'#Bad: value\n',base.replace(b'Package: owned-fixture\n',b'Package: owned-fixture\n continuation\n'))
  for raw in malformed:
   with self.subTest(raw=raw),self.assertRaisesRegex(RuntimeError,'package_status_shape'):I.installed_packages(raw)
 def test_purl_and_source_epochs_refuse_unknown_duplicate_conflicting_or_redundant_values(self):
  baseline=json.loads(self.sbom)
  index=next(i for i,row in enumerate(baseline['components'])if row.get('name')=='diffutils')
  purl=baseline['components'][index]['purl']
  for replacement in('epoch=','epoch=01','epoch=-1','epoch=%2B1','epoch=1&epoch=1','epoch=1&unknown=1'):
   changed=copy.deepcopy(baseline);changed['components'][index]['purl']=purl.replace('epoch=1',replacement)
   with self.subTest(qualifier=replacement),self.assertRaisesRegex(RuntimeError,'sbom_purl'):I.sbom_packages(json.dumps(changed).encode())
  changed=copy.deepcopy(baseline);changed['components'][index]['purl']=purl.replace('@3.10-4','@1%3A3.10-4')
  with self.assertRaisesRegex(RuntimeError,'sbom_purl'):I.sbom_packages(json.dumps(changed).encode())
  changed=copy.deepcopy(baseline);changed['components'][index]['version']='2:3.10-4'
  with self.assertRaisesRegex(RuntimeError,'sbom_purl'):I.sbom_packages(json.dumps(changed).encode())
  for replacement in('','01','-1','+1'):
   changed=copy.deepcopy(baseline)
   next(row for row in changed['components'][index]['properties']if row['name']=='aquasecurity:trivy:SrcEpoch')['value']=replacement
   with self.subTest(source_epoch=replacement),self.assertRaisesRegex(RuntimeError,'sbom_source'):I.sbom_packages(json.dumps(changed).encode())
  changed=copy.deepcopy(baseline);properties=changed['components'][index]['properties']
  next(row for row in properties if row['name']=='aquasecurity:trivy:SrcVersion')['value']='1:3.10'
  with self.assertRaisesRegex(RuntimeError,'sbom_source'):I.sbom_packages(json.dumps(changed).encode())
  changed=copy.deepcopy(baseline);properties=changed['components'][index]['properties']
  properties.append({'name':'aquasecurity:trivy:SrcEpoch','value':'1'})
  with self.assertRaisesRegex(RuntimeError,'sbom_properties'):I.sbom_packages(json.dumps(changed).encode())
 def test_failure_diagnostics_emit_only_finite_public_codes_and_phases(self):
  with patch.object(I,'PHASE','package_metadata'):
   for code in I.PUBLIC_FAILURE_CODES:
    value=I.failure(RuntimeError(code));self.assertEqual(value,{'status':'REFUSED','phase':'package_metadata','code':code,'security_accepted':False})
    self.assertLess(len(json.dumps(value)),512)
   for error in(RuntimeError('OWNED-UNEXPECTED-DETAIL'),ValueError('OWNED-UNEXPECTED-DETAIL'),OSError('OWNED-UNEXPECTED-DETAIL')):
    value=I.failure(error);self.assertEqual(value['code'],'acl_image_inventory_refused');self.assertNotIn('OWNED-UNEXPECTED-DETAIL',json.dumps(value))
  with patch.object(I,'PHASE','OWNED-UNEXPECTED-PHASE'):self.assertEqual(I.failure(RuntimeError('sbom_purl'))['phase'],'unarmed')
 def test_real_nonzero_owned_child_preserves_public_refusal_without_green_exit(self):
  raw=b'{"code":"package_status_shape","phase":"package_metadata","security_accepted":false,"status":"REFUSED"}\n'
  with tempfile.TemporaryDirectory(prefix='.owned-format-',dir=TEST_ROOT)as tmp:
   target=Path(tmp)/'inventory-output.json'
   code='import os;os.write(1,'+repr(raw)+');raise SystemExit(7)'
   with self.assertRaisesRegex(RuntimeError,'command_failed'):
    B.command([sys.executable,'-I','-S','-B','-c',code],seconds=3,maximum=1024,log_path=target)
   self.assertEqual(target.read_bytes(),raw);self.assertEqual(I.document(target.read_bytes())['status'],'REFUSED')


if __name__=='__main__':unittest.main()
