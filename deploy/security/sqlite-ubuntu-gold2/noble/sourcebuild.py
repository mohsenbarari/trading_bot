"""Pinned native Ubuntu SQLite backport recipe; no execution on import.

Only Root/CI runs an actual build in the disposable Docker builder. The output
is a proper local-version libsqlite3-0 package, never a loader-path override.
Gold2 retains WAL and requires the original standard vendor target plus the
official FTS5 regression; exhaustive fault permutations are not claimed.
"""
from hashlib import sha256
from pathlib import Path
import argparse,base64,ctypes,errno,fcntl,datetime,email.utils,json,os,re,select,selectors,shutil,signal,stat,subprocess,sys,tarfile,time,uuid
from urllib.parse import urlsplit
from urllib.request import build_opener,HTTPSHandler,ProxyHandler,Request

BASE_VERSION='3.45.1-1ubuntu2.8';VERSION=BASE_VERSION+'+gold2'
CODENAME='noble';VERSION_ID='24.04'
PYTHON_VERSION=(3, 12, 3);PYTHON='/usr/bin/python3.12';UPSTREAM='3.45.1'
BASE_IMAGE='ubuntu:24.04@sha256:a2d43bffc46670d7108bb3c0468c67c5557c4c92e83e1e34cbff1322ad1b94dc'
INRELEASES={'noble': (255850, 'cdb2f31d809f589719a53c6ad15f255b27569c4059542ada282aaa21b8e164b0'), 'noble-updates': (126125, 'c053de67f316159b441e167f3f29b3f00751b71409dc1537bb90cb86d58b2024'), 'noble-security': (126127, 'd53de120a213d4e17ede2854718ed8eedfc04f3ea2e263994d7f21317f7b851b')}
PROFILE_PIN=(662, 'bc10b4fe4ccc1a668ce1c55a00daf8ed0f129b9298ebaf9f0487a2a14c0b4af3')
SIGNER_FINGERPRINT='7FE79B445728C8EA0042839E45BCE75B840B1F69'
SIGNER_PIN=(14582,'91b93e45fb77b7098882dc1e2035951b7c351b9d3f754990642be80191030a73')
SOURCE_EPOCH=1793491200  # Authored2026-11-01, reproducible local changelog date.
SNAPSHOT='https://snapshot.ubuntu.com/ubuntu/20261025T000000Z'
SOURCE_FILES={'sqlite3_3.45.1-1ubuntu2.8.debian.tar.xz': (37512, 'd543bf91c6f14ccd52ebe44d6ef3b360196b2596e0a7ca86089aef5fc52f5ec0'), 'sqlite3_3.45.1-1ubuntu2.8.dsc': (2601, 'f3c0a904d2d2386e81b39774f2a2f00f92c42adb2f141ff59cc6a347c4487a69'), 'sqlite3_3.45.1.orig-www.tar.xz': (5693812, '79b60798195a024d447e661e5bbc1eb40af50387ebf840e6f581190cc02064b6'), 'sqlite3_3.45.1.orig.tar.xz': (8257884, 'e32e817f7b4166a301f60b14a711871bfab7d35c1d7e29b585dfc479ae150aa4')}
PATCH='0001-wal-reset-production-only.patch'
PATCH_PIN=(6746,'ab1d8c75b8ed521cc5d1d0c64da66be26ca3b72d53326c28b6a86473f42ddc85')
VERIFY_PIN=(16832, '0c2027475c4445244eb546965e6205cc1c0bd98f7834d4bf60890015cf71a489')
WAL_BEFORE=(174376,'d4a104b5f3cb807e026a9dbcdb77519358b06b66f76cb6b6ef70e3793379dd62')
WAL_AFTER=(175230,'7287fb98dcfbfb3c33dfba555c21396f7ef53c68a325dc706e68ca3c19ab73a7')
FTS_PATCH='0002-fts5-detail-none-merge-bound.patch'
FTS_PATCH_PIN=(1252, 'bc1403e417a5673ad51712c36f3b9b33f87b77a6f6f06d7bedba02b46d098231')
FTS_TEST_PATCH='0003-fts5-detail-none-upstream-regression.patch'
FTS_TEST_PATCH_PIN=(931, '81edf417c615f7a76fe9506c74e4a05a5ee1a135bce805de7ae8ac02c3dbfaed')
FTS_BEFORE=(283756, 'b358488b1ff31b08b4f263eaa3c820e378218c5cf9164b74c29b647fb7f9e630')
FTS_AFTER=(283780, '35c85de15aef3e47162a9fa8e246858304bb6f2e5a93a7ef6103157897d2c1eb')
FTS_TEST_BEFORE=(2113, 'd4260474ddc5d374c5fc932c1251f695fcfac57c2ce2f43b3f2056e426ebab46')
FTS_TEST_AFTER=(2598, 'b03372c1a4d9b112b09a75574fcff4414d2eab431ae4d9f3bd156a648fe8d58e')
UPSTREAM_FTS_COMMIT='e1c9934f9f11a499029abb020ea715a08970da80'
# These helper pins describe the actual post-Ubuntu-quilt source stage.
VENDOR_HELPERS={'test/tester.tcl': (78000, '5c4c2881737e9b5d488ad453be74630bc8209d3ee5cf1492dc08a9a1f6166aa7'), 'ext/fts5/test/fts5_common.tcl': (17586, '7600a7adf913e45113ea66d0395de96d18dff9b187599c01151de5dd61e26e80'), 'Makefile.in': (52789, '9090f59eb24e9f168cce0faf173011a210bac5cff2548a0683db7782ad7b1273'), 'test/veryquick.test': (587, '814df9511e0412a654e721851613f9061bbd7557c688b50d7bd57c980e4ec773')}
VENDOR_STANDARD_SCOPE={'standard_target': 'test', 'standard_prerequisites': ['srctree-check', 'fuzztest', 'sourcetest', '$(TESTPROGS)', 'tcltest'], 'executed_standard_subtargets': ['srctree-check', 'fuzztest', 'sourcetest', 'tcltest'], 'default_tcl_suite': 'test/veryquick.test', 'vendor_default_omissions_scope': 'upstream_veryquick_excludes_malloc_and_io_error_simulations', 'all_fault_permutations_run': False}
VENDOR_LOG_LIMITS={'vendor-standard.log':32*1024**2,'vendor-fts5-regression.log':4*1024**2}
VENDOR_COMMANDS={'vendor-standard.log':['/usr/bin/make','test'],'vendor-fts5-regression.log':['./testfixture','ext/fts5/test/fts5prefix2.test']}
RECIPE=Path('/recipe');INPUTS=Path('/inputs');WORK=Path('/build/sqlite-wal-backport');OUT=Path('/out')
ENV={'PATH':'/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin','HOME':'/build/home','LC_ALL':'C.UTF-8','LANG':'C.UTF-8',
 'DEBIAN_FRONTEND':'noninteractive',
 'SOURCE_DATE_EPOCH':str(SOURCE_EPOCH),'DEB_BUILD_OPTIONS':'nocheck parallel=2',
 'QUILT_PATCHES':'debian/patches','QUILT_PATCH_OPTS':'--fuzz=0'}
