"""Two ordinary vendor builds, with a separate candidate ACL toolchain stage."""
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import selectors
import signal
import stat
import subprocess
import sys
import time

ACL_VENDOR, TAR_VENDOR = '2.4.0-1', '1.35+dfsg-5'
ACL_VERSION, TAR_VERSION = ACL_VENDOR+'~trixie+gold1', TAR_VENDOR+'~trixie+gold1'
ACL_FAMILY, TAR_FAMILY = {'acl','libacl1','libacl1-dev'}, {'tar','tar-scripts'}
RUNTIME = {'libacl1','tar'}
BASELINE = {'libacl1':'2.3.2-2+b1','libacl1-dev':'2.3.2-2+b1','tar':'1.35+dfsg-3.1'}
INPUTS = {
 'acl_2.4.0-1.dsc':(2624,'b987ebbeb3d498794bad29cd65301b77384978493d8765c94dba8bad8fa325b6'),
 'acl_2.4.0.orig.tar.xz':(384828,'e661131456d2708a01c614a0f400e11d7d1bfaeb6f3e74b75bb980b72f0161a3'),
 'acl_2.4.0.orig.tar.xz.asc':(833,'5f4f0b9b78821764fe6b88e32aef6ec519628522c211c36706bcb78d6f3f036b'),
 'acl_2.4.0-1.debian.tar.xz':(47932,'65931c2fb3e821bda67f8d8d72d77e99ac61502748dcdf38b6805fe89339085e'),
 'tar_1.35+dfsg-5.dsc':(2428,'e026e7a19c295212fcc3250e3136b134ae09661c559bf707b8ebfdd8f5093c0a'),
 'tar_1.35+dfsg.orig.tar.xz':(2111608,'9ae57e981c1e73c0eebc2b26c9b0c4497fe310ef1d516ea430efb5470b71f7a8'),
 'tar_1.35+dfsg-5.debian.tar.xz':(22704,'51fecfaa10cbadb1ab19be88adfd34a6d876619c50aaaafd69011ddb2e283b70'),
}
RECIPE = {'Dockerfile','build.py','verify.py'}
SCHEMA = 'trixie_acl_tar_vendor_candidate/1'
LOG_LIMIT = 32*1024**2

def require(ok, code):
    if not ok: raise RuntimeError(code)

def read(path, maximum=16*1024**2):
    path=Path(path);fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        before=os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_uid in {0,os.geteuid()} and before.st_nlink==1
                and not before.st_mode&0o022 and 0<=before.st_size<=maximum,'input_metadata')
        raw=bytearray()
        while chunk:=os.read(fd,min(65536,maximum+1-len(raw))):
            raw.extend(chunk);require(len(raw)<=maximum,'input_bound')
        identity=lambda s:(s.st_dev,s.st_ino,s.st_size,s.st_mode,s.st_uid,s.st_nlink,s.st_mtime_ns,s.st_ctime_ns)
        require(len(raw)==before.st_size and identity(before)==identity(os.fstat(fd))==identity(path.lstat()),'input_changed')
        return bytes(raw)
    finally:os.close(fd)

def environment():
    return {'PATH':'/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin','HOME':'/work/home','LC_ALL':'C.UTF-8',
            'SOURCE_DATE_EPOCH':'1792800000','DEB_BUILD_OPTIONS':'parallel=2','DEB_BUILD_PROFILES':'noudeb',
            'PYTHONDONTWRITEBYTECODE':'1'}

