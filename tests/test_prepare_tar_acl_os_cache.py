"""Owned synthetic ZIP/cache/process contracts; no package/daemon/network action."""
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from types import ModuleType,SimpleNamespace
from unittest.mock import patch
from zipfile import ZipFile,ZipInfo,ZIP_DEFLATED
import fcntl,io,json,os,signal,subprocess,sys,tempfile,unittest,warnings
ROOT=Path(__file__).resolve().parents[1]
P=ROOT/'scripts/prepare_tar_acl_os_cache.py';M=ModuleType('_owned_tar_cache');M.__file__=str(P);exec(compile(P.read_bytes(),str(P),'exec'),M.__dict__)


def fixture():
 """Synthetic package bytes, with copies of public CI schema references only."""
 lock=json.loads((ROOT/'scripts/tar_acl_package.lock.json').read_bytes())
 contents={name:(b'OWNED_PUBLIC_PACKAGE_NOT_A_DEB'if name==M.PACKAGE else b'OWNED_PUBLIC_BUILD_METADATA\n')for name in M.OUTPUT_LIMITS}
 for name in contents:
  if name.endswith('.json'):contents[name]=(ROOT/'tests/fixtures/tar_acl_cache'/name).read_bytes()
 payload=deepcopy(lock['runtime_payload']);payload['tar']['deb_sha256']=sha256(contents[M.PACKAGE]).hexdigest()
 contents['runtime-payload.json']=M.encoded(payload);lock['runtime_payload']=payload
 lock['outputs']={name:{'bytes':len(raw),'sha256':sha256(raw).hexdigest()}for name,raw in contents.items()}
 stream=io.BytesIO()
 with ZipFile(stream,'w',compression=ZIP_DEFLATED)as archive:
  for name,raw in contents.items():
   info=ZipInfo(name);info.create_system=3;info.external_attr=0o100644<<16;info.compress_type=ZIP_DEFLATED;archive.writestr(info,raw)
 raw=stream.getvalue();lock['artifact']={'id':101,'run_id':202,'bytes':len(raw),'sha256':sha256(raw).hexdigest(),'members':12}
 return lock,raw,contents


