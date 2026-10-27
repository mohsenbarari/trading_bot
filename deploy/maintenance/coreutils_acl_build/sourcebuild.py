"""Source-only normal SDK and Coreutils caller package recipe; default is unarmed.
Root/CI alone invokes this in the exact disposable Trixie builder. No imports
perform I/O. Runtime libacl is reinstalled from its unchanged approved DEB.
"""
from hashlib import sha256
from pathlib import Path
import argparse,io,json,os,re,select,selectors,signal,stat,subprocess,sys,tarfile,time,urllib.request
from types import ModuleType

RECIPE=Path('/recipe');INPUTS=Path('/inputs');WORK=Path('/build/coreutils-acl');OUT=Path('/out')
VERSION='9.7-3+gold1'
SDK_VERSION='2.4.0-1~trixie+gold1'
SOURCE_EPOCH=1793059200
SOURCE_FILES={'acl_2.4.0-1.dsc': (2624, 'b987ebbeb3d498794bad29cd65301b77384978493d8765c94dba8bad8fa325b6'), 'acl_2.4.0.orig.tar.xz': (384828, 'e661131456d2708a01c614a0f400e11d7d1bfaeb6f3e74b75bb980b72f0161a3'), 'acl_2.4.0.orig.tar.xz.asc': (833, '5f4f0b9b78821764fe6b88e32aef6ec519628522c211c36706bcb78d6f3f036b'), 'acl_2.4.0-1.debian.tar.xz': (47932, '65931c2fb3e821bda67f8d8d72d77e99ac61502748dcdf38b6805fe89339085e'), 'coreutils_9.7-3.dsc': (2122, 'c3a207e3aaad165765c7a6fab89045f5fd20035fea6830b9f9ebbb92ebfbff89'), 'coreutils_9.7.orig.tar.xz': (6158960, 'e8bb26ad0293f9b5a1fc43fb42ba970e312c66ce92c1b0b16713d7500db251bf'), 'coreutils_9.7.orig.tar.xz.asc': (833, '5070e9428bd372a7fa1f66b395b81092bb40c98f9f56a2e2bc55c47fd387e901'), 'coreutils_9.7-3.debian.tar.xz': (26820, '483f77876a080577f63da1d004cc1cf17d16df65d6925aefdd3294c6101eccfb'), 'attr-2.6.0.tar.xz': (343608, '6c8a2148a7b85043b68492bce43316b0e2e214fc4e628c7ede078e76e216330b')}
OLD_PACKAGES={'libacl1_2.4.0-1~trixie+gold1_amd64.deb': (37076, 'bbb12e190bfe19b876f73a669848ebd8d04293d95000babf51058c88879966b1')}
PATCH='0001-coreutils-bound-acl-callers.patch';PATCH_PIN=(60929,'3c91b4386c7608b4620422daaf88ec561d3bc709140ae29dc904ed54a9833d1d')
CHANGELOG='0002-coreutils-unfiltered-vendor-tests.patch';CHANGELOG_PIN=(304,'aac49190579f7b17df5d9cd6cb0d622a1b7ebe78950e3b8e27aeb1825441541d')
SOURCE_MANIFEST_PIN=(10477, '600d00b191cdd271ba2c65abb6aa9c3c8f992d47a7d35c0e8572bc33786d6cda')
FIXTURE_PIN=(9979, '5a716da021e7c0e72058f4bf1537b44fae28a5c15fa445d5d99905d9f6025936')
CA_PIN=(222073,'e9c7b63ee2623386e9b0a3ee32f499d01a8ea23dc83b8383ddff9f8e749543b7')
RECIPE_FILES={'ordinary_fd_acl.c', 'local-source-packet.json', 'root-source-approval.json', 'sourcebuild.py', 'owned-fixture.mk', 'Dockerfile', 'ordinary_gnulib_fd0.c', 'print-vendor-selection.mk', 'verify_ordinary.py', 'gnulib-fd0-fixture.mk', 'verify_vendor_suite.py', '0002-coreutils-unfiltered-vendor-tests.patch', 'source-manifest.json', '0001-coreutils-bound-acl-callers.patch'}
ENV={'PATH':'/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin','HOME':'/work/home','LC_ALL':'C.UTF-8','LANG':'C.UTF-8',
 'DEBIAN_FRONTEND':'noninteractive','SOURCE_DATE_EPOCH':str(SOURCE_EPOCH),
 'DEB_BUILD_OPTIONS':'parallel=2','DEB_BUILD_PROFILES':'noudeb','QUILT_PATCHES':'debian/patches','QUILT_PATCH_OPTS':'--fuzz=0'}
MAX_METADATA=4*1024**2
SDK_FAMILY={'acl','libacl1','libacl1-dev'}
LEGACY={'acl_get_file','acl_set_file','acl_extended_file','acl_delete_def_file'}
NEW_APIS={'acl_get_file_at','acl_set_file_at','acl_extended_file_at','acl_delete_def_file_at'}
OUTPUTS={'coreutils_9.7-3+gold1_amd64.deb': 67108864, 'coreutils_9.7-3+gold1_amd64.changes': 4194304, 'coreutils_9.7-3+gold1_amd64.buildinfo': 4194304, 'acl_2.4.0-1~trixie+gold1_amd64.changes': 4194304, 'acl_2.4.0-1~trixie+gold1_amd64.buildinfo': 4194304, 'source-proof.json': 4194304, 'sdk-proof.json': 4194304, 'abi-contract.json': 4194304, 'ordinary-compatibility.json': 4194304, 'provenance.json': 4194304, 'runtime-payload.json': 4194304, 'caller-proof.json': 4194304, 'vendor-suite.json': 4194304, 'generated-build-proof.json': 4194304}


def require(ok,code):
 if not ok:raise RuntimeError(code)

def identity(s):return (s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)

