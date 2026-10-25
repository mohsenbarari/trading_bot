"""CI-only closed nine-product overlays on the fixed tested E46 OS export."""
from contextlib import contextmanager, ExitStack
from hashlib import sha256
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

PARENT='sha256:2c6ddd332f0aac17613208e66980e0154933bcb23cf2b94bd3d228c3ad5fd4e7'
PARENT_RUN=40145507410
PARENT_SOURCE='e2948eefc6b4e186b13895d298bca38eb6a1a53b'
IMAGE_ARTIFACT=(12580172031,63183253,'88f4a81f387f357d7e7dc4c4812cbce324ec209606e565d153dd261af13c1a9d')
EVIDENCE_ARTIFACT=(12580311665,241567,'04e051e29ad84c34180317033f7ab3210c8629b9c6bc14d6b037a14544121280')
EXPORT_SHA='c241819a46f6a8cbebda4f60937655b90544312e0e7a1f31ff6a99d22da2a812'
# Exact public Config.Labels from the pinned2c6 parent export. These four
# inherited security labels describe its E46 stack ancestry, not the replacement
# product. Unknown parent keys/values are never silently retained or discarded.
PARENT_STACK_LABELS={
    'io.gold-trade.security.baseline.image':'sha256:e46aedbe135117301fb19183ff39587174162ca4a454ac60c7fccb2b302033cd',
    'io.gold-trade.security.baseline.tree':'259c9c4964864b7acfd92f94c9856e664130d082',
    'io.gold-trade.security.overlay.revision':'88299eb42bfa05361f8b8ed82358873336a6480e',
    'io.gold-trade.security.overlay.tree':'52d90fc10fb851298c256ef101c178beeaea934f'}
PARENT_LABELS={**PARENT_STACK_LABELS,
    'io.gold-trade.release.input-signature':'2df1bc87630d4fc86926daa6d9f3e3a50120ebb10bee6bebc102cb87b0c25ccf',
    'io.gold-trade.release.tree':'73863439a60e39629a21ecd52daa5305f5beaf1c',
    'org.opencontainers.image.description':'Docker-native private market data pipeline',
    'org.opencontainers.image.revision':'a6dcd636a1525a3a3d47e2a6083ca887b16080db',
    'org.opencontainers.image.title':'Trading Bot Private Market Pipeline',
    'org.opencontainers.image.version':'stage5-coin-parser-shadow'}
PRODUCT_LABEL_KEYS=frozenset(('io.gold-trade.release.input-signature','io.gold-trade.release.tree',
    'org.opencontainers.image.description','org.opencontainers.image.revision',
    'org.opencontainers.image.title','org.opencontainers.image.version'))
F79_LABEL_KEYS=frozenset(('io.gold-trade.capture.replay.baseline','io.gold-trade.capture.replay.fix-commit',
    'io.gold-trade.capture.replay.source-sha256','io.gold-trade.capture.replay.status',
    'io.gold-trade.observability.base-image','io.gold-trade.observability.source-sha256',
    'io.gold-trade.observability.status'))
HELPERS={'prepare_market_security_profile.py':'c998a45c5777df52e575ef3ea70cd65699708f05194d67b0ea3f1f52dd2b3e50',
    'compose_security_os_candidate.py':'10ccbe216fef04e5f7a7ce2ad31ac30eca7eeae090dfaaf8074fa3cc5c789d44',
    'compose_market_security_os_candidate.py':'c53cfd7ae4570314ef7f13d43ecd73de26436cf56407676ac2fe24d19dc1caa9',
    'prepare_market_security_payload.py':'9131fe760b94d54f2f3fcc577b8cb4d36f0170a0f28a2f0d8adfb750c2b78c84',
    'verify_image_component_presence.py':'36e92fdfd3105a9db6bc20fd75aade1238a5e0b053a581bfb24c7b5e1bf6a6d3',
    'verify_acl_consumer_presence.py':'001e51d270c079153cc38db1be53c723fbbc8a8bb9e981e718e7b657127bc778'}
MAX_TAR,MAX_JSON=2*1024**3,2*1024**2
COMMON=PRIOR=MARKET=PREP=COMPONENT=None
PHASE='controller_admission'
PHASES=frozenset(('controller_admission','sealed_sources','parent_evidence','parent_image','load_parent','parent_inspect',
    'prepare_profile','audit_parent','alias','builder','build','candidate_inspect','audit_candidate','native','historical',
    'root_fixture','replay','component','acl','composed','finalize','reports','export','complete','cleanup'))
REPORTS={'parent-admission.json':MAX_JSON,'parent-evidence.json':MAX_JSON,'profile-payload.json':MAX_JSON,
    'baseline-inventory.json':MAX_JSON,'candidate-inventory.json':MAX_JSON,'before-run.log':4*1024**2,
    'after-run.log':4*1024**2,'native.json':MAX_JSON,'market-native-run.log':MAX_JSON,
    'focused-nonroot.json':MAX_JSON,'focused-root-fixture.json':MAX_JSON,'focused.json':MAX_JSON,
    'market-profile-tests-run.log':4*1024**2,'market-root-fixture-run.log':4*1024**2,
    'component-presence.json':MAX_JSON,'acl-consumer-presence.json':4*1024**2,'acl-run.log':4*1024**2,
    'build.log':32*1024**2,'builder-inspect.log':65536,'base-alias-plan.json':65536}


def require(ok,code):
    if not ok:raise RuntimeError(code)


def encoded(value):return (json.dumps(value,sort_keys=True)+'\n').encode()


def document(raw):
    def pairs(rows):
        value={}
        for key,item in rows:
            require(key not in value,'profile_json');value[key]=item
        return value
    return json.loads(raw,object_pairs_hook=pairs,parse_constant=lambda _:require(False,'profile_json'))


@contextmanager
def pinned(path,maximum):
    require(path.is_absolute() and path.resolve(strict=True)==path,'input_path')
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    identity=lambda q:(q.st_dev,q.st_ino,q.st_mode,q.st_uid,q.st_nlink,q.st_size,q.st_mtime_ns,q.st_ctime_ns)
    try:
        before=os.fstat(fd);require(stat.S_ISREG(before.st_mode) and before.st_uid==os.geteuid()
            and before.st_nlink==1 and stat.S_IMODE(before.st_mode)==0o600 and 0<before.st_size<=maximum,'input_metadata')
        require(identity(before)==identity(path.lstat()),'input_changed')
        with os.fdopen(os.dup(fd),'rb') as stream:yield stream,before.st_size
        require(identity(before)==identity(os.fstat(fd))==identity(path.lstat()),'input_changed')
    finally:os.close(fd)


def read(path,maximum=MAX_JSON):
    with pinned(path,maximum) as (stream,size):
        raw=stream.read(size+1);require(len(raw)==size,'input_bound');return raw


def write(path,raw,mode=0o600):
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,mode)
    with os.fdopen(fd,'wb') as stream:stream.write(raw);stream.flush();os.fchmod(stream.fileno(),mode);os.fsync(stream.fileno())


def digest(stream,size):
    total=0;value=sha256()
    while block:=stream.read(min(65536,size+1-total)):
        total+=len(block);require(total<=size,'stream_bound');value.update(block)
    require(total==size,'stream_size');return value.hexdigest()


