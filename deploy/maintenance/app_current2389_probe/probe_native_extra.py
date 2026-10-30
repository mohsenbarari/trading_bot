"""Read-only native payload proof in a disposable immutable CI image only.

Default CLI is unarmed. The outer controller proves image/namespace identity;
the manifest must be complete, source-admitted, sealed and explicitly armed.
"""
from hashlib import sha256
from pathlib import Path,PurePosixPath
import argparse,errno,json,math,os,re,signal,stat,time
MAX_JSON=4*1024**2;MAX_BYTES=2*1024**3;MAX_FILES=16384;MAX_SECONDS=60
NATIVE_SCOPE='all_ELF_executable_scripts_and_ncurses_terminfo_with_native_aliases_and_one_exact_distribution_filtered_doc_absence'
FILTERED_ABSENCE = {'schema': 'exact_distribution_filtered_documentation_absence/1', 'image_id': 'sha256:2389c900dbbf520bd7d7f723e8c48a5c258f1612c43e80d2b66c6d046f9f8434', 'package': 'passwd', 'installed_version': '1:4.17.4-2', 'source_package': 'shadow', 'source_version': '1:4.17.4', 'source_release': '2', 'deb_sha256': 'c5eaef41c6947d2888fa3462f787a7e02158b997bd0119154283f425ba57b8b8', 'original_deb_file': {'path': '/usr/share/doc/passwd/examples/passwd.expire.cron', 'bytes': 1816, 'sha256': '77e169c74ccd5bfff5c5d3c6504353abd06aacef875dd0b24a80d70ff39a1474', 'mode': 33261, 'uid': 0, 'gid': 0}, 'policy_file': {'bytes': 3501, 'gid': 0, 'mode': 33188, 'path': '/etc/dpkg/dpkg.cfg.d/docker', 'sha256': '050ba34b74480e1cc00dd774153e729e76553b0af595e0d517fddc95b855d4ce', 'uid': 0}, 'policy_origin_layer': 'sha256:a70f6272827f64e1427d048ced77491aab5d64ab95725e56920802ff391197d7', 'frozen_recipe': {'files': [{'bytes': 5812, 'path': 'Dockerfile', 'sha256': '54447fdee488e17246238796cb5e6778a382227a51399256da0b22eeb147178e'}, {'bytes': 27954, 'path': 'scripts/prepare_app_security_os_cache.py', 'sha256': '3db14bd37f089baa60d34896953fc7ec6ff2107395ce451767883fc5bacc0855'}, {'bytes': 54258, 'path': 'scripts/compose_security_os_candidate.py', 'sha256': 'a7ba6515937e7ae8193be4d550547651fe5b313399ec3a1f7634efe513e151c1'}], 'input_signature': 'cc637b44fad85351bcb5675fbc28646dd1f30a15aeaae8d66c8bedac08f9f7ba', 'source_commit': '4df2fed29859c0b82367666521f1a345f269f58a', 'source_tree': '499e157c3d1d1d2d35f17ff83829477e34849f17'}, 'policy_audit': {'bytes': 1421, 'sha256': 'fc6529866aaed36eb446baf3781ffa34c62e791a36df59e7aeddf8866ce2f195'}, 'status': 'CURRENT_SOURCE_DRAFT_REQUIRES_ROOT_REVIEW', 'causal_inference_only': True, 'filesystem_ENOENT_measured': False, 'current_flat_export_absence_measured': False, 'generic_documentation_waiver': False, 'security_accepted': False}
BINDING_ARMED=True
EXPECTED_NATIVE_FILES=25
EXPECTED_HISTORICAL_NATIVE_FILES=26
EXPECTED_ALIAS_COUNT=2
EXPECTED_EXTRA_DEBS={'sed': '7071270ed4f6adda55bc4f926347fb847bacaffbdee3dff917bc6006ed7e3775', 'passwd': 'c5eaef41c6947d2888fa3462f787a7e02158b997bd0119154283f425ba57b8b8', 'libattr1': '606b5ee12ea2786be607a17f40c1fb5e65c76ceaff66665bdf8f8c6c1b71d1fb'}
EXTRA_POLICY_FILE={'path': '/etc/xattr.conf', 'bytes': 681, 'sha256': '0fc794a9826011c88b118c5ff4e30dfcbebd73518e64b0cda7aaec3ad7e578bd', 'mode': 33188, 'uid': 0, 'gid': 0}
PHASE='bootstrap'
PUBLIC_CODES=frozenset({'manifest_unarmed_or_incomplete', 'native_hardlink_target', 'native_fd_changed', 'manifest_counts', 'native_alias_parent', 'manifest_nonfinite', 'duplicate_manifest_key', 'manifest_bound', 'native_deadline', 'native_alias_open', 'actual_container_root_required', 'manifest_file', 'expectation_changed', 'native_open_identity', 'native_parent', 'manifest_identity', 'native_named_changed', 'expectation_file', 'native_payload_hash', 'native_output_bound', 'native_metadata', 'native_file_bound', 'manifest_shape', 'manifest_files', 'manifest_alias', 'manifest_package', 'native_alias_changed', 'native_parent_changed', 'native_alias_target', 'native_read_bound', 'manifest_inventory','native_absence_contract','native_absence_present','native_absence_lookup','native_absence_parent','native_absence_parent_changed'})
def need(value,code):
 if not value:raise ValueError(code)
