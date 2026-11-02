"""Build/export the ordinary main App image on disposable hosted CI only.

This builder grants no production activation or security acceptance authority.
It uses the ordinary foreign input-signature format without loading deploy.sh.
"""
import argparse
from contextlib import ExitStack
from hashlib import sha1,sha256
import gzip
import io
import errno
import json
import os
from pathlib import Path,PurePosixPath
import platform
import re
import resource
import signal
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
from types import ModuleType
import zipfile

INPUTS=('Dockerfile','.dockerignore','requirements.txt','deploy/production/pip-bootstrap-requirements.txt','pip_packages','api','bot','core','src','migrations',
    'models','templates','fonts','alembic.ini','main.py','manage.py','run_bot.py','schemas.py','seed_fake_data.py',
    'trading_settings.json','scripts','mini_app_dist')
GENERATED={'pip_packages','mini_app_dist'}
MAX_FILE=64*1024**2
MAX_TOTAL=512*1024**2
MAX_COMMAND=16*1024**2
MAX_ARCHIVE=2*1024**3
MAX_REPORT=32*1024**2
DOCKER=['/usr/bin/docker','--host=unix:///var/run/docker.sock']
SUBPHASE='entry'
COMMAND_RETURN_CODE=None
CANONICAL3541=False
APP_PROFILE='web'
APP_PROFILES={'web':('4df2fed29859c0b82367666521f1a345f269f58a','499e157c3d1d1d2d35f17ff83829477e34849f17'),
    'native':('ba1f7616ade57558f51bb3a7fbb903243a33aab4','5d2ff35831696a3886372e4388f2a769a3594a8a')}
BUSINESS_COMMIT='4df2fed29859c0b82367666521f1a345f269f58a'
BUSINESS_TREE='499e157c3d1d1d2d35f17ff83829477e34849f17'
APP_RECIPE='deploy/security/Dockerfile.app-sqlite3541'
APP_RECIPE_PIN=(6342,'2e75562809856f24b1112bac1aaf56c62098f392353982037c098740ac03b74f')
SECURITY_UPDATES=('scripts/prepare_sqlite_wal_os_cache.py','scripts/verify_sqlite_runtime.py',
    'scripts/sqlite3541_package.py','scripts/sqlite3541_packages.lock.json')
NATIVE_SECURITY_UPDATES=(
    'requirements.txt','deploy/production/pip-bootstrap-requirements.txt',
    'api/deps.py','api/routers/auth.py','api/routers/chat.py','api/routers/commodities.py','api/routers/realtime.py','api/routers/sessions.py',
    'core/security.py','core/jwt_codec.py','core/frontend_static.py','main.py','core/services/chat_upload_session_service.py',
    'frontend/package.json','frontend/package-lock.json','frontend/scripts/check-node-security-baseline.mjs',
    'frontend/src/composables/useWebSocket.ts',
    'scripts/prepare_psycopg2_source.py','scripts/prepare_app_security_os_cache.py','scripts/app_security_os_packages.lock.json',
    'scripts/compose_security_os_candidate.py','scripts/sqlite_wal_package.lock.json',
    'scripts/prepare_tar_acl_os_cache.py','scripts/tar_acl_package.lock.json',
    'scripts/prepare_coreutils_acl_os_cache.py','scripts/coreutils_acl_package.lock.json',
    'scripts/verify_image_trust.py','scripts/probe_python_micro_release.py','scripts/verify_psycopg2_runtime.py','scripts/remove_runtime_pip.py')
GLIBC_ROOT='deploy/maintenance/glibc_backport_two_profiles/Root-TrixieSelected01-qualification.json'
GLIBC_ROOT_PIN={'bytes':23392,'sha256':'7cacf5aaf2c03759343c4a355fe9e8f0e414412fae22ccce0d17eb22c7a2073d'}
GLIBC_HELPERS={'scripts/prepare_glibc_security_os_cache.py':{'bytes':29419,'sha256':'ec8cfbf83dc1edb96fb4e6604d3e86fdbf2c7b7a0bec648550c897651a8d79f3'},
    'scripts/glibc_image_io.py':{'bytes':40590,'sha256':'e5a1587af0db34acfb5ab04434754cf75b9ad0aa43c0550f7ec7fedf5dd8a65f'}}
GLIBC_DEBS={'libc6_2.41-12+deb13u4+gold1_amd64.deb':{'bytes':2850128,'sha256':'0fc62e67eade0c3759cd07b01098a4aba0cd2395bca0777a405d12dc5fa04687'},
    'libc-bin_2.41-12+deb13u4+gold1_amd64.deb':{'bytes':638572,'sha256':'15dabd6c7449929462845a58df6f07929cd31d64cad613a40ca7c4cd317e3da0'}}
GLIBC_MANIFEST_PIN={'bytes':1245,'sha256':'d2b8bf088159575896f7924cda1f33036c4a257b640522055ae8adb1121b88aa'}
GLIBC_FILES={'manifest.json':GLIBC_MANIFEST_PIN,**{Path(k).name:v for k,v in GLIBC_HELPERS.items()},**GLIBC_DEBS}
GLIBC_ARTIFACT={'id':13077600231,'bytes':35878836,'sha256':'bb5dc6aef44629d0f72cdf095832c4e7ec523eea06902c57121c983f4fb06f22'}
APP32_INPUTS='scripts/app32_dependency_inputs.py'
APP32_LOCK='deploy/security/app32-dependencies.lock.json'
SMOKE_BASE='deploy/maintenance/app_r2_observe02/'
SMOKE_DRIVER=SMOKE_BASE+'run_app_image_ci_smoke.py'
SMOKE_ADAPTER=SMOKE_BASE+'producer02_controller.py'
SMOKE_PIN=(68163,'b2c958060d176c0098b86a44d0dc225d4494023120b3253239bef9e2f3f2acb2')
SMOKE_ADAPTER_PIN=(57553,'9de2b86dedbb773351b042bcd3177ebb41a88272cfd76e13f24de5ece6d49a25')
SMOKE_LEAVES=('compose_security_os_candidate.py','compose_app_mountpoint_image.py','compose_market_security_os_candidate.py',
    'admit_security_component_image_reuse.py','datastore_candidate_contract.py','runtime_image_archive_transport.py',
    'test_security_form_parser.py','test_stage6_otp_delivery_redis.py','test_telegram_otp_ephemeral_queue_redis.py')
SMOKE_SOURCES={name:('tests/' if name.startswith('test_') else SMOKE_BASE)+name for name in SMOKE_LEAVES}
SECURITY_RUNTIME='scripts/collect_normal_market_runtime.py'
SECURITY_NORMALIZER='scripts/normalize_normal_market_security_evidence.py'
SECURITY_ASSESSOR='scripts/assess_image_security_evidence.py'
SECURITY_COMPONENT='scripts/verify_image_component_presence.py'
SECURITY_ACL='scripts/verify_acl_consumer_presence.py'
SECURITY_SEED='deploy/security/normal-market-assessment/stable-source-inputs.zip'
SECURITY_REVIEW='deploy/security/normal-market-assessment/current-review.json'
SECURITY_SEED_PIN={'bytes':202287,'sha256':'0a908fe0b1a4c3042dfc1bd4378530349a27aaf6b169910df0e10f74d30d68a6'}
SECURITY_CONTROLLERS=(SECURITY_RUNTIME,SECURITY_NORMALIZER,SECURITY_ASSESSOR,SECURITY_COMPONENT,SECURITY_ACL,SECURITY_SEED,SECURITY_REVIEW)
CANONICAL_EXTRA=(APP_RECIPE,GLIBC_ROOT,APP32_LOCK,SMOKE_DRIVER,SMOKE_ADAPTER,*SMOKE_SOURCES.values(),*SECURITY_CONTROLLERS)
CODES=frozenset(('hosted_only','root_scope','path','metadata','source_changed','source_inventory','git_identity',
    'git_dirty','command_failed','command_timeout','command_output_bound','cleanup_unproven','input_bound',
    'input_inventory','input_changed','source_fd_budget','state','image_identity','image_config','image_labels','archive_bound',
    'archive_shape','archive_config','report_identity','report_shape','raw_findings','output_exists','io_refusal','refused'))
CODES=CODES|frozenset(('artifact_download_failed','artifact_size','artifact_digest','download_exists',
    'vendor_release_source','vendor_release_token','vendor_release_metadata','vendor_asset_metadata','vendor_asset_pin','app_profile',
    'app32_inventory','app32_transport_pin','app32_transport_inventory','app32_transport_descriptor',
    'app32_dependency_token','app32_release_metadata','app32_offline_transport','app32_transport_deadline'))

def need(ok,code):
    if not ok:raise RuntimeError(code)

def select_app_profile(value):
    global APP_PROFILE,BUSINESS_COMMIT,BUSINESS_TREE
    need(type(value)is str and value in APP_PROFILES,'app_profile')
    APP_PROFILE=value;BUSINESS_COMMIT,BUSINESS_TREE=APP_PROFILES[value]

def security_updates():
    return SECURITY_UPDATES+(NATIVE_SECURITY_UPDATES if APP_PROFILE=='native' else ())

def encode(value):return (json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode()

def document(raw):
    def unique(rows):
        out={}
        for key,value in rows:need(key not in out,'state');out[key]=value
        return out
    return json.loads(raw,object_pairs_hook=unique,parse_constant=lambda _:need(False,'state'))

def identity(s):return [s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns]

def canonical(path):
    path=Path(path).absolute();need(path.resolve(strict=True)==path,'path');return path

def read_file(path,maximum):
    path=canonical(path);fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC)
    try:
        before=os.fstat(fd);need(stat.S_ISREG(before.st_mode) and before.st_uid==os.geteuid()
            and before.st_nlink==1 and not before.st_mode&0o022 and 0<=before.st_size<=maximum,'metadata')
        raw=os.pread(fd,before.st_size+1,0)
        need(len(raw)==before.st_size and identity(before)==identity(os.fstat(fd))==identity(path.lstat()),'source_changed')
        return raw,identity(before)
    finally:os.close(fd)

