"""Compose one existing public App image with fifteen fixed runtime DEBs in CI."""
from contextlib import contextmanager
from hashlib import sha1, sha256
import argparse
import base64
import gzip
import io
import json
import os
from pathlib import Path
import re
import resource
import selectors
import signal
import stat
import subprocess
import sys
import tarfile
import time
from types import ModuleType
from zipfile import ZipFile

IMAGE = 'sha256:3b4f58b071f3a7c51778e1f6a1ae66d03beaa401fdefee6f8fd2af69999512cf'
SOURCE = '19d4c2da778f8f1edc567db4c9616c2d153980c9'
BASELINE = 'ba1f7616ade57558f51bb3a7fbb903243a33aab4'
EVIDENCE = (12553191740, 190111, '976a32a2c5284b27cf1f007992495d1a2353f0ca957133916f2fd27871d7d30a')
TEST_PROOF = '39826fc608153f0ce34cd0a48cfe5e6712914e2d157c8c5475c4262445319593'
PROBE_SHA = '36e92fdfd3105a9db6bc20fd75aade1238a5e0b053a581bfb24c7b5e1bf6a6d3'
ACL_PROBE_SHA = '001e51d270c079153cc38db1be53c723fbbc8a8bb9e981e718e7b657127bc778'
UTIL_VERSION = '2.41.6-0+deb13u0+gold1'
NCURSES_VERSION = '6.6+20260608-2~trixie+gold1'
PACKAGES = {
    'bsdutils': ('1:'+UTIL_VERSION, 110376, 'b7041ab345345cfa9fc4902ae617d39a2eb2c59b24227cc5f88793adb8c78abf'),
    'libblkid1': (UTIL_VERSION, 171904, 'b4664ba6da38424714dd38c9b37b466d175bf15d7bbbc33f5f9c512771b81038'),
    'liblastlog2-2': (UTIL_VERSION, 28928, 'fafeb6f0d621c94bc2d8db00b6797977b6f7438937307830fd55a0a542341ae4'),
    'libmount1': (UTIL_VERSION, 210776, '111baa6313bd1e72002d99725bf2f82e07d8dafd282269e005d5600aa616aaca'),
    'libsmartcols1': (UTIL_VERSION, 143624, '49e6fd1f2433405e11078fb7de3eba438252f93586c6eb8baaeee03dfe93ab67'),
    'libuuid1': (UTIL_VERSION, 37988, '418905541325eab71d991d8ad252940cc2686fa2c39da387485acfef14e8140c'),
    'login': ('1:4.16.0-2+really'+UTIL_VERSION, 114936, '37bcd2e3c6f4c3096e95a17f32a96e14de92b8305c3bb4013fc37c6c6a687ef8'),
    'mount': (UTIL_VERSION, 163408, '8e38ae7990a2bba1a0e3b556da640c874f271ffa2e840f66da459c5b336ae45f'),
    'util-linux': (UTIL_VERSION, 1243300, 'a5670cae12a53439815e45666c7dc31cc7f413b1c9a644fb4f4e54a95e68da6b'),
    'libncursesw6': (NCURSES_VERSION, 136976, '5f86354c76eee6802131050780b843a74bf266a60811a8364f110fc0030624a7'),
    'libtinfo6': (NCURSES_VERSION, 354996, '6011b4d5a67960dea434e3c80ab350402167453c8b35bc7ba51644a7031bc392'),
    'ncurses-base': (NCURSES_VERSION, 279592, '6041a72d67e6161825b5ede36314420787e4f7c77e254e38c4896d3d4e9c8bd2'),
    'ncurses-bin': (NCURSES_VERSION, 447776, '93e5cf6a4bf63721e772d22ef7f24db794be0710c278341487ae63283fbef9a5'),
    'libacl1': ('2.4.0-1~trixie+gold1', 37076, 'bbb12e190bfe19b876f73a669848ebd8d04293d95000babf51058c88879966b1'),
    'tar': ('1.35+dfsg-5~trixie+gold1', 814984, 'b6a5715adc801680c94ab653641668d03f429dddc1efa822015b6395482195c4'),
}
GROUPS = {
    'util': (40112666640, 12567076825, 2362423, 'a53a204c2ad0b04d2526811eaeb2fa40e0799d720c3d57a9650db9ada4279e8b',
             '3a799ad92549c9d3c5e18013463743d9bc2b721eb83557e5ee3a0273d4514dde', set(list(PACKAGES)[:9]), 19),
    'ncurses': (40112668891, 12567123180, 1319510, '5d4f181112b2965d177c10e3923b2ba2ff4d2b0ee6716818acc9b7f784bfee08',
                '0903c9fbcd69d5dbc6e381f2c84d2718f85899e23c375de2e1c8ec57e81483fd', set(list(PACKAGES)[9:13]), 11),
    'acl-tar': (40115339187, 12567979094, 933904, 'd367fb3c1b92f069538a6e90a77ea110efec6dccd9286a8f0e1359b285bc1765',
                'c0114c344ec6103c1d8053421f5e63dc06721b16d8e318f21cde95608d9b465c', {'libacl1','tar'}, 16),
}
TESTS = {
    'test_chat_upload_image_security.py': (8239,'81aeb78631fd992ca12e848a10c3ed34bd2ae3c2','4ed366db4d981d943f5cbabe6353ed90f565d82611312bf3a91417b9f5244199'),
    'test_jwt_codec_security.py': (3624,'35714a892d93b3e9e7659c77cb6fcdb7215a5d4f','27233b5f60e6b2c208351c2f774394c5689f01828a3ac127cd67960592c0224b'),
    'test_frontend_static_security.py': (9269,'5e05ddb5af06eac0859ef2ca1743d46cbfada124','a5df5bea2cf701bf10bd29918cc707112e7103a0af4cb9b6fc1549c3555141ed'),
    'test_coin_intelligence_market_store.py': (12472,'b82833057a7b0d201a6ae951c315fb44c1887bee','266b037424438a9112709cebfb51eb81e3371db4170f5cff0d4200af105f66eb'),
    'test_coin_intelligence_coin_rate_engine.py': (26286,'7b94be9e424c290e9738fd8e6566733582a3ddf3','d46ce47853364920c5dd7dcf7648108b1a798d2f0828f9dc0234d833e6f05751'),
    'unit-test.env.example': (685,'faf84c09e6d3e0ead1441ec524ec324b4bfae351','801525a75e9d6d9310311cd7b9ba80141551e7b86cd0e720ddb41a37a6f3a770'),
}
DOCKER = ['/usr/bin/docker','--host=unix:///var/run/docker.sock']
EVIDENCE_REPORTS = {'base-admission.json':2*1024**2,'baseline-inventory.json':2*1024**2,
    'candidate-inventory.json':2*1024**2,'before-run.log':2*1024**2,'after-run.log':2*1024**2,
    'native-run.log':2*1024**2,'focused-run.log':2*1024**2,'focused-tests.json':65536,
    'component-presence.json':131072,'build.log':32*1024**2,'builder-inspect.log':65536,
    'base-alias-plan.json':65536,'baseline-image-identity.json':65536,
    'acl-consumer-presence.json':4*1024**2,'acl-run.log':4*1024**2}