def identity(row):return (row.st_dev,row.st_ino,row.st_mode,row.st_uid,row.st_gid,row.st_nlink,row.st_size,row.st_mtime_ns,row.st_ctime_ns)
def parse(raw):
 need(type(raw)is bytes and 0<len(raw)<=MAX_JSON,'manifest_bound')
 def unique(pairs):
  value={}
  for key,item in pairs:need(key not in value,'duplicate_manifest_key');value[key]=item
  return value
 def finite(raw):
  value=float(raw);need(math.isfinite(value),'manifest_nonfinite');return value
 return json.loads(raw,object_pairs_hook=unique,parse_float=finite,parse_constant=lambda _:need(False,'manifest_nonfinite'))
def path_allowed(value):
 return type(value)is str and 0<len(value)<=4096 and str(PurePosixPath(value))==value and '..'not in PurePosixPath(value).parts and value.startswith(('/usr/','/lib/','/lib64/','/bin/','/sbin/','/etc/terminfo/'))
def named(path,old,parents):
 need(path.resolve(strict=True)==path and identity(path.lstat())==old,'native_named_changed')
 need(all(p.resolve(strict=True)==p and identity(p.lstat())[:5]==before for p,before in parents),'native_parent_changed')
def file_hold(path,expected,held,end,total):
 parents=[]
 for parent in path.parents:
  row=parent.lstat();need(parent.resolve(strict=True)==parent and stat.S_ISDIR(row.st_mode)and row.st_uid==row.st_gid==0 and not row.st_mode&0o022,'native_parent')
  parents.append((parent,identity(row)[:5]))
 before=identity(path.lstat());need(stat.S_ISREG(before[2])and before[2:5]==(expected['mode'],expected['uid'],expected['gid'])
  and before[5]>=1 and before[6]==expected['bytes'],'native_metadata')
 named(path,before,parents);fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC|os.O_NONBLOCK)
 held.append((path,fd,before,parents));need(identity(os.fstat(fd))==before,'native_open_identity')
 digest=sha256();count=0
 while True:
  need(time.monotonic()<end,'native_deadline');part=os.read(fd,min(65536,expected['bytes']-count+1))
  if not part:break
  count+=len(part);total[0]+=len(part);need(count<=expected['bytes']and total[0]<=MAX_BYTES,'native_read_bound');digest.update(part)
 need(count==expected['bytes']and digest.hexdigest()==expected['sha256'],'native_payload_hash')
 need(identity(os.fstat(fd))==before,'native_fd_changed');named(path,before,parents)
 return dict(expected)