MAX_METADATA=4*1024**2
CA_BOOTSTRAP={'schema': 'ubuntu_public_ca_bootstrap/1', 'source_image': 'python:3.11.17-slim-trixie@sha256:b95327ce1439e687237841b0210677aeadd62d55a060a5f6bcb9c15bf6251dd3', 'source_path': '/etc/ssl/certs/ca-certificates.crt', 'bytes': 222073, 'sha256': 'e9c7b63ee2623386e9b0a3ee32f499d01a8ea23dc83b8383ddff9f8e749543b7', 'mode': '0444', 'data_only': True, 'foreign_package_or_binary_copied': False}

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
def publish(path,raw,maximum=MAX_METADATA):
 require(maximum==MAX_METADATA or (Path(path).name in VENDOR_LOG_LIMITS and maximum==VENDOR_LOG_LIMITS[Path(path).name]),'output_role_bound')
 require(type(raw) is bytes and len(raw)<=maximum,'output_bound')
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
def bounded_command(argv,*,cwd,timeout,maximum,capture,with_code=False,tee=False,unprivileged=False,absolute_end=None):
 global VENDOR_RETIREMENT_UNPROVEN
 """Bound bytes BEFORE accumulation/forwarding; no payload file-size limit."""
 require(type(unprivileged)is bool,'vendor_credentials_type')
 require(absolute_end is None or(type(absolute_end)in(int,float)and 0<absolute_end<float('inf')),'command_absolute_end_type')
 env=current_environment();credentials={}
 if unprivileged:
  require(VENDOR_PROCESS_SCOPE is not None and VENDOR_PROCESS_SCOPE['armed']and not VENDOR_PROCESS_SCOPE['fault']and VENDOR_PROCESS_SCOPE['controller_pid']==os.getpid(),'vendor_process_scope_required')
  env['HOME']=str(WORK/'vendor-test-home');credentials={'user':VENDOR_UID,'group':VENDOR_GID,'extra_groups':[],'umask':0o077}
 if absolute_end is not None:require(unprivileged and time.monotonic()<absolute_end,'command_absolute_deadline')
 child=subprocess.Popen(argv,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
  env=env,cwd=cwd,close_fds=True,start_new_session=True,**credentials)
 if unprivileged:VENDOR_RETIREMENT_UNPROVEN=True
 parts=[];count=0;end=time.monotonic()+timeout
 try:
  if absolute_end is not None:
   end=min(end,absolute_end);require(time.monotonic()<absolute_end,'command_absolute_deadline')
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
  if absolute_end is not None:require(time.monotonic()<absolute_end,'command_absolute_deadline')
  if with_code:
   require(capture,'capture_contract');return code,b''.join(parts)
  require(code==0,'build_command_refused')
  return b''.join(parts) if capture else None
 except BaseException as primary:
  try:cleanup_owned(child)
  except BaseException as cleanup:
   if unprivileged:raise BaseExceptionGroup('dropped vendor child and cleanup failures',[primary,cleanup])
   pass  # CI tears down the disposable builder cgroup.
  raise
 finally:
  child.stdout.close()



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
   require(urlsplit(response.geturl()).hostname=='snapshot.ubuntu.com' and urlsplit(response.geturl()).scheme=='https','fetch_redirect')
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
 digest(recipe/'source-signer.asc',SIGNER_PIN);digest(recipe/'consumer-profile.json',PROFILE_PIN)
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
 require(sys.version_info[:3]==PYTHON_VERSION and Path(sys.executable).resolve(strict=True)==Path(PYTHON)
  and sys.base_prefix=='/usr' and os.uname().machine=='x86_64','build_platform')
 os_release=bytes_at(Path('/etc/os-release').resolve(strict=True),maximum=65536).decode('utf-8')
 fields=dict(row.split('=',1) for row in os_release.splitlines() if '=' in row)
 require(fields.get('ID','').strip('"')=='ubuntu' and fields.get('VERSION_ID','').strip('"')==VERSION_ID
  and fields.get('VERSION_CODENAME','').strip('"')==CODENAME,'matching_ubuntu_builder_only')
 current_environment()
 context=json.loads(bytes_at(RECIPE/'context-manifest.json'))
 require(type(context) is dict and set(context)=={'schema','files'} and context['schema']=='sqlite_allowlisted_context/1','build_context')
 expected={'sourcebuild.py','Dockerfile',PATCH,FTS_PATCH,FTS_TEST_PATCH,'verify_positive.py','source-signer.asc','consumer-profile.json',*('inputs/'+name for name in SOURCE_FILES)}
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
 files={name:digest(recipe/name) for name in ('sourcebuild.py','Dockerfile',PATCH,FTS_PATCH,FTS_TEST_PATCH,'verify_positive.py','source-signer.asc','consumer-profile.json')}
 files.update({'inputs/'+name:row for name,row in proofs.items()})
 destination.mkdir(mode=0o700);(destination/'inputs').mkdir(mode=0o700)
 for name,binding in files.items():
  original=recipe/name;target=destination/name
  expected=(binding['bytes'],binding['sha256']);copy_exact(original,target,expected)
 manifest={'schema':'sqlite_allowlisted_context/1','files':files};publish(destination/'context-manifest.json',encoded(manifest))
 allow='**\n!.dockerignore\n!context-manifest.json\n'+''.join('!'+name+'\n' for name in ('sourcebuild.py','Dockerfile',PATCH,FTS_PATCH,FTS_TEST_PATCH,'verify_positive.py','source-signer.asc','consumer-profile.json'))
 allow+='!inputs/\n'+''.join('!inputs/'+name+'\n' for name in SOURCE_FILES)
 publish(destination/'.dockerignore',allow.encode())
 return {'schema':'sqlite_public_context_staged/1','context':str(destination),'files':files,'build_performed':False,'security_accepted':False}
def configure_repository():
 builder_context()
 require(json.loads(bytes_at(Path('/build/ca-bootstrap-public.json')))==CA_BOOTSTRAP,'public_ca_bootstrap')
 native_ca=metadata(['/usr/bin/dpkg-query','-W','-f=${Version}\n','ca-certificates']).decode('ascii').strip()
 require(native_ca and len(native_ca)<=100 and '\n' not in native_ca,'native_ca_certificates')
 expected=''.join('deb [check-valid-until=no signed-by=/usr/share/keyrings/ubuntu-archive-keyring.gpg] '+SNAPSHOT+' '+suite+' main universe\n' for suite in INRELEASES)
 require(bytes_at(Path('/etc/apt/sources.list')).decode()==expected,'native_repository')
 require(not list(Path('/etc/apt/sources.list.d').glob('*')),'native_repository')
 for suite,binding in INRELEASES.items():
  matches=list(Path('/var/lib/apt/lists').glob('*_dists_'+suite+'_InRelease'))
  require(len(matches)==1,'repository_inventory');digest(matches[0],binding,maximum=524288)

PUBLIC_POSITIVE_REFUSAL_CODES = ('backup_integrity', 'backup_query_drift', 'binary_bound', 'binary_changed', 'binary_identity', 'binary_mapping', 'binary_parent', 'compile_options_bound', 'fts_contract', 'isolated_python_required', 'json_contract', 'loader_override', 'lookup_index_not_used', 'mapping_bound', 'native_library_layout', 'native_python_required', 'native_ubuntu_required', 'non_system_library', 'os_metadata', 'os_metadata_bound', 'package_library_path', 'package_metadata_bound', 'package_paths_bound', 'package_payload_changed', 'package_scope', 'package_version', 'package_version_changed', 'python_customization', 'python_version_changed', 'readonly_reopen_drift', 'scratch_not_private', 'sqlite_runtime_package_mismatch', 'stdlib_extension_required', 'stdlib_sqlite_required', 'system_library_missing_or_ambiguous', 'transaction_or_query_drift', 'wal_mode', 'wal_reader', 'wal_refresh', 'wal_snapshot_drift')
def positive_failure(raw):
 reason='sqlite_compatibility_gate_failed'
 def unique(pairs):
  value={}
  for key,item in pairs:
   if key in value:raise ValueError('duplicate')
   value[key]=item
  return value
 try:
  require(type(raw)is bytes and 0<len(raw)<=65536,'positive_failure_bound')
  value=json.loads(raw,object_pairs_hook=unique)
  if (type(value)is dict and set(value)=={'schema','status','reason','security_accepted'}
   and value['schema']=='sqlite_ordinary_compatibility/1'and value['status']=='FAIL'
   and value['security_accepted']is False and type(value['reason'])is str
   and value['reason']in PUBLIC_POSITIVE_REFUSAL_CODES):reason=value['reason']
 except (ValueError,UnicodeError,RuntimeError):pass
 return {'schema':'sqlite_ordinary_compatibility/1','status':'FAIL','reason':reason,'security_accepted':False}
def positive(expected):
 digest(RECIPE/'verify_positive.py',VERIFY_PIN)
 code,raw=bounded_command([PYTHON,'-I','-S','-B',str(RECIPE/'verify_positive.py'),'--expect-package-version',expected],cwd=None,timeout=30,maximum=65536,capture=True,with_code=True)
 if code!=0:
  print(json.dumps(positive_failure(raw),sort_keys=True,separators=(',',':')),flush=True)
  raise RuntimeError('positive_compatibility')
 value=json.loads(raw)
 require(type(value) is dict and value.get('schema')=='sqlite_ordinary_compatibility/1'
  and value.get('status')=='PASS' and value.get('sqlite_package')==expected
  and value.get('production_data_accessed') is False and value.get('wal_reset_reproduction_performed') is False
  and value.get('security_accepted') is False and value.get('python_no_site') is True,'positive_compatibility')
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
# LOCAL generated testfixture link correction; not an upstream security fix.
TESTFIXTURE_ASSIGNMENT_BEFORE = b'TESTFIXTURE_SRC += $(TESTFIXTURE_SRC$(USE_AMALGAMATION))\n'
TESTFIXTURE_ASSIGNMENT_AFTER = b'TESTFIXTURE_SRC += $(filter-out $(TOP)/ext/fts3/fts3_term.c,$(TESTFIXTURE_SRC$(USE_AMALGAMATION)))\n'
TESTFIXTURE_STANZAS_PIN = (4097, '5dc8fb147f38fe9b3f645555f399ad1365a00a830a0dc9affa3f9569bbcc121e')
TESTFIXTURE_OLD_WORDS_PIN = {'0': (4322, '689171f808bea32fdf84b7cee8762cc515676ff7f393ebd0ba4f86f4a39e6514'), '1': (2856, '965c92915bdd29de3f884edd25509a63ccfc1a3f5a74ed9f6aab85fabb32df06')}
TESTFIXTURE_LOCAL_PATCH = (b'Description: LOCAL generated test-harness source-list correction only\n'
    b'Origin: local; not an upstream or production CVE patch\n\n'
    b'--- a/Makefile\n+++ b/Makefile\n@@ -1 +1 @@\n-'
    + TESTFIXTURE_ASSIGNMENT_BEFORE + b'+' + TESTFIXTURE_ASSIGNMENT_AFTER)

def testfixture_stanzas(raw):
    require(type(raw) is bytes and 0 < len(raw) <= 131072, 'testfixture_makefile_bound')
    lines = raw.splitlines(keepends=True)
    result, keys, index = [], [], 0
    pattern = rb'^(TESTSRC2?|TESTFIXTURE_SRC[01]?) (\+?=) '
    while index < len(lines):
        match = re.match(pattern, lines[index])
        if match is None:
            index += 1
            continue
        keys.append((match[1], match[2]))
        block = [lines[index]]
        while block[-1].endswith(b'\\\n'):
            index += 1
            require(index < len(lines), 'testfixture_assignment_truncated')
            block.append(lines[index])
        require(block[-1].endswith(b'\n'), 'testfixture_assignment_truncated')
        result.extend(block)
        index += 1
    require(keys == [(b'TESTSRC', b'='), (b'TESTSRC', b'+='), (b'TESTSRC2', b'='),
        (b'TESTFIXTURE_SRC0', b'='), (b'TESTFIXTURE_SRC1', b'='),
        (b'TESTFIXTURE_SRC', b'='), (b'TESTFIXTURE_SRC', b'+=')], 'testfixture_assignment_inventory')
    return b''.join(result)

def testfixture_probe_program(stanzas, mode):
    require(type(stanzas) is bytes and 0 < len(stanzas) <= 65536 and type(mode) is int and mode in (0, 1), 'testfixture_probe_scope')
    return (b'TOP := /OWNED_SOURCE\nUSE_AMALGAMATION := ' + str(mode).encode() + b'\n'
        + stanzas + b'\n.PHONY: __sqlite_source_inventory__\n'
        b'__sqlite_source_inventory__: ; @:\n'
        b'$(info SQLITE_TESTFIXTURE_WORDS=$(TESTFIXTURE_SRC))\n')