def write_new(path,value):
    raw=value if type(value) is bytes else encode(value)
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
    try:
        view=memoryview(raw)
        while view:
            count=os.write(fd,view);need(count>0,'io_refusal');view=view[count:]
        os.fchmod(fd,0o600);os.fsync(fd)
    finally:os.close(fd)

def env(root):return {'PATH':'/usr/local/bin:/usr/bin:/bin','DOCKER_CONFIG':str(root/'docker-config'),'DOCKER_BUILDKIT':'1','PYTHONDONTWRITEBYTECODE':'1'}

def command(argv,*,maximum=MAX_COMMAND,seconds=120,environment=None,target=None,combine_stderr=False):
    """Finite regular output before spawn; no PIPE accumulation and no Go AS cap."""
    global COMMAND_RETURN_CODE
    COMMAND_RETURN_CODE=None
    private=os.memfd_create('canonical-app-command',os.MFD_CLOEXEC) if target is None else target
    child=None;primary=False
    def limits():
        os.umask(0o077);resource.setrlimit(resource.RLIMIT_CORE,(0,0))
        resource.setrlimit(resource.RLIMIT_FSIZE,(maximum+1,maximum+1))
        resource.setrlimit(resource.RLIMIT_CPU,(max(1,int(seconds)),)*2)
    try:
        child=subprocess.Popen(argv,stdin=subprocess.DEVNULL,stdout=private,stderr=subprocess.STDOUT if combine_stderr else subprocess.DEVNULL,
            env=environment,start_new_session=True,close_fds=True,preexec_fn=limits)
        end=time.monotonic()+seconds
        while child.poll() is None:
            need(os.fstat(private).st_size<=maximum,'command_output_bound')
            need(time.monotonic()<end,'command_timeout');time.sleep(.02)
        need(os.fstat(private).st_size<=maximum,'command_output_bound')
        COMMAND_RETURN_CODE=child.wait(timeout=1);need(COMMAND_RETURN_CODE==0,'command_failed')
        if target is None:
            size=os.fstat(private).st_size;raw=os.pread(private,size+1,0);need(len(raw)==size,'command_output_bound');return raw
    except BaseException:primary=True;raise
    finally:
        clean=True
        if child is not None and child.returncode is None:
            try:
                # WNOWAIT verifies that this PID remains our unreaped child.
                # Never poll/reap it before a signal; a known returncode means
                # the identity is already released and must not be signalled.
                os.waitid(os.P_PID,child.pid,os.WEXITED|os.WNOHANG|os.WNOWAIT)
                try:os.killpg(child.pid,signal.SIGKILL)
                except ProcessLookupError:pass
                child.wait(timeout=3)
            except BaseException:clean=False
        if target is None:
            try:os.close(private)
            except OSError:clean=False
        if not clean and not primary:raise RuntimeError('cleanup_unproven')

def git(repo,*args,maximum=MAX_COMMAND):
    return command(['/usr/bin/git','--no-optional-locks','-C',str(repo),*args],maximum=maximum,seconds=30)

def hosted(root,repo=None):
    need(os.geteuid()==1001 and os.environ.get('RUNNER_ENVIRONMENT')=='github-hosted'
        and os.environ.get('GITHUB_REPOSITORY')=='mohsenbarari/trading_bot'
        and os.environ.get('GITHUB_REF')=='refs/heads/main' and platform.machine()=='x86_64','hosted_only')
    root=canonical(root);temporary=canonical(os.environ['RUNNER_TEMP']);s=root.lstat();parent=temporary.lstat()
    need(root.is_relative_to(temporary) and root!=temporary and s.st_uid==1001 and stat.S_IMODE(s.st_mode)==0o700,'root_scope')
    need(parent.st_uid==1001 and stat.S_ISDIR(parent.st_mode) and not parent.st_mode&0o022,'root_scope')
    if repo is not None:
        repo=canonical(repo);need(repo==canonical(os.environ['GITHUB_WORKSPACE']) and not root.is_relative_to(repo),'root_scope')
        need(repo.lstat().st_uid==1001 and not repo.lstat().st_mode&0o022,'root_scope')
    return root

def git_identity(repo,expected=None):
    commit=git(repo,'rev-parse','HEAD',maximum=128).decode().strip()
    tree=git(repo,'rev-parse','HEAD^{tree}',maximum=128).decode().strip()
    need(re.fullmatch('[a-f0-9]{40}',commit) and re.fullmatch('[a-f0-9]{40}',tree)
        and commit==os.environ.get('GITHUB_SHA') and (expected is None or (commit,tree)==expected),'git_identity')
    need(not git(repo,'status','--porcelain','--untracked-files=all'),'git_dirty')
    return commit,tree

def selected(name):return name in INPUTS or name.split('/')[0] in {*INPUTS,'frontend'} or (CANONICAL3541 and name in CANONICAL_EXTRA)

def source_manifest(repo,commit):
    raw=git(repo,'ls-tree','-rz','--full-tree','--long',commit);rows=[];total=0
    for record in raw.split(b'\0'):
        if not record:continue
        head,name=record.split(b'\t',1);name=name.decode('utf-8');mode,kind,oid,size=head.split()
        if not selected(name):continue
        need(kind==b'blob' and mode in {b'100644',b'100755'} and not PurePosixPath(name).is_absolute()
            and '..' not in PurePosixPath(name).parts and '\n' not in name and '\\' not in name,'source_inventory')
        content,metadata=read_file(repo/name,MAX_FILE);total+=len(content);need(total<=MAX_TOTAL and len(rows)<20000,'input_bound')
        need(len(content)==int(size) and sha1(b'blob '+str(len(content)).encode()+b'\0'+content).hexdigest()==oid.decode(),'source_changed')
        rows.append({'path':name,'git_mode':mode.decode(),'git_blob':oid.decode(),'bytes':len(content),
            'sha256':sha256(content).hexdigest(),'identity':metadata})
    need(rows and any(r['path']=='Dockerfile' for r in rows),'source_inventory')
    return sorted(rows,key=lambda r:r['path'])

def source_module(raw,path):
    module=ModuleType('_app3541_'+Path(path).stem);module.__file__=str(path)
    exec(compile(raw,str(path),'exec'),module.__dict__);return module

def write_context_file(root,name,raw,mode=0o644):
    path=root/name;path.parent.mkdir(mode=0o755,parents=True,exist_ok=True)
    # The workflow's private umask must not become a copied business-dir mode.
    parent=path.parent
    while parent!=root:
        need(parent.is_relative_to(root),'path');parent.chmod(0o755);parent=parent.parent
    write_new(path,raw);path.chmod(mode)
    return path

def frozen_context(root,release):
    """One closed Git business base plus the profile's enumerated security files.

    Neither Main product paths nor a moving business ref can enter the context.
    The ordinary Dockerfile input signature still covers the resulting bytes.
    """
    need(git(release,'rev-parse',BUSINESS_COMMIT+'^{tree}',maximum=128).decode().strip()==BUSINESS_TREE,'git_identity')
    expected={};total=0
    for record in git(release,'ls-tree','-rz','--full-tree','--long',BUSINESS_COMMIT).split(b'\0'):
        if not record:continue
        head,name=record.split(b'\t',1);name=name.decode();mode,kind,oid,size=head.split()
        if not (name in INPUTS or name.split('/')[0] in {*INPUTS,'frontend'}):continue
        need(kind==b'blob' and mode in {b'100644',b'100755'} and not PurePosixPath(name).is_absolute()
            and not {'..','.git'}&set(PurePosixPath(name).parts) and '\n' not in name and '\\' not in name,'source_inventory')
        total+=int(size);need(int(size)<=MAX_FILE and total<=MAX_TOTAL and len(expected)<20000,'input_bound')
        expected[name]=(mode.decode(),oid.decode(),int(size))
    need(expected and 'Dockerfile' in expected and 'frontend/package-lock.json' in expected,'source_inventory')
    roots=sorted(entry for entry in (*INPUTS,'frontend') if any(name==entry or name.startswith(entry+'/') for name in expected))
    archive=git(release,'archive','--format=tar',BUSINESS_COMMIT,'--',*roots,maximum=MAX_TOTAL)
    context=root/'app-context';context.mkdir(mode=0o755);context.chmod(0o755);seen=set();origins={}
    with tarfile.open(fileobj=io.BytesIO(archive),mode='r:') as incoming:
        for member in incoming:
            if member.isdir():continue
            name=member.name;need(member.isfile() and name in expected and name not in seen,'source_inventory');seen.add(name)
            mode,oid,size=expected[name];need(member.size==size,'source_inventory')
            stream=incoming.extractfile(member)
            with stream:raw=stream.read(size+1)
            need(len(raw)==size and sha1(b'blob '+str(size).encode()+b'\0'+raw).hexdigest()==oid,'source_changed')
            if name=='Dockerfile' or name in security_updates():continue
            write_context_file(context,name,raw,0o755 if mode=='100755' else 0o644)
            origins[name]={'commit':BUSINESS_COMMIT,'source_path':name,'git_blob':oid,'git_mode':mode}
    need(seen==set(expected),'source_inventory')
    for name in (*security_updates(),APP_RECIPE):
        target='Dockerfile' if name==APP_RECIPE else name
        raw,_=read_file(release/name,MAX_FILE)
        oid=git(release,'rev-parse',os.environ['GITHUB_SHA']+':'+name,maximum=128).decode().strip()
        need(sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()==oid,'source_changed')
        if name==APP_RECIPE:need((len(raw),sha256(raw).hexdigest())==APP_RECIPE_PIN,'source_changed')
        write_context_file(context,target,raw)
        origins[target]={'commit':os.environ['GITHUB_SHA'],'source_path':name,'git_blob':oid,'git_mode':'100644'}
    rows=[]
    for name,origin in sorted(origins.items()):
        raw,metadata=read_file(context/name,MAX_FILE)
        rows.append({'path':name,**origin,'bytes':len(raw),'sha256':sha256(raw).hexdigest(),'identity':metadata})
    return context,rows