def ci(root):
    require(os.geteuid()==1001 and os.environ.get('GITHUB_ACTIONS')=='true'
        and os.environ.get('RUNNER_ENVIRONMENT')=='github-hosted'
        and os.environ.get('GITHUB_REPOSITORY')=='mohsenbarari/trading_bot','github_ci_only')
    parent=Path(os.environ['RUNNER_TEMP']).resolve(strict=True)
    require(root.is_absolute() and root.resolve(strict=True)==root and root.parent==parent
        and root.name.startswith('security-market-profile-') and root.stat().st_uid==1001
        and stat.S_IMODE(root.stat().st_mode)==0o700,'ci_root')
    require(re.fullmatch('[a-f0-9]{40}',os.environ.get('GITHUB_SHA',''))
        and re.fullmatch('[0-9]+-[0-9]+',os.environ.get('GITHUB_RUN_ID','')+'-'+os.environ.get('GITHUB_RUN_ATTEMPT','')),'ci_identity')
    os.umask(0o077)


def phase(root,name):
    global PHASE
    require(name in PHASES,'phase_invalid');PHASE=name
    temporary=root/'reports/current-phase.new';write(temporary,encoded({'schema':'market_security_profile_phase/1','phase':name}))
    os.replace(temporary,root/'reports/current-phase.json')


@contextmanager
def sources(root):
    global COMMON,PRIOR,MARKET,PREP,COMPONENT
    with ExitStack() as stack:
        identity=document(read(root/'source/identity.json',65536));own=Path(__file__).name
        names={own,'prepare_market_security_profile.py',*HELPERS}
        require(identity.get('recipe_commit')==os.environ['GITHUB_SHA'] and set(identity.get('files',{}))==names,'source_inventory')
        buffers={}
        for name in names:
            stream,size=stack.enter_context(pinned(root/'source'/name,131072));raw=stream.read(size+1)
            require(len(raw)==size and identity['files'][name]=={'bytes':size,'sha256':sha256(raw).hexdigest()},'source_changed')
            require(name not in HELPERS or sha256(raw).hexdigest()==HELPERS[name],'source_pin');buffers[name]=raw
        require(buffers[own]==read(Path(__file__).resolve(strict=True),131072),'controller_source_changed')
        modules={}
        for name in names-{own}:
            module=ModuleType('_sealed_market_profile_'+name[:-3]);module.__file__=str(root/'source'/name)
            exec(compile(buffers[name],module.__file__,'exec'),module.__dict__);modules[name]=module
        COMMON=modules['compose_security_os_candidate.py'];PRIOR=modules['compose_market_security_os_candidate.py']
        MARKET=modules['prepare_market_security_payload.py'];PREP=modules['prepare_market_security_profile.py']
        COMPONENT=modules['verify_image_component_presence.py']
        yield identity


def admit_parent_evidence(path):
    with pinned(path,EVIDENCE_ARTIFACT[1]) as (stream,size):
        require(size==EVIDENCE_ARTIFACT[1] and digest(stream,size)==EVIDENCE_ARTIFACT[2],'parent_evidence_zip');stream.seek(0)
        with ZipFile(stream) as archive:
            rows=archive.infolist();require(len(rows)==26 and len({q.filename for q in rows})==26,'parent_evidence_inventory')
            require(all(not q.is_dir() and not q.flag_bits&1 and q.compress_type in (0,8)
                and stat.S_IFMT(q.external_attr>>16) in (0,stat.S_IFREG) and 0<q.file_size<=32*1024**2 for q in rows),
                'parent_evidence_member')
            selected={}
            for name in ('image-export.json','source/identity.json','reports/composition.json','reports/vulnerabilities.json',
                         'reports/sbom.cdx.json','reports/base-alias-cleanup.json'):
                row=archive.getinfo(name);require(row.file_size<=MAX_JSON,'parent_evidence_bound')
                selected[name]=archive.read(name);require(len(selected[name])==row.file_size,'parent_evidence_bound')
            export=document(selected['image-export.json']);require(sha256(selected['image-export.json']).hexdigest()==EXPORT_SHA,'parent_export_pin')
            composition=document(selected['reports/composition.json']);recipe=document(selected['source/identity.json'])
            require(export.get('schema')=='market_os_composition_image_export/1' and export.get('image_id')==PARENT
                and export.get('status')=='exported_not_accepted' and export.get('recipe')==recipe
                and recipe.get('recipe_commit')==PARENT_SOURCE
                and export['composition_sha256']==sha256(selected['reports/composition.json']).hexdigest()
                and composition.get('image_id')==PARENT and composition.get('recipe')==recipe
                and export.get('evidence_sha256')==composition.get('evidence_sha256'),'parent_evidence_identity')
            require(set(export['evidence_sha256'])<=set(q.filename.removeprefix('reports/') for q in rows),'parent_evidence_inventory')
            for name,expected in export['evidence_sha256'].items():
                row=archive.getinfo('reports/'+name);require(row.file_size<=32*1024**2,'parent_evidence_bound')
                raw=archive.read(row);require(len(raw)==row.file_size and sha256(raw).hexdigest()==expected,'parent_evidence_hash')
            for name,expected in export['reports_sha256'].items():
                require(name in ('vulnerabilities.json','sbom.cdx.json')
                    and sha256(selected['reports/'+name]).hexdigest()==expected,'parent_evidence_hash')
    require(export.get('whole_config_unchanged') is True and export.get('product_files_equal_baseline') is True
        and export.get('vendor_debs_reused') is True and export.get('historical_tests')==85
        and all(export.get(k) is False for k in ('app_or_market_recompiled','full_runtime_matrix_completed',
            'security_accepted','findings_waived','deployment_eligible','deployment_performed')),'parent_evidence_scope')
    raw=document(selected['reports/vulnerabilities.json']);config=raw['Metadata']['ImageConfig']
    require(raw['Metadata']['ImageID']==PARENT and config['rootfs']['diff_ids']==composition['candidate_layers']
        and len(config['rootfs']['diff_ids'])==18,'parent_rootfs')
    cleanup=document(selected['reports/base-alias-cleanup.json'])
    require(cleanup.get('status')=='removed_exact_owned_alias' and cleanup.get('security_accepted') is False,'parent_cleanup')
    return {'export':export,'config':config,'composition':composition,'export_raw':selected['image-export.json'],
        'raw_sha256':sha256(selected['reports/vulnerabilities.json']).hexdigest(),
        'sbom_sha256':sha256(selected['reports/sbom.cdx.json']).hexdigest()}


# Fixed-packet derivative of C53 physical archive admission. The parent or
# exact current candidate identity is explicit; sealed C53 globals never change.
def validate_archive(stream,size,expected_config,expected_image,*,product=None):
    require(expected_image==PARENT or product is not None and re.fullmatch('sha256:[a-f0-9]{64}',expected_image),'archive_expected_image')
    SliceReader=PRIOR.SliceReader;safe_name=PRIOR.safe_name;legacy_configs=PRIOR.legacy_configs
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
    require(config_name in (expected_image[7:]+'.json','blobs/sha256/'+expected_image[7:]) and stored(config_name)==expected_image[7:],'archive_config_identity')
    config=document(data(config_name));projection=PRIOR.compare_scanner_config(config,expected_config)
    projection['original_nanosecond_config_sha256']=expected_image[7:]
    diffs=config['rootfs']['diff_ids'];require(type(layers) is list and 0<len(layers)==len(diffs)<=128
        and all(safe_name(n) for n in layers),'archive_layers')
    tags=row.get('RepoTags');require(tags is None or type(tags) is list and len(tags)<=8
        and all(type(t) is str and re.fullmatch(r'[A-Za-z0-9_./:@-]{1,256}',t) for t in tags),'archive_tags')
    allowed={'manifest.json',config_name,*layers};expanded=0
    for position,(name,diff) in enumerate(zip(layers,diffs)):
        source,length=member(name);prefix=source.read(min(2,length));source.seek(0)
        decoded=gzip.GzipFile(fileobj=source) if prefix==b'\x1f\x8b' else source;value=sha256()
        try:
            while block:=decoded.read(min(65536,MAX_TAR+1-expanded)):
                expanded+=len(block);require(expanded<=MAX_TAR,'archive_layer_expansion');value.update(block)
        finally:
            if decoded is not source:decoded.close()
        require(type(diff) is str and diff=='sha256:'+value.hexdigest(),'archive_layer_diff_id')
        if product is not None and position>=18:
            source.seek(0);validate_product_layer(source,product,end)
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
    return {'image_id':expected_image,'docker_archive_bytes':size,'docker_archive_sha256':archive_sha,
        'layers':len(layers),'legacy_compatibility_configs':legacy,'original_oci_config':config['config'],
        'scanner_history_projection':projection,'whole_oci_config':config}


