"""Prepare one pinned SQLite cache, or install it in an offline image build.

The tracked lock binds actual reviewed package, CI and artifact evidence.
No placeholder is accepted as an artifact identity.
"""
from contextlib import ExitStack
from hashlib import sha256
from pathlib import Path
from types import ModuleType
from zipfile import ZipFile,ZIP_STORED,ZIP_DEFLATED
import argparse,ctypes,errno,fcntl,io,json,os,platform,re,resource,selectors,shutil,signal,stat,subprocess,sys,tempfile,time

ROOT=Path(__file__).resolve().parent
LOCK_NAME='sqlite_wal_package.lock.json'
LOCK_SHA='26b14975e21b51587bd39b9749c658ac37acdb861407745639e9913f6fed03eb'
BASE_VERSION='3.46.1-7+deb13u2';VERSION=BASE_VERSION+'+gold1'
PACKAGE='libsqlite3-0_'+VERSION+'_amd64.deb'
CAPSULE=Path('/sqlite-capsule')
FETCHER=('compose_security_os_candidate.py',54267,'10ccbe216fef04e5f7a7ce2ad31ac30eca7eeae090dfaaf8074fa3cc5c789d44')
DOC_INCLUDES=('--path-include=/usr/share/doc/libsqlite3-0','--path-include=/usr/share/doc/libsqlite3-0/*')
OUTPUT_LIMITS={PACKAGE:32*1024**2,'sqlite3_'+VERSION+'_amd64.changes':4*1024**2,
 'sqlite3_'+VERSION+'_amd64.buildinfo':4*1024**2,'provenance.json':4*1024**2,
 'positive-baseline.json':65536,'positive-candidate.json':65536,'abi-contract.json':4*1024**2}
MAX_ZIP=64*1024**2;MAX_OUTPUT=2*1024**2
INVENTORY=('/usr/bin/dpkg-query','-W','-f=${binary:Package}\t${Version}\t${db:Status-Status}\n')
CODES=frozenset(('lock_unarmed','lock_contract','path','parent','parent_changed','file_metadata','file_bound','file_pin',
 'file_changed','file_closed_changed','directory','cache_inventory','cache_changed','cache_busy','cache_lock','publication',
 'archive_contract','archive_bound','archive_pin','archive_member','source_contract','image_boundary','loader_override',
 'installer_command','installer_output','installer_deadline','installer_failed','installer_descendants','installer_cleanup',
 'package_control','package_baseline','package_inventory','package_payload','installed_library','write','unexpected_refusal'))

def need(ok,code):
 if not ok:raise RuntimeError(code)