def testfixture_probe_argv(label, mode):
    return ['/usr/bin/make', '--no-print-directory', '--dry-run', '-rR', '-f',
        'makefile-' + label + '-' + str(mode) + '.mk', '__sqlite_source_inventory__']

def testfixture_words(raw):
    require(type(raw) is bytes and 0 < len(raw) <= 65536, 'testfixture_probe_bound')
    lines = raw.splitlines()
    prefix = b'SQLITE_TESTFIXTURE_WORDS='
    require(len(lines) == 2 and lines[0].startswith(prefix) and lines[1] == b':', 'testfixture_probe_output')
    words = lines[0][len(prefix):].split()
    require(1 <= len(words) <= 1024 and all(re.fullmatch(rb'(?:/OWNED_SOURCE/[A-Za-z0-9_./+-]{1,256}|parse\.c|fts5\.c|sqlite3\.c|libsqlite3\.la)', word) is not None for word in words), 'testfixture_source_words')
    return words

def testfixture_blob(raw):
    return {'bytes': len(raw), 'sha256': sha256(raw).hexdigest(), 'base64': base64.b64encode(raw).decode('ascii')}

def testfixture_decode(value, maximum):
    require(type(value) is dict and set(value) == {'bytes', 'sha256', 'base64'}
        and type(value['bytes']) is int and 0 < value['bytes'] <= maximum
        and type(value['sha256']) is str and re.fullmatch('[a-f0-9]{64}', value['sha256']) is not None
        and type(value['base64']) is str and len(value['base64']) <= (maximum + 2) // 3 * 4, 'testfixture_blob_bound')
    try:
        raw = base64.b64decode(value['base64'], validate=True)
    except (ValueError, TypeError):
        require(False, 'testfixture_blob_encoding')
    require(len(raw) == value['bytes'] and sha256(raw).hexdigest() == value['sha256'], 'testfixture_blob_pin')
    return raw

def testfixture_harness_contract(receipt):
    require(type(receipt) is dict and set(receipt) == {'schema', 'phase', 'local_patch', 'vendor_makefile_in',
        'generated_before', 'generated_after', 'gnu_make', 'official_or_production_fix', 'test_selection_changed', 'security_accepted'}, 'testfixture_receipt_shape')
    require(receipt['schema'] == 'LOCAL_generated_testfixture_single_source/2'
        and receipt['phase'] == 'AFTER_SUCCESSFUL_PACKAGE_BUILD_BEFORE_VENDOR_TESTS'
        and receipt['official_or_production_fix'] is receipt['test_selection_changed'] is receipt['security_accepted'] is False,
        'testfixture_receipt_scope')
    require(receipt['local_patch'] == {'bytes': len(TESTFIXTURE_LOCAL_PATCH), 'sha256': sha256(TESTFIXTURE_LOCAL_PATCH).hexdigest()}
        and receipt['vendor_makefile_in'] == {'bytes': VENDOR_HELPERS['Makefile.in'][0], 'sha256': VENDOR_HELPERS['Makefile.in'][1]}, 'testfixture_vendor_binding')
    before = testfixture_decode(receipt['generated_before'], 131072)
    after = testfixture_decode(receipt['generated_after'], 131072)
    require(before.count(TESTFIXTURE_ASSIGNMENT_BEFORE) == 1 and TESTFIXTURE_ASSIGNMENT_AFTER not in before
        and after == before.replace(TESTFIXTURE_ASSIGNMENT_BEFORE, TESTFIXTURE_ASSIGNMENT_AFTER), 'testfixture_exact_one_line')
    old_stanzas, new_stanzas = testfixture_stanzas(before), testfixture_stanzas(after)
    require((len(old_stanzas), sha256(old_stanzas).hexdigest()) == TESTFIXTURE_STANZAS_PIN
        and new_stanzas == old_stanzas.replace(TESTFIXTURE_ASSIGNMENT_BEFORE, TESTFIXTURE_ASSIGNMENT_AFTER), 'testfixture_vendor_stanzas')
    require(type(receipt['gnu_make']) is dict and set(receipt['gnu_make']) == {'0', '1'}, 'testfixture_modes')
    term = b'/OWNED_SOURCE/ext/fts3/fts3_term.c'
    for mode in (0, 1):
        pair = receipt['gnu_make'][str(mode)]
        require(type(pair) is dict and set(pair) == {'before', 'after'}, 'testfixture_probe_inventory')
        expanded = []
        for label in ('before', 'after'):
            row = pair[label]
            require(type(row) is dict and set(row) == {'argv', 'actual_returncode', 'program', 'stdout'}
                and row['argv'] == testfixture_probe_argv(label, mode)
                and type(row['actual_returncode']) is int and row['actual_returncode'] == 0, 'testfixture_probe_receipt')
            program = testfixture_probe_program(old_stanzas if label == 'before' else new_stanzas, mode)
            require(row['program'] == {'bytes': len(program), 'sha256': sha256(program).hexdigest()}, 'testfixture_probe_program')
            expanded.append(testfixture_words(testfixture_decode(row['stdout'], 65536)))
        old_words, new_words = expanded
        canonical = b'\n'.join(old_words) + b'\n'
        require((len(canonical), sha256(canonical).hexdigest()) == TESTFIXTURE_OLD_WORDS_PIN[str(mode)], 'testfixture_original_word_inventory')
        if mode == 0:
            duplicates = {word for word in old_words if old_words.count(word) > 1}
            unique = [word for index, word in enumerate(old_words) if word not in old_words[:index]]
            require(duplicates == {term} and old_words.count(term) == 2 and new_words == unique
                and new_words.count(term) == 1 and len(new_words) + 1 == len(old_words), 'testfixture_mode0_not_single_duplicate')
        else:
            require(old_words == new_words and old_words.count(term) == 1, 'testfixture_mode1_changed')
    return receipt

def prepare_vendor_testfixture(src):
    """One LOCAL generated test source correction after the production package build."""
    path = src / 'Makefile'
    before = bytes_at(path, maximum=131072)
    require(before.count(TESTFIXTURE_ASSIGNMENT_BEFORE) == 1 and TESTFIXTURE_ASSIGNMENT_AFTER not in before, 'testfixture_exact_one_line')
    vendor_pin = VENDOR_HELPERS['Makefile.in']
    digest(src / 'Makefile.in', vendor_pin, maximum=131072)
    old_stanzas = testfixture_stanzas(before)
    require((len(old_stanzas), sha256(old_stanzas).hexdigest()) == TESTFIXTURE_STANZAS_PIN, 'testfixture_vendor_stanzas')
    expected = before.replace(TESTFIXTURE_ASSIGNMENT_BEFORE, TESTFIXTURE_ASSIGNMENT_AFTER)
    probe_root = WORK / 'testfixture-source-list-probes'
    require(not probe_root.exists(), 'testfixture_probe_once')
    probe_root.mkdir(mode=0o700)
    probes = {'0': {}, '1': {}}
    for label, raw in (('before', before), ('after', expected)):
        if label == 'after':
            require(bytes_at(path, maximum=131072) == before, 'testfixture_generated_source_changed')
            patch_path = WORK / 'testfixture-single-source-local.patch'
            publish(patch_path, TESTFIXTURE_LOCAL_PATCH)
            command(['/usr/bin/patch', '--batch', '--forward', '--fuzz=0', '--no-backup-if-mismatch',
                '--reject-file=-', '-p1', '--directory', str(src), '--input', str(patch_path)], timeout=30)
            require(bytes_at(path, maximum=131072) == expected, 'testfixture_exact_one_line')
            digest(patch_path, (len(TESTFIXTURE_LOCAL_PATCH), sha256(TESTFIXTURE_LOCAL_PATCH).hexdigest()))
        for mode in (0, 1):
            program = testfixture_probe_program(testfixture_stanzas(raw), mode)
            leaf = 'makefile-' + label + '-' + str(mode) + '.mk'
            publish(probe_root / leaf, program)
            output = metadata(testfixture_probe_argv(label, mode), cwd=probe_root, maximum=65536)
            probes[str(mode)][label] = {'argv': testfixture_probe_argv(label, mode), 'actual_returncode': 0,
                'program': {'bytes': len(program), 'sha256': sha256(program).hexdigest()}, 'stdout': testfixture_blob(output)}
    digest(src / 'Makefile.in', vendor_pin, maximum=131072)
    receipt = {'schema': 'LOCAL_generated_testfixture_single_source/2',
        'phase': 'AFTER_SUCCESSFUL_PACKAGE_BUILD_BEFORE_VENDOR_TESTS',
        'local_patch': {'bytes': len(TESTFIXTURE_LOCAL_PATCH), 'sha256': sha256(TESTFIXTURE_LOCAL_PATCH).hexdigest()},
        'vendor_makefile_in': {'bytes': vendor_pin[0], 'sha256': vendor_pin[1]},
        'generated_before': testfixture_blob(before), 'generated_after': testfixture_blob(bytes_at(path, maximum=131072)),
        'gnu_make': probes, 'official_or_production_fix': False, 'test_selection_changed': False, 'security_accepted': False}
    return testfixture_harness_contract(receipt)


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
VENDOR_UID=10001;VENDOR_GID=10001
VENDOR_RETIREMENT_UNPROVEN=False
VENDOR_PHASE_SECONDS=1560;VENDOR_RESTORE_SECONDS=60
VENDOR_IDENTITY_PROGRAM=r'''import json,os,stat
home=os.environ['HOME'];s=os.stat(home,follow_symlinks=False)
assert stat.S_ISDIR(s.st_mode) and s.st_uid==s.st_gid==10001 and stat.S_IMODE(s.st_mode)==0o700
def writable(directory):
 p=os.path.join(directory,'.sqlite-vendor-owned-write-proof')
 fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
 try:
  assert os.write(fd,b'owned vendor identity only\n')==27
  row=os.fstat(fd);assert row.st_uid==row.st_gid==10001
  os.fsync(fd)
 finally:os.close(fd)
 os.unlink(p);return True
value={'uid':os.getuid(),'euid':os.geteuid(),'gid':os.getgid(),'egid':os.getegid(),'groups':os.getgroups(),
 'home':home,'home_mode':stat.S_IMODE(s.st_mode),'home_uid':s.st_uid,'home_gid':s.st_gid,
 'home_writable':writable(home),'source_writable':writable(os.getcwd())}
print(json.dumps(value,sort_keys=True,separators=(',',':')))
'''
def vendor_identity_argv():
 return [PYTHON if 'PYTHON'in globals()else'/usr/local/bin/python3','-I','-B','-c',VENDOR_IDENTITY_PROGRAM]
def vendor_privilege_contract(value):
 require(type(value)is dict and set(value)=={'schema','identity_probe','initial_tree','restored_tree','initial_entries','final_entries',
  'all_original_bindings_and_modes_restored','all_final_owners_root','no_symlink_targets_followed','work_mode_before','work_mode_during',
  'work_mode_restored','private_home_root_restored','phase_seconds_limit','restore_reserve_seconds','process_tree','security_accepted'},'vendor_privilege_fields')
 require(value['schema']=='sqlite_vendor_unprivileged_phase/1'and value['phase_seconds_limit']==VENDOR_PHASE_SECONDS
  and value['restore_reserve_seconds']==VENDOR_RESTORE_SECONDS,'vendor_privilege_scope')
 for k in('all_original_bindings_and_modes_restored','all_final_owners_root','no_symlink_targets_followed','private_home_root_restored'):require(value[k]is True,'vendor_privilege_restore')
 require(value['security_accepted']is False and value['work_mode_before']==value['work_mode_restored']==0o700 and value['work_mode_during']==0o711,'vendor_privilege_modes')
 for k in('initial_tree','restored_tree'):
  p=value[k];require(type(p)is dict and set(p)=={'bytes','sha256'}and type(p['bytes'])is int and 0<p['bytes']<=16*1024**2
   and type(p['sha256'])is str and re.fullmatch('[a-f0-9]{64}',p['sha256']),'vendor_tree_binding')
 require(type(value['initial_entries'])is int and type(value['final_entries'])is int and 0<value['initial_entries']<=value['final_entries']<=50000,'vendor_tree_count')
 probe=value['identity_probe'];require(type(probe)is dict and set(probe)=={'argv','actual_returncode','observed','raw_output'}
  and probe['argv']==vendor_identity_argv()and type(probe['actual_returncode'])is int and probe['actual_returncode']==0,'vendor_identity_probe')
 observed=probe['observed'];expected={'uid':10001,'euid':10001,'gid':10001,'egid':10001,'groups':[],
  'home':str(WORK/'vendor-test-home'),'home_mode':0o700,'home_uid':10001,'home_gid':10001,'home_writable':True,'source_writable':True}
 require(type(observed)is dict and set(observed)==set(expected)and all(type(observed[k])is type(v)and observed[k]==v for k,v in expected.items()),'vendor_actual_identity')
 raw=encoded(observed);require(probe['raw_output']=={'bytes':len(raw),'sha256':sha256(raw).hexdigest()},'vendor_identity_output_binding')
 vendor_process_contract(value['process_tree'])
 return value

def vendor_close_fd(fd):
 os.close(fd)
 try:os.fstat(fd)
 except OSError as e:require(e.errno==errno.EBADF,'vendor_original_fd_close')
 else:raise RuntimeError('vendor_original_fd_still_open')
def vendor_tree(root,owners,end,*,change=None,reference=None):
 """Metadata-only traversal. Every chown uses the admitted directory FD and nofollow."""
 rows={};name_bytes=0;opened=closed=0
 def visit(fd,relative,depth):
  nonlocal name_bytes,opened,closed
  require(time.monotonic()<end and depth<=64,'vendor_tree_deadline_or_depth')
  s=os.fstat(fd);require(stat.S_ISDIR(s.st_mode),'vendor_directory_fd')
  def record(name,row,dirfd=None):
   nonlocal name_bytes
   require(time.monotonic()<end,'vendor_tree_deadline')
   require((row.st_uid,row.st_gid)in owners and (stat.S_ISDIR(row.st_mode)or stat.S_ISREG(row.st_mode)or stat.S_ISLNK(row.st_mode)),'vendor_tree_owner_or_type')
   require(stat.S_ISDIR(row.st_mode)or row.st_nlink==1,'vendor_tree_hardlink')
   key=relative if name is None else(relative+'/'+name if relative else name)
   require(len(key)<=1024 and key not in rows and len(rows)<50000,'vendor_tree_inventory')
   name_bytes+=len(key);require(name_bytes<=4*1024**2,'vendor_tree_names_bound')
   prior=None if reference is None else reference.get(key)
   links=0 if stat.S_ISDIR(row.st_mode)else row.st_nlink
   before=[row.st_dev,row.st_ino,row.st_mode,row.st_uid,row.st_gid,links]
   if prior is not None:require(before[:3]==prior[:3]and before[5]==prior[5],'vendor_original_mode_or_inode_changed')
   if change is not None:
    if name is None:os.fchown(fd,*change);after=os.fstat(fd)
    else:os.chown(name,*change,dir_fd=dirfd,follow_symlinks=False);after=os.stat(name,dir_fd=dirfd,follow_symlinks=False)
    require((after.st_dev,after.st_ino,after.st_mode,after.st_nlink)==(row.st_dev,row.st_ino,row.st_mode,row.st_nlink)
     and(after.st_uid,after.st_gid)==change,'vendor_chown_changed_identity_or_mode')
    row=after
   rows[key]=[row.st_dev,row.st_ino,row.st_mode,row.st_uid,row.st_gid,0 if stat.S_ISDIR(row.st_mode)else row.st_nlink]
  # Open every child directory without following links, then operate relative to
  # that held FD. Symlinks themselves may be owned; their targets are never read.
  with os.scandir(fd)as stream:
   names=[]
   for entry in stream:names.append(entry.name);require(len(names)<=50000,'vendor_directory_bound')
  for name in sorted(names):
   row=os.stat(name,dir_fd=fd,follow_symlinks=False)
   if stat.S_ISDIR(row.st_mode):
    child=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=fd);opened+=1
    try:
     require((os.fstat(child).st_dev,os.fstat(child).st_ino)==(row.st_dev,row.st_ino),'vendor_child_directory_changed')
     visit(child,relative+'/'+name if relative else name,depth+1)
    finally:
     vendor_close_fd(child);closed+=1
   else:record(name,row,fd)
  record(None,s)
 fd=os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC);opened+=1
 try:visit(fd,'',0)
 finally:vendor_close_fd(fd);closed+=1
 require(opened==closed and(reference is None or set(reference)<=set(rows)),'vendor_tree_finality_or_missing_original')
 return rows