def canonical_glibc(root,release,context,*,check_only=False):
    """Reuse the admitted two-package cache and its exact named-context format."""
    named={'schema':'glibc84243_exact_runtime_named_context/1','files':GLIBC_FILES,'security_accepted':False}
    if not check_only:
        sources=root/'glibc-source';sources.mkdir(mode=0o700)
        for name,pin in GLIBC_HELPERS.items():
            raw,_=read_file(release/name,131072);need({'bytes':len(raw),'sha256':sha256(raw).hexdigest()}==pin,'source_changed')
            write_context_file(sources,Path(name).name,raw)
        qualified=root/'glibc-qualified-inputs';qualified.mkdir(mode=0o700)
        raw,_=read_file(release/GLIBC_ROOT,65536);need({'bytes':len(raw),'sha256':sha256(raw).hexdigest()}==GLIBC_ROOT_PIN,'source_changed')
        write_context_file(qualified,'Root-qualification.json',raw)
        # Existing authenticated fetcher; retain the original ZIP, not a rebuild.
        raw,_=read_file(context/'scripts/compose_security_os_candidate.py',131072)
        need(sha256(raw).hexdigest()=='a7ba6515937e7ae8193be4d550547651fe5b313399ec3a1f7634efe513e151c1','source_changed')
        fetcher=source_module(raw,context/'scripts/compose_security_os_candidate.py');artifact=root/'glibc-candidate.zip'
        fetcher.fetch_artifact(GLIBC_ARTIFACT['id'],artifact,GLIBC_ARTIFACT['bytes'],GLIBC_ARTIFACT['sha256'])
        need(file_digest(artifact,64*1024**2)=={k:GLIBC_ARTIFACT[k]for k in('bytes','sha256')},'input_changed')
        with zipfile.ZipFile(artifact) as incoming:
            names=incoming.infolist();need(len(names)<=96 and len({r.filename for r in names})==len(names),'input_inventory')
            for name,pin in GLIBC_DEBS.items():
                item=incoming.getinfo(name);need(not item.is_dir() and item.file_size==pin['bytes']
                    and stat.S_IFMT(item.external_attr>>16) in (0,stat.S_IFREG),'input_inventory')
                with incoming.open(item) as stream:raw=stream.read(pin['bytes']+1)
                need({'bytes':len(raw),'sha256':sha256(raw).hexdigest()}==pin,'input_changed');write_context_file(qualified,name,raw)
    child={**env(root),**{key:os.environ[key]for key in('GITHUB_ACTIONS','RUNNER_ENVIRONMENT','GITHUB_REPOSITORY','GITHUB_REF','RUNNER_TEMP')}}
    command([sys.executable,'-I','-S','-B',str(root/'glibc-source/prepare_glibc_security_os_cache.py'),
        '--cache-dir',str(root/'glibc-capsule'),'--package-dir',str(root/'glibc-qualified-inputs')]+(['--check']if check_only else[]),
        environment=child,seconds=600)
    need({p.name for p in (root/'glibc-capsule').iterdir()}==set(GLIBC_FILES),'input_inventory')
    for name,pin in GLIBC_FILES.items():need(file_digest(root/'glibc-capsule'/name,32*1024**2)==pin,'input_changed')
    return named

def canonical_vendor_originals(root,context,release):
    """Use the existing retained release contract; original CI ZIPs have expired."""
    global SUBPHASE
    name='scripts/prepare_ci_security_caches.py';raw,_=read_file(release/name,131072)
    policy=source_module(raw,release/name)
    need(policy.REPO=='mohsenbarari/trading_bot'and policy.RELEASE_ID==430404133
        and policy.RELEASE_TAG=='security-app-os-cache-20261101'
        and policy.RELEASE_TARGET=='48c9706b10d7db802e0a7552e26aea6ab4921299'
        and {key:policy.ASSET_IDS[key]for key in('util','ncurses','acl-tar')}==
        {'util':697537730,'ncurses':697537733,'acl-tar':697537734},'vendor_release_source')
    need(bool(os.environ.get('GH_TOKEN')),'vendor_release_token')
    def api(endpoint,maximum,accept='application/vnd.github+json'):
        return command(['/usr/bin/gh','api','--method','GET','-H','Accept: '+accept,
            'repos/'+policy.REPO+'/'+endpoint],environment={**env(root),'GH_TOKEN':os.environ['GH_TOKEN']},
            maximum=maximum,seconds=120)
    lock=document(read_file(context/'scripts/app_security_os_packages.lock.json',65536)[0])
    SUBPHASE='vendor_release_metadata';metadata=document(api('releases/'+str(policy.RELEASE_ID),65536))
    need(type(metadata)is dict and type(metadata.get('id'))is int and metadata.get('id')==policy.RELEASE_ID and metadata.get('tag_name')==policy.RELEASE_TAG
        and metadata.get('target_commitish')==policy.RELEASE_TARGET and metadata.get('draft')is False
        and metadata.get('prerelease')is True and metadata.get('html_url')=='https://github.com/'+policy.REPO+'/releases/tag/'+policy.RELEASE_TAG,
        'vendor_release_metadata')
    assets=metadata.get('assets');need(type(assets)is list and len(assets)==len(policy.ASSET_IDS)and all(type(row.get('id'))is int for row in assets)
        and {row.get('id')for row in assets}==set(policy.ASSET_IDS.values())
        and len({row.get('name')for row in assets})==len(assets),'vendor_asset_metadata')
    originals=root/'security-originals';originals.mkdir(mode=0o700);arguments=[]
    for group in ('util','ncurses','acl-tar'):
        spec=lock['artifacts'][group];path=originals/(group+'.zip');identifier=policy.ASSET_IDS[group]
        asset=next(row for row in assets if row['id']==identifier)
        need(type(asset.get('id'))is int and type(asset.get('size'))is int and asset.get('name')=='app-os-'+group+'.zip'
            and asset.get('size')==spec['bytes']and asset.get('digest')=='sha256:'+spec['sha256']
            and asset.get('state')=='uploaded'and asset.get('url')=='https://api.github.com/repos/'+policy.REPO+'/releases/assets/'+str(identifier),
            'vendor_asset_metadata')
        SUBPHASE='vendor_release_download_'+group
        raw=api('releases/assets/'+str(identifier),spec['bytes'],'application/octet-stream')
        need(len(raw)==spec['bytes']and sha256(raw).hexdigest()==spec['sha256'],'vendor_asset_pin')
        write_new(path,raw)
        arguments+=['--'+group+'-zip',str(path)]
    SUBPHASE='vendor_cache_prepare'
    return arguments

def app32_sources(proofroot,release):
    old_raw,_=read_file(release/SMOKE_DRIVER,131072);adapter_raw,_=read_file(release/SMOKE_ADAPTER,131072)
    need((len(old_raw),sha256(old_raw).hexdigest())==SMOKE_PIN
        and (len(adapter_raw),sha256(adapter_raw).hexdigest())==SMOKE_ADAPTER_PIN,'source_changed')
    old=source_module(old_raw,release/SMOKE_DRIVER);adapter=source_module(adapter_raw,release/SMOKE_ADAPTER)
    for name,path in SMOKE_SOURCES.items():
        raw,_=read_file(release/path,131072)
        expected=old.SELECTORS[name][2] if name in old.SELECTORS else old.HELPERS[name][1]
        need(sha256(raw).hexdigest()==expected,'source_changed');write_new(proofroot/'source'/name,raw)
    inputs=source_module(read_file(release/APP32_INPUTS,131072)[0],release/APP32_INPUTS)
    return old,adapter,inputs


def canonical_app32_prepare(root,release,environment):
    """Admit every fixed smoke input before vendor/npm/Docker work starts."""
    proofroot=Path(tempfile.mkdtemp(prefix='security-app-ci-smoke-',dir=canonical(os.environ['RUNNER_TEMP'])))
    for name in ('source','reports','docker-config'):(proofroot/name).mkdir(mode=0o700)
    with ExitStack()as stack:
        old,_,inputs=app32_sources(proofroot,release)
        ctx=old.Controller(proofroot,stack)
        lock=inputs.inventory(read_file(release/APP32_LOCK,65536)[0],old,ctx)
        need(bool(environment.get('GH_TOKEN')),'app32_dependency_token')
        inputs.prepare(root,old,ctx,lock,command=command,environment=environment,write=write_new,digest=file_digest)


def canonical_external_rows(root):
    pins={**{'glibc-capsule/'+name:pin for name,pin in GLIBC_FILES.items()},
        **{'glibc-source/'+Path(name).name:pin for name,pin in GLIBC_HELPERS.items()},
        'glibc-qualified-inputs/Root-qualification.json':GLIBC_ROOT_PIN,
        'glibc-candidate.zip':{k:GLIBC_ARTIFACT[k]for k in('bytes','sha256')}}
    lock=document(read_file(root/'app-context/scripts/app_security_os_packages.lock.json',65536)[0])
    pins.update({'security-originals/'+group+'.zip':{key:spec[key]for key in('bytes','sha256')}
        for group,spec in lock['artifacts'].items()})
    lock=document(read_file(root/'app32-dependencies/inventory.json',65536)[0])
    for fixture in lock['fixtures'].values():
        for kind in ('image','evidence'):
            asset=fixture[kind];pins['app32-dependencies/'+asset['name']]={key:asset[key]for key in('bytes','sha256')}
    rows=[]
    for name,pin in sorted(pins.items()):
        path=root/name;metadata=identity(path.lstat())
        actual=file_digest(path,512*1024**2 if name.startswith('app32-dependencies/')else 64*1024**2)
        need(actual==pin and metadata==identity(path.lstat()),'input_changed')
        rows.append({'path':name,**pin,'identity':metadata})
    return rows