PUBLIC_FAILURE_CODES = frozenset((
    'acl_probe_changed','acl_receipt_bound','acl_receipt_identity','acl_receipt_scope',
    'acl_parent_pin','application_or_python_changed','archive_config','archive_config_bound','archive_config_digest',
    'archive_config_name','archive_manifest','artifact_digest','artifact_download_failed','artifact_size','base_admission_scope',
    'base_alias','base_alias_changed','base_alias_cleanup_owner','base_alias_cleanup_incomplete','base_alias_plan_missing','base_archive_changed','base_daemon_layers_changed',
    'base_user_or_onbuild','baseline_layer_prefix_changed','builder_inspect_shape','candidate_identity','ci_identity','ci_root',
    'combined_package_inventory','command_failed','command_output_bound','command_timeout','component_receipt_bound',
    'component_receipt_identity','component_receipt_scope','composition_baseline_changed','composition_evidence_changed',
    'composition_image_changed','composition_state','compressed_export_bound','container_cleanup_owner','container_cleanup_unknown',
    'container_exit','container_identity','controller_source_changed','document_invalid','download_exists','export_byte_bound',
    'export_failed','export_timeout','focused_test_result','github_ci_only','image_identity','image_inspect','input_bound',
    'input_changed','input_metadata','input_path','input_unavailable','installed_delta_changed','installed_version_changed',
    'io_failure','libc_changed','local_docker_builder_required','package_bytes','package_path','package_pin','package_receipt_pin',
    'package_receipt_state','package_runtime_inventory','package_zip_identity','package_zip_inventory','package_zip_member',
    'phase_invalid','phase_state_invalid','probe_changed','raw_report_config','raw_report_identity','runner_space',
    'runtime_config_changed','sbom_image_identity','sbom_package_version','sealed_inventory','sealed_source_changed','stream_bound',
    'stream_size','test_blob_identity','test_blob_metadata','test_evidence_pin','test_proof_identity','test_proof_inventory',
    'test_proof_pin','test_source_pin','unclassified_refusal','unexpected_failure'))
PUBLIC_PHASES = frozenset(('controller_admission','sealed_sources','download_image',
    'download_util','admit_util','download_ncurses','admit_ncurses','download_acl-tar','admit_acl-tar',
    'download_test_evidence','admit_test_evidence','fetch_test_sources','base_image_admission','base_archive_config',
    'load_base_image','inspect_base_image','audit_base_image','create_base_alias','inspect_base_alias','inspect_builder',
    'write_build_context','build_candidate','cleanup_base_alias','inspect_candidate_image','audit_candidate_image',
    'native_checks','focused_tests','component_probe','acl_consumer_probe','bind_composition_evidence','composition_complete',
    'finalize_admission','inspect_final_candidate','inspect_final_baseline','read_final_base_archive',
    'bind_raw_reports','export_image','export_complete'))


def require(ok, code):
    if not ok: raise RuntimeError(code)


@contextmanager
def pinned(path, maximum):
    require(path.is_absolute() and path.resolve(strict=True)==path,'input_path')
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        before=os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_uid==os.geteuid() and before.st_nlink==1
                and stat.S_IMODE(before.st_mode)==0o600 and 0<before.st_size<=maximum,'input_metadata')
        identity=lambda s:(s.st_dev,s.st_ino,s.st_size,s.st_mode,s.st_uid,s.st_nlink,s.st_mtime_ns,s.st_ctime_ns)
        require(identity(before)==identity(path.lstat()),'input_changed')
        with os.fdopen(os.dup(fd),'rb') as stream: yield stream,before.st_size
        require(identity(before)==identity(os.fstat(fd))==identity(path.lstat()),'input_changed')
    finally: os.close(fd)


def read(path, maximum=1024**2):
    with pinned(path,maximum) as (stream,size):
        raw=stream.read(size+1);require(len(raw)==size,'input_bound');return raw


def write(path,raw,mode=0o600):
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,mode)
    with os.fdopen(fd,'wb') as stream: stream.write(raw);stream.flush();os.fchmod(stream.fileno(),mode);os.fsync(stream.fileno())


def encoded(value): return (json.dumps(value,sort_keys=True)+'\n').encode()


def public_error(error):
    if type(error) is RuntimeError:
        return error.args[0] if len(error.args)==1 and type(error.args[0]) is str and error.args[0] in PUBLIC_FAILURE_CODES else 'unclassified_refusal'
    if isinstance(error,subprocess.TimeoutExpired):return 'command_timeout'
    if isinstance(error,FileNotFoundError):return 'input_unavailable'
    if isinstance(error,(UnicodeError,json.JSONDecodeError)):return 'document_invalid'
    if isinstance(error,OSError):return 'io_failure'
    return 'unexpected_failure'


def phase_state(root):
    path=root/'reports/phase.json'
    if not path.exists():return {}
    value=json.loads(read(path,65536))
    require(value.get('schema')=='security_os_composition_phase/1' and value.get('phase') in PUBLIC_PHASES
        and value.get('status') in {'running','complete','failed'}
        and value.get('first_failure_phase') in PUBLIC_PHASES|{None}
        and value.get('first_failure_code') in PUBLIC_FAILURE_CODES|{None},'phase_state_invalid')
    return value