VENDOR_PROCESS_SCOPE=None
def vendor_subreaper(value=None):
 # linux/prctl.h: SET_CHILD_SUBREAPER36, GET_CHILD_SUBREAPER37. x86_64
 # uses unsigned long for all four variadic arguments; pointer width matches.
 libc=ctypes.CDLL(None,use_errno=True);call=libc.prctl
 call.argtypes=[ctypes.c_int,ctypes.c_ulong,ctypes.c_ulong,ctypes.c_ulong,ctypes.c_ulong];call.restype=ctypes.c_int
 if value is not None:
  require(type(value)is int and value in(0,1),'vendor_subreaper_value')
  if call(36,value,0,0,0)!=0:raise OSError(ctypes.get_errno(),'vendor process-local subreaper set')
 actual=ctypes.c_int()
 if call(37,ctypes.addressof(actual),0,0,0)!=0:raise OSError(ctypes.get_errno(),'vendor process-local subreaper get')
 require(actual.value in(0,1)and(value is None or actual.value==value),'vendor_subreaper_state');return actual.value
def vendor_process_bytes(path,maximum):
 fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC)
 try:
  raw=os.read(fd,maximum+1);require(len(raw)<=maximum,'vendor_proc_bound');return raw
 finally:vendor_close_fd(fd)
def vendor_process_children():
 raw=vendor_process_bytes('/proc/self/task/'+str(os.getpid())+'/children',65536)
 words=raw.split();require(len(words)<=4096 and all(re.fullmatch(b'[1-9][0-9]{0,9}',p)for p in words),'vendor_child_inventory')
 values=[int(p)for p in words];require(len(values)==len(set(values)),'vendor_child_duplicate');return sorted(values)
def vendor_process_empty():
 require(vendor_process_children()==[],'vendor_children_remain')
 try:os.waitid(os.P_ALL,0,os.WEXITED|os.WNOHANG|os.WNOWAIT)
 except ChildProcessError as e:require(e.errno==errno.ECHILD,'vendor_no_child_wait');return errno.ECHILD
 raise RuntimeError('vendor_unexpected_waitable_or_running_child')
def vendor_process_begin(end):
 global VENDOR_PROCESS_SCOPE,VENDOR_RETIREMENT_UNPROVEN
 require(VENDOR_PROCESS_SCOPE is None and not VENDOR_RETIREMENT_UNPROVEN,'prior_vendor_process_scope_unproven')
 VENDOR_RETIREMENT_UNPROVEN=True
 require(signal.getsignal(signal.SIGCHLD)==signal.SIG_DFL,'vendor_SIGCHLD_scope')
 with os.scandir('/proc/self/task')as stream:
  tasks=[]
  for row in stream:tasks.append(row.name);require(len(tasks)<=2,'vendor_controller_threads')
 require(tasks==[str(os.getpid())],'vendor_controller_single_thread');vendor_process_empty()
 require(vendor_subreaper()==0,'vendor_borrowed_subreaper_refused')
 scope={'controller_pid':os.getpid(),'end':end,'retire_end':end-VENDOR_RESTORE_SECONDS,'fault':False,'commands':{},'armed':False}
 VENDOR_PROCESS_SCOPE=scope
 try:require(time.monotonic()<scope['retire_end']and vendor_subreaper(1)==1,'vendor_subreaper_arm');scope['armed']=True
 except BaseException:
  scope['fault']=True
  # No vendor child has been started. Restore only our process-local mutation
  # after proving the original child set is still empty; retain failed status.
  vendor_process_empty();vendor_subreaper(0);VENDOR_PROCESS_SCOPE=None
  raise
 return scope