def command(argv,cwd,log,timeout=120,extra=None):
    # Only vendor build/test children receive 022. The controller and evidence
    # keep 077, while stock debhelper sees normal packaging permissions.
    child=subprocess.Popen(argv,cwd=cwd,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                           env={**environment(),**(extra or {})},start_new_session=True,umask=0o022)
    deadline=time.monotonic()+timeout;complete=False
    try:
        with Path(log).open('xb') as output,selectors.DefaultSelector() as selector:
            selector.register(child.stdout,selectors.EVENT_READ);total=0
            while selector.get_map():
                require(time.monotonic()<deadline,'command_timeout')
                for key,_ in selector.select(.1):
                    chunk=os.read(key.fileobj.fileno(),min(65536,LOG_LIMIT+1-total))
                    if not chunk:selector.unregister(key.fileobj)
                    else:
                        total+=len(chunk);require(total<=LOG_LIMIT,'command_log_bound');output.write(chunk)
            require(child.wait(timeout=max(.1,deadline-time.monotonic()))==0,'command_status');complete=True
    finally:
        if not complete or child.poll() is None:
            try:os.killpg(child.pid,signal.SIGKILL)
            except ProcessLookupError:pass
        child.wait(timeout=3);child.stdout.close()

def validate_inputs(folder,toolchain=False):
    require({p.name for p in folder.iterdir()}==set(INPUTS)|({'toolchain-packages.txt'} if toolchain else set()),'vendor_input_inventory')
    for name,(size,digest) in INPUTS.items():
        raw=read(folder/name,size);require(len(raw)==size and sha256(raw).hexdigest()==digest,'vendor_input_identity')

def checked_receipt(path,phase):
    raw=read(path,1024**2);value=json.loads(raw)
    require(value['schema']==SCHEMA and value['phase']==phase and value['status']=='candidate_built_not_accepted'
            and all(value[k] is False for k in ('security_accepted','production_installed','raw_scanner_gate_waived','legacy_file_api_security_assessed'))
            and value['abi']['status']=='passed' and value['abi']['security_accepted'] is False
            and value['interactive_or_privileged_coverage']=='NOT_TESTED','receipt_state')
    require(re.fullmatch('sha256:[a-f0-9]{64}',value['builder_image_id']),'builder_identity')
    require(value['inputs_sha256']=={name:row[1] for name,row in INPUTS.items()},'receipt_inputs')
    require(value['selected_runtime_packages']==(sorted(RUNTIME) if phase=='tar' else [])
            and value['compiler_phase_outputs']==(['libacl1','libacl1-dev'] if phase=='acl' else [])
            and value['abi']['phase']==phase and value['abi']['normal_file_acl']=='passed'
            and set(value['abi']['libraries'])=={'acl'}
            and value['source_authentication']=='signed_debian_snapshot_sources_index_and_pinned_archive_bytes'
            and value['maintainer_signatures_authenticated'] is False and value['default_vendor_build']=='passed','receipt_scope')
    family=ACL_FAMILY if phase=='acl' else ACL_FAMILY|TAR_FAMILY
    rows=value['all_family_packages'];require(0<len(rows)<=20 and len({r['name'] for r in rows})==len(rows),'family_inventory')
    require({r['name'] for r in rows if not r['name'].endswith('-dbgsym')}==family,'family_inventory')
    for row in rows:
        name=row['name'].removesuffix('-dbgsym')
        require(name in family and row['version']==(ACL_VERSION if name in ACL_FAMILY else TAR_VERSION)
                and Path(row['path']).name==row['path'] and re.fullmatch(r'[A-Za-z0-9+._~-]+\.deb',row['path'])
                and type(row['bytes']) is int and 0<row['bytes']<=32*1024**2
                and re.fullmatch('[a-f0-9]{64}',row['sha256']),'package_identity')
    return raw,value

def package_bytes(folder,row):
    require(Path(row['path']).name==row['path'] and re.fullmatch(r'[A-Za-z0-9+._~-]+\.deb',row['path']),'package_path')
    raw=read(folder/row['path'],32*1024**2)
    require(len(raw)==row['bytes'] and sha256(raw).hexdigest()==row['sha256'],'package_bytes')
    return raw