def encoded(value,maximum=MAX_METADATA):
 parts=[];count=0
 for part in json.JSONEncoder(sort_keys=True,separators=(',',':'),allow_nan=False).iterencode(value):
  raw=part.encode();require(count+len(raw)+1<=maximum,'json_output_bound');count+=len(raw);parts.append(raw)
 return b''.join(parts)+b'\n'

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
  and 0<expected[0]<=64*1024**2 and type(expected[1]) is str
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

def current_environment():
 require(not any(n.startswith('LD_') or n in {'PYTHONPATH','PYTHONHOME'} for n in os.environ),'loader_override')
 return dict(ENV)
def run(argv,*,cwd=None,timeout=60,maximum=4*1024**2,capture=True,user=None,with_code=False):
 require(type(argv)is list and argv and all(type(v)is str for v in argv)and user in (None,10001),'command_scope')
 credentials={}
 if user==10001:
  if os.geteuid()==0:credentials={'user':10001,'group':10001,'extra_groups':[]}
  else:require(os.getuid()==os.geteuid()==os.getgid()==os.getegid()==10001,'actual_builder_user')
 child=subprocess.Popen(argv,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
  env=current_environment(),cwd=cwd,close_fds=True,start_new_session=True,umask=0o022,
  **credentials)
 end=time.monotonic()+timeout;parts=[];count=0
 try:
  with selectors.DefaultSelector()as selector:
   fd=child.stdout.fileno();os.set_blocking(fd,False);selector.register(fd,selectors.EVENT_READ)
   while True:
    left=end-time.monotonic();require(left>0,'command_timeout')
    if not selector.select(min(1,left)):continue
    part=os.read(fd,min(65536,maximum-count+1))
    if not part:break
    count+=len(part);require(count<=maximum,'command_output_bound')
    if capture:parts.append(part)
    else:forward(part,end)
  code=child.wait(timeout=max(.1,end-time.monotonic()))
  if with_code:require(capture is True,'capture_contract')
  else:require(code==0,'command_failed')
 except BaseException:
  cleanup_owned(child);raise
 finally:child.stdout.close()
 require(child.stdout.closed and child.returncode is not None,'command_physically_closed')
 return (code,b''.join(parts))if with_code else b''.join(parts)if capture else None
def command(argv,*,cwd=None,timeout=600,user=None):return run(argv,cwd=cwd,timeout=timeout,maximum=64*1024**2,capture=False,user=user)
def metadata(argv,*,cwd=None,user=None):return run(argv,cwd=cwd,timeout=30,maximum=MAX_METADATA,user=user)
def owned_read(path,*,maximum=4*1024**2):
 path=Path(path);require(path.is_relative_to(WORK)and path.resolve(strict=True)==path,'owned_path')
 before=identity(path.lstat());require(stat.S_ISREG(before[2])and before[3]in (0,10001)and before[4]in (0,10001)
  and before[5]==1 and not before[2]&0o022 and 0<before[6]<=maximum,'owned_file')
 fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC)
 try:
  require(identity(os.fstat(fd))==before,'owned_identity');raw=os.pread(fd,before[6]+1,0)
  require(len(raw)==before[6]and identity(os.fstat(fd))==identity(path.lstat())==before,'owned_changed')
 finally:os.close(fd)
 require(identity(path.lstat())==before,'owned_closed_changed');return raw