def canonical_smoke(root):
    """Run the existing 8 installed-runtime + 24 component assertions unchanged."""
    global SUBPHASE
    repo,source=state(root);release=canonical(source['repo'])
    record=document(read_file(root/'image-build.json',MAX_COMMAND)[0]);image=record['image_id']
    proofroot=Path(tempfile.mkdtemp(prefix='security-app-ci-smoke-',dir=canonical(os.environ['RUNNER_TEMP'])))
    for name in ('source','reports','docker-config'):(proofroot/name).mkdir(mode=0o700)
    write_new(root/'app32-root.json',{'root':str(proofroot),'image_id':image,'security_accepted':False})
    emit('app32_root',str(proofroot))
    with ExitStack() as stack:
        held=HeldSource(stack,repo,source['files']);external=HeldSource(stack,root,canonical_external_rows(root))
        unchanged(root,repo,source,held);inspect(root,image,source,record['input_signature'])
        old,adapter,inputs=app32_sources(proofroot,release)
        ctx=None;primary=None;cleanup_errors=[]
        try:
            SUBPHASE='actual_unchanged_app32';ctx=old.Controller(proofroot,stack)
            lock=inputs.inventory(read_file(release/APP32_LOCK,65536)[0],old,ctx)
            need(document(read_file(root/'app32-dependencies/inventory.json',65536)[0])==lock,'app32_inventory')
            inputs.bind(ctx,old,root,lock,stack)
            ctx.checks.extend((lambda:unchanged(root,repo,source,held),external.check,
                lambda:inspect(root,image,source,record['input_signature'])))
            result=adapter.run_smoke(ctx,old,image,{'image_id':image})
            SUBPHASE='actual_copied_source_parity'
            inputs=document(read_file(root/'input-manifest.json',MAX_COMMAND)[0])
            directories={'api','bot','core','src','migrations','models','templates','fonts','scripts','mini_app_dist'}
            leaves={'requirements.txt','alembic.ini','main.py','manage.py','run_bot.py','schemas.py','seed_fake_data.py','trading_settings.json'}
            vector=[{'path':'/app/'+row['path'],'bytes':row['bytes'],'sha256':row['sha256']}for row in inputs['files']
                if row['path'] in leaves or row['path'].split('/')[0] in directories]
            need(vector and any(row['path']=='/app/mini_app_dist/index.html'for row in vector),'input_inventory')
            vector_raw=encode(vector);need(len(vector_raw)<=2*1024**2,'input_bound')
            ctx.C.write(proofroot/'proof/source-inputs.json',vector_raw,0o444)
            capsule='''import hashlib,json,os,stat
raw=open('/proof/source-inputs.json','rb').read(2097153)
assert len(raw)<=2097152
rows=json.loads(raw);assert type(rows)is list and 0<len(rows)<=20000
total=0
for row in rows:
 path=row['path'];assert type(path)is str and path.startswith('/app/')and '..'not in path.split('/')
 fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC)
 try:
  before=os.fstat(fd);assert stat.S_ISREG(before.st_mode)and before.st_size==row['bytes']and before.st_size<=67108864
  total+=before.st_size;assert total<=536870912
  digest=hashlib.sha256();count=0
  while True:
   block=os.read(fd,65536)
   if not block:break
   count+=len(block);assert count<=before.st_size;digest.update(block)
  after=os.fstat(fd);named=os.lstat(path)
  frame=lambda s:(s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
  assert count==before.st_size and digest.hexdigest()==row['sha256']and frame(before)==frame(after)==frame(named)
 finally:os.close(fd)
print(json.dumps({'status':'PASS','verified_files':len(rows),'vector_sha256':hashlib.sha256(raw).hexdigest(),'production_data_or_mounts_used':False}))
'''
            cid=old.create(ctx,'app-ci-source-'+old.owner(),image,['/usr/local/bin/python3','-I','-S','-B','-c',capsule],
                proof=proofroot/'proof',output_limit=True,tmpfs=('/tmp:rw,nosuid,nodev,noexec,size=16m,mode=1777',))
            source_result=document(ctx.docker(['start','--attach',cid],seconds=120,maximum=65536))
            need(ctx.docker(['inspect','--format','{{.State.ExitCode}}',cid],maximum=64).strip()==b'0'
                and source_result=={'status':'PASS','verified_files':len(vector),'vector_sha256':sha256(vector_raw).hexdigest(),
                    'production_data_or_mounts_used':False},'source_changed')
            write_new(root/'reports/source-parity.json',{'schema':'canonical_app_actual_copied_source_parity/1','image_id':image,
                'app_profile':APP_PROFILE,'business_commit':BUSINESS_COMMIT,'business_tree':BUSINESS_TREE,
                'input_signature':record['input_signature'],'security_overrides':list(security_updates()),'actual':source_result,'security_accepted':False})
            SUBPHASE='actual_canonical_sqlite_probe'
            cid=old.create(ctx,'app-ci-sqlite-'+old.owner(),image,
                ['/usr/local/bin/python3','-I','-B','/app/scripts/verify_sqlite_runtime.py','--require-libmagic'],
                tmpfs=('/tmp:rw,nosuid,nodev,noexec,size=64m,mode=1777',),output_limit=True)
            runtime=document(ctx.docker(['start','--attach',cid],seconds=60,maximum=65536))
            need(ctx.docker(['inspect','--format','{{.State.ExitCode}}',cid],maximum=64).strip()==b'0'
                and runtime.get('status')=='PASS' and runtime.get('sqlite_package')=='3.54.1-0+trading1~trixie1'
                and runtime.get('sqlite_runtime')=='3.54.1' and runtime.get('canonical_release_identity_checked')is True
                and runtime.get('legacy_cast15_verified')is True,'state')
            write_new(root/'reports/sqlite-runtime.json',{'image_id':image,'actual':runtime,'security_accepted':False})
            write_new(root/'reports/app32.json',{'schema':'canonical_app_actual_unchanged32/1','image_id':image,
                'app_profile':APP_PROFILE,'business_commit':BUSINESS_COMMIT,'business_tree':BUSINESS_TREE,
                'source_parity':file_digest(root/'reports/source-parity.json',65536),
                'driver':{'bytes':SMOKE_PIN[0],'sha256':SMOKE_PIN[1]},
                'adapter':{'bytes':SMOKE_ADAPTER_PIN[0],'sha256':SMOKE_ADAPTER_PIN[1]},
                'assertions_changed':False,'actual':result,'security_accepted':False})
        except BaseException as error:primary=error;raise
        finally:
            if ctx is not None:
                cleanup_errors=old.cleanup(ctx)
                cleanup={'owned_cleanup_complete':not cleanup_errors,'closed_causes':cleanup_errors,
                    'primary_failure_retained':primary is not None,'security_accepted':False}
                ctx.write(proofroot/'reports/cleanup.json',cleanup);write_new(root/'reports/app32-cleanup.json',cleanup)
                if cleanup_errors and primary is None:need(False,'cleanup_unproven')
            external.check();unchanged(root,repo,source,held)
    return {'status':'actual_unchanged32_passed','tests':32,'image_id':image,'security_accepted':False}

def canonical_collect_runtime(root,bundle,helper_raw,guard):
    """Run the existing public observer in one owned, mount-free App container."""
    image=bundle['image_id'];name='canonical-app-security-'+os.environ['GITHUB_RUN_ID']+'-'+os.environ['GITHUB_RUN_ATTEMPT']
    label='io.gold-trade.canonical-app-security-owner';owner=os.environ['GITHUB_SHA'];primary=None;result=None
    def run(args,**kwargs):
        guard()
        try:return command([*DOCKER,*args],environment=env(root),**kwargs)
        finally:guard()
    def listed():return run(['container','ls','--all','--no-trunc','--quiet','--filter','name=^/'+name+'$'],maximum=256,seconds=10).decode().strip()
    need(listed()=='','state')
    bootstrap='import hashlib,sys;h=sys.argv.pop(1);p=sys.argv.pop(1);raw=bytes.fromhex(h);assert hashlib.sha256(raw).hexdigest()==p;exec(compile(raw,"<sealed-app-runtime>","exec"),{"__name__":"__main__"})'
    try:
        cid=run(['create','--name',name,'--label',label+'='+owner,'--network','none','--read-only','--cap-drop','ALL',
            '--security-opt','no-new-privileges','--tmpfs','/tmp:rw,noexec,nosuid,nodev,size=16m','--user','0:0',
            '--memory','512m','--memory-swap','512m','--cpus','1','--pids-limit','64','--ulimit','core=0',
            '--entrypoint','/usr/local/bin/python3',image,'-I','-S','-B','-c',bootstrap,helper_raw.hex(),sha256(helper_raw).hexdigest(),
            '--inside',encode(bundle).hex()],maximum=256,seconds=30).decode().strip()
        need(re.fullmatch('[a-f0-9]{64}',cid),'image_identity')
        raw=run(['start','--attach',cid],maximum=4*1024**2,seconds=180)
        need(run(['inspect','--format','{{.State.ExitCode}}',cid],maximum=64,seconds=10).strip()==b'0','state')
        result=document(raw)
    except BaseException as error:primary=error;raise
    finally:
        try:
            cid=listed()
            if cid:
                need(re.fullmatch('[a-f0-9]{64}',cid),'cleanup_unproven')
                template='{"id":{{json .Id}},"name":{{json .Name}},"image":{{json .Image}},"owner":{{json (index .Config.Labels "'+label+'")}}}'
                actual=document(run(['container','inspect','--format',template,cid],maximum=2048,seconds=10))
                need(actual=={'id':cid,'name':'/'+name,'image':image,'owner':owner},'cleanup_unproven')
                run(['container','rm','--force',cid],maximum=256,seconds=15)
            need(listed()=='','cleanup_unproven')
        except BaseException as cleanup_error:
            if primary is not None:raise BaseExceptionGroup('App runtime and owned cleanup',[primary,cleanup_error])from None
            raise
    need(type(result)is dict and result.get('schema')=='normal_market_same_image_runtime_observation/1'
        and result.get('status')=='OBSERVED_NOT_ASSESSED'and all(result.get(key)==bundle[key]
        for key in('image_id','source_commit','source_tree','input_signature','run_id','run_attempt','vector_sha256')),'state')
    result['owned_container_cleanup_proven']=True;return result