def capsule(source,receipt,target,names):
    require(not os.path.lexists(target) and source.resolve(strict=True)==source,'capsule_destination')
    rows=[row for row in receipt['all_family_packages'] if row['name'] in names]
    require(len(rows)==len(names) and {r['name'] for r in rows}==names,'capsule_inventory')
    raw=read(source/'build-receipt.json',1024**2);require(json.loads(raw)==receipt,'capsule_receipt')
    buffers={'build-receipt.json':raw,**{r['path']:package_bytes(source,r) for r in rows}}
    require(sum(map(len,buffers.values()))<=64*1024**2,'capsule_bound')
    target.mkdir(mode=0o755);target.chmod(0o755)
    for name,data in buffers.items():
        fd=os.open(target/name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o644)
        with os.fdopen(fd,'wb') as output:output.write(data);output.flush();os.fchmod(output.fileno(),0o644)
    return target

def fields(path,work,label):
    log=work/('fields-'+label+'.log')
    command(['/usr/bin/dpkg-deb','-f',str(path),'Package','Version','Architecture'],work,log,20)
    return dict(line.split(': ',1) for line in read(log,65536).decode().splitlines())

def coherent_acl(work):
    log=work/'coherent-acl-versions.txt'
    command(['/usr/bin/dpkg-query','-W','-f=${Package}=${Version}\n','libacl1','libacl1-dev'],work,log,15)
    require(dict(line.split('=',1) for line in read(log).decode().splitlines())=={'libacl1':ACL_VERSION,'libacl1-dev':ACL_VERSION},'candidate_acl_toolchain_required')
    header=read(Path('/usr/include/sys/acl.h'),65536)
    require(all(name in header for name in (b'acl_get_file_at',b'acl_set_file_at',b'acl_delete_def_file_at')),'candidate_acl_headers_required')
    library=Path('/usr/lib/x86_64-linux-gnu/libacl.so.1').resolve(strict=True)
    require(library.parent==Path('/usr/lib/x86_64-linux-gnu'),'candidate_acl_library_path')
    return {'runtime_version':ACL_VERSION,'development_version':ACL_VERSION,'header_sha256':sha256(header).hexdigest(),
            'library_sha256':sha256(read(library)).hexdigest(),'compiler_only':True,'production_installed':False}