def vendor_process_retire(scope,label,argv):
 global VENDOR_RETIREMENT_UNPROVEN
 require(scope is VENDOR_PROCESS_SCOPE and scope['armed']and not scope['fault']and os.getpid()==scope['controller_pid'],'vendor_process_scope_identity')
 require(label not in scope['commands']and type(argv)is list,'vendor_process_command_once')
 events=[];initial=None
 try:
  while True:
   require(time.monotonic()<scope['retire_end']and len(events)<4096,'vendor_process_retire_deadline_or_bound')
   children=vendor_process_children()
   if initial is None:initial=list(children)
   if not children:vendor_process_empty();break
   for pid in children:
    require(time.monotonic()<scope['retire_end'],'vendor_process_retire_deadline')
    fd=os.pidfd_open(pid,0)
    try:
     before=identity(os.fstat(fd));flags=(fcntl.fcntl(fd,fcntl.F_GETFD),fcntl.fcntl(fd,fcntl.F_GETFL))
     require(flags[0]&fcntl.FD_CLOEXEC,'vendor_pidfd_cloexec')
     raw=vendor_process_bytes('/proc/'+str(pid)+'/status',16384)
     fields={line.split(b':',1)[0]:line.split(b':',1)[1].split()for line in raw.splitlines()if b':'in line}
     ppid=fields.get(b'PPid');uids=fields.get(b'Uid');gids=fields.get(b'Gid')
     require(ppid==[str(scope['controller_pid']).encode()]and uids==[b'10001']*4 and gids==[b'10001']*4,'vendor_adopted_child_identity')
     rawstat=vendor_process_bytes('/proc/'+str(pid)+'/stat',4096);split=rawstat.rfind(b') ')
     require(rawstat.split(b' ',1)[0]==str(pid).encode()and split>0,'vendor_child_stat')
     statfields=rawstat[split+2:].split();require(len(statfields)>19 and statfields[1]==str(scope['controller_pid']).encode()and statfields[19].isdigit(),'vendor_child_start_identity')
     start_ticks=int(statfields[19]);require(start_ticks>0,'vendor_child_start_identity')
     # P_PIDFD3 is provided by Linux wait.h and native Python; WNOWAIT retains
     # this original child's identity until our final wait, even across setsid.
     require(os.P_PIDFD==3,'vendor_pidfd_wait_api')
     ready=os.waitid(os.P_PIDFD,fd,os.WEXITED|os.WNOHANG|os.WNOWAIT);sent=0
     if ready is None:
      signal.pidfd_send_signal(fd,signal.SIGKILL,None,0);sent=int(signal.SIGKILL)
     while ready is None:
      require(time.monotonic()<scope['retire_end'],'vendor_pidfd_wait_deadline')
      ready=os.waitid(os.P_PIDFD,fd,os.WEXITED|os.WNOHANG|os.WNOWAIT)
      if ready is None:time.sleep(min(.005,max(0,scope['retire_end']-time.monotonic())))
     require(ready.si_pid==pid and ready.si_uid==10001,'vendor_pidfd_wait_identity')
     result=os.waitid(os.P_PIDFD,fd,os.WEXITED)
     require(result==ready,'vendor_pidfd_final_wait_changed')
     require(identity(os.fstat(fd))==before and(fcntl.fcntl(fd,fcntl.F_GETFD),fcntl.fcntl(fd,fcntl.F_GETFL))==flags,'vendor_pidfd_changed')
     event={'pid':pid,'ppid':scope['controller_pid'],'uid':[10001]*4,'gid':[10001]*4,'start_ticks':start_ticks,
      'pidfd_identity':list(before),'pidfd_flags':list(flags),'signal':sent,
      'wait':{'si_pid':result.si_pid,'si_uid':result.si_uid,'si_code':result.si_code,'si_status':result.si_status},'pidfd_closed':False}
    finally:
     # One close attempt only; an uncertain close is never retried by this
     # routine or a later phase callback. Any exception latches the scope fault.
     vendor_close_fd(fd)
    event['pidfd_closed']=True;events.append(event)
  row={'argv':argv,'initial_child_pids':initial,'retired':events,'final_child_pids':[],'no_children_wait_errno':errno.ECHILD}
  scope['commands'][label]=row;VENDOR_RETIREMENT_UNPROVEN=False;return row
 except BaseException:
  scope['fault']=True;VENDOR_RETIREMENT_UNPROVEN=True;raise
def vendor_process_end(scope):
 global VENDOR_PROCESS_SCOPE,VENDOR_RETIREMENT_UNPROVEN
 require(scope is VENDOR_PROCESS_SCOPE and not scope['fault'],'vendor_process_retirement_unproven')
 vendor_process_retire(scope,'phase-finalizer',[])
 try:
  require(time.monotonic()<scope['end']and vendor_process_empty()==errno.ECHILD,'vendor_process_final_empty')
  require(vendor_subreaper(0)==0,'vendor_subreaper_restore')
  proof={'schema':'sqlite_vendor_process_tree/1','controller_pid':scope['controller_pid'],'initial_thread_ids':[scope['controller_pid']],
   'initial_child_pids':[],'original_subreaper':0,'armed_subreaper':1,'final_subreaper':0,'final_child_pids':[],
   'final_wait_errno':errno.ECHILD,'discovery_scope':'proc_self_task_children_only','signal_scope':'original_adopted_child_pidfd_only',
   'commands':scope['commands'],'security_accepted':False}
  VENDOR_PROCESS_SCOPE=None;VENDOR_RETIREMENT_UNPROVEN=False;return proof
 except BaseException:scope['fault']=True;VENDOR_RETIREMENT_UNPROVEN=True;raise
def vendor_process_contract(value):
 keys={'schema','controller_pid','initial_thread_ids','initial_child_pids','original_subreaper','armed_subreaper','final_subreaper',
  'final_child_pids','final_wait_errno','discovery_scope','signal_scope','commands','security_accepted'}
 require(type(value)is dict and set(value)==keys and value['schema']=='sqlite_vendor_process_tree/1','vendor_process_fields')
 pid=value['controller_pid'];require(type(pid)is int and 0<pid<2**31 and type(value['initial_thread_ids'])is list and all(type(p)is int for p in value['initial_thread_ids'])and value['initial_thread_ids']==[pid]
  and value['initial_child_pids']==value['final_child_pids']==[],'vendor_process_initial_scope')
 require(type(value['original_subreaper'])is int and type(value['final_subreaper'])is int and value['original_subreaper']==value['final_subreaper']==0
  and type(value['armed_subreaper'])is int and value['armed_subreaper']==1 and type(value['final_wait_errno'])is int
  and value['final_wait_errno']==errno.ECHILD and value['security_accepted']is False,'vendor_process_finality')
 require(value['discovery_scope']=='proc_self_task_children_only'and value['signal_scope']=='original_adopted_child_pidfd_only','vendor_process_authority')
 commands=value['commands'];expected={'identity':vendor_identity_argv(),**VENDOR_COMMANDS,'phase-finalizer':[]}
 require(type(commands)is dict and set(commands)==set(expected),'vendor_process_command_inventory')
 for name,argv in expected.items():
  row=commands[name];require(type(row)is dict and set(row)=={'argv','initial_child_pids','retired','final_child_pids','no_children_wait_errno'}
   and row['argv']==argv and row['final_child_pids']==[]and type(row['no_children_wait_errno'])is int and row['no_children_wait_errno']==errno.ECHILD,'vendor_process_command_finality')
  initial=row['initial_child_pids'];events=row['retired']
  require(type(initial)is list and len(initial)<=4096 and all(type(p)is int and 0<p<2**31 for p in initial)
   and initial==sorted(set(initial))and type(events)is list and len(events)<=4096,'vendor_process_event_bound')
  seen=set()
  for event in events:
   require(type(event)is dict and set(event)=={'pid','ppid','uid','gid','start_ticks','pidfd_identity','pidfd_flags','signal','wait','pidfd_closed'},'vendor_process_event_fields')
   child=event['pid'];start=event['start_ticks'];require(type(child)is int and 0<child<2**31 and type(start)is int and start>0
    and type(event['ppid'])is int and event['ppid']==pid and event['uid']==event['gid']==[10001]*4,'vendor_process_owned_event')
   require((child,start)not in seen,'vendor_process_duplicate_event');seen.add((child,start))
   frame=event['pidfd_identity'];flags=event['pidfd_flags']
   require(type(frame)is list and len(frame)==9 and all(type(n)is int for n in frame)
    and type(flags)is list and len(flags)==2 and all(type(n)is int for n in flags)and flags[0]&fcntl.FD_CLOEXEC
    and event['pidfd_closed']is True and type(event['signal'])is int and event['signal']==0,'vendor_process_pidfd_finality')
   wait=event['wait'];require(type(wait)is dict and set(wait)=={'si_pid','si_uid','si_code','si_status'}
    and all(type(v)is int for v in wait.values())and wait=={'si_pid':child,'si_uid':10001,'si_code':os.CLD_EXITED,'si_status':0},'vendor_process_descendant_failed')
  require(set(initial)<={event['pid']for event in events},'vendor_process_unaccounted_child')
 return value

