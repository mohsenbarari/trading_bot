"""Owned files/inert children and labelled source metadata; no package action."""
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from types import ModuleType
from unittest.mock import patch
from zipfile import ZipFile,ZipInfo,ZIP_DEFLATED
import ctypes,io,json,os,subprocess,tempfile,unittest
ROOT=Path(__file__).resolve().parents[1]
P=ROOT/'scripts/prepare_sqlite_wal_os_cache.py';M=ModuleType('_owned_sqlite_cache');M.__file__=str(P);exec(compile(P.read_bytes(),str(P),'exec'),M.__dict__)

def fixture():
 contents={name:(b'PUBLIC_SYNTHETIC_PACKAGE' if name==M.PACKAGE else b'PUBLIC_METADATA\n') for name in M.OUTPUT_LIMITS}
 out=io.BytesIO()
 with ZipFile(out,'w',compression=ZIP_DEFLATED) as archive:
  for name,raw in contents.items():
   info=ZipInfo(name);info.create_system=3;info.external_attr=0o100644<<16;info.compress_type=ZIP_DEFLATED;archive.writestr(info,raw)
 raw=out.getvalue()
 lock={'schema':'sqlite_wal_image_package_lock/1','armed':True,
  'package':{'name':'libsqlite3-0','baseline_version':M.BASE_VERSION,'version':M.VERSION,'architecture':'amd64','multi_arch':'same','path':M.PACKAGE},
  'outputs':{name:{'bytes':len(value),'sha256':sha256(value).hexdigest()} for name,value in contents.items()},
  'artifact':{'id':101,'run_id':202,'bytes':len(raw),'sha256':sha256(raw).hexdigest(),'members':len(contents)},
  'source':{'commit':'a'*40,'recipe_sha256':'b'*64,'production_patch_sha256':'ab1d8c75b8ed521cc5d1d0c64da66be26ca3b72d53326c28b6a86473f42ddc85','root_build_receipt_sha256':'c'*64},
  'installed_library':{'path':'/usr/lib/x86_64-linux-gnu/libsqlite3.so.0.8.6','bytes':1,'sha256':'d'*64}}
 return lock,raw,contents

class CacheSourceTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory(prefix='.owned-cache-',dir=ROOT);self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
 def test_lock_pin_and_unarmed_policy_precede_any_package_or_network_action(self):
  raw=(ROOT/'scripts/sqlite_wal_package.lock.json').read_bytes();self.assertEqual(sha256(raw).hexdigest(),M.LOCK_SHA)
  with patch.object(M.subprocess,'Popen',side_effect=AssertionError('ambient denied')):
   with self.assertRaisesRegex(RuntimeError,'lock_unarmed'):M.validate_lock({'schema':'sqlite_wal_image_package_lock/1','armed':False})
 def test_exact_typed_lock_and_unknown_armed_fields_refuse(self):
  lock,_,_=fixture();self.assertEqual(M.validate_lock(lock),lock)
  for mutate in ('bool_id','bool_size','bad_package','extra_output','wrong_patch','library_escape'):
   wrong=deepcopy(lock)
   if mutate=='bool_id':wrong['artifact']['id']=True
   elif mutate=='bool_size':wrong['outputs'][M.PACKAGE]['bytes']=True
   elif mutate=='bad_package':wrong['package']['name']='OTHER'
   elif mutate=='extra_output':wrong['outputs']['OTHER']={'bytes':1,'sha256':'e'*64}
   elif mutate=='wrong_patch':wrong['source']['production_patch_sha256']='e'*64
   else:wrong['installed_library']['path']='/tmp/OTHER'
   with self.subTest(case=mutate),self.assertRaisesRegex(RuntimeError,'lock_contract'):M.validate_lock(wrong)
 def test_duplicate_or_nonfinite_document_refuses(self):
  for raw in (b'{"a":1,"a":2}',b'{"a":NaN}'):
   with self.assertRaises(RuntimeError):M.document(raw)
 def test_archive_exact_seven_public_outputs_and_all_pins(self):
  lock,raw,contents=fixture();self.assertEqual(M.admit_archive(raw,lock),contents[M.PACKAGE])
  wrong=deepcopy(lock);wrong['outputs']['abi-contract.json']['sha256']='0'*64
  with self.assertRaisesRegex(RuntimeError,'archive_pin'):M.admit_archive(raw,wrong)
 def test_zip_symlink_duplicate_or_unexpected_member_refuses(self):
  lock,_,contents=fixture()
  for edit in ('symlink','extra','duplicate'):
   out=io.BytesIO()
   with ZipFile(out,'w') as archive:
    for name,raw in contents.items():
     info=ZipInfo(name);info.create_system=3;info.external_attr=(0o120777 if edit=='symlink' and name==M.PACKAGE else 0o100644)<<16;archive.writestr(info,raw)
    if edit=='extra':archive.writestr('../OTHER',b'PUBLIC')
    elif edit=='duplicate':
     import warnings
     with warnings.catch_warnings():warnings.simplefilter('ignore',UserWarning);archive.writestr(M.PACKAGE,contents[M.PACKAGE])
   raw=out.getvalue();wrong=deepcopy(lock);wrong['artifact'].update(bytes=len(raw),sha256=sha256(raw).hexdigest())
   with self.subTest(case=edit),self.assertRaises(RuntimeError):M.admit_archive(raw,wrong)
 def test_cache_prepare_check_and_corruption_refusal_only_owned_synthetic_data(self):
  lock,raw,contents=fixture();archive=self.root/'fixture.zip';archive.write_bytes(raw);cache=self.root/'sqlite-wal-capsule'
  with patch.object(M,'load_lock',return_value=lock),patch.object(M,'download',side_effect=AssertionError('network denied')),patch.object(M.subprocess,'Popen',side_effect=AssertionError('package denied')):
   self.assertEqual(M.prepare(cache,archive)['status'],'cache_prepared')
   self.assertEqual({p.name for p in cache.iterdir()},{M.PACKAGE,'manifest.json'})
   self.assertEqual((cache/M.PACKAGE).read_bytes(),contents[M.PACKAGE]);self.assertEqual(M.prepare(cache,check_only=True)['status'],'cache_verified')
   (cache/M.PACKAGE).write_bytes(b'PUBLIC_CHANGED')
   with self.assertRaisesRegex(RuntimeError,'file_pin'):M.prepare(cache,check_only=True)
 def test_same_bytes_artifact_refresh_reuses_valid_cache_without_get(self):
  lock,raw,_=fixture();archive=self.root/'fixture.zip';archive.write_bytes(raw);cache=self.root/'sqlite-wal-capsule'
  with patch.object(M,'load_lock',return_value=lock):M.prepare(cache,archive)
  refreshed=deepcopy(lock);refreshed['artifact']['id']+=1;refreshed['artifact']['run_id']+=1
  refreshed['source']['commit']='e'*40;refreshed['source']['root_build_receipt_sha256']='f'*64
  with patch.object(M,'load_lock',return_value=refreshed),patch.object(M,'download',side_effect=AssertionError('GET not permitted')):
   self.assertEqual(M.prepare(cache)['status'],'cache_verified')
 def test_late_physical_package_close_change_cannot_publish_success(self):
  lock,raw,_=fixture();archive=self.root/'fixture.zip';archive.write_bytes(raw);cache=self.root/'sqlite-wal-capsule'
  with patch.object(M,'load_lock',return_value=lock):M.prepare(cache,archive)
  close=os.close;target=cache/M.PACKAGE;inode=target.stat().st_ino;changed=False
  def actual(fd):
   nonlocal changed
   chosen=os.fstat(fd).st_ino==inode;close(fd)
   if chosen and not changed:changed=True;target.chmod(0o400)
  with patch.object(M,'load_lock',return_value=lock),patch.object(M.os,'close',actual),self.assertRaisesRegex(RuntimeError,'file_closed_changed'):
   M.prepare(cache,check_only=True)
  self.assertTrue(changed)
 def test_physical_cache_lock_close_change_still_caught_by_retained_data_fds(self):
  lock,raw,_=fixture();archive=self.root/'fixture.zip';archive.write_bytes(raw);cache=self.root/'sqlite-wal-capsule'
  with patch.object(M,'load_lock',return_value=lock):M.prepare(cache,archive)
  close=os.close;lockinode=(self.root/'.sqlite-wal-capsule.lock').stat().st_ino;changed=False
  def actual(fd):
   nonlocal changed
   chosen=os.fstat(fd).st_ino==lockinode;close(fd)
   if chosen and not changed:changed=True;(cache/M.PACKAGE).chmod(0o400)
  with patch.object(M,'load_lock',return_value=lock),patch.object(M.os,'close',actual),self.assertRaisesRegex(RuntimeError,'file_closed_changed'):
   M.prepare(cache,check_only=True)
  self.assertTrue(changed)
 def test_hardlink_and_symlink_source_refuse_before_read(self):
  path=self.root/'public';path.write_bytes(b'PUBLIC');other=self.root/'alias';os.link(path,other)
  with self.assertRaisesRegex(RuntimeError,'file_metadata'):M.PublicFile(path,64)
  other.unlink();link=self.root/'link';link.symlink_to(path)
  with self.assertRaisesRegex(RuntimeError,'file_metadata'):M.PublicFile(link,64)
 def test_exact_installer_commands_allow_no_apt_or_other_package(self):
  path=M.CAPSULE/M.PACKAGE;allowed=M.commands(path)
  self.assertIn(('/usr/bin/dpkg',*M.DOC_INCLUDES,'--install',str(path)),allowed)
  self.assertEqual(len(allowed),8);self.assertFalse(any('apt-get' in part for row in allowed for part in row))
  with self.assertRaisesRegex(RuntimeError,'installer_command'):M.commands(Path('/tmp/OTHER.deb'))
 def test_actual_owned_cache_lock_conflict_has_specific_busy_result(self):
  cache=self.root/'sqlite-wal-capsule'
  with M.CacheLock(cache),self.assertRaisesRegex(RuntimeError,'cache_busy'):M.CacheLock(cache)
 def test_public_capability_contract_requires_declared_no_inherited_or_ambient_caps(self):
  # Schema fixture only; this is not evidence of actual privilege admission.
  rows={'NoNewPrivs':'1','CapBnd':'d9','CapEff':'d9','CapPrm':'d9','CapInh':'0','CapAmb':'0'}
  M.capability_contract(rows)
  for field,bad in [('CapInh','1'),('CapAmb','1'),('NoNewPrivs','0'),('CapEff','ffff')]:
   wrong={**rows,field:bad}
   with self.subTest(field=field),self.assertRaisesRegex(RuntimeError,'image_boundary'):M.capability_contract(wrong)
 def test_inventory_keeps_every_other_package_and_rejects_duplicate_names(self):
  raw=b'libsqlite3-0:amd64\t3.46.1-7+deb13u2\tinstalled\nOTHER\t1\tinstalled\n'
  with self.assertRaises(RuntimeError):M.package_inventory(raw)
  raw=raw.replace(b'OTHER',b'other');self.assertEqual(len(M.package_inventory(raw)),2)
  with self.assertRaises(RuntimeError):M.package_inventory(raw+raw.splitlines(keepends=True)[0])
 def test_unapproved_subprocess_never_spawns(self):
  with patch.object(M.subprocess,'Popen',side_effect=AssertionError('spawn denied')),self.assertRaisesRegex(RuntimeError,'installer_command'):M.bounded(['/bin/OTHER'],set())

class OwnedProcessTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  # Only this short-lived test process becomes a subreaper. It never loads an
  # artifact, invokes package tools, changes privileges or touches the daemon.
  if ctypes.CDLL(None,use_errno=True).prctl(36,1,0,0,0)!=0:raise AssertionError('owned subreaper unavailable')
 def inert(self,code,seconds=3,interrupted_wait=False):
  argv=['/usr/bin/python3','-I','-B','-c',code];children=[];signals=[];real=subprocess.Popen;killpg=os.killpg
  def spawn(*args,**kwargs):
   child=real(*args,**kwargs);children.append(child)
   if interrupted_wait:
    actual=child.wait
    def wait(*args,**kwargs):actual(*args,**kwargs);raise RuntimeError('OWNED_WAIT_INTERRUPTED')
    child.wait=wait
   return child
  def signal_owned(pid,sig):
   self.assertIn(pid,{p.pid for p in children});self.assertTrue(all(p.returncode is None for p in children if p.pid==pid));signals.append(pid);return killpg(pid,sig)
  with patch.object(M.subprocess,'Popen',spawn),patch.object(M.os,'killpg',signal_owned):
   try:return M.bounded(argv,{tuple(argv)},seconds=seconds),signals
   finally:self.assertTrue(children);self.assertTrue(all(p.returncode is not None for p in children));self.assertEqual(M.children(),set())
 def test_real_normal_reaped_child_receives_no_signal(self):
  raw,signals=self.inert("print('PUBLIC')");self.assertEqual(raw,b'PUBLIC\n');self.assertEqual(signals,[])
 def test_interrupted_wait_after_actual_reap_receives_no_signal(self):
  with self.assertRaisesRegex(RuntimeError,'OWNED_WAIT_INTERRUPTED'):self.inert("print('PUBLIC')",interrupted_wait=True)
 def test_live_owned_timeout_is_killed_and_reaped(self):
  with self.assertRaisesRegex(RuntimeError,'installer_deadline'):self.inert('import time;time.sleep(2)',seconds=.05)
 def test_output_bound_stops_and_reaps_owned_child(self):
  with patch.object(M,'MAX_OUTPUT',64),self.assertRaisesRegex(RuntimeError,'installer_output'):self.inert("print('P'*4096)")
 def test_unexpected_owned_descendant_prevents_success_and_is_reaped(self):
  code='import os,time\npid=os.fork()\nif pid:os._exit(0)\nos.close(1);os.close(2);time.sleep(2)'
  with self.assertRaisesRegex(RuntimeError,'installer_descendants'):self.inert(code)

if __name__=='__main__':unittest.main()
