"""Build/export the ordinary main App image on disposable hosted CI only.

This builder grants no production activation or security acceptance authority.
It uses the ordinary foreign input-signature format without loading deploy.sh.
"""
import argparse
from contextlib import ExitStack
from hashlib import sha1,sha256
import gzip
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
import time

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
CODES=frozenset(('hosted_only','root_scope','path','metadata','source_changed','source_inventory','git_identity',
    'git_dirty','command_failed','command_timeout','command_output_bound','cleanup_unproven','input_bound',
    'input_inventory','input_changed','source_fd_budget','state','image_identity','image_config','image_labels','archive_bound',
    'archive_shape','archive_config','report_identity','report_shape','raw_findings','output_exists','io_refusal','refused'))

def need(ok,code):
    if not ok:raise RuntimeError(code)

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

def selected(name):return name in INPUTS or name.split('/')[0] in {*INPUTS,'frontend'}

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

class HeldSource:
    def __init__(self,stack,repo,rows):
        soft=resource.getrlimit(resource.RLIMIT_NOFILE)[0]
        need(len(rows)<=20000 and (soft==resource.RLIM_INFINITY or len(rows)+len(os.listdir('/proc/self/fd'))+64<=soft),'source_fd_budget')
        self.checks=[]
        for row in rows:
            path=canonical(repo/row['path']);fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC);stack.callback(os.close,fd)
            def check(fd=fd,path=path,row=row):
                need(identity(os.fstat(fd))==identity(path.lstat())==row['identity'],'source_changed')
                need(sha256(os.pread(fd,row['bytes']+1,0)).hexdigest()==row['sha256'],'source_changed')
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
    sqlite_expected=next(row for row in source['files'] if row['path']=='scripts/sqlite_wal_package.lock.json')
    sqlite_raw,sqlite_metadata=read_file(repo/sqlite_expected['path'],65536)
    need(sqlite_metadata==sqlite_expected['identity'] and sha256(sqlite_raw).hexdigest()==sqlite_expected['sha256'],'source_changed')
    sqlite=document(sqlite_raw)
    need(type(sqlite) is dict and sqlite.get('schema')=='sqlite_wal_image_package_lock/1'
        and type(sqlite.get('package')) is dict and sqlite['package'].get('name')=='libsqlite3-0'
        and sqlite['package'].get('version')=='3.46.1-7+deb13u2+gold1'
        and sqlite['package'].get('path')=='libsqlite3-0_3.46.1-7+deb13u2+gold1_amd64.deb','input_inventory')
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
        and core.get('package',{}).get('name')=='coreutils'and core['package'].get('version')=='9.7-3+gold1'
        and core['package'].get('path')=='coreutils_9.7-3+gold1_amd64.deb','input_inventory')
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
    git_identity(repo,(value['source_commit'],value['source_tree']));return repo,value