def vendor_unprivileged_commands(src):
 """Loan the owned tree only inside a process-local, proven descendant scope."""
 global VENDOR_RETIREMENT_UNPROVEN
 require(os.geteuid()==os.getegid()==0 and src==WORK/'source'and src.resolve(strict=True)==src and WORK.resolve(strict=True)==WORK,'vendor_phase_scope')
 work=WORK.lstat();require(stat.S_ISDIR(work.st_mode)and work.st_uid==work.st_gid==0 and stat.S_IMODE(work.st_mode)==0o700,'vendor_work_scope')
 end=time.monotonic()+VENDOR_PHASE_SECONDS;home=WORK/'vendor-test-home';require(not os.path.lexists(home),'vendor_home_fresh')
 original=vendor_tree(src,{(0,0)},end);home.mkdir(mode=0o700);home_initial=vendor_tree(home,{(0,0)},end)
 current=WORK.lstat();require(identity(current)[:5]==identity(work)[:5],'vendor_work_changed_before_loan');work=current
 errors=[];logs={};probe=None;restored=None;home_restored=None;scope=None;process_proof=None
 workfd=os.open(WORK,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC)
 try:
  require(identity(os.fstat(workfd))==identity(work),'vendor_work_fd')
  scope=vendor_process_begin(end)
  os.fchmod(workfd,0o711)
  vendor_tree(src,{(0,0)},end,change=(VENDOR_UID,VENDOR_GID),reference=original)
  vendor_tree(home,{(0,0)},end,change=(VENDOR_UID,VENDOR_GID),reference=home_initial)
  def timeout(limit):
   remaining=min(limit,end-time.monotonic()-VENDOR_RESTORE_SECONDS);require(remaining>0,'vendor_phase_deadline');return remaining
  def run(label,argv,limit,maximum,tee=False):
   failures=[];result=None;retirement=None
   try:result=bounded_command(argv,cwd=src,timeout=timeout(limit),maximum=maximum,capture=True,with_code=True,tee=tee,unprivileged=True)
   except BaseException as e:failures.append(e)
   try:retirement=vendor_process_retire(scope,label,argv)
   except BaseException as e:failures.append(e)
   if result is not None and result[0]!=0:
    failures.append(RuntimeError('vendor_identity_command_refused'if label=='identity'else'vendor_test_command_refused',result[0]))
   if retirement is not None and any(event['signal']!=0 or event['wait']['si_code']!=os.CLD_EXITED or event['wait']['si_status']!=0 for event in retirement['retired']):
    failures.append(RuntimeError('vendor_descendant_outlived_or_failed_command'))
   if label=='vendor-standard.log'and result is not None and result[0]!=0 and retirement is not None and len(failures)==1 and not scope['fault']:
    try:vendor_failure_diagnostic(src,result[1],scope)
    except BaseException as e:failures.append(e)
   if failures:
    if len(failures)==1:raise failures[0]
    raise BaseExceptionGroup('vendor command and descendant retirement failures',failures)
   return result
  code,raw=run('identity',vendor_identity_argv(),30,4096)
  require(code==0,'vendor_identity_command_refused');observed=json.loads(raw)
  probe={'argv':vendor_identity_argv(),'actual_returncode':code,'observed':observed,'raw_output':{'bytes':len(raw),'sha256':sha256(raw).hexdigest()}}
  expected={'uid':10001,'euid':10001,'gid':10001,'egid':10001,'groups':[],'home':str(home),'home_mode':0o700,
   'home_uid':10001,'home_gid':10001,'home_writable':True,'source_writable':True}
  require(type(observed)is dict and set(observed)==set(expected)and all(type(observed[k])is type(v)and observed[k]==v for k,v in expected.items())and raw==encoded(observed),'vendor_actual_identity')
  for name,limit in(('vendor-standard.log',1200),('vendor-fts5-regression.log',180)):
   code,raw=run(name,VENDOR_COMMANDS[name],limit,VENDOR_LOG_LIMITS[name],True)
   require(code==0,'vendor_test_command_refused');logs[name]=raw
 except BaseException as e:errors.append(e)
 finally:
  if scope is not None:
   try:process_proof=vendor_process_end(scope)
   except BaseException as e:VENDOR_RETIREMENT_UNPROVEN=True;errors.append(e)
  if VENDOR_RETIREMENT_UNPROVEN:errors.append(RuntimeError('vendor_process_retirement_unproven'))
  for path,reference in(()if VENDOR_RETIREMENT_UNPROVEN else((src,original),(home,home_initial))):
   try:
    result=vendor_tree(path,{(0,0),(VENDOR_UID,VENDOR_GID)},end,change=(0,0),reference=reference)
    require({n:result[n]for n in reference}==reference,'vendor_original_bindings_not_restored')
    if path==src:restored=result
    else:home_restored=result
   except BaseException as e:errors.append(e)
  try:
   require((os.fstat(workfd).st_dev,os.fstat(workfd).st_ino)==(work.st_dev,work.st_ino),'vendor_work_changed')
   os.fchmod(workfd,0o700);require(os.fstat(workfd).st_uid==os.fstat(workfd).st_gid==0 and stat.S_IMODE(os.fstat(workfd).st_mode)==0o700,'vendor_work_restore')
  except BaseException as e:errors.append(e)
  finally:vendor_close_fd(workfd)
 if errors:
  if len(errors)==1:raise errors[0]
  raise BaseExceptionGroup('vendor phase and restoration failures',errors)
 require(time.monotonic()<end and restored is not None and home_restored is not None and process_proof is not None,'vendor_phase_finality')
 bound=lambda rows:{'bytes':len(encoded(rows)),'sha256':sha256(encoded(rows)).hexdigest()}
 receipt={'schema':'sqlite_vendor_unprivileged_phase/1','identity_probe':probe,'initial_tree':bound(original),'restored_tree':bound(restored),
  'initial_entries':len(original),'final_entries':len(restored),'all_original_bindings_and_modes_restored':True,'all_final_owners_root':True,
  'no_symlink_targets_followed':True,'work_mode_before':0o700,'work_mode_during':0o711,'work_mode_restored':0o700,
  'private_home_root_restored':True,'phase_seconds_limit':VENDOR_PHASE_SECONDS,'restore_reserve_seconds':VENDOR_RESTORE_SECONDS,
  'process_tree':process_proof,'security_accepted':False}
 return logs,vendor_privilege_contract(receipt)


VENDOR_TEST_COMPAT={'orig_archive': 'sqlite3_3.45.1.orig.tar.xz', 'distro_patch': 'debian/patches/31-increase_SQLITE_MAX_DEFAULT_PAGE_SIZE_to_32k.patch', 'distro_patch_pin': (779, '27af1c41614efba22f9eb1f1b4f46d49e3ac37a6087668a186ff2bc1bc4dcb75'), 'io_before': (18788, '941b60a0ca67ee9ace46b73deecb31998c5fca9944e1405637257ef4503e7f35'), 'io_after': (18788, 'b7ae8d3def6ab27074b21cf51df2e50dd50d34eb00ca671f32882f7033ce559e'), 'production': {'src/pager.c': (301475, '8b371bfcd459dc73415cb0d92232bf2962ad5fda0fdf4ea762d54e2247e7e474'), 'src/sqliteLimit.h': (6699, 'f5bffc74f8d25a88f173b9b6d5559985091299d3b362674294fda32660e9cd61')}, 'fuzzcheck': {'before': (81518, 'c8fa9f37b70d07cb4445c0910f079da6ce497f4095eeacec9d6e512fcd930704'), 'after': (81560, '982bb4a2f4f6e0e75a841081c7e282a3102d7916dd1a59f85c7a8e5bd249ca75'), 'implementations': {'test/vt02.c': (33516, '1685ac0e174719c33a075a3eaa367c8903ac6866047515b1c6996afc6a766116'), 'ext/misc/randomjson.c': (7430, '3ec3b5424c3cd86a7b2fcd9454a4cbc873614e152396ff388f1f4ac8a8af94ea')}}}
# Embedded unchanged into the three signed-source recipes; no operation on import.
IO_ORACLE_BEFORE = b'         {}                   16384      8192\n'
IO_ORACLE_AFTER = b'         {}                   16384     16384\n'

def vendor_test_compatibility_expected():
 """Exact test-only record, never a native-suite or security verdict."""
 def binding(pin):return {'bytes':pin[0],'sha256':pin[1]}
 spec=VENDOR_TEST_COMPAT
 fuzz=None
 if spec['fuzzcheck'] is not None:
  f=spec['fuzzcheck']
  fuzz={'upstream_checkin':'803481f250','file':'test/fuzzcheck.c',
   'before':binding(f['before']),'after':binding(f['after']),
   'implementations':{name:binding(pin)for name,pin in f['implementations'].items()},
   'exact_declarations_changed':2,'test_only':True,'crash_resolution_proven':False}
 return {'schema':'sqlite_test_only_vendor_compatibility/1',
  'phase':'AFTER_SUCCESSFUL_PACKAGE_BUILD_BEFORE_UNFILTERED_VENDOR_TESTS',
  'original_source_archive':binding(SOURCE_FILES[spec['orig_archive']]),
  'signed_distro_archive':binding(SOURCE_FILES['sqlite3_'+BASE_VERSION+'.debian.tar.xz']),
  'distro31_patch':{'path':spec['distro_patch'],'binding':binding(spec['distro_patch_pin'])},
  'unchanged_production_inputs':{name:binding(pin)for name,pin in spec['production'].items()},
  'io_oracle':{'file':'test/io.test','before':binding(spec['io_before']),'after':binding(spec['io_after']),
   'case':'io-5.5','simulated_sector_size':16384,'production_max_default_page_size':32768,
   'expected_before':8192,'expected_after':16384,'exact_rows_changed':1,'test_ids_removed':0},
  'fuzzcheck_prototypes':fuzz,'production_flags_changed':False,'test_selection_changed':False,
  'official_security_or_production_fix':False,'Ubuntu_crashes_resolved':False,'security_accepted':False}

def vendor_test_compatibility_contract(value):
 require(type(value)is dict and encoded(value)==encoded(vendor_test_compatibility_expected()),'vendor_test_compatibility_contract')
 return value