class CacheTests(unittest.TestCase):
 def setUp(self):
  temp=tempfile.TemporaryDirectory(prefix='.owned-tar-',dir=ROOT);self.addCleanup(temp.cleanup);self.root=Path(temp.name)
 def prepared(self):
  lock,raw,contents=fixture();archive=self.root/'fixture.zip';archive.write_bytes(raw);cache=self.root/'tar-acl-capsule'
  with patch.object(M,'load_lock',return_value=lock):M.prepare(cache,archive)
  return lock,archive,cache,contents
 def test_genuine_lock_pins_actual_source_package_without_global_acceptance(self):
  raw=(ROOT/'scripts/tar_acl_package.lock.json').read_bytes();self.assertEqual(sha256(raw).hexdigest(),M.LOCK_SHA)
  value=M.validate_lock(M.document(raw));self.assertEqual(value['artifact']['run_id'],40397973636);self.assertEqual(value['outputs'][M.PACKAGE]['bytes'],816380)
  self.assertEqual(len(value['runtime_payload']['tar']['regular_files']),54);self.assertFalse(value['security_accepted']);self.assertFalse(value['whole_image_other_callers_assessed'])
 def test_unarmed_unknown_fields_boolean_sizes_and_other_libacl_refuse(self):
  with self.assertRaisesRegex(RuntimeError,'lock_unarmed'):M.validate_lock({'schema':'tar_acl_image_package_lock/1','armed':False})
  original,_,_=fixture();M.validate_lock(original)
  for mode in('extra','bool','library','version','payload'):
   value=deepcopy(original)
   if mode=='extra':value['unknown']=True
   elif mode=='bool':value['artifact']['id']=True
   elif mode=='library':value['approved_libacl']['sha256']='0'*64
   elif mode=='version':value['package']['version']=M.BASE_VERSION
   else:value['runtime_payload']['tar']['regular_files'][0]['uid']=True
   with self.subTest(mode=mode),self.assertRaises(RuntimeError):M.validate_lock(value)
 def test_duplicate_nonfinite_and_overflow_number_json_refuse(self):
  for raw in(b'{"x":1,"x":2}',b'{"x":NaN}',b'{"x":1e999}'):
   with self.assertRaises(RuntimeError):M.document(raw)
 def test_exact_twelve_member_archive_and_every_ordinary_gate(self):
  lock,raw,contents=fixture();self.assertEqual(M.admit_archive(raw,lock),contents[M.PACKAGE])
  for name in('caller-proof.json','runtime-payload.json','acl_2.4.0-1~trixie+gold1_amd64.buildinfo'):
   changed=deepcopy(lock);changed['outputs'][name]['sha256']='0'*64
   with self.subTest(name=name),self.assertRaisesRegex(RuntimeError,'archive_pin'):M.admit_archive(raw,changed)
 def test_incomplete_actual_roundtrip_schema_cannot_be_relabelled(self):
  lock,raw,contents=fixture();ordinary=json.loads(contents['ordinary-compatibility.json']);ordinary['tar_archives']['roundtrips'].pop()
  contents['ordinary-compatibility.json']=M.encoded(ordinary);stream=io.BytesIO()
  with ZipFile(stream,'w')as archive:
   for name,value in contents.items():archive.writestr(name,value)
  raw=stream.getvalue();lock['artifact'].update(bytes=len(raw),sha256=sha256(raw).hexdigest());value=contents['ordinary-compatibility.json'];lock['outputs']['ordinary-compatibility.json']={'bytes':len(value),'sha256':sha256(value).hexdigest()}
  with self.assertRaisesRegex(RuntimeError,'archive_contract'):M.admit_archive(raw,lock)
 def test_symlink_duplicate_and_extra_zip_member_refuse(self):
  lock,_,contents=fixture()
  for mode in('link','duplicate','extra'):
   stream=io.BytesIO()
   with ZipFile(stream,'w')as archive:
    for name,raw in contents.items():
     info=ZipInfo(name);info.create_system=3;info.external_attr=(0o120777 if mode=='link'and name==M.PACKAGE else 0o100644)<<16;archive.writestr(info,raw)
    if mode=='extra':archive.writestr('../unexpected',b'PUBLIC')
    if mode=='duplicate':
     with warnings.catch_warnings():warnings.simplefilter('ignore',UserWarning);archive.writestr(M.PACKAGE,b'PUBLIC')
   raw=stream.getvalue();changed=deepcopy(lock);changed['artifact'].update(bytes=len(raw),sha256=sha256(raw).hexdigest())
   with self.subTest(mode=mode),self.assertRaises(RuntimeError):M.admit_archive(raw,changed)
 def test_cache_publish_preserves_two_exact_created_inodes_and_no_get_on_check(self):
  with patch.object(M.subprocess,'Popen',side_effect=AssertionError('ambient denied')):
   lock,archive,cache,contents=self.prepared();self.assertEqual({p.name for p in cache.iterdir()},{M.PACKAGE,'manifest.json'})
   self.assertEqual((cache/M.PACKAGE).read_bytes(),contents[M.PACKAGE])
   with patch.object(M,'load_lock',return_value=lock):self.assertEqual(M.prepare(cache,check_only=True)['status'],'cache_verified')
  self.assertFalse(any(p.name.startswith('.tar-acl-download-')or p.name.startswith('.tar-acl-capsule-')for p in self.root.iterdir()))
 def test_same_package_refresh_reuses_cache_without_artifact_fetch(self):
  lock,_,cache,_=self.prepared();lock=deepcopy(lock);lock['artifact']['id']+=1;lock['source']['commit']='e'*40;lock['source']['ci_result_sha256']='f'*64
  with patch.object(M,'load_lock',return_value=lock),patch.object(M,'download',side_effect=AssertionError('GET forbidden')):
   self.assertEqual(M.prepare(cache)['status'],'cache_verified')
 def test_package_bytes_mode_and_hardlink_corruption_refuse(self):
  lock,_,cache,_=self.prepared();target=cache/M.PACKAGE;target.chmod(0o400)
  with patch.object(M,'load_lock',return_value=lock),self.assertRaises(RuntimeError):M.prepare(cache,check_only=True)
  target.chmod(0o644);alias=self.root/'alias';os.link(target,alias)
  with self.assertRaisesRegex(RuntimeError,'file_metadata'):M.PublicFile(target,65536)
 def test_busy_cache_lock_refuses_without_payload(self):
  with M.CacheLock(self.root/'tar-acl-capsule'),self.assertRaisesRegex(RuntimeError,'cache_busy'):M.CacheLock(self.root/'tar-acl-capsule')
 def test_known_write_is_bound_before_original_writer_physical_close(self):
  target=self.root/'owned';close=os.close;changed=[]
  def closing(fd):
   writer=target.exists()and os.fstat(fd).st_ino==target.stat().st_ino and fcntl.fcntl(fd,fcntl.F_GETFL)&os.O_ACCMODE==os.O_WRONLY
   close(fd)
   if writer:target.chmod(0o400);changed.append(True)
  with patch.object(M.os,'close',closing),self.assertRaises(RuntimeError),M.Held()as held:M.write_new(target,b'PUBLIC',held=held)
  self.assertEqual(changed,[True])
 def test_final_original_source_close_drift_still_refuses(self):
  lock,_,cache,_=self.prepared();close=os.close;chosen=(cache/M.PACKAGE).stat().st_ino;changed=[]
  def closing(fd):
   matches=os.fstat(fd).st_ino==chosen;close(fd)
   if matches and not changed:(cache/M.PACKAGE).chmod(0o400);changed.append(True)
  with patch.object(M,'load_lock',return_value=lock),patch.object(M.os,'close',closing),self.assertRaises(RuntimeError):M.prepare(cache,check_only=True)
  self.assertTrue(changed)
 def test_temp_cleanup_cannot_hide_cache_metadata_drift(self):
  lock,_,cache,_=self.prepared();actual=M.tempfile.TemporaryDirectory
  class Late:
   def __init__(self,*a,**k):self.original=actual(*a,**k)
   def __enter__(self):return self.original.__enter__()
   def __exit__(self,*args):
    result=self.original.__exit__(*args);(cache/M.PACKAGE).chmod(0o400);return result
  with patch.object(M,'load_lock',return_value=lock),patch.object(M.tempfile,'TemporaryDirectory',Late),self.assertRaises(RuntimeError):M.prepare(cache,check_only=True)
 def test_installer_is_only_tar_offline_with_complete_locale_man_info_payload(self):
  allowed=M.commands(M.CAPSULE/M.PACKAGE);install=('/usr/bin/dpkg',*M.DOC_INCLUDES,'--install',str(M.CAPSULE/M.PACKAGE));self.assertIn(install,allowed)
  self.assertEqual(len(allowed),9);self.assertFalse(any('apt-get'in x for row in allowed for x in row))
  for name in('/usr/share/doc/tar','/usr/share/locale','/usr/share/man','/usr/share/info'):
   self.assertIn('--path-include='+name,install);self.assertIn('--path-include='+name+'/*',install)
 def test_inventory_only_tar_version_may_change_and_approved_libacl_is_required(self):
  lock,_,_=fixture();before={'tar':(M.BASE_VERSION,'installed'),'libacl1:amd64':(lock['approved_libacl']['version'],'installed'),'libsqlite3-0:amd64':('UNCHANGED_SQLITE','installed'),'other14':('UNCHANGED','installed')}
  M.baseline_inventory(before,lock);after={**before,'tar':(M.VERSION,'installed')};M.verify_transition(before,after)
  for key in('libacl1:amd64','libsqlite3-0:amd64','other14','unexpected'):
   changed={**after,key:('CHANGED','installed')}
   with self.subTest(key=key),self.assertRaisesRegex(RuntimeError,'package_inventory'):M.verify_transition(before,changed)
  with self.assertRaisesRegex(RuntimeError,'package_baseline'):M.baseline_inventory({**before,'libacl1:amd64':('WRONG','installed')},lock)
 @unittest.skipUnless(os.geteuid()==os.getegid()==0,'requires genuine Root-owned symlink; no owner spoof')
 def test_actual_owned_symlink_descriptor_and_late_close_binding(self):
  path=self.root/'owned-link';path.symlink_to('/owned/public-target');close=os.close;changed=[]
  with M.Held()as held:held.files.append(M.PublicLink(path,'/owned/public-target'));held.check()
  def closing(fd):
   selected=os.fstat(fd).st_ino==path.lstat().st_ino;close(fd)
   if selected:path.unlink();path.symlink_to('/owned/other-target');changed.append(True)
  with patch.object(M.os,'close',closing),self.assertRaises(RuntimeError),M.Held()as held:held.files.append(M.PublicLink(path,'/owned/public-target'))
  self.assertTrue(changed)