def same(left,right):
 if type(left)is not type(right):return False
 if type(left)is dict:return set(left)==set(right)and all(same(left[k],right[k])for k in left)
 if type(left)is list:return len(left)==len(right)and all(same(a,b)for a,b in zip(left,right))
 return left==right
class Absent:
 """Exact ENOENT under held canonical Root-owned nonwritable prefix dirs.

 The first absent component is fixed at admission. A newly created component,
 any symlink, permission/lookup error or renamed/replaced prefix refuses.
 Existing dirs retain genuine O_PATH FDs until physical finalization.
 """
 def __init__(self,path):
  need(path.is_absolute()and len(str(path))<=4096 and '..'not in path.parts and str(path)==str(PurePosixPath(path)),'native_absence_contract')
  self.path=path;self.dirs=[];self.closed=set();self.missing=None
  try:
   current=Path('/');before=identity(current.lstat())[:5];fd=os.open(current,os.O_PATH|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC)
   self.admit(current,fd,before)
   for component in path.parts[1:]:
    try:row=os.stat(component,dir_fd=fd,follow_symlinks=False)
    except OSError as error:
     need(error.errno==errno.ENOENT,'native_absence_lookup');self.missing=(current,fd,component);break
    need(stat.S_ISDIR(row.st_mode)and row.st_uid==row.st_gid==0 and not row.st_mode&0o022,'native_absence_parent')
    current=current/component
    try:next_fd=os.open(component,os.O_PATH|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=fd)
    except OSError:raise ValueError('native_absence_parent')from None
    self.admit(current,next_fd,identity(row)[:5]);fd=next_fd
   need(self.missing is not None,'native_absence_present');self.check()
  except BaseException:
   self.cleanup();raise
 def admit(self,path,fd,expected):
  row=identity(os.fstat(fd));self.dirs.append((path,fd,row[:5]))
  need(stat.S_ISDIR(row[2])and row[3]==row[4]==0 and not row[2]&0o022
   and row[:5]==expected and path.resolve(strict=True)==path and identity(path.lstat())[:5]==row[:5],'native_absence_parent')
 def named(self):
  for path,_,before in self.dirs:
   need(path.resolve(strict=True)==path and identity(path.lstat())[:5]==before,'native_absence_parent_changed')
  if self.missing is not None:
   parent,_,component=self.missing
   try:os.stat(parent/component,follow_symlinks=False)
   except OSError as error:need(error.errno==errno.ENOENT,'native_absence_lookup')
   else:raise ValueError('native_absence_present')
 def check(self):
  self.named()
  for _,fd,before in self.dirs:
   if fd not in self.closed:need(identity(os.fstat(fd))[:5]==before,'native_absence_parent_changed')
  if self.missing is not None and self.missing[1]not in self.closed:
   _,fd,component=self.missing
   try:os.stat(component,dir_fd=fd,follow_symlinks=False)
   except OSError as error:need(error.errno==errno.ENOENT,'native_absence_lookup')
   else:raise ValueError('native_absence_present')
 def close(self):
  for _,fd,_ in reversed(self.dirs):
   self.check()
   if fd not in self.closed:self.closed.add(fd);os.close(fd)
   self.check()
  self.named()
 def cleanup(self):
  for _,fd,_ in reversed(self.dirs):
   if fd not in self.closed:
    self.closed.add(fd)
    try:os.close(fd)
    except OSError:pass
 def observation(self,root):
  parent,_,component=self.missing
  return {'contract':FILTERED_ABSENCE,'status':'distribution_filtered_absence_verified','lookup_errno':'ENOENT',
   'canonical_existing_prefix':'/'+str(parent.relative_to(root))if parent!=root else '/',
   'first_missing_component':component,'all_existing_prefix_directories_held_until_physical_finalization':True,
   'policy_file_hash_verified':True,'security_accepted':False}

