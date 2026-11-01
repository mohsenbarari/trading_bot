"""Pinned local Debian SQLite backport recipe; no execution on import.

Only Root/CI runs an actual build in the disposable Docker builder. The output
is a proper local-version libsqlite3-0 package, never a loader-path override.
Gold2 retains the production WAL fix and adds only the official FTS5 fix.
The same configured source must pass unfiltered standard make test and the
official FTS5 regression. No actual test/build is performed by importing this file.
"""
from hashlib import sha256
from pathlib import Path
import argparse,datetime,email.utils,json,os,re,select,selectors,shutil,signal,stat,subprocess,sys,tarfile,time,uuid
from urllib.parse import urlsplit
from urllib.request import build_opener,HTTPSHandler,ProxyHandler,Request

BASE_VERSION='3.46.1-7+deb13u2';VERSION=BASE_VERSION+'+gold2'
SOURCE_EPOCH=1793491200  # Authored2026-11-01, reproducible local changelog date.
SNAPSHOT='https://snapshot.debian.org/archive/debian/20261025T000000Z'
INRELEASE=(140421,'0584fba32e13e0ab8285fb16c27adea1ec03a73669c18702821094fd6ca86675')
SOURCE_FILES={
 'sqlite3_3.46.1-7+deb13u2.dsc':(2673,'46e1eec171e11108fc436e45309b635b601029726b6ffd3bf435bb474e511638'),
 'sqlite3_3.46.1.orig.tar.xz':(8456776,'d0cdd2ece271b29e7ce18095745d892517ee26d0f270065b3a25c2e9eb11639c'),
 'sqlite3_3.46.1.orig-www.tar.xz':(5861820,'648df41a8e532882b1905df45919aae4bafaf74c455f66bc86f1f52f45c8b8f0'),
 'sqlite3_3.46.1-7+deb13u2.debian.tar.xz':(37084,'59d33bc436258603702bb051d645743ab03612b82f79e7c7d638c65127c21261')}
PATCH='0001-wal-reset-production-only.patch'
PATCH_PIN=(6746,'ab1d8c75b8ed521cc5d1d0c64da66be26ca3b72d53326c28b6a86473f42ddc85')
FTS_PATCH='0002-fts5-detail-none-merge-bound.patch'
FTS_PATCH_PIN=(1240, '90258813c142898908fe16bdb027688249e9353289bc42fc3f7bac15d64507e8')
FTS_TEST_PATCH='0003-fts5-detail-none-upstream-regression.patch'
FTS_TEST_PATCH_PIN=(968, 'b1936d0f06461f7eb7a3517b7c56dcdfcb1606c6c52338cf50bb3b82bb2523e7')
FTS_BEFORE=(283821, 'a8cb1edfb4772d6f6d2c5fa417c9802c905e56e420832e216337dad4579455e9')
FTS_AFTER=(283845, '2a64009c24fd1194027d2383754545edf9a55c2134ce5588a22143d52bf1e495')
FTS_TEST_BEFORE=(2113, 'd4260474ddc5d374c5fc932c1251f695fcfac57c2ce2f43b3f2056e426ebab46')
FTS_TEST_AFTER=(2598, 'b03372c1a4d9b112b09a75574fcff4414d2eab431ae4d9f3bd156a648fe8d58e')
UPSTREAM_FTS_COMMIT='e1c9934f9f11a499029abb020ea715a08970da80'
VENDOR_HELPERS={'test/tester.tcl': (78000, '5c4c2881737e9b5d488ad453be74630bc8209d3ee5cf1492dc08a9a1f6166aa7'), 'ext/fts5/test/fts5_common.tcl': (17586, '7600a7adf913e45113ea66d0395de96d18dff9b187599c01151de5dd61e26e80'), 'Makefile.in': (52641, '78ebd7e89c5c95d9a8e30c39a5da99155e8d0d8cc901e36e74c4e6b20ddb0074'), 'test/veryquick.test': (587, '814df9511e0412a654e721851613f9061bbd7557c688b50d7bd57c980e4ec773')}
VENDOR_STANDARD_SCOPE={'standard_target':'test',
 'standard_prerequisites':['srctree-check','fuzztest','sourcetest','$(TESTPROGS)','tcltest'],
 'executed_standard_subtargets':['srctree-check','fuzztest','sourcetest','tcltest'],
 'default_tcl_suite':'test/veryquick.test',
 'vendor_default_omissions_scope':'upstream_veryquick_excludes_malloc_and_io_error_simulations',
 'all_fault_permutations_run':False}
RECIPE_FILES=('sourcebuild.py','Dockerfile',PATCH,FTS_PATCH,FTS_TEST_PATCH,'verify_positive.py')
VENDOR_LOG_LIMITS={'vendor-standard.log':32*1024**2,'vendor-fts5-regression.log':4*1024**2}
VENDOR_COMMANDS={'vendor-standard.log':['/usr/bin/make','test'],
 'vendor-fts5-regression.log':['./testfixture','ext/fts5/test/fts5prefix2.test']}