def run(builder,phase):
    require(re.fullmatch('sha256:[a-f0-9]{64}',builder) and phase in {'acl','tar'} and os.geteuid()==1001
            and Path('/.dockerenv').is_file(),'nonroot_immutable_container_required')
    require('VERSION_CODENAME=trixie\n' in read(Path('/etc/os-release').resolve(),65536).decode(),'trixie_required')
    work,inputs,recipe=Path('/work'),Path('/inputs'),Path('/recipe')
    require(not tuple(work.iterdir()) and work.stat().st_uid==1001,'fresh_owned_work_required')
    require({p.name for p in recipe.iterdir()}==RECIPE,'recipe_inventory');validate_inputs(inputs,True)
    os.umask(0o077);signal.signal(signal.SIGALRM,lambda *_:(_ for _ in ()).throw(TimeoutError('build_deadline')));signal.alarm(4800)
    (work/'home').mkdir();command(['/usr/bin/dpkg-query','-W'],work,work/'toolchain-packages.txt',20)
    artifacts=work/'artifacts';artifacts.mkdir();candidate=work/'candidate';candidate.mkdir()
    acl_raw=acl_prior=None;coherence=None
    if phase=='tar':
        acl_raw,acl_prior=checked_receipt(Path('/acl/build-receipt.json'),'acl')
        require(acl_prior['recipe_sha256']=={p.name:sha256(read(p)).hexdigest() for p in recipe.iterdir()},'acl_recipe_changed')
        require({p.name for p in Path('/acl').iterdir()}=={'build-receipt.json'}|{r['path'] for r in acl_prior['all_family_packages'] if r['name'] in {'libacl1','libacl1-dev'}},'acl_capsule_inventory')
        coherence=coherent_acl(work)
        for index,row in enumerate(r for r in acl_prior['all_family_packages'] if r['name'] in {'libacl1','libacl1-dev'}):
            package_bytes(Path('/acl'),row)
            command(['/usr/bin/dpkg-deb','-x','/acl/'+row['path'],str(candidate)],work,work/f'acl-extract-{index}.log',30)
        candidate_library=(candidate/'usr/lib/x86_64-linux-gnu/libacl.so.1').resolve(strict=True)
        require(candidate_library.is_relative_to(candidate) and coherence['header_sha256']==sha256(read(candidate/'usr/include/sys/acl.h')).hexdigest()
                and coherence['library_sha256']==sha256(read(candidate_library)).hexdigest(),'candidate_acl_compiler_bytes_changed')
    vendor,version,family=(ACL_VENDOR,ACL_VERSION,ACL_FAMILY) if phase=='acl' else (TAR_VENDOR,TAR_VERSION,TAR_FAMILY)
    source=work/'source';dsc='acl_2.4.0-1.dsc' if phase=='acl' else 'tar_1.35+dfsg-5.dsc'
    command(['/usr/bin/dpkg-source','-x',str(inputs/dsc),str(source)],work,work/'source-extract.log',120)
    changelog=source/'debian/changelog';original=read(changelog)
    require(original.startswith((phase+' ('+vendor+')').encode()),'vendor_version_changed')
    packaging={name:sha256(read(source/'debian'/name)).hexdigest() for name in ('rules','control')}
    prefix=(f'{phase} ({version}) trixie; urgency=medium\n\n  * Local Trixie rebuild of unchanged vendor source.\n  * Candidate only; no security acceptance.\n\n -- Project local build <local-build@invalid>  Sat, 24 Oct 2026 00:00:00 +0000\n\n')
    changelog.write_bytes(prefix.encode()+original)
    command(['/usr/bin/dpkg-buildpackage','-us','-uc','-b','-Pnoudeb'],source,work/'build.log',3600)
    require(all(sha256(read(source/'debian'/name)).hexdigest()==digest for name,digest in packaging.items()),'vendor_packaging_changed')
    rows=[];seen=set();packages=sorted(work.glob('*.deb'));require(0<len(packages)<=12 and not tuple(work.glob('*.udeb')),'family_output_bound')
    for index,path in enumerate(packages):
        value=fields(path,work,str(index));name=value['Package']
        require(name not in seen and name.removesuffix('-dbgsym') in family and value['Version']==version and value['Architecture'] in {'all','amd64'},'package_identity')
        seen.add(name);raw=read(path,32*1024**2);rows.append({'name':name,'path':path.name,'version':version,'bytes':len(raw),'sha256':sha256(raw).hexdigest()})
        command(['/usr/bin/dpkg-deb','-x',str(path),str(candidate)],work,work/f'extract-{index}.log',30)
        if name in ({'libacl1','libacl1-dev'} if phase=='acl' else {'tar'}):(artifacts/path.name).write_bytes(raw)
    require({name for name in seen if not name.endswith('-dbgsym')}==family,'full_family_missing')
    baseline=work/'baseline';baseline.mkdir();old_seen=set()
    for index,path in enumerate(sorted(Path('/baseline').glob('*.deb'))):
        value=fields(path,work,'old-'+str(index));name=value['Package']
        require(name in BASELINE and name not in old_seen and value['Version']==BASELINE[name],'baseline_identity');old_seen.add(name)
        command(['/usr/bin/dpkg-deb','-x',str(path),str(baseline)],work,work/f'baseline-{index}.log',30)
    require(old_seen==set(BASELINE),'baseline_inventory')
    command(['/usr/local/bin/python3','-I','-B',str(recipe/'verify.py'),str(baseline),str(candidate),phase],work,work/'abi-tests.json',240)
    abi=json.loads(read(work/'abi-tests.json',1024**2));require(abi['status']=='passed' and abi['security_accepted'] is False,'ordinary_tests_required')
    records=[]
    for pattern in ('*.changes','*.buildinfo'):
        paths=list(work.glob(pattern));require(len(paths)==1,'build_record_missing');raw=read(paths[0]);(artifacts/paths[0].name).write_bytes(raw)
        records.append({'path':paths[0].name,'sha256':sha256(raw).hexdigest()})
    if phase=='tar':
        runtime_row=next(r for r in acl_prior['all_family_packages'] if r['name']=='libacl1')
        (artifacts/runtime_row['path']).write_bytes(package_bytes(Path('/acl'),runtime_row))
    for name,origin,limit in (('abi-tests.json',work/'abi-tests.json',1024**2),('upstream-build.log',work/'build.log',LOG_LIMIT),('toolchain-packages.txt',work/'toolchain-packages.txt',1024**2)):
        (artifacts/name).write_bytes(read(origin,limit))
    receipt={'schema':SCHEMA,'phase':phase,'status':'candidate_built_not_accepted','builder_image_id':builder,
             'all_family_packages':(acl_prior['all_family_packages'] if acl_prior else [])+rows,'build_records':records,
             'selected_runtime_packages':sorted(RUNTIME) if phase=='tar' else [],'compiler_phase_outputs':['libacl1','libacl1-dev'] if phase=='acl' else [],
             'inputs_sha256':{name:row[1] for name,row in INPUTS.items()},'recipe_sha256':{p.name:sha256(read(p)).hexdigest() for p in recipe.iterdir()},
             'vendor_packaging_sha256':packaging,'source_authentication':'signed_debian_snapshot_sources_index_and_pinned_archive_bytes',
             'maintainer_signatures_authenticated':False,'abi':abi,'default_vendor_build':'passed','interactive_or_privileged_coverage':'NOT_TESTED',
             'acl_build_receipt_sha256':sha256(acl_raw).hexdigest() if acl_raw else None,'acl_compiler_provenance':coherence,
             'toolchain_inventory_sha256':sha256(read(work/'toolchain-packages.txt')).hexdigest(),
             'security_accepted':False,'production_installed':False,'raw_scanner_gate_waived':False,'legacy_file_api_security_assessed':False}
    (artifacts/'build-receipt.json').write_text(json.dumps(receipt,sort_keys=True)+'\n')
    for path in artifacts.iterdir():path.chmod(0o644)
    signal.alarm(0);print(json.dumps({'status':receipt['status'],'phase':phase,'security_accepted':False}))