def canonical_assess(root):
    """Explicit App32 product assessment; retain raw findings and all originals."""
    global SUBPHASE
    repo,source=state(root);release=canonical(source['repo']);work=root/'security-assessment';work.mkdir(mode=0o700)
    inputs_dir=work/'inputs';inputs_dir.mkdir(mode=0o700);stack=ExitStack();closed=False;all_held=[]
    try:
        held=HeldSource(stack,repo,source['files']);external=HeldSource(stack,root,canonical_external_rows(root))
        all_held.extend((held,external))
        def guard():
            unchanged(root,repo,source,held);external.check()
            for reader in all_held[2:]:reader.check()
        guard();raws={name:read_file(release/name,MAX_REPORT)[0]for name in SECURITY_CONTROLLERS}
        A=source_module(raws[SECURITY_ASSESSOR],release/SECURITY_ASSESSOR)
        R=source_module(raws[SECURITY_RUNTIME],release/SECURITY_RUNTIME)
        N=source_module(raws[SECURITY_NORMALIZER],release/SECURITY_NORMALIZER)
        need({'bytes':len(raws[SECURITY_SEED]),'sha256':sha256(raws[SECURITY_SEED]).hexdigest()}==SECURITY_SEED_PIN,'source_changed')
        originals={}
        with zipfile.ZipFile(io.BytesIO(raws[SECURITY_SEED]))as archive:
            entries=archive.infolist();need(len(entries)==29 and sum(row.file_size for row in entries)<=1024**2,'input_inventory')
            for row in entries:
                need(A.NAME.fullmatch(row.filename)and row.filename not in originals and not row.is_dir()
                    and not row.flag_bits&1 and 0<row.file_size<=196608 and not stat.S_ISLNK(row.external_attr>>16),'input_inventory')
                with archive.open(row)as stream:raw=stream.read(row.file_size+1)
                need(len(raw)==row.file_size,'input_inventory');originals[row.filename]=raw
        paths={'product-build.json':root/'image-build.json','product-source.json':root/'source-identity.json',
            'product-manifest.json':root/'input-manifest.json','product-Dockerfile':repo/'Dockerfile',
            'scan.json':root/'reports/vulnerabilities.json','sbom.json':root/'reports/sbom.cdx.json',
            'image-export.json':root/'image-export.json','factual-evidence.json':root/'reports/app32.json',
            'app-cleanup.json':root/'reports/app32-cleanup.json','sqlite-lock.json':repo/'scripts/sqlite3541_packages.lock.json',
            'sqlite-canonical-package.py':repo/'scripts/sqlite3541_package.py',
            'sqlite-canonical-provenance.json':repo/'pip_packages/sqlite3541-capsule/provenance.json',
            'util.candidate.zip':root/'security-originals/util.zip','ncurses.candidate.zip':root/'security-originals/ncurses.zip',
            'acl.candidate.zip':root/'security-originals/acl-tar.zip'}
        originals.update({name:read_file(path,A.MAX_JSON)[0]for name,path in paths.items()})
        originals.update({'runtime-collector.py':raws[SECURITY_RUNTIME],'normalizer.py':raws[SECURITY_NORMALIZER],
            'component-verifier.py':raws[SECURITY_COMPONENT],'acl-verifier.py':raws[SECURITY_ACL],'current-review.json':raws[SECURITY_REVIEW]})
        sqlite=document(read_file(root/'reports/sqlite-runtime.json',65536)[0]);originals['sqlite-probe.json']=encode(sqlite['actual'])
        need(sqlite['image_id']==document(originals['product-build.json'])['image_id'],'image_identity')
        rows=[]
        for name,raw in sorted(originals.items()):
            need(A.NAME.fullmatch(name)and 0<len(raw)<=A.MAX_JSON,'input_inventory');write_new(inputs_dir/name,raw)
            rows.append({'path':name,'bytes':len(raw),'sha256':sha256(raw).hexdigest(),'identity':identity((inputs_dir/name).lstat())})
        original_reader=HeldSource(stack,inputs_dir,rows);all_held.append(original_reader)
        build=document(originals['product-build.json']);counts,_=reports(root,build['image_id'])
        write_new(root/'reports/raw-gate.json',{'image_id':build['image_id'],'raw_counts':counts,
            'raw_gate_passed':counts['HIGH']==counts['CRITICAL']==0,'scanner_findings_modified':False,'security_accepted':False})
        SUBPHASE='same_image_app_runtime';_,bundle=R.prepare_request(build,originals['runtime-vector.json'],
            originals['sqlite-lock.json'],originals['component-verifier.py'],originals['acl-verifier.py'])
        runtime=canonical_collect_runtime(root,bundle,raws[SECURITY_RUNTIME],guard)
        originals['runtime-observation.json']=encode(runtime);originals['component-original.json']=encode(runtime['component_presence'])
        SUBPHASE='same_image_app_normalization';prepared=N.prepare(A,R,originals,now=int(time.time()));proof_rows=[]
        for name,raw in sorted(prepared['inputs'].items()):
            need(A.NAME.fullmatch(name),'input_inventory')
            if name in paths or name in {row['path']for row in rows}:
                need(raw==originals[name],'input_changed');continue
            write_new(inputs_dir/name,raw)
            proof_rows.append({'path':name,'bytes':len(raw),'sha256':sha256(raw).hexdigest(),'identity':identity((inputs_dir/name).lstat())})
        all_held.append(HeldSource(stack,inputs_dir,proof_rows));write_new(work/'lock.json',prepared['lock_raw'])
        lock_rows=[{'path':'lock.json','bytes':len(prepared['lock_raw']),'sha256':sha256(prepared['lock_raw']).hexdigest(),
            'identity':identity((work/'lock.json').lstat())}];all_held.append(HeldSource(stack,work,lock_rows))
        def finalizer():
            nonlocal closed
            need(not closed,'state');guard()
            named=[(path,row['identity'])for directory,records in ((repo,source['files']),(root,canonical_external_rows(root)),
                (inputs_dir,rows+proof_rows),(work,lock_rows))for row in records for path in (directory/row['path'],)]
            stack.close();closed=True
            need(all(identity(path.lstat())==metadata for path,metadata in named),'source_changed');return True
        SUBPHASE='same_image_app_assessment'
        result=A.assess(prepared['lock_raw'],originals['scan.json'],originals['sbom.json'],prepared['inputs'],now=int(time.time()),finalizer=finalizer)
        need(closed and result.get('product_profile')=='canonical_app32'and result.get('raw_counts')==counts
            and result.get('scanner_findings_modified')is False and result.get('security_accepted')is False,'state')
        write_new(work/'assessment.json',result);write_new(root/'reports/security-assessment.json',result)
        need(result.get('status')=='PASS'and result.get('unresolved_count')==0,'raw_findings')
        return {'status':'same_image_App32_security_evidence_passed','raw_counts':counts,
            'raw_gate_passed':result['raw_gate_passed'],'assessment_policy_sha256':result['policy_sha256'],'security_accepted':False}
    finally:
        if not closed:stack.close()

class HeldSource:
    def __init__(self,stack,repo,rows):
        soft=resource.getrlimit(resource.RLIMIT_NOFILE)[0]
        need(len(rows)<=20000 and (soft==resource.RLIM_INFINITY or len(rows)+len(os.listdir('/proc/self/fd'))+64<=soft),'source_fd_budget')
        self.checks=[]
        for row in rows:
            path=canonical(repo/row['path']);fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC);stack.callback(os.close,fd)
            def check(fd=fd,path=path,row=row):
                need(identity(os.fstat(fd))==identity(path.lstat())==row['identity'],'source_changed')
                digest=sha256();offset=0
                while offset<row['bytes']:
                    block=os.pread(fd,min(65536,row['bytes']-offset),offset);need(block,'source_changed')
                    offset+=len(block);digest.update(block)
                need(not os.pread(fd,1,offset)and digest.hexdigest()==row['sha256']
                    and identity(os.fstat(fd))==identity(path.lstat())==row['identity'],'source_changed')
            check();self.checks.append(check)
    def check(self):
        for check in self.checks:check()