class FetchTests(unittest.TestCase):
 def test_real_owned_successful_fetch_is_bounded_and_never_signals_reaped_pid(self):
  with tempfile.TemporaryDirectory(prefix='.owned-fetch-',dir=ROOT)as tmp:
   path=Path(tmp)/'archive';raw=b'OWNED_PUBLIC_ARCHIVE';lock={'artifact':{'id':101,'bytes':len(raw),'sha256':sha256(raw).hexdigest()}};actual=subprocess.Popen;children=[]
   def spawn(argv,**options):
    self.assertEqual(argv,['/usr/bin/gh','api','--method','GET','repos/mohsenbarari/trading_bot/actions/artifacts/101/zip'])
    self.assertEqual(options['env']['GH_TOKEN'],'OWNED_PUBLIC_TOKEN');argv=[sys.executable,'-I','-S','-B','-c','import os;os.write(1,'+repr(raw)+')'];child=actual(argv,**options);children.append(child);return child
   with patch.object(M.os,'environ',{'GH_TOKEN':'OWNED_PUBLIC_TOKEN'}),patch.object(M.subprocess,'Popen',spawn),patch.object(M.os,'killpg',side_effect=AssertionError('post-reap signal forbidden')),M.Held()as held:
    M.download(lock,path,held)
   self.assertEqual(path.read_bytes(),raw);self.assertEqual(children[0].returncode,0)
 def test_real_owned_overflow_is_stopped_before_extra_disk_byte_and_reaped(self):
  with tempfile.TemporaryDirectory(prefix='.owned-fetch-',dir=ROOT)as tmp:
   path=Path(tmp)/'archive';lock={'artifact':{'id':101,'bytes':8,'sha256':'0'*64}};actual=subprocess.Popen;children=[];signals=[];kill=os.killpg
   def spawn(argv,**options):
    child=actual([sys.executable,'-I','-S','-B','-c','import os,time;os.write(1,b"a"*9);time.sleep(2)'],**options);children.append(child);return child
   def signal_owned(pid,sig):
    self.assertEqual(pid,children[0].pid);self.assertIsNone(children[0].returncode);os.waitid(os.P_PID,pid,os.WEXITED|os.WNOHANG|os.WNOWAIT);signals.append(pid);return kill(pid,sig)
   with patch.object(M.os,'environ',{'GH_TOKEN':'OWNED_PUBLIC_TOKEN'}),patch.object(M.subprocess,'Popen',spawn),patch.object(M.os,'killpg',signal_owned),self.assertRaisesRegex(RuntimeError,'artifact_bound'),M.Held()as held:
    M.download(lock,path,held)
   self.assertLessEqual(path.stat().st_size,8);self.assertEqual(signals,[children[0].pid]);self.assertIsNotNone(children[0].returncode)