def observe(raw,*,image,commit,verifier,root=Path('/')):
 global PHASE
 PHASE='manifest'
 need(BINDING_ARMED is True and type(FILTERED_ABSENCE)is dict and type(EXPECTED_NATIVE_FILES)is int and 0<EXPECTED_NATIVE_FILES<=MAX_FILES and type(EXPECTED_HISTORICAL_NATIVE_FILES)is int and EXPECTED_HISTORICAL_NATIVE_FILES==EXPECTED_NATIVE_FILES+1 and type(EXPECTED_ALIAS_COUNT)is int and 0<=EXPECTED_ALIAS_COUNT<=MAX_FILES and type(EXPECTED_EXTRA_DEBS)is dict and set(EXPECTED_EXTRA_DEBS)=={'sed','passwd','libattr1'},'manifest_unarmed_or_incomplete')
 value=parse(raw)
 need(type(value)is dict and set(value)=={'schema','armed','scope','native_scope','packages','aliases','original_package_lock_sha256','missing_package_sources','native_file_count','alias_count','actual_installed_payload_measured','security_accepted','historical_native_file_count','distribution_filtered_absences'},'manifest_shape')
 need(value['schema']=='image_native_payload_expectation/2'and value['armed']is True
  and value['missing_package_sources']==[]and value['native_scope']==NATIVE_SCOPE and value['security_accepted']is False,'manifest_unarmed_or_incomplete')
 scope=value['scope'];need(type(scope)is dict and scope.get('image_id')==image and scope.get('source_commit')==commit and scope.get('platform')=='linux/amd64'
  and type(verifier)is str and re.fullmatch('[a-f0-9]{64}',verifier),'manifest_identity')
 need(type(value['packages'])is list and 0<len(value['packages'])<=128 and type(value['aliases'])is list and len(value['aliases'])<=MAX_FILES,'manifest_inventory')
 need(same(value['distribution_filtered_absences'],[FILTERED_ABSENCE])and type(value['native_file_count'])is int and value['native_file_count']==EXPECTED_NATIVE_FILES
  and type(value['historical_native_file_count'])is int and value['historical_native_file_count']==EXPECTED_HISTORICAL_NATIVE_FILES
  and scope.get('image_id')==FILTERED_ABSENCE['image_id']and all(scope.get(k)==v for k,v in FILTERED_ABSENCE['frozen_recipe'].items()if k!='files'),'native_absence_contract')
 end=time.monotonic()+MAX_SECONDS;held=[];alias_held=[];total=[0];packages=[];count=0;historical_count=0;all_expected={};package_names=set();aliases=[];closed=False;closed_fds=set();absence=None;negative=[];policy_total=[0]
 def close_once(fd):
  if fd not in closed_fds:
   closed_fds.add(fd);os.close(fd)
   try:os.fstat(fd)
   except OSError as error:need(error.errno==errno.EBADF,'native_fd_changed')
   else:raise ValueError('native_fd_changed')
 try:
  PHASE='distribution_policy'
  policy=FILTERED_ABSENCE['policy_file'];file_hold(root/policy['path'].lstrip('/'),policy,held,end,policy_total)
  file_hold(root/'etc/xattr.conf',EXTRA_POLICY_FILE,held,end,policy_total)
  PHASE='native_files'
  for package in value['packages']:
   need(type(package)is dict and set(package)=={'package','installed_version','source_package','source_version','source_release','deb_sha256','expected_files'},'manifest_package')
   need(all(type(package[key])is str and 0<len(package[key])<=512 for key in ('package','installed_version','source_package','source_version','source_release','deb_sha256'))
    and re.fullmatch('[a-f0-9]{64}',package['deb_sha256'])and package['package']not in package_names,'manifest_package');package_names.add(package['package'])
   files=package['expected_files'];need(type(files)is list and 0<len(files)<=8192,'manifest_files');actual=[]
   for file in files:
    need(type(file)is dict and set(file)=={'path','bytes','sha256','mode','uid','gid'}and path_allowed(file['path'])and file['path']not in all_expected
     and type(file['bytes'])is int and 0<file['bytes']<=128*1024**2 and type(file['sha256'])is str and re.fullmatch('[a-f0-9]{64}',file['sha256'])
     and type(file['mode'])is int and 0<=file['mode']<=0o177777 and file['mode']&0o170000==stat.S_IFREG
     and type(file['uid'])is int and 0<=file['uid']<=65535 and type(file['gid'])is int and 0<=file['gid']<=65535,'manifest_file')
    historical_count+=1;need(historical_count<=MAX_FILES,'native_file_bound');all_expected[file['path']]=file
    if file['path']==FILTERED_ABSENCE['original_deb_file']['path']:
     need(same(file,FILTERED_ABSENCE['original_deb_file'])and all(same(package[k],FILTERED_ABSENCE[k])for k in ('package','installed_version','source_package','source_version','source_release','deb_sha256'))and absence is None,'native_absence_contract')
     PHASE='distribution_absence';absence=Absent(root/file['path'].lstrip('/'));negative.append(absence.observation(root));PHASE='native_files';continue
    count+=1
    actual.append(file_hold(root/file['path'].lstrip('/'),file,held,end,total))
   packages.append({**package,'actual_files':actual})
  need({p['package']:p['deb_sha256']for p in value['packages']}==EXPECTED_EXTRA_DEBS and len(value['aliases'])==EXPECTED_ALIAS_COUNT,'manifest_package')
  PHASE='native_aliases';seen=set()
  for alias in value['aliases']:
   need(type(alias)is dict and set(alias)=={'path','target','link_text','kind','resolved_native_target'}and path_allowed(alias['path'])
    and alias['path']not in seen and alias['path']not in all_expected and alias['resolved_native_target']in all_expected
    and alias['resolved_native_target']!=FILTERED_ABSENCE['original_deb_file']['path']and alias['kind']in {'symlink','hardlink'},'manifest_alias');seen.add(alias['path'])
   path=root/alias['path'].lstrip('/');target=root/alias['resolved_native_target'].lstrip('/');parents=[]
   for parent in path.parents:
    row=parent.lstat();need(parent.resolve(strict=True)==parent and stat.S_ISDIR(row.st_mode)and row.st_uid==row.st_gid==0 and not row.st_mode&0o022,'native_alias_parent');parents.append((parent,identity(row)[:5]))
   before=identity(path.lstat());fd=os.open(path,os.O_PATH|os.O_NOFOLLOW|os.O_CLOEXEC);alias_held.append((path,fd,before,parents))
   need(identity(os.fstat(fd))==before==identity(path.lstat()),'native_alias_open')
   if alias['kind']=='symlink':need(stat.S_ISLNK(before[2])and os.readlink(path)==alias['link_text']and path.resolve(strict=True)==target,'native_alias_target')
   else:need(stat.S_ISREG(before[2])and (before[0],before[1])==(target.stat().st_dev,target.stat().st_ino),'native_hardlink_target')
   aliases.append((path,before,alias))
  need(count==value['native_file_count']and historical_count==value['historical_native_file_count']and len(negative)==1 and absence is not None and len(aliases)==value['alias_count'],'manifest_counts')
  PHASE='physical_finalization'
  for path,fd,before,parents in reversed(held):
   need(identity(os.fstat(fd))==before,'native_fd_changed');named(path,before,parents);absence.check();close_once(fd);absence.check()
  for path,fd,before,parents in reversed(alias_held):
   need(identity(os.fstat(fd))==before==identity(path.lstat())and all(p.resolve(strict=True)==p and identity(p.lstat())[:5]==old for p,old in parents),'native_alias_changed');absence.check();close_once(fd);absence.check()
  absence.close();absence.named();closed=True
  for path,fd,before,parents in held:named(path,before,parents)
  for path,fd,before,parents in alias_held:need(identity(path.lstat())==before and all(p.resolve(strict=True)==p and identity(p.lstat())[:5]==old for p,old in parents),'native_alias_changed')
  absence.named();need(time.monotonic()<end and total[0]+policy_total[0]<=MAX_BYTES,'native_deadline')
  return {'schema':'image_native_payload_observation/2','scope':scope,'verifier_sha256':verifier,'expectation_sha256':sha256(raw).hexdigest(),
   'native_scope':NATIVE_SCOPE,'inventory_complete':True,'packages':packages,'aliases_verified':len(aliases),
   'native_files_verified':count,'distribution_filtered_files_verified':len(negative),'historical_native_files_covered':historical_count,
   'distribution_filtered_absences':negative,'policy_files_verified':2,'additional_policy_files':[EXTRA_POLICY_FILE],'policy_bytes_hashed':policy_total[0],'bytes_hashed':total[0],'production_mounts_or_data_used':False,'scanner_findings_modified':False,'security_accepted':False}
 finally:
  if not closed:
   if absence is not None:absence.cleanup()
   for _,fd,_,_ in reversed(held+alias_held):
    try:close_once(fd)
    except OSError:pass