# This installer is used twice in disposable network-isolated CI containers.
# The first result is a compiler layer, the second a runtime scan candidate.
INSTALL = r'''
from hashlib import sha256
import json,os,pathlib,subprocess,sys
os.umask(0o077)
root=pathlib.Path('/packages');phase=sys.argv[1]
names={'libacl1','libacl1-dev'} if phase=='toolchain' else {'libacl1','tar'}
with (root/'build-receipt.json').open('rb') as stream:raw=stream.read(1048577)
assert 0<len(raw)<=1048576
receipt=json.loads(raw);assert receipt['status']=='candidate_built_not_accepted' and receipt['security_accepted'] is False
rows=[r for r in receipt['all_family_packages'] if r['name'] in names]
assert len(rows)==2 and {r['name'] for r in rows}==names
paths=[]
for row in rows:
    path=root/row['path'];assert path.parent==root and path.is_absolute()
    with path.open('rb') as stream:raw=stream.read(32*1024**2+1)
    assert len(raw)==row['bytes']<=32*1024**2 and sha256(raw).hexdigest()==row['sha256']
    paths.append(str(path))
def inventory():
    result=subprocess.run(['/usr/bin/dpkg-query','-W','-f=${binary:Package}=${Version}\n'],check=True,capture_output=True,timeout=15)
    assert len(result.stdout)<=1024**2
    rows=[line.split('=',1) for line in result.stdout.decode().splitlines()]
    value={name.removesuffix(':amd64'):version for name,version in rows};assert len(value)==len(rows);return value
before=inventory();assert names<=set(before)
with open('/tmp/simulation','xb') as out:subprocess.run(['/usr/bin/apt-get','--simulate','--no-remove','--no-download','install',*paths],check=True,stdout=out,stderr=out,timeout=30)
with open('/tmp/simulation','rb') as inp:plan=inp.read(65537).decode()
assert len(plan)<=65536 and not any(line.startswith('Remv ') for line in plan.splitlines())
inst=[line.split()[1].removesuffix(':amd64') for line in plan.splitlines() if line.startswith('Inst ')]
assert len(inst)==2 and set(inst)==names
# Real local-file acquisition must remain enabled. The kernel network boundary
# prevents fetching anything else; simulation and final inventory close scope.
subprocess.run(['/usr/bin/apt-get','--yes','--no-remove','install',*paths],check=True,timeout=120)
after=inventory();assert set(before)==set(after)
changed={name.removesuffix(':amd64') for name in before if before[name]!=after[name]}
assert changed==names and before['libc6']==after['libc6']
for row in rows:assert after.get(row['name'],after.get(row['name']+':amd64'))==row['version']
audit=subprocess.run(['/usr/bin/dpkg','--audit'],check=True,capture_output=True,timeout=15);assert not audit.stdout.strip()
print(json.dumps({'status':'candidate_installed_not_accepted','compiler_only':phase=='toolchain','security_accepted':False,'production_installed':False,'libc_unchanged':True},sort_keys=True))
'''

