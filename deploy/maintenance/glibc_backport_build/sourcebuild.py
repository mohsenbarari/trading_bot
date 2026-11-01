"""Full local glibc packages in disposable CI; three closed distro profiles.

No operations on import/default and no Host package installation. The Debian
image profile and two Ubuntu Host candidate profiles never share source inputs.
"""
from pathlib import Path
from hashlib import sha256
from types import ModuleType
import argparse,json,os,re,selectors,signal,stat,subprocess,sys,time
from urllib.parse import urlsplit

RECIPE=Path('/recipe');ROOT=Path('/build');DOWNLOAD=ROOT/'downloads';BUILD_AREA=ROOT/'build-area';SOURCE=BUILD_AREA/'glibc-source';OUT=Path('/out')
OLD='2.41-12+deb13u4';VERSION=OLD+'+gold1';EPOCH=1793491200
RUNTIME_PACKAGES=['libc6','libc-bin']
ENV={'PATH':'/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin','HOME':'/work/home','LC_ALL':'C.UTF-8',
 'LANG':'C.UTF-8','TMPDIR':'/build/tmp','DEBIAN_FRONTEND':'noninteractive','SOURCE_DATE_EPOCH':str(EPOCH),'DEB_BUILD_OPTIONS':'parallel=4',
 'DEB_BUILD_PROFILES':'noudeb','QUILT_PATCHES':'debian/patches','QUILT_PATCH_OPTS':'--fuzz=0'}
SOURCES={'glibc_2.41.orig.tar.xz':(20323540,'f24aa441021121a79266f0d75242706cab8843a47901fefe74527491807f1998'),
 'glibc_2.41-12+deb13u4.debian.tar.xz':(499524,'dda4153511bfd543502d18e5bc9323110996fe9c531f14ae3fad6eb17f027c7d'),
 'glibc_2.41-12+deb13u4.dsc':(7576,'0915324aa646bb99abfaa9aa6ecb734b30babd28454a3e51f3152912370fbd3e')}
BASELINE={'libc6_2.41-12+deb13u4_amd64.deb':(2846580,'967aa62605721081c3eb2a17650611a792aa802d76a6511d1840242623d204c9'),
 'libc-bin_2.41-12+deb13u4_amd64.deb':(638452,'dc8c79647fe3a5f37f72a400414f70263d0212f010ff2c09f990e30d91e38d1c')}
OLD_LIBC=(1995216,'9792e3cbb541c8f44c7acf5f14f4022ea62998ecc787d326bed4d8b6547dfd92')
OLD_LOADER=(225672,'c8438e4fde1934e61c88311633f00949ff645d5c04cdb8671fa3d78164d2f307')
DSC_SIGNER='52BC8695BE34F90AD7D40CB81388C0F899E8336B'
PROFILE_NAME='trixie241';KEYRING_PACKAGE='debian-keyring';TARGET_DIST='trixie';OS_ID='debian';OS_CODENAME='trixie';EXTRA_TOOLS=[];_PROFILE_CONFIGURED=False
APT_SOURCES='Types: deb deb-src\nURIs: https://snapshot.debian.org/archive/debian/20261024T000000Z/\nSuites: trixie trixie-updates\nComponents: main\nSigned-By: /usr/share/keyrings/debian-archive-keyring.gpg\nCheck-Valid-Until: no\n\nTypes: deb deb-src\nURIs: https://snapshot.debian.org/archive/debian-security/20261024T000000Z/\nSuites: trixie-security\nComponents: main\nSigned-By: /usr/share/keyrings/debian-archive-keyring.gpg\nCheck-Valid-Until: no\n'
PATCH='CVE-2026-84243-glibc-2.41.patch';QUILT_PATCH='any/local-CVE-2026-84243-gold1.patch'
BEFORE={'intl/dcigettext.c':(46551,'059431d99c6a657dac28ff15f4a2cbe31eec7a10dd7690b3630fbbc42ad2a304'),
 'intl/Makefile':(5782,'31df6f7ad0b6217aa9bafff60704e43de1f9d5d6572881b8471488fe8f474166')}
AFTER={'intl/dcigettext.c':(46515,'06631f67d1832181c2f7c36296d8a1e0b074adb5aa9e5113a4f544624343bd40'),
 'intl/Makefile':(5991,'c594a17946172f83f6f16fd352916cae30f58b6973a5fc9dafbbb0c42da7d405'),
 'intl/tst-gettext-path-traversal.c':(3336,'c732e301bbe037a6aa612f1452d7768d0bb3efd893f4fbf1d4d02e4651e2d3f6')}


def need(ok,code):
 if ok is not True:raise RuntimeError(code)
