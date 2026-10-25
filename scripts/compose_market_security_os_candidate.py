"""One CI-only E46 export plus fifteen already built runtime DEBs; no deployment."""
from contextlib import contextmanager, ExitStack
from datetime import datetime, timedelta, timezone
from hashlib import sha1, sha256
import argparse
import gzip
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import tarfile
import time
from types import ModuleType
from zipfile import ZipFile

IMAGE = 'sha256:c22d9d459ab4ca0586066d87e2b2456958309fa12e47dfaac2651a1743e0fde4'
SOURCE = '88299eb42bfa05361f8b8ed82358873336a6480e'
BASELINE = 'a6dcd636a1525a3a3d47e2a6083ca887b16080db'
RUN = 40140063747
IMAGE_ARTIFACT = (12578501302, 59814206, 'da7ee559c4815113ed3ff74be845443b48185c410cc4f8b474cabb0b7efdd236')
EVIDENCE_ARTIFACT = (12578306625, 169181, '474360da7f53997b30e1dd8f754b46ba1ae8a811f55432c084a0aec756cf9f49')
PAYLOAD_SHA = 'bcd9c4a0af773a0fbda306a616ee8e9c900406b6c69f72a486e71b0c773f3a10'
TEST_SHA = '39336b06d6d87f61188e49c4e005651a2f6e69422ce0ce0504b29d634f2e2bbe'
BUILD_SHA = 'd41d20ae0ab352d8e8d93028e86b2cb3bc580bac7c6627f14cf250ece9a6d5bd'
RAW_SHA = 'd6fa1a82ba7e26056f08271a7232dc798f621cd66c3dd0370c135899a806fba7'
SBOM_SHA = '855d62fbcbf2aea711bfdfb4598879404c9a242f9518f389c874aad8d8fac49c'
CONFIG_SHA = 'cf1d8ac89f67e05eef50957837bca63f65bf31595d3fecd67ec1353329efbcbe'
HELPERS = {'compose_security_os_candidate.py': '10ccbe216fef04e5f7a7ce2ad31ac30eca7eeae090dfaaf8074fa3cc5c789d44',
    'prepare_market_security_payload.py': '9131fe760b94d54f2f3fcc577b8cb4d36f0170a0f28a2f0d8adfb750c2b78c84',
    'verify_image_component_presence.py': '36e92fdfd3105a9db6bc20fd75aade1238a5e0b053a581bfb24c7b5e1bf6a6d3',
    'verify_acl_consumer_presence.py': '001e51d270c079153cc38db1be53c723fbbc8a8bb9e981e718e7b657127bc778'}
MAX_TAR, MAX_JSON = 2*1024**3, 2*1024**2
COMMON = MARKET = COMPONENT = ACL = None
PHASE = 'controller_admission'
PHASES = frozenset(('controller_admission','sealed_sources','download_evidence','admit_evidence','download_image',
    'admit_image','load_image','inspect_baseline','historical_capsule','download_packages','audit_baseline',
    'create_alias','inspect_builder','build_candidate','inspect_candidate','audit_candidate','native_checks',
    'historical_tests','root_fixture','component_probe','acl_probe','composition_complete',
    'finalize','raw_reports','export','complete','cleanup'))
REPORTS = {'base-admission.json':MAX_JSON,'original-evidence.json':MAX_JSON,'baseline-inventory.json':MAX_JSON,
    'candidate-inventory.json':MAX_JSON,'before-run.log':4*1024**2,'after-run.log':4*1024**2,
    'market-native-run.log':MAX_JSON,'native.json':MAX_JSON,'market-focused-run.log':4*1024**2,
    'market-root-fixture-run.log':4*1024**2,'focused-nonroot.json':MAX_JSON,'focused-root-fixture.json':MAX_JSON,
    'focused.json':MAX_JSON,'component-presence.json':MAX_JSON,'acl-consumer-presence.json':4*1024**2,
    'acl-run.log':4*1024**2,'build.log':32*1024**2,'builder-inspect.log':65536,'base-alias-plan.json':65536}


def require(ok, code):
    if not ok: raise RuntimeError(code)


def encoded(value): return (json.dumps(value, sort_keys=True)+'\n').encode()


def document(raw):
    def pairs(rows):
        result = {}
        for key,value in rows:
            require(key not in result, 'duplicate_json_key'); result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=lambda _: require(False,'invalid_json_number'))