def publish(root,acl,tar):
    acl_source,tar_source=root/'acl-work/artifacts',root/'tar-work/artifacts'
    acl_raw=read(acl_source/'build-receipt.json',1024**2)
    require(tar['acl_build_receipt_sha256']==sha256(acl_raw).hexdigest(),'acl_parent_changed')
    layer_raw=read(root/'reports/compiler-toolchain.json',65536);layer=json.loads(layer_raw)
    require(layer['schema']=='acl_tar_compiler_layer/1' and layer['compiler_only'] is True
            and layer['production_installed'] is False and layer['security_accepted'] is False
            and layer['image_id']==tar['builder_image_id'] and layer['parent_builder_id']==acl['builder_image_id']
            and layer['acl_build_receipt_sha256']==tar['acl_build_receipt_sha256'],'compiler_layer_changed')
    buffers={r['path']:package_bytes(tar_source,r) for r in tar['all_family_packages'] if r['name'] in RUNTIME}
    require(len(buffers)==2,'runtime_inventory')
    buffers.update({'acl-build-receipt.json':acl_raw,'build-receipt.json':read(tar_source/'build-receipt.json',1024**2),
                    'compiler-toolchain.json':layer_raw})
    for phase,receipt,source in (('acl',acl,acl_source),('tar',tar,tar_source)):
        require(len(receipt['build_records'])==2 and {Path(r['path']).suffix for r in receipt['build_records']}=={'.changes','.buildinfo'},'build_records')
        for row in receipt['build_records']:
            require(Path(row['path']).name==row['path'],'record_path');raw=read(source/row['path'])
            require(sha256(raw).hexdigest()==row['sha256'],'record_bytes');buffers[row['path']]=raw
        abi=read(source/'abi-tests.json',1024**2);require(json.loads(abi)==receipt['abi'],'abi_receipt_changed')
        buffers[phase+'-abi-tests.json']=abi;buffers[phase+'-upstream-build.log']=read(source/'upstream-build.log',LOG_LIMIT)
        inventory=read(source/'toolchain-packages.txt',1024**2)
        require(sha256(inventory).hexdigest()==receipt['toolchain_inventory_sha256'],'toolchain_changed')
        buffers[phase+'-toolchain-packages.txt']=inventory
    buffers['recipe-identity.json']=read(root/'recipe-identity.json',65536)
    require(sum(map(len,buffers.values()))<=96*1024**2,'public_export_bound')
    target=root/'public';require(not tuple(target.iterdir()),'public_export_not_empty')
    for name,raw in buffers.items():
        fd=os.open(target/name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'wb') as output:output.write(raw)

