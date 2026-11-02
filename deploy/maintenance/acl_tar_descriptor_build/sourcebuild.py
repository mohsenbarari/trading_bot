"""Source-only normal SDK and Tar caller package recipe; default is unarmed.
Root/CI alone invokes this in the exact disposable Trixie builder. No imports
perform I/O. Runtime libacl is reinstalled from its unchanged approved DEB.
"""
from hashlib import sha256
from pathlib import Path
import argparse,json,os,re,select,selectors,signal,stat,subprocess,sys,time

RECIPE=Path('/recipe');INPUTS=Path('/inputs');WORK=Path('/build/tar-acl');OUT=Path('/out')
SDK_VERSION='2.4.0-1~trixie+gold1';VERSION='1.35+dfsg-5~trixie+gold2'
SOURCE_EPOCH=1792972800
SOURCE_FILES={
 'acl_2.4.0-1.dsc':(2624,'b987ebbeb3d498794bad29cd65301b77384978493d8765c94dba8bad8fa325b6'),
 'acl_2.4.0.orig.tar.xz':(384828,'e661131456d2708a01c614a0f400e11d7d1bfaeb6f3e74b75bb980b72f0161a3'),
 'acl_2.4.0.orig.tar.xz.asc':(833,'5f4f0b9b78821764fe6b88e32aef6ec519628522c211c36706bcb78d6f3f036b'),
 'acl_2.4.0-1.debian.tar.xz':(47932,'65931c2fb3e821bda67f8d8d72d77e99ac61502748dcdf38b6805fe89339085e'),
 'tar_1.35+dfsg-5.dsc':(2428,'e026e7a19c295212fcc3250e3136b134ae09661c559bf707b8ebfdd8f5093c0a'),
 'tar_1.35+dfsg.orig.tar.xz':(2111608,'9ae57e981c1e73c0eebc2b26c9b0c4497fe310ef1d516ea430efb5470b71f7a8'),
 'tar_1.35+dfsg-5.debian.tar.xz':(22704,'51fecfaa10cbadb1ab19be88adfd34a6d876619c50aaaafd69011ddb2e283b70')}
OLD_PACKAGES={
 'libacl1_2.4.0-1~trixie+gold1_amd64.deb':(37076,'bbb12e190bfe19b876f73a669848ebd8d04293d95000babf51058c88879966b1'),
 'tar_1.35+dfsg-5~trixie+gold1_amd64.deb':(814984,'b6a5715adc801680c94ab653641668d03f429dddc1efa822015b6395482195c4')}
OLD_TAR_SHA='1d9d884a06465ad5c0551858ceaaaa4dc60eda57ad5f12b710ab52d63e817892'
PATCH='0001-tar-descriptor-acl-caller.patch';PATCH_PIN=(26083,'8fbdc8332e1a9dc131bb18423f1bc57ced307ae2d2589ec23a8afa5a639637ee')
CHANGELOG='debian-local-changelog.patch';CHANGELOG_PIN=(617,'1a3e1273bfc436fe8dab20d7c09a704ac187da3f1550e0251ea025963d6ddff0')
SOURCE_MANIFEST_PIN=(1866,'3bb405d134275541b4ecdecdce9fb597a9ed1d88adcfdca43574532be3bad77d')
FIXTURE_PIN=(4514,'a0c7e9a44cd960058b2c906578cd2d224bf8b52d4d4ba6c64d4623aa825f2042')
CA_PIN=(222073,'e9c7b63ee2623386e9b0a3ee32f499d01a8ea23dc83b8383ddff9f8e749543b7')
RECIPE_FILES={'sourcebuild.py','Dockerfile','verify_ordinary.py',PATCH,CHANGELOG,'source-manifest.json','ordinary_acl_fd_fixture.c'}
ENV={'PATH':'/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin','HOME':'/work/home','LC_ALL':'C.UTF-8','LANG':'C.UTF-8',
 'DEBIAN_FRONTEND':'noninteractive','SOURCE_DATE_EPOCH':str(SOURCE_EPOCH),
 'DEB_BUILD_OPTIONS':'parallel=2','DEB_BUILD_PROFILES':'noudeb','QUILT_PATCHES':'debian/patches','QUILT_PATCH_OPTS':'--fuzz=0'}