def validate_product_layer(stream,product,end):
    """New filesystem bytes are confined to the five reviewed public COPY scopes."""
    expected={'app/'+q['path']:q for q in product};roots=tuple('app/'+q for q in MARKET.COPY_ROOTS)
    exact={'app/'+q for q in MARKET.COPY_FILES}
    directories={str(p) for name in expected for p in PurePosixPath(name).parents if str(p)!='.'}
    def scope(name):return name in exact or any(name==root or name.startswith(root+'/') for root in roots)
    total=0;seen=set();prefix=stream.read(2);stream.seek(0)
    decoded=gzip.GzipFile(fileobj=stream) if prefix==b'\x1f\x8b' else stream
    def block(count):
        nonlocal total
        require(time.monotonic()<end and type(count) is int and 0<=count<=4*1024**2,'product_layer_bound')
        require(total+count<=64*1024**2,'product_layer_bound');raw=decoded.read(count)
        require(len(raw)==count,'product_layer_short');total+=count;return raw
    try:
        while True:
            raw=block(512)
            if not any(raw):
                require(not any(block(512)),'product_layer_trailer')
                while True:
                    require(time.monotonic()<end,'archive_timeout')
                    tail=decoded.read(min(65536,64*1024**2+1-total))
                    if not tail:break
                    total+=len(tail);require(total<=64*1024**2 and not any(tail),'product_layer_trailer')
                break
            member=tarfile.TarInfo.frombuf(raw,'utf-8','strict');name=member.name.rstrip('/') if member.isdir() else member.name
            require(PRIOR.safe_name(name) and name not in seen and len(seen)<4096,'product_layer_member');seen.add(name)
            require(type(member.size) is int and 0<=member.size<=4*1024**2,'product_layer_bound')
            parent,base=name.rsplit('/',1) if '/' in name else ('',name)
            if base.startswith('.wh.'):
                target=parent+'/'+base[4:]
                allowed=any(parent==r or parent.startswith(r+'/') for r in roots) if base=='.wh..wh..opq' else scope(target)
                require(allowed and member.size==0 and (member.isfile() or member.type==tarfile.CHRTYPE
                    and member.devmajor==member.devminor==0),'product_layer_whiteout')
            elif member.isdir():
                require(name in directories and member.size==0 and member.uid==member.gid==10001
                    and member.mode==0o755,'product_layer_directory')
            else:
                require(member.isfile() and name in expected,'product_layer_outside_scope');row=expected[name]
                require(member.uid==member.gid==10001 and member.mode==(0o755 if row['mode']=='100755' else 0o644)
                    and member.size==row['bytes'],'product_layer_file')
                body=block(member.size);require(sha256(body).hexdigest()==row['sha256'],'product_layer_file')
                padding=(-member.size)%512
                if padding:require(not any(block(padding)),'product_layer_padding')
    finally:
        if decoded is not stream:decoded.close()
    return {'new_layer_bytes':total,'new_layer_entries':len(seen),'outside_product_scope_changes':False}


def decode_gzip(source,size,target,expected_size):
    require(type(size) is int and size>0 and type(expected_size) is int and 0<expected_size<=MAX_TAR,'gzip_bound')
    fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    end=time.monotonic()+180
    bounded=PRIOR.SliceReader(source,0,size,end)
    with os.fdopen(fd,'wb') as out,gzip.GzipFile(fileobj=bounded) as decoded:
        total=0
        while True:
            require(time.monotonic()<end,'archive_timeout');raw=decoded.read(min(65536,expected_size+1-total))
            if not raw:break
            total+=len(raw);require(total<=expected_size,'gzip_bound');out.write(raw)
        require(total==expected_size,'gzip_bound');out.flush();os.fsync(out.fileno())