def save_phase(root,value):
    reports=root/'reports';reports.mkdir(mode=0o700,exist_ok=True)
    info=reports.lstat()
    require(reports.resolve(strict=True)==reports and stat.S_ISDIR(info.st_mode) and info.st_uid==os.geteuid()
        and stat.S_IMODE(info.st_mode)==0o700,'phase_state_invalid')
    temporary=reports/('.phase-'+str(os.getpid())+'-'+str(time.monotonic_ns())+'.json')
    try:
        write(temporary,encoded(value));os.replace(temporary,reports/'phase.json')
        descriptor=os.open(reports,os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(descriptor)
        finally:os.close(descriptor)
    finally:temporary.unlink(missing_ok=True)


def phase(root,name,*,complete=False):
    require(name in PUBLIC_PHASES,'phase_invalid');previous=phase_state(root)
    save_phase(root,{'schema':'security_os_composition_phase/1','phase':name,'status':'complete' if complete else 'running',
        'first_failure_phase':previous.get('first_failure_phase'),'first_failure_code':previous.get('first_failure_code'),
        'updated_at_epoch_seconds':time.time(),'security_accepted':False,'deployment_performed':False})


def failed_phase(root,error):
    previous=phase_state(root);name=previous.get('phase','controller_admission')
    code=public_error(error)
    value={'schema':'security_os_composition_phase/1','phase':name,'status':'failed',
        'first_failure_phase':previous.get('first_failure_phase') or name,
        'first_failure_code':previous.get('first_failure_code') or code,'last_failure_code':code,
        'updated_at_epoch_seconds':time.time(),'security_accepted':False,'deployment_performed':False}
    save_phase(root,value);return value


def stream_digest(stream,size):
    value=sha256();total=0
    while block:=stream.read(min(1024**2,size+1-total)):
        total+=len(block);require(total<=size,'stream_bound');value.update(block)
    require(total==size,'stream_size');return value.hexdigest()


def cleanup_owned_child(child):
    # The unreaped direct child is still kernel-owned; never signal after wait.
    if child.returncode is not None: return
    try: os.waitid(os.P_PID,child.pid,os.WEXITED|os.WNOHANG|os.WNOWAIT)
    except ChildProcessError: return
    try: os.killpg(child.pid,signal.SIGKILL)
    except ProcessLookupError: pass
    child.wait(timeout=10)


def command(argv, *, seconds=30, maximum=1024**2, input_stream=None, environment=None,
            combine_stderr=False, log_path=None):
    env={'PATH':'/usr/bin:/bin','LC_ALL':'C.UTF-8',**(environment or {})}
    child=subprocess.Popen(argv,stdin=subprocess.DEVNULL if input_stream is None else input_stream,
                           stdout=subprocess.PIPE,stderr=subprocess.STDOUT if combine_stderr else subprocess.DEVNULL,
                           env=env,start_new_session=True)
    deadline=time.monotonic()+seconds;out=bytearray();complete=False
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(child.stdout,selectors.EVENT_READ)
            while selector.get_map():
                require(time.monotonic()<deadline,'command_timeout')
                for key,_ in selector.select(.1):
                    block=os.read(key.fileobj.fileno(),min(65536,maximum+1-len(out)))
                    if not block:selector.unregister(key.fileobj)
                    else:out.extend(block);require(len(out)<=maximum,'command_output_bound')
        require(child.wait(timeout=max(.01,deadline-time.monotonic()))==0,'command_failed');complete=True
        return bytes(out)
    finally:
        try:cleanup_owned_child(child)
        finally:
            child.stdout.close()
            if log_path is not None:write(log_path,bytes(out[:maximum]))


def fetch_artifact(identifier,path,size,digest):
    require(not path.exists(),'download_exists')
    def limits():resource.setrlimit(resource.RLIMIT_FSIZE,(size+1,)*2);resource.setrlimit(resource.RLIMIT_CORE,(0,0))
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb') as stream:
        child=subprocess.Popen(['gh','api','--method','GET',f'repos/mohsenbarari/trading_bot/actions/artifacts/{identifier}/zip'],
            stdin=subprocess.DEVNULL,stdout=stream,stderr=subprocess.DEVNULL,start_new_session=True,preexec_fn=limits,
            env={'PATH':'/usr/bin:/bin','GH_TOKEN':os.environ['GH_TOKEN']})
        try:require(child.wait(timeout=300)==0,'artifact_download_failed')
        finally:cleanup_owned_child(child)
        stream.flush();os.fsync(stream.fileno())
    with pinned(path,size) as (source,observed):
        require(observed==size,'artifact_size')
        require(stream_digest(source,size)==digest,'artifact_digest')


def admit_packages(path,group,target):
    run,identifier,size,digest,receipt_sha,names,count=GROUPS[group]
    raw=read(path,size);require(len(raw)==size and sha256(raw).hexdigest()==digest,'package_zip_identity')
    with ZipFile(io.BytesIO(raw)) as archive:
        rows=archive.infolist();require(len(rows)==count and len({r.filename for r in rows})==count,'package_zip_inventory')
        require(all(re.fullmatch(r'[A-Za-z0-9+._~-]+',r.filename) and Path(r.filename).name==r.filename and not r.is_dir() and not r.flag_bits&1
                    and stat.S_IFMT(r.external_attr>>16) in {0,stat.S_IFREG} and 0<r.file_size<=32*1024**2 for r in rows),'package_zip_member')
        receipt_raw=archive.read('build-receipt.json');require(sha256(receipt_raw).hexdigest()==receipt_sha,'package_receipt_pin')
        receipt=json.loads(receipt_raw);require(receipt['security_accepted'] is False and receipt['production_installed'] is False
            and receipt['status']=='candidate_built_not_accepted' and receipt['abi']['status']=='passed'
            and set(receipt['selected_runtime_packages'])==names,'package_receipt_state')
        selected=[r for r in receipt['all_family_packages'] if r['name'] in names]
        require(len(selected)==len(names) and {r['name'] for r in selected}==names,'package_runtime_inventory')
        for row in selected:
            version,length,expected=PACKAGES[row['name']]
            require((row['version'],row['bytes'],row['sha256'])==(version,length,expected),'package_pin')
            require(Path(row['path']).name==row['path'] and row['path'].endswith('.deb'),'package_path')
            package=archive.read(row['path']);require(len(package)==length and sha256(package).hexdigest()==expected,'package_bytes')
            write(target/row['path'],package,0o644)
        if group=='acl-tar':
            require(sha256(archive.read('acl-build-receipt.json')).hexdigest()==receipt['acl_build_receipt_sha256']
                =='04a7e8f89a037d5a471c46d604d796b63f98ce9b6a0e0a01b450dc3af977e029','acl_parent_pin')
    return {'run':run,'artifact':identifier,'zip_sha256':digest,'receipt_sha256':receipt_sha,'packages':selected}


def admit_test_proof(path):
    with pinned(path,EVIDENCE[1]) as (stream,size):
        raw=stream.read(size+1);require(len(raw)==EVIDENCE[1] and sha256(raw).hexdigest()==EVIDENCE[2],'test_evidence_pin')
        with ZipFile(io.BytesIO(raw)) as archive: proof=archive.read('test-sources.json')
    require(sha256(proof).hexdigest()==TEST_PROOF,'test_proof_pin')
    value=json.loads(proof);require(value['source_commit']==SOURCE and value['baseline_commit']==BASELINE
        and value['runtime_data_included'] is False and len(value['files'])==6,'test_proof_identity')
    rows={r['name']:r for r in value['files']};require(set(rows)==set(TESTS),'test_proof_inventory')
    for name,(length,blob,digest) in TESTS.items():
        row=rows[name];origin=BASELINE if name.startswith('test_coin_intelligence_') else SOURCE
        require((row['bytes'],row['git_blob'],row['sha256'],row['source_commit'])==(length,blob,digest,origin),'test_source_pin')
    return proof


def fetch_tests(target):
    for name,(length,blob,digest) in TESTS.items():
        raw=command(['gh','api',f'repos/mohsenbarari/trading_bot/git/blobs/{blob}'],maximum=length*2+4096,
                    environment={'GH_TOKEN':os.environ['GH_TOKEN']})
        record=json.loads(raw);require(record['sha']==blob and record['size']==length and record['encoding']=='base64','test_blob_metadata')
        source=base64.b64decode(record['content'].replace('\n',''),validate=True)
        require(len(source)==length and sha256(source).hexdigest()==digest
                and sha1(b'blob '+str(length).encode()+b'\0'+source).hexdigest()==blob,'test_blob_identity')
        write(target/name,source,0o644)


INSTALLER = r'''
import hashlib,json,os,pathlib,stat,subprocess
root=pathlib.Path('/capsule')
def bounded(path,maximum):
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        before=os.fstat(fd)
        assert stat.S_ISREG(before.st_mode) and before.st_nlink==1 and not before.st_mode&0o022 and 0<before.st_size<=maximum
        raw=os.read(fd,maximum+1)
        identity=lambda s:(s.st_dev,s.st_ino,s.st_size,s.st_mode,s.st_uid,s.st_nlink,s.st_mtime_ns,s.st_ctime_ns)
        assert len(raw)==before.st_size and identity(before)==identity(os.fstat(fd))==identity(path.lstat())
        return raw
    finally:os.close(fd)
status=dict(line.split(':',1) for line in pathlib.Path('/proc/self/status').read_text().splitlines() if ':' in line)
assert os.geteuid()==0 and status['NoNewPrivs'].strip()=='1'
assert all(int(status[key].strip(),16)==0xd9 for key in ('CapEff','CapPrm','CapBnd'))
assert all(int(status[key].strip(),16)==0 for key in ('CapInh','CapAmb'))
manifest=json.loads(bounded(root/'manifest.json',65536));rows=manifest['packages']
assert manifest['schema']=='security_os_composition_packages/1' and len(rows)==15
names={row['name'] for row in rows};assert len(names)==15
assert {p.name for p in root.iterdir()}=={'manifest.json'}|{r['path'] for r in rows}
paths=[]
env={**os.environ,'LC_ALL':'C.UTF-8','DEBIAN_FRONTEND':'noninteractive','NEEDRESTART_MODE':'l'}
def run(argv,**kwargs):return subprocess.run(argv,check=True,timeout=120,env=env,stdin=subprocess.DEVNULL,**kwargs)
for row in rows:
    path=root/row['path'];assert path.parent==root and path.is_absolute()
    raw=bounded(path,32*1024**2);assert len(raw)==row['bytes'] and hashlib.sha256(raw).hexdigest()==row['sha256']
    fields=run(['/usr/bin/dpkg-deb','-f',str(path),'Package','Version','Architecture'],capture_output=True).stdout.decode()
    fields=dict(line.split(': ',1) for line in fields.splitlines())
    assert fields['Package']==row['name'] and fields['Version']==row['version'] and fields['Architecture'] in {'all','amd64'}
    paths.append(str(path))
def inventory():
    raw=run(['/usr/bin/dpkg-query','-W','-f=${Package}\t${Version}\t${db:Status-Status}\n'],capture_output=True).stdout.decode()
    items=[line.split('\t') for line in raw.splitlines()];assert all(len(row)==3 and row[2]=='installed' for row in items)
    value={name.removesuffix(':amd64'):version for name,version,_ in items};assert len(value)==len(items);return value
before=inventory();assert names<=set(before)
with open('/tmp/os-compose-simulation','xb') as output:
    run(['/usr/bin/apt-get','--simulate','--no-remove','--no-download','install',*paths],stdout=output,stderr=output)
plan=bounded(pathlib.Path('/tmp/os-compose-simulation'),65536).decode()
assert not any(line.startswith('Remv ') for line in plan.splitlines())
upgrades=[line.split()[1].removesuffix(':amd64') for line in plan.splitlines() if line.startswith('Inst ')]
assert len(upgrades)==15 and set(upgrades)==names
# Real APT must acquire the absolute LOCAL paths; --no-download prevents that.
# RUN --network=none provides the kernel network boundary.
run(['/usr/bin/apt-get','--yes','--no-remove','install',*paths])
after=inventory();assert set(after)==set(before) and before['libc6']==after['libc6']
assert {name for name in before if before[name]!=after[name]}==names
assert all(after[row['name']]==row['version'] for row in rows)
audit=run(['/usr/bin/dpkg','--audit'],capture_output=True);assert not audit.stdout.strip()
'''


AUDIT = r'''
import hashlib,json,os,pathlib,stat,subprocess
raw=subprocess.run(['/usr/bin/dpkg-query','-W','-f=${Package}\t${Version}\t${db:Status-Status}\n'],check=True,capture_output=True,timeout=20).stdout.decode()
rows=[line.split('\t') for line in raw.splitlines()];assert all(len(row)==3 and row[2]=='installed' for row in rows)
inventory={name.removesuffix(':amd64'):version for name,version,_ in rows};assert len(inventory)==len(rows)
digest=hashlib.sha256();count=total=0
for root in ('/app','/usr/local'):
    for folder,dirs,files in os.walk(root,followlinks=False):
        dirs.sort();files.sort()
        for name in sorted(dirs+files):
            path=pathlib.Path(folder)/name;info=path.lstat();count+=1;assert count<=60000
            digest.update(json.dumps([str(path),info.st_mode,info.st_uid,info.st_gid,info.st_size if not stat.S_ISDIR(info.st_mode) else 0],ensure_ascii=True).encode())
            if stat.S_ISLNK(info.st_mode):digest.update(os.readlink(path).encode())
            elif stat.S_ISREG(info.st_mode):
                total+=info.st_size;assert total<=768*1024**2
                with path.open('rb') as source:
                    while block:=source.read(65536):digest.update(block)
            else:assert stat.S_ISDIR(info.st_mode)
libc=hashlib.sha256(pathlib.Path('/usr/lib/x86_64-linux-gnu/libc.so.6').read_bytes()).hexdigest()
print(json.dumps({'inventory':inventory,'app_and_python_sha256':digest.hexdigest(),'files':count,'bytes':total,'libc_sha256':libc},sort_keys=True))
'''


FOCUSED = r'''
import json,os,pathlib,stat,sys,tempfile,types,unittest
from hashlib import sha1,sha256
from contextlib import ExitStack
from unittest.mock import patch
image=sys.argv[1];folder=pathlib.Path('/app/tests')
raw=pathlib.Path('/proof/test-sources.json').read_bytes()
assert len(raw)==2054 and sha256(raw).hexdigest()=='39826fc608153f0ce34cd0a48cfe5e6712914e2d157c8c5475c4262445319593'
proof=json.loads(raw);assert proof['source_commit']=='19d4c2da778f8f1edc567db4c9616c2d153980c9' and proof['baseline_commit']=='ba1f7616ade57558f51bb3a7fbb903243a33aab4' and proof['runtime_data_included'] is False
rows={row['name']:row for row in proof['files']};assert len(rows)==6 and {p.name for p in folder.iterdir()}==set(rows)
sources={}
for name,row in rows.items():
    path=folder/name;info=path.lstat();assert stat.S_ISREG(info.st_mode) and info.st_nlink==1 and not info.st_mode&0o022 and info.st_size==row['bytes']<=1048576
    raw=path.read_bytes();assert len(raw)==row['bytes'] and sha256(raw).hexdigest()==row['sha256']
    assert sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()==row['git_blob']
    if name.endswith('.py'):sources[name]=raw
    else:configuration=raw
# Preserve the original trusted libmagic loader bootstrap. The absolute
# host/network fence begins before any application or test module is imported.
import magic
with tempfile.TemporaryDirectory(prefix='composition-public-env-') as private:
    config=pathlib.Path(private)/'unit-test.env.example';config.write_bytes(configuration);config.chmod(0o600)
    os.environ['APP_ENV_FILE']=str(config);sys.path[:0]=['/app','/app/tests']
    denied=lambda *a,**k:(_ for _ in ()).throw(AssertionError('network/host command forbidden'))
    with ExitStack() as fence:
        for target in ('socket.socket.connect','socket.create_connection','socket.getaddrinfo','subprocess.run','subprocess.Popen','urllib.request.urlopen','urllib.request.OpenerDirector.open'):
            fence.enter_context(patch(target,side_effect=denied))
        suite=unittest.TestSuite()
        for name,raw in sources.items():
            module=types.ModuleType(name[:-3]);module.__file__=str(folder/name);sys.modules[module.__name__]=module
            exec(compile(raw,module.__file__,'exec'),module.__dict__)
            tests=unittest.defaultTestLoader.loadTestsFromModule(module);assert tests.countTestCases()>0;suite.addTests(tests)
        result=unittest.TextTestRunner(verbosity=2,stream=sys.stderr).run(suite)
    assert result.wasSuccessful() and not result.skipped and result.testsRun==56 and len(sources)==5
print(json.dumps({'schema':'security_os_composition_focused_tests/1','image_id':image,'tests':56,'modules':5,'status':'passed','test_sources_sha256':'39826fc608153f0ce34cd0a48cfe5e6712914e2d157c8c5475c4262445319593','full_runtime_matrix':'NOT_TESTED','runtime_data_used':False},sort_keys=True))
'''

NATIVE = r'''
import runpy,sys
for path,args in (('/app/scripts/verify_image_trust.py',['--require-certifi']),('/app/scripts/verify_psycopg2_runtime.py',['--final-runtime']),('/app/scripts/verify_sqlite_runtime.py',['--require-libmagic'])):
    sys.argv=[path,*args]
    try:runpy.run_path(path,run_name='__main__')
    except SystemExit as error:assert error.code in (None,0)
'''


def dockerfile_for(alias):
    require(re.fullmatch(r'security-os-base-[0-9]+-[0-9]+:ci',alias),'base_alias')
    caps='--bounding-set=-all,+chown,+fowner,+fsetid,+setuid,+setgid'
    return ('FROM '+alias+'\nRUN --network=none --mount=type=bind,source=capsule,target=/capsule,ro '
        '--mount=type=bind,source=installer.py,target=/compose-installer.py,ro --mount=type=tmpfs,target=/tmp '
        +json.dumps(['/usr/bin/setpriv',caps,'--inh-caps=-all','--ambient-caps=-all','--no-new-privs',
                    '/usr/local/bin/python3','-I','-B','/compose-installer.py'])+'\n')


def inspect_builder(root):
    # buildx inspect has no --format option. Keep its bounded documented text.
    raw=docker(root,['buildx','inspect','default'],maximum=65536,combine_stderr=True,
               log_path=root/'reports/builder-inspect.log')
    parse_builder(raw)
    return raw


def parse_builder(raw):
    require(type(raw) is bytes and 0<len(raw)<=65536,'builder_inspect_shape')
    lines=raw.decode('utf-8').splitlines()
    values=lambda key:[line.split(':',1)[1].strip() for line in lines if line.startswith(key+':')]
    names,drivers,statuses=values('Name'),values('Driver'),values('Status')
    require(names and names[0]=='default' and drivers==['docker'] and statuses
        and all(value=='running' for value in statuses)
        and not any(line.lstrip().startswith('Error:') for line in lines),'local_docker_builder_required')


def image_record(raw, expected=None):
    values=json.loads(raw);require(type(values) is list and len(values)==1,'image_inspect')
    value=values[0]
    require(re.fullmatch(r'sha256:[a-f0-9]{64}',value.get('Id','')) and (expected is None or value['Id']==expected)
        and value.get('Os')=='linux' and value.get('Architecture')=='amd64' and type(value.get('Config')) is dict
        and value.get('RootFS',{}).get('Type')=='layers' and type(value['RootFS'].get('Layers')) is list
        and all(re.fullmatch(r'sha256:[a-f0-9]{64}',item) for item in value['RootFS']['Layers']),'image_identity')
    return value


def archive_config(path,image,*,include_tags=False):
    with pinned(path,2*1024**3) as (stream,_),tarfile.open(fileobj=stream,mode='r:') as archive:
        member=archive.getmember('manifest.json');require(member.isfile() and 0<member.size<=2*1024**2,'archive_manifest')
        manifest=json.loads(archive.extractfile(member).read(member.size+1))
        require(type(manifest) is list and len(manifest)==1,'archive_manifest')
        name=manifest[0]['Config'];require(name in {image[7:]+'.json','blobs/sha256/'+image[7:]},'archive_config_name')
        member=archive.getmember(name);require(member.isfile() and 0<member.size<=2*1024**2,'archive_config_bound')
        raw=archive.extractfile(member).read(member.size+1)
        require(len(raw)==member.size and sha256(raw).hexdigest()==image[7:],'archive_config_digest')
        value=json.loads(raw);require(type(value.get('config')) is dict,'archive_config')
        return (value,manifest[0].get('RepoTags')) if include_tags else value


def compare_images(before,after):
    require(after['Id']!=before['Id'] and after['Config']==before['Config'],'runtime_config_changed')
    base,new=before['RootFS']['Layers'],after['RootFS']['Layers']
    require(0<len(base)<len(new) and new[:len(base)]==base,'baseline_layer_prefix_changed')


def compare_inventory(before,after):
    left,right=before['inventory'],after['inventory']
    require(set(left)==set(right) and {n for n in left if left[n]!=right[n]}==set(PACKAGES),'installed_delta_changed')
    require(all(right[name]==row[0] for name,row in PACKAGES.items()),'installed_version_changed')
    require(left['libc6']==right['libc6']=='2.41-12+deb13u4' and before['libc_sha256']==after['libc_sha256'],'libc_changed')
    require(all(before[k]==after[k] for k in ('app_and_python_sha256','files','bytes')),'application_or_python_changed')


def docker(root,arguments,**kwargs):
    return command([*DOCKER,*arguments],environment={'DOCKER_CONFIG':str(root/'docker-config')},**kwargs)


def base_alias_plan():
    owner=os.environ['GITHUB_RUN_ID']+'-'+os.environ['GITHUB_RUN_ATTEMPT']
    require(re.fullmatch('[0-9]+-[0-9]+',owner),'ci_identity')
    return {'schema':'security_os_composition_base_alias/1','name':'security-os-base-'+owner+':ci',
            'image_id':IMAGE,'owner':owner,'recipe_commit':os.environ['GITHUB_SHA']}


def cleanup(root):
    ci_root(root)
    path=root/'reports/base-alias-plan.json'
    if not path.exists():
        require(not (root/'reports/composition.json').exists() and not (root/'image-export.json').exists(),'base_alias_plan_missing')
        return
    load_sealed(root);plan=json.loads(read(path,65536))
    require(plan==base_alias_plan(),'base_alias_cleanup_owner')
    phase(root,'cleanup_base_alias');name=plan['name']
    observed=docker(root,['image','ls','--quiet','--no-trunc',name],maximum=256).decode().splitlines()
    status='absent_at_observation'
    if observed:
        require(observed==[IMAGE],'base_alias_cleanup_owner')
        image_record(docker(root,['image','inspect',name],maximum=2*1024**2),IMAGE)
        # This can remove the baseline image's final tag and its image record.
        # Every baseline consumer and the candidate export must already be done.
        docker(root,['image','rm','--no-prune',name],seconds=20)
        require(not docker(root,['image','ls','--quiet','--no-trunc',name],maximum=256).strip(),'base_alias_cleanup_incomplete')
        status='removed_owned_alias'
    write(root/'reports/base-alias-cleanup.json',encoded({'schema':'security_os_composition_base_alias_cleanup/1',
        'name':name,'image_id':IMAGE,'owner':plan['owner'],'status':status,'cancellation_proven':False,
        'security_accepted':False,'deployment_performed':False}))
    phase(root,'cleanup_base_alias',complete=True)


def cleanup_isolated(root,target,name,token,image):
    try:
        values=json.loads(docker(root,['inspect',target],maximum=2*1024**2))
        require(type(values) is list and len(values)==1,'container_cleanup_unknown')
        value=values[0]
    except (ValueError,KeyError,TypeError):
        raise RuntimeError('container_cleanup_unknown') from None
    require(value.get('Name')=='/'+name and value.get('Image')==image
        and value.get('Config',{}).get('Labels',{}).get('io.gold-trade.os-compose-owner')==token
        and re.fullmatch('[a-f0-9]{64}',value.get('Id',''))
        and (target==name or target==value['Id']),'container_cleanup_owner')
    docker(root,['rm','--force',value['Id']],seconds=20)


def isolated(root,image,program,purpose,*,mounts=(),maximum=2*1024**2):
    token=os.environ['GITHUB_RUN_ID']+'-'+os.environ['GITHUB_RUN_ATTEMPT']
    name='security-os-'+purpose+'-'+token;cid=None
    argv=['create','--name',name,'--label','io.gold-trade.os-compose-owner='+token,
          '--network=none','--read-only','--cap-drop=ALL','--security-opt=no-new-privileges',
          '--memory=1g','--memory-swap=1g','--cpus=2','--pids-limit=128','--ulimit','core=0',
          '--tmpfs','/tmp:rw,nosuid,nodev,noexec,size=128m','--entrypoint','/usr/local/bin/python3']
    for source,target in mounts:argv+=['--mount',f'type=bind,source={source},target={target},readonly']
    try:
        observed=docker(root,[*argv,image,'-I','-B','-c',program,image],maximum=256).decode().strip()
        require(re.fullmatch('[a-f0-9]{64}',observed),'container_identity');cid=observed
        output=docker(root,['start','--attach',cid],seconds=180,maximum=maximum,
                      combine_stderr=True,log_path=root/'reports'/(purpose+'-run.log'))
        require(docker(root,['inspect','--format','{{.State.ExitCode}}',cid],maximum=64).strip()==b'0','container_exit')
        return output
    finally:
        if cid is None:
            # A timed-out create request can materialize after the client dies.
            # This is bounded best effort only; the original failure propagates.
            cleanup='not_confirmed'
            try:cleanup_isolated(root,name,name,token,image);cleanup='removed_owned_container'
            except Exception:pass
            try:write(root/'reports'/(purpose+'-daemon-outcome.json'),encoded({
                'daemon_outcome':'UNKNOWN','best_effort_cleanup':cleanup,'name':name,'image_id':image,
                'cancellation_proven':False,'security_accepted':False}))
            except OSError:pass
        else:
            cleanup_isolated(root,cid,name,token,image)


def validate_component(raw,image):
    require(0<len(raw)<=131072,'component_receipt_bound');value=json.loads(raw)
    require(value.get('schema')=='security_image_component_presence/1' and value.get('image_id')==image
        and value.get('source_commit')==SOURCE and value.get('verifier_sha256')==PROBE_SHA,'component_receipt_identity')
    require(all(value.get(key) is False for key in ('security_accepted','scanner_findings_modified',
        'production_mounts_or_data_used','dynamic_runtime_code_downloads_analyzed',
        'renamed_or_obfuscated_embedded_code_analyzed','directory_symlinks_followed'))
        and value.get('scope')=='known_vendor_component_paths_and_names_plus_actual_perl_inc','component_receipt_scope')
    return value


def evidence_hashes(root):
    return {name:sha256(read(root/'reports'/name,maximum)).hexdigest() for name,maximum in EVIDENCE_REPORTS.items()}


def validate_acl_receipt(raw,image,commit):
    require(0<len(raw)<=4*1024**2,'acl_receipt_bound');value=json.loads(raw)
    require(value.get('schema')=='security_acl_consumer_presence/1' and value.get('image_id')==image
        and value.get('source_commit')==commit and value.get('verifier_sha256')==ACL_PROBE_SHA,'acl_receipt_identity')
    require(all(value.get(key) is False for key in ('directory_symlinks_followed','inspected_code_executed',
        'static_or_obfuscated_embedded_code_analyzed','dlopen_or_ctypes_reachability_analyzed',
        'acl_flags_or_callsite_safety_analyzed','production_mounts_or_data_used','scanner_findings_modified','security_accepted'))
        and value.get('scope')=='regular_elf_dynamic_needed_and_undefined_acl_symbols_under_fixed_roots'
        and type(value.get('inventory')) is dict and value['inventory'].get('roots')==['/usr','/app','/opt']
        and type(value['inventory'].get('elf_files')) is list and value['inventory']['elf_files'],'acl_receipt_scope')
    return value


def acl_consumer_probe(root,image,commit):
    raw=read(root/'source/verify_acl_consumer_presence.py',131072)
    require(sha256(raw).hexdigest()==ACL_PROBE_SHA,'acl_probe_changed')
    program=('import hashlib,json,sys;raw=bytes.fromhex('+repr(raw.hex())+');'
        'assert hashlib.sha256(raw).hexdigest()=='+repr(ACL_PROBE_SHA)+';'
        'scope={"__name__":"sealed_acl_inventory"};'
        'exec(compile(raw,"<sealed-acl-inventory>","exec"),scope);'
        'print(json.dumps(scope["probe"](sys.argv[1],'+repr(commit)+','+repr(ACL_PROBE_SHA)+'),sort_keys=True))')
    result=isolated(root,image,program,'acl',maximum=4*1024**2)
    validate_acl_receipt(result,image,commit)
    write(root/'reports/acl-consumer-presence.json',result)


def load_sealed(root):
    identity=json.loads(read(root/'source/identity.json',65536))
    require(identity['recipe_commit']==os.environ['GITHUB_SHA'] and set(identity['files'])=={
        'compose_security_os_candidate.py','admit_security_component_image_reuse.py','verify_image_component_presence.py',
        'verify_acl_consumer_presence.py'},'sealed_inventory')
    modules={}
    for name,row in identity['files'].items():
        raw=read(root/'source'/name,131072)
        require(len(raw)==row['bytes'] and sha256(raw).hexdigest()==row['sha256'],'sealed_source_changed')
        if name=='compose_security_os_candidate.py':
            require(raw==read(Path(__file__).resolve(),131072),'controller_source_changed');continue
        if name=='verify_image_component_presence.py':require(sha256(raw).hexdigest()==PROBE_SHA,'probe_changed')
        if name=='verify_acl_consumer_presence.py':require(sha256(raw).hexdigest()==ACL_PROBE_SHA,'acl_probe_changed')
        module=ModuleType('composition_'+name.removesuffix('.py'));exec(compile(raw,'<sealed-'+name+'>','exec'),module.__dict__);modules[name]=module
    return identity,modules


def ci_root(root):
    require(os.environ.get('GITHUB_ACTIONS')=='true' and os.environ.get('RUNNER_ENVIRONMENT')=='github-hosted'
        and os.environ.get('GITHUB_REPOSITORY')=='mohsenbarari/trading_bot' and os.geteuid()==1001,'github_ci_only')
    require(root.is_absolute() and root.resolve(strict=True)==root and root.parent==Path(os.environ['RUNNER_TEMP']).resolve(strict=True)
        and root.name.startswith('security-os-compose-') and root.stat().st_uid==1001
        and stat.S_IMODE(root.stat().st_mode)==0o700,'ci_root')
    require(re.fullmatch('[0-9]+-[0-9]+',os.environ['GITHUB_RUN_ID']+'-'+os.environ['GITHUB_RUN_ATTEMPT']),'ci_identity')
    os.umask(0o077)


def compose(root):
    ci_root(root);phase(root,'controller_admission');phase(root,'sealed_sources');identity,modules=load_sealed(root)
    admission=modules['admit_security_component_image_reuse.py']
    require((admission.IMAGE,admission.SOURCE,admission.ARTIFACT_ID)==(IMAGE,SOURCE,12552928309),'base_admission_scope')
    for name in ('inputs','context','tests','docker-config'):(root/name).mkdir(mode=0o700)
    (root/'context/capsule').mkdir(mode=0o755);(root/'context/capsule').chmod(0o755)
    floor=os.statvfs(root);require(floor.f_bavail*floor.f_frsize>=8*1024**3,'runner_space')
    phase(root,'download_image');fetch_artifact(admission.ARTIFACT_ID,root/'inputs/image.zip',admission.ZIP_BYTES,admission.ZIP_SHA256)
    packages=[];families={}
    for group,record in GROUPS.items():
        _,identifier,size,digest,*_=record;path=root/'inputs'/(group+'.zip')
        phase(root,'download_'+group);fetch_artifact(identifier,path,size,digest)
        phase(root,'admit_'+group);families[group]=admit_packages(path,group,root/'context/capsule')
        packages+=families[group]['packages']
    require(len(packages)==15 and {r['name'] for r in packages}==set(PACKAGES),'combined_package_inventory')
    phase(root,'download_test_evidence');fetch_artifact(EVIDENCE[0],root/'inputs/tests.zip',EVIDENCE[1],EVIDENCE[2])
    phase(root,'admit_test_evidence');proof=admit_test_proof(root/'inputs/tests.zip')
    phase(root,'fetch_test_sources');fetch_tests(root/'tests');(root/'tests').chmod(0o755)
    write(root/'test-sources.json',proof,0o644)
    write(root/'context/capsule/manifest.json',encoded({'schema':'security_os_composition_packages/1','packages':packages}),0o644)
    phase(root,'base_image_admission');admitted=admission.admit_image(root/'inputs/image.zip',root/'admitted')
    write(root/'reports/base-admission.json',encoded(admitted))
    phase(root,'base_archive_config');original_config,archive_tags=archive_config(root/'admitted/candidate-image.tar',IMAGE,include_tags=True)
    # The admission helper's original component-only receipt remains immutable.
    # This separately authorized recipe proves its own composition boundaries.
    phase(root,'load_base_image')
    with admission.pinned(root/'admitted/candidate-image.tar',admission.MAX_TAR) as (stream,size):
        require(size==admitted['docker_archive_bytes'] and admission.digest(stream,size)==admitted['docker_archive_sha256'],'base_archive_changed')
        stream.seek(0);docker(root,['image','load'],seconds=180,input_stream=stream)
    phase(root,'inspect_base_image');before=image_record(docker(root,['image','inspect',IMAGE],maximum=2*1024**2),IMAGE)
    require(original_config['rootfs']['diff_ids']==before['RootFS']['Layers'],'base_daemon_layers_changed')
    require(before['Config'].get('User','') in ('','root','0') and not before['Config'].get('OnBuild'),'base_user_or_onbuild')
    write(root/'reports/baseline-image-identity.json',encoded({'schema':'security_os_composition_baseline_identity/1',
        'image_id':IMAGE,'archive_repo_tags':archive_tags,'daemon_repo_tags_before_alias':before.get('RepoTags')}))
    phase(root,'audit_base_image');original=json.loads(isolated(root,IMAGE,AUDIT,'before').splitlines()[-1])
    plan=base_alias_plan();alias=plan['name']
    phase(root,'create_base_alias');write(root/'reports/base-alias-plan.json',encoded(plan));docker(root,['image','tag',IMAGE,alias])
    try:
        phase(root,'inspect_base_alias')
        require(image_record(docker(root,['image','inspect',alias],maximum=2*1024**2),IMAGE)['Config']==before['Config'],'base_alias_changed')
        phase(root,'inspect_builder');inspect_builder(root)
        phase(root,'write_build_context')
        write(root/'context/Dockerfile',dockerfile_for(alias).encode(),0o644);write(root/'context/installer.py',INSTALLER.encode(),0o644)
        phase(root,'build_candidate')
        docker(root,['build','--builder','default','--pull=false','--no-cache','--network=none','--iidfile',str(root/'candidate.id'),str(root/'context')],
               seconds=600,maximum=32*1024**2,combine_stderr=True,log_path=root/'reports/build.log')
    except Exception as error:
        try:failed_phase(root,error)
        except Exception:pass
        raise
    image=read(root/'candidate.id',256).decode().strip();require(re.fullmatch(r'sha256:[a-f0-9]{64}',image),'candidate_identity')
    phase(root,'inspect_candidate_image');after=image_record(docker(root,['image','inspect',image],maximum=2*1024**2),image);compare_images(before,after)
    phase(root,'audit_candidate_image');installed=json.loads(isolated(root,image,AUDIT,'after').splitlines()[-1]);compare_inventory(original,installed)
    write(root/'reports/baseline-inventory.json',encoded(original));write(root/'reports/candidate-inventory.json',encoded(installed))
    phase(root,'native_checks');isolated(root,image,NATIVE,'native')
    phase(root,'focused_tests');tests=isolated(root,image,FOCUSED,'focused',mounts=((root/'tests','/app/tests'),(root/'test-sources.json','/proof/test-sources.json')))
    result=json.loads(tests.splitlines()[-1]);require(result['image_id']==image and result['tests']==56 and result['modules']==5 and result['status']=='passed','focused_test_result')
    write(root/'reports/focused-tests.json',encoded(result))
    probe=modules['verify_image_component_presence.py'];probe_raw=read(root/'source/verify_image_component_presence.py',65536)
    phase(root,'component_probe');component=probe.in_container(image,SOURCE,probe_raw,PROBE_SHA);validate_component(component,image)
    write(root/'reports/component-presence.json',component)
    phase(root,'acl_consumer_probe');acl_consumer_probe(root,image,identity['recipe_commit'])
    record={'schema':'security_os_composition/1','status':'composed_tested_not_accepted','image_id':image,'parent_image_id':IMAGE,
        'original_provenance':admitted['export_provenance'],'recipe':identity,'families':families,'runtime_config':before['Config'],
        'original_oci_config':original_config['config'],
        'baseline_archive_repo_tags':archive_tags,'baseline_repo_tags_before_alias':before.get('RepoTags'),
        'baseline_layers':before['RootFS']['Layers'],'candidate_layers':after['RootFS']['Layers'],
        'runtime_config_unchanged':True,'app_and_python_unchanged':True,'libc_unchanged':True,
        'installer_resource_cgroup_caps':'NOT_ASSERTED','installer_network':'none','installer_caps_mask':'0xd9',
        'focused_tests':56,'full_runtime_matrix':'NOT_TESTED','app_recompiled':False,'security_accepted':False,
        'raw_scanner_gate_waived':False,'production_installed':False,'deployment_performed':False}
    phase(root,'bind_composition_evidence');record['test_sources_sha256']=TEST_PROOF;record['evidence_sha256']=evidence_hashes(root)
    write(root/'reports/composition.json',encoded(record))
    for name in ('vulnerabilities.json','sbom.cdx.json'):write(root/'reports'/name,b'')
    write(root/'empty-trivy.yaml',b'{}\n')
    phase(root,'composition_complete',complete=True)
    with Path(os.environ['GITHUB_OUTPUT']).open('a') as output:output.write('image_id='+image+'\n')


def validate_reports(record,vulnerabilities,sbom):
    image=record['image_id']
    require(vulnerabilities.get('SchemaVersion')==2 and vulnerabilities.get('ArtifactType')=='container_image'
        and vulnerabilities.get('Metadata',{}).get('ImageID')==image and type(vulnerabilities.get('Results')) is list
        and vulnerabilities['Results'],'raw_report_identity')
    actual=vulnerabilities['Metadata']['ImageConfig']
    require(actual['config']==record['original_oci_config'] and actual['rootfs']['diff_ids']==record['candidate_layers'],'raw_report_config')
    component=sbom.get('metadata',{}).get('component',{})
    properties=component.get('properties',[])
    require(component.get('type')=='container' and component.get('name')==image
        and [r.get('value') for r in properties if r.get('name')=='aquasecurity:trivy:ImageID']==[image],'sbom_image_identity')
    for name,row in PACKAGES.items():
        require({r.get('version') for r in sbom.get('components',[]) if r.get('name')==name}=={row[0]},'sbom_package_version')
    counts={}
    for result in vulnerabilities['Results']:
        for finding in result.get('Vulnerabilities') or []:
            severity=finding['Severity'];counts[severity]=counts.get(severity,0)+1
    return counts


def export_image(root,image):
    path=root/'candidate-image.tar.gz';maximum=2*1024**3
    child=subprocess.Popen([*DOCKER,'image','save',image],stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
        start_new_session=True,env={'PATH':'/usr/bin:/bin','LC_ALL':'C.UTF-8','DOCKER_CONFIG':str(root/'docker-config')})
    deadline=time.monotonic()+300;total=0;complete=False
    try:
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'wb') as target,gzip.GzipFile(filename='',mode='wb',fileobj=target,mtime=0,compresslevel=6) as output,selectors.DefaultSelector() as selector:
            selector.register(child.stdout,selectors.EVENT_READ)
            while selector.get_map():
                require(time.monotonic()<deadline,'export_timeout')
                for key,_ in selector.select(.1):
                    block=os.read(key.fileobj.fileno(),min(65536,maximum+1-total))
                    if not block:selector.unregister(key.fileobj)
                    else:
                        total+=len(block);require(total<=maximum,'export_byte_bound');output.write(block)
                        require(target.tell()<=maximum+16*1024**2,'compressed_export_bound')
            output.close();target.flush();os.fsync(target.fileno())
        require(child.wait(timeout=max(.01,deadline-time.monotonic()))==0,'export_failed');complete=True
    finally:
        try:cleanup_owned_child(child)
        finally:child.stdout.close()
    with pinned(path,maximum+16*1024**2) as (stream,size):
        digest=stream_digest(stream,size)
    return {'name':path.name,'format':'docker-archive+gzip','size_bytes':size,'sha256':digest,'uncompressed_bytes':total}