MAX_METADATA=4*1024**2
SDK_FAMILY={'acl','libacl1','libacl1-dev'}
LEGACY={'acl_get_file','acl_set_file','acl_extended_file','acl_delete_def_file'}
NEW_APIS={'acl_get_file_at','acl_set_file_at','acl_extended_file_at','acl_delete_def_file_at'}
OUTPUTS={'tar_'+VERSION+'_amd64.deb':32*1024**2,'tar_'+VERSION+'_amd64.changes':4*1024**2,
 'tar_'+VERSION+'_amd64.buildinfo':4*1024**2,'acl_'+SDK_VERSION+'_amd64.changes':4*1024**2,
 'acl_'+SDK_VERSION+'_amd64.buildinfo':4*1024**2,**{name:4*1024**2 for name in
 ('source-proof.json','sdk-proof.json','abi-contract.json','ordinary-compatibility.json','provenance.json','runtime-payload.json','caller-proof.json')}}


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
 require(type(argv)is list and argv and all(type(v)is str for v in argv)and user in (None,1001),'command_scope')
 child=subprocess.Popen(argv,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
  env=current_environment(),cwd=cwd,close_fds=True,start_new_session=True,umask=0o022,
  **({'user':1001,'group':1001,'extra_groups':[]}if user==1001 else {}))
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
  if with_code:
   require(capture is True,'capture_contract');return code,b''.join(parts)
  require(code==0,'command_failed')
  return b''.join(parts)if capture else None
 except BaseException:
  cleanup_owned(child);raise
 finally:child.stdout.close()