def public_input_names(repo,source):
    names={row['path'] for row in source['files']}
    expected=next(row for row in source['files'] if row['path']=='scripts/app_security_os_packages.lock.json')
    raw,metadata=read_file(repo/expected['path'],65536)
    need(metadata==expected['identity'] and sha256(raw).hexdigest()==expected['sha256'],'source_changed')
    lock=document(raw)
    need(type(lock) is dict and len(lock['packages'])==15,'input_inventory')
    names|={'pip_packages/.security-os-capsule.lock','pip_packages/security-os-capsule/manifest.json'}
    for row in lock['packages']:
        path=row['path'];need(type(path) is str and re.fullmatch(r'[A-Za-z0-9_.+~\-]+\.deb',path),'input_inventory')
        names.add('pip_packages/security-os-capsule/'+path)
    sqlite_lock='scripts/sqlite3541_packages.lock.json' if CANONICAL3541 else 'scripts/sqlite_wal_package.lock.json'
    sqlite_expected=next(row for row in source['files'] if row['path']==sqlite_lock)
    sqlite_raw,sqlite_metadata=read_file(repo/sqlite_expected['path'],65536)
    need(sqlite_metadata==sqlite_expected['identity'] and sha256(sqlite_raw).hexdigest()==sqlite_expected['sha256'],'source_changed')
    sqlite=document(sqlite_raw)
    if CANONICAL3541:
        module_expected=next(row for row in source['files'] if row['path']=='scripts/sqlite3541_package.py')
        module_raw,module_metadata=read_file(repo/module_expected['path'],65536)
        need(module_metadata==module_expected['identity'] and sha256(module_raw).hexdigest()==module_expected['sha256'],'source_changed')
        module=source_module(module_raw,repo/module_expected['path']);sqlite=module.validate_lock(sqlite,'trixie')
        names|={'pip_packages/.sqlite3541-capsule.lock','pip_packages/sqlite3541-capsule/manifest.json',
            'pip_packages/sqlite3541-capsule/provenance.json','pip_packages/sqlite3541-capsule/'+sqlite['package']['path']}
    else:
        need(type(sqlite) is dict and sqlite.get('schema')=='sqlite_wal_image_package_lock/1'
            and type(sqlite.get('package')) is dict and sqlite['package'].get('name')=='libsqlite3-0'
            and sqlite['package'].get('version')=='3.46.1-7+deb13u2+gold2'
            and sqlite['package'].get('path')=='libsqlite3-0_3.46.1-7+deb13u2+gold2_amd64.deb','input_inventory')
        names|={'pip_packages/.sqlite-wal-capsule.lock','pip_packages/sqlite-wal-capsule/manifest.json',
            'pip_packages/sqlite-wal-capsule/'+sqlite['package']['path']}
    tar_expected=next(row for row in source['files'] if row['path']=='scripts/tar_acl_package.lock.json')
    tar_raw,tar_metadata=read_file(repo/tar_expected['path'],65536)
    need(tar_metadata==tar_expected['identity'] and sha256(tar_raw).hexdigest()==tar_expected['sha256'],'source_changed')
    tar=document(tar_raw)
    need(type(tar)is dict and tar.get('schema')=='tar_acl_image_package_lock/1' and tar.get('armed')is True
        and tar.get('package',{}).get('name')=='tar' and tar['package'].get('version')=='1.35+dfsg-5~trixie+gold2'
        and tar['package'].get('path')=='tar_1.35+dfsg-5~trixie+gold2_amd64.deb','input_inventory')
    names|={'pip_packages/.tar-acl-capsule.lock','pip_packages/tar-acl-capsule/manifest.json',
        'pip_packages/tar-acl-capsule/'+tar['package']['path']}
    core_expected=next(row for row in source['files']if row['path']=='scripts/coreutils_acl_package.lock.json')
    core_raw,core_metadata=read_file(repo/core_expected['path'],1024**2)
    need(core_metadata==core_expected['identity']and sha256(core_raw).hexdigest()==core_expected['sha256'],'source_changed')
    core=document(core_raw)
    need(type(core)is dict and core.get('schema')=='coreutils_acl_image_package_lock/1'and core.get('armed')is True
        and core.get('package',{}).get('name')=='coreutils'and core['package'].get('version')=='9.7-3+gold2'
        and core['package'].get('path')=='coreutils_9.7-3+gold2_amd64.deb','input_inventory')
    names|={'pip_packages/.coreutils-acl-capsule.lock','pip_packages/coreutils-acl-capsule/manifest.json',
        'pip_packages/coreutils-acl-capsule/'+core['package']['path']}
    return names

def input_files(repo,allowed=None):
    rows=[];seen=set();total=0;entries=0
    for entry in INPUTS:
        base=repo/entry
        if not base.exists():continue
        need(not base.is_symlink(),'input_inventory')
        paths=[base] if base.is_file() else []
        if base.is_dir():
            for folder,dirs,files in os.walk(base,followlinks=False):
                entries+=len(dirs)+len(files);need(entries<=20000,'input_bound')
                for name in list(dirs):
                    path=Path(folder)/name;relative=path.relative_to(repo).as_posix()
                    if name=='__pycache__':dirs.remove(name);continue
                    need(not path.is_symlink(),'input_inventory')
                    need(allowed is None or relative.startswith('mini_app_dist/') or relative=='mini_app_dist'
                        or any(p.startswith(relative+'/') for p in allowed),'input_inventory')
                paths.extend(Path(folder)/name for name in files)
            paths.sort()
        for path in paths:
            relative=path.relative_to(repo).as_posix()
            if '__pycache__' in path.parts or path.suffix in {'.pyc','.pyo','.pyd'}:continue
            need(allowed is None or relative in allowed or relative.startswith('mini_app_dist/'),'input_inventory')
            need(not path.is_symlink(),'input_inventory')
            if path.is_dir():continue
            need(relative not in seen and '\n' not in relative and '\\' not in relative,'input_inventory');seen.add(relative)
            raw,metadata=read_file(path,MAX_FILE);total+=len(raw);need(total<=MAX_TOTAL and len(rows)<20000,'input_bound')
            rows.append({'path':relative,'bytes':len(raw),'sha256':sha256(raw).hexdigest(),'identity':metadata})
    return rows

def empty_inputs(repo,rows):
    names={r['path'] for r in rows}
    return [entry for entry in INPUTS if (repo/entry).is_dir() and not any(p.startswith(entry+'/') for p in names)]

def foreign_signature(rows,empty_directories=()):
    # Exact deploy.sh foreign_image_signature/hash_file_or_dir wire format:
    # fixed root order, then LC_ALL=C relative-path sort within each directory.
    by_path={r['path']:r for r in rows};body=bytearray(b'docker_image=trading_bot_base\n')
    for entry in INPUTS:
        names=[entry] if entry in by_path else sorted((p for p in by_path if p.startswith(entry+'/')),key=lambda p:p.encode())
        for name in names:body.extend((by_path[name]['sha256']+'  '+name+'\n').encode())
        # The existing xargs invocation intentionally has no -r: an empty
        # directory emits sha256sum's empty-stdin record, unlike a missing root.
        if not names and entry in empty_directories:body.extend((sha256(b'').hexdigest()+'  -\n').encode())
    return sha256(body).hexdigest()

def state(root):
    hosted(root)
    raw,_=read_file(root/'source-identity.json',MAX_COMMAND);value=document(raw);repo=canonical(value['repo']);hosted(root,repo)
    need(value['schema']=='canonical_app_source/1' and value['run_id']==os.environ['GITHUB_RUN_ID']
        and value['run_attempt']==os.environ['GITHUB_RUN_ATTEMPT'],'state')
    git_identity(repo,(value['source_commit'],value['source_tree']))
    need(value.get('canonical3541',False) is CANONICAL3541,'state')
    if CANONICAL3541:
        need(value.get('app_profile','web')==APP_PROFILE and value.get('business_commit')==BUSINESS_COMMIT and value.get('business_tree')==BUSINESS_TREE
            and value.get('context_repo')==str(root/'app-context'),'state')
        return canonical(root/'app-context'),value
    return repo,value

def unchanged(root,repo,value,held):
    held.check()
    release=canonical(value['repo']) if CANONICAL3541 else repo
    git_identity(release,(value['source_commit'],value['source_tree']))
    need(source_manifest(release,value['source_commit'])==value['release_files' if CANONICAL3541 else 'files'],'source_changed')

def emit(name,value):
    path=os.environ.get('GITHUB_OUTPUT')
    if path:
        with open(path,'a') as output:output.write(name+'='+value+'\n')

def prepare(root,repo):
    global SUBPHASE
    SUBPHASE='source_admission'
    hosted(root,repo);commit,tree=git_identity(repo);rows=source_manifest(repo,commit)
    for name in ('reports','docker-config'):(root/name).mkdir(mode=0o700)
    write_new(root/'empty-trivy.yaml',b'{}\n')
    value={'schema':'canonical_app_source/1','repo':str(repo),'source_commit':commit,'source_tree':tree,
        'run_id':os.environ['GITHUB_RUN_ID'],'run_attempt':os.environ['GITHUB_RUN_ATTEMPT'],'files':rows,'security_accepted':False}
    if CANONICAL3541:
        release=repo;repo,context_rows=frozen_context(root,release)
        value.update(canonical3541=True,context_repo=str(repo),business_commit=BUSINESS_COMMIT,business_tree=BUSINESS_TREE,
            app_profile=APP_PROFILE,security_overrides=list(security_updates()),release_files=rows,files=context_rows)
        rows=context_rows
    write_new(root/'source-identity.json',value)
    with ExitStack() as stack:
        held=HeldSource(stack,repo,rows);(repo/'pip_packages').mkdir(exist_ok=True)
        child_env={**env(root)}
        if os.environ.get('GH_TOKEN'):child_env['GH_TOKEN']=os.environ['GH_TOKEN']
        if CANONICAL3541:
            SUBPHASE='app32_dependency_prepare'
            canonical_app32_prepare(root,release,child_env)
        SUBPHASE='vendor_cache_prepare'
        vendor_arguments=canonical_vendor_originals(root,repo,release) if CANONICAL3541 else []
        command([sys.executable,'-I','-B',str(repo/'scripts/prepare_app_security_os_cache.py'),
            '--cache-dir',str(repo/'pip_packages/security-os-capsule'),*vendor_arguments],seconds=600,environment=child_env)
        SUBPHASE='sqlite_cache_prepare'
        command([sys.executable,'-I','-B',str(repo/'scripts/prepare_sqlite_wal_os_cache.py'),
            '--cache-dir',str(repo/('pip_packages/sqlite3541-capsule' if CANONICAL3541 else 'pip_packages/sqlite-wal-capsule'))]
            +(['--canonical3541'] if CANONICAL3541 else []),seconds=600,environment=child_env)
        SUBPHASE='tar_cache_prepare'
        command([sys.executable,'-I','-B',str(repo/'scripts/prepare_tar_acl_os_cache.py'),
            '--cache-dir',str(repo/'pip_packages/tar-acl-capsule')],seconds=600,environment=child_env)
        command([sys.executable,'-I','-B',str(repo/'scripts/prepare_coreutils_acl_os_cache.py'),
            '--cache-dir',str(repo/'pip_packages/coreutils-acl-capsule')],seconds=600,environment=child_env)
        if CANONICAL3541:canonical_glibc(root,release,repo)
        unchanged(root,repo,value,held)
    write_new(root/'prepare.json',{'schema':'canonical_app_prepare/1','status':'prepared','security_accepted':False})
    emit('root',str(root));emit('context',str(repo));return {'status':'prepared','security_accepted':False}