def configure_profile(entry,value):
 """Exactly two additional reviewed Source presets; no runtime policy callback."""
 global PROFILE_NAME,OLD,VERSION,TARGET_DIST,APT_SOURCES,SOURCES,BASELINE,OLD_LIBC,OLD_LOADER,BEFORE,AFTER,PATCH,DSC_SIGNER,KEYRING_PACKAGE,EXTRA_TOOLS,OS_ID,OS_CODENAME,RUNTIME_PACKAGES,_PROFILE_CONFIGURED
 keys={'name','old_version','version','target_dist','apt_sources','source_files','baseline_debs','old_libc','old_loader','before','after','patch','dsc_signer','keyring_package','extra_tools','os_id','os_codename','runtime_packages'}
 need(_PROFILE_CONFIGURED is False and type(value)is dict and set(value)==keys,'profile_shape')
 expected={'ubuntu239':'2.39-0ubuntu8.9','ubuntu243':'2.43-2ubuntu2.4'}
 name=value['name'];need(name in expected and value['old_version']==expected[name]and value['version']==expected[name]+'+gold1','profile_version')
 need(Path(entry).name=='worker_'+name+'.py'and value['dsc_signer']is None and value['os_id']=='ubuntu','profile_source')
 need(type(value['apt_sources'])is str and 'Signed-By:'in value['apt_sources']and 'trusted=yes'not in value['apt_sources'].lower()and re.search(r'(?mi)^\s*trusted\s*:',value['apt_sources'])is None,'profile_apt')
 need(type(value['extra_tools'])is list and all(type(x)is str and re.fullmatch('[a-z0-9+.-]+',x)for x in value['extra_tools']),'profile_tools')
 families={'ubuntu239':['libc6','libc-bin','libc-dev-bin','libc-devtools','libc6-dev','locales'],
  'ubuntu243':['libc6','libc-bin','libc-dev-bin','libc-gconv-modules-extra','libc6-dev','locales']}
 need(value['runtime_packages']==families[name],'profile_runtime_family');RUNTIME_PACKAGES=value['runtime_packages'][:]
 PROFILE_NAME=name;OLD=value['old_version'];VERSION=value['version'];TARGET_DIST=value['target_dist'];APT_SOURCES=value['apt_sources'];SOURCES=value['source_files'];BASELINE=value['baseline_debs'];OLD_LIBC=value['old_libc'];OLD_LOADER=value['old_loader'];BEFORE=value['before'];AFTER=value['after'];PATCH=value['patch'];DSC_SIGNER=value['dsc_signer'];KEYRING_PACKAGE=value['keyring_package'];EXTRA_TOOLS=value['extra_tools'];OS_ID=value['os_id'];OS_CODENAME=value['os_codename'];_PROFILE_CONFIGURED=True


def enc(v):return(json.dumps(v,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode()
def read(path,maximum=16*1024**2):
 path=Path(path);fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
 try:
  before=os.fstat(fd);need(stat.S_ISREG(before.st_mode)and 0<before.st_size<=maximum,'file_bound')
  raw=os.pread(fd,before.st_size+1,0);need(len(raw)==before.st_size and key(before)==key(os.fstat(fd))==key(path.lstat()),'file_changed');return raw
 finally:os.close(fd)
def key(s):return(s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
def digest(path,expected=None,maximum=512*1024**2):
 p=Path(path);fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC);h=sha256();n=0
 try:
  before=key(os.fstat(fd));need(stat.S_ISREG(before[2])and 0<before[6]<=maximum,'file_bound')
  while True:
   block=os.read(fd,min(65536,maximum-n+1))
   if not block:break
   n+=len(block);need(n<=maximum,'file_bound');h.update(block)
  need(n==before[6]and key(os.fstat(fd))==key(p.lstat())==before,'file_changed')
 finally:os.close(fd)
 value={'bytes':n,'sha256':h.hexdigest()};need(expected is None or(n,h.hexdigest())==expected,'file_pin');return value

def save(path,raw,maximum=16*1024**2):
 need(type(raw)is bytes and 0<len(raw)<=maximum,'output_bound');fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o644)
 try:
  os.fchmod(fd,0o644);v=memoryview(raw)
  while v:n=os.write(fd,v[:65536]);need(n>0,'short_write');v=v[n:]
  os.fsync(fd)
 finally:os.close(fd)
def copy_payload(source,target,expected):
 need(not target.exists(),'output_exclusive');source=Path(source);before=key(source.lstat())
 r=os.open(source,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC);w=None;h=sha256();n=0
 try:
  need(key(os.fstat(r))==before and before[6]==expected['bytes'],'copy_source')
  w=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o644);os.fchmod(w,0o644)
  while True:
   block=os.read(r,min(65536,expected['bytes']-n+1))
   if not block:break
   n+=len(block);need(n<=expected['bytes'],'copy_bound');h.update(block);v=memoryview(block)
   while v:count=os.write(w,v);need(count>0,'short_write');v=v[count:]
  need(n==expected['bytes']and h.hexdigest()==expected['sha256']and key(os.fstat(r))==key(source.lstat())==before,'copy_changed');os.fsync(w)
 finally:
  if w is not None:os.close(w)
  os.close(r)
 digest(target,(expected['bytes'],expected['sha256']))


def module(name):
 p=RECIPE/name;m=ModuleType('_glibc_'+p.stem);m.__file__=str(p);exec(compile(read(p,131072),str(p),'exec'),m.__dict__);return m

