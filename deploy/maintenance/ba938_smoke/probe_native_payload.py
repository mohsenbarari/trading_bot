"""Read-only native payload proof in a disposable immutable CI image only.

Default CLI is unarmed. The outer controller proves image/namespace identity;
the manifest must be complete, source-admitted, sealed and explicitly armed.
"""
from hashlib import sha256
from pathlib import Path,PurePosixPath
import argparse,json,math,os,re,signal,stat,time
MAX_JSON=4*1024**2;MAX_BYTES=2*1024**3;MAX_FILES=16384;MAX_SECONDS=60
NATIVE_SCOPE='all_ELF_executable_scripts_and_ncurses_terminfo_with_native_aliases'
PHASE='bootstrap'
PUBLIC_CODES=frozenset({'manifest_unarmed_or_incomplete', 'native_hardlink_target', 'native_fd_changed', 'manifest_counts', 'native_alias_parent', 'manifest_nonfinite', 'duplicate_manifest_key', 'manifest_bound', 'native_deadline', 'native_alias_open', 'actual_container_root_required', 'manifest_file', 'expectation_changed', 'native_open_identity', 'native_parent', 'manifest_identity', 'native_named_changed', 'expectation_file', 'native_payload_hash', 'native_output_bound', 'native_metadata', 'native_file_bound', 'manifest_shape', 'manifest_files', 'manifest_alias', 'manifest_package', 'native_alias_changed', 'native_parent_changed', 'native_alias_target', 'native_read_bound', 'manifest_inventory'})
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
def observe(raw,*,image,commit,verifier,root=Path('/')):
 global PHASE
 PHASE='manifest'
 value=parse(raw)
 need(type(value)is dict and set(value)=={'schema','armed','scope','native_scope','packages','aliases','original_package_lock_sha256','missing_package_sources','native_file_count','alias_count','actual_installed_payload_measured','security_accepted'},'manifest_shape')
 need(value['schema']=='image_native_payload_expectation/1'and value['armed']is True
  and value['missing_package_sources']==[]and value['native_scope']==NATIVE_SCOPE and value['security_accepted']is False,'manifest_unarmed_or_incomplete')
 scope=value['scope'];need(type(scope)is dict and scope.get('image_id')==image and scope.get('source_commit')==commit and scope.get('platform')=='linux/amd64'
  and type(verifier)is str and re.fullmatch('[a-f0-9]{64}',verifier),'manifest_identity')
 need(type(value['packages'])is list and 0<len(value['packages'])<=128 and type(value['aliases'])is list and len(value['aliases'])<=MAX_FILES,'manifest_inventory')
 end=time.monotonic()+MAX_SECONDS;held=[];alias_held=[];total=[0];packages=[];count=0;all_expected={};package_names=set();aliases=[];closed=False;closed_fds=set()
 def close_once(fd):
  if fd not in closed_fds:closed_fds.add(fd);os.close(fd)
 try:
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
    count+=1;need(count<=MAX_FILES,'native_file_bound');all_expected[file['path']]=file
    actual.append(file_hold(root/file['path'].lstrip('/'),file,held,end,total))
   packages.append({**package,'actual_files':actual})
  PHASE='native_aliases';seen=set()
  for alias in value['aliases']:
   need(type(alias)is dict and set(alias)=={'path','target','link_text','kind','resolved_native_target'}and path_allowed(alias['path'])
    and alias['path']not in seen and alias['path']not in all_expected and alias['resolved_native_target']in all_expected
    and alias['kind']in {'symlink','hardlink'},'manifest_alias');seen.add(alias['path'])
   path=root/alias['path'].lstrip('/');target=root/alias['resolved_native_target'].lstrip('/');parents=[]
   for parent in path.parents:
    row=parent.lstat();need(parent.resolve(strict=True)==parent and stat.S_ISDIR(row.st_mode)and row.st_uid==row.st_gid==0 and not row.st_mode&0o022,'native_alias_parent');parents.append((parent,identity(row)[:5]))
   before=identity(path.lstat());fd=os.open(path,os.O_PATH|os.O_NOFOLLOW|os.O_CLOEXEC);alias_held.append((path,fd,before,parents))
   need(identity(os.fstat(fd))==before==identity(path.lstat()),'native_alias_open')
   if alias['kind']=='symlink':need(stat.S_ISLNK(before[2])and os.readlink(path)==alias['link_text']and path.resolve(strict=True)==target,'native_alias_target')
   else:need(stat.S_ISREG(before[2])and (before[0],before[1])==(target.stat().st_dev,target.stat().st_ino),'native_hardlink_target')
   aliases.append((path,before,alias))
  need(count==value['native_file_count']and len(aliases)==value['alias_count'],'manifest_counts')
  PHASE='physical_finalization'
  for path,fd,before,parents in reversed(held):
   need(identity(os.fstat(fd))==before,'native_fd_changed');named(path,before,parents);close_once(fd)
  for path,fd,before,parents in reversed(alias_held):
   need(identity(os.fstat(fd))==before==identity(path.lstat())and all(p.resolve(strict=True)==p and identity(p.lstat())[:5]==old for p,old in parents),'native_alias_changed');close_once(fd)
  closed=True
  for path,fd,before,parents in held:named(path,before,parents)
  for path,fd,before,parents in alias_held:need(identity(path.lstat())==before and all(p.resolve(strict=True)==p and identity(p.lstat())[:5]==old for p,old in parents),'native_alias_changed')
  need(time.monotonic()<end,'native_deadline')
  return {'schema':'image_native_payload_observation/1','scope':scope,'verifier_sha256':verifier,'expectation_sha256':sha256(raw).hexdigest(),
   'native_scope':NATIVE_SCOPE,'inventory_complete':True,'packages':packages,'aliases_verified':len(aliases),
   'native_files_verified':count,'bytes_hashed':total[0],'production_mounts_or_data_used':False,'scanner_findings_modified':False,'security_accepted':False}
 finally:
  if not closed:
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
 path=Path('/proof/native-expectation.json');before=identity(path.lstat());need(type(args.expectation_gid)is int and 0<=args.expectation_gid<=65535
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
 return {'status':'REFUSED','code':code,'phase':PHASE if PHASE in {'bootstrap','manifest','expectation','native_files','native_aliases','physical_finalization'}else'bootstrap','security_accepted':False}
if __name__=='__main__':
 try:raise SystemExit(main())
 except Exception as error:
  print(json.dumps(failure(error),sort_keys=True));raise SystemExit(1)from None