def inspect(root,image,source,signature):
    value=document(command([*DOCKER,'image','inspect','--format','{{json .}}',image],environment=env(root),maximum=MAX_COMMAND))
    config=value.get('Config');layers=value.get('RootFS',{}).get('Layers')
    need(value.get('Id')==image and value.get('Os')=='linux' and value.get('Architecture')=='amd64'
        and type(config) is dict and type(layers) is list and 0<len(layers)<=128
        and all(type(p) is str and re.fullmatch('sha256:[a-f0-9]{64}',p) for p in layers),'image_identity')
    need(config.get('WorkingDir')=='/app' and (config.get('User') or '') in {'','0','root'}
        and (config.get('Entrypoint') or [])==[] and config.get('Cmd')==['python3'] and not config.get('OnBuild'),'image_config')
    labels={'org.opencontainers.image.revision':source['source_commit'],'io.gold-trade.release.tree':source['source_tree'],
        'io.gold-trade.release.input-signature':signature}
    if CANONICAL3541:
        named={'schema':'glibc84243_exact_runtime_named_context/1','files':GLIBC_FILES,'security_accepted':False}
        labels.update({'io.gold-trade.business.commit':BUSINESS_COMMIT,'io.gold-trade.business.tree':BUSINESS_TREE,
            'io.gold-trade.app.business-profile':APP_PROFILE,
            'io.gold-trade.sqlite.family':'canonical3541','io.gold-trade.glibc.named-context':sha256(encode(named)).hexdigest(),
            'io.gold-trade.glibc.Root-qualification':GLIBC_ROOT_PIN['sha256']})
    need(type(config.get('Labels')) is dict and all(config['Labels'].get(k)==v for k,v in labels.items()),'image_labels')
    return value

def build_output(root,argv,*,seconds=1800,maximum=MAX_COMMAND):
    """Retain only a bounded public prefix from the clean normal Docker build."""
    target=os.memfd_create('canonical-app-build-output',os.MFD_CLOEXEC);primary=False
    try:
        try:command(argv,environment=env(root),seconds=seconds,maximum=maximum,target=target,combine_stderr=True)
        except BaseException:primary=True;raise
        finally:
            try:
                size=os.fstat(target).st_size;budget=min(maximum,256*1024);prefix_limit=min(budget,128*1024);tail_limit=budget-prefix_limit
                prefix_count=min(size,prefix_limit);tail_count=min(max(0,size-prefix_count),tail_limit);tail_offset=size-tail_count
                prefix=os.pread(target,prefix_count,0);tail=os.pread(target,tail_count,tail_offset)
                need(len(prefix)==prefix_count and len(tail)==tail_count,'command_output_bound');count=prefix_count+tail_count
                value={'schema':'canonical_app_docker_build_output/2','combined_stdout_stderr':True,
                    'output_prefix':prefix.decode('utf-8','replace'),'prefix_offset':0,'prefix_bytes':prefix_count,'prefix_limit':prefix_limit,
                    'output_tail':tail.decode('utf-8','replace'),'tail_offset':tail_offset,'tail_bytes':tail_count,'tail_limit':tail_limit,
                    'observed_bytes':size,'captured_bytes':count,'output_truncated':size>count,
                    'complete_output':size==count and type(COMMAND_RETURN_CODE)is int,
                    'actual_command_exit':COMMAND_RETURN_CODE if type(COMMAND_RETURN_CODE)is int else 'UNKNOWN',
                    'build_accepted':False,'security_accepted':False}
                write_new(root/'reports/docker-build-output.json',value)
            except BaseException:
                if not primary:raise
    finally:os.close(target)


def build(root):
    global SUBPHASE
    SUBPHASE='build_source_admission'
    repo,value=state(root)
    with ExitStack() as stack:
        held=HeldSource(stack,repo,value['files']);unchanged(root,repo,value,held)
        if CANONICAL3541:
            named=canonical_glibc(root,canonical(value['repo']),repo,check_only=True)
            external=HeldSource(stack,root,canonical_external_rows(root));external.check()
        SUBPHASE='vendor_cache_check'
        command([sys.executable,'-I','-B',str(repo/'scripts/prepare_app_security_os_cache.py'),
            '--check','--cache-dir',str(repo/'pip_packages/security-os-capsule')],environment=env(root),seconds=120)
        SUBPHASE='sqlite_cache_check'
        command([sys.executable,'-I','-B',str(repo/'scripts/prepare_sqlite_wal_os_cache.py'),
            '--check','--cache-dir',str(repo/('pip_packages/sqlite3541-capsule' if CANONICAL3541 else 'pip_packages/sqlite-wal-capsule'))]
            +(['--canonical3541'] if CANONICAL3541 else []),environment=env(root),seconds=120)
        SUBPHASE='tar_cache_check'
        command([sys.executable,'-I','-B',str(repo/'scripts/prepare_tar_acl_os_cache.py'),
            '--check','--cache-dir',str(repo/'pip_packages/tar-acl-capsule')],environment=env(root),seconds=120)
        command([sys.executable,'-I','-B',str(repo/'scripts/prepare_coreutils_acl_os_cache.py'),
            '--check','--cache-dir',str(repo/'pip_packages/coreutils-acl-capsule')],environment=env(root),seconds=120)
        allowed=public_input_names(repo,value);rows=input_files(repo,allowed);known={r['path'] for r in value['files']}
        need(all(r['path'] in known or r['path'].split('/')[0] in GENERATED for r in rows)
            and any(r['path']=='mini_app_dist/index.html' for r in rows),'input_inventory')
        empty=empty_inputs(repo,rows);signature=foreign_signature(rows,empty);write_new(root/'input-manifest.json',{'schema':'canonical_app_inputs/1',
            'signature_format':'deploy.sh:foreign_image_signature','source_commit':value['source_commit'],
            'source_tree':value['source_tree'],'input_signature':signature,'empty_directories':empty,'files':rows,'security_accepted':False})
        argv=[*DOCKER,'build','--platform','linux/amd64','--iidfile',str(root/'candidate-image.id'),
            '-t','canonical-app:'+value['run_id']+'-'+value['run_attempt']]
        if CANONICAL3541:
            argv+=['--build-context','glibc-runtime='+str(root/'glibc-capsule'),
                '--label','io.gold-trade.glibc.named-context='+sha256(encode(named)).hexdigest(),
                '--label','io.gold-trade.glibc.Root-qualification='+GLIBC_ROOT_PIN['sha256'],
                '--label','io.gold-trade.business.commit='+BUSINESS_COMMIT,
                '--label','io.gold-trade.business.tree='+BUSINESS_TREE,
                '--label','io.gold-trade.app.business-profile='+APP_PROFILE,
                '--label','io.gold-trade.sqlite.family=canonical3541']
        for key,item in {'org.opencontainers.image.revision':value['source_commit'],'io.gold-trade.release.tree':value['source_tree'],
                'io.gold-trade.release.input-signature':signature}.items():argv+=['--label',key+'='+item]
        SUBPHASE='docker_build'
        build_output(root,[*argv,'-f',str(repo/'Dockerfile'),str(repo)])
        SUBPHASE='build_identity_and_inputs'
        raw,_=read_file(root/'candidate-image.id',128);image=raw.decode().strip();need(re.fullmatch('sha256:[a-f0-9]{64}',image),'image_identity')
        first=inspect(root,image,value,signature);unchanged(root,repo,value,held)
        if CANONICAL3541:external.check()
        need(input_files(repo,allowed)==rows and empty_inputs(repo,rows)==empty
            and encode(inspect(root,image,value,signature)['Config'])==encode(first['Config']),'input_changed')
        record={'schema':'canonical_app_image_build/1','image_id':image,'source_commit':value['source_commit'],
            'source_tree':value['source_tree'],'input_signature':signature,'run_id':value['run_id'],'run_attempt':value['run_attempt'],
            'platform':'linux/amd64','rootfs':first['RootFS'],'dockerfile_checks':['trust','coreutils_acl','python_micro_release','psycopg2','sqlite_libmagic','pip_removed'],
            'activation_performed':False,'market_roles_built':False,'security_accepted':False}
        if CANONICAL3541:
            record.update(sqlite_family='canonical3541',business_commit=BUSINESS_COMMIT,business_tree=BUSINESS_TREE,
                app_profile=APP_PROFILE,security_overrides=list(security_updates()),
                recipe={'path':APP_RECIPE,'bytes':APP_RECIPE_PIN[0],'sha256':APP_RECIPE_PIN[1]},
                glibc_named_context=named,glibc_context_signature=sha256(encode(named)).hexdigest(),
                glibc_external_inputs=canonical_external_rows(root))
        write_new(root/'image-build.json',record)
    emit('image_id',image);return {'status':'built_not_accepted','image_id':image,'security_accepted':False}

def file_digest(path,maximum):
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC)
    try:
        before=identity(os.fstat(fd));need(stat.S_ISREG(before[2]) and before[3]==os.geteuid() and before[5]==1
            and not before[2]&0o022 and 0<before[6]<=maximum,'metadata')
        digest=sha256();offset=0
        while offset<before[6]:
            raw=os.pread(fd,min(65536,before[6]-offset),offset);need(raw,'input_bound');offset+=len(raw);digest.update(raw)
        need(before==identity(os.fstat(fd))==identity(Path(path).lstat()),'source_changed')
        return {'bytes':offset,'sha256':digest.hexdigest()}
    finally:os.close(fd)

def archive_config(path,image,layers):
    # Metadata/config binding only. A receiving Root still admits the complete
    # physical archive and portable daemon identity before any LOAD/activation.
    with tarfile.open(path,'r:') as archive:
        members={}
        for item in archive:
            need(len(members)<4096 and item.name not in members and not PurePosixPath(item.name).is_absolute()
                and '..' not in PurePosixPath(item.name).parts and (item.isfile() or item.isdir()),'archive_shape')
            members[item.name]=item
        def read(name,maximum):
            item=members.get(name);need(item is not None and item.isfile() and 0<item.size<=maximum,'archive_config')
            return archive.extractfile(item).read(maximum+1)
        manifest=document(read('manifest.json',1024**2));need(type(manifest) is list and len(manifest)==1,'archive_config')
        name=manifest[0]['Config'];need(type(name) is str and name in members,'archive_config')
        raw=read(name,4*1024**2);config_id='sha256:'+sha256(raw).hexdigest();config=document(raw)
        need(config_id==image and config.get('rootfs')=={'type':'layers','diff_ids':layers},'archive_config')
        return config_id