def run(argv,*,cwd=None,seconds=60,maximum=128*1024**2,user=None,with_code=False,env=None,forward=False):
 """Streaming output bound before accumulation; each public log is finite."""
 actual=dict(ENV if env is None else env);deadline=time.monotonic()+seconds
 options={}if user is None else{'user':user,'group':user,'extra_groups':[]}
 c=subprocess.Popen(argv,cwd=cwd,env=actual,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,start_new_session=True,**options)
 b=bytearray();eof=False;primary=None;value=None;cleanup=[];group_closed=False
 try:
  with selectors.DefaultSelector()as poll:
   os.set_blocking(c.stdout.fileno(),False);poll.register(c.stdout,selectors.EVENT_READ)
   while not eof:
    need(time.monotonic()<deadline,'command_deadline')
    for item,_ in poll.select(min(.1,max(0,deadline-time.monotonic()))):
     block=os.read(item.fd,min(65536,maximum-len(b)+1))
     if not block:eof=True;break
     need(len(b)+len(block)<=maximum,'command_output_bound');b.extend(block)
     if forward:os.write(1,block)
  remaining=deadline-time.monotonic();need(remaining>0,'command_deadline')
  try:rc=c.wait(timeout=remaining)
  except subprocess.TimeoutExpired:need(False,'command_deadline')
  need(time.monotonic()<deadline,'command_deadline');need(with_code or rc==0,'command_failed')
  # Leader exit and EOF can hide a descendant which closed stdout. Reuse the
  # proven finite original-PGID guard; never publish success while it remains.
  group_end=min(deadline,time.monotonic()+1)
  while True:
   try:os.killpg(c.pid,0)
   except ProcessLookupError:break
   need(time.monotonic()<group_end,'command_group_survived_leader');time.sleep(.01)
  need(time.monotonic()<deadline,'command_deadline');group_closed=True
  value=(rc,bytes(b))if with_code else bytes(b)
 except BaseException as error:primary=error
 finally:
  if not group_closed:
   try:
    try:os.killpg(c.pid,signal.SIGKILL)
    except ProcessLookupError:pass
    c.wait(timeout=10)
   except BaseException:cleanup.append('command_cleanup_unknown')
  try:c.stdout.close()
  except BaseException:cleanup.append('command_stdout_close_unknown')
 if primary is not None:
  primary.command_cleanup_codes=cleanup;raise primary
 need(not cleanup,'command_cleanup_unknown');need(time.monotonic()<deadline,'command_deadline');return value

def context():
 need(os.geteuid()==os.getegid()==0 and Path.cwd()==ROOT and Path('/.dockerenv').is_file(),'disposable_container_only')
 need(sys.flags.isolated==sys.flags.no_site==1 and os.uname().machine=='x86_64','builder_runtime')
 release=dict(line.split('=',1)for line in read(Path('/etc/os-release').resolve(),65536).decode().splitlines()if'='in line)
 need(release.get('ID','').strip(chr(34))==OS_ID and release.get('VERSION_CODENAME','').strip(chr(34))==OS_CODENAME,'profile_OS')
 manifest=json.loads(read(RECIPE/'context-manifest.json',131072));need(manifest['schema']=='glibc84243_allowlisted_context/1'
  and manifest['profile']==PROFILE_NAME and re.fullmatch(r'[a-z0-9:./_-]+@sha256:[a-f0-9]{64}',manifest['immutable_base'])is not None,'context_manifest')
 for name,pin in manifest['files'].items():
  need('/'not in name and name not in{'.','..'},'context_leaf');digest(RECIPE/name,(pin['bytes'],pin['sha256']))
 return manifest

def apt_snapshot(phase):
 """Retain original authenticated compressed indexes before APT changes them."""
 target=ROOT/(phase+'-APT');target.mkdir(mode=0o755);total=0;files={}
 targets=run(['/usr/bin/apt-get','indextargets','--format','$(FILENAME)\t$(URI)\t$(METAKEY)\t$(REPO_URI)\t$(TRUSTED)\t$(IDENTIFIER)','Identifier: Packages'],maximum=1048576)
 for p in sorted(Path('/var/lib/apt/lists').iterdir()):
  if p.name in{'lock','partial','auxfiles'}:continue
  need(p.is_file()and not p.is_symlink(),'apt_index_shape')
  if not p.stat().st_size:continue
  pin=digest(p,maximum=128*1024**2);total+=pin['bytes'];need(total<=256*1024**2,'apt_metadata_bound')
  copy_payload(p,target/p.name,pin);files[p.name]=pin
 sources={}
 for p in [Path('/etc/apt/sources.list'),*sorted(Path('/etc/apt/sources.list.d').iterdir())]:
  if not os.path.lexists(p):continue
  need(p.is_file()and not p.is_symlink(),'apt_source_shape')
  if p.stat().st_size:sources[str(p)]=read(p,131072).decode('utf-8')
 rows=[]
 for line in targets.decode('utf-8').splitlines():
  values=line.split('\t');need(len(values)==6 and '$('not in line,'apt_target_shape')
  path,uri,meta,repo,trusted,identifier=values;parsed=urlsplit(repo)
  need(Path(path).parent==Path('/var/lib/apt/lists')and Path(path).name in files and identifier=='Packages'and trusted=='yes'
   and parsed.scheme in{'http','https'}and parsed.hostname in{'archive.ubuntu.com','security.ubuntu.com','snapshot.debian.org'}
   and parsed.port is None and not parsed.query and not parsed.fragment and parsed.username is None and parsed.password is None
   and uri.startswith(repo)and re.fullmatch('[A-Za-z0-9+./_-]+/Packages',meta)is not None,'apt_target_untrusted')
  need(any(repo in text.split()for text in sources.values()),'apt_target_repository')
  rows.append({'filename':Path(path).name,'URI':uri,'MetaKey':meta,'Repo_URI':repo,'Trusted':trusted})
 need(len(rows)<=64 and(phase=='bootstrap'or len(rows)>0),'apt_target_inventory')
 save(target/'sources.json',enc(sources));save(target/'targets.json',enc(rows))
 return {'path':target.name,'files':files,'targets':rows,'sources':sources}

