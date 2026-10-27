"""Genuine Root-owned FS fixtures; no UID/stat/hash/clock result overrides.

Run as genuine Root source QA. The hosted portable preflights remain unchanged;
the actual container probe independently exercises the same guards as Root.
Synthetic files test semantics only and never represent actual package payload.
"""
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from types import ModuleType
from unittest.mock import patch
import ast,json,os,stat,tempfile,time,unittest
HERE=Path(__file__).resolve().parent
# Frozen04 canonical JSON pins: independent historical originals, no sibling/local dependency.
HISTORICAL_PACKAGES_SHA256='455e0053d5988d683c1aa69be5ebc88a29a7c08d08b2bba3056a6f2d19119c2c'
HISTORICAL_ALIASES_SHA256='9e56942f7327a264e916eb8791956cd2d9cce52347316fefd5b150b38b4009c5'
ORIGINAL_IMMUTABLE_FILES={'run_app_image_ci_smoke.py':(68163,'b2c958060d176c0098b86a44d0dc225d4494023120b3253239bef9e2f3f2acb2'),'archive_inventory.py':(16581,'cf5e665286429b02e64963620538c339f29e0839b60f97db58347a89764022c3')}
P=ModuleType('owned_native');P.__file__=str(HERE/'probe_native_payload.py');exec(compile(Path(P.__file__).read_bytes(),P.__file__,'exec'),P.__dict__)
class AbsenceTests(unittest.TestCase):
 def fixture(self,examples=False):
  self.assertEqual((os.geteuid(),os.getegid()),(0,0));tmp=tempfile.TemporaryDirectory(prefix='.owned-native-',dir=HERE);self.addCleanup(tmp.cleanup);root=Path(tmp.name);base=root/'usr/share/doc/util-linux';base.mkdir(parents=True,mode=0o755)
  if examples:(base/'examples').mkdir(mode=0o755)
  return root,base,root/P.FILTERED_ABSENCE['original_deb_file']['path'].lstrip('/')
 def hold(self,path):
  item=P.Absent(path);self.addCleanup(item.cleanup);return item
 def policy(self,root):
  path=root/P.FILTERED_ABSENCE['policy_file']['path'].lstrip('/');path.parent.mkdir(parents=True,mode=0o755);path.write_bytes((HERE/'dpkg-docker-policy.original.txt').read_bytes());path.chmod(0o644);return path
 def expectation(self,root):
  value=json.loads((HERE/'native-expectation.json').read_bytes());package=deepcopy(next(p for p in value['packages']if p['package']=='util-linux'));package['expected_files']=[deepcopy(P.FILTERED_ABSENCE['original_deb_file'])];files=[]
  for n in range(153):
   name='/usr/share/owned-native-'+str(n);path=root/name.lstrip('/');path.write_bytes(b'a');path.chmod(0o644);files.append({'path':name,'bytes':1,'sha256':sha256(b'a').hexdigest(),'mode':stat.S_IFREG|0o644,'uid':0,'gid':0})
  package['expected_files']+=files;value['packages']=[package];value['aliases']=[]
  for n in range(28):
   name='/usr/share/owned-alias-'+str(n);(root/name.lstrip('/')).symlink_to('owned-native-0');value['aliases'].append({'path':name,'target':files[0]['path'],'link_text':'owned-native-0','kind':'symlink','resolved_native_target':files[0]['path']})
  self.policy(root);return value
 def observe(self,value,root):
  raw=(json.dumps(value,sort_keys=True,separators=(',',':'))+'\n').encode();return P.observe(raw,image=value['scope']['image_id'],commit=value['scope']['source_commit'],verifier=sha256(Path(P.__file__).read_bytes()).hexdigest(),root=root)
 def test_missing_examples_and_missing_leaf_have_exact_ENOENT_and_retained_actual_dirs(self):
  for examples in(False,True):
   with self.subTest(examples=examples):
    root,base,path=self.fixture(examples);held=self.hold(path);held.check();self.assertEqual(held.missing[2],'getopt-example.bash'if examples else'examples')
    for _,fd,frame in held.dirs:self.assertEqual(P.identity(os.fstat(fd))[:5],frame)
    held.close();held.named();self.assertEqual(len(held.closed),len(held.dirs))
 def test_present_file_directory_or_symlink_never_count_as_absence(self):
  for kind in('file','directory','dangling_symlink','prefix_symlink'):
   with self.subTest(kind=kind):
    root,base,path=self.fixture(kind!='prefix_symlink')
    if kind=='file':path.write_bytes(b'example')
    elif kind=='directory':path.mkdir()
    elif kind=='dangling_symlink':path.symlink_to('does-not-exist')
    else:(base/'examples').symlink_to('does-not-exist')
    with self.assertRaises(ValueError):self.hold(path)
 def test_late_missing_component_creation_and_existing_prefix_inode_replacement_refuse(self):
  for change in('create','replace','writable'):
   with self.subTest(change=change):
    root,base,path=self.fixture();held=self.hold(path)
    if change=='create':(base/'examples').mkdir()
    elif change=='replace':base.rename(base.with_name('util-linux-old'));base.mkdir()
    else:base.chmod(0o777)
    with self.assertRaises(ValueError):held.check()
 def test_last_physical_directory_close_late_creation_is_detected(self):
  root,base,path=self.fixture();held=self.hold(path);last=held.dirs[0][1];real_close=os.close
  def close(fd):
   real_close(fd)
   if fd==last:(base/'examples').mkdir()
  with patch.object(P.os,'close',side_effect=close),self.assertRaisesRegex(ValueError,'native_absence_present'):held.close()
 def test_exact_policy_hash_and_real_same_bytes_replacement_are_guarded(self):
  root,base,path=self.fixture();policy=self.policy(root);held=[];total=[0]
  P.file_hold(policy,P.FILTERED_ABSENCE['policy_file'],held,time.monotonic()+10,total);self.assertEqual(total,[3501])
  try:
   replacement=policy.with_name('replacement');replacement.write_bytes(policy.read_bytes());replacement.chmod(0o644);replacement.replace(policy)
   with self.assertRaisesRegex(ValueError,'native_named_changed'):P.named(*[held[0][i]for i in(0,2,3)])
  finally:
   for _,fd,_,_ in held:os.close(fd)
  policy.write_bytes(b'x'*3501);held=[]
  try:
   with self.assertRaisesRegex(ValueError,'native_payload_hash'):P.file_hold(policy,P.FILTERED_ABSENCE['policy_file'],held,time.monotonic()+10,[0])
  finally:
   for _,fd,_,_ in held:os.close(fd)
 def test_full_owned153_plus_one_negative_and28_aliases_report154_covered_not154_hashed(self):
  root,base,path=self.fixture();value=self.expectation(root);result=self.observe(value,root)
  self.assertEqual((result['schema'],result['native_files_verified'],result['distribution_filtered_files_verified'],result['historical_native_files_covered'],result['aliases_verified']),('image_native_payload_observation/2',153,1,154,28));self.assertEqual((result['bytes_hashed'],result['policy_bytes_hashed']),(153,3501));self.assertEqual(sum(len(p['actual_files'])for p in result['packages']),153);self.assertFalse(result['security_accepted'])
 def test_full_observation_detects_late_creation_at_positive_payload_close(self):
  root,base,path=self.fixture();value=self.expectation(root);real_close=os.close;triggered=[]
  def close(fd):
   try:target=os.readlink('/proc/self/fd/'+str(fd))
   except OSError:target=''
   real_close(fd)
   if target==str(root/'usr/share/owned-native-152')and not triggered:triggered.append(True);(base/'examples').mkdir()
  with patch.object(P.os,'close',side_effect=close),self.assertRaisesRegex(ValueError,'native_absence_present'):self.observe(value,root)
  self.assertEqual(triggered,[True])
 def test_arbitrary_or_weakened_absence_contract_and_old_schema_refuse_before_FS_reads(self):
  value=json.loads((HERE/'native-expectation.json').read_bytes())
  for change in('path','sha','uid_bool','drop','oldschema','count'):
   item=deepcopy(value)
   if change=='path':item['distribution_filtered_absences'][0]['original_deb_file']['path']='/usr/bin/anything'
   elif change=='sha':item['distribution_filtered_absences'][0]['policy_file']['sha256']='0'*64
   elif change=='uid_bool':item['distribution_filtered_absences'][0]['policy_file']['uid']=False
   elif change=='drop':item['distribution_filtered_absences']=[]
   elif change=='oldschema':item['schema']='image_native_payload_expectation/1'
   else:item['native_file_count']=154
   with self.subTest(change=change),self.assertRaises(ValueError):self.observe(item,HERE/'DOES-NOT-EXIST')
 def test_genuine_unprivileged_EACCES_is_not_misreported_as_ENOENT(self):
  root,base,path=self.fixture();readfd,writefd=os.pipe();pid=os.fork()
  if pid==0:
   os.close(readfd)
   try:
    os.setgroups([]);os.setgid(65534);os.setuid(65534)
    try:P.Absent(path)
    except ValueError as error:result=error.args[0]
    else:result='incorrect_absence_acceptance'
   except OSError:result='genuine_setuid_capability_unavailable'
   os.write(writefd,result.encode());os.close(writefd);os._exit(0)
  os.close(writefd)
  try:raw=os.read(readfd,256)
  finally:os.close(readfd);_,status=os.waitpid(pid,0)
  self.assertEqual(status,0);self.assertEqual(raw,b'native_absence_lookup','This genuine privilege-drop case needs Root capability, never UID/stat masking or skip-green.')
 def test_prior154_package_entries_aliases_and32_bodies_are_byte_exact(self):
  new=json.loads((HERE/'native-expectation.json').read_bytes())
  def canonical_sha(value):return sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()
  self.assertEqual(canonical_sha(new['packages']),HISTORICAL_PACKAGES_SHA256);self.assertEqual(canonical_sha(new['aliases']),HISTORICAL_ALIASES_SHA256);self.assertEqual(sum(len(p['expected_files'])for p in new['packages']),154);self.assertEqual(len(new['aliases']),28)
  for leaf,original in ORIGINAL_IMMUTABLE_FILES.items():
   raw=(HERE/leaf).read_bytes();self.assertEqual((len(raw),sha256(raw).hexdigest()),original)
  parsed=ast.parse((HERE/'probe_native_payload.py').read_bytes());codes={n.args[1].value for n in ast.walk(parsed)if isinstance(n,ast.Call)and isinstance(n.func,ast.Name)and n.func.id=='need'and len(n.args)>1 and isinstance(n.args[1],ast.Constant)};self.assertFalse(codes-P.PUBLIC_CODES)
if __name__=='__main__':unittest.main()