def reports(root,image):
    raw,_=read_file(root/'reports/vulnerabilities.json',MAX_REPORT);scan=document(raw)
    raw_sbom,_=read_file(root/'reports/sbom.cdx.json',MAX_REPORT);sbom=document(raw_sbom)
    need(scan.get('Metadata',{}).get('ImageID')==image,'report_identity')
    component=sbom.get('metadata',{}).get('component',{})
    need(component.get('type')=='container' and component.get('name')==image and
        [p.get('value') for p in component.get('properties',[]) if p.get('name')=='aquasecurity:trivy:ImageID']==[image],'report_identity')
    counts={name:0 for name in ('UNKNOWN','LOW','MEDIUM','HIGH','CRITICAL')}
    need(type(scan.get('Results')) is list,'report_shape')
    for row in scan['Results']:
        need(type(row) is dict and (row.get('Vulnerabilities') is None or type(row['Vulnerabilities']) is list),'report_shape')
        for finding in row.get('Vulnerabilities') or []:
            need(type(finding) is dict and finding.get('Severity') in counts,'report_shape');counts[finding['Severity']]+=1
    return counts,{'vulnerabilities.json':{'bytes':len(raw),'sha256':sha256(raw).hexdigest()},
        'sbom.cdx.json':{'bytes':len(raw_sbom),'sha256':sha256(raw_sbom).hexdigest()}}

def finalize(root):
    global SUBPHASE
    SUBPHASE='export_source_admission'
    repo,source=state(root);record=document(read_file(root/'image-build.json',MAX_COMMAND)[0])
    inputs=document(read_file(root/'input-manifest.json',MAX_COMMAND)[0]);image=record['image_id'];signature=record['input_signature']
    need(record['source_commit']==source['source_commit'] and record['source_tree']==source['source_tree']
        and inputs['input_signature']==signature==foreign_signature(inputs['files'],inputs['empty_directories']),'state')
    with ExitStack() as stack:
        held=HeldSource(stack,repo,source['files']);unchanged(root,repo,source,held)
        if CANONICAL3541:
            external_rows=canonical_external_rows(root);need(external_rows==record['glibc_external_inputs'],'input_changed')
            external=HeldSource(stack,root,external_rows)
            proof=document(read_file(root/'reports/app32.json',MAX_COMMAND)[0]);cleanup=document(read_file(root/'reports/app32-cleanup.json',65536)[0])
            runtime=document(read_file(root/'reports/sqlite-runtime.json',65536)[0])
            source_parity=document(read_file(root/'reports/source-parity.json',65536)[0])
            need(proof['image_id']==runtime['image_id']==image and proof['assertions_changed']is False
                and proof['actual']['status']=='passed' and cleanup['owned_cleanup_complete']is True
                and cleanup['closed_causes']==[] and cleanup['primary_failure_retained']is False,'state')
            need(source_parity['image_id']==image and source_parity['app_profile']==APP_PROFILE
                and source_parity['business_commit']==BUSINESS_COMMIT and source_parity['business_tree']==BUSINESS_TREE
                and source_parity['input_signature']==signature and source_parity['security_overrides']==list(security_updates())
                and source_parity['actual']['status']=='PASS'and proof['source_parity']==file_digest(root/'reports/source-parity.json',65536),'source_changed')
        allowed=public_input_names(repo,source)
        need(input_files(repo,allowed)==inputs['files'] and empty_inputs(repo,inputs['files'])==inputs['empty_directories'],'input_changed');before=inspect(root,image,source,signature)
        SUBPHASE='scan_report_binding';counts,bound_reports=reports(root,image)
        need(os.statvfs(root).f_bavail*os.statvfs(root).f_frsize>=2*MAX_ARCHIVE+512*1024**2,'archive_bound')
        tar=root/'candidate-image.tar';fd=os.open(tar,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        SUBPHASE='docker_export'
        try:command([*DOCKER,'image','save',image],target=fd,maximum=MAX_ARCHIVE,seconds=600,environment=env(root));os.fsync(fd)
        finally:os.close(fd)
        SUBPHASE='archive_config_binding';tar_fact=file_digest(tar,MAX_ARCHIVE);tar_identity=identity(tar.lstat())
        config_id=archive_config(tar,image,before['RootFS']['Layers'])
        target=root/'candidate-image.tar.gz';out=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        SUBPHASE='archive_compression'
        try:
            deadline=time.monotonic()+300
            with os.fdopen(os.dup(out),'wb') as output,gzip.GzipFile(fileobj=output,filename='',mode='wb',mtime=0) as compressed,tar.open('rb') as incoming:
                while block:=incoming.read(65536):
                    need(time.monotonic()<deadline,'command_timeout');compressed.write(block);need(os.fstat(out).st_size<=MAX_ARCHIVE,'archive_bound')
            os.fsync(out)
        finally:os.close(out)
        SUBPHASE='export_final_checks';compressed_fact=file_digest(target,MAX_ARCHIVE)
        need(encode(inspect(root,image,source,signature)['Config'])==encode(before['Config']),'image_config')
        need(input_files(repo,allowed)==inputs['files'] and empty_inputs(repo,inputs['files'])==inputs['empty_directories']
            and identity(tar.lstat())==tar_identity and file_digest(tar,MAX_ARCHIVE)==tar_fact,'input_changed')
        unchanged(root,repo,source,held)
        if CANONICAL3541:external.check()
        receipt={'schema':'canonical_app_image_export/1','status':'exported_not_accepted','image_id':image,'image_config_id':config_id,
            'source_commit':source['source_commit'],'source_tree':source['source_tree'],'input_signature':signature,
            'run_id':source['run_id'],'run_attempt':source['run_attempt'],'platform':'linux/amd64','rootfs':before['RootFS'],
            'archive':{'format':'docker-archive+gzip',**compressed_fact,'uncompressed_bytes':tar_fact['bytes'],'uncompressed_sha256':tar_fact['sha256']},
            'inputs':{name:file_digest(root/name,MAX_COMMAND) for name in ('source-identity.json','input-manifest.json','image-build.json')},
            'reports':bound_reports,'raw_counts':counts,'full_config_compared_in_ram':True,'physical_archive_admission_complete':False,
            'native_abi_checks':{'source':'ordinary_Dockerfile_RUN','completed_with_build':True,
                'checks':record.get('dockerfile_checks',[])},
            'signatures_verified':False,'activation_performed':False,'security_accepted':False}
        if CANONICAL3541:
            receipt.update(sqlite_family='canonical3541',business_commit=BUSINESS_COMMIT,business_tree=BUSINESS_TREE,
                app_profile=APP_PROFILE,security_overrides=list(security_updates()),
                glibc_named_context=record['glibc_named_context'],glibc_context_signature=record['glibc_context_signature'],
                actual_app32_and_sqlite={name:file_digest(root/'reports'/name,MAX_COMMAND)
                    for name in('app32.json','app32-cleanup.json','sqlite-runtime.json','source-parity.json')})
        write_new(root/'image-export.json',receipt);write_new(root/'finalize.json',{'schema':'canonical_app_finalize/1','status':'exported_not_accepted','security_accepted':False})
    emit('export_complete','true');return {'status':'exported_not_accepted','image_id':image,'security_accepted':False}

def gate(root):
    if CANONICAL3541:return canonical_assess(root)
    hosted(root);record=document(read_file(root/'image-build.json',MAX_COMMAND)[0]);counts,_=reports(root,record['image_id'])
    need(counts['HIGH']==counts['CRITICAL']==0,'raw_findings');return {'status':'raw_high_critical_zero','security_accepted':False}

def main(argv=None):
    global SUBPHASE,CANONICAL3541
    SUBPHASE='entry';trusted=False;root=None
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('phase',choices=('prepare','build','app32','finalize','gate'))
    parser.add_argument('--root',type=Path,required=True);parser.add_argument('--repo',type=Path)
    parser.add_argument('--canonical3541',action='store_true')
    parser.add_argument('--app-profile',choices=tuple(APP_PROFILES),default='web')
    parser.error=lambda _:need(False,'state')
    try:
        args=parser.parse_args(argv);CANONICAL3541=args.canonical3541;select_app_profile(args.app_profile)
        need(CANONICAL3541 or APP_PROFILE=='web','app_profile')
        root=canonical(args.root);hosted(root);trusted=True
        need((args.phase=='prepare')==(args.repo is not None),'state')
        need(args.phase!='app32' or CANONICAL3541,'state')
        value=prepare(root,canonical(args.repo)) if args.phase=='prepare' else {'build':build,'app32':canonical_smoke,'finalize':finalize,'gate':gate}[args.phase](root)
    except SystemExit as error:
        if error.code==0:return 0
        raise
    except BaseException as error:
        code=error.args[0] if type(error) is RuntimeError and len(error.args)==1 and type(error.args[0]) is str and error.args[0] in CODES else 'io_refusal' if isinstance(error,OSError) else 'refused'
        value={'schema':'canonical_app_failure/1','status':'refused','subphase':SUBPHASE,'code':code,
            'command_return_code':COMMAND_RETURN_CODE if code in ('command_failed','command_timeout','command_output_bound') and type(COMMAND_RETURN_CODE) is int and -255<=COMMAND_RETURN_CODE<=255 else None,
            'os_errno':errno.errorcode.get(error.errno,'unclassified_errno') if isinstance(error,OSError) else None,
            'exception_class':type(error).__name__ if type(error) in {RuntimeError,OSError,PermissionError,ValueError,TypeError,KeyError} else 'unclassified','security_accepted':False}
        if trusted:
            try:
                folder=canonical(root/'reports');need(folder.lstat().st_uid==1001 and stat.S_IMODE(folder.lstat().st_mode)==0o700,'root_scope')
                write_new(folder/'builder-failure.json',value)
            except BaseException:pass
        print(encode(value).decode(),end='');return 1
    print(encode(value).decode(),end='');return 0

if __name__=='__main__':raise SystemExit(main())