def selected_index_records(snapshot,wanted):
 """Authenticate decompressed SHA/size while streaming only chosen stanzas."""
 records=[]
 for target in snapshot['targets']:
  p=ROOT/snapshot['path']/target['filename'];stem=p.name
  for suffix in('.lz4','.gz','.xz','.bz2'):
   if stem.endswith(suffix):stem=stem[:-len(suffix)];break
  suffix=target['MetaKey'].replace('/','_');need(stem.endswith('_'+suffix),'apt_release_path')
  release=ROOT/snapshot['path']/(stem[:-len(suffix)]+'InRelease');raw=read(release,4*1024**2)
  release_rows=[];inside=False
  for line in raw.decode('utf-8').splitlines():
   if line=='SHA256:':inside=True;continue
   if inside and not line.startswith(' '):inside=False
   if inside:
    parts=line.split()
    if len(parts)==3 and parts[2]==target['MetaKey']:
     need(re.fullmatch('[a-f0-9]{64}',parts[0])is not None and parts[1].isdigit(),'apt_release_entry');release_rows.append((int(parts[1]),parts[0]))
  need(len(release_rows)==1 and 0<release_rows[0][0]<=512*1024**2,'apt_release_entry')
  expected_size,expected_sha=release_rows[0];child=subprocess.Popen(['/usr/lib/apt/apt-helper','cat-file',str(p)],env=ENV,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,start_new_session=True)
  h=sha256();n=0;pending=bytearray();fields={};stanza_size=0;end=time.monotonic()+120;eof=False
  def line_seen(line):
   nonlocal fields,stanza_size
   line=bytes(line)
   stanza_size+=len(line)+1;need(stanza_size<=1048576,'apt_stanza_bound')
   if not line:
    identity=(fields.get('Package'),fields.get('Architecture'),fields.get('Version'))
    if identity in wanted:
     need({'Package','Version','Architecture','SHA256','Size','Filename'}<=set(fields),'apt_selected_fields')
     records.append({**fields,'evidence_phase':snapshot['path'].removesuffix('-APT'),'authenticated_index':p.name,
      'authenticated_InRelease':release.name,'repository':target['Repo_URI'],'Packages_URI':target['URI']})
    fields={};stanza_size=0;return
   if line.startswith((b' ',b'\t')):return
   name,sep,value=line.partition(b':')
   if name in{b'Package',b'Version',b'Architecture',b'SHA256',b'Size',b'Filename'}:
    key=name.decode();need(sep==b':'and key not in fields,'apt_selected_fields');fields[key]=value.strip().decode('utf-8')
  try:
   with selectors.DefaultSelector()as poll:
    os.set_blocking(child.stdout.fileno(),False);poll.register(child.stdout,selectors.EVENT_READ)
    while not eof:
     need(time.monotonic()<end,'apt_index_deadline')
     for item,_ in poll.select(.1):
      block=os.read(item.fd,min(65536,expected_size-n+1))
      if not block:eof=True;break
      n+=len(block);need(n<=expected_size,'apt_index_bound');h.update(block);pending.extend(block)
      while b'\n'in pending:
       line,_,tail=pending.partition(b'\n');pending=bytearray(tail);need(len(line)<=131072,'apt_line_bound');line_seen(line)
      need(len(pending)<=131072,'apt_line_bound')
   if pending:line_seen(bytes(pending))
   line_seen(b'');need(child.wait(timeout=max(.01,end-time.monotonic()))==0,'apt_index_decode')
   need((n,h.hexdigest())==(expected_size,expected_sha),'apt_index_authentication')
  finally:
   if child.poll()is None:
    try:os.killpg(child.pid,signal.SIGKILL)
    except ProcessLookupError:pass
    child.wait(timeout=10)
   child.stdout.close()
 return records