def finalize(root):
    ci_root(root);phase(root,'finalize_admission');identity,_=load_sealed(root)
    raw=read(root/'reports/composition.json',2*1024**2);record=json.loads(raw)
    require(record['schema']=='security_os_composition/1' and record['status']=='composed_tested_not_accepted'
        and record['parent_image_id']==IMAGE and record['security_accepted'] is False and record['recipe']==identity
        and all(record[key] is False for key in ('app_recompiled','raw_scanner_gate_waived','production_installed','deployment_performed'))
        and record['focused_tests']==56 and record['full_runtime_matrix']=='NOT_TESTED','composition_state')
    require(record['test_sources_sha256']==TEST_PROOF and record['evidence_sha256']==evidence_hashes(root),'composition_evidence_changed')
    validate_component(read(root/'reports/component-presence.json',131072),record['image_id'])
    validate_acl_receipt(read(root/'reports/acl-consumer-presence.json',4*1024**2),record['image_id'],identity['recipe_commit'])
    image=record['image_id'];phase(root,'inspect_final_candidate')
    current=image_record(docker(root,['image','inspect',image],maximum=2*1024**2),image)
    phase(root,'inspect_final_baseline')
    baseline=image_record(docker(root,['image','inspect',IMAGE],maximum=2*1024**2),IMAGE)
    compare_images(baseline,current)
    phase(root,'read_final_base_archive')
    original_config=archive_config(root/'admitted/candidate-image.tar',IMAGE)
    require(original_config['config']==record['original_oci_config'] and baseline['Config']==record['runtime_config']
        and baseline['RootFS']['Layers']==record['baseline_layers'],'composition_baseline_changed')
    require(current['Config']==record['runtime_config'] and current['RootFS']['Layers']==record['candidate_layers'],'composition_image_changed')
    phase(root,'bind_raw_reports')
    reports={name:read(root/'reports'/name,32*1024**2) for name in ('vulnerabilities.json','sbom.cdx.json')}
    counts=validate_reports(record,*(json.loads(reports[name]) for name in ('vulnerabilities.json','sbom.cdx.json')))
    phase(root,'export_image');archive=export_image(root,image)
    proof={'schema':'security_os_composition_image_export/1','status':'exported_not_accepted','image_id':image,
        'parent_image_id':IMAGE,'original_provenance':record['original_provenance'],'composition_sha256':sha256(raw).hexdigest(),
        'runtime_config_unchanged':True,'baseline_layer_prefix_unchanged':True,'archive':archive,'raw_counts':counts,
        'reports_sha256':{name:sha256(value).hexdigest() for name,value in reports.items()},
        'evidence_sha256':record['evidence_sha256'],'test_sources_sha256':TEST_PROOF,
        'app_recompiled':False,'full_runtime_matrix':'NOT_TESTED','security_accepted':False,'raw_scanner_gate_waived':False,
        'runtime_data_included':False,'secrets_disclosed':False,'deployment_performed':False}
    write(root/'image-export.json',encoded(proof))
    phase(root,'export_complete',complete=True)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('command',choices=('compose','finalize','cleanup'));parser.add_argument('--root',type=Path,required=True)
    args=parser.parse_args(argv)
    try:
        {'compose':compose,'finalize':finalize,'cleanup':cleanup}[args.command](args.root)
        return 0
    except Exception as error:
        failure={'status':'failed','code':public_error(error),'phase':None,'security_accepted':False,'deployment_performed':False}
        try:
            # A host refusal must never create diagnostics at a caller path.
            ci_root(args.root);state=failed_phase(args.root,error)
            failure.update(code=state['first_failure_code'],phase=state['first_failure_phase'])
        except Exception:pass
        print(json.dumps(failure,sort_keys=True),file=sys.stderr)
        return 1


if __name__=='__main__':raise SystemExit(main())