@unittest.skipUnless(os.geteuid()==os.getegid()==0,'requires genuine Root-owned directories; no owner spoof')
class TarManDirectoryTests(unittest.TestCase):
 def setUp(self):
  temp=tempfile.TemporaryDirectory(prefix='.owned-tar-man-',dir=ROOT);self.addCleanup(temp.cleanup);self.root=Path(temp.name)
 def directory(self,name='man1',mode=0o755):
  path=self.root/name;path.mkdir();path.chmod(mode);self.assertEqual((path.stat().st_uid,path.stat().st_gid),(0,0));return path
 def test_actual_0755_is_noop_without_chmod(self):
  path=self.directory();before=M.identity(path.stat())
  with patch.object(M.os,'fchmod',side_effect=AssertionError('0755 must be a no-op')),M.Held()as held:
   row=M._TarManDirectory(path);held.files.append(row);row.normalize();held.check()
  self.assertEqual(M.identity(path.stat()),before)
 def test_actual_0775_chmod_preserves_inode_and_all_other_stable_fields(self):
  path=self.directory(mode=0o775);before=M.identity(path.stat());actual=os.fchmod;calls=[];fds=[]
  def chmod(fd,mode):
   self.assertEqual(M.identity(os.fstat(fd)),before);self.assertEqual(mode,0o755);calls.append(fd);return actual(fd,mode)
  with patch.object(M.os,'fchmod',chmod),M.Held()as held:
   row=M._TarManDirectory(path);held.files.append(row);fds=[row.fd]+[fd for fd,_ in row.ancestors];row.normalize();held.check()
  after=M.identity(path.stat());self.assertEqual(len(calls),1);self.assertEqual(after[:2],before[:2]);self.assertEqual(after[2],0o40755);self.assertEqual(after[3:8],before[3:8]);self.assertGreaterEqual(after[8],before[8])
  for fd in fds:
   with self.assertRaises(OSError):os.fstat(fd)
 def test_symlink_and_every_other_original_mode_refuse_without_chmod(self):
  target=self.directory('target');link=self.root/'link';link.symlink_to(target)
  with patch.object(M.os,'fchmod',side_effect=AssertionError('unadmitted mutation')):
   with self.assertRaises(RuntimeError):M._TarManDirectory(link)
   for mode in(0o700,0o750,0o777,0o1755,0o2755):
    path=self.directory('mode-'+str(mode),mode)
    with self.subTest(mode=mode),self.assertRaisesRegex(RuntimeError,'tar_man_directory'):M._TarManDirectory(path)
 def test_actual_foreign_gid_refuses_without_owner_spoof(self):
  path=self.directory()
  try:os.chown(path,0,1)
  except OSError as error:self.skipTest('actual alternate GID unavailable: errno '+str(error.errno))
  self.assertEqual(path.stat().st_gid,1)
  with patch.object(M.os,'fchmod',side_effect=AssertionError('foreign owner mutation')),self.assertRaisesRegex(RuntimeError,'tar_man_directory'):M._TarManDirectory(path)
 def test_drift_before_transition_is_not_adopted(self):
  path=self.directory(mode=0o775)
  with self.assertRaisesRegex(RuntimeError,'tar_man_directory'),M.Held()as held:
   row=M._TarManDirectory(path);held.files.append(row);path.chmod(0o777)
   with patch.object(M.os,'fchmod',side_effect=AssertionError('changed object mutation')):row.normalize()
 def test_chmod_cannot_adopt_unrelated_mtime_change(self):
  path=self.directory(mode=0o775);actual=os.fchmod
  def chmod(fd,mode):
   actual(fd,mode);before=os.fstat(fd);os.utime(fd,ns=(before.st_atime_ns,before.st_mtime_ns+1))
  with patch.object(M.os,'fchmod',chmod),self.assertRaisesRegex(RuntimeError,'tar_man_directory'),M.Held()as held:
   row=M._TarManDirectory(path);held.files.append(row);row.normalize()
 def test_actual_parent_swap_refuses_with_all_descriptors_closed(self):
  parent=self.directory('parent');path=parent/'man1';path.mkdir();path.chmod(0o775);fds=[]
  with self.assertRaises(RuntimeError),M.Held()as held:
   row=M._TarManDirectory(path);held.files.append(row);fds=[row.fd]+[fd for fd,_ in row.ancestors]
   parent.rename(self.root/'retired');parent.mkdir();(parent/'man1').mkdir();row.normalize()
  for fd in fds:
   with self.assertRaises(OSError):os.fstat(fd)
 def test_last_original_source_physical_close_drift_refuses(self):
  path=self.directory(mode=0o775);source=self.root/'source';source.write_bytes(b'OWNED_PUBLIC');close=os.close;changed=[];fds=[]
  def closing(fd):
   chosen=os.fstat(fd).st_ino==source.stat().st_ino;close(fd)
   if chosen:path.chmod(0o775);changed.append(True)
  with patch.object(M.os,'close',closing),self.assertRaisesRegex(RuntimeError,'tar_man_directory'),M.Held()as held:
   held.read(source,65536);row=M._TarManDirectory(path);held.files.append(row);fds=[row.fd]+[fd for fd,_ in row.ancestors];row.normalize()
  self.assertEqual(changed,[True])
  for fd in fds:
   with self.assertRaises(OSError):os.fstat(fd)
 def test_ancestor_actual_physical_close_drift_refuses(self):
  path=self.directory(mode=0o775);close=os.close;chosen=[];changed=[]
  def closing(fd):
   close(fd)
   if chosen and fd==chosen[0]:path.chmod(0o775);changed.append(True)
  with patch.object(M.os,'close',closing),self.assertRaisesRegex(RuntimeError,'tar_man_directory'),M.Held()as held:
   row=M._TarManDirectory(path);held.files.append(row);chosen.append(row.ancestors[-1][0]);row.normalize()
  self.assertEqual(changed,[True])
 def test_general_parent_guard_still_refuses_group_writable_directories(self):
  path=self.directory(mode=0o775)
  with self.assertRaisesRegex(RuntimeError,'parent'):M.parents(path/'unread-public-file')
 def test_production_paths_are_fixed_and_both_admitted_before_first_chmod(self):
  events=[]
  class OwnedModel:
   def __init__(self,path):events.append(('admit',str(path)))
   def normalize(self):
    self_test.assertEqual(events[:2],[('admit','/usr/share/man/man1'),('admit','/usr/share/man/man8')]);events.append(('normalize',))
   def check(self):events.append(('check',))
   def close(self):pass
   def named(self):pass
  self_test=self
  with patch.object(M,'_TarManDirectory',OwnedModel),M.Held()as held:M.normalize_tar_man_dirs(held)
  self.assertEqual([row for row in events if row==('normalize',)],[('normalize',),('normalize',)])


if __name__=='__main__':unittest.main()