def signal_qualification():
 """Actual disposable-container cross-UID child and failure cleanup, before build."""
 identity=run([sys.executable,'-I','-S','-B','-c','import os,json;print(json.dumps([os.getuid(),os.getgid(),os.getgroups()]))'],user=10001,maximum=4096)
 need(json.loads(identity)==[10001,10001,[]],'build_user_identity')
 try:run([sys.executable,'-I','-S','-B','-c','import time;time.sleep(30)'],user=10001,seconds=.15,maximum=4096)
 except RuntimeError as error:need(str(error)=='command_deadline','build_user_timeout_cleanup')
 else:need(False,'build_user_timeout_cleanup')
 for p in Path('/proc').iterdir():
  if not p.name.isdigit():continue
  try:raw=(p/'status').read_bytes()
  except FileNotFoundError:continue
  need(len(raw)<=65536,'build_user_census_bound')
  rows=[line.split()[1:]for line in raw.splitlines()if line.startswith(b'Uid:')]
  need(len(rows)==1 and len(rows[0])==4 and all(x.isdigit()for x in rows[0]),'build_user_census_shape')
  need(b'10001'not in rows[0],'build_user_child_survived')
 return {'actual_identity':[10001,10001,[]],'actual_cross_UID_timeout_owned_group_reaped':True}

def prepare():
 ctx=context();need(not DOWNLOAD.exists(),'fresh_preparation_required');DOWNLOAD.mkdir(mode=0o755)
 base_status=read(ROOT/'bootstrap-evidence/base-status.tsv',4*1024**2);bootstrap=apt_snapshot('bootstrap')
 (ROOT/'tmp').mkdir(mode=0o1777);(ROOT/'tmp').chmod(0o1777)
 # Remove only known public Docker apt cache deletion hook inside this builder.
 docker_clean=Path('/etc/apt/apt.conf.d/docker-clean');removed=digest(docker_clean)if docker_clean.exists()else None
 if removed is not None:docker_clean.unlink()
 for p in Path('/etc/apt/sources.list.d').iterdir():need(p.is_file()and not p.is_symlink(),'apt_source');p.unlink()
 Path('/etc/apt/sources.list').write_text('')
 stanza=APT_SOURCES
 Path('/etc/apt/sources.list.d/locked.sources').write_text(stanza)
 Path('/usr/sbin/policy-rc.d').write_text('#!/bin/sh\nexit 101\n');Path('/usr/sbin/policy-rc.d').chmod(0o755)
 apt=['/usr/bin/apt-get','-o','Acquire::Retries=0','-o','APT::Update::Error-Mode=any','-o','Acquire::AllowInsecureRepositories=false','-o','APT::Get::AllowUnauthenticated=false']
 run([*apt,'update'],seconds=900,forward=True)
 run([*apt,'-y','--no-install-recommends','build-dep','--build-profiles=noudeb','--only-source','glibc='+OLD],seconds=1200,forward=True)
 run([*apt,'-y','--no-install-recommends','install','build-essential','dpkg-dev','fakeroot','quilt','gettext','debhelper',KEYRING_PACKAGE,'gpgv','binutils','passwd','util-linux',*EXTRA_TOOLS],seconds=900,forward=True)
 run([*apt,'source','--download-only','--only-source','glibc='+OLD],cwd=DOWNLOAD,seconds=600,forward=True)
 for name,pin in SOURCES.items():digest(DOWNLOAD/name,pin)
 valid=[]
 if DSC_SIGNER is not None:
  signature=run(['/usr/bin/gpgv','--status-fd','1','--keyring','/usr/share/keyrings/debian-keyring.gpg',str(DOWNLOAD/('glibc_'+OLD+'.dsc'))])
  valid=[l.split()[2]for l in signature.decode().splitlines()if l.startswith('[GNUPG:] VALIDSIG ')]
  need(valid==[DSC_SIGNER],'DSC_signer')
 else:need(PROFILE_NAME in{'ubuntu239','ubuntu243'},'source_auth_profile')
 run([*apt,'download',*(name+'='+OLD for name in RUNTIME_PACKAGES)],cwd=DOWNLOAD,seconds=600,forward=True)
 for name,pin in BASELINE.items():digest(DOWNLOAD/name,pin)
 run(['/usr/sbin/groupadd','--gid','10001','glibc-builder']);run(['/usr/sbin/useradd','--uid','10001','--gid','10001','--home-dir','/work/home','--no-create-home','--shell','/usr/sbin/nologin','glibc-builder'])
 Path('/work/home').mkdir(parents=True,mode=0o700);os.chown('/work/home',10001,10001)
 qualifier=signal_qualification()
 packages=run(['/usr/bin/dpkg-query','-W','-f=${db:Status-Status}\t${binary:Package}\t${Version}\t${Architecture}\n'],maximum=4*1024**2)
 releases={p.name:digest(p,maximum=4*1024**2)for p in Path('/var/lib/apt/lists').glob('*InRelease')};need(1<=len(releases)<=6,'apt_release_inventory')
 indexes={p.name:digest(p,maximum=128*1024**2)for p in Path('/var/lib/apt/lists').iterdir()if p.is_file()and p.name not in{'lock'}and p.stat().st_size>0}
 V=module('verify_build.py');deps={}
 for p in sorted(Path('/var/cache/apt/archives').glob('*.deb')):
  control=V.control_fields(run(['/usr/bin/dpkg-deb','-f',str(p),'Package','Version','Architecture'],maximum=131072))
  need(set(control)=={'Package','Version','Architecture'},'dependency_control');deps[p.name]={**digest(p),'control':control}
 final_indexes=apt_snapshot('final');wanted={(name,arch,version)for(name,arch),version in V.status_packages(packages).items()if V.status_packages(base_status).get((name,arch))!=version}
 records=selected_index_records(bootstrap,wanted)+selected_index_records(final_indexes,wanted)
 closure=V.dependency_closure(base_status,packages,deps,records)
 hook=ROOT/'bootstrap-evidence/docker-clean'
 save(ROOT/'preparation.json',enc({'schema':'glibc84243_authenticated_preparation/1','sources':{k:digest(DOWNLOAD/k,v)for k,v in SOURCES.items()},'DSC_VALID_SIG':valid,'source_authentication_profile':'Debian_maintainer_DSC_plus_archive_SignedSources'if DSC_SIGNER else'Ubuntu_archive_SignedSources_and_exact_rooted_descriptor_hashes','dependency_packages_status_sha256':sha256(packages).hexdigest(),'immutable_base_status':digest(ROOT/'bootstrap-evidence/base-status.tsv'),'immutable_base_OCI':ctx['immutable_base'],'complete_dependency_closure':closure,'dependency_debs':deps,'bootstrap_APT':bootstrap,'final_APT':final_indexes,'actual_cross_UID_signal_qualification':qualifier,'signed_InRelease':releases,'APT_indexes':indexes,'preserved_pre_bootstrap_public_docker_clean':digest(hook)if hook.exists()else None,'removed_builder_public_docker_clean':removed,'context':ctx,'security_accepted':False}))
 save(ROOT/'dependency-status.tsv',packages)
 print(json.dumps({'status':'AUTHENTICATED_PREPARATION_COMPLETE','profile':PROFILE_NAME,'installed_packages':closure['installed_package_count'],'all_post_base_changes_have_exact_authenticated_payload':True,'security_accepted':False},sort_keys=True))