def uid_idle():
 """Only this disposable container's bounded UID metadata; never cmdline/Env."""
 end=time.monotonic()+5
 while True:
  alive=0;entries=0
  with os.scandir('/proc')as rows:
   for row in rows:
    entries+=1;require(entries<=1024,'pid_bound')
    if not row.name.isdigit():continue
    try:
     fd=os.open('/proc/'+row.name+'/status',os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
     try:raw=os.read(fd,65537);require(len(raw)<=65536,'status_bound')
     finally:os.close(fd)
    except FileNotFoundError:continue
    users=[line for line in raw.splitlines()if line.startswith(b'Uid:')];require(len(users)==1,'uid_metadata')
    values=users[0].split()[1:];require(len(values)==4 and all(v.isdigit()for v in values),'uid_metadata')
    alive+=int(b'10001'in values)
  if not alive:return
  require(time.monotonic()<end,'sdk_children_remain');time.sleep(.1)

def builder_context():
 require(os.geteuid()==os.getegid()==0 and Path.cwd()==Path('/build')and Path('/.dockerenv').is_file(),'disposable_root_builder')
 require(sys.version_info[:3]==(3,11,17)and sys.flags.isolated==sys.flags.no_site==1 and os.uname().machine=='x86_64','builder_python')
 fields=dict(line.split('=',1)for line in bytes_at(Path('/etc/os-release').resolve(),maximum=65536).decode().splitlines()if'='in line)
 require(fields.get('ID','').strip('"')=='debian'and fields.get('VERSION_CODENAME','').strip('"')=='trixie','trixie_only')
 current_environment();ctx=json.loads(bytes_at(RECIPE/'context-manifest.json'))
 expected=RECIPE_FILES|{'inputs/'+n for n in OLD_PACKAGES}
 require(type(ctx)is dict and set(ctx)=={'schema','files'}and ctx['schema']=='coreutils_acl_allowlisted_context/1'
  and type(ctx['files'])is dict and set(ctx['files'])==expected,'context_inventory')
 for name,pin in ctx['files'].items():
  require(type(pin)is dict and set(pin)=={'bytes','sha256'}and type(pin['bytes'])is int and 0<pin['bytes']<=32*1024**2
   and type(pin['sha256'])is str and re.fullmatch('[a-f0-9]{64}',pin['sha256']),'context_binding')
  digest(RECIPE/name,(pin['bytes'],pin['sha256']))
 for name,pin in OLD_PACKAGES.items():digest(INPUTS/name,pin)
 for name,pin in ((PATCH,PATCH_PIN),(CHANGELOG,CHANGELOG_PIN),('source-manifest.json',SOURCE_MANIFEST_PIN),('ordinary_fd_acl.c',FIXTURE_PIN)):digest(RECIPE/name,pin)
 return ctx

def source_signature(path):
 raw=metadata(['/usr/bin/gpgv','--status-fd','1','--keyring','/usr/share/keyrings/debian-keyring.gpg',str(path)])
 rows=[line.split()for line in raw.splitlines()if line.startswith(b'[GNUPG:] VALIDSIG ')]
 require(len(rows)==1 and len(rows[0])>=3 and re.fullmatch(rb'[A-F0-9]{40}',rows[0][2]),'dsc_signature')
 return {'status':'VALIDSIG','signer_fingerprint':rows[0][2].decode(),'dsc':digest(path)}

def configure_repository():
 builder_context();require(not WORK.exists(),'fresh_build_required');digest(Path('/etc/ssl/certs/ca-certificates.crt'),CA_PIN)
 Path('/work').mkdir(mode=0o755,exist_ok=True);os.chown('/work',10001,10001);Path('/work/home').mkdir(mode=0o700,exist_ok=True);os.chown('/work/home',10001,10001)
 for path in Path('/etc/apt/sources.list.d').glob('*'):
  require(path.is_file()and not path.is_symlink(),'repository_file');path.unlink()
 Path('/etc/apt/sources.list').write_text('')
 stanzas='Types: deb deb-src\nURIs: https://snapshot.debian.org/archive/debian/20261024T000000Z/\nSuites: trixie trixie-updates\nComponents: main\nSigned-By: /usr/share/keyrings/debian-archive-keyring.gpg\nCheck-Valid-Until: no\n\nTypes: deb deb-src\nURIs: https://snapshot.debian.org/archive/debian-security/20261024T000000Z/\nSuites: trixie-security\nComponents: main\nSigned-By: /usr/share/keyrings/debian-archive-keyring.gpg\nCheck-Valid-Until: no\n\nTypes: deb-src\nURIs: https://snapshot.debian.org/archive/debian/20260820T000000Z/\nSuites: forky\nComponents: main\nSigned-By: /usr/share/keyrings/debian-archive-keyring.gpg\nCheck-Valid-Until: no\n'
 Path('/etc/apt/sources.list.d/pinned.sources').write_text(stanzas)
 policy=Path('/usr/sbin/policy-rc.d');policy.write_text('#!/bin/sh\nexit 101\n');policy.chmod(0o755)
 apt=['/usr/bin/apt-get','-o','Acquire::Retries=0','-o','Acquire::https::CaInfo=/etc/ssl/certs/ca-certificates.crt']
 command([*apt,'-o','APT::Update::Error-Mode=any','update'],timeout=600)
 command([*apt,'-y','--no-install-recommends','build-dep','--build-profiles=noudeb','--only-source','acl=2.4.0-1','coreutils=9.7-3'],timeout=900)
 command([*apt,'-y','--no-install-recommends','install','build-essential','dpkg-dev','binutils','ca-certificates','pkgconf','patch','debian-keyring','gpgv','util-linux','passwd','autoconf','automake','autopoint','dh-autoreconf','fakeroot','locales-all','acl','attr'],timeout=600)
 command(['/usr/sbin/groupadd','--gid','10001','candidate-builder'],timeout=30)
 command(['/usr/sbin/useradd','--uid','10001','--gid','10001','--home-dir','/work/home','--no-create-home','--shell','/usr/sbin/nologin','candidate-builder'],timeout=30)
 downloads=Path('/build/source-inputs');downloads.mkdir(mode=0o755)
 command([*apt,'source','--download-only','--only-source','acl=2.4.0-1','coreutils=9.7-3'],cwd=downloads,timeout=600)
 require({p.name for p in downloads.iterdir()}==set(SOURCE_FILES)-{'attr-2.6.0.tar.xz'},'source_input_inventory')
 for name,pin in SOURCE_FILES.items():
  if name!='attr-2.6.0.tar.xz':digest(downloads/name,pin)
 # Maintained private compatibility provenance is official GNU HTTPS + exact
 # source pin, distinct from the signed Debian APT/DSC authentication chain.
 name='attr-2.6.0.tar.xz';pin=SOURCE_FILES[name];parts=[];count=0
 with urllib.request.urlopen('https://download.savannah.nongnu.org/releases/attr/'+name,timeout=60)as stream:
  require(stream.url.startswith('https://'),'attr_https')
  while True:
   part=stream.read(min(65536,pin[0]-count+1))
   if not part:break
   require(count+len(part)<=pin[0],'attr_source_bound');count+=len(part);parts.append(part)
 raw=b''.join(parts);require((len(raw),sha256(raw).hexdigest())==pin,'attr_source_pin');publish(downloads/name,raw);(downloads/name).chmod(0o644)
 signatures={name:source_signature(downloads/name)for name in ('acl_2.4.0-1.dsc','coreutils_9.7-3.dsc')}
 release=[]
 for path in sorted(Path('/var/lib/apt/lists').glob('*InRelease')):
  require(len(release)<8,'release_inventory');release.append({'name':path.name,**digest(path,maximum=1024**2)})
 require(3<=len(release)<=4,'release_inventory')
 publish(Path('/build/preparation.json'),encoded({'schema':'coreutils_acl_authenticated_preparation/1','releases':release,
  'source_inputs':{n:{'bytes':p[0],'sha256':p[1]}for n,p in SOURCE_FILES.items()},'binary_repository_family':'trixie_only',
  'forky_source_only':True,'maintainer_dsc_signatures':signatures,'maintainer_dsc_signatures_authenticated':True,
  'attr_official_https_exact_pin_not_debian_apt':True,'security_accepted':False}))

def package_fields(path):
 raw=metadata(['/usr/bin/dpkg-deb','-f',str(path),'Package','Version','Architecture']).decode();rows=[line.split(': ',1)for line in raw.splitlines()]
 require(all(len(v)==2 for v in rows),'package_fields');value=dict(rows);require(set(value)=={'Package','Version','Architecture'},'package_fields');return value

def family_outputs(directory,family,version):
 rows=[];seen=set();paths=list(directory.glob('*.deb'));require(0<len(paths)<=12,'package_count')
 for path in sorted(paths):
  fields=package_fields(path);name=fields['Package'];require(name not in seen and name.removesuffix('-dbgsym')in family
   and fields['Version']==version and fields['Architecture']in {'amd64','all'},'package_identity');seen.add(name)
  os.chown(path,0,0);path.chmod(0o644);rows.append({'name':name,'path':str(path),'version':version,**digest(path,maximum=64*1024**2)})
 require({n for n in seen if not n.endswith('-dbgsym')}==family,'package_family');return rows

def observed(path,maximum=16*1024**2):
 raw=owned_read(path,maximum=maximum);return {'bytes':len(raw),'sha256':sha256(raw).hexdigest()}

def public_guards():
 rows=[]
 for path in (WORK/'public-inputs').iterdir():
  frame=identity(path.lstat());require(stat.S_ISREG(frame[2])and frame[3:6]==(0,0,1)and not frame[2]&0o022,'public_guard');rows.append((path,frame))
 def check():
  require(all(p.resolve(strict=True)==p and identity(p.lstat())==frame for p,frame in rows),'public_source_changed')
 return check

def source_contract(source,manifest,phase):
 for name,pins in manifest['files'].items():
  target=source/name;pin=pins.get('after_test_hook',pins['after'])if phase=='after'else pins['before']
  if pin is None:require(not os.path.lexists(target),'source_before_new_file')
  else:require(observed(target)==pin,'source_'+phase+'_patch')

def vendor_tests():
 require(os.getuid()==os.geteuid()==os.getgid()==os.getegid()==10001 and Path.cwd()==WORK/'coreutils/source','actual_vendor_test_context')
 require(ENV['DEB_BUILD_OPTIONS']=='parallel=2'and 'nocheck'not in ENV['DEB_BUILD_OPTIONS'],'unfiltered_test_environment')
 public=WORK/'public-inputs';guard=public_guards();raw=bytes_at(public/'verify_vendor_suite.py',(13055,'3db1e3bf662282fbeea5c43c68713f01d5f9996fdfb202bbca87fdf6d55bafcd'),maximum=65536)
 module=ModuleType('coreutils_vendor_results');exec(compile(raw,str(public/'verify_vendor_suite.py'),'exec'),module.__dict__)
 fragment=public/'print-vendor-selection.mk';chosen={};generated={}
 for name,scope in [('coreutils',Path.cwd()),('gnulib',Path.cwd()/'gnulib-tests')]:
  generated[name]=observed(scope/'Makefile');chosen[name]=metadata(['/usr/bin/make','--no-print-directory','-s','-f','Makefile','-f',str(fragment),'gold-print-vendor-selection'],cwd=scope);module.selection(chosen[name],name);guard()
 # This is the explicit additional Debian hook. It invokes the ordinary
 # debhelper runner without test selections, exclusions, or test-body edits.
 started=time.time_ns();end=time.monotonic()+9000;command(['/usr/bin/dh_auto_test'],cwd=Path.cwd(),timeout=8400);guard()
 # The direct child and pipe are physically closed in run(). Any descendant
 # that kept the inherited output pipe open would prevent that EOF/completion.
 observation=module.collect(chosen,guard=guard,end=min(end,time.monotonic()+300),started_at_ns=started);guard()
 require(all(observed((Path.cwd()if n=='coreutils'else Path.cwd()/'gnulib-tests')/'Makefile')==v for n,v in generated.items()),'configured_selection_changed')
 receipt={'schema':'coreutils_unfiltered_vendor_command/1','actual_command':['/usr/bin/dh_auto_test'],'actual_returncode':0,
  'actual_uid':os.getuid(),'actual_gid':os.getgid(),'actual_pipe_closed':True,'configured_default_selections_captured_before_test':True,
  'test_filter_override_used':False,'test_bodies_modified':False,'observation':observation,'security_accepted':False}
 publish(WORK/'coreutils/vendor-suite.json',encoded(receipt));guard()

def build_vendor(kind):
 require(kind in {'sdk','coreutils'},'vendor_kind');directory=WORK/kind;directory.mkdir(mode=0o755);os.chown(directory,10001,10001)
 dsc='acl_2.4.0-1.dsc'if kind=='sdk'else'coreutils_9.7-3.dsc';source=directory/'source'
 command(['/usr/bin/dpkg-source','-x',str(WORK/'public-inputs'/dsc),str(source)],cwd=directory,timeout=120,user=10001)
 uid_idle();version=SDK_VERSION if kind=='sdk'else VERSION;manifest=None
 changelog=source/'debian/changelog';old=owned_read(changelog)
 require(old.startswith(('acl (2.4.0-1)'if kind=='sdk'else'coreutils (9.7-3)').encode()),'source_version')
 if kind=='coreutils':
  manifest=json.loads(bytes_at(RECIPE/'source-manifest.json',SOURCE_MANIFEST_PIN));source_contract(source,manifest,'before')
  original_bindings=original_source_bindings(source);test_sources=test_source_inventory(source);configure_before=observed(source/'configure')
  for patch in (PATCH,CHANGELOG):command(['/usr/bin/patch','-p1','--batch','--fuzz=0','--no-backup-if-mismatch','-i',str(WORK/'public-inputs'/patch)],cwd=source,timeout=30,user=10001)
  source_contract(source,manifest,'after')
 package='acl'if kind=='sdk'else'coreutils'
 prefix=(package+' ('+version+') trixie; urgency=medium\n\n  * Local isolated security vendor build; private Coreutils caller fix only.\n  * No libattr ABI or separately approved ACL runtime promotion.\n\n -- Project local build <local-build@invalid>  Tue, 27 Oct 2026 00:00:00 +0000\n\n').encode();changelog.write_bytes(prefix+old)
 packaging={n:observed(source/'debian'/n)for n in ('rules','control','changelog')}
 guard=public_guards();guard();command(['/usr/bin/dpkg-buildpackage','-us','-uc','-b','-Pnoudeb'],cwd=source,timeout=1800 if kind=='sdk'else 10800,user=10001);uid_idle();guard()
 require(all(observed(source/'debian'/n)==h for n,h in packaging.items()),'vendor_packaging_changed');os.chown(directory,0,0)
 rows=family_outputs(directory,SDK_FAMILY if kind=='sdk'else{'coreutils'},version);records={}
 for suffix in ('changes','buildinfo'):
  paths=list(directory.glob('*.'+suffix));require(len(paths)==1,'build_record_inventory');path=paths[0];os.chown(path,0,0);path.chmod(0o644);records[suffix]=path
 if kind=='coreutils':
  source_contract(source,manifest,'after');config=owned_read(source/'config.h',maximum=1024**2).decode()
  require('#define GOLD_DESCRIPTOR_ACL 1'in config,'compiled_api_configuration')
  verify_test_sources(source,test_sources)
  publish(directory/'source-contract.json',encoded({'schema':'coreutils_authenticated_source_contract/1','original_pinned_source_files':original_bindings,
   'unchanged_original_vendor_tests':test_sources,'only_test_hook_delta':{'bytes':CHANGELOG_PIN[0],'sha256':CHANGELOG_PIN[1]},'security_accepted':False}))
  publish(directory/'generated-build-proof.json',encoded(generated_proof(source,configure_before)))
  receipt=json.loads(owned_read(directory/'vendor-suite.json'));require(receipt['schema']=='coreutils_unfiltered_vendor_command/1'and receipt['actual_returncode']==0 and receipt['actual_uid']==receipt['actual_gid']==10001,'actual_vendor_suite_required')
 return source,rows,records,packaging

def install_packages(paths,*,documentation,complete_package_payload=False):
 flags=[]
 for name in documentation:
  flags+=['--path-include=/usr/share/doc/'+name,'--path-include=/usr/share/doc/'+name+'/*']
 if complete_package_payload:
  for root in ('/usr/share/locale','/usr/share/man','/usr/share/info'):
   flags+=['--path-include='+root,'--path-include='+root+'/*']
 command(['/usr/bin/dpkg',*flags,'--install',*(str(p)for p in paths)],timeout=120)
 require(metadata(['/usr/bin/dpkg','--audit'])==b'','dpkg_audit')

def inventory():
 raw=metadata(['/usr/bin/dpkg-query','-W','-f=${binary:Package}=${Version}\n']);rows=[line.split('=',1)for line in raw.decode().splitlines()];v=dict(rows);require(len(v)==len(rows),'package_inventory');return v

def regular_payload(deb,package,version,label):
 expected=WORK/('expected-'+label);expected.mkdir(mode=0o755);command(['/usr/bin/dpkg-deb','-x',str(deb),str(expected)],timeout=60)
 rows=[];links=[];count=0
 for directory,dirs,files in os.walk(expected,followlinks=False):
  require(len(rows)+len(links)+len(dirs)<=16384,'payload_count')
  for name in [*dirs,*files]:
   count+=1;require(count<=16384,'payload_count');path=Path(directory)/name;relative=str(path.relative_to(expected));actual=Path('/'+relative);s=path.lstat()
   if stat.S_ISLNK(s.st_mode):require(actual.is_symlink()and os.readlink(actual)==os.readlink(path),'payload_link');links.append({'path':str(actual),'target':os.readlink(path)})
   elif stat.S_ISREG(s.st_mode):
    ref=digest(path);canonical=actual.resolve(strict=True);got=digest(canonical);st=canonical.lstat()
    require(got==ref and stat.S_IMODE(st.st_mode)==stat.S_IMODE(s.st_mode)and st.st_uid==s.st_uid and st.st_gid==s.st_gid,'payload_changed')
    rows.append({'path':str(actual),'canonical_path':str(canonical),'bytes':ref['bytes'],'sha256':ref['sha256'],'mode':st.st_mode,'uid':st.st_uid,'gid':st.st_gid})
   else:require(stat.S_ISDIR(s.st_mode),'payload_type')
 require(rows and metadata(['/usr/bin/dpkg','--verify',package])==b'','payload_verify')
 return {'package':package,'installed_version':version,'deb_sha256':digest(deb)['sha256'],'regular_files':rows,'symlinks':links,'regular_and_link_inventory_complete':True}

def acl_library_binding(expected_dir):
 expected=(expected_dir/'usr/lib/x86_64-linux-gnu/libacl.so.1').resolve(strict=True)
 actual=Path('/usr/lib/x86_64-linux-gnu/libacl.so.1').resolve(strict=True)
 require(expected.is_relative_to(expected_dir)and actual.parent==Path('/usr/lib/x86_64-linux-gnu'),'libacl_path')
 require(digest(actual)==digest(expected),'approved_runtime_library_changed');return {'path':str(actual),**digest(actual)}

def exported_symbols(path):
 raw=metadata(['/usr/bin/readelf','--wide','--dyn-syms',str(path)]).decode()
 return sorted({line.split()[-1].split('@')[0]for line in raw.splitlines()if ' UND 'not in line and re.search(r'\bacl_[A-Za-z0-9_]+(?:@@?[^\s]+)?\s*$',line)})

def caller_binary_contract(path,library):
 dynamic=metadata(['/usr/bin/readelf','--wide','--dynamic',str(path)]).decode();symbols=metadata(['/usr/bin/readelf','--wide','--dyn-syms',str(path)]).decode()
 undefined=set(re.findall(r'\bUND\s+(acl_[A-Za-z0-9_]+)(?:@[^\s]+)?(?:\s+\([0-9]+\))?\s*$',symbols,re.M))
 required={'acl_get_file_at','acl_set_file_at','acl_delete_def_file_at'}
 require('Shared library: [libacl.so.1]'in dynamic and required<=undefined,'coreutils_private_at_imports')
 # GNU regular-FD compatibility code remains intact and may retain legitimate
 # pathname symbols. Imports alone neither close nor prove a source callsite.
 versions=metadata(['/usr/bin/readelf','--wide','--version-info',str(path)]).decode();glibc=[tuple(map(int,v.split('.')))for v in re.findall(r'GLIBC_([0-9]+\.[0-9]+)',versions)]
 require(glibc and max(glibc)<=(2,41),'glibc_abi')
 bindings={}
 libsymbols=metadata(['/usr/bin/readelf','--wide','--dyn-syms',library['path']]).decode()
 for name in required:
  expected=re.findall(r'\b'+name+r'@@([^\s]+)',libsymbols);actual=re.findall(r'\bUND\s+'+name+r'@([^\s]+)',symbols)
  require(len(expected)==len(actual)==1 and actual==expected,'versioned_acl_abi');bindings[name]=actual[0]
 loaded=metadata(['/usr/bin/ldd',str(path)]).decode();match=re.findall(r'^\s*libacl\.so\.1 => (/[^\s]+)',loaded,re.M)
 require(len(match)==1 and Path(match[0]).resolve(strict=True)==Path(library['path'])and digest(Path(library['path']))=={'bytes':library['bytes'],'sha256':library['sha256']},'loaded_approved_libacl')
 return {'path':str(path),'candidate_binary':digest(path),'libacl':library,'required_private_at_imports':sorted(required),'actual_versioned_acl_bindings':bindings,
  'retained_legacy_import_names_not_callsite_proof':sorted(LEGACY&undefined),'actual_ldd_acl_target':str(Path(match[0]).resolve()),
  'maximum_glibc_requirement':'.'.join(map(str,max(glibc))),'dynamic_approved_libacl_not_static_sdk':True,'security_accepted':False}

def original_source_bindings(source):
 packet=json.loads(bytes_at(RECIPE/'local-source-packet.json',(13474,'8300cbac75601f87591263811f95514e1faa8d99c25113189b8e4b91b13f8e0e')));rows=[]
 require(type(packet['files'])is list and len(packet['files'])==76,'local_packet_76')
 prefix='upstream/coreutils-9.7-3/'
 for row in packet['files']:
  if row['path'].startswith(prefix):
   name=row['path'][len(prefix):];pin=observed(source/name);require(pin=={'bytes':row['bytes'],'sha256':row['sha256']},'full_source_original_pin');rows.append({'path':name,**pin})
 require(len(rows)>=20,'original_source_contract');return rows

def attr_lineage(manifest):
 path=WORK/'public-inputs/attr-2.6.0.tar.xz';raw=bytes_at(path,SOURCE_FILES[path.name],maximum=1024**2);expected={r['original']:r for r in manifest['attr26_lineage']};found={};total=0;count=0
 with tarfile.open(fileobj=io.BytesIO(raw),mode='r:xz')as archive:
  while True:
   member=archive.next()
   if member is None:break
   count+=1;require(count<=4096 and member.size<=16*1024**2,'attr_archive_bound');total+=member.size;require(total<=32*1024**2,'attr_archive_bound')
   if not member.name.startswith('attr-2.6.0/'):continue
   name=member.name[len('attr-2.6.0/'):]
   if name not in expected:continue
   pin=expected[name];require(name not in found and member.isfile()and not member.issparse()and member.size==pin['upstream_bytes']and member.size<=65536,'attr_member')
   with archive.extractfile(member)as stream:data=stream.read(member.size+1)
   require(len(data)==pin['upstream_bytes']and sha256(data).hexdigest()==pin['upstream_sha256'],'attr_member_pin');found[name]={'bytes':len(data),'sha256':sha256(data).hexdigest()}
 require(set(found)==set(expected)and len(found)==13,'attr_lineage_13');return found

def test_source_inventory(source):
 result={};total=0
 for scope in ('tests','gnulib-tests'):
  for directory,dirs,files in os.walk(source/scope,followlinks=False):
   dirs.sort();files.sort()
   for name in files:
    path=Path(directory)/name;relative=str(path.relative_to(source));require(len(result)<16384,'vendor_source_count')
    if name=='Makefile.in':continue # autoreconf-generated, separately recorded
    if path.is_symlink():result[relative]={'symlink':os.readlink(path)};continue
    frame=identity(path.lstat());require(stat.S_ISREG(frame[2])and frame[6]<=4*1024**2,'vendor_source_file');total+=frame[6];require(total<=64*1024**2,'vendor_source_total')
    if frame[6]==0:result[relative]={'bytes':0,'sha256':sha256(b'').hexdigest()}
    else:result[relative]=observed(path,maximum=4*1024**2)
 return result

def verify_test_sources(source,inventory):
 for name,pin in inventory.items():
  path=source/name
  if 'symlink'in pin:require(path.is_symlink()and os.readlink(path)==pin['symlink'],'vendor_test_source_changed')
  elif pin['bytes']==0:require(path.is_file()and not path.is_symlink()and path.stat().st_size==0,'vendor_test_source_changed')
  else:require(observed(path)==pin,'vendor_test_source_changed')

def generated_proof(source,configure_before):
 names=('configure','Makefile.in','Makefile','config.h','config.log','config.status','gnulib-tests/Makefile.in','gnulib-tests/Makefile')
 rows={name:observed(source/name)for name in names}
 require(rows['configure']!=configure_before and b'GOLD_DESCRIPTOR_ACL'in owned_read(source/'configure',maximum=16*1024**2)
  and b'#define GOLD_DESCRIPTOR_ACL 1'in owned_read(source/'config.h',maximum=1024**2),'real_autotools_regeneration')
 return {'schema':'coreutils_actual_generated_build_provenance/1','original_configure':configure_before,'actual_generated_files':rows,
  'configured_descriptor_acl_feature':True,'debian_dh_autoreconf_noop_removed':True,'security_accepted':False}

def build():
 context=builder_context();require(not WORK.exists()and not OUT.exists(),'fresh_build_required')
 prep=bytes_at(Path('/build/preparation.json'),maximum=MAX_METADATA);preparation=json.loads(prep)
 require(preparation['schema']=='coreutils_acl_authenticated_preparation/1'and preparation['forky_source_only']is True
  and preparation['maintainer_dsc_signatures_authenticated']is True,'preparation_required')
 require(sorted(os.listdir('/sys/class/net'))==['lo'],'offline_build_required')
 WORK.mkdir(mode=0o755);public=WORK/'public-inputs';public.mkdir(mode=0o755)
 for name,pin in SOURCE_FILES.items():copy_exact(Path('/build/source-inputs')/name,public/name,pin,mode=0o644)
 for name in RECIPE_FILES:
  if name=='Dockerfile':continue
  pin=digest(RECIPE/name);copy_exact(RECIPE/name,public/name,(pin['bytes'],pin['sha256']),mode=0o644)
 guard=public_guards();sources={name:digest(public/name,pin)for name,pin in SOURCE_FILES.items()}
 manifest=json.loads(bytes_at(public/'source-manifest.json',SOURCE_MANIFEST_PIN));lineage=attr_lineage(manifest)
 approval=json.loads(bytes_at(public/'root-source-approval.json',(1963,'39f1ddaffbe9f22ffad55edbe07556a62238e606b7a1f05685a6c712592444fa')));require(approval['source_only_approval_for_isolated_vendor_ci']is True and approval['security_accepted']is False,'isolated_source_approval')
 _,sdk_rows,sdk_records,sdk_packaging=build_vendor('sdk');guard()
 sdk={r['name']:r for r in sdk_rows};install_packages([Path(sdk[n]['path'])for n in ('libacl1','libacl1-dev')],documentation=('libacl1','libacl1-dev'))
 fresh_sdk_library=digest(Path('/usr/lib/x86_64-linux-gnu/libacl.so.1').resolve());sdk_headers={n:digest(Path(n))for n in ('/usr/include/sys/acl.h','/usr/include/acl/libacl.h')}
 old_lib=INPUTS/'libacl1_2.4.0-1~trixie+gold1_amd64.deb';install_packages([old_lib],documentation=('libacl1',))
 require(metadata(['/usr/bin/dpkg-query','-W','-f=${Version}','libacl1']).decode()==SDK_VERSION
  and metadata(['/usr/bin/dpkg-query','-W','-f=${Version}','libacl1-dev']).decode()==SDK_VERSION,'sdk_runtime_version')
 lib_payload=regular_payload(old_lib,'libacl1',SDK_VERSION,'approved-libacl');library=acl_library_binding(WORK/'expected-approved-libacl')
 approved_exports=exported_symbols(Path(library['path']));require(NEW_APIS<=set(approved_exports),'sdk_approved_api');before=inventory()
 source,rows,records,packaging=build_vendor('coreutils');candidate=next(r for r in rows if r['name']=='coreutils');guard()
 install_packages([Path(candidate['path'])],documentation=('coreutils',),complete_package_payload=True)
 after=inventory();expected=dict(before);expected['coreutils']=VERSION;require(after==expected,'only_coreutils_runtime_delta')
 payload=regular_payload(Path(candidate['path']),'coreutils',VERSION,'candidate-coreutils')
 require(acl_library_binding(WORK/'expected-approved-libacl')==library and exported_symbols(Path(library['path']))==approved_exports,'libacl_abi_changed')
 abi={n:caller_binary_contract(Path('/usr/bin')/n,library)for n in ('cp','mv','install')}
 # Compile actual frozen private helper fixture using actual generated GNU flags,
 # and a separate companion invoking the real GNU qcopy_acl regular-FD0 fix.
 native={}
 for name,fragment,variable,fixture,target in [('private_fd','owned-fixture.mk','GOLD_OWNED_FIXTURE','ordinary_fd_acl.c','gold-owned-fd-acl-fixture'),('gnu_fd0','gnulib-fd0-fixture.mk','GOLD_GNULIB_FD0_FIXTURE','ordinary_gnulib_fd0.c','gold-owned-gnulib-fd0-fixture')]:
  command(['/usr/bin/make','--no-print-directory','-f','Makefile','-f',str(public/fragment),variable+'='+str(public/fixture),target],cwd=source,user=10001,timeout=120)
  flag='--run-owned-fd-acl'if name=='private_fd'else'--run-owned-gnulib-fd0'
  code,raw=run([str(source/target),flag],cwd=Path('/work'),user=10001,with_code=True,timeout=60,maximum=65536);uid_idle();require(code==0,'required_owned_c_fixture_failed')
  if name=='private_fd':
   match=re.fullmatch(rb'owned_fd_acl_fixture_passed;NFSv4_real_filesystem_NOT_EXERCISED;security_accepted=false\nactual_assertions=([0-9]+)\n',raw);require(match is not None and int(match[1])>=20,'private_fixture_receipt')
   native[name]={'actual_assertions':int(match[1]),'actual_uid':10001,'actual_gid':10001,'actual_returncode':0,'skip_count':0,'NFSv4_real_filesystem_exercised':False,'raw_stdout_sha256':sha256(raw).hexdigest()}
  else:
   value=json.loads(raw);require(value['schema']=='ordinary_gnulib_regular_fd0/1'and value['status']=='PASS'and value['actual_uid']==value['actual_gid']==10001
    and value['actual_gnulib_qcopy_acl_called']is True and value['source_fd_zero']is True,'gnu_fd0_receipt');native[name]=value
  guard();native[name]['actual_fixture_binary']=observed(source/target)
 code,raw=run(['/usr/local/bin/python3','-I','-S','-B',str(public/'verify_ordinary.py'),'--run-owned-compatibility'],cwd=Path('/work'),user=10001,with_code=True,timeout=240,maximum=MAX_METADATA);uid_idle();require(code==0,'ordinary_coreutils_compatibility');ordinary=json.loads(raw)
 require(ordinary['schema']=='coreutils_owned_cli_compatibility/1'and ordinary['status']=='PASS'and ordinary['actual_uid']==ordinary['actual_gid']==10001
  and ordinary['skip_count']==0 and ordinary['security_accepted']is False,'ordinary_cli_receipt');guard()
 require(all(ordinary['actual_installed_candidate_binaries'][n]['sha256']==abi[n]['candidate_binary']['sha256']for n in abi),'ordinary_candidate_binary_binding')
 suite_raw=owned_read(WORK/'coreutils/vendor-suite.json');suite=json.loads(suite_raw);require(suite['actual_returncode']==0,'vendor_tests_required');os.chown(WORK/'coreutils/vendor-suite.json',0,0)
 sourceproof=json.loads(bytes_at(WORK/'coreutils/source-contract.json',maximum=MAX_METADATA));verify_test_sources(source,sourceproof['unchanged_original_vendor_tests']);source_contract(source,manifest,'after');generated=json.loads(bytes_at(WORK/'coreutils/generated-build-proof.json',maximum=MAX_METADATA))
 require(sorted(os.listdir('/sys/class/net'))==['lo'],'offline_final');guard();OUT.mkdir(mode=0o755)
 for name,path in [('coreutils_'+VERSION+'_amd64.deb',Path(candidate['path'])),*[(('acl_'+SDK_VERSION if kind=='acl'else'coreutils_'+VERSION)+'_amd64.'+suffix,path)for kind,rs in [('acl',sdk_records),('coreutils',records)]for suffix,path in rs.items()]]:
  pin=digest(path,maximum=64*1024**2);copy_exact(path,OUT/name,(pin['bytes'],pin['sha256']),mode=0o644)
 sdk_proof={'schema':'coreutils_acl_compiler_sdk/1','fresh_sdk_family':sdk_rows,'compiler_only':True,'fresh_sdk_library_before_reinstall':fresh_sdk_library,'fresh_sdk_headers':sdk_headers,
  'actual_approved_runtime_after_reinstall':library,'runtime_deb':{'bytes':OLD_PACKAGES[old_lib.name][0],'sha256':OLD_PACKAGES[old_lib.name][1]},'runtime_replacement_exported':False,'old_exports_preserved':approved_exports,'security_accepted':False}
 reports={'source-proof.json':{'schema':'coreutils_acl_source_build/1','sources':sources,'attr13_original_lineage':lineage,'patch':{'bytes':PATCH_PIN[0],'sha256':PATCH_PIN[1]},'source_manifest':manifest,
  'actual_source_contract':sourceproof,'sdk_packaging':sdk_packaging,'coreutils_packaging':packaging,'maintainer_dsc_signatures_authenticated':True,'security_accepted':False},
 'sdk-proof.json':sdk_proof,'abi-contract.json':{'schema':'coreutils_dynamic_approved_acl_abi/1','callers':abi,'security_accepted':False},
 'ordinary-compatibility.json':{'schema':'coreutils_acl_ordinary_compatibility/1','required_owned_c_fixtures':native,'actual_cli':ordinary,'all_required_owned_cases_skip_count':0,'security_accepted':False},
 'runtime-payload.json':{'schema':'coreutils_acl_runtime_payload/1','approved_libacl':lib_payload,'coreutils':payload,'security_accepted':False},
 'caller-proof.json':{'schema':'coreutils_descriptor_caller_build/1','source_patch_sha256':PATCH_PIN[1],'compiled_caller_contract':abi,'required_owned_fixtures':native,
  'ordinary_coreutils_compatibility_passed':True,'whole_image_other_callers_assessed':False,'all_cves_attested':False,'security_accepted':False},
 'vendor-suite.json':suite,'generated-build-proof.json':generated,
 'provenance.json':{'schema':'coreutils_acl_package_provenance/1','context':context,'preparation_sha256':sha256(prep).hexdigest(),'preparation':preparation,
  'coreutils_packages':rows,'source_date_epoch':SOURCE_EPOCH,'normal_package_version':VERSION,'build_metadata_unsigned':True,'full_unfiltered_gnu_test_hook_actual_returncode':0,
  'vendor_test_selection':'configured_default_unchanged','vendor_zero_skip_coverage_claimed':False,'runtime_loader_override_used':False,
  'vendor_build_only_loader_mechanics_not_disabled':True,'uid10001_children_gone':True,'only_new_coreutils_runtime_exported':True,'production_installed':False,'security_accepted':False}}
 for name,value in reports.items():publish(OUT/name,encoded(value));(OUT/name).chmod(0o644)
 require({p.name for p in OUT.iterdir()}==set(OUTPUTS),'output_inventory')
 for name,maximum in OUTPUTS.items():digest(OUT/name,maximum=maximum)
 guard();print(json.dumps({'schema':'coreutils_acl_normal_package_build/1','status':'BUILT_NOT_ACCEPTED','outputs':sorted(OUTPUTS),'security_accepted':False},sort_keys=True))

def main(argv=None):
 p=argparse.ArgumentParser();p.add_argument('--configure-build-repository',action='store_true');p.add_argument('--build',action='store_true');p.add_argument('--vendor-tests',action='store_true');args=p.parse_args(argv)
 if not any((args.configure_build_repository,args.build,args.vendor_tests)):print(json.dumps({'schema':'coreutils_acl_normal_package_build/1','status':'UNARMED','build_performed':False,'security_accepted':False}));return 0
 require(sum((args.configure_build_repository,args.build,args.vendor_tests))==1,'one_phase_only');signal.signal(signal.SIGALRM,lambda *_:(_ for _ in ()).throw(RuntimeError('build_deadline')));signal.alarm(13000 if args.build else 9000 if args.vendor_tests else 2400)
 try:
  if args.configure_build_repository:configure_repository()
  elif args.vendor_tests:vendor_tests()
  else:build()
  return 0
 finally:signal.alarm(0)
if __name__=='__main__':raise SystemExit(main())