def main(argv=None):
 global PHASE
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--collect-expected-native-payload',action='store_true')
 parser.add_argument('--image-id');parser.add_argument('--source-commit');parser.add_argument('--verifier-sha256');parser.add_argument('--expectation-sha256');parser.add_argument('--expectation-gid',type=int);args=parser.parse_args(argv)
 if not args.collect_expected_native_payload:
  print(json.dumps({'status':'UNARMED','collection_performed':False,'security_accepted':False}));return 0
 need(os.geteuid()==os.getegid()==0,'actual_container_root_required')
 PHASE='expectation'
 # This is the hosted runner's readonly public bind, not a root-owned image
 # payload. Its UID is the admitted hosted1001; GID is observed by the caller.
 path=Path('/proof/native-extra-expectation.json');before=identity(path.lstat());need(type(args.expectation_gid)is int and 0<=args.expectation_gid<=65535
  and before[2:7]==(stat.S_IFREG|0o444,1001,args.expectation_gid,1,before[6])and 0<before[6]<=MAX_JSON,'expectation_file')
 fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC|os.O_NONBLOCK)
 def expired(*_):raise ValueError('native_deadline')
 signal.signal(signal.SIGALRM,expired);signal.alarm(MAX_SECONDS+5)
 try:
  raw=os.pread(fd,before[6]+1,0);need(len(raw)==before[6]and sha256(raw).hexdigest()==args.expectation_sha256 and identity(os.fstat(fd))==before==identity(path.lstat()),'expectation_changed')
  result=observe(raw,image=args.image_id,commit=args.source_commit,verifier=args.verifier_sha256)
  need(identity(os.fstat(fd))==before==identity(path.lstat()),'expectation_changed')
 finally:os.close(fd);signal.alarm(0)
 need(identity(path.lstat())==before,'expectation_changed');encoded=(json.dumps(result,sort_keys=True,separators=(',',':'))+'\n').encode();need(len(encoded)<=4*1024**2,'native_output_bound')
 print(encoded.decode(),end='');return 0
def failure(error):
 code=error.args[0]if type(error)is ValueError and len(error.args)==1 and type(error.args[0])is str and error.args[0]in PUBLIC_CODES else 'unclassified_native_refusal'
 return {'status':'REFUSED','code':code,'phase':PHASE if PHASE in {'bootstrap','manifest','expectation','native_files','native_aliases','physical_finalization','distribution_policy','distribution_absence'}else'bootstrap','security_accepted':False}
if __name__=='__main__':
 try:raise SystemExit(main())
 except Exception as error:
  print(json.dumps(failure(error),sort_keys=True));raise SystemExit(1)from None