def rewrite_owned_vendor_test(path,before_pin,after_pin,replacements):
 """Rewrite this owned regular test inode only; retain partial/error truth."""
 path=Path(path);initial=identity(path.lstat());old=bytes_at(path,before_pin,maximum=131072)
 require(identity(path.lstat())==initial and initial[3:6]==(0,0,1),'vendor_test_rewrite_identity')
 new=old
 for before,after in replacements:
  require(type(before)is bytes and type(after)is bytes and before!=after and new.count(before)==1,'vendor_test_rewrite_once')
  new=new.replace(before,after,1)
 require((len(new),sha256(new).hexdigest())==after_pin,'vendor_test_rewrite_pin')
 fd=None;errors=[];after=None
 try:
  fd=os.open(path,os.O_RDWR|os.O_NOFOLLOW|os.O_CLOEXEC)
  require(identity(os.fstat(fd))==identity(path.lstat())==initial,'vendor_test_rewrite_identity')
  flags=(fcntl.fcntl(fd,fcntl.F_GETFD),fcntl.fcntl(fd,fcntl.F_GETFL));written=0;expected=initial
  while written<len(new):
   require(identity(os.fstat(fd))==identity(path.lstat())==expected
    and (fcntl.fcntl(fd,fcntl.F_GETFD),fcntl.fcntl(fd,fcntl.F_GETFL))==flags,'vendor_test_rewrite_changed')
   count=os.pwrite(fd,new[written:],written);require(count>0,'vendor_test_rewrite_short');written+=count
   expected=identity(os.fstat(fd));require(expected[:6]==initial[:6]and identity(path.lstat())==expected,'vendor_test_rewrite_changed')
  require(identity(os.fstat(fd))==identity(path.lstat())==expected,'vendor_test_rewrite_changed')
  os.ftruncate(fd,len(new));expected=identity(os.fstat(fd))
  require(expected[:6]==initial[:6]and identity(path.lstat())==expected,'vendor_test_rewrite_changed')
  os.fsync(fd);after=identity(os.fstat(fd));require(after==expected,'vendor_test_rewrite_changed')
  require(after[:6]==initial[:6]and after[6]==len(new)and identity(path.lstat())==after
   and os.pread(fd,len(new)+1,0)==new and os.lseek(fd,0,os.SEEK_CUR)==0
   and (fcntl.fcntl(fd,fcntl.F_GETFD),fcntl.fcntl(fd,fcntl.F_GETFL))==flags,'vendor_test_rewrite_final')
 except BaseException as e:errors.append(e)
 finally:
  if fd is not None:
   try:vendor_close_fd(fd)
   except BaseException as e:errors.append(e)
 if errors:
  if len(errors)==1:raise errors[0]
  raise BaseExceptionGroup('vendor test rewrite and close failures',errors)
 require(identity(path.lstat())==after and bytes_at(path,after_pin,maximum=131072)==new
  and identity(path.lstat())==after,'vendor_test_rewrite_readback')

def verify_vendor_test_compatibility(src,receipt):
 vendor_test_compatibility_contract(receipt);spec=VENDOR_TEST_COMPAT
 for name,pin in spec['production'].items():digest(src/name,pin,maximum=1024**2)
 digest(src/spec['distro_patch'],spec['distro_patch_pin'],maximum=4096)
 digest(src/'test/io.test',spec['io_after'],maximum=131072)
 if spec['fuzzcheck'] is not None:
  digest(src/'test/fuzzcheck.c',spec['fuzzcheck']['after'],maximum=131072)
  for name,pin in spec['fuzzcheck']['implementations'].items():digest(src/name,pin,maximum=131072)
 return receipt

def prepare_vendor_test_compatibility(src):
 # Exact full-file pins were derived from the already authenticated public
 # orig/distro archives. The distro patch changes only the production cap.
 spec=VENDOR_TEST_COMPAT
 for name,pin in spec['production'].items():digest(src/name,pin,maximum=1024**2)
 digest(src/spec['distro_patch'],spec['distro_patch_pin'],maximum=4096)
 digest(src/'test/io.test',spec['io_before'],maximum=131072)
 f=spec['fuzzcheck']
 if f is not None:
  digest(src/'test/fuzzcheck.c',f['before'],maximum=131072)
  for name,pin in f['implementations'].items():digest(src/name,pin,maximum=131072)
 rewrite_owned_vendor_test(src/'test/io.test',spec['io_before'],spec['io_after'],[(IO_ORACLE_BEFORE,IO_ORACLE_AFTER)])
 if f is not None:
  replacements=[(('extern int '+name+'(sqlite3*,char***,void*);').encode(),
   ('extern int '+name+'(sqlite3*,char**,const sqlite3_api_routines*);').encode())
   for name in('sqlite3_vt02_init','sqlite3_randomjson_init')]
  rewrite_owned_vendor_test(src/'test/fuzzcheck.c',f['before'],f['after'],replacements)
 return verify_vendor_test_compatibility(src,vendor_test_compatibility_expected())

def vendor_failure_diagnostic(src,failed_output,scope):
 """Failure-only public own-child trace; never heals the original make result."""
 require(type(failed_output)is bytes and len(failed_output)<=VENDOR_LOG_LIMITS['vendor-standard.log'],
  'vendor_diagnostic_input')
 target=None;cases=[]
 for n in range(1,9):
  corpus=str(src/'test'/('fuzzdata'+str(n)+'.db'))
  for sqlid,dbid in re.findall(re.escape(corpus.encode())+rb' \(sqlid=([0-9]{1,9}),dbid=([0-9]{1,9})\): segfault',failed_output):
   cases.append((corpus,int(sqlid),int(dbid)))
 if len(cases)==1:
  corpus,sqlid,dbid=cases[0];target=['./fuzzcheck','--dbid',str(dbid),'--sqlid',str(sqlid),corpus]
 elif b'tcltest] Segmentation fault'in failed_output:
  target=['./testfixture',str(src/'test/veryquick.test'),'--verbose=file','--output=diagnostic-test-out.txt']
 row={'schema':'sqlite_failed_vendor_diagnostic/1','diagnostic_only':True,'original_make_failure_retained':True,
  'security_accepted':False,'status':'NOT_APPLICABLE','seconds_limit':60,'output_limit':65536}
 if target is None:
  print(encoded(row).decode(),end='',flush=True);return
 started=time.monotonic();absolute_end=min(started+60,scope['retire_end']-5);remaining=absolute_end-started
 if remaining<=0 or not Path('/usr/bin/gdb').is_file():
  row['status']='UNAVAILABLE_DEADLINE_OR_GDB';print(encoded(row).decode(),end='',flush=True);return
 argv=['/usr/bin/gdb','--batch','--nx','--nh','--return-child-result',
  '-iex','set debuginfod enabled off','-iex','set auto-load off',
  '-ex','set startup-with-shell off','-ex','set disable-randomization off',
  '-ex','set pagination off','-ex','set confirm off','-ex','set print frame-arguments none',
  '-ex','handle SIGSEGV stop print nopass','-ex','run','-ex','thread apply all backtrace 24','--args',*target]
 row.update({'status':'ATTEMPTED','argv':argv,'seconds_limit':remaining,'absolute_end':absolute_end,
  'original_scope_retire_end':scope['retire_end']});print(encoded(row).decode(),end='',flush=True)
 errors=[];result=None
 try:
  require(time.monotonic()<absolute_end,'vendor_diagnostic_original_clock')
  result=bounded_command(argv,cwd=src,timeout=remaining,maximum=65536,capture=True,with_code=True,tee=True,unprivileged=True,absolute_end=absolute_end)
  require(time.monotonic()<absolute_end,'vendor_diagnostic_original_clock')
 except BaseException as e:errors.append(e)
 try:row['owned_descendant_retirement']=vendor_process_retire(scope,'failure-diagnostic',argv)
 except BaseException as e:errors.append(e)
 row.update({'status':'RETURNED'if result is not None else'FAILED_OR_UNAVAILABLE',
  'actual_returncode':None if result is None else result[0],
  'actual_public_output':None if result is None else result[1].decode('utf-8','replace'),
  'completed_output_available':result is not None,'bounded_prefix_tee_to_CI_log':True,
  'errors':[type(e).__name__ for e in errors]})
 print(encoded(row).decode(),end='',flush=True)
 if errors:raise BaseExceptionGroup('failure-only diagnostic and retirement failures',errors)

def vendor_tests(src):
 # Debian has already configured/built this exact source with its unchanged
 # rules/feature flags. Do not reconfigure, filter TESTOPTS, or substitute a suite.
 watched=('Makefile','debian/rules','debian/control','src/wal.c','ext/fts5/fts5_index.c','ext/fts5/test/fts5prefix2.test',*VENDOR_HELPERS)
 before={name:digest(src/name,maximum=4*1024**2)for name in watched}
 require(all(before[name]=={'bytes':pin[0],'sha256':pin[1]}for name,pin in VENDOR_HELPERS.items()),'vendor_naming_helper_changed')
 require(not any(k in current_environment()for k in('TESTOPTS','TESTS','EXTRA_QUICKTEST','SQLITE_TEST_PREFIX')),'vendor_test_filter')
 harness=prepare_vendor_testfixture(src)
 before['Makefile']=digest(src/'Makefile',maximum=4*1024**2)
 logs,privilege=vendor_unprivileged_commands(src)
 facts={name:vendor_log_contract(name,logs[name])for name in VENDOR_COMMANDS}
 require({name:digest(src/name,maximum=4*1024**2)for name in watched}==before,'vendor_source_or_flags_changed')
 return {'same_configured_source':before,'standard_and_official_regression':facts,
  'standard_scope':dict(VENDOR_STANDARD_SCOPE),
  'no_new_filters_or_exclusions':True,'no_new_filters_or_exclusions_scope':'test_selection_only',
  'testfixture_harness':harness,'full_all_permutations_claimed':False,
  'default_omissions_are_not_waivers':True,'unprivileged_execution':privilege},logs

