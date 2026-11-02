"""Synthetic native SQLite metadata/compatibility cases; no install or service action."""
from pathlib import Path
from types import ModuleType
from tempfile import TemporaryDirectory
from unittest.mock import patch
import copy,json,os,unittest

ROOT=Path(__file__).resolve().parents[1]
H=ModuleType('_sqlite_host_runtime');H.__file__=str(ROOT/'deploy/maintenance/sqlite_host_runtime.py')
exec(compile(Path(H.__file__).read_bytes(),H.__file__,'exec'),H.__dict__)
REPO=next(p for p in (ROOT,*ROOT.parents)if(p/'docs/memory/manifest.md').is_file())
R=ModuleType('_versioned_recipe');R.__file__=str(REPO/'deploy/security/sqlite3541/verify.py')
exec(compile(Path(R.__file__).read_bytes(),R.__file__,'exec'),R.__dict__)

def process(root,pid,start,maps,unit='market.service'):
 p=root/str(pid);p.mkdir();(p/'stat').write_text(str(pid)+' (public unit) '+' '.join(['S']+['0']*18+[str(start)])+'\n')
 (p/'maps').write_text(maps);(p/'cgroup').write_text('0::/system.slice/'+unit+'\n');return p

class RuntimeTests(unittest.TestCase):
 def frame(self):return {'path':'/usr/lib/x86_64-linux-gnu/libsqlite3.so.0.8.6','device':os.makedev(8,1),'inode':1234}
 def line(self,path=None,inode=1234):return '1000-2000 r-xp 00000000 08:01 '+str(inode)+' '+(path or self.frame()['path'])+'\n'
 def test_exact_deleted_dpkg_renamed_object_detected_but_recycled_nginx_inode_ignored(self):
  with TemporaryDirectory()as folder:
   root=Path(folder);process(root,111,10,self.line(self.frame()['path']+'.dpkg-tmp (deleted)'))
   process(root,222,20,self.line('/usr/sbin/nginx'));process(root,333,30,self.line(inode=999))
   self.assertEqual(H.old_mappings(self.frame(),root),[{'pid':111,'start_ticks':10,'unit':'market.service'}])
 def test_usrmerge_alias_of_exact_object_is_detected(self):
  with TemporaryDirectory()as folder:
   root=Path(folder);process(root,111,10,self.line(self.frame()['path'][4:]+' (deleted)'))
   self.assertEqual(len(H.old_mappings(self.frame(),root)),1)
 def test_rollback_detects_stale_candidate_in_admitted_unit_but_ignores_foreign_image(self):
  with TemporaryDirectory()as folder:
   root=Path(folder);process(root,111,10,self.line(inode=8888),'market.service')
   process(root,222,20,self.line(inode=9999),'docker.service')
   rows=H.old_mappings(self.frame(),root,different=True,admitted={'market.service'})
   self.assertEqual(rows,[{'pid':111,'start_ticks':10,'unit':'market.service'}])
 def test_original_PID_same_start_cannot_be_treated_as_restarted(self):
  with TemporaryDirectory()as folder:
   root=Path(folder);p=process(root,111,10,'');rows=[{'pid':111,'start_ticks':10,'unit':'market.service'}]
   with self.assertRaisesRegex(RuntimeError,'original_old_library_consumer_alive'):H.original_consumers_gone(rows,root)
   (p/'stat').write_text('111 (new public unit) '+' '.join(['S']+['0']*18+['20'])+'\n')
   H.original_consumers_gone(rows,root);(p/'stat').unlink();H.original_consumers_gone(rows,root)
 def test_recycled_original_inode_allows_verified_current_mapping_after_old_PID_gone(self):
  with TemporaryDirectory()as folder:
   root=Path(folder);process(root,222,20,self.line(),'market.service')
   baseline={'object':self.frame(),'consumers':[{'pid':111,'start_ticks':10,'unit':'market.service'}]}
   result=H.verify_consumers(baseline,self.frame(),{'market.service'},root)
   self.assertTrue(result['previous_object_identity_reused_for_verified_current_payload']);self.assertIsNone(result['old_object_mapping_count'])
   process(root,111,10,self.line(),'market.service')
   with self.assertRaisesRegex(RuntimeError,'original_old_library_consumer_alive'):H.verify_consumers(baseline,self.frame(),{'market.service'},root)
 def test_different_old_object_mapping_remains_a_blocker_even_after_known_PID_gone(self):
  with TemporaryDirectory()as folder:
   root=Path(folder);process(root,222,20,self.line(),'market.service')
   baseline={'object':self.frame(),'consumers':[{'pid':111,'start_ticks':10,'unit':'market.service'}]}
   current={**self.frame(),'inode':9999}
   with self.assertRaisesRegex(RuntimeError,'old_library_mappings_remain'):H.verify_consumers(baseline,current,{'market.service'},root)
 def test_unadmitted_or_unknown_old_consumer_fails_before_install(self):
  for unit in ('other.service',None):
   with self.subTest(unit=unit),self.assertRaisesRegex(RuntimeError,'unadmitted_old_library_consumer'):
    H.consumers_admitted([{'unit':unit}],{'market.service'})
  H.consumers_admitted([{'unit':'market.service'}],{'market.service'})
 def facts(self):return {'package':'3.54.1-0+trading1~noble1','sqlite_runtime':'3.54.1','sqlite_source_id':'2026 '+H.SOURCE_ID,'stdlib_extension':{'sha256':'a'*64},'limits':[1]*12,'numeric_contract':R.NUMERIC_CONTRACT,'numeric_digest':H.CAST_DIGEST,'numeric_roundtrip_failures':{'quote':0,'json_array':0,'json_object':0}}
 def test_real_recipe_numeric_contract_accepts_exact_CAST_and_semantic_roundtrip(self):
  value=self.facts();baseline={'facts':copy.deepcopy(value)};baseline['facts']['numeric_roundtrip_failures']['json_array']=1922
  H.verify_facts(R,baseline,value)
  for key,bad in (('numeric_digest','0'*64),('numeric_roundtrip_failures',{'quote':1,'json_array':0,'json_object':0}),('sqlite_source_id','other'),('package','3.54.1-unqualified')):
   changed=copy.deepcopy(value);changed[key]=bad
   with self.subTest(key=key),self.assertRaises(RuntimeError):H.verify_facts(R,baseline,changed)
 def test_changed_native_extension_or_connection_limits_refused(self):
  value=self.facts();baseline={'facts':copy.deepcopy(value)}
  for key,bad in (('stdlib_extension',{'sha256':'b'*64}),('limits',[2]*12)):
   changed=copy.deepcopy(value);changed[key]=bad
   with self.subTest(key=key),self.assertRaises(RuntimeError):H.verify_facts(R,baseline,changed)
 def test_rollback_requires_original_payload_version_and_exact_original_numeric_observations(self):
  value=self.facts();value.update(package='3.45.1-1ubuntu2.8+gold1',sqlite_runtime='3.45.1',sqlite_source_id='PUBLIC OLD ID',library={'sha256':'a'*64},compile_options=['PUBLIC'],numeric_format_digests={'CAST':H.CAST_DIGEST,'quote':'q','json_array':'j','json_object':'o'})
  value['numeric_roundtrip_failures']['json_array']=1922;baseline={'facts':copy.deepcopy(value)}
  H.verify_rollback_facts(baseline,value)
  for key,bad in (('package','3.54.1-0+trading1~noble1'),('library',{'sha256':'b'*64}),('numeric_roundtrip_failures',{'quote':0,'json_array':0,'json_object':0})):
   changed=copy.deepcopy(value);changed[key]=bad
   with self.subTest(key=key),self.assertRaisesRegex(RuntimeError,'original_baseline_not_restored'):H.verify_rollback_facts(baseline,changed)
 def test_recipe_digest_is_required_before_any_source_execution(self):
  with TemporaryDirectory()as folder:
   path=Path(folder)/'verify.py';path.write_text("raise AssertionError('should not execute')\n")
   with self.assertRaisesRegex(RuntimeError,'recipe_pin'):H.load_recipe(path,'0'*64)
 def test_original_baseline_receipt_cannot_be_overwritten(self):
  with TemporaryDirectory()as folder:
   path=Path(folder)/'baseline.json';H.write(path,{'public':1});before=path.read_bytes()
   with self.assertRaises(FileExistsError):H.write(path,{'public':2})
   self.assertEqual(path.read_bytes(),before);self.assertEqual(path.stat().st_mode&0o777,0o600)
 def test_installed_dpkg_version_must_match_explicit_qualified_version(self):
  with patch.object(H.subprocess,'check_output',return_value=b'3.46.1-9ubuntu0.3'):
   with self.assertRaisesRegex(RuntimeError,'installed_package_version'):
    H.fresh_facts(R,'3.54.1-0+trading1~resolute1',Path('/PUBLIC-NOT-OPENED'),'a'*64)

if __name__=='__main__':unittest.main()