def identity(s):return (s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
def encoded(value):return (json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode()
def document(raw):
 def unique(rows):
  out={}
  for key,value in rows:need(key not in out,'lock_contract');out[key]=value
  return out
 return json.loads(raw,object_pairs_hook=unique,parse_constant=lambda _:need(False,'lock_contract'))
def binding(value,maximum):
 need(type(value) is dict and set(value)=={'bytes','sha256'} and type(value['bytes']) is int
  and 0<value['bytes']<=maximum and type(value['sha256']) is str and re.fullmatch('[a-f0-9]{64}',value['sha256']),'lock_contract')
 return value
def validate_lock(value):
 need(type(value) is dict and value.get('schema')=='sqlite_wal_image_package_lock/1','lock_contract')
 need(value.get('armed') is True,'lock_unarmed')
 need(set(value)=={'schema','armed','package','outputs','artifact','source','installed_library'},'lock_contract')
 need(value['package']=={'name':'libsqlite3-0','baseline_version':BASE_VERSION,'version':VERSION,
  'architecture':'amd64','multi_arch':'same','path':PACKAGE},'lock_contract')
 outputs=value['outputs'];need(type(outputs) is dict and set(outputs) in (set(OUTPUT_LIMITS),set(OUTPUT_LIMITS)|{'ci-receipt.json'}),'lock_contract')
 for name,row in outputs.items():binding(row,OUTPUT_LIMITS.get(name,65536))
 artifact=value['artifact'];need(type(artifact) is dict and set(artifact)=={'id','run_id','bytes','sha256','members'}
  and all(type(artifact[k]) is int and artifact[k]>0 for k in ('id','run_id','members'))
  and artifact['members']==len(outputs),'lock_contract');binding({k:artifact[k] for k in ('bytes','sha256')},MAX_ZIP)
 source=value['source'];need(type(source) is dict and set(source)=={'commit','recipe_sha256','production_patch_sha256','root_build_receipt_sha256'},'lock_contract')
 need(type(source['commit']) is str and re.fullmatch('[a-f0-9]{40}',source['commit'])
  and all(type(source[k]) is str and re.fullmatch('[a-f0-9]{64}',source[k]) for k in ('recipe_sha256','production_patch_sha256','root_build_receipt_sha256'))
  and source['production_patch_sha256']=='ab1d8c75b8ed521cc5d1d0c64da66be26ca3b72d53326c28b6a86473f42ddc85','lock_contract')
 library=value['installed_library'];need(type(library) is dict and set(library)=={'path','bytes','sha256'}
  and library['path']=='/usr/lib/x86_64-linux-gnu/libsqlite3.so.0.8.6','lock_contract')
 binding({k:library[k] for k in ('bytes','sha256')},16*1024**2)
 return value
def parents(path,cache_owner=None):
 rows=[]
 for parent in path.parents:
  s=parent.lstat();sticky=parent==Path('/tmp') and s.st_uid==0 and stat.S_IMODE(s.st_mode)==0o1777
  allowed={0,os.geteuid()};allowed.update(() if cache_owner is None else (cache_owner,))
  need(parent.resolve(strict=True)==parent and stat.S_ISDIR(s.st_mode) and s.st_uid in allowed
   and (not s.st_mode&0o022 or sticky),'parent');rows.append((parent,identity(s)[:5]))
 return rows
def check_parents(rows):need(all(p.resolve(strict=True)==p and identity(p.lstat())[:5]==old for p,old in rows),'parent_changed')
class PublicFile:
 def __init__(self,path,maximum,pin=None,owner=None,cache_owner=None):
  self.path=Path(path).absolute();self.parents=parents(self.path,cache_owner);self.before=identity(self.path.lstat());self.fd=None
  need(self.path.resolve(strict=True)==self.path and stat.S_ISREG(self.before[2]) and self.before[5]==1
   and not self.before[2]&0o022 and 0<self.before[6]<=maximum
   and (owner is None or self.before[3:5]==owner),'file_metadata')
  self.fd=os.open(self.path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC)
  try:
   self.check();h=sha256();parts=[];count=0
   while True:
    block=os.read(self.fd,min(65536,self.before[6]-count+1))
    if not block:break
    count+=len(block);need(count<=self.before[6],'file_bound');h.update(block);parts.append(block)
   need(count==self.before[6],'file_bound');self.raw=b''.join(parts);self.binding={'bytes':count,'sha256':h.hexdigest()}
   need(pin is None or self.binding==pin,'file_pin');self.check()
  except BaseException:os.close(self.fd);self.fd=None;raise
 def named(self):check_parents(self.parents);need(identity(self.path.lstat())==self.before,'file_closed_changed')
 def check(self):self.named();need(identity(os.fstat(self.fd))==self.before,'file_changed')
 def close(self):
  try:self.check()
  finally:os.close(self.fd);self.fd=None
  self.named()
class Held:
 def __init__(self):self.files=[];self.final_checks=[]
 def __enter__(self):return self
 def read(self,*args,**kwargs):
  row=PublicFile(*args,**kwargs);self.files.append(row);return row
 def check(self):
  for row in self.files:row.check()
  for check in self.final_checks:check()
 def __exit__(self,*_):
  failed=None
  for row in reversed(self.files):
   try:row.close()
   except BaseException as error:failed=error
  for row in self.files:
   try:row.named()
   except BaseException as error:failed=error
  for check in self.final_checks:
   try:check()
   except BaseException as error:failed=error
  if failed is not None:raise failed
def load_lock(held):
 row=held.read(ROOT/LOCK_NAME,65536,owner=(os.geteuid(),os.getegid()))
 need(row.binding['sha256']==LOCK_SHA,'lock_contract');return validate_lock(document(row.raw))
def inventory(path,expected):
 rows=[]
 with os.scandir(path) as stream:
  for row in stream:rows.append(row.name);need(len(rows)<=len(expected),'cache_inventory')
 need(set(rows)==set(expected) and len(rows)==len(expected),'cache_inventory')
def cache_manifest(lock):return encoded({'schema':'sqlite_wal_image_package_cache/1','package':lock['package'],'binding':lock['outputs'][PACKAGE],'source':{key:lock['source'][key] for key in ('recipe_sha256','production_patch_sha256')}})
def verify_cache(cache,lock,held,owner=None):
 cache=Path(cache).absolute();ancestors=parents(cache);before=identity(cache.lstat())
 need(cache.resolve(strict=True)==cache and stat.S_ISDIR(before[2]) and stat.S_IMODE(before[2])==0o755
  and (owner is None or before[3:5]==owner),'directory');inventory(cache,{PACKAGE,'manifest.json'})
 cache_owner=before[3];pair=before[3:5]
 meta=held.read(cache/'manifest.json',65536,owner=pair,cache_owner=cache_owner)
 need(meta.raw==cache_manifest(lock),'cache_inventory')
 package=held.read(cache/PACKAGE,OUTPUT_LIMITS[PACKAGE],lock['outputs'][PACKAGE],owner=pair,cache_owner=cache_owner)
 def check():check_parents(ancestors);need(identity(cache.lstat())==before,'cache_changed');inventory(cache,{PACKAGE,'manifest.json'})
 check();held.final_checks.append(check);return package
def write_new(path,raw,mode=0o644):
 need(type(raw) is bytes and len(raw)<=MAX_ZIP,'write')
 fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,mode)
 try:
  view=memoryview(raw)
  while view:n=os.write(fd,view);need(n>0,'write');view=view[n:]
  os.fchmod(fd,mode);os.fsync(fd)
 finally:os.close(fd)
class CacheLock:
 def __init__(self,cache):
  self.path=cache.parent/('.'+cache.name+'.lock');self.parents=parents(self.path)
  self.fd=os.open(self.path,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC,0o600);self.before=identity(os.fstat(self.fd))
  try:
   need(stat.S_ISREG(self.before[2]) and self.before[3:5]==(os.geteuid(),os.getegid()) and self.before[5:7]==(1,0) and stat.S_IMODE(self.before[2])==0o600,'cache_lock');self.check()
   try:fcntl.flock(self.fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
   except OSError as error:
    if error.errno in {errno.EAGAIN,errno.EWOULDBLOCK}:raise RuntimeError('cache_busy') from None
    raise
  except BaseException:os.close(self.fd);raise
 def check(self):check_parents(self.parents);need(identity(os.fstat(self.fd))==identity(self.path.lstat())==self.before,'cache_changed')
 def __enter__(self):return self
 def __exit__(self,*_):
  try:self.check()
  finally:os.close(self.fd)
  check_parents(self.parents);need(identity(self.path.lstat())==self.before,'cache_changed')
def rename_noreplace(source,target):
 libc=ctypes.CDLL(None,use_errno=True);fn=libc.renameat2
 fn.argtypes=(ctypes.c_int,ctypes.c_char_p,ctypes.c_int,ctypes.c_char_p,ctypes.c_uint);fn.restype=ctypes.c_int
 if fn(-100,os.fsencode(source),-100,os.fsencode(target),1):raise OSError(ctypes.get_errno(),'cache publication refused')
def admit_archive(raw,lock):
 need(len(raw)==lock['artifact']['bytes'] and sha256(raw).hexdigest()==lock['artifact']['sha256'],'archive_pin')
 package=None
 with ZipFile(io.BytesIO(raw)) as archive:
  rows=archive.infolist();need(len(rows)==len(lock['outputs']) and len({r.filename for r in rows})==len(rows)
   and {r.filename for r in rows}==set(lock['outputs']),'archive_contract')
  for row in rows:
   expected=lock['outputs'][row.filename];kind=stat.S_IFMT(row.external_attr>>16)
   need(not row.is_dir() and not row.flag_bits&1 and row.compress_type in (ZIP_STORED,ZIP_DEFLATED)
    and kind in (0,stat.S_IFREG) and row.file_size==expected['bytes'] and 0<row.compress_size<=MAX_ZIP,'archive_member')
   parts=[];count=0;h=sha256()
   with archive.open(row) as source:
    while True:
     block=source.read(min(65536,expected['bytes']-count+1))
     if not block:break
     count+=len(block);need(count<=expected['bytes'],'archive_bound');h.update(block)
     if row.filename==PACKAGE:parts.append(block)
   need(count==expected['bytes'] and h.hexdigest()==expected['sha256'],'archive_pin')
   if row.filename==PACKAGE:package=b''.join(parts)
 need(package is not None,'archive_contract');return package
def download(lock,target,held):
 name,size,digest=FETCHER;row=held.read(ROOT/name,131072,{'bytes':size,'sha256':digest},owner=(os.geteuid(),os.getegid()))
 module=ModuleType('_sealed_sqlite_artifact_fetcher');module.__file__=str(ROOT/name);exec(compile(row.raw,module.__file__,'exec'),module.__dict__)
 module.fetch_artifact(lock['artifact']['id'],target,lock['artifact']['bytes'],lock['artifact']['sha256']);held.check()
def prepare(cache,artifact_zip=None,check_only=False):
 cache=Path(cache).absolute();need(cache.name=='sqlite-wal-capsule' and cache.parent.resolve(strict=True)==cache.parent,'path');parents(cache)
 with tempfile.TemporaryDirectory(prefix='.sqlite-wal-download-',dir=cache.parent) as temporary:
  with Held() as held:
   lock=load_lock(held)
   # Close/unlock this owned lock BEFORE finalizing the retained data/source
   # FDs. A physical lock close can never hide a late data mutation.
   with CacheLock(cache) as busy:
    if cache.exists():verify_cache(cache,lock,held,(os.geteuid(),os.getegid()));held.check();status='cache_verified'
    else:
     need(not check_only,'cache_inventory');stage=Path(tempfile.mkdtemp(prefix='.sqlite-wal-capsule-',dir=cache.parent));stage_id=identity(stage.lstat())[:2];published=False
     try:
      target=Path(artifact_zip).absolute() if artifact_zip else Path(temporary)/'artifact.zip'
      if artifact_zip is None:download(lock,target,held)
      archive=held.read(target,MAX_ZIP,{k:lock['artifact'][k] for k in ('bytes','sha256')},owner=(os.geteuid(),os.getegid()))
      package=admit_archive(archive.raw,lock);write_new(stage/PACKAGE,package);write_new(stage/'manifest.json',cache_manifest(lock));stage.chmod(0o755)
      # Verify the new namespace completely before its no-clobber publication;
      # open fresh held output FDs immediately after that exact owned rename.
      with Held() as staged:verify_cache(stage,lock,staged,(os.geteuid(),os.getegid()));staged.check()
      held.check();busy.check();need(identity(stage.lstat())[:2]==stage_id,'cache_changed')
      rename_noreplace(stage,cache);published=True;need(identity(cache.lstat())[:2]==stage_id,'publication')
      verify_cache(cache,lock,held,(os.geteuid(),os.getegid()));held.check();status='cache_prepared'
     finally:
      if not published and stage.exists():need(identity(stage.lstat())[:2]==stage_id,'cache_changed');shutil.rmtree(stage)
    busy.check()
 return {'status':status,'packages':1,'sqlite_package':VERSION,'security_accepted':False}

def owned(pid):
 try:return os.waitid(os.P_PID,pid,os.WEXITED|os.WNOHANG|os.WNOWAIT)
 except ChildProcessError:raise RuntimeError('installer_cleanup') from None
def children():
 fd=os.open('/proc/self/task/'+str(os.getpid())+'/children',os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
 try:raw=os.read(fd,4097)
 finally:os.close(fd)
 need(len(raw)<=4096,'installer_cleanup');values=raw.split();need(len(values)<=64 and all(v.isdigit() for v in values),'installer_cleanup');return {int(v) for v in values}
def reap_extras(except_pid=None):
 end=time.monotonic()+3;unexpected=False
 while True:
  extra=children()-({except_pid} if except_pid is not None else set())
  if not extra:break
  unexpected=True;need(time.monotonic()<end,'installer_cleanup')
  for pid in extra:
   owned(pid)
   try:os.kill(pid,signal.SIGKILL)
   except ProcessLookupError:pass
   os.waitpid(pid,0)
 return unexpected
def cleanup_child(child):
 if child.returncode is None:
  try:owned(child.pid)
  except RuntimeError:return
  try:os.killpg(child.pid,signal.SIGKILL)
  except ProcessLookupError:pass
  child.wait(timeout=3)
def bounded(argv,allowed,*,guard=lambda:None,seconds=120):
 need(tuple(argv) in allowed,'installer_command');guard()
 def limits():
  for kind,value in ((resource.RLIMIT_CORE,0),(resource.RLIMIT_FSIZE,64*1024**2),(resource.RLIMIT_CPU,240),(resource.RLIMIT_AS,512*1024**2)):
   hard=resource.getrlimit(kind)[1];value=value if hard==resource.RLIM_INFINITY else min(value,hard);resource.setrlimit(kind,(value,value))
 child=subprocess.Popen(argv,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,close_fds=True,start_new_session=True,
  env={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','LC_ALL':'C.UTF-8','DEBIAN_FRONTEND':'noninteractive'},preexec_fn=limits)
 parts=[];count=0;end=time.monotonic()+seconds
 try:
  with selectors.DefaultSelector() as select:
   fd=child.stdout.fileno();os.set_blocking(fd,False);select.register(fd,selectors.EVENT_READ)
   while True:
    guard();left=end-time.monotonic();need(left>0,'installer_deadline')
    if not select.select(min(1,left)):continue
    block=os.read(fd,min(65536,MAX_OUTPUT-count+1))
    if not block:break
    count+=len(block);need(count<=MAX_OUTPUT,'installer_output');parts.append(block)
  while owned(child.pid) is None:need(time.monotonic()<end,'installer_deadline');time.sleep(.01)
  need(not (children()-{child.pid}),'installer_descendants')
  need(child.wait(timeout=3)==0,'installer_failed');guard();return b''.join(parts)
 finally:
  active=sys.exc_info()[0] is not None;bad=False
  try:cleanup_child(child)
  except BaseException:bad=True
  try:bad=reap_extras() or bad
  except BaseException:bad=True
  child.stdout.close()
  if bad and not active:raise RuntimeError('installer_cleanup')
def commands(path):
 need(Path(path)==CAPSULE/PACKAGE,'installer_command')
 return {INVENTORY,('/usr/bin/dpkg','--audit'),('/usr/bin/dpkg','--verify','libsqlite3-0'),
  ('/usr/bin/dpkg',*DOC_INCLUDES,'--install',str(path)),
  *(('/usr/bin/dpkg-deb','-f',str(path),field) for field in ('Package','Version','Architecture','Multi-Arch'))}
def package_inventory(raw):
 need(len(raw)<=MAX_OUTPUT,'package_inventory');out={}
 for line in raw.decode('ascii').splitlines():
  parts=line.split('\t');need(len(parts)==3 and parts[0] not in out and len(parts[0])<=256 and len(parts[1])<=100
   and re.fullmatch('[a-z0-9][a-z0-9+.-]*(?::[a-z0-9]+)?',parts[0]) and parts[1] and parts[2] in {'installed','config-files'},'package_inventory')
  out[parts[0]]=(parts[1],parts[2]);need(len(out)<=4096,'package_inventory')
 need(out,'package_inventory');return out
def image_boundary(cache):
 need(os.geteuid()==os.getegid()==0 and platform.machine() in {'x86_64','amd64'} and cache==CAPSULE
  and os.statvfs(cache).f_flag&os.ST_RDONLY,'image_boundary')
 with Held() as held:
  release=held.read(Path('/etc/os-release').resolve(strict=True),65536,owner=(0,0))
  fields=dict(line.split('=',1) for line in release.raw.decode('utf-8').splitlines() if '=' in line)
  need(fields.get('ID','').strip('"')=='debian' and fields.get('VERSION_ID','').strip('"')=='13'
   and fields.get('VERSION_CODENAME','').strip('"')=='trixie','image_boundary')
 need(sys.flags.isolated==sys.flags.ignore_environment==sys.flags.no_user_site==1 and not any(name.startswith('LD_') for name in os.environ),'loader_override')
 need(set(os.listdir('/sys/class/net'))=={'lo'},'image_boundary')
 # The normal Dockerfile's actual setpriv mask: CHOWN,FOWNER,FSETID,SETGID,SETUID.
 fd=os.open('/proc/self/status',os.O_RDONLY|os.O_CLOEXEC)
 try:raw=os.read(fd,65537)
 finally:os.close(fd)
 need(len(raw)<=65536,'image_boundary');rows=dict(line.split(':',1) for line in raw.decode('ascii').splitlines() if ':' in line)
 capability_contract(rows)
def capability_contract(rows):
 mask=sum(1<<bit for bit in (0,3,4,6,7))
 need(rows.get('NoNewPrivs','').strip()=='1' and int(rows['CapBnd'],16)==mask
  and int(rows['CapEff'],16)&~mask==0 and int(rows['CapPrm'],16)&~mask==0
  and int(rows['CapInh'],16)==int(rows['CapAmb'],16)==0,'image_boundary')
def install_admitted(cache):
 with Held() as held:
  lock=load_lock(held);package=verify_cache(cache,lock,held);allowed=commands(package.path)
  need(ctypes.CDLL(None,use_errno=True).prctl(36,1,0,0,0)==0,'installer_cleanup')
  before=package_inventory(bounded(list(INVENTORY),allowed,guard=held.check))
  target='libsqlite3-0:amd64';need(target in before and before[target][0] in {BASE_VERSION,VERSION} and before[target][1]=='installed','package_baseline')
  need(bounded(['/usr/bin/dpkg','--audit'],allowed,guard=held.check).strip()==b'','package_inventory')
  for field,expected in [('Package','libsqlite3-0'),('Version',VERSION),('Architecture','amd64'),('Multi-Arch','same')]:
   need(bounded(['/usr/bin/dpkg-deb','-f',str(package.path),field],allowed,guard=held.check).decode('ascii').strip()==expected,'package_control')
  bounded(['/usr/bin/dpkg',*DOC_INCLUDES,'--install',str(package.path)],allowed,guard=held.check)
  after=package_inventory(bounded(list(INVENTORY),allowed,guard=held.check));expected=dict(before);expected[target]=(VERSION,'installed')
  need(after==expected,'package_inventory')
  need(bounded(['/usr/bin/dpkg','--audit'],allowed,guard=held.check).strip()==b'' and bounded(['/usr/bin/dpkg','--verify','libsqlite3-0'],allowed,guard=held.check).strip()==b'','package_payload')
  library=lock['installed_library'];held.read(Path(library['path']),16*1024**2,{k:library[k] for k in ('bytes','sha256')},owner=(0,0))
  held.check()
 return {'status':'image_package_installed','packages':1,'sqlite_package':VERSION,'security_accepted':False}
def install(cache):
 cache=Path(cache);image_boundary(cache)
 def expired(*_):raise RuntimeError('installer_deadline')
 previous=signal.signal(signal.SIGALRM,expired);signal.alarm(600)
 try:return install_admitted(cache)
 finally:signal.alarm(0);signal.signal(signal.SIGALRM,previous)
def main(argv=None):
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--cache-dir',type=Path,required=True);parser.add_argument('--artifact-zip',type=Path)
 mode=parser.add_mutually_exclusive_group();mode.add_argument('--check',action='store_true');mode.add_argument('--install-image-cache',action='store_true')
 try:
  args=parser.parse_args(argv);need(not args.artifact_zip or not (args.check or args.install_image_cache),'path')
  result=install(args.cache_dir) if args.install_image_cache else prepare(args.cache_dir,args.artifact_zip,args.check)
 except Exception as error:
  code=error.args[0] if type(error) is RuntimeError and len(error.args)==1 and type(error.args[0]) is str and error.args[0] in CODES else 'unexpected_refusal'
  result={'status':'refused','code':code,'security_accepted':False};print(json.dumps(result,sort_keys=True));return 1
 print(json.dumps(result,sort_keys=True));return 0
if __name__=='__main__':raise SystemExit(main())