VERIFY_PIN=(16377, '623384cc7e63c04834d1042c63064b85a83c6966d0cc95da172f5e719f8f06ab')
WAL_BEFORE=(174376,'d4a104b5f3cb807e026a9dbcdb77519358b06b66f76cb6b6ef70e3793379dd62')
WAL_AFTER=(175230,'7287fb98dcfbfb3c33dfba555c21396f7ef53c68a325dc706e68ca3c19ab73a7')
RECIPE=Path('/recipe');INPUTS=Path('/inputs');WORK=Path('/build/sqlite-wal-backport');OUT=Path('/out')
ENV={'PATH':'/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin','HOME':'/build/home','LC_ALL':'C.UTF-8','LANG':'C.UTF-8',
 'DEBIAN_FRONTEND':'noninteractive',
 'SOURCE_DATE_EPOCH':str(SOURCE_EPOCH),'DEB_BUILD_OPTIONS':'nocheck parallel=2',
 'QUILT_PATCHES':'debian/patches','QUILT_PATCH_OPTS':'--fuzz=0'}
MAX_METADATA=4*1024**2
DOC_INCLUDES=('--path-include=/usr/share/doc/libsqlite3-0','--path-include=/usr/share/doc/libsqlite3-0/*')
PUBLIC_FAILURE_CODES=frozenset(('backup_integrity', 'backup_query_drift', 'binary_bound', 'binary_changed', 'binary_identity', 'binary_mapping', 'binary_parent', 'compile_options_bound', 'fts_contract', 'isolated_python_required', 'json_contract', 'loader_override', 'lookup_index_not_used', 'mapping_bound', 'non_system_library', 'os_metadata', 'os_metadata_bound', 'package_library_path', 'package_metadata_bound', 'package_paths_bound', 'package_payload_changed', 'package_scope', 'package_version', 'package_version_changed', 'python_customization', 'python_version_changed', 'readonly_reopen_drift', 'scratch_not_private', 'sqlite_runtime_package_mismatch', 'stdlib_extension_required', 'stdlib_sqlite_required', 'system_library_missing_or_ambiguous', 'transaction_or_query_drift', 'trixie_required', 'wal_mode', 'wal_reader', 'wal_refresh', 'wal_snapshot_drift')+('unclassified',))

def require(ok,code):
 if not ok:raise RuntimeError(code)