def unchanged(root,repo,value,held):
    held.check();git_identity(repo,(value['source_commit'],value['source_tree']))
    need(source_manifest(repo,value['source_commit'])==value['files'],'source_changed')

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
    write_new(root/'source-identity.json',value)
    with ExitStack() as stack:
        held=HeldSource(stack,repo,rows);(repo/'pip_packages').mkdir(exist_ok=True)
        child_env={**env(root)}
        if os.environ.get('GH_TOKEN'):child_env['GH_TOKEN']=os.environ['GH_TOKEN']
        SUBPHASE='vendor_cache_prepare'
        command([sys.executable,'-I','-B',str(repo/'scripts/prepare_app_security_os_cache.py'),
            '--cache-dir',str(repo/'pip_packages/security-os-capsule')],seconds=600,environment=child_env)
        SUBPHASE='sqlite_cache_prepare'
        command([sys.executable,'-I','-B',str(repo/'scripts/prepare_sqlite_wal_os_cache.py'),
            '--cache-dir',str(repo/'pip_packages/sqlite-wal-capsule')],seconds=600,environment=child_env)
        SUBPHASE='tar_cache_prepare'
        command([sys.executable,'-I','-B',str(repo/'scripts/prepare_tar_acl_os_cache.py'),
            '--cache-dir',str(repo/'pip_packages/tar-acl-capsule')],seconds=600,environment=child_env)
        command([sys.executable,'-I','-B',str(repo/'scripts/prepare_coreutils_acl_os_cache.py'),
            '--cache-dir',str(repo/'pip_packages/coreutils-acl-capsule')],seconds=600,environment=child_env)
        unchanged(root,repo,value,held)
    write_new(root/'prepare.json',{'schema':'canonical_app_prepare/1','status':'prepared','security_accepted':False})
    emit('root',str(root));return {'status':'prepared','security_accepted':False}

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
        SUBPHASE='vendor_cache_check'
        command([sys.executable,'-I','-B',str(repo/'scripts/prepare_app_security_os_cache.py'),
            '--check','--cache-dir',str(repo/'pip_packages/security-os-capsule')],environment=env(root),seconds=120)
        SUBPHASE='sqlite_cache_check'
        command([sys.executable,'-I','-B',str(repo/'scripts/prepare_sqlite_wal_os_cache.py'),
            '--check','--cache-dir',str(repo/'pip_packages/sqlite-wal-capsule')],environment=env(root),seconds=120)
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
        for key,item in {'org.opencontainers.image.revision':value['source_commit'],'io.gold-trade.release.tree':value['source_tree'],
                'io.gold-trade.release.input-signature':signature}.items():argv+=['--label',key+'='+item]
        SUBPHASE='docker_build'
        build_output(root,[*argv,'-f',str(repo/'Dockerfile'),str(repo)])
        SUBPHASE='build_identity_and_inputs'
        raw,_=read_file(root/'candidate-image.id',128);image=raw.decode().strip();need(re.fullmatch('sha256:[a-f0-9]{64}',image),'image_identity')
        first=inspect(root,image,value,signature);unchanged(root,repo,value,held)
        need(input_files(repo,allowed)==rows and empty_inputs(repo,rows)==empty
            and encode(inspect(root,image,value,signature)['Config'])==encode(first['Config']),'input_changed')
        record={'schema':'canonical_app_image_build/1','image_id':image,'source_commit':value['source_commit'],
            'source_tree':value['source_tree'],'input_signature':signature,'run_id':value['run_id'],'run_attempt':value['run_attempt'],
            'platform':'linux/amd64','rootfs':first['RootFS'],'dockerfile_checks':['trust','coreutils_acl','python_micro_release','psycopg2','sqlite_libmagic','pip_removed'],
            'activation_performed':False,'market_roles_built':False,'security_accepted':False}
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
        receipt={'schema':'canonical_app_image_export/1','status':'exported_not_accepted','image_id':image,'image_config_id':config_id,
            'source_commit':source['source_commit'],'source_tree':source['source_tree'],'input_signature':signature,
            'run_id':source['run_id'],'run_attempt':source['run_attempt'],'platform':'linux/amd64','rootfs':before['RootFS'],
            'archive':{'format':'docker-archive+gzip',**compressed_fact,'uncompressed_bytes':tar_fact['bytes'],'uncompressed_sha256':tar_fact['sha256']},
            'inputs':{name:file_digest(root/name,MAX_COMMAND) for name in ('source-identity.json','input-manifest.json','image-build.json')},
            'reports':bound_reports,'raw_counts':counts,'full_config_compared_in_ram':True,'physical_archive_admission_complete':False,
            'native_abi_checks':{'source':'ordinary_Dockerfile_RUN','completed_with_build':True,
                'checks':record.get('dockerfile_checks',[])},
            'signatures_verified':False,'activation_performed':False,'security_accepted':False}
        write_new(root/'image-export.json',receipt);write_new(root/'finalize.json',{'schema':'canonical_app_finalize/1','status':'exported_not_accepted','security_accepted':False})
    emit('export_complete','true');return {'status':'exported_not_accepted','image_id':image,'security_accepted':False}

def gate(root):
    hosted(root);record=document(read_file(root/'image-build.json',MAX_COMMAND)[0]);counts,_=reports(root,record['image_id'])
    need(counts['HIGH']==counts['CRITICAL']==0,'raw_findings');return {'status':'raw_high_critical_zero','security_accepted':False}

def main(argv=None):
    global SUBPHASE
    SUBPHASE='entry';trusted=False;root=None
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('phase',choices=('prepare','build','finalize','gate'))
    parser.add_argument('--root',type=Path,required=True);parser.add_argument('--repo',type=Path)
    parser.error=lambda _:need(False,'state')
    try:
        args=parser.parse_args(argv);root=canonical(args.root);hosted(root);trusted=True
        need((args.phase=='prepare')==(args.repo is not None),'state')
        value=prepare(root,canonical(args.repo)) if args.phase=='prepare' else {'build':build,'finalize':finalize,'gate':gate}[args.phase](root)
    except SystemExit as error:
        if error.code==0:return 0
        raise
    except BaseException as error:
        code=error.args[0] if type(error) is RuntimeError and len(error.args)==1 and type(error.args[0]) is str and error.args[0] in CODES else 'io_refusal' if isinstance(error,OSError) else 'refused'
        value={'schema':'canonical_app_failure/1','status':'refused','subphase':SUBPHASE,'code':code,
            'command_return_code':COMMAND_RETURN_CODE if type(COMMAND_RETURN_CODE) is int and -255<=COMMAND_RETURN_CODE<=255 else None,
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