def baseline_payload():
 base=ROOT/'official-baseline';base.mkdir(mode=0o755)
 expected={name+'_'+OLD+'_'+('all'if name=='locales'else'amd64')+'.deb'for name in RUNTIME_PACKAGES}
 need(set(BASELINE)==expected,'runtime_baseline_family')
 for name,pin in BASELINE.items():digest(DOWNLOAD/name,pin);run(['/usr/bin/dpkg-deb','-x',str(DOWNLOAD/name),str(base)])
 libc=base/'usr/lib/x86_64-linux-gnu/libc.so.6';loader=base/'usr/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2'
 digest(libc,OLD_LIBC);digest(loader,OLD_LOADER);return base,libc,loader

def build():
 ctx=context();V=module('verify_build.py');prep=json.loads(read(ROOT/'preparation.json',16*1024**2))
 need(prep['schema']=='glibc84243_authenticated_preparation/1'and sorted(os.listdir('/sys/class/net'))==['lo'],'offline_prepared_build_only')
 current_status=run(['/usr/bin/dpkg-query','-W','-f=${db:Status-Status}\t${binary:Package}\t${Version}\t${Architecture}\n'],maximum=4*1024**2)
 need(sha256(current_status).hexdigest()==prep['dependency_packages_status_sha256'],'prepared_dependency_status_changed')
 need(not SOURCE.exists()and not BUILD_AREA.exists()and not OUT.exists(),'fresh_build_required');OUT.mkdir(mode=0o755)
 BUILD_AREA.mkdir(mode=0o755);os.chown(BUILD_AREA,10001,10001)
 for name,pin in SOURCES.items():digest(DOWNLOAD/name,pin)
 run(['/usr/bin/dpkg-source','-x',str(DOWNLOAD/('glibc_'+OLD+'.dsc')),str(SOURCE)],seconds=180)
 for name,pin in BEFORE.items():digest(SOURCE/name,pin)
 series=SOURCE/'debian/patches/series';original_series=read(series,131072);need(QUILT_PATCH.encode()not in original_series,'patch_already_present')
 target=SOURCE/'debian/patches'/QUILT_PATCH;target.parent.mkdir(mode=0o755,exist_ok=True);target.write_bytes(read(RECIPE/PATCH,65536));series.write_bytes(original_series+b'\n'+QUILT_PATCH.encode()+b'\n')
 run(['/usr/bin/patch','--batch','--fuzz=0','--forward','--dry-run','-p1','-i',str(target)],cwd=SOURCE)
 run(['/usr/bin/quilt','--quiltrc','/dev/null','push'],cwd=SOURCE)
 for name,pin in AFTER.items():digest(SOURCE/name,pin)
 changelog=SOURCE/'debian/changelog';old_changelog=read(changelog,2*1024**2)
 header=('glibc ('+VERSION+') '+TARGET_DIST+'; urgency=high\n\n  * Local source backport of proposed upstream v7 CVE-2026-84243 fix.\n    Not an official Debian or upstream fixed-version claim.\n\n -- Trading Bot Security Build <noreply@gold-trade.invalid>  Sun, 01 Nov 2026 00:00:00 +0000\n\n').encode()
 changelog.write_bytes(header+old_changelog)
 source_proof={'schema':'glibc84243_local_full_Debian_backport_source/1','original_version':OLD,'local_version':VERSION,'profile':PROFILE_NAME,'target_distribution':TARGET_DIST,'source_date_epoch':EPOCH,'source_epoch_is_not_a_probe_timestamp':True,'authenticated_originals':prep['sources'],'original_Debian_patch_series':{'bytes':len(original_series),'sha256':sha256(original_series).hexdigest()},'last_appended_patch':{'path':QUILT_PATCH,**digest(target)},'exact_source_after':{name:digest(SOURCE/name,pin)for name,pin in AFTER.items()},'upstream_v7_merged_claimed':False,'official_Debian_fixed_claimed':False,'security_accepted':False}
 save(OUT/'source-proof.json',enc(source_proof));save(OUT/'preparation.json',read(ROOT/'preparation.json'));save(OUT/'dependency-status.tsv',read(ROOT/'dependency-status.tsv'))
 save(OUT/'original-signed-source.dsc',read(DOWNLOAD/('glibc_'+OLD+'.dsc'),65536));save(OUT/'new-source-changelog',read(changelog,2*1024**2))
 run(['/usr/bin/tar','--create','--xz','--file',str(OUT/'signed-APT-metadata.tar.xz'),'--directory',str(ROOT),'bootstrap-evidence','bootstrap-APT','final-APT'],seconds=180)
 (OUT/'signed-APT-metadata.tar.xz').chmod(0o644);digest(OUT/'signed-APT-metadata.tar.xz',maximum=256*1024**2)
 run(['/usr/bin/chown','-R','10001:10001',str(SOURCE)])
 # Standard complete binary build, no nocheck/nobiarch/stage/test selection.
 rc,log=run(['/usr/bin/dpkg-buildpackage','-b','-us','-uc','-rfakeroot'],cwd=SOURCE,seconds=13200,maximum=256*1024**2,user=10001,with_code=True,forward=True)
 save(OUT/'full-Debian-build.log',log,maximum=256*1024**2);need(rc==0,'Debian_build_failed')
 for name,pin in AFTER.items():digest(SOURCE/name,pin)
 ledgers={};native=SOURCE/'build-tree/amd64-libc';need(native.is_dir(),'native_build_missing')
 for flavour in('libc','i386','x32'):
  p=SOURCE/('build-tree/amd64-'+flavour)/'tests.sum'
  if p.is_file():
   ledger_raw=read(p);ledgers[flavour]={**V.vendor_ledger(ledger_raw,native=flavour=='libc'),'tests_sum':digest(p)}
   save(OUT/('vendor-'+flavour+'.sum'),ledger_raw)
  else:
   need(flavour!='libc','native_vendor_tests_missing');ledgers[flavour]={'vendor_default_test_execution_unqualified':True,'production_runtime_scope':False}
 save(OUT/'vendor-suite.json',enc({'schema':'glibc84243_full_default_Debian_vendor_ledger/1','actual_command':['dpkg-buildpackage','-b','-us','-uc','-rfakeroot'],'actual_returncode':rc,'build_options':ENV['DEB_BUILD_OPTIONS'],'build_profiles':ENV['DEB_BUILD_PROFILES'],'no_test_body_or_filter_override':True,'native_CVE_explicit_PASS_required':True,'flavours':ledgers,'security_accepted':False}))
 # Do not treat Debian stable-security packaging's0 as proof without the ledger.
 test=native/'intl/tst-gettext-path-traversal';digest(test);
 for suffix in('.test-result','.out'):
  target=native/('intl/tst-gettext-path-traversal'+suffix)
  if target.is_file()and target.stat().st_size:save(OUT/('native-CVE-regression'+suffix),read(target))
 catalog=native/'intl/domaindir/de_DE/LC_MESSAGES/codeset.mo';locale=native/'localedata/de_DE.ISO-8859-1/LC_CTYPE'
 fixture={'test':digest(test),'catalog':digest(catalog),'locale':digest(locale)}
 base,libc,loader=baseline_payload();oldlib=str(libc.parent)
 clean={k:v for k,v in ENV.items()if k not in{'DEB_BUILD_OPTIONS','DEB_BUILD_PROFILES'}};clean['LOCPATH']=str(native/'localedata')
 mapping=run([str(loader),'--library-path',oldlib,'--list',str(test)],cwd=SOURCE,env=clean)
 loadproof=V.loader_list(mapping,str(base),str(libc))
 rc,raw=run([str(loader),'--library-path',oldlib,str(test)],cwd=SOURCE,env=clean,maximum=131072,with_code=True)
 counter=V.old_counterfactual(rc,raw);need(fixture=={'test':digest(test),'catalog':digest(catalog),'locale':digest(locale)},'counterfactual_fixture_changed')
 save(OUT/'old-counterfactual.log',raw);save(OUT/'old-counterfactual.json',enc({'schema':'glibc84243_same_vendor_ELF_old_library_counterfactual/1','old_libc':digest(libc,OLD_LIBC),'old_loader':digest(loader,OLD_LOADER),'loader_mapping':loadproof,'same_test_fixture':fixture,'result':counter,'security_accepted':False}))
 products=[];total=0
 for p in sorted(BUILD_AREA.glob('*.deb')):
  control=run(['/usr/bin/dpkg-deb','-f',str(p),'Package','Version','Architecture','Source']);meta=V.package_identity(control,read(DOWNLOAD/('glibc_'+OLD+'.dsc'),131072),VERSION,p.name)
  info=digest(p);total+=info['bytes'];need(total<=2*1024**3,'aggregate_package_bound');target=OUT/p.name;copy_payload(p,target,info);products.append({'name':p.name,**digest(target),'control':meta})
 required={name+'_'+VERSION+'_'+('all'if name=='locales'else'amd64')+'.deb'for name in RUNTIME_PACKAGES}
 need(required<={v['name']for v in products},'runtime_packages_missing')
 candidate=ROOT/'candidate-payload';candidate.mkdir(mode=0o755)
 for name in sorted(required):run(['/usr/bin/dpkg-deb','-x',str(OUT/name),str(candidate)])
 def layout(root):
  result={}
  for p in root.rglob('*'):
   st=p.lstat();result[str(p.relative_to(root))]={'type':stat.S_IFMT(st.st_mode),'mode':stat.S_IMODE(st.st_mode),'uid':st.st_uid,'gid':st.st_gid,'link':os.readlink(p)if p.is_symlink()else None}
  return result
 need(layout(base)==layout(candidate),'runtime_payload_structure_changed')
 ABI={}
 for old in sorted(base.rglob('*')):
  if not old.is_file()or old.is_symlink():continue
  with open(old,'rb')as f:elf=f.read(4)==b'\x7fELF'
  if not elf:continue
  new=candidate/old.relative_to(base);need(new.is_file()and not new.is_symlink(),'ABI_path_missing')
  headers=[run(['/usr/bin/readelf','--file-header','--program-headers','--dynamic','--wide',str(p)])for p in(old,new)]
  def properties(raw):
   text=raw.decode();return {'class':re.findall(r'Class:\s+(\S+)',text),'machine':re.findall(r'Machine:\s+(.+)',text),'interpreter':re.findall(r'\[Requesting program interpreter: ([^\]]+)\]',text),'SONAME':re.findall(r'\(SONAME\).*\[(.+)\]',text),'NEEDED':re.findall(r'\(NEEDED\).*\[(.+)\]',text)}
  need(properties(headers[0])==properties(headers[1]),'ABI_loader_contract_changed')
  symbols=[run(['/usr/bin/readelf','--dyn-syms','--wide',str(p)])for p in(old,new)]
  ABI[str(old.relative_to(base))]={'old':digest(old),'new':digest(new),'ELF_loader_properties':properties(headers[1]),'exports':V.ABI_equal(*symbols,allow_empty=not properties(headers[0])['SONAME'] and not properties(headers[0])['NEEDED'])}
 need(len(ABI)>10,'ABI_inventory_incomplete');save(OUT/'abi-contract.json',enc({'schema':'glibc84243_selected_family_ELF_ABI_and_layout/1','packages':RUNTIME_PACKAGES,'ELFs':ABI,'complete_selected_file_symlink_layout_equal':True,'static_archive_member_ABI_separately_qualified':False,'old_binary_byte_identity_required':False,'all_new_payloads_recorded':True,'security_accepted':False}))
 for p in BUILD_AREA.glob('*.changes'):
  if VERSION in p.name:save(OUT/p.name,read(p,4*1024**2))
 for p in BUILD_AREA.glob('*.buildinfo'):
  if VERSION in p.name:save(OUT/p.name,read(p,16*1024**2))
 save(OUT/'provenance.json',enc({'schema':'glibc84243_unsigned_local_package_candidate/1','profile':PROFILE_NAME,'target_distribution':TARGET_DIST,'version':VERSION,'products':products,'runtime_package_family':RUNTIME_PACKAGES,'source':source_proof,'vendor_suite_required':True,'new_vendor_test_actual_PASS':True,'same_test_old_counterfactual_required':True,'all_selected_family_ELF_ABI_and_layout_checked':True,'static_archive_member_ABI_separately_qualified':False,'dependency_preparation':prep,'old_family_source_preserved':True,'official_version_fix_claimed':False,'ordinary_whole_image_scan_runtime_restore_and_rollout_still_required':True,'Host_Ubuntu_packages_eligible':False,'security_accepted':False}))
 print(json.dumps({'status':'PACKAGES_BUILT_NOT_SECURITY_ACCEPTED','profile':PROFILE_NAME,'products':len(products),'security_accepted':False},sort_keys=True))

def main(argv=None):
 p=argparse.ArgumentParser();g=p.add_mutually_exclusive_group();g.add_argument('--prepare',action='store_true');g.add_argument('--build',action='store_true');a=p.parse_args(argv)
 if not a.prepare and not a.build:print('{"status":"UNARMED","build":false}');return 0
 try:prepare()if a.prepare else build();return 0
 except BaseException as e:
  code=str(e)if type(e)is RuntimeError and re.fullmatch('[A-Za-z0-9_]{1,80}',str(e))else'refused_or_unknown'
  print(json.dumps({'status':'FAILED','code':code,'secondary_codes':getattr(e,'command_cleanup_codes',[]),'retry_allowed':False,'security_accepted':False},sort_keys=True));return 1
if __name__=='__main__':raise SystemExit(main())