def build():
 builder_context();configure_repository();proofs=inputs(INPUTS,RECIPE);require(not WORK.exists() and not OUT.exists(),'build_once')
 WORK.mkdir(mode=0o700);Path(ENV['HOME']).mkdir(mode=0o700,exist_ok=True)
 # The exact official Ubuntu DSC signer key is public, immutable and pinned.
 # Archive authentication is independently enforced by signed pinned InRelease.
 keyhome=WORK/'public-gnupg';keyhome.mkdir(mode=0o700)
 public=RECIPE/'source-signer.asc';digest(public,SIGNER_PIN)
 raw=metadata(['/usr/bin/gpg','--no-options','--homedir',str(keyhome),'--batch','--with-colons','--show-keys','--fingerprint',str(public)])
 fingerprints=[line.split(':')[9] for line in raw.decode('ascii').splitlines() if line.startswith('fpr:')]
 require(fingerprints and fingerprints[0]==SIGNER_FINGERPRINT,'ubuntu_source_signer')
 keyring=WORK/'source-signers.gpg'
 command(['/usr/bin/gpg','--no-options','--homedir',str(keyhome),'--batch','--dearmor','--output',str(keyring),str(public)],timeout=30)
 command(['/usr/bin/gpgv','--homedir',str(keyhome),'--keyring',str(keyring),str(INPUTS/('sqlite3_'+BASE_VERSION+'.dsc'))],timeout=30)
 baseline=positive(BASE_VERSION);base_lib=library(baseline['system_library']);base_elf=elf(base_lib)
 base_binary=digest(base_lib);base_python=digest(Path(sys.executable).resolve(),maximum=64*1024**2)
 # Preserve the genuine Debian build rules and flags, including their normal
 # internal Tcl-index generation. No new runtime loader variable is introduced.
 with tarfile.open(INPUTS/('sqlite3_'+BASE_VERSION+'.debian.tar.xz'),'r:xz') as archive:
  entry=archive.getmember('debian/rules');require(entry.isfile() and entry.size<=65536,'debian_rules')
  rules=archive.extractfile(entry).read(65537);require(len(rules)==entry.size,'debian_rules')
 rules_pin=(len(rules),sha256(rules).hexdigest())
 with tarfile.open(INPUTS/('sqlite3_'+BASE_VERSION+'.debian.tar.xz'),'r:xz') as archive:
  item=archive.getmember('debian/control');require(item.isfile() and item.size<=65536,'ubuntu_control')
  control=archive.extractfile(item).read(65537);require(len(control)==item.size,'ubuntu_control')
 control_pin=(len(control),sha256(control).hexdigest())
 src=WORK/'source'
 command(['/usr/bin/dpkg-source','-x',str(INPUTS/('sqlite3_'+BASE_VERSION+'.dsc')),str(src)],timeout=180)
 digest(src/'debian/rules',rules_pin);digest(src/'debian/control',control_pin);digest(src/'src/wal.c',WAL_BEFORE)
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
 digest(src/'src/wal.c',WAL_AFTER);digest(src/'ext/fts5/fts5_index.c',FTS_AFTER);digest(src/'ext/fts5/test/fts5prefix2.test',FTS_TEST_AFTER)
 changelog=src/'debian/changelog';original=changelog.read_bytes();require(original.startswith(('sqlite3 ('+BASE_VERSION+')').encode()),'changelog_base')
 date=email.utils.format_datetime(datetime.datetime.fromtimestamp(SOURCE_EPOCH,datetime.timezone.utc))
 entry=('sqlite3 ('+VERSION+') '+CODENAME+'; urgency=high\n\n'
  '  * Retain production WAL fix; add the official FTS5 merge bound fix.\n'
  '  * Keep upstream'+UPSTREAM+' ABI, Ubuntu build rules and feature flags.\n'
  '  * Require unchanged standard vendor make test and official FTS5 regression.\n\n'
  ' -- Trading Bot Security Build <security-build@localhost>  '+date+'\n\n').encode()
 changelog.write_bytes(entry+original)
 require(metadata(['/usr/bin/dpkg-parsechangelog','-S','Version'],cwd=src).decode().strip()==VERSION,'local_package_version')
 command(['/usr/bin/dpkg-buildpackage','-B','-uc','-us','-j2'],cwd=src,timeout=1800)
 compatibility=prepare_vendor_test_compatibility(src)
 vendor,vendor_logs=vendor_tests(src)
 verify_vendor_test_compatibility(src,compatibility)
 digest(src/'debian/rules',rules_pin);digest(src/'debian/control',control_pin);digest(src/'src/wal.c',WAL_AFTER)
 digest(src/'ext/fts5/fts5_index.c',FTS_AFTER);digest(src/'ext/fts5/test/fts5prefix2.test',FTS_TEST_AFTER)
 packages=list(WORK.glob('libsqlite3-0_'+VERSION+'_amd64.deb'));require(len(packages)==1,'runtime_package_inventory');package=packages[0]
 for field,expected in (('Package','libsqlite3-0'),('Version',VERSION),('Architecture','amd64'),('Multi-Arch','same')):
  require(metadata(['/usr/bin/dpkg-deb','-f',str(package),field]).decode().strip()==expected,'runtime_package_control')
 # Proper installation occurs only inside this disposable builder. Candidate
 # verification starts a new unchanged Python process through normal dpkg/ld.so.
 command(['/usr/bin/dpkg','--path-include=/usr/share/doc/libsqlite3-0',
  '--path-include=/usr/share/doc/libsqlite3-0/*','--install',str(package)],timeout=60)
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
 for name,raw in vendor_logs.items():
  publish(OUT/name,raw,maximum=VENDOR_LOG_LIMITS[name]);(OUT/name).chmod(0o644)
 copy_exact(package,target,(package_pin['bytes'],package_pin['sha256']),mode=0o644)
 metadata_proofs={}
 for item,binding in build_metadata:
  copy_exact(item,OUT/item.name,(binding['bytes'],binding['sha256']),mode=0o644)
  metadata_proofs[item.name]=binding
 report={'schema':'sqlite_wal_fts5_local_ubuntu_package/2','package':'libsqlite3-0','base_package_version':BASE_VERSION,
  'local_package_version':VERSION,'upstream_version_unchanged':UPSTREAM,'architecture':'amd64',
  'repository_snapshot':SNAPSHOT,'repository_releases':{suite:{'bytes':row[0],'sha256':row[1]} for suite,row in INRELEASES.items()},
  'public_ca_bootstrap':CA_BOOTSTRAP,'native_ca_certificates_package':metadata(['/usr/bin/dpkg-query','-W','-f=${Version}\n','ca-certificates']).decode('ascii').strip(),
  'native_bundle_replaces_bootstrap_after_authenticated_install':True,
  'base_image':BASE_IMAGE,'root_observed_consumer_profile':{'bytes':PROFILE_PIN[0],'sha256':PROFILE_PIN[1]},
  'source_signer':{'fingerprint':SIGNER_FINGERPRINT,'bytes':SIGNER_PIN[0],'sha256':SIGNER_PIN[1]},
  'source_files':proofs,'source_maintainer_signature_verified':True,'debian_rules':{'bytes':rules_pin[0],'sha256':rules_pin[1]},
  'ubuntu_control':{'bytes':control_pin[0],'sha256':control_pin[1]},
  'production_patch':{'bytes':PATCH_PIN[0],'sha256':PATCH_PIN[1]},
  'fts5_merge_patch':{'bytes':FTS_PATCH_PIN[0],'sha256':FTS_PATCH_PIN[1]},'fts5_upstream_regression_patch':{'bytes':FTS_TEST_PATCH_PIN[0],'sha256':FTS_TEST_PATCH_PIN[1]},
  'official_fts5_commit':UPSTREAM_FTS_COMMIT,'fts5_before':{'bytes':FTS_BEFORE[0],'sha256':FTS_BEFORE[1]},'fts5_after':{'bytes':FTS_AFTER[0],'sha256':FTS_AFTER[1]},
  'vendor_tests':vendor,'test_only_compatibility':compatibility,'wal_before':{'bytes':WAL_BEFORE[0],'sha256':WAL_BEFORE[1]},
  'wal_after':{'bytes':WAL_AFTER[0],'sha256':WAL_AFTER[1]},'positive_verifier':{'bytes':VERIFY_PIN[0],'sha256':VERIFY_PIN[1]},
  'source_date_epoch':SOURCE_EPOCH,'build_options':ENV['DEB_BUILD_OPTIONS'],'upstream_fault_suites_run':False,
  'wal_reset_reproduction_performed':False,'full_upstream_suite_pass_claimed':False,
  'baseline_library':base_binary,'candidate_library':new_binary,'package_file':{'name':target.name,**package_pin},
  'local_build_metadata':metadata_proofs,'local_build_metadata_signed':False,
  'baseline_authenticated_reinstall_required':True,'strict_full_package_payload_verification':True,
  'package_doc_path_includes':['/usr/share/doc/libsqlite3-0','/usr/share/doc/libsqlite3-0/*'],
  'compile_options_equal':True,'elf_soname_exports_dependencies_equal':True,'python_binary_unchanged':True,
  'sqlite_stdlib_extension_unchanged':True,'mapped_installed_libraries_verified':True,
  'build_toolchain_packages':metadata(['/usr/bin/dpkg-query','-W','-f=${binary:Package} ${Version}\n',
   'gcc','g++','libc6','libc6-dev','binutils','dpkg-dev','debhelper','libtool','autoconf','automake','gdb']).decode('ascii').splitlines(),
  'runtime_loader_override_used':False,'production_data_accessed':False,'app_ci_restore_gate_required':True,'security_accepted':False}
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
  inputs(INPUTS,RECIPE);configure_repository();result={'schema':'sqlite_builder_repository/1','snapshot':SNAPSHOT,'release_sha256':{suite:row[1] for suite,row in INRELEASES.items()},'build_performed':False,'security_accepted':False}
 elif args.build:result=build()
 else:
  result={'schema':'sqlite_recipe_unarmed/1','required_public_inputs':SOURCE_FILES,'build_performed':False,'artifact_created':False,'security_accepted':False}
 print(json.dumps(result,sort_keys=True));return 0

if __name__=='__main__':raise SystemExit(main())