@contextmanager
def pinned(path, maximum):
    require(path.is_absolute() and path.resolve(strict=True)==path, 'input_path')
    fd = os.open(path, os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        identity = lambda s:(s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
        require(stat.S_ISREG(before.st_mode) and before.st_uid==os.geteuid() and before.st_nlink==1
                and stat.S_IMODE(before.st_mode)==0o600 and 0<before.st_size<=maximum, 'input_metadata')
        require(identity(before)==identity(path.lstat()), 'input_changed')
        with os.fdopen(os.dup(fd),'rb') as stream: yield stream,before.st_size
        require(identity(before)==identity(os.fstat(fd))==identity(path.lstat()), 'input_changed')
    finally: os.close(fd)


def read(path, maximum=MAX_JSON):
    with pinned(path,maximum) as (stream,size):
        raw=stream.read(size+1); require(len(raw)==size,'input_bound'); return raw


def digest(stream,size):
    value,total=sha256(),0
    while block:=stream.read(min(65536,size+1-total)):
        total+=len(block);require(total<=size,'stream_bound');value.update(block)
    require(total==size,'stream_size');return value.hexdigest()


def write(path,raw,mode=0o600):
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,mode)
    with os.fdopen(fd,'wb') as stream:
        stream.write(raw);stream.flush();os.fchmod(stream.fileno(),mode);os.fsync(stream.fileno())


def phase(root,name):
    global PHASE
    require(name in PHASES,'phase_invalid');PHASE=name
    temporary=root/'reports/current-phase.new';write(temporary,encoded({'schema':'market_os_composition_phase/1','phase':name}))
    os.replace(temporary,root/'reports/current-phase.json')


def ci(root):
    require(os.geteuid()==1001 and os.environ.get('GITHUB_ACTIONS')=='true'
        and os.environ.get('RUNNER_ENVIRONMENT')=='github-hosted'
        and os.environ.get('GITHUB_REPOSITORY')=='mohsenbarari/trading_bot','github_ci_only')
    parent=Path(os.environ['RUNNER_TEMP']).resolve(strict=True)
    require(root.is_absolute() and root.resolve(strict=True)==root and root.parent==parent
        and root.name.startswith('security-market-os-') and root.stat().st_uid==1001
        and stat.S_IMODE(root.stat().st_mode)==0o700,'ci_root')
    require(re.fullmatch('[a-f0-9]{40}',os.environ.get('GITHUB_SHA','')),'ci_commit')
    require(re.fullmatch('[0-9]+-[0-9]+',os.environ.get('GITHUB_RUN_ID','')+'-'+os.environ.get('GITHUB_RUN_ATTEMPT','')),'ci_identity')
    os.umask(0o077)


@contextmanager
def sources(root):
    global COMMON,MARKET,COMPONENT,ACL
    with ExitStack() as stack:
        manifest=document(read(root/'source/identity.json',65536))
        own=Path(__file__).name;names={own,*HELPERS}
        require(manifest.get('recipe_commit')==os.environ['GITHUB_SHA'] and set(manifest.get('files',{}))==names,'source_inventory')
        buffers={}
        for name in names:
            stream,size=stack.enter_context(pinned(root/'source'/name,131072));raw=stream.read(size+1)
            require(len(raw)==size and manifest['files'][name]=={'bytes':size,'sha256':sha256(raw).hexdigest()},'source_changed')
            require(name==own or sha256(raw).hexdigest()==HELPERS[name],'source_pin');buffers[name]=raw
        require(buffers[own]==read(Path(__file__).resolve(strict=True),131072),'controller_source_changed')
        modules={}
        for name in HELPERS:
            module=ModuleType('_sealed_market_os_'+name[:-3]);module.__file__=str(root/'source'/name)
            exec(compile(buffers[name],module.__file__,'exec'),module.__dict__);modules[name]=module
        COMMON=modules['compose_security_os_candidate.py'];MARKET=modules['prepare_market_security_payload.py']
        COMPONENT=modules['verify_image_component_presence.py'];ACL=modules['verify_acl_consumer_presence.py']
        yield manifest


def admit_evidence(path):
    _,expected_size,expected_sha=EVIDENCE_ARTIFACT
    with pinned(path,expected_size) as (stream,size):
        require(size==expected_size and digest(stream,size)==expected_sha,'evidence_zip_identity');stream.seek(0)
        with ZipFile(stream) as archive:
            rows=archive.infolist();require(len(rows)==15 and len({r.filename for r in rows})==15,'evidence_zip_inventory')
            require(all(not r.is_dir() and not r.flag_bits&1 and stat.S_IFMT(r.external_attr>>16) in (0,stat.S_IFREG)
                and r.compress_type in (0,8) and 0<r.file_size<=32*1024**2 for r in rows),'evidence_zip_member')
            wanted={'payload/payload.json':PAYLOAD_SHA,'payload/tests/test-sources.json':TEST_SHA,
                'reports/build-receipt.json':BUILD_SHA,'reports/vulnerabilities.json':RAW_SHA,'reports/sbom.cdx.json':SBOM_SHA}
            selected={}
            for name,expected in wanted.items():
                row=archive.getinfo(name);require(row.file_size<=MAX_JSON,'evidence_member_bound')
                raw=archive.read(name);require(len(raw)==row.file_size and sha256(raw).hexdigest()==expected,'evidence_member_pin')
                selected[name]=raw
    payload=document(selected['payload/payload.json']);tests=document(selected['payload/tests/test-sources.json'])
    build=document(selected['reports/build-receipt.json']);report=document(selected['reports/vulnerabilities.json'])
    config=report['Metadata']['ImageConfig']
    require(sha256(encoded(config)).hexdigest()==CONFIG_SHA and report['Metadata']['ImageID']==IMAGE
        and payload['source_commit']==SOURCE and payload['baseline_commit']==BASELINE
        and payload['profile']=='e46' and build['image_id']==IMAGE and build['payload_sha256']==PAYLOAD_SHA
        and build['test_sources_sha256']==TEST_SHA and payload['test_sources_sha256']==TEST_SHA
        and tests['source_commit']==SOURCE and tests['baseline_commit']==BASELINE
        and tests['expected_tests']==85 and tests['test_ids']==payload['test_ids']
        and build['rootfs_layers']==config['rootfs']['diff_ids'],'evidence_identity')
    require(config['config']['User']=='10001:10001' and config['config']['WorkingDir']=='/app'
        and not config['config'].get('Volumes') and not config['config'].get('OnBuild'),'baseline_runtime_contract')
    return {'payload':payload,'tests':tests,'build':build,'config':config,'raw':selected}


def validate_export(value,evidence):
    build,payload=evidence['build'],evidence['payload']
    require(type(value) is dict and value.get('schema')=='market_security_candidate_image_export/1'
        and value.get('status')=='exported_not_accepted' and value.get('image_id')==IMAGE,'export_identity')
    for field in ('baseline_image_id','baseline_commit','baseline_tree','source_commit','source_tree',
                  'payload_tree','image_input_signature','public_source_binding'):
        require(value.get(field)==payload[field],'export_provenance')
    require(value.get('payload_sha256')==PAYLOAD_SHA and value.get('build_receipt_sha256')==BUILD_SHA
        and value.get('test_sources_sha256')==TEST_SHA
        and value.get('reports_sha256')=={'vulnerabilities.json':RAW_SHA,'sbom.cdx.json':SBOM_SHA},'export_evidence')
    for name in ('native_sha256','focused_sha256','focused_nonroot_sha256','focused_root_fixture_sha256'):
        require(value.get(name)==build[name],'export_evidence')
    require(value.get('product_files_equal_baseline') is True and value.get('public_source_directory_mode')=='0755'
        and all(value.get(k) is False for k in ('vendor_debs_reused','full_runtime_matrix_completed','security_accepted',
            'findings_waived','runtime_data_used','deployment_eligible','deployment_performed')),'export_scope')
    a=value.get('archive');require(type(a) is dict and set(a)=={'name','format','size_bytes','sha256','uncompressed_bytes'}
        and a['name']=='candidate-image.tar.gz' and a['format']=='docker-archive+gzip'
        and type(a['size_bytes']) is int and 0<a['size_bytes']<=MAX_TAR+16*1024**2
        and type(a['uncompressed_bytes']) is int and 0<a['uncompressed_bytes']<=MAX_TAR
        and type(a['sha256']) is str and re.fullmatch('[a-f0-9]{64}',a['sha256']),'export_archive')
    return a


def oci_runtime_config(value):
    # Image inspect expands API zero fields; raw OCI can omit them. This is
    # only the OCI/API comparison. Whole Docker before/after Config is exact.
    defaults={'Hostname':'','Domainname':'','User':'','AttachStdin':False,'AttachStdout':False,
        'AttachStderr':False,'Tty':False,'OpenStdin':False,'StdinOnce':False,'ExposedPorts':None,
        'Env':None,'Cmd':None,'Healthcheck':None,'ArgsEscaped':False,'Image':'','Volumes':None,
        'WorkingDir':'','Entrypoint':None,'NetworkDisabled':False,'MacAddress':'','OnBuild':None,
        'Labels':None,'StopSignal':'','StopTimeout':None,'Shell':None}
    require(type(value) is dict,'runtime_config')
    return {k:v for k,v in value.items() if k not in defaults or type(v) is not type(defaults[k]) or v!=defaults[k]}


def same_json_value(left,right):
    """JSON equality also distinguishes bool/int and int/float representations."""
    if type(left) is not type(right):return False
    if type(left) is dict:return set(left)==set(right) and all(same_json_value(left[k],right[k]) for k in left)
    if type(left) is list:return len(left)==len(right) and all(same_json_value(a,b) for a,b in zip(left,right))
    return left==right


def trivy_history_second(value):
    """The Docker-history second projected by Trivy, never instant equivalence.

    Trivy v0.76.1 pkg/fanal/image/daemon/image.go configHistory constructs
    history.Created as time.Unix(h.Created, 0).UTC(). Only this history field
    loses subsecond precision; the raw config's SHA remains authoritative.
    """
    require(type(value) is str,'history_timestamp_invalid')
    match=re.fullmatch(r'(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,9}))?(Z|[+-]\d{2}:\d{2})',value)
    require(match is not None,'history_timestamp_invalid')
    try:
        date=datetime.strptime(match[1],'%Y-%m-%dT%H:%M:%S');offset=match[3]
        if offset=='Z':zone=timezone.utc
        else:
            hours,minutes=int(offset[1:3]),int(offset[4:6]);require(hours<24 and minutes<60,'history_timestamp_invalid')
            zone=timezone((1 if offset[0]=='+' else -1)*timedelta(hours=hours,minutes=minutes))
        result=date.replace(tzinfo=zone).astimezone(timezone.utc)
    except (ValueError,OverflowError):raise RuntimeError('history_timestamp_invalid') from None
    # No microsecond conversion, rounding or generic field removal. Discarding
    # the validated fractional digits is precisely the observed daemon policy.
    return result.isoformat(timespec='seconds').replace('+00:00','Z')


def compare_scanner_config(actual,scanner):
    require(type(actual) is dict and type(scanner) is dict and type(actual.get('history')) is list
        and type(scanner.get('history')) is list,'archive_config_changed')
    projected={**actual};history=[];changed=0;subseconds_lost=0
    for row in actual['history']:
        require(type(row) is dict and 'created' in row,'archive_config_changed')
        second=trivy_history_second(row['created']);changed+=second!=row['created']
        fraction=re.search(r'\.(\d+)',row['created']);subseconds_lost+=bool(fraction and int(fraction[1]))
        history.append({**row,'created':second})
    projected['history']=history
    require(same_json_value(projected,scanner),'archive_config_changed')
    return {'schema':'trivy_0761_docker_history_second_projection/1','scope':'history.created_only',
        'source':'https://github.com/aquasecurity/trivy/blob/v0.76.1/pkg/fanal/image/daemon/image.go#L213',
        'history_created_entries_changed':changed,'nonzero_subsecond_entries_lost':subseconds_lost,
        'scanner_history_projection_lossy':subseconds_lost>0,
        'full_metadata_identical':same_json_value(actual,scanner),'every_other_key_type_value_equal':True,
        'whole_runtime_config_equal':same_json_value(actual.get('config'),scanner.get('config')),
        'ordered_rootfs_equal':same_json_value(actual.get('rootfs'),scanner.get('rootfs')),
        'original_nanosecond_config_sha256':IMAGE[7:],'raw_scanner_report_modified':False,'security_accepted':False}


def safe_name(name):
    return type(name) is str and 0<len(name)<=256 and re.fullmatch(r'[A-Za-z0-9._+/-]+',name) is not None and not name.startswith('/') \
        and all(part not in ('','..','.') for part in name.split('/')) and str(PurePosixPath(name))==name


class SliceReader:
    """Borrow an already retained archive FD; every read/seek is bounded first."""
    def __init__(self,stream,offset,size,end):self.stream,self.offset,self.size,self.end,self.pos=stream,offset,size,end,0
    def read(self,size=-1):
        require(time.monotonic()<self.end,'archive_timeout')
        size=self.size-self.pos if size<0 else min(size,self.size-self.pos)
        require(size>=0,'archive_interval');self.stream.seek(self.offset+self.pos);raw=self.stream.read(size)
        require(len(raw)==size,'archive_short');self.pos+=size;return raw
    def seek(self,offset,whence=0):
        require(time.monotonic()<self.end,'archive_timeout')
        target=offset if whence==0 else self.pos+offset if whence==1 else self.size+offset if whence==2 else -1
        require(0<=target<=self.size,'archive_interval');self.pos=target;return target
    def tell(self):return self.pos


def go_json(value):
    raw=json.dumps(value,ensure_ascii=False,separators=(',',':'),allow_nan=False)
    for a,b in (('&','\\u0026'),('<','\\u003c'),('>','\\u003e'),('\u2028','\\u2028'),('\u2029','\\u2029')):raw=raw.replace(a,b)
    return raw.encode()


def container_config(value=None):
    # Official Moby v28.5.1 api/types/container/config.go declaration order.
    result={'Hostname':'','Domainname':'','User':'','AttachStdin':False,'AttachStdout':False,
        'AttachStderr':False,'Tty':False,'OpenStdin':False,'StdinOnce':False,'Env':None,'Cmd':None}
    if value is not None:
        require(type(value) is dict and set(value)=={'Cmd','Entrypoint','Env','Labels','User','WorkingDir','ArgsEscaped'},'legacy_projection')
        require(type(value['ArgsEscaped']) is bool and all(type(value[k]) is str for k in ('User','WorkingDir'))
            and all(type(value[k]) is list and all(type(x) is str for x in value[k]) for k in ('Cmd','Entrypoint','Env'))
            and type(value['Labels']) is dict and all(type(k) is str and type(v) is str for k,v in value['Labels'].items()),'legacy_projection')
        result.update({k:value[k] for k in ('User','Env','Cmd')})
        if value['ArgsEscaped']:result['ArgsEscaped']=True
    result.update({'Image':'','Volumes':None,'WorkingDir':'','Entrypoint':None,'OnBuild':None,'Labels':None})
    if value is not None:
        result.update({k:value[k] for k in ('WorkingDir','Entrypoint')});result['Labels']=dict(sorted(value['Labels'].items()))
    return result


def legacy_configs(config):
    """Exact saveImage/CreateID projection; no arbitrary blob admission.

    https://github.com/moby/moby/blob/v28.5.1/image/tarexport/save.go
    https://github.com/moby/moby/blob/v28.5.1/image/v1/imagev1.go
    """
    require(type(config) is dict and set(config)=={'architecture','created','history','os','rootfs','config'}
        and config['architecture']=='amd64' and config['os']=='linux' and type(config['history']) is list,'legacy_image')
    created=config['created'];require(type(created) is str and re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?Z',created),'legacy_timestamp')
    if '.' in created:
        prefix,fraction=created[:-1].split('.');fraction=fraction.rstrip('0');created=prefix+('.'+fraction if fraction else '')+'Z'
    root=config['rootfs'];require(type(root) is dict and set(root)=={'type','diff_ids'} and root['type']=='layers'
        and type(root['diff_ids']) is list and 0<len(root['diff_ids'])<=128,'legacy_rootfs')
    final=container_config(config['config']);chain=parent=None;expected={}
    for index,diff in enumerate(root['diff_ids']):
        require(type(diff) is str and re.fullmatch('sha256:[a-f0-9]{64}',diff),'legacy_rootfs')
        chain=diff if chain is None else 'sha256:'+sha256((chain+' '+diff).encode()).hexdigest()
        value={'created':'1970-01-01T00:00:00Z','container_config':container_config()}
        if index==len(root['diff_ids'])-1:value={'created':created,'container_config':container_config(),'config':final,'architecture':'amd64','os':'linux'}
        identifying={**value,'layer_id':chain}
        if parent is not None:identifying['parent']='sha256:'+parent
        raw=b'{'+b','.join(go_json(k)+b':'+go_json(identifying[k]) for k in sorted(identifying))+b'}'
        identifier=sha256(raw).hexdigest();saved={'id':identifier}
        if parent is not None:saved['parent']=parent
        saved.update(value);saved['os']='linux';raw=go_json(saved)
        require(len(raw)<=65536,'legacy_bound');expected['blobs/sha256/'+sha256(raw).hexdigest()]=raw;parent=identifier
    require(len(expected)==len(root['diff_ids']),'legacy_collision');return expected


def validate_archive(stream,size,expected_config):
    """Closed physical TAR inventory, one config and ordered raw/gzip layer chain."""
    require(type(size) is int and 1024<=size<=MAX_TAR and size%512==0,'archive_size')
    end=time.monotonic()+300;whole=SliceReader(stream,0,size,end);members={};offset=0
    while True:
        require(offset+512<=size,'archive_trailer');whole.seek(offset);header=whole.read(512)
        if not any(header):break
        member=tarfile.TarInfo.frombuf(header,'utf-8','strict')
        name=member.name.rstrip('/') if member.isdir() else member.name
        require(len(members)<512 and safe_name(name) and name not in members and member.type in (tarfile.REGTYPE,tarfile.AREGTYPE,tarfile.DIRTYPE)
            and type(member.size) is int and member.size>=0 and (member.size==0 or member.isfile()),'archive_member')
        offset+=512;require(member.size<=size-offset,'archive_interval')
        members[name]=(offset,member.size,member.isdir());offset+=(member.size+511)//512*512
    tail=size-offset;require(tail>=1024,'archive_trailer');whole.seek(offset)
    while block:=whole.read(min(65536,size-whole.tell())):
        require(not any(block),'archive_trailing_payload')
    def member(name):
        require(name in members and not members[name][2],'archive_missing');start,length,_=members[name]
        return SliceReader(stream,start,length,end),length
    def data(name,maximum=MAX_JSON):
        source,length=member(name);require(length<=maximum,'archive_json_bound');return source.read(length)
    def stored(name):
        source,length=member(name);return digest(source,length)
    manifest=document(data('manifest.json'));require(type(manifest) is list and len(manifest)==1 and type(manifest[0]) is dict,'docker_manifest')
    row=manifest[0];require({'Config','Layers'}<=set(row)<={'Config','Layers','RepoTags','LayerSources'},'docker_manifest')
    config_name,layers=row['Config'],row['Layers']
    require(config_name in (IMAGE[7:]+'.json','blobs/sha256/'+IMAGE[7:]) and stored(config_name)==IMAGE[7:],'archive_config_identity')
    config=document(data(config_name));projection=compare_scanner_config(config,expected_config)
    diffs=config['rootfs']['diff_ids'];require(type(layers) is list and 0<len(layers)==len(diffs)<=128
        and all(safe_name(n) for n in layers),'archive_layers')
    tags=row.get('RepoTags');require(tags is None or type(tags) is list and len(tags)<=8
        and all(type(t) is str and re.fullmatch(r'[A-Za-z0-9_./:@-]{1,256}',t) for t in tags),'archive_tags')
    allowed={'manifest.json',config_name,*layers};expanded=0
    for name,diff in zip(layers,diffs):
        source,length=member(name);prefix=source.read(min(2,length));source.seek(0)
        decoded=gzip.GzipFile(fileobj=source) if prefix==b'\x1f\x8b' else source;value=sha256()
        try:
            while block:=decoded.read(min(65536,MAX_TAR+1-expanded)):
                expanded+=len(block);require(expanded<=MAX_TAR,'archive_layer_expansion');value.update(block)
        finally:
            if decoded is not source:decoded.close()
        require(type(diff) is str and diff=='sha256:'+value.hexdigest(),'archive_layer_diff_id')
    if 'index.json' in members:
        require(document(data('oci-layout'))=={'imageLayoutVersion':'1.0.0'},'oci_layout');allowed.update(('index.json','oci-layout'))
        def descriptor(value):
            require(type(value) is dict and type(value.get('digest')) is str and re.fullmatch('sha256:[a-f0-9]{64}',value['digest'])
                and type(value.get('size')) is int and value['size']>0,'oci_descriptor')
            name='blobs/sha256/'+value['digest'][7:];require(name in members and members[name][1]==value['size']
                and stored(name)==value['digest'][7:],'oci_blob');allowed.add(name);return name
        index=document(data('index.json'))
        for _ in range(4):
            require(type(index) is dict and index.get('schemaVersion')==2 and type(index.get('manifests')) is list
                and len(index['manifests'])==1,'oci_index')
            selected=document(data(descriptor(index['manifests'][0])))
            if 'manifests' in selected:index=selected;continue
            require(selected.get('schemaVersion')==2 and descriptor(selected.get('config'))==config_name
                and type(selected.get('layers')) is list and [descriptor(r) for r in selected['layers']]==layers,'oci_manifest');break
        else:require(False,'oci_depth')
    else:
        require('oci-layout' not in members and all(n.endswith('/layer.tar') and not n.startswith('blobs/') for n in layers),'classic_layout')
        legacy_values=list(legacy_configs(config).values())
        for name,expected_legacy in zip(layers,legacy_values):
            prefix=name[:-len('layer.tar')]
            for suffix in ('VERSION','json'):
                if prefix+suffix in members:
                    if suffix=='VERSION':require(data(prefix+suffix,32) in (b'1.0',b'1.0\n'),'classic_version')
                    else:
                        expected=document(expected_legacy)
                        require(prefix==expected['id']+'/' and document(data(prefix+suffix,65536))==expected,'classic_metadata')
                    allowed.add(prefix+suffix)
        if 'repositories' in members:require(type(document(data('repositories'))) is dict,'classic_repositories');allowed.add('repositories')
    directories={str(p) for n in allowed for p in PurePosixPath(n).parents if str(p)!='.'}
    extras=set(members)-allowed-directories;legacy=0
    if extras and 'index.json' in members:
        expected=legacy_configs(config)
        require(extras==set(expected),'archive_closed_inventory')
        for name,raw in expected.items():require(data(name,65536)==raw,'legacy_bytes')
        allowed.update(expected);legacy=len(expected)
    require(set(members)<=allowed|directories and all(isdir==(n in directories) for n,(_,_,isdir) in members.items()),'archive_closed_inventory')
    whole.seek(0);archive_sha=digest(whole,size)
    return {'image_id':IMAGE,'docker_archive_bytes':size,'docker_archive_sha256':archive_sha,
        'layers':len(layers),'legacy_compatibility_configs':legacy,'original_oci_config':config['config'],
        'scanner_history_projection':projection}


def admit_image(path,output,evidence):
    _,expected_size,expected_sha=IMAGE_ARTIFACT
    require(not os.path.lexists(output) and output.parent.resolve(strict=True)==output.parent,'admission_output')
    with pinned(path,expected_size) as (stream,size):
        require(size==expected_size and digest(stream,size)==expected_sha,'image_zip_identity');stream.seek(0)
        with ZipFile(stream) as archive:
            rows=archive.infolist();require(len(rows)==2 and {r.filename for r in rows}=={'candidate-image.tar.gz','image-export.json'},'image_zip_inventory')
            for row in rows:require(not row.is_dir() and not row.flag_bits&1 and row.compress_type in (0,8)
                and stat.S_IFMT(row.external_attr>>16) in (0,stat.S_IFREG)
                and 0<row.file_size<=(MAX_JSON if row.filename=='image-export.json' else MAX_TAR+16*1024**2),'image_zip_member')
            export_raw=archive.read('image-export.json');value=document(export_raw);expected=validate_export(value,evidence)
            require(archive.getinfo('candidate-image.tar.gz').file_size==expected['size_bytes'],'image_gzip_size')
            output.mkdir(mode=0o700)
            with archive.open('candidate-image.tar.gz') as source:
                fd=os.open(output/'candidate-image.tar.gz',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
                with os.fdopen(fd,'wb') as target:
                    total=0
                    while block:=source.read(min(65536,expected['size_bytes']+1-total)):
                        total+=len(block);require(total<=expected['size_bytes'],'image_zip_expansion');target.write(block)
                    require(total==expected['size_bytes'],'image_zip_expansion');target.flush();os.fsync(target.fileno())
    with pinned(output/'candidate-image.tar.gz',MAX_TAR+16*1024**2) as (compressed,size):
        require(size==expected['size_bytes'] and digest(compressed,size)==expected['sha256'],'image_gzip_identity');compressed.seek(0)
        fd=os.open(output/'candidate-image.tar',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'wb') as target,gzip.GzipFile(fileobj=compressed) as decoded:
            total=0
            while block:=decoded.read(min(65536,expected['uncompressed_bytes']+1-total)):
                total+=len(block);require(total<=expected['uncompressed_bytes'],'image_gzip_expansion');target.write(block)
            require(total==expected['uncompressed_bytes'],'image_gzip_expansion');target.flush();os.fsync(target.fileno())
    with pinned(output/'candidate-image.tar',MAX_TAR) as (stream,size):result=validate_archive(stream,size,evidence['config'])
    write(output/'image-export.json',export_raw)
    return {'schema':'market_os_composition_base_admission/1','artifact_id':IMAGE_ARTIFACT[0],'run_id':RUN,
        'artifact_zip_sha256':IMAGE_ARTIFACT[2],'export_receipt_sha256':sha256(export_raw).hexdigest(),**result,
        'source_commit':SOURCE,'security_accepted':False,'deployment_eligible':False}


def public_blob(repo,commit,path,size,blob):
    require(commit in (BASELINE,SOURCE) and type(size) is int and 0<size<=4*1024**2
        and MARKET.public_path(path) and re.fullmatch('[a-f0-9]{40}',blob),'test_source_identity')
    operand=commit+':'+path
    actual=COMMON.command(['git','--no-optional-locks','-C',str(repo),'cat-file','-s',operand],seconds=10,maximum=64).strip()
    require(actual==str(size).encode(),'test_source_size')
    raw=COMMON.command(['git','--no-optional-locks','-C',str(repo),'cat-file','blob',operand],seconds=10,maximum=size)
    require(len(raw)==size and sha1(b'blob '+str(size).encode()+b'\0'+raw).hexdigest()==blob,'test_source_blob');return raw


def test_capsule(root,repo,evidence):
    proof=evidence['tests'];rows=proof['files'];require(len(rows)==17 and len({r['path'] for r in rows})==17
        and {r['path'] for r in rows}=={*MARKET.TEST_PATHS,'deploy/market-data/Dockerfile'},'test_capsule_inventory')
    payload=root/'payload';payload.mkdir(mode=0o700);target=payload/'tests';target.mkdir(mode=0o755);target.chmod(0o755)
    for row in rows:
        path=row['path'];source=BASELINE if path in MARKET.TEST_PATHS else SOURCE
        require(row['source_commit']==source and row['transformation'] in ('none','security_base_assertion_only'),'test_source_identity')
        # Original size is recovered from cat-file, then bounded before the blob command.
        raw_size=COMMON.command(['git','--no-optional-locks','-C',str(repo),'cat-file','-s',source+':'+path],seconds=10,maximum=64)
        require(re.fullmatch(rb'[1-9][0-9]{0,6}\n',raw_size) is not None,'test_source_size')
        original=public_blob(repo,source,path,int(raw_size),row['git_blob']);effective=MARKET.test_transform(path,original)
        require(sha256(original).hexdigest()==row['original_sha256'] and len(effective)==row['bytes']
            and sha256(effective).hexdigest()==row['sha256']
            and row['transformation']==('none' if effective==original else 'security_base_assertion_only'),'test_source_digest')
        mode=COMMON.command(['git','--no-optional-locks','-C',str(repo),'ls-tree',source,'--',path],seconds=10,maximum=1024)
        require(mode in ((m+' blob '+row['git_blob']+'\t'+path+'\n').encode() for m in ('100644','100755')),'test_source_mode')
        write(MARKET.public_destination(target,path),effective,0o755 if mode.startswith(b'100755 ') else 0o644)
    control=COMMON.command(['git','--no-optional-locks','-C',str(repo),'cat-file','blob',SOURCE+':'+MARKET.PUBLIC_PROOF_PATH],seconds=10,maximum=MAX_JSON)
    require(sha256(control).hexdigest()==proof['public_baseline_sha256']==MARKET.E46_PUBLIC_FINGERPRINT_SHA256,'public_control_pin')
    write(target/'public-baseline.json',control,0o644);write(target/'test-sources.json',evidence['raw']['payload/tests/test-sources.json'],0o644)
    return target


def audit_program():
    require(COMMON.AUDIT.count("('/app','/usr/local')")==1,'audit_scope')
    value=COMMON.AUDIT.replace("('/app','/usr/local')","('/app','/usr/local','/opt')")
    require(value.count('os.walk(root,followlinks=False)')==1,'audit_scope')
    return value.replace('os.walk(root,followlinks=False)',"os.walk(root,followlinks=False,onerror=lambda error:(_ for _ in ()).throw(error))")


def compare_inventory(before,after):
    # This exact package contract is independent of the original App image.
    COMMON.compare_inventory(before,after)


def dockerfile(alias):
    require(re.fullmatch(r'security-market-os-base-[0-9]+-[0-9]+:ci',alias),'alias_identity')
    # Only the normal package installation phase runs as root. Restore exact Config.User.
    return COMMON.dockerfile_for(alias.replace('security-market-os-base-','security-os-base-')).replace(
        'FROM '+alias.replace('security-market-os-base-','security-os-base-')+'\n','FROM '+alias+'\nUSER 0:0\n')+'USER 10001:10001\n'


def alias_plan():
    token=os.environ['GITHUB_RUN_ID']+'-'+os.environ['GITHUB_RUN_ATTEMPT'];require(re.fullmatch('[0-9]+-[0-9]+',token),'ci_identity')
    return {'schema':'market_os_composition_alias/1','name':'security-market-os-base-'+token+':ci',
        'image_id':IMAGE,'recipe_commit':os.environ['GITHUB_SHA'],'owner':token}


def cleanup(root):
    ci(root)
    with sources(root):
        path=root/'reports/base-alias-plan.json'
        if not path.exists():return
        plan=document(read(path,65536));require(plan==alias_plan(),'alias_owner')
        raw=COMMON.docker(root,['image','ls','--no-trunc','--quiet',plan['name']],maximum=256).strip()
        require(raw==IMAGE.encode(),'alias_cleanup_unknown')
        value=COMMON.image_record(COMMON.docker(root,['image','inspect',plan['name']],maximum=MAX_JSON),IMAGE)
        require(value['Config']['User']=='10001:10001','alias_owner')
        COMMON.docker(root,['image','rm','--no-prune',plan['name']],maximum=65536)
        write(root/'reports/base-alias-cleanup.json',encoded({'schema':'market_os_composition_alias_cleanup/1',
            'status':'removed_exact_owned_alias','plan_sha256':sha256(read(path,65536)).hexdigest(),
            'security_accepted':False,'image_deletion_or_daemon_cancellation_proven':False}))


def evidence_hashes(root):return {name:sha256(read(root/'reports'/name,maximum)).hexdigest() for name,maximum in REPORTS.items()}


def validate_component(raw,image,commit):
    require(0<len(raw)<=131072,'component_receipt_bound');value=document(raw)
    require(value.get('schema')=='security_image_component_presence/1' and value.get('image_id')==image
        and value.get('source_commit')==commit and value.get('verifier_sha256')==HELPERS['verify_image_component_presence.py'],
        'component_receipt_identity')
    require(value.get('scope')=='known_vendor_component_paths_and_names_plus_actual_perl_inc'
        and all(value.get(k) is False for k in ('security_accepted','scanner_findings_modified','production_mounts_or_data_used',
            'dynamic_runtime_code_downloads_analyzed','renamed_or_obfuscated_embedded_code_analyzed','directory_symlinks_followed')),
        'component_receipt_scope');return value


def validate_reports(record,raw,sbom):
    require(raw.get('SchemaVersion')==2 and raw.get('ArtifactType')=='container_image'
        and raw.get('Metadata',{}).get('ImageID')==record['image_id'] and type(raw.get('Results')) is list and raw['Results'],'raw_report_identity')
    config=raw['Metadata']['ImageConfig']
    require(oci_runtime_config(config['config'])==oci_runtime_config(record['baseline_config'])
        and config['rootfs']['diff_ids']==record['candidate_layers'],'raw_report_config')
    component=sbom.get('metadata',{}).get('component',{})
    require(sbom.get('bomFormat')=='CycloneDX' and component.get('type')=='container' and component.get('name')==record['image_id']
        and [p.get('value') for p in component.get('properties',[]) if p.get('name')=='aquasecurity:trivy:ImageID']==[record['image_id']],
        'sbom_image_identity')
    for name,row in COMMON.PACKAGES.items():
        require({r.get('version') for r in sbom.get('components',[]) if r.get('name')==name}=={row[0]},'sbom_package_version')
    counts={k:0 for k in ('UNKNOWN','LOW','MEDIUM','HIGH','CRITICAL')}
    for result in raw['Results']:
        require(type(result) is dict and type(result.get('Class')) is str and type(result.get('Type')) is str,'raw_report_shape')
        findings=result.get('Vulnerabilities');require(findings is None or type(findings) is list,'raw_report_shape')
        for row in findings or []:
            require(type(row) is dict and row.get('Severity') in counts and type(row.get('VulnerabilityID')) is str
                and type(row.get('PkgName')) is str,'raw_report_shape');counts[row['Severity']]+=1
    return counts


def raw_gate(value):
    require(type(value) is dict and value.get('schema')=='market_os_composition_image_export/1'
        and value.get('security_accepted') is False,'gate_receipt')
    counts=value.get('raw_counts');require(type(counts) is dict and set(counts)=={'UNKNOWN','LOW','MEDIUM','HIGH','CRITICAL'}
        and all(type(n) is int and n>=0 for n in counts.values()),'gate_receipt')
    require(counts['HIGH']==counts['CRITICAL']==0,'raw_gate_failed')


def compose(root,repo):
    ci(root)
    with sources(root) as identity:
        (root/'reports').mkdir(mode=0o700,exist_ok=True);phase(root,'sealed_sources')
        for name in ('inputs','context','docker-config'):(root/name).mkdir(mode=0o700)
        (root/'context/capsule').mkdir(mode=0o755);(root/'context/capsule').chmod(0o755)
        floor=os.statvfs(root);require(floor.f_bavail*floor.f_frsize>=12*1024**3,'runner_space')
        phase(root,'download_evidence');COMMON.fetch_artifact(*EVIDENCE_ARTIFACT[:1],root/'inputs/evidence.zip',*EVIDENCE_ARTIFACT[1:])
        phase(root,'admit_evidence');original=admit_evidence(root/'inputs/evidence.zip')
        write(root/'reports/original-evidence.json',encoded({'run_id':RUN,'artifact':EVIDENCE_ARTIFACT,
            'source_commit':SOURCE,'payload_sha256':PAYLOAD_SHA,'test_sources_sha256':TEST_SHA,'build_sha256':BUILD_SHA,
            'raw_report_sha256':RAW_SHA,'sbom_sha256':SBOM_SHA,'security_accepted':False}))
        phase(root,'download_image');COMMON.fetch_artifact(*IMAGE_ARTIFACT[:1],root/'inputs/image.zip',*IMAGE_ARTIFACT[1:])
        phase(root,'admit_image');admitted=admit_image(root/'inputs/image.zip',root/'admitted',original)
        write(root/'reports/base-admission.json',encoded(admitted))
        phase(root,'load_image')
        with pinned(root/'admitted/candidate-image.tar',MAX_TAR) as (stream,size):
            require(size==admitted['docker_archive_bytes'] and digest(stream,size)==admitted['docker_archive_sha256'],'archive_changed')
            stream.seek(0);COMMON.docker(root,['image','load'],seconds=180,input_stream=stream)
        phase(root,'inspect_baseline');before=COMMON.image_record(COMMON.docker(root,['image','inspect',IMAGE],maximum=MAX_JSON),IMAGE)
        require(oci_runtime_config(before['Config'])==oci_runtime_config(original['config']['config'])
            and before['RootFS']['Layers']==original['build']['rootfs_layers'],'baseline_image_drift')
        MARKET.image_metadata(COMMON,root,IMAGE,original['payload'])
        phase(root,'historical_capsule');test_capsule(root,repo,original)
        phase(root,'download_packages');families={};packages=[]
        for group,record in COMMON.GROUPS.items():
            _,identifier,size,expected,*_=record;path=root/'inputs'/(group+'.zip');COMMON.fetch_artifact(identifier,path,size,expected)
            families[group]=COMMON.admit_packages(path,group,root/'context/capsule');packages+=families[group]['packages']
        require(len(packages)==15 and {r['name'] for r in packages}==set(COMMON.PACKAGES),'package_inventory')
        write(root/'context/capsule/manifest.json',encoded({'schema':'security_os_composition_packages/1','packages':packages}),0o644)
        phase(root,'audit_baseline');left=document(COMMON.isolated(root,IMAGE,audit_program(),'before',maximum=4*1024**2).splitlines()[-1])
        plan=alias_plan();phase(root,'create_alias')
        require(not COMMON.docker(root,['image','ls','--no-trunc','--quiet',plan['name']],maximum=256).strip(),'alias_exists')
        write(root/'reports/base-alias-plan.json',encoded(plan))
        COMMON.docker(root,['image','tag',IMAGE,plan['name']])
        require(COMMON.image_record(COMMON.docker(root,['image','inspect',plan['name']],maximum=MAX_JSON),IMAGE)['Config']==before['Config'],'alias_owner')
        phase(root,'inspect_builder');COMMON.inspect_builder(root)
        write(root/'context/Dockerfile',dockerfile(plan['name']).encode(),0o644);write(root/'context/installer.py',COMMON.INSTALLER.encode(),0o644)
        phase(root,'build_candidate');COMMON.docker(root,['build','--builder','default','--pull=false','--no-cache','--network=none',
            '--iidfile',str(root/'candidate.id'),str(root/'context')],seconds=600,maximum=32*1024**2,combine_stderr=True,log_path=root/'reports/build.log')
        image=read(root/'candidate.id',256).decode().strip();require(re.fullmatch('sha256:[a-f0-9]{64}',image),'candidate_identity')
        phase(root,'inspect_candidate');after=COMMON.image_record(COMMON.docker(root,['image','inspect',image],maximum=MAX_JSON),image)
        COMMON.compare_images(before,after);MARKET.image_metadata(COMMON,root,image,original['payload'])
        phase(root,'audit_candidate');right=document(COMMON.isolated(root,image,audit_program(),'after',maximum=4*1024**2).splitlines()[-1]);compare_inventory(left,right)
        write(root/'reports/baseline-inventory.json',encoded(left));write(root/'reports/candidate-inventory.json',encoded(right))
        phase(root,'native_checks');native=document(COMMON.isolated(root,image,MARKET.NATIVE_PROGRAM,'market-native').splitlines()[-1])
        require(native.get('schema')=='market_security_native_checks/1' and native.get('image_id')==image and native.get('status')=='passed'
            and native.get('pydantic')=='2.8.2' and native.get('psycopg2')=='2.9.13' and native.get('runtime_data_used') is False
            and native.get('execution_uid')==10001 and native.get('execution_gid')==10001 and native.get('security_accepted') is False,'native_receipt')
        write(root/'reports/native.json',encoded(native))
        phase(root,'historical_tests');normal=document(COMMON.isolated(root,image,MARKET.candidate_test_program(original['payload']),
            'market-focused',mounts=((root/'payload/tests','/evidence'),),maximum=4*1024**2).splitlines()[-1])
        phase(root,'root_fixture');fixture=document(MARKET.root_fixture_container(COMMON,root,image,
            MARKET.candidate_test_program(original['payload'],root_fixture=True)).splitlines()[-1])
        focused=MARKET.validate_test_receipts(original['payload'],image,normal,fixture)
        for name,value in (('focused-nonroot.json',normal),('focused-root-fixture.json',fixture),('focused.json',focused)):write(root/'reports'/name,encoded(value))
        phase(root,'component_probe');raw=read(root/'source/verify_image_component_presence.py',65536)
        component=COMPONENT.in_container(image,os.environ['GITHUB_SHA'],raw,HELPERS['verify_image_component_presence.py'])
        validate_component(component,image,os.environ['GITHUB_SHA'])
        write(root/'reports/component-presence.json',component)
        phase(root,'acl_probe');COMMON.acl_consumer_probe(root,image,os.environ['GITHUB_SHA'])
        record={'schema':'market_os_composition/1','status':'composed_tested_not_accepted','image_id':image,'parent_image_id':IMAGE,
            'original_source_commit':SOURCE,'recipe':identity,'families':families,'baseline_config':before['Config'],
            'baseline_layers':before['RootFS']['Layers'],'candidate_layers':after['RootFS']['Layers'],
            'original_oci_config':original['config']['config'],'whole_config_unchanged':True,'protected_app_python_opt_unchanged':True,
            'libc_unchanged':True,'historical_tests':85,'product_files':93,'app_or_market_recompiled':False,
            'full_runtime_matrix_completed':False,'security_accepted':False,'deployment_eligible':False,
            'raw_scanner_gate_waived':False,'production_installed':False,'deployment_performed':False,
            'buildkit_resource_cgroup_asserted':False,'evidence_sha256':evidence_hashes(root)}
        write(root/'reports/composition.json',encoded(record));write(root/'empty-trivy.yaml',b'{}\n');phase(root,'composition_complete')
        with Path(os.environ['GITHUB_OUTPUT']).open('a') as output:output.write('image_id='+image+'\n')


def finalize(root):
    ci(root)
    with sources(root) as identity:
        phase(root,'finalize');record=document(read(root/'reports/composition.json'))
        require(record['schema']=='market_os_composition/1' and record['recipe']==identity
            and record['parent_image_id']==IMAGE and record['original_source_commit']==SOURCE
            and all(record.get(k) is True for k in ('whole_config_unchanged','protected_app_python_opt_unchanged','libc_unchanged'))
            and type(record.get('historical_tests')) is int and record['historical_tests']==85
            and type(record.get('product_files')) is int and record['product_files']==93
            and all(record[k] is False for k in ('security_accepted','deployment_eligible','raw_scanner_gate_waived','production_installed','deployment_performed'))
            and record['evidence_sha256']==evidence_hashes(root),'composition_evidence_changed')
        compare_inventory(document(read(root/'reports/baseline-inventory.json')),
                          document(read(root/'reports/candidate-inventory.json')))
        before=COMMON.image_record(COMMON.docker(root,['image','inspect',IMAGE],maximum=MAX_JSON),IMAGE)
        after=COMMON.image_record(COMMON.docker(root,['image','inspect',record['image_id']],maximum=MAX_JSON),record['image_id'])
        COMMON.compare_images(before,after)
        require(before['Config']==record['baseline_config'] and before['RootFS']['Layers']==record['baseline_layers']
            and after['RootFS']['Layers']==record['candidate_layers'],'image_drift')
        phase(root,'raw_reports');reports={name:read(root/'reports'/name,32*1024**2) for name in ('vulnerabilities.json','sbom.cdx.json')}
        counts=validate_reports(record,*(document(reports[n]) for n in ('vulnerabilities.json','sbom.cdx.json')))
        phase(root,'export');archive=COMMON.export_image(root,record['image_id'])
        require(record['evidence_sha256']==evidence_hashes(root),'composition_evidence_changed')
        write(root/'image-export.json',encoded({'schema':'market_os_composition_image_export/1','status':'exported_not_accepted',
            'image_id':record['image_id'],'parent_image_id':IMAGE,'original_source_commit':SOURCE,'recipe':identity,
            'composition_sha256':sha256(read(root/'reports/composition.json')).hexdigest(),'evidence_sha256':record['evidence_sha256'],
            'reports_sha256':{n:sha256(r).hexdigest() for n,r in reports.items()},'raw_counts':counts,'archive':archive,
            'whole_config_unchanged':True,'product_files_equal_baseline':True,'historical_tests':85,
            'vendor_debs_reused':True,'app_or_market_recompiled':False,'full_runtime_matrix_completed':False,
            'security_accepted':False,'findings_waived':False,'deployment_eligible':False,'deployment_performed':False}));phase(root,'complete')


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('action',choices=('compose','finalize','cleanup','gate'))
    parser.add_argument('--root',type=Path,required=True);parser.add_argument('--repo',type=Path)
    args=parser.parse_args(argv)
    try:
        if args.action=='compose':require(args.repo is not None,'repository_required');compose(args.root,args.repo)
        elif args.action=='finalize':finalize(args.root)
        elif args.action=='cleanup':cleanup(args.root)
        else:
            ci(args.root);raw_gate(document(read(args.root/'image-export.json')))
        return 0
    except Exception as error:
        # Only closed codes known to this public recipe are exposed, never exception strings.
        code=error.args[0] if type(error) is RuntimeError and len(error.args)==1 and type(error.args[0]) is str and error.args[0] in ERROR_CODES else 'unexpected_failure'
        if code=='unexpected_failure' and COMMON is not None:code=COMMON.public_error(error)
        failure={'schema':'market_os_composition_failure/1','phase':PHASE,'code':code,'security_accepted':False}
        if COMMON is not None and PHASE!='controller_admission' and args.action!='cleanup':
            try:write(args.root/'reports/failure.json',encoded(failure))
            except OSError:pass
        print(json.dumps(failure,sort_keys=True),file=sys.stderr);return 1


ERROR_CODES=frozenset(('duplicate_json_key','invalid_json_number','input_path','input_metadata','input_changed','input_bound','stream_bound','stream_size',
    'phase_invalid','github_ci_only','ci_root','ci_commit','source_inventory','source_changed','source_pin','controller_source_changed',
    'evidence_zip_identity','evidence_zip_inventory','evidence_zip_member','evidence_member_bound','evidence_member_pin','evidence_identity',
    'baseline_runtime_contract','export_identity','export_provenance','export_evidence','export_scope','export_archive','archive_timeout',
    'archive_interval','archive_short','legacy_projection','legacy_image','legacy_timestamp','legacy_rootfs','legacy_bound','legacy_collision',
    'archive_size','archive_trailer','archive_member','archive_trailing_payload','archive_missing','archive_json_bound','docker_manifest',
    'archive_config_identity','archive_config_changed','archive_layers','archive_tags','archive_layer_expansion','archive_layer_diff_id',
    'oci_layout','oci_descriptor','oci_blob','oci_index','oci_manifest','oci_depth','classic_layout','classic_version','classic_metadata',
    'classic_repositories','archive_closed_inventory','legacy_bytes','admission_output','image_zip_identity','image_zip_inventory',
    'image_zip_member','image_gzip_size','image_zip_expansion','image_gzip_identity','image_gzip_expansion','test_source_identity',
    'test_source_size','test_source_blob','test_source_mode','test_capsule_inventory','test_source_digest','public_control_pin','audit_scope','alias_identity',
    'ci_identity','alias_owner','alias_exists','alias_cleanup_unknown','runner_space','archive_changed','baseline_image_drift','package_inventory',
    'candidate_identity','native_receipt','composition_evidence_changed','image_drift','repository_required','gate_receipt','raw_gate_failed',
    'runtime_config','history_timestamp_invalid','component_receipt_bound','component_receipt_identity','component_receipt_scope','raw_report_identity','raw_report_config',
    'sbom_image_identity','sbom_package_version','raw_report_shape'))


if __name__=='__main__':raise SystemExit(main())