def command(argv,*,cwd=None,timeout=600,user=None):return run(argv,cwd=cwd,timeout=timeout,maximum=64*1024**2,capture=False,user=user)
def metadata(argv,*,cwd=None,user=None):return run(argv,cwd=cwd,timeout=30,maximum=MAX_METADATA,user=user)
def owned_read(path,*,maximum=4*1024**2):
 path=Path(path);require(path.is_relative_to(WORK)and path.resolve(strict=True)==path,'owned_path')
 before=identity(path.lstat());require(stat.S_ISREG(before[2])and before[3]in (0,1001)and before[4]in (0,1001)
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
    alive+=int(b'1001'in values)
  if not alive:return
  require(time.monotonic()<end,'sdk_children_remain');time.sleep(.1)

def builder_context():
 require(os.geteuid()==os.getegid()==0 and Path.cwd()==Path('/build')and Path('/.dockerenv').is_file(),'disposable_root_builder')
 require(sys.version_info[:3]==(3,11,17)and sys.flags.isolated==sys.flags.no_site==1 and os.uname().machine=='x86_64','builder_python')
 fields=dict(line.split('=',1)for line in bytes_at(Path('/etc/os-release').resolve(),maximum=65536).decode().splitlines()if'='in line)
 require(fields.get('ID','').strip('"')=='debian'and fields.get('VERSION_CODENAME','').strip('"')=='trixie','trixie_only')
 current_environment();ctx=json.loads(bytes_at(RECIPE/'context-manifest.json'))
 expected=RECIPE_FILES|{'inputs/'+n for n in OLD_PACKAGES}
 require(type(ctx)is dict and set(ctx)=={'schema','files'}and ctx['schema']=='tar_acl_allowlisted_context/1'
  and type(ctx['files'])is dict and set(ctx['files'])==expected,'context_inventory')
 for name,pin in ctx['files'].items():
  require(type(pin)is dict and set(pin)=={'bytes','sha256'}and type(pin['bytes'])is int and 0<pin['bytes']<=32*1024**2
   and type(pin['sha256'])is str and re.fullmatch('[a-f0-9]{64}',pin['sha256']),'context_binding')
  digest(RECIPE/name,(pin['bytes'],pin['sha256']))
 for name,pin in OLD_PACKAGES.items():digest(INPUTS/name,pin)
 for name,pin in ((PATCH,PATCH_PIN),(CHANGELOG,CHANGELOG_PIN),('source-manifest.json',SOURCE_MANIFEST_PIN),('ordinary_acl_fd_fixture.c',FIXTURE_PIN)):digest(RECIPE/name,pin)
 return ctx

def configure_repository():
 builder_context();require(not WORK.exists(),'fresh_build_required');digest(Path('/etc/ssl/certs/ca-certificates.crt'),CA_PIN)
 Path('/work').mkdir(mode=0o755,exist_ok=True);os.chown('/work',1001,1001);Path('/work/home').mkdir(mode=0o700,exist_ok=True);os.chown('/work/home',1001,1001)
 # Frozen repositories authenticate normally; only snapshot expiry is relaxed.
 for path in Path('/etc/apt/sources.list.d').glob('*'):
  require(path.is_file()and not path.is_symlink(),'repository_file');path.unlink()
 Path('/etc/apt/sources.list').write_text('')
 stanzas='Types: deb deb-src\nURIs: https://snapshot.debian.org/archive/debian/20261024T000000Z/\nSuites: trixie trixie-updates\nComponents: main\nSigned-By: /usr/share/keyrings/debian-archive-keyring.gpg\nCheck-Valid-Until: no\n\nTypes: deb deb-src\nURIs: https://snapshot.debian.org/archive/debian-security/20261024T000000Z/\nSuites: trixie-security\nComponents: main\nSigned-By: /usr/share/keyrings/debian-archive-keyring.gpg\nCheck-Valid-Until: no\n\nTypes: deb-src\nURIs: https://snapshot.debian.org/archive/debian/20260820T000000Z/\nSuites: forky\nComponents: main\nSigned-By: /usr/share/keyrings/debian-archive-keyring.gpg\nCheck-Valid-Until: no\n'
 Path('/etc/apt/sources.list.d/pinned.sources').write_text(stanzas)
 policy=Path('/usr/sbin/policy-rc.d');policy.write_text('#!/bin/sh\nexit 101\n');policy.chmod(0o755)
 apt=['/usr/bin/apt-get','-o','Acquire::Retries=0','-o','Acquire::https::CaInfo=/etc/ssl/certs/ca-certificates.crt']
 command([*apt,'-o','APT::Update::Error-Mode=any','update'],timeout=600)
 command([*apt,'-y','--no-install-recommends','build-dep','--build-profiles=noudeb','--only-source','acl=2.4.0-1','tar=1.35+dfsg-5'],timeout=900)
 command([*apt,'-y','--no-install-recommends','install','build-essential','dpkg-dev','binutils','ca-certificates','pkgconf','patch','debian-keyring','util-linux','passwd'],timeout=600)
 command(['/usr/sbin/groupadd','--gid','1001','candidate-builder'],timeout=30)
 command(['/usr/sbin/useradd','--uid','1001','--gid','1001','--home-dir','/work/home','--no-create-home','--shell','/usr/sbin/nologin','candidate-builder'],timeout=30)
 downloads=Path('/build/source-inputs');downloads.mkdir(mode=0o755)
 command([*apt,'source','--download-only','--only-source','acl=2.4.0-1','tar=1.35+dfsg-5'],cwd=downloads,timeout=600)
 require({p.name for p in downloads.iterdir()}==set(SOURCE_FILES),'source_input_inventory')
 for name,pin in SOURCE_FILES.items():digest(downloads/name,pin)
 # Record genuine signed-APT index bytes; no independently checked DSC signature claim.
 release=[]
 for path in sorted(Path('/var/lib/apt/lists').glob('*InRelease')):
  require(len(release)<8,'release_inventory');release.append({'name':path.name,**digest(path,maximum=1024**2)})
 require(3<=len(release)<=4,'release_inventory')
 publish(Path('/build/preparation.json'),encoded({'schema':'tar_acl_authenticated_preparation/1','releases':release,
  'source_inputs':{n:{'bytes':p[0],'sha256':p[1]}for n,p in SOURCE_FILES.items()},'binary_repository_family':'trixie_only',
  'forky_source_only':True,'maintainer_dsc_signatures_authenticated':False,'security_accepted':False}))

def package_fields(path):
 raw=metadata(['/usr/bin/dpkg-deb','-f',str(path),'Package','Version','Architecture']).decode();rows=[line.split(': ',1)for line in raw.splitlines()]
 require(all(len(v)==2 for v in rows),'package_fields');value=dict(rows);require(set(value)=={'Package','Version','Architecture'},'package_fields');return value

def family_outputs(directory,family,version):
 rows=[];seen=set();paths=list(directory.glob('*.deb'));require(0<len(paths)<=12,'package_count')
 for path in sorted(paths):
  fields=package_fields(path);name=fields['Package'];require(name not in seen and name.removesuffix('-dbgsym')in family
   and fields['Version']==version and fields['Architecture']in {'amd64','all'},'package_identity');seen.add(name)
  os.chown(path,0,0);path.chmod(0o644);rows.append({'name':name,'path':str(path),'version':version,**digest(path)})
 require({n for n in seen if not n.endswith('-dbgsym')}==family,'package_family');return rows

def build_vendor(kind):
 directory=WORK/kind;directory.mkdir(mode=0o755);os.chown(directory,1001,1001)
 dsc='acl_2.4.0-1.dsc'if kind=='sdk'else'tar_1.35+dfsg-5.dsc';source=directory/'source'
 command(['/usr/bin/dpkg-source','-x',str(WORK/'public-inputs'/dsc),str(source)],cwd=directory,timeout=120,user=1001)
 uid_idle();version=SDK_VERSION if kind=='sdk'else VERSION
 if kind=='sdk':
  changelog=source/'debian/changelog';old=owned_read(changelog);require(old.startswith(b'acl (2.4.0-1)'),'sdk_source_version')
  prefix=('acl ('+SDK_VERSION+') trixie; urgency=medium\n\n  * Fresh compiler-only SDK build of unchanged official ACL2.4 sources.\n  * Runtime replacement authority is excluded.\n\n -- Project local build <local-build@invalid>  Mon, 26 Oct 2026 00:00:00 +0000\n\n').encode();changelog.write_bytes(prefix+old)
 else:
  manifest=json.loads(bytes_at(RECIPE/'source-manifest.json',SOURCE_MANIFEST_PIN));require(manifest['patch']=={'bytes':PATCH_PIN[0],'sha256':PATCH_PIN[1]},'patch_source_manifest')
  for name,pin in manifest['files'].items():
   raw=owned_read(source/name);require({'bytes':len(raw),'sha256':sha256(raw).hexdigest()}==pin['before'],'source_before_patch')
  command(['/usr/bin/patch','-p1','--batch','--fuzz=0','--no-backup-if-mismatch','-i',str(WORK/'public-inputs'/PATCH)],cwd=source,timeout=30,user=1001)
  command(['/usr/bin/patch','-p1','--batch','--fuzz=0','--no-backup-if-mismatch','-i',str(WORK/'public-inputs'/CHANGELOG)],cwd=source,timeout=30,user=1001)
  for name,pin in manifest['files'].items():
   raw=owned_read(source/name);require({'bytes':len(raw),'sha256':sha256(raw).hexdigest()}==pin['after'],'source_after_patch')
 packaging={n:sha256(owned_read(source/'debian'/n)).hexdigest()for n in ('rules','control')}
 command(['/usr/bin/dpkg-buildpackage','-us','-uc','-b','-Pnoudeb'],cwd=source,timeout=1200 if kind=='sdk'else 3000,user=1001)
 uid_idle();require(all(sha256(owned_read(source/'debian'/n)).hexdigest()==h for n,h in packaging.items()),'vendor_packaging_changed')
 os.chown(directory,0,0)
 rows=family_outputs(directory,SDK_FAMILY if kind=='sdk'else{'tar','tar-scripts'},version)
 records={}
 for suffix in ('changes','buildinfo'):
  paths=list(directory.glob('*.'+suffix));require(len(paths)==1,'build_record_inventory');path=paths[0];os.chown(path,0,0);path.chmod(0o644);records[suffix]=path
 if kind=='tar':
  for name,pin in manifest['files'].items():
   raw=owned_read(source/name);require({'bytes':len(raw),'sha256':sha256(raw).hexdigest()}==pin['after'],'built_source_changed')
  config=owned_read(source/'config.h',maximum=1024**2).decode();require(all('#define HAVE_'+n.upper()+' 1'in config for n in NEW_APIS),'compiled_api_configuration')
 return source,rows,records,packaging

def install_packages(paths,*,documentation,complete_tar_payload=False):
 flags=[]
 for name in documentation:
  flags+=['--path-include=/usr/share/doc/'+name,'--path-include=/usr/share/doc/'+name+'/*']
 if complete_tar_payload:
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

def tar_binary_contract(path,library):
 dynamic=metadata(['/usr/bin/readelf','--wide','--dynamic',str(path)]).decode();symbols=metadata(['/usr/bin/readelf','--wide','--dyn-syms',str(path)]).decode()
 undefined=set(re.findall(r'\bUND\s+(acl_[A-Za-z0-9_]+)(?:@[^\s]+)?(?:\s+\([0-9]+\))?\s*$',symbols,re.M))
 require('Shared library: [libacl.so.1]'in dynamic and NEW_APIS<=undefined and not LEGACY&undefined,'tar_caller_imports')
 versions=metadata(['/usr/bin/readelf','--wide','--version-info',str(path)]).decode();glibc=[tuple(map(int,v.split('.')))for v in re.findall(r'GLIBC_([0-9]+\.[0-9]+)',versions)]
 require(glibc and max(glibc)<=(2,41),'glibc_abi')
 return {'candidate_binary':digest(path),'libacl':library,'new_acl_imports':sorted(NEW_APIS),'legacy_pathname_imports':[],
  'maximum_glibc_requirement':'.'.join(map(str,max(glibc))),'static_source_flags_review_still_required':True,'security_accepted':False}

def build():
 context=builder_context();require(not WORK.exists()and not OUT.exists(),'fresh_build_required')
 prep=bytes_at(Path('/build/preparation.json'),maximum=MAX_METADATA);preparation=json.loads(prep)
 require(preparation['schema']=='tar_acl_authenticated_preparation/1'and preparation['forky_source_only']is True,'preparation_required')
 require(sorted(os.listdir('/sys/class/net'))==['lo'],'offline_build_required')
 WORK.mkdir(mode=0o755);public=WORK/'public-inputs';public.mkdir(mode=0o755)
 for name,pin in SOURCE_FILES.items():digest(Path('/build/source-inputs')/name,pin);copy_exact(Path('/build/source-inputs')/name,public/name,pin,mode=0o644)
 for name in (PATCH,CHANGELOG,'ordinary_acl_fd_fixture.c','verify_ordinary.py'):
  pin=digest(RECIPE/name);copy_exact(RECIPE/name,public/name,(pin['bytes'],pin['sha256']),mode=0o644)
 sources={name:digest(public/name,pin)for name,pin in SOURCE_FILES.items()}
 _,sdk_rows,sdk_records,sdk_packaging=build_vendor('sdk')
 sdk={r['name']:r for r in sdk_rows};install_packages([Path(sdk[n]['path'])for n in ('libacl1','libacl1-dev')],documentation=('libacl1','libacl1-dev'))
 fresh_sdk_library=digest(Path('/usr/lib/x86_64-linux-gnu/libacl.so.1').resolve())
 old_lib=INPUTS/'libacl1_2.4.0-1~trixie+gold1_amd64.deb';install_packages([old_lib],documentation=('libacl1',))
 require(metadata(['/usr/bin/dpkg-query','-W','-f=${Version}','libacl1']).decode()==SDK_VERSION
  and metadata(['/usr/bin/dpkg-query','-W','-f=${Version}','libacl1-dev']).decode()==SDK_VERSION,'sdk_runtime_version')
 lib_payload=regular_payload(old_lib,'libacl1',SDK_VERSION,'approved-libacl');library=acl_library_binding(WORK/'expected-approved-libacl')
 approved_exports=exported_symbols(Path(library['path']));require(NEW_APIS<=set(approved_exports),'sdk_approved_api')
 before=inventory();source,tar_rows,tar_records,tar_packaging=build_vendor('tar');tar=next(r for r in tar_rows if r['name']=='tar')
 install_packages([Path(tar['path'])],documentation=('tar',),complete_tar_payload=True)
 after=inventory();expected=dict(before);expected['tar']=VERSION;require(after==expected,'only_tar_runtime_delta')
 tar_payload=regular_payload(Path(tar['path']),'tar',VERSION,'candidate-tar');require(acl_library_binding(WORK/'expected-approved-libacl')==library
  and exported_symbols(Path(library['path']))==approved_exports,'libacl_abi_changed')
 binary=Path('/usr/bin/tar');abi=tar_binary_contract(binary,library)
 # Preserve normal old/current tar interoperability using approved public DEB.
 old_tar=Path('/build/old-tar');old_tar.mkdir(mode=0o755);command(['/usr/bin/dpkg-deb','-x',str(INPUTS/'tar_1.35+dfsg-5~trixie+gold1_amd64.deb'),str(old_tar)],timeout=60)
 digest(old_tar/'usr/bin/tar',expected=None);require(digest(old_tar/'usr/bin/tar')['sha256']==OLD_TAR_SHA,'old_tar_binary')
 fixture=Path('/work/acl-fd-native');command(['/usr/bin/cc','-Wall','-Wextra','-Werror','-UNDEBUG',str(public/'ordinary_acl_fd_fixture.c'),'-lacl','-o',str(fixture)],user=1001,timeout=60)
 code,raw=run([str(fixture)],cwd=Path('/work'),user=1001,with_code=True,timeout=30,maximum=65536)
 if code!=0:
  match=re.search(rb'ordinary_acl_fd_fixture\.c:([0-9]{1,4}):',raw)
  line=int(match[1])if match and 1<=int(match[1])<=200 else None
  print(json.dumps({'schema':'tar_acl_native_api_failure/1','actual_returncode':code,'known_fixture_line':line,'raw_output_discarded':True,'security_accepted':False},sort_keys=True),flush=True)
  raise RuntimeError('ordinary_native_api')
 api=json.loads(raw)
 expected_api={'schema':'ordinary_acl_fd_compatibility/1','status':'PASS','regular_file':True,'directory_default':True,'fifo_no_payload_open':True,'actual_runtime_inode_checked':True,'all_tar_callsites_proven':False,'exploit_reproduction_performed':False,'production_data_accessed':False,'security_accepted':False}
 require(api==expected_api,'ordinary_native_api')
 code,raw=run(['/usr/local/bin/python3','-I','-S','-B',str(public/'verify_ordinary.py'),'--run-owned-compatibility','--candidate-sha256',digest(binary)['sha256'],'--libacl-sha256',library['sha256']],cwd=Path('/work'),user=1001,with_code=True,timeout=150,maximum=MAX_METADATA)
 ordinary=json.loads(raw)
 if code!=0:
  require(type(ordinary)is dict and set(ordinary)=={'schema','status','phase','closed_reason','security_accepted'}and ordinary['schema']=='tar_ordinary_owned_compatibility/1'and ordinary['status']=='FAIL'and ordinary['security_accepted']is False,'ordinary_tar_failure_shape')
  print(json.dumps(ordinary,sort_keys=True),flush=True);raise RuntimeError('ordinary_tar_compatibility')
 expected_fields={'schema','status','candidate','baseline','approved_libacl','roundtrips','actual_roundtrip_count','default_only_directory_tested','default_cleanup_tested','ordinary_fifo_no_payload_open','bounded_directory_fds_tested','normal_deep_directory_count','production_data_accessed','exploit_or_race_reproduction_performed','whole_image_acl_closed','security_accepted'}
 require(type(ordinary)is dict and set(ordinary)==expected_fields and ordinary['schema']=='tar_ordinary_owned_compatibility/1'and ordinary['status']=='PASS'and ordinary['security_accepted']is False and ordinary['actual_roundtrip_count']==8 and type(ordinary['actual_roundtrip_count'])is int,'ordinary_tar_compatibility')
 require(all(ordinary[k]is True for k in ('default_only_directory_tested','default_cleanup_tested','ordinary_fifo_no_payload_open','bounded_directory_fds_tested'))and all(ordinary[k]is False for k in ('production_data_accessed','exploit_or_race_reproduction_performed','whole_image_acl_closed')),'ordinary_tar_compatibility');require(type(ordinary['normal_deep_directory_count'])is int and ordinary['normal_deep_directory_count']==160,'ordinary_directory_bound');uid_idle()
 require(sorted(os.listdir('/sys/class/net'))==['lo'],'offline_final')
 OUT.mkdir(mode=0o755)
 for name,path in [('tar_'+VERSION+'_amd64.deb',Path(tar['path'])),*[(('acl_'+SDK_VERSION if kind=='acl'else'tar_'+VERSION)+'_amd64.'+suffix,path)for kind,records in [('acl',sdk_records),('tar',tar_records)]for suffix,path in records.items()]]:
  pin=digest(path);copy_exact(path,OUT/name,(pin['bytes'],pin['sha256']),mode=0o644)
 sdk_proof={'schema':'tar_acl_compiler_sdk/1','fresh_sdk_family':sdk_rows,'compiler_only':True,'fresh_sdk_library_before_reinstall':fresh_sdk_library,
  'actual_approved_runtime_after_reinstall':library,'runtime_deb':{'bytes':OLD_PACKAGES[old_lib.name][0],'sha256':OLD_PACKAGES[old_lib.name][1]},'runtime_replacement_exported':False,'old_exports_preserved':approved_exports,'security_accepted':False}
 vendor_tests={'vendor_test_selection':'unmodified_default','default_vendor_package_build_exit_codes':{'acl':0,'tar':0},
  'tests_disabled_by_build_options':False,'vendor_zero_skip_coverage_proven':False,'privileged_or_optional_vendor_cases_may_skip':True}
 reports={'source-proof.json':{'schema':'tar_acl_source_build/1','sources':sources,'patch':{'bytes':PATCH_PIN[0],'sha256':PATCH_PIN[1]},'source_manifest':json.loads(bytes_at(RECIPE/'source-manifest.json')),
  'sdk_packaging':sdk_packaging,'tar_packaging':tar_packaging,**vendor_tests,'maintainer_dsc_signatures_authenticated':False,'security_accepted':False},
 'sdk-proof.json':sdk_proof,'abi-contract.json':abi,'ordinary-compatibility.json':{'schema':'tar_acl_ordinary_compatibility/1','native_api':api,'tar_archives':ordinary,'security_accepted':False},
 'runtime-payload.json':{'schema':'tar_acl_runtime_payload/1','libacl':lib_payload,'tar':tar_payload,'security_accepted':False},
 'caller-proof.json':{'schema':'tar_descriptor_caller_build/1','tar':digest(binary),'source_patch_sha256':PATCH_PIN[1],'compiled_caller_contract':abi,
  'ordinary_native_api_passed':True,'ordinary_tar_compatibility_passed':True,'whole_image_other_callers_assessed':False,'all_cves_attested':False,'security_accepted':False},
 'provenance.json':{'schema':'tar_acl_package_provenance/1','context':context,'preparation_sha256':sha256(prep).hexdigest(),'preparation':preparation,
  'tar_packages':tar_rows,'source_date_epoch':SOURCE_EPOCH,'normal_package_version':VERSION,'build_metadata_unsigned':True,**vendor_tests,'runtime_loader_override_used':False,
  'vendor_build_only_loader_mechanics_not_disabled':True,'uid1001_children_gone':True,'only_new_tar_runtime_exported':True,'production_installed':False,'security_accepted':False}}
 for name,value in reports.items():publish(OUT/name,encoded(value));(OUT/name).chmod(0o644)
 require({p.name for p in OUT.iterdir()}==set(OUTPUTS),'output_inventory')
 for name,maximum in OUTPUTS.items():digest(OUT/name,maximum=maximum)
 print(json.dumps({'schema':'tar_acl_normal_package_build/1','status':'BUILT_NOT_ACCEPTED','outputs':sorted(OUTPUTS),'security_accepted':False},sort_keys=True))

def main(argv=None):
 p=argparse.ArgumentParser();p.add_argument('--configure-build-repository',action='store_true');p.add_argument('--build',action='store_true');args=p.parse_args(argv)
 if not args.configure_build_repository and not args.build:print(json.dumps({'schema':'tar_acl_normal_package_build/1','status':'UNARMED','build_performed':False,'security_accepted':False}));return 0
 require(args.configure_build_repository!=args.build,'one_phase_only');signal.signal(signal.SIGALRM,lambda *_:(_ for _ in ()).throw(RuntimeError('build_deadline')));signal.alarm(4800 if args.build else 1800)
 try:
  if args.configure_build_repository:configure_repository()
  else:build()
  return 0
 finally:signal.alarm(0)
if __name__=='__main__':raise SystemExit(main())