def ci_build(root):
    import resource
    require(os.environ.get('GITHUB_ACTIONS')=='true' and os.environ.get('GITHUB_REPOSITORY')=='mohsenbarari/trading_bot'
            and os.geteuid()==1001,'ci_controller_only')
    require(root.is_absolute() and root.resolve(strict=True)==root and root.is_relative_to(Path(os.environ['RUNNER_TEMP']).resolve(strict=True))
            and root.stat().st_uid==1001 and stat.S_IMODE(root.stat().st_mode)==0o700,'ci_root')
    os.umask(0o077)
    identity=json.loads(read(root/'recipe-identity.json',65536))
    require(set(identity['recipe_sha256'])==RECIPE and all(sha256(read(root/'context'/name,65536)).hexdigest()==digest for name,digest in identity['recipe_sha256'].items()),'sealed_recipe_changed')
    token=os.environ['GITHUB_RUN_ID']+'-'+os.environ['GITHUB_RUN_ATTEMPT'];require(re.fullmatch('[0-9]+-[0-9]+',token),'ci_identity')
    docker=['/usr/bin/docker','--host=unix:///var/run/docker.sock']
    def call(arguments,label,seconds):
        def limits():resource.setrlimit(resource.RLIMIT_FSIZE,(128*1024**2,)*2);resource.setrlimit(resource.RLIMIT_CORE,(0,0))
        with (root/(label+'.log')).open('xb') as output:
            subprocess.run([*docker,*arguments],stdin=subprocess.DEVNULL,stdout=output,stderr=subprocess.STDOUT,check=True,
                           timeout=seconds,preexec_fn=limits,env={'PATH':'/usr/bin:/bin','LC_ALL':'C.UTF-8','DOCKER_CONFIG':str(root/'docker-config')})
    def image(path):
        value=read(path,256).decode().strip();require(re.fullmatch('sha256:[a-f0-9]{64}',value),'immutable_image_required');return value
    def cleanup(cid,purpose):
        call(['inspect','--format','{{index .Config.Labels "io.gold-trade.acl-tar-owner"}}',cid],purpose+'-owner',15)
        require(read(root/(purpose+'-owner.log'),256).decode().strip()==token,'cleanup_owner_changed')
        call(['rm','--force',cid],purpose+'-remove',20)
    def container(base,purpose,arguments,capsule_path=None,compiler=False):
        name='trixie-acl-tar-'+purpose+'-'+token;cid=None
        args=['create','--name',name,'--label','io.gold-trade.acl-tar-owner='+token,'--network=none','--cap-drop=ALL',
              '--security-opt=no-new-privileges','--cpus=2','--memory='+('4g' if compiler else '1g'),
              '--memory-swap='+('4g' if compiler else '1g'),'--pids-limit='+('512' if compiler else '128'),'--ulimit','core=0']
        if compiler:
            work=root/(purpose+'-work');work.mkdir(mode=0o700)
            args+=['--entrypoint','/usr/local/bin/python3','--user=1001:1001','--read-only','--tmpfs','/tmp:rw,nosuid,nodev,size=256m,uid=1001,gid=1001',
                   '--mount','type=bind,source='+str(work)+',target=/work']
            # docker commit retains the installer entrypoint and command.
            # Explicitly select the sealed helper in both compiler phases.
            arguments=['-I','-B','/recipe/build.py',*arguments]
            if capsule_path:args+=['--mount','type=bind,source='+str(capsule_path)+',target=/acl,readonly']
        else:
            args+=['--user=0:0',*['--cap-add='+cap for cap in ('CHOWN','FOWNER','FSETID','SETUID','SETGID')],
                   '--tmpfs','/tmp:rw,nosuid,nodev,size=128m','--env','DEBIAN_FRONTEND=noninteractive','--env','NEEDRESTART_MODE=l',
                   '--mount','type=bind,source='+str(capsule_path)+',target=/packages,readonly','--entrypoint','/usr/local/bin/python3']
        try:
            call([*args,base,*arguments],purpose+'-create',30)
            cid=read(root/(purpose+'-create.log'),256).decode().strip();require(re.fullmatch('[a-f0-9]{64}',cid),'container_identity')
            call(['start','--attach',cid],purpose+'-run',4950 if compiler else 180)
            call(['inspect','--format','{{.State.ExitCode}}',cid],purpose+'-exit',15)
            require(read(root/(purpose+'-exit.log'),64).strip()==b'0','container_failed')
            if not compiler:
                call(['commit','--pause=false',cid],purpose+'-image',90)
                return image(root/(purpose+'-image.log'))
        finally:
            if cid:cleanup(cid,purpose)
    space=os.statvfs('/var/lib/docker');require(space.f_bavail*space.f_frsize>=12*1024**3,'ci_space')
    for target in ('runtime','builder'):
        call(['build','--pull=false','--target',target,'--iidfile',str(root/(target+'.id')),str(root/'context')],target+'-prepare',1500)
    runtime,builder=(image(root/(target+'.id')) for target in ('runtime','builder'))
    container(builder,'acl',[builder,'acl'],compiler=True)
    acl_raw,acl=checked_receipt(root/'acl-work/artifacts/build-receipt.json','acl')
    require(acl['recipe_sha256']==identity['recipe_sha256'],'acl_recipe_changed')
    acl_capsule=capsule(root/'acl-work/artifacts',acl,root/'acl-public',{'libacl1','libacl1-dev'})
    coherent=container(builder,'toolchain',['-I','-B','-c',INSTALL,'toolchain'],acl_capsule)
    provenance={'schema':'acl_tar_compiler_layer/1','image_id':coherent,'parent_builder_id':builder,
                'acl_build_receipt_sha256':sha256(acl_raw).hexdigest(),'compiler_only':True,'production_installed':False,'security_accepted':False}
    (root/'reports/compiler-toolchain.json').write_text(json.dumps(provenance,sort_keys=True)+'\n')
    container(coherent,'tar',[coherent,'tar'],acl_capsule,compiler=True)
    _,tar=checked_receipt(root/'tar-work/artifacts/build-receipt.json','tar')
    require(tar['recipe_sha256']==identity['recipe_sha256'],'tar_recipe_changed');publish(root,acl,tar)
    with Path(os.environ['GITHUB_OUTPUT']).open('a') as output:output.write('packages_ready=true\n')
    final_capsule=capsule(root/'tar-work/artifacts',tar,root/'runtime-public',RUNTIME)
    candidate=container(runtime,'runtime',['-I','-B','-c',INSTALL,'runtime'],final_capsule)
    (root/'reports/candidate-image.json').write_text(json.dumps({'image_id':candidate,'runtime_base_id':runtime,
        'compiler_layer':provenance,'security_accepted':False,'production_installed':False},sort_keys=True)+'\n')
    with Path(os.environ['GITHUB_OUTPUT']).open('a') as output:output.write('image_id='+candidate+'\n')

if __name__=='__main__':
    try:
        if sys.argv[1:]==['--verify-inputs']:
            require(os.geteuid()==0 and Path(__file__).resolve()==Path('/recipe/build.py'),'preparation_container_only');validate_inputs(Path('/inputs'))
        elif sys.argv[1:2]==['--ci-root']:ci_build(Path(sys.argv[2]))
        else:run(sys.argv[1],sys.argv[2])
    except Exception as error:
        value=error.args[0] if type(error) is RuntimeError and error.args else type(error).__name__
        print(json.dumps({'status':'failed','code':value if isinstance(value,str) and re.fullmatch('[a-zA-Z0-9_]{1,96}',value) else 'unexpected_failure',
                          'security_accepted':False,'production_installed':False}))
        raise SystemExit(1) from None