def admit_parent_image(path,output,evidence):
    require(not os.path.lexists(output) and output.parent.resolve(strict=True)==output.parent,'admission_output')
    expected=evidence['export']['archive']
    require(expected=={'name':'candidate-image.tar.gz','format':'docker-archive+gzip','size_bytes':63555322,
        'sha256':'0ffeb9ca7d3ae73928040cfe7eb611826063e5ce44eed81562dbadd8b62f82be',
        'uncompressed_bytes':188604928},'parent_archive_metadata')
    with pinned(path,IMAGE_ARTIFACT[1]) as (stream,size):
        require(size==IMAGE_ARTIFACT[1] and digest(stream,size)==IMAGE_ARTIFACT[2],'parent_image_zip');stream.seek(0)
        with ZipFile(stream) as archive:
            rows=archive.infolist();require(len(rows)==2 and {q.filename for q in rows}=={'candidate-image.tar.gz','image-export.json'},'parent_image_inventory')
            require(all(not q.is_dir() and not q.flag_bits&1 and q.compress_type in (0,8)
                and stat.S_IFMT(q.external_attr>>16) in (0,stat.S_IFREG)
                and 0<q.file_size<=(MAX_JSON if q.filename=='image-export.json' else expected['size_bytes']) for q in rows),
                'parent_image_member')
            require(archive.read('image-export.json')==evidence['export_raw']
                and archive.getinfo('candidate-image.tar.gz').file_size==expected['size_bytes'],'parent_export_changed')
            output.mkdir(mode=0o700)
            with archive.open('candidate-image.tar.gz') as compressed:
                # ZipFile decompression is bounded by the advertised, authenticated
                # member size; gzip decompression has its separate 2GiB ceiling.
                fd=os.open(output/'candidate-image.tar.gz',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
                with os.fdopen(fd,'wb') as out:
                    total=0
                    while raw:=compressed.read(min(65536,expected['size_bytes']+1-total)):
                        total+=len(raw);require(total<=expected['size_bytes'],'parent_image_expansion');out.write(raw)
                    require(total==expected['size_bytes'],'parent_image_expansion');out.flush();os.fsync(out.fileno())
    with pinned(output/'candidate-image.tar.gz',expected['size_bytes']) as (stream,size):
        require(size==expected['size_bytes'] and digest(stream,size)==expected['sha256'],'parent_gzip_identity');stream.seek(0)
        decode_gzip(stream,size,output/'candidate-image.tar',expected['uncompressed_bytes'])
    with pinned(output/'candidate-image.tar',MAX_TAR) as (stream,size):result=validate_archive(stream,size,evidence['config'],PARENT)
    require(result['layers']==18,'parent_rootfs');return result


def expected_config(before,payload):
    require(before.get('User')=='10001:10001' and type(before.get('Env')) is list
        and all(type(q) is str for q in before['Env']) and type(before.get('Labels')) is dict,'parent_runtime_config')
    key='MARKET_PIPELINE_IMAGE_REVISION=';positions=[i for i,q in enumerate(before['Env']) if q.startswith(key)]
    require(len(positions)==1 and before['Env'][positions[0]]==key+'a6dcd636a1525a3a3d47e2a6083ca887b16080db'
        and before['Env'].count('PYTHONDONTWRITEBYTECODE=1')==1,'public_source_default')
    result={**before,'Env':list(before['Env'])};result['Env'][positions[0]]=key+payload['old_public_source_revision']
    old=payload['old_image_nonenv_config']
    require(PRIOR.same_json_value(before['Labels'],PARENT_LABELS),'parent_label_inventory')
    expected_keys=PRODUCT_LABEL_KEYS|(F79_LABEL_KEYS if payload['profile']=='f79cdda21c19' else frozenset())
    require(type(old.get('Labels')) is dict and set(old['Labels'])==expected_keys
        and all(type(k) is str and type(v) is str for k,v in old['Labels'].items()),'target_label_inventory')
    signature=payload.get('image_input_signature')
    require(type(signature) is str and re.fullmatch('[a-f0-9]{64}',signature),'overlay_input_signature')
    result['Labels']={**PARENT_STACK_LABELS,**old['Labels'],'io.gold-trade.release.input-signature':signature,
        'io.gold-trade.security.parent-image':PARENT,
        'io.gold-trade.security.product-proof':payload['source_binding']['source_sha256'],
        'io.gold-trade.security.recipe':payload['source_commit'],
        'io.gold-trade.security.product-overrides':'none' if payload['source_overrides'] is None else PREP.OVERRIDES[1]}
    for name,value in old.items():
        if name!='Labels':
            # The normal image-inspect template emits null for an omitted key.
            # Only this observation/API comparison treats omission as omission;
            # the entire before/after Docker Config remains exact below.
            actual={} if before.get(name) is None else PRIOR.oci_runtime_config({name:before[name]})
            observed={} if value is None else PRIOR.oci_runtime_config({name:value})
            require(PRIOR.same_json_value(actual,observed),'historical_runtime_config_drift')
    return result


def compare_images(before,after,payload):
    expected=expected_config(before['Config'],payload)
    require(after['Id']!=before['Id'] and PRIOR.same_json_value(after['Config'],expected),'candidate_config_drift')
    left,right=before['RootFS']['Layers'],after['RootFS']['Layers']
    require(len(left)==18 and len(right)==19 and right[:18]==left,'candidate_rootfs_drift');return expected


def replacement_program(seed_files,payload):
    return 'seed='+repr(seed_files)+'\ntarget='+repr(payload['files'])+'\n'+MARKET.PRODUCT_CHECK_SOURCE+r'''
import hashlib,os,pathlib,shutil,stat
assert os.geteuid()==0
def read(path,maximum):
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        before=os.fstat(fd);assert stat.S_ISREG(before.st_mode) and before.st_nlink==1 and 0<=before.st_size<=maximum
        raw=os.read(fd,before.st_size+1);identity=lambda s:(s.st_dev,s.st_ino,s.st_mode,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
        assert len(raw)==before.st_size and identity(before)==identity(os.fstat(fd))==identity(path.lstat());return raw
    finally:os.close(fd)
verify_product_files(pathlib.Path('/app'),seed,read)
# BuildKit can present a local context as root; the controller separately
# verifies host UID1001. Both ordinary public representations remain read-only.
source_uid=pathlib.Path('/product').lstat().st_uid
assert source_uid in (0,1001)
verify_product_files(pathlib.Path('/product'),target,read,expected_uid=source_uid)
roots=('core/market_intelligence','contracts/market_data','deploy/market-data/migrations')
files=('core/__init__.py','config/market_data_sources.v1.json')
for name in files:os.unlink('/app/'+name)
for name in roots:shutil.rmtree('/app/'+name)
directories={str(p) for row in target for p in pathlib.PurePosixPath(row['path']).parents if str(p)!='.'}
for name in sorted(directories,key=lambda x:(x.count('/'),x)):
    path=pathlib.Path('/app')/name;path.mkdir(exist_ok=True);assert path.is_dir() and not path.is_symlink()
    os.chown(path,10001,10001);path.chmod(0o755)
for row in target:
    raw=read(pathlib.Path('/product')/row['path'],4194304)
    assert len(raw)==row['bytes'] and hashlib.sha256(raw).hexdigest()==row['sha256']
    path=pathlib.Path('/app')/row['path'];fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb') as out:
        out.write(raw);out.flush();os.fchown(out.fileno(),10001,10001);os.fchmod(out.fileno(),0o755 if row['mode']=='100755' else 0o644)
    os.utime(path,(0,0),follow_symlinks=False)
for name in sorted(directories,key=lambda x:(-x.count('/'),x)):os.utime('/app/'+name,(0,0),follow_symlinks=False)
os.utime('/app',(0,0),follow_symlinks=False)
verify_product_files(pathlib.Path('/app'),target,read)
'''


def dockerfile(alias,payload,expected):
    require(re.fullmatch('security-market-profile-base-[0-9]+-[0-9]+:ci',alias),'alias_identity')
    labels=' '.join(json.dumps(k)+'='+json.dumps(v) for k,v in sorted(expected['Labels'].items())
                    if k!='io.gold-trade.release.input-signature')
    labels+=' "io.gold-trade.release.input-signature"="${IMAGE_INPUT_SIGNATURE}"'
    # Fixed Dockerfile bytes: the owner-specific alias is a transport binding to
    # PARENT, and the input signature is the ordinary explicit build argument.
    return ('ARG SECURITY_PARENT_ALIAS=security-market-profile-unbound:ci\nFROM ${SECURITY_PARENT_ALIAS}\n'
        'ARG IMAGE_INPUT_SIGNATURE\nUSER 0:0\nRUN --network=none --mount=type=bind,source=product,target=/product,ro '
        '--mount=type=bind,source=replace.py,target=/replace.py,ro '
        +json.dumps(['/usr/local/bin/python3','-I','-B','/replace.py'])+'\nUSER 10001:10001\n'
        +'ENV MARKET_PIPELINE_IMAGE_REVISION='+payload['old_public_source_revision']+'\nLABEL '+labels+'\n')


def bind_overlay_inputs(before,payload,seed_files,identity,alias):
    replacement=replacement_program(seed_files,payload).encode()
    placeholder={**payload,'image_input_signature':'0'*64}
    recipe=dockerfile(alias,placeholder,expected_config(before,placeholder)).encode()
    closure={'schema':'market_security_profile_overlay_inputs/1','profile':payload['profile'],'parent_image_id':PARENT,
        'parent_image_artifact':IMAGE_ARTIFACT,'parent_evidence_artifact':EVIDENCE_ARTIFACT,'parent_export_sha256':EXPORT_SHA,
        'parent_stack_ancestry_labels':dict(PARENT_STACK_LABELS),
        'parent_stack_label_scope':'inherited_E46_security_stack_ancestry_not_current_target_product',
        'product_files':payload['files'],'recipe':identity,
        'controls':{'dossier_sha256':PREP.DOSSIER[1],'stack_sha256':PREP.STACK[1],
            'source_sha256':payload['source_binding']['source_sha256'],'cache_sha256':payload['source_binding']['cache_sha256']},
        'source_overrides':payload['source_overrides'],
        'historical_capsule_sha256':payload['historical']['test_sources_sha256'],
        'replay_capsule_sha256':None if payload['replay'] is None else payload['replay']['test_sources_sha256'],
        'effective_dockerfile':{'bytes':len(recipe),'sha256':sha256(recipe).hexdigest()},
        'replacement_program':{'bytes':len(replacement),'sha256':sha256(replacement).hexdigest()},
        'build_argument_bindings':{'SECURITY_PARENT_ALIAS':{'image_id':PARENT,
            'owner_alias_namespace':'security-market-profile-base-<run>-<attempt>:ci','exact_owner_receipt_required':True},
            'IMAGE_INPUT_SIGNATURE':'sha256_of_this_document_encoded_as_sorted_JSON_with_terminal_newline'}}
    result={**payload,'baseline_input_signature':payload['old_image_nonenv_config']['Labels']['io.gold-trade.release.input-signature'],
        'overlay_inputs':closure,'image_input_signature':sha256(encoded(closure)).hexdigest()}
    require(recipe==dockerfile(alias,result,expected_config(before,result)).encode(),'overlay_recipe_self_reference')
    return result,recipe,replacement


AUDIT=r'''
import hashlib,json,os,pathlib,stat,subprocess
raw=subprocess.run(['/usr/bin/dpkg-query','-W','-f=${Package}\t${Version}\t${db:Status-Status}\n'],check=True,capture_output=True,timeout=20).stdout.decode()
rows=[line.split('\t') for line in raw.splitlines()];assert all(len(row)==3 and row[2]=='installed' for row in rows)
inventory={name.removesuffix(':amd64'):version for name,version,_ in rows};assert len(inventory)==len(rows)
roots=('/app/core/market_intelligence','/app/contracts/market_data','/app/deploy/market-data/migrations')
files=('/app/core/__init__.py','/app/config/market_data_sources.v1.json')
digest=hashlib.sha256();count=total=0
for root in ('/app','/usr/local','/opt'):
    for folder,dirs,names in os.walk(root,followlinks=False,onerror=lambda error:(_ for _ in ()).throw(error)):
        dirs[:]=sorted(name for name in dirs if str(pathlib.Path(folder)/name) not in roots)
        for name in sorted(dirs+names):
            path=pathlib.Path(folder)/name
            if str(path) in files:continue
            info=path.lstat();count+=1;assert count<=60000
            digest.update(json.dumps([str(path),info.st_mode,info.st_uid,info.st_gid,info.st_size if not stat.S_ISDIR(info.st_mode) else 0],ensure_ascii=True).encode())
            if stat.S_ISLNK(info.st_mode):digest.update(os.readlink(path).encode())
            elif stat.S_ISREG(info.st_mode):
                total+=info.st_size;assert total<=768*1024**2
                with path.open('rb') as source:
                    remaining=info.st_size
                    while remaining:
                        block=source.read(min(65536,remaining));assert block;remaining-=len(block);digest.update(block)
                    assert not source.read(1)
            else:assert stat.S_ISDIR(info.st_mode)
libc=hashlib.sha256(pathlib.Path('/usr/lib/x86_64-linux-gnu/libc.so.6').read_bytes()).hexdigest()
print(json.dumps({'inventory':inventory,'non_product_stack_sha256':digest.hexdigest(),'files':count,'bytes':total,'libc_sha256':libc},sort_keys=True))
'''


def compare_inventory(before,after):
    require(type(before) is dict and type(after) is dict and set(before)==set(after)
        =={'inventory','non_product_stack_sha256','files','bytes','libc_sha256'}
        and PRIOR.same_json_value(before,after),'non_product_stack_changed')
    require(type(after['inventory']) is dict and all(after['inventory'].get(name)==row[0]
        for name,row in COMMON.PACKAGES.items()),'parent_security_packages_changed')


def validate_tests(payload,image,normal,fixture,replay=None):
    expected=payload['historical']['test_ids'];root_id=MARKET.ROOT_FIXTURE_TEST_ID
    require(type(expected) is list and expected==sorted(expected) and len(expected)==len(set(expected))
        ==payload['historical']['expected_tests'] and root_id in expected,'profile_test_inventory')
    profiles=[(normal,10001,[q for q in expected if q!=root_id],'nonroot_product_tests',payload['historical']),
              (fixture,0,[root_id],'root_owned_metadata_fixture',payload['historical'])]
    require((replay is not None)==(payload['replay'] is not None),'profile_replay_inventory')
    if replay is not None:profiles.append((replay,10001,payload['replay']['test_ids'],'capture_replay_regressions',payload['replay']))
    for value,uid,ids,scope,capsule in profiles:
        require(type(value) is dict and value.get('schema')=='market_security_profile_tests/1'
            and value.get('image_id')==image and value.get('test_sources_sha256')==capsule['test_sources_sha256']
            and value.get('status')=='passed' and value.get('scope')==scope
            and type(value.get('tests')) is int and value['tests']==len(ids)
            and type(value.get('skips')) is int and value['skips']==0
            and type(value.get('execution_uid')) is int and type(value.get('execution_gid')) is int
            and value['execution_uid']==value['execution_gid']==uid
            and value.get('selected_test_ids')==ids and value.get('product_files_equal_baseline') is True
            and value.get('public_source_directory_mode')=='0755'
            and value.get('suite_execution_thread')=='owned_nonmain_test_thread'
            and all(value.get(k) is False for k in ('runtime_data_used','security_accepted')),'profile_test_result')
    return {'schema':'market_security_profile_focused_tests/1','image_id':image,'status':'passed',
        'historical_tests':len(expected),'historical_test_ids':expected,'historical_selections_disjoint':True,
        'historical_test_sources_sha256':payload['historical']['test_sources_sha256'],
        'capture_replay_tests':0 if replay is None else len(payload['replay']['test_ids']),
        'capture_replay_test_ids':[] if replay is None else payload['replay']['test_ids'],
        'shared_adca_methods_changed_but_not_selected':[] if replay is None else payload['replay']['shared_method_changes'],
        'shared_adca_support_methods_changed_and_bound_to_replay_capsule':[] if replay is None else payload['replay']['shared_support_method_changes'],
        'runtime_data_used':False,'full_runtime_matrix_completed':False,'security_accepted':False}


def alias_plan():
    token=os.environ['GITHUB_RUN_ID']+'-'+os.environ['GITHUB_RUN_ATTEMPT']
    require(re.fullmatch('[0-9]+-[0-9]+',token),'ci_identity')
    return {'schema':'market_security_profile_alias/1','name':'security-market-profile-base-'+token+':ci',
        'image_id':PARENT,'recipe_commit':os.environ['GITHUB_SHA'],'owner':token}


def cleanup(root):
    ci(root)
    with sources(root):
        path=root/'reports/base-alias-plan.json'
        if not path.exists():return
        plan=document(read(path,65536));require(plan==alias_plan(),'alias_owner')
        require(COMMON.docker(root,['image','ls','--no-trunc','--quiet',plan['name']],maximum=256).strip()==PARENT.encode(),
            'alias_cleanup_unknown')
        image=COMMON.image_record(COMMON.docker(root,['image','inspect',plan['name']],maximum=MAX_JSON),PARENT)
        require(image['Config']['User']=='10001:10001','alias_owner')
        COMMON.docker(root,['image','rm','--no-prune',plan['name']],maximum=65536)
        write(root/'reports/base-alias-cleanup.json',encoded({'schema':'market_security_profile_alias_cleanup/1',
            'status':'removed_exact_owned_alias','plan_sha256':sha256(read(path,65536)).hexdigest(),
            'image_deletion_or_daemon_cancellation_proven':False,'security_accepted':False}))


def evidence_hashes(root,payload):
    names=dict(REPORTS)
    if payload['replay'] is not None:names.update({'focused-replay.json':MAX_JSON,'market-profile-replay-run.log':4*1024**2})
    return {name:sha256(read(root/'reports'/name,maximum)).hexdigest() for name,maximum in names.items()}


def compose(root,repo,profile):
    ci(root)
    with sources(root) as identity:
        require(profile in PREP.PROFILES,'profile_unknown')
        (root/'reports').mkdir(mode=0o700,exist_ok=True);phase(root,'sealed_sources')
        for name in ('inputs','docker-config'):(root/name).mkdir(mode=0o700)
        floor=os.statvfs(root);require(floor.f_bavail*floor.f_frsize>=12*1024**3,'runner_space')
        phase(root,'parent_evidence');COMMON.fetch_artifact(EVIDENCE_ARTIFACT[0],root/'inputs/evidence.zip',*EVIDENCE_ARTIFACT[1:])
        evidence=admit_parent_evidence(root/'inputs/evidence.zip')
        write(root/'reports/parent-evidence.json',encoded({'run_id':PARENT_RUN,'artifact':EVIDENCE_ARTIFACT,
            'source_commit':PARENT_SOURCE,'export_sha256':EXPORT_SHA,'raw_sha256':evidence['raw_sha256'],
            'sbom_sha256':evidence['sbom_sha256'],'security_accepted':False}))
        phase(root,'parent_image');COMMON.fetch_artifact(IMAGE_ARTIFACT[0],root/'inputs/image.zip',*IMAGE_ARTIFACT[1:])
        admitted=admit_parent_image(root/'inputs/image.zip',root/'admitted',evidence)
        write(root/'reports/parent-admission.json',encoded(admitted))
        phase(root,'load_parent')
        with pinned(root/'admitted/candidate-image.tar',MAX_TAR) as (stream,size):
            require(size==admitted['docker_archive_bytes'] and digest(stream,size)==admitted['docker_archive_sha256'],'archive_changed')
            stream.seek(0);COMMON.docker(root,['image','load'],seconds=180,input_stream=stream)
        phase(root,'parent_inspect');before=COMMON.image_record(COMMON.docker(root,['image','inspect',PARENT],maximum=MAX_JSON),PARENT)
        require(PRIOR.same_json_value(PRIOR.oci_runtime_config(before['Config']),PRIOR.oci_runtime_config(admitted['original_oci_config']))
            and before['RootFS']['Layers']==evidence['config']['rootfs']['diff_ids'],'parent_image_drift')
        phase(root,'prepare_profile');payload=PREP.prepare(repo,os.environ['GITHUB_SHA'],root/'context',profile,MARKET)
        # The original c22 evidence binds all93 parent product bytes. No old
        # preparer invocation, old checkout, runtime data or new dependency build.
        COMMON.fetch_artifact(PRIOR.EVIDENCE_ARTIFACT[0],root/'inputs/original-e46.zip',*PRIOR.EVIDENCE_ARTIFACT[1:])
        original=PRIOR.admit_evidence(root/'inputs/original-e46.zip')
        seed_files=[q for q in original['payload']['files'] if MARKET.copied_path(q['path'])]
        require(len(seed_files)==93,'parent_product_inventory')
        plan=alias_plan();payload,recipe,replacement=bind_overlay_inputs(before['Config'],payload,seed_files,identity,plan['name'])
        write(root/'reports/profile-payload.json',encoded(payload));expected=expected_config(before['Config'],payload)
        phase(root,'audit_parent');left=document(COMMON.isolated(root,PARENT,AUDIT,'before',maximum=4*1024**2).splitlines()[-1])
        phase(root,'alias')
        require(not COMMON.docker(root,['image','ls','--no-trunc','--quiet',plan['name']],maximum=256).strip(),'alias_exists')
        write(root/'reports/base-alias-plan.json',encoded(plan));COMMON.docker(root,['image','tag',PARENT,plan['name']])
        require(COMMON.image_record(COMMON.docker(root,['image','inspect',plan['name']],maximum=MAX_JSON),PARENT)['Config']==before['Config'],
            'alias_owner')
        phase(root,'builder');COMMON.inspect_builder(root)
        write(root/'context/Dockerfile',recipe,0o644);write(root/'context/replace.py',replacement,0o644)
        phase(root,'build');COMMON.docker(root,['build','--builder','default','--pull=false','--no-cache','--network=none',
            '--build-arg','SECURITY_PARENT_ALIAS='+plan['name'],'--build-arg','IMAGE_INPUT_SIGNATURE='+payload['image_input_signature'],
            '--iidfile',str(root/'candidate.id'),str(root/'context')],seconds=600,maximum=32*1024**2,
            combine_stderr=True,log_path=root/'reports/build.log')
        image=read(root/'candidate.id',256).decode().strip();require(re.fullmatch('sha256:[a-f0-9]{64}',image),'candidate_identity')
        phase(root,'candidate_inspect');after=COMMON.image_record(COMMON.docker(root,['image','inspect',image],maximum=MAX_JSON),image)
        expected=compare_images(before,after,payload)
        phase(root,'audit_candidate');right=document(COMMON.isolated(root,image,AUDIT,'after',maximum=4*1024**2).splitlines()[-1])
        compare_inventory(left,right)
        write(root/'reports/baseline-inventory.json',encoded(left));write(root/'reports/candidate-inventory.json',encoded(right))
        phase(root,'native');native=document(COMMON.isolated(root,image,MARKET.NATIVE_PROGRAM,'market-native').splitlines()[-1])
        require(native.get('schema')=='market_security_native_checks/1' and native.get('image_id')==image and native.get('status')=='passed'
            and native.get('pydantic')=='2.8.2' and native.get('psycopg2')=='2.9.13'
            and native.get('execution_uid')==native.get('execution_gid')==10001
            and all(native.get(k) is False for k in ('runtime_data_used','security_accepted')),'native_receipt')
        write(root/'reports/native.json',encoded(native))
        # The existing root-fixture primitive has one fixed /payload/tests mount.
        # Publish that one alias by renaming the public prepared capsule, keeping
        # its bytes/UID/modes unchanged. Other tests use that exact same directory.
        (root/'payload').mkdir(mode=0o700);os.rename(root/'context/tests',root/'payload/tests')
        phase(root,'historical');normal=document(COMMON.isolated(root,image,PREP.candidate_test_program(payload,MARKET),
            'market-profile-tests',mounts=((root/'payload/tests','/evidence'),),maximum=4*1024**2).splitlines()[-1])
        phase(root,'root_fixture');fixture=document(MARKET.root_fixture_container(COMMON,root,image,
            PREP.candidate_test_program(payload,MARKET,root_fixture=True)).splitlines()[-1])
        replay=None
        if payload['replay'] is not None:
            phase(root,'replay');replay=document(COMMON.isolated(root,image,PREP.candidate_test_program(payload,MARKET,replay=True),
                'market-profile-replay',mounts=((root/'context/replay-tests','/evidence'),),maximum=4*1024**2).splitlines()[-1])
            write(root/'reports/focused-replay.json',encoded(replay))
        focused=validate_tests(payload,image,normal,fixture,replay)
        for name,value in (('focused-nonroot.json',normal),('focused-root-fixture.json',fixture),('focused.json',focused)):
            write(root/'reports'/name,encoded(value))
        phase(root,'component');probe_raw=read(root/'source/verify_image_component_presence.py',65536)
        component=COMPONENT.in_container(image,os.environ['GITHUB_SHA'],probe_raw,HELPERS['verify_image_component_presence.py'])
        PRIOR.validate_component(component,image,os.environ['GITHUB_SHA']);write(root/'reports/component-presence.json',component)
        phase(root,'acl');COMMON.acl_consumer_probe(root,image,os.environ['GITHUB_SHA'])
        record={'schema':'market_security_profile_composition/1','status':'composed_tested_not_accepted','profile':profile,
            'image_id':image,'parent_image_id':PARENT,'recipe':identity,'payload_sha256':sha256(encoded(payload)).hexdigest(),
            'image_input_signature':payload['image_input_signature'],'baseline_input_signature':payload['baseline_input_signature'],
            'parent_config':before['Config'],'candidate_config':expected,'parent_layers':before['RootFS']['Layers'],
            'candidate_layers':after['RootFS']['Layers'],'historical_tests':payload['historical']['expected_tests'],
            'product_files':len(payload['files']),'all_dependencies_reused':True,'all_non_product_stack_hashes_equal':True,
            'source_revision_default_only_env_delta':True,'final_new_layer_scope_verified':False,
            'buildkit_resource_cgroup_asserted':False,'security_accepted':False,'deployment_eligible':False,
            'findings_waived':False,'runtime_data_used':False,'full_runtime_matrix_completed':False,
            'evidence_sha256':evidence_hashes(root,payload)}
        write(root/'reports/composition.json',encoded(record))
        for name in ('vulnerabilities.json','sbom.cdx.json'):write(root/'reports'/name,b'')
        write(root/'empty-trivy.yaml',b'{}\n');phase(root,'composed')
        with Path(os.environ['GITHUB_OUTPUT']).open('a') as out:out.write('image_id='+image+'\n')


def validate_reports(record,payload,raw,sbom):
    require(raw.get('SchemaVersion')==2 and raw.get('ArtifactType')=='container_image'
        and raw.get('Metadata',{}).get('ImageID')==record['image_id'] and type(raw.get('Results')) is list
        and raw['Results'],'raw_report_identity')
    config=raw['Metadata']['ImageConfig']
    require(PRIOR.same_json_value(PRIOR.oci_runtime_config(config['config']),PRIOR.oci_runtime_config(record['candidate_config']))
        and config['rootfs']['diff_ids']==record['candidate_layers'],'raw_report_config')
    component=sbom.get('metadata',{}).get('component',{})
    require(sbom.get('bomFormat')=='CycloneDX' and component.get('type')=='container'
        and component.get('name')==record['image_id']
        and [q.get('value') for q in component.get('properties',[]) if q.get('name')=='aquasecurity:trivy:ImageID']==[record['image_id']],
        'sbom_image_identity')
    for name,row in COMMON.PACKAGES.items():
        require({q.get('version') for q in sbom.get('components',[]) if q.get('name')==name}=={row[0]},'sbom_package_version')
    expected={(q['name'].lower().replace('_','-'), '2.9.13' if q['name']=='psycopg2-binary' else q['version'])
              for q in payload['old_public_packages']}
    observed=[]
    for result in raw['Results']:
        for package in result.get('Packages',[]) or []:
            if package.get('FilePath','').startswith('opt/market-python/'):
                observed.append((package['Name'].lower().replace('_','-'),package['Version']))
    require(len(observed)==len(set(observed))==9 and set(observed)==expected,'raw_python_packages')
    counts={k:0 for k in ('UNKNOWN','LOW','MEDIUM','HIGH','CRITICAL')}
    for result in raw['Results']:
        require(type(result) is dict and type(result.get('Class')) is str and type(result.get('Type')) is str,'raw_report_shape')
        findings=result.get('Vulnerabilities');require(findings is None or type(findings) is list,'raw_report_shape')
        for row in findings or []:
            require(type(row) is dict and row.get('Severity') in counts and type(row.get('VulnerabilityID')) is str
                and type(row.get('PkgName')) is str,'raw_report_shape');counts[row['Severity']]+=1
    return counts


def finalize(root):
    ci(root)
    with sources(root) as identity:
        phase(root,'finalize');record=document(read(root/'reports/composition.json'))
        payload=document(read(root/'reports/profile-payload.json'))
        require(record.get('schema')=='market_security_profile_composition/1' and record.get('recipe')==identity
            and record.get('parent_image_id')==PARENT and record.get('profile')==payload.get('profile') in PREP.PROFILES
            and record.get('payload_sha256')==sha256(encoded(payload)).hexdigest()
            and record.get('image_input_signature')==payload.get('image_input_signature')
                ==sha256(encoded(payload['overlay_inputs'])).hexdigest()
            and record.get('baseline_input_signature')==payload.get('baseline_input_signature')
            and payload['overlay_inputs'].get('schema')=='market_security_profile_overlay_inputs/1'
            and payload['overlay_inputs'].get('recipe')==identity
            and payload['overlay_inputs'].get('product_files')==payload['files']
            and PRIOR.same_json_value(payload['overlay_inputs'].get('parent_stack_ancestry_labels'),PARENT_STACK_LABELS)
            and payload['overlay_inputs'].get('parent_stack_label_scope')=='inherited_E46_security_stack_ancestry_not_current_target_product'
            and all(record.get(k) is True for k in ('all_dependencies_reused','all_non_product_stack_hashes_equal',
                                                  'source_revision_default_only_env_delta'))
            and all(record.get(k) is False for k in ('final_new_layer_scope_verified','security_accepted','deployment_eligible',
                'findings_waived','runtime_data_used','full_runtime_matrix_completed'))
            and record.get('evidence_sha256')==evidence_hashes(root,payload),'composition_evidence_changed')
        for name,field in (('Dockerfile','effective_dockerfile'),('replace.py','replacement_program')):
            public=MARKET.read(root/'context'/name,131072)
            require(payload['overlay_inputs'][field]=={'bytes':len(public),'sha256':sha256(public).hexdigest()},'overlay_build_input_changed')
        before=COMMON.image_record(COMMON.docker(root,['image','inspect',PARENT],maximum=MAX_JSON),PARENT)
        after=COMMON.image_record(COMMON.docker(root,['image','inspect',record['image_id']],maximum=MAX_JSON),record['image_id'])
        expected=compare_images(before,after,payload)
        require(PRIOR.same_json_value(before['Config'],record['parent_config'])
            and PRIOR.same_json_value(expected,record['candidate_config'])
            and before['RootFS']['Layers']==record['parent_layers'] and after['RootFS']['Layers']==record['candidate_layers'],'image_drift')
        compare_inventory(document(read(root/'reports/baseline-inventory.json')),document(read(root/'reports/candidate-inventory.json')))
        phase(root,'reports');reports={n:read(root/'reports'/n,32*1024**2) for n in ('vulnerabilities.json','sbom.cdx.json')}
        raw=document(reports['vulnerabilities.json']);counts=validate_reports(record,payload,raw,document(reports['sbom.cdx.json']))
        phase(root,'export');archive=COMMON.export_image(root,record['image_id'])
        with pinned(root/'candidate-image.tar.gz',MAX_TAR+16*1024**2) as (stream,size):
            require(size==archive['size_bytes'] and digest(stream,size)==archive['sha256'],'export_changed');stream.seek(0)
            decode_gzip(stream,size,root/'export-check.tar',archive['uncompressed_bytes'])
        with pinned(root/'export-check.tar',MAX_TAR) as (stream,size):
            proof=validate_archive(stream,size,raw['Metadata']['ImageConfig'],record['image_id'],product=payload['files'])
        require(proof['layers']==19 and proof['whole_oci_config']['rootfs']['diff_ids']==record['candidate_layers']
            and record['evidence_sha256']==evidence_hashes(root,payload),'export_changed')
        write(root/'reports/export-scope.json',encoded(proof))
        write(root/'image-export.json',encoded({'schema':'market_security_profile_image_export/1','status':'exported_not_accepted',
            'profile':payload['profile'],'image_id':record['image_id'],'parent_image_id':PARENT,'recipe':identity,
            'composition_sha256':sha256(read(root/'reports/composition.json')).hexdigest(),
            'payload_sha256':record['payload_sha256'],'evidence_sha256':record['evidence_sha256'],
            'image_input_signature':payload['image_input_signature'],'baseline_input_signature':payload['baseline_input_signature'],
            'input_signature_schema':'market_security_profile_overlay_inputs/1',
            'parent_stack_ancestry_labels':dict(PARENT_STACK_LABELS),
            'parent_stack_label_scope':'inherited_E46_security_stack_ancestry_not_current_target_product',
            'reports_sha256':{n:sha256(b).hexdigest() for n,b in reports.items()},
            'export_scope_sha256':sha256(read(root/'reports/export-scope.json')).hexdigest(),'raw_counts':counts,'archive':archive,
            'historical_tests':payload['historical']['expected_tests'],
            'capture_replay_tests':0 if payload['replay'] is None else payload['replay']['expected_tests'],
            'product_files':len(payload['files']),'all_dependencies_reused':True,'non_product_stack_unchanged':True,
            'new_layer_product_scope_verified':True,'source_revision_default_only_env_delta':True,
            'full_runtime_matrix_completed':False,'findings_waived':False,'runtime_data_used':False,
            'security_accepted':False,'deployment_eligible':False,'deployment_performed':False}));phase(root,'complete')


def raw_gate(record):
    require(record.get('schema')=='market_security_profile_image_export/1' and record.get('security_accepted') is False,'gate_receipt')
    counts=record.get('raw_counts');require(type(counts) is dict and set(counts)=={'UNKNOWN','LOW','MEDIUM','HIGH','CRITICAL'}
        and all(type(q) is int and q>=0 for q in counts.values()),'gate_receipt')
    require(counts['HIGH']==counts['CRITICAL']==0,'raw_gate_failed')


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=('compose','finalize','cleanup','gate'))
    p.add_argument('--root',type=Path,required=True);p.add_argument('--repo',type=Path);p.add_argument('--profile')
    args=p.parse_args(argv)
    try:
        if args.action=='compose':require(args.repo is not None,'repository_required');compose(args.root,args.repo,args.profile)
        elif args.action=='finalize':finalize(args.root)
        elif args.action=='cleanup':cleanup(args.root)
        else:ci(args.root);raw_gate(document(read(args.root/'image-export.json')))
        return 0
    except Exception as error:
        known=ERROR_CODES|(PRIOR.ERROR_CODES if PRIOR is not None else frozenset())
        trusted_type=type(error) is RuntimeError or MARKET is not None and type(error) is MARKET.PayloadError
        code=error.args[0] if trusted_type and len(error.args)==1 and type(error.args[0]) is str and error.args[0] in known else 'unexpected_failure'
        if code=='unexpected_failure' and COMMON is not None:code=COMMON.public_error(error)
        failure={'schema':'market_security_profile_failure/1','phase':PHASE,'code':code,'security_accepted':False}
        if COMMON is not None and PHASE!='controller_admission' and args.action!='cleanup':
            try:write(args.root/'reports/failure.json',encoded(failure))
            except OSError:pass
        print(json.dumps(failure,sort_keys=True),file=sys.stderr);return 1