def identity(s):return (s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
def encoded(value):return (json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode()
def digest(path,expected=None,maximum=32*1024**2):
 path=Path(path);before=path.lstat();bound=identity(before)
 require(path.resolve(strict=True)==path and stat.S_ISREG(before.st_mode) and before.st_uid==before.st_gid==0
  and not before.st_mode&0o022 and before.st_nlink==1
  and 0<before.st_size<=maximum,'input_identity')
 fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
 try:
  require(identity(os.fstat(fd))==bound,'input_identity')
  h=sha256();count=0
  while True:
   part=os.read(fd,min(65536,maximum-count+1))
   if not part:break
   count+=len(part);require(count<=maximum,'input_bound');h.update(part)
  require(count==before.st_size and identity(os.fstat(fd))==identity(path.lstat())==bound,'input_changed')
 finally:os.close(fd)
 require(identity(path.lstat())==bound,'input_changed')
 result=(count,h.hexdigest())
 if expected is not None:require(result==expected,'input_pin')
 return {'bytes':count,'sha256':result[1]}
def bytes_at(path,expected=None,maximum=131072):
 path=Path(path);before=path.lstat();bound=identity(before)
 require(path.resolve(strict=True)==path and stat.S_ISREG(before.st_mode) and before.st_uid==before.st_gid==0
  and not before.st_mode&0o022 and before.st_nlink==1 and 0<before.st_size<=maximum
  and (expected is None or before.st_size==expected[0]),'input_identity')
 fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
 try:
  require(identity(os.fstat(fd))==bound,'input_identity');raw=os.pread(fd,before.st_size+1,0)
  require(len(raw)==before.st_size and (expected is None or sha256(raw).hexdigest()==expected[1])
   and identity(os.fstat(fd))==identity(path.lstat())==bound,'input_pin')
 finally:os.close(fd)
 require(identity(path.lstat())==bound,'input_changed');return raw
def copy_exact(source,target,expected,*,mode=0o600):
 """Copy only admitted bytes through one held source; never reopen to copy."""
 source=Path(source);target=Path(target)
 require(type(expected) is tuple and len(expected)==2 and type(expected[0]) is int
  and 0<expected[0]<=32*1024**2 and type(expected[1]) is str
  and re.fullmatch('[a-f0-9]{64}',expected[1]) and mode in (0o600,0o644),'copy_scope')
 parents=[]
 for path in (source,target):
  require(path.is_absolute() and path.parent.resolve(strict=True)==path.parent,'copy_path')
  for parent in path.parents:
   row=parent.lstat();require(stat.S_ISDIR(row.st_mode) and row.st_uid==row.st_gid==0
    and not row.st_mode&0o022 and parent.resolve(strict=True)==parent,'copy_parent')
   parents.append((parent,identity(row)[:5]))
 def check_parents():
  require(all(path.resolve(strict=True)==path and identity(path.lstat())[:5]==old
   for path,old in parents),'copy_parent_changed')
 before=identity(source.lstat())
 require(source.resolve(strict=True)==source and stat.S_ISREG(before[2]) and before[3:6]==(0,0,1)
  and not before[2]&0o022 and before[6]==expected[0],'copy_identity')
 source_fd=os.open(source,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC)
 target_fd=None;end=time.monotonic()+30
 try:
  require(identity(os.fstat(source_fd))==before,'copy_identity');check_parents()
  target_fd=os.open(target,os.O_RDWR|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,mode)
  os.fchmod(target_fd,mode);count=0;h=sha256()
  while True:
   require(time.monotonic()<end,'copy_timeout')
   part=os.read(source_fd,min(65536,expected[0]-count+1))
   if not part:break
   count+=len(part);require(count<=expected[0],'copy_bound');h.update(part);view=memoryview(part)
   while view:
    require(time.monotonic()<end,'copy_timeout');n=os.write(target_fd,view)
    require(n>0,'copy_write');view=view[n:]
  require((count,h.hexdigest())==expected,'copy_pin');os.fsync(target_fd)
  after=identity(os.fstat(target_fd))
  require(after[2:7]==(stat.S_IFREG|mode,0,0,1,expected[0])
   and identity(target.lstat())==after and target.resolve(strict=True)==target,'copy_target')
  require(identity(os.fstat(source_fd))==identity(source.lstat())==before,'copy_source_changed')
  check_parents()
 finally:
  try:
   if target_fd is not None:os.close(target_fd)
  finally:os.close(source_fd)
 check_parents()
 require(identity(source.lstat())==before and identity(target.lstat())==after,'copy_closed_changed')
 digest(target,expected,maximum=expected[0])
 check_parents();require(identity(source.lstat())==before,'copy_closed_changed')
 return {'bytes':expected[0],'sha256':expected[1]}
def publish(path,raw):
 require(type(raw) is bytes and len(raw)<=MAX_METADATA,'output_bound')
 fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
 try:
  view=memoryview(raw)
  while view:n=os.write(fd,view);require(n>0,'output_write');view=view[n:]
  os.fsync(fd)
 finally:os.close(fd)
def current_environment():
 require(not any(name.startswith('LD_') or name in {'PYTHONPATH','PYTHONHOME'} for name in os.environ),'loader_override')
 return dict(ENV)
def cleanup_owned(child):
 if child.returncode is not None:return
 # WNOWAIT verifies that this PID is still our unreaped child without freeing
 # its identity. A failed/reaped wait must never license signalling a new PID.
 try:os.waitid(os.P_PID,child.pid,os.WEXITED|os.WNOHANG|os.WNOWAIT)
 except ChildProcessError:return
 try:os.killpg(child.pid,signal.SIGKILL)
 except ProcessLookupError:pass
 child.wait(timeout=10)
def forward(raw,end):
 fd=sys.stderr.fileno();view=memoryview(raw)
 while view:
  left=end-time.monotonic();require(left>0,'output_timeout')
  ready=select.select([], [fd], [], min(1,left))[1]
  if not ready:continue
  count=os.write(fd,view[:4096]);require(count>0,'output_write');view=view[count:]
def bounded_command(argv,*,cwd,timeout,maximum,capture,with_code=False,tee=False):
 """Bound bytes BEFORE accumulation/forwarding; no payload file-size limit."""
 child=subprocess.Popen(argv,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
  env=current_environment(),cwd=cwd,close_fds=True,start_new_session=True)
 parts=[];count=0;end=time.monotonic()+timeout
 try:
  with selectors.DefaultSelector() as selector:
   fd=child.stdout.fileno();os.set_blocking(fd,False);selector.register(fd,selectors.EVENT_READ)
   while True:
    left=end-time.monotonic();require(left>0,'command_timeout')
    if not selector.select(min(1,left)):continue
    raw=os.read(fd,min(65536,maximum-count+1))
    if not raw:break
    count+=len(raw);require(count<=maximum,'command_output_bound')
    if capture:parts.append(raw)
    if not capture or tee:forward(raw,end)
  code=child.wait(timeout=max(.1,end-time.monotonic()))
  if with_code:
   require(capture,'capture_contract');return code,b''.join(parts)
  require(code==0,'build_command_refused')
  return b''.join(parts) if capture else None
 except BaseException:
  try:cleanup_owned(child)
  except BaseException:pass  # CI tears down the disposable builder cgroup.
  raise
 finally:child.stdout.close()
def metadata(argv,*,cwd=None,maximum=MAX_METADATA):
 return bounded_command(argv,cwd=cwd,timeout=30,maximum=maximum,capture=True)
def command(argv,*,cwd=None,timeout=1800):
 return bounded_command(argv,cwd=cwd,timeout=timeout,maximum=32*1024**2,capture=False)
def fetch(url,target,expected):
 """Only pinned public Debian inputs; never sends project/private content."""
 require(url.startswith(SNAPSHOT+'/') and not target.exists(),'fetch_scope')
 opener=build_opener(ProxyHandler({}),HTTPSHandler());partial=target.parent/('.public-partial-'+uuid.uuid4().hex)
 fd=os.open(partial,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
 try:
  h=sha256();count=0;end=time.monotonic()+180
  with opener.open(Request(url,headers={'User-Agent':'pinned-public-debian-source/1'}),timeout=20) as response:
   require(urlsplit(response.geturl()).hostname=='snapshot.debian.org' and urlsplit(response.geturl()).scheme=='https','fetch_redirect')
   length=response.getheader('Content-Length')
   if length is not None:require(length.isdigit() and int(length)==expected[0],'fetch_size')
   while True:
    require(time.monotonic()<end,'fetch_timeout');part=response.read1(min(65536,expected[0]-count+1))
    if not part:break
    count+=len(part);require(count<=expected[0],'fetch_size');h.update(part);view=memoryview(part)
    while view:n=os.write(fd,view);require(n>0,'fetch_write');view=view[n:]
  require((count,h.hexdigest())==expected,'fetch_pin');os.fsync(fd);os.close(fd);fd=None
  os.link(partial,target);partial.unlink()
 finally:
  if fd is not None:os.close(fd)
  if partial.exists():partial.unlink()
def inputs(root,recipe):
 proofs={leaf:digest(root/leaf,pin) for leaf,pin in SOURCE_FILES.items()}
 digest(recipe/PATCH,PATCH_PIN);digest(recipe/FTS_PATCH,FTS_PATCH_PIN);digest(recipe/FTS_TEST_PATCH,FTS_TEST_PATCH_PIN);digest(recipe/'verify_positive.py',VERIFY_PIN)
 dsc=bytes_at(root/('sqlite3_'+BASE_VERSION+'.dsc'),SOURCE_FILES['sqlite3_'+BASE_VERSION+'.dsc']).decode('ascii')
 require(('Version: '+BASE_VERSION+'\n') in dsc and 'Format: 3.0 (quilt)\n' in dsc,'source_version')
 for leaf,(size,pin) in SOURCE_FILES.items():
  if leaf.endswith('.dsc'):continue
  require((pin+' '+str(size)+' '+leaf) in dsc,'dsc_source_binding')
 patch=bytes_at(recipe/PATCH,PATCH_PIN)
 require(b'sqlite3FaultSim' not in patch and b'walrestart' not in patch,'production_patch_scope')
 return proofs
def builder_context():
 require(os.geteuid()==os.getegid()==0 and Path.cwd()==Path('/build') and RECIPE.resolve()==RECIPE,'disposable_builder_required')
 require(sys.version_info[:3]==(3,11,17) and os.uname().machine=='x86_64','build_platform')
 os_release=bytes_at(Path('/etc/os-release').resolve(strict=True),maximum=65536).decode('utf-8')
 fields=dict(row.split('=',1) for row in os_release.splitlines() if '=' in row)
 require(fields.get('ID','').strip('"')=='debian' and fields.get('VERSION_ID','').strip('"')=='13'
  and fields.get('VERSION_CODENAME','').strip('"')=='trixie','trixie_builder_only')
 current_environment()
 context=json.loads(bytes_at(RECIPE/'context-manifest.json'))
 require(type(context) is dict and set(context)=={'schema','files'} and context['schema']=='sqlite_allowlisted_context/1','build_context')
 expected={*RECIPE_FILES,*('inputs/'+name for name in SOURCE_FILES)}
 require(type(context['files']) is dict and set(context['files'])==expected,'build_context')
 for name,binding in context['files'].items():
  require(type(binding) is dict and set(binding)=={'bytes','sha256'} and type(binding['bytes']) is int
   and type(binding['sha256']) is str and re.fullmatch('[a-f0-9]{64}',binding['sha256']),'build_context')
  target=INPUTS/name.removeprefix('inputs/') if name.startswith('inputs/') else RECIPE/name
  digest(target,(binding['bytes'],binding['sha256']))
def stage_context(destination):
 recipe=Path(__file__).absolute().parent;require(recipe==Path.cwd(),'context_workdir');proofs=inputs(recipe/'inputs',recipe)
 destination=Path(destination);require(destination.is_absolute() and not destination.exists()
  and destination.parent.resolve(strict=True)==destination.parent and destination.parent.stat().st_uid==0
  and not destination.parent.stat().st_mode&0o022,'context_destination')
 files={name:digest(recipe/name) for name in RECIPE_FILES}
 files.update({'inputs/'+name:row for name,row in proofs.items()})
 destination.mkdir(mode=0o700);(destination/'inputs').mkdir(mode=0o700)
 for name,binding in files.items():
  original=recipe/name;target=destination/name
  expected=(binding['bytes'],binding['sha256']);copy_exact(original,target,expected)
 manifest={'schema':'sqlite_allowlisted_context/1','files':files};publish(destination/'context-manifest.json',encoded(manifest))
 allow='**\n!.dockerignore\n!context-manifest.json\n'+''.join('!'+name+'\n' for name in RECIPE_FILES)
 allow+='!inputs/\n'+''.join('!inputs/'+name+'\n' for name in SOURCE_FILES)
 publish(destination/'.dockerignore',allow.encode())
 return {'schema':'sqlite_public_context_staged/1','context':str(destination),'files':files,'build_performed':False,'security_accepted':False}
def configure_repository():
 builder_context();require(not WORK.exists(),'build_once')
 Path(ENV['HOME']).mkdir(mode=0o700,exist_ok=True)
 cached=Path('/build/trixie.InRelease')
 fetch(SNAPSHOT+'/dists/trixie/InRelease',cached,INRELEASE)
 # This is an immutable archival repository. Expiry checking is disabled only
 # for its frozen timestamp; archive signatures and exact release hash remain.
 for path in Path('/etc/apt/sources.list.d').glob('*'):
  require(path.is_file() and not path.is_symlink(),'repository_path');path.unlink()
 line='deb [check-valid-until=no signed-by=/usr/share/keyrings/debian-archive-keyring.gpg] '+SNAPSHOT+' trixie main\n'
 Path('/etc/apt/sources.list').write_text(line)
 command(['/usr/bin/apt-get','-o','Acquire::Retries=0','update'],timeout=300)
 releases=list(Path('/var/lib/apt/lists').glob('*_dists_trixie_InRelease'))
 require(len(releases)==1,'repository_inventory');digest(releases[0],INRELEASE,262144)
 command(['/usr/bin/apt-get','-y','--no-install-recommends','install','build-essential','dpkg-dev','debhelper',
  'autoconf','libtool','automake','pkgconf','chrpath','lynx','libreadline-dev','tcl8.6-dev','libicu-dev',
  'quilt','debian-keyring','gpgv','binutils'],timeout=900)
 # Restore the exact genuine package's documentation excluded by slim images.
 # These CLI includes follow normal dpkg excludes; full payload verification
 # remains strict. No global filter configuration is changed.
 command(['/usr/bin/apt-get','-y','--no-install-recommends','--reinstall',
  '-o','Dpkg::Options::='+DOC_INCLUDES[0],'-o','Dpkg::Options::='+DOC_INCLUDES[1],
  'install','libsqlite3-0='+BASE_VERSION],timeout=300)
def positive(expected):
 digest(RECIPE/'verify_positive.py',VERIFY_PIN)
 code,raw=bounded_command(['/usr/local/bin/python3','-I','-B',str(RECIPE/'verify_positive.py'),'--expect-package-version',expected],cwd=None,timeout=30,maximum=65536,capture=True,with_code=True)
 value=json.loads(raw)
 if code!=0:
  allowed={'schema','status','reason','known_code','exception_class','security_accepted'}
  if (type(value) is dict and set(value)==allowed and value['schema']=='sqlite_ordinary_compatibility/1'
   and value['status']=='FAIL' and value['reason']=='sqlite_compatibility_gate_failed'
   and value['security_accepted'] is False and type(value['known_code']) is str
   and value['known_code'] in PUBLIC_FAILURE_CODES and type(value['exception_class']) is str and value['exception_class'] in
   {'ValueError','OSError','FileNotFoundError','PermissionError','CalledProcessError','TimeoutExpired','TimeoutError','unclassified'}):
   sys.stderr.write(json.dumps(value,sort_keys=True)+'\n');sys.stderr.flush()
  require(False,'positive_refused')
 require(type(value) is dict and value.get('schema')=='sqlite_ordinary_compatibility/1'
  and value.get('status')=='PASS' and value.get('sqlite_package')==expected
  and value.get('production_data_accessed') is False and value.get('wal_reset_reproduction_performed') is False
  and value.get('security_accepted') is False,'positive_compatibility')
 require(type(value.get('compile_options')) is list and value['compile_options']==sorted(set(value['compile_options'])),'compile_options')
 for name in ('sqlite_stdlib_extension','sqlite_system_library'):
  binding=value.get(name)
  require(type(binding) is dict and set(binding)=={'path','bytes','sha256','mapped_inode_verified'}
   and binding['mapped_inode_verified'] is True and type(binding['bytes']) is int and binding['bytes']>0
   and type(binding['path']) is str and type(binding['sha256']) is str
   and re.fullmatch('[a-f0-9]{64}',binding['sha256']),'mapped_library_binding')
  require(digest(Path(binding['path']))=={key:binding[key] for key in ('bytes','sha256')},'mapped_library_binding')
 require(value['sqlite_system_library']['path']==value['system_library'],'mapped_library_binding')
 return value
def library(path):
 path=Path(path);require(path.resolve(strict=True)==path and path.is_relative_to(Path('/usr/lib')),'system_library')
 digest(path,maximum=16*1024**2);return path
def elf(path):
 dynamic=metadata(['/usr/bin/readelf','--dynamic','--wide',str(path)]).decode('ascii')
 names=re.findall(r'\(SONAME\).*?\[([^\]]+)\]',dynamic);needed=sorted(re.findall(r'\(NEEDED\).*?\[([^\]]+)\]',dynamic))
 require(names==['libsqlite3.so.0'] and needed,'elf_soname')
 raw=metadata(['/usr/bin/nm','-D','--defined-only','--format=posix',str(path)]).decode('ascii')
 exports=sorted(line.split()[0] for line in raw.splitlines() if line.strip())
 versions=metadata(['/usr/bin/readelf','--version-info','--wide',str(path)]).decode('ascii')
 required=sorted(set(re.findall(r'Name:\s+(GLIBC_[0-9.]+)',versions)))
 require(exports and required,'elf_contract');return {'soname':names[0],'needed':needed,'exports':exports,'glibc_versions':required}
def vendor_log_contract(name,raw):
 """Parse only genuine bounded vendor stdout; observations grant no CVE acceptance."""
 require(name in VENDOR_COMMANDS and type(raw)is bytes and 0<len(raw)<=VENDOR_LOG_LIMITS[name],'vendor_log_bound')
 summaries=re.findall(rb'(?m)^([0-9]+) errors out of ([0-9]+) tests(?: on [^\r\n]*)?$',raw)
 require(summaries and all(int(errors)==0 and int(count)>0 for errors,count in summaries),'vendor_test_summary')
 require(b' new errors and 'not in raw and b'!Failures on these tests:'not in raw,'vendor_known_errors_not_waived')
 omissions=[line.decode('utf-8','replace')for line in raw.splitlines()if line.startswith(b'.  ')]
 require(len(omissions)<=4096 and all(len(line)<=1024 for line in omissions),'vendor_omission_bound')
 markers=[]
 if name=='vendor-fts5-regression.log':
  markers=[f'fts5prefix2-4-{detail}-{case}... Ok'for detail in('full','col','none')for case in(1,2)]
  require(all(raw.splitlines().count(marker.encode())==1 for marker in markers),'official_fts5_cases_missing')
 return {'argv':VENDOR_COMMANDS[name],'actual_returncode':0,'unfiltered_standard_target':name=='vendor-standard.log',
  'raw_output':{'name':name,'bytes':len(raw),'sha256':sha256(raw).hexdigest()},
  'zero_error_summaries':[{'errors':int(e),'tests':int(n)}for e,n in summaries],
  'official_regression_markers':markers,'vendor_default_omissions':omissions,
  'raw_omissions_retained':True,'omissions_accepted':False,'security_accepted':False}
def vendor_tests(src):
 # Debian has already configured/built this exact source with its unchanged
 # rules/feature flags. Do not reconfigure, filter TESTOPTS, or substitute a suite.
 watched=('Makefile','debian/rules','src/wal.c','ext/fts5/fts5_index.c','ext/fts5/test/fts5prefix2.test',*VENDOR_HELPERS)
 before={name:digest(src/name,maximum=4*1024**2)for name in watched}
 require(all(before[name]=={'bytes':pin[0],'sha256':pin[1]}for name,pin in VENDOR_HELPERS.items()),'vendor_naming_helper_changed')
 require(not any(k in current_environment()for k in('TESTOPTS','TESTS','EXTRA_QUICKTEST','SQLITE_TEST_PREFIX')),'vendor_test_filter')
 logs={};facts={}
 for name,timeout in(('vendor-standard.log',1200),('vendor-fts5-regression.log',180)):
  code,raw=bounded_command(VENDOR_COMMANDS[name],cwd=src,timeout=timeout,
   maximum=VENDOR_LOG_LIMITS[name],capture=True,with_code=True,tee=True)
  require(code==0,'vendor_test_command_refused')
  facts[name]=vendor_log_contract(name,raw);logs[name]=raw
 require({name:digest(src/name,maximum=4*1024**2)for name in watched}==before,'vendor_source_or_flags_changed')
 return {'same_configured_source':before,'standard_and_official_regression':facts,
  'standard_scope':dict(VENDOR_STANDARD_SCOPE),
  'no_new_filters_or_exclusions':True,'full_all_permutations_claimed':False,
  'default_omissions_are_not_waivers':True},logs
def build():
 builder_context();proofs=inputs(INPUTS,RECIPE);require(not WORK.exists() and not OUT.exists(),'build_once')
 WORK.mkdir(mode=0o700);Path(ENV['HOME']).mkdir(mode=0o700,exist_ok=True)
 # Maintainer signature verification uses only packaged PUBLIC keyrings.
 keyrings=[p for p in (Path('/usr/share/keyrings/debian-keyring.gpg'),Path('/usr/share/keyrings/debian-maintainers.gpg')) if p.is_file()]
 require(keyrings,'source_keyring')
 argv=['/usr/bin/gpgv','--homedir',ENV['HOME']]
 for path in keyrings:argv+=['--keyring',str(path)]
 command([*argv,str(INPUTS/('sqlite3_'+BASE_VERSION+'.dsc'))],timeout=30)
 baseline=positive(BASE_VERSION);base_lib=library(baseline['system_library']);base_elf=elf(base_lib)
 base_binary=digest(base_lib);base_python=digest(Path(sys.executable).resolve(),maximum=64*1024**2)
 # Preserve the genuine Debian build rules and flags, including their normal
 # internal Tcl-index generation. No new runtime loader variable is introduced.
 with tarfile.open(INPUTS/('sqlite3_'+BASE_VERSION+'.debian.tar.xz'),'r:xz') as archive:
  entry=archive.getmember('debian/rules');require(entry.isfile() and entry.size<=65536,'debian_rules')
  rules=archive.extractfile(entry).read(65537);require(len(rules)==entry.size,'debian_rules')
 rules_pin=(len(rules),sha256(rules).hexdigest())
 src=WORK/'source'
 command(['/usr/bin/dpkg-source','-x',str(INPUTS/('sqlite3_'+BASE_VERSION+'.dsc')),str(src)],timeout=180)
 digest(src/'debian/rules',rules_pin);digest(src/'src/wal.c',WAL_BEFORE)
 for name,pin in VENDOR_HELPERS.items():digest(src/name,pin)
 digest(src/'ext/fts5/fts5_index.c',FTS_BEFORE);digest(src/'ext/fts5/test/fts5prefix2.test',FTS_TEST_BEFORE)
 series=src/'debian/patches/series';previous=bytes_at(series,maximum=65536)
 patches=[('trading-bot-production-wal-reset.patch',PATCH,PATCH_PIN),
  ('trading-bot-fts5-detail-none-merge.patch',FTS_PATCH,FTS_PATCH_PIN),
  ('trading-bot-fts5-upstream-regression.patch',FTS_TEST_PATCH,FTS_TEST_PATCH_PIN)]
 require(all(name.encode()not in previous for name,_,_ in patches),'patch_once')
 for name,leaf,pin in patches:publish(src/'debian/patches'/name,bytes_at(RECIPE/leaf,pin))
 series.write_bytes(previous+(b''if previous.endswith(b'\n')else b'\n')+b''.join(name.encode()+b'\n'for name,_,_ in patches))
 command(['/usr/bin/quilt','--quiltrc','/dev/null','push','-a'],cwd=src,timeout=30)
 digest(src/'src/wal.c',WAL_AFTER);digest(src/'ext/fts5/fts5_index.c',FTS_AFTER)
 digest(src/'ext/fts5/test/fts5prefix2.test',FTS_TEST_AFTER)
 changelog=src/'debian/changelog';original=changelog.read_bytes();require(original.startswith(('sqlite3 ('+BASE_VERSION+')').encode()),'changelog_base')
 date=email.utils.format_datetime(datetime.datetime.fromtimestamp(SOURCE_EPOCH,datetime.timezone.utc))
 entry=('sqlite3 ('+VERSION+') trixie; urgency=high\n\n'
  '  * Retain production WAL-reset backport; add official FTS5 merge bound fix.\n'
  '  * Keep upstream3.46.1 ABI, Debian build rules and feature flags.\n'
  '  * Require unchanged standard vendor make test and official FTS5 regression.\n\n'
  ' -- Trading Bot Security Build <security-build@localhost>  '+date+'\n\n').encode()
 changelog.write_bytes(entry+original)
 require(metadata(['/usr/bin/dpkg-parsechangelog','-S','Version'],cwd=src).decode().strip()==VERSION,'local_package_version')
 command(['/usr/bin/dpkg-buildpackage','-B','-uc','-us','-j2'],cwd=src,timeout=1800)
 vendor,vendor_logs=vendor_tests(src)
 digest(src/'debian/rules',rules_pin);digest(src/'src/wal.c',WAL_AFTER)
 digest(src/'ext/fts5/fts5_index.c',FTS_AFTER);digest(src/'ext/fts5/test/fts5prefix2.test',FTS_TEST_AFTER)
 packages=list(WORK.glob('libsqlite3-0_'+VERSION+'_amd64.deb'));require(len(packages)==1,'runtime_package_inventory');package=packages[0]
 for field,expected in (('Package','libsqlite3-0'),('Version',VERSION),('Architecture','amd64'),('Multi-Arch','same')):
  require(metadata(['/usr/bin/dpkg-deb','-f',str(package),field]).decode().strip()==expected,'runtime_package_control')
 # Proper installation occurs only inside this disposable builder. Candidate
 # verification starts a new unchanged Python process through normal dpkg/ld.so.
 command(['/usr/bin/dpkg',*DOC_INCLUDES,'--install',str(package)],timeout=60)
 candidate=positive(VERSION);new_lib=library(candidate['system_library']);new_elf=elf(new_lib)
 require(baseline['compile_options']==candidate['compile_options'],'compile_option_parity')
 require(baseline['sqlite_stdlib_extension']==candidate['sqlite_stdlib_extension'],'stdlib_extension_changed')
 require(base_elf==new_elf,'elf_abi_parity');require(base_python==digest(Path(sys.executable).resolve(),maximum=64*1024**2),'python_binary_changed')
 package_pin=digest(package);new_binary=digest(new_lib)
 build_metadata=[]
 for suffix in ('changes','buildinfo'):
  matches=list(WORK.glob('sqlite3_'+VERSION+'_amd64.'+suffix))
  require(len(matches)==1,'local_build_metadata_inventory')
  item=matches[0];build_metadata.append((item,digest(item,maximum=MAX_METADATA)))
 OUT.mkdir(mode=0o755);target=OUT/package.name
 copy_exact(package,target,(package_pin['bytes'],package_pin['sha256']),mode=0o644)
 metadata_proofs={}
 for item,binding in build_metadata:
  copy_exact(item,OUT/item.name,(binding['bytes'],binding['sha256']),mode=0o644)
  metadata_proofs[item.name]=binding
 report={'schema':'sqlite_wal_fts5_local_debian_package/2','package':'libsqlite3-0','base_package_version':BASE_VERSION,
  'local_package_version':VERSION,'upstream_version_unchanged':'3.46.1','architecture':'amd64',
  'repository_snapshot':SNAPSHOT,'repository_release':{'bytes':INRELEASE[0],'sha256':INRELEASE[1]},
  'source_files':proofs,'source_maintainer_signature_verified':True,'debian_rules':{'bytes':rules_pin[0],'sha256':rules_pin[1]},
  'production_patch':{'bytes':PATCH_PIN[0],'sha256':PATCH_PIN[1]},'wal_before':{'bytes':WAL_BEFORE[0],'sha256':WAL_BEFORE[1]},
  'fts5_patch':{'bytes':FTS_PATCH_PIN[0],'sha256':FTS_PATCH_PIN[1]},
  'fts5_regression_patch':{'bytes':FTS_TEST_PATCH_PIN[0],'sha256':FTS_TEST_PATCH_PIN[1]},
  'fts5_source_before':{'bytes':FTS_BEFORE[0],'sha256':FTS_BEFORE[1]},
  'fts5_source_after':{'bytes':FTS_AFTER[0],'sha256':FTS_AFTER[1]},
  'fts5_regression_before':{'bytes':FTS_TEST_BEFORE[0],'sha256':FTS_TEST_BEFORE[1]},
  'fts5_regression_after':{'bytes':FTS_TEST_AFTER[0],'sha256':FTS_TEST_AFTER[1]},
  'official_fts5_commit':UPSTREAM_FTS_COMMIT,'vendor_tests':vendor,
  'wal_after':{'bytes':WAL_AFTER[0],'sha256':WAL_AFTER[1]},'positive_verifier':{'bytes':VERIFY_PIN[0],'sha256':VERIFY_PIN[1]},
  'source_date_epoch':SOURCE_EPOCH,'build_options':ENV['DEB_BUILD_OPTIONS'],'upstream_fault_suites_run':False,
  'wal_reset_reproduction_performed':False,'full_upstream_suite_pass_claimed':False,
  'baseline_library':base_binary,'candidate_library':new_binary,'package_file':{'name':target.name,**package_pin},
  'local_build_metadata':metadata_proofs,'local_build_metadata_signed':False,
  'compile_options_equal':True,'elf_soname_exports_dependencies_equal':True,'python_binary_unchanged':True,
  'sqlite_stdlib_extension_unchanged':True,'mapped_installed_libraries_verified':True,
  'build_toolchain_packages':metadata(['/usr/bin/dpkg-query','-W','-f=${binary:Package} ${Version}\n',
   'gcc','g++','libc6','libc6-dev','binutils','dpkg-dev','debhelper','libtool','autoconf','automake']).decode('ascii').splitlines(),
  'full_sqlite_documentation_restored':True,'dpkg_path_includes':list(DOC_INCLUDES),
  'runtime_loader_override_used':False,'production_data_accessed':False,'app_ci_restore_gate_required':True,'security_accepted':False}
 for name,raw in vendor_logs.items():publish(OUT/name,raw);(OUT/name).chmod(0o644)
 for name,value in (('provenance.json',report),('positive-baseline.json',baseline),('positive-candidate.json',candidate),('abi-contract.json',new_elf)):
  publish(OUT/name,encoded(value));(OUT/name).chmod(0o644)
 return report
def main(argv=None):
 parser=argparse.ArgumentParser();parser.add_argument('--fetch-public-inputs',action='store_true');parser.add_argument('--stage-context');parser.add_argument('--configure-build-repository',action='store_true');parser.add_argument('--build',action='store_true');args=parser.parse_args(argv)
 require(sum((args.fetch_public_inputs,args.stage_context is not None,args.configure_build_repository,args.build))<=1,'action_scope')
 if args.fetch_public_inputs:
  require(Path(__file__).absolute().parent==Path.cwd(),'fetch_workdir');root=Path.cwd()/'inputs';root.mkdir(mode=0o700,exist_ok=True)
  require(root.resolve()==root and root.stat().st_mode&0o777==0o700,'fetch_directory')
  for leaf,pin in SOURCE_FILES.items():
   if (root/leaf).exists():digest(root/leaf,pin)
   else:fetch(SNAPSHOT+'/pool/main/s/sqlite3/'+leaf,root/leaf,pin)
  result={'schema':'sqlite_public_inputs/1','inputs':inputs(root,Path.cwd()),'build_performed':False,'security_accepted':False}
 elif args.stage_context:result=stage_context(args.stage_context)
 elif args.configure_build_repository:
  inputs(INPUTS,RECIPE);configure_repository();result={'schema':'sqlite_builder_repository/1','snapshot':SNAPSHOT,'release_sha256':INRELEASE[1],'build_performed':False,'security_accepted':False}
 elif args.build:result=build()
 else:
  result={'schema':'sqlite_recipe_unarmed/1','required_public_inputs':SOURCE_FILES,'build_performed':False,'artifact_created':False,'security_accepted':False}
 print(json.dumps(result,sort_keys=True));return 0

if __name__=='__main__':raise SystemExit(main())