ERROR_CODES=frozenset(('admission_output', 'alias_cleanup_unknown', 'alias_exists', 'alias_identity', 'alias_owner', 'archive_changed', 'archive_closed_inventory', 'archive_config_identity', 'archive_expected_image', 'archive_interval', 'archive_json_bound', 'archive_layer_diff_id', 'archive_layer_expansion', 'archive_layers', 'archive_member', 'archive_missing', 'archive_size', 'archive_tags', 'archive_timeout', 'archive_trailer', 'archive_trailing_payload', 'baseline_copy_input', 'baseline_copy_scope_drift', 'baseline_product_drift', 'baseline_public_fingerprint_pin', 'baseline_public_fingerprint_unreviewed', 'baseline_test_assertion', 'baseline_test_missing', 'baseline_tree_identity', 'build_evidence_changed', 'build_receipt', 'cache_classification_header', 'cache_classification_identity', 'cache_classification_inventory', 'cache_classification_metadata', 'cache_classification_path', 'cache_classification_pin', 'cache_classification_scope', 'cache_classification_shape', 'cache_classification_source', 'cache_classification_unknown', 'candidate_config_drift', 'candidate_identity', 'candidate_image_contract', 'candidate_image_drift', 'candidate_image_identity', 'candidate_rootfs_drift', 'candidate_test_selection', 'candidate_tests', 'ci_identity', 'ci_root', 'classic_layout', 'classic_metadata', 'classic_repositories', 'classic_version', 'command_helper_pin', 'composition_evidence_changed', 'context_byte_bound', 'context_directory_mode', 'context_inventory', 'context_metadata', 'context_source_drift', 'controller_source_changed', 'dependency_lock_shape', 'dependency_product_drift', 'dependency_security_delta', 'docker_manifest', 'export_changed', 'export_receipt', 'file_changed', 'file_metadata', 'fixture_container_identity', 'fixture_exit', 'gate_receipt', 'git_tree_bound', 'git_tree_shape', 'github_ci_only', 'gzip_bound', 'historical_runtime_config_drift', 'historical_test_inventory', 'image_drift', 'input_bound', 'input_changed', 'input_metadata', 'input_path', 'legacy_bytes', 'native_checks', 'native_receipt', 'non_product_stack_changed', 'oci_blob', 'oci_depth', 'oci_descriptor', 'oci_index', 'oci_layout', 'oci_manifest', 'output_parent', 'output_scope', 'overlay_build_input_changed', 'overlay_input_signature', 'overlay_recipe_self_reference', 'parent_archive_metadata', 'parent_cleanup', 'parent_evidence_bound', 'parent_evidence_hash', 'parent_evidence_identity', 'parent_evidence_inventory', 'parent_evidence_member', 'parent_evidence_scope', 'parent_evidence_zip', 'parent_export_changed', 'parent_export_pin', 'parent_gzip_identity', 'parent_image_drift', 'parent_image_expansion', 'parent_image_inventory', 'parent_image_member', 'parent_image_zip', 'parent_label_inventory', 'parent_product_inventory', 'parent_rootfs', 'parent_runtime_config', 'parent_security_packages_changed', 'payload_identity', 'phase_invalid', 'product_layer_bound', 'product_layer_directory', 'product_layer_file', 'product_layer_member', 'product_layer_outside_scope', 'product_layer_padding', 'product_layer_short', 'product_layer_trailer', 'product_layer_whiteout', 'profile_cache_control', 'profile_control_pin', 'profile_dependency_control', 'profile_dossier', 'profile_historical_tree', 'profile_json', 'profile_json_bound', 'profile_output', 'profile_override_base', 'profile_override_control', 'profile_override_result', 'profile_patch_context', 'profile_patch_shape', 'profile_product_drift', 'profile_product_inventory', 'profile_product_mode', 'profile_recipe', 'profile_replay_inventory', 'profile_runtime_control', 'profile_source_control', 'profile_source_identity', 'profile_source_missing', 'profile_stack_control', 'profile_test_execution', 'profile_test_inventory', 'profile_test_result', 'profile_unknown', 'public_fingerprint_bound', 'public_fingerprint_digest', 'public_fingerprint_identity', 'public_fingerprint_json', 'public_fingerprint_metadata', 'public_fingerprint_missing', 'public_fingerprint_path', 'public_fingerprint_scope', 'public_fingerprint_shape', 'public_git_output_bound', 'public_git_read_failed', 'public_output_directory', 'public_output_path', 'public_source_default', 'published_main_required', 'raw_gate_failed', 'raw_high_critical_findings', 'raw_python_packages', 'raw_report_config', 'raw_report_identity', 'raw_report_shape', 'repository_required', 'runner_space', 'sbom_image_identity', 'sbom_package_version', 'security_overlay_inventory', 'source_blob_identity', 'source_changed', 'source_commit', 'source_file_bound', 'source_inventory', 'source_link_refused', 'source_pin', 'source_publication_drift', 'source_repository', 'source_tree_identity', 'stream_bound', 'stream_size', 'target_label_inventory', 'test_execution_profile', 'unreviewed_market_profile'))


if __name__=='__main__':raise SystemExit(main())
