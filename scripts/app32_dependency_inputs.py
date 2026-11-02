"""Closed public fixture transport for the unchanged App32 assertion driver.

New ZIP/TAR envelopes are explicitly distinct from the deleted original artifacts.
Their payload must retain the original Config, complete RootFS and public runtime
Config. Only PREPARE has network authority; the smoke adapter is strictly local.
"""
from hashlib import sha256
import json
import os
from pathlib import Path
import stat
import zipfile

LOCK='deploy/security/app32-dependencies.lock.json'
REPO='mohsenbarari/trading_bot'
TAG='security-app32-fixtures-20261102'
TARGET='88830c49f508df9969f20ee53f03a24a1f1ea8ea'
EVIDENCE_IDS={'postgres':12579072479,'redis':12579152538}
MAX_ZIP=512*1024**2
MAX_JSON=65536
PUBLIC_CODES=frozenset(('app32_inventory','app32_transport_pin','app32_transport_inventory',
    'app32_transport_descriptor','app32_dependency_token','app32_release_metadata','app32_offline_transport'))


def need(ok,code):
    if not ok:raise RuntimeError(code)


def encode(value):return (json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode()


def document(raw):
    def unique(rows):
        out={}
        for key,value in rows:need(key not in out,'app32_inventory');out[key]=value
        return out
    need(type(raw)is bytes and 0<len(raw)<=MAX_JSON,'app32_inventory')
    return json.loads(raw,object_pairs_hook=unique,parse_constant=lambda _:need(False,'app32_inventory'))


def pin(raw):return {'bytes':len(raw),'sha256':sha256(raw).hexdigest()}


def descriptor(profile,spec,archive):
    return {'schema':'app32_exact_fixture_new_transport/1','profile':profile,
        'original_run_id':spec['run_id'],'original_image_artifact_id':spec['image_artifact_id'],
        'original_image_zip':{'bytes':spec['image_zip_bytes'],'sha256':spec['image_zip_sha256']},
        'original_evidence':{'artifact_id':EVIDENCE_IDS[profile],'bytes':spec['evidence_bytes'],'sha256':spec['evidence_sha256']},
        'image_config_id':spec['config_id'],'rootfs_layers':spec['layers'],'archive':archive,
        'original_artifact_recreated':False,'production_data_used':False,'security_accepted':False}


def inventory(raw,old,ctx):
    value=document(raw)
    need(type(value)is dict and set(value)=={'schema','release','fixtures'}
        and value['schema']=='app32_closed_public_dependencies/1','app32_inventory')
    release=value['release'];need(type(release)is dict and set(release)=={'id','tag','target'}
        and type(release['id'])is int and release['id']>0 and release['tag']==TAG and release['target']==TARGET,'app32_inventory')
    fixtures=value['fixtures'];need(type(fixtures)is dict and set(fixtures)==set(EVIDENCE_IDS),'app32_inventory')
    ids=set()
    for profile in ('postgres','redis'):
        spec=ctx.D.PROFILES[profile];row=fixtures[profile]
        need(type(row)is dict and set(row)=={'evidence','image','descriptor'},'app32_inventory')
        for kind in ('evidence','image'):
            asset=row[kind]
            need(type(asset)is dict and set(asset)=={'asset_id','name','bytes','sha256'}
                and type(asset['asset_id'])is int and asset['asset_id']>0 and asset['asset_id']not in ids
                and asset['name']==profile+'-'+kind+'.zip'and type(asset['bytes'])is int
                and 0<asset['bytes']<=MAX_ZIP and type(asset['sha256'])is str
                and len(asset['sha256'])==64 and all(c in '0123456789abcdef'for c in asset['sha256']),'app32_inventory')
            ids.add(asset['asset_id'])
        need({k:row['evidence'][k]for k in('bytes','sha256')}==
            {'bytes':spec['evidence_bytes'],'sha256':spec['evidence_sha256']},'app32_inventory')
        archive=row['descriptor'].get('archive')if type(row['descriptor'])is dict else None
        need(type(archive)is dict and set(archive)=={'format','name','size_bytes','sha256','uncompressed_bytes'}
            and archive['format']=='docker-archive+gzip'and archive['name']=='candidate-image.tar.gz'
            and type(archive['size_bytes'])is int and 0<archive['size_bytes']<=MAX_ZIP
            and type(archive['uncompressed_bytes'])is int and 0<archive['uncompressed_bytes']<=2*1024**3
            and type(archive['sha256'])is str and len(archive['sha256'])==64
            and all(c in'0123456789abcdef'for c in archive['sha256'])
            and row['descriptor']==descriptor(profile,spec,archive),'app32_inventory')
    return value


def admit(ctx,old,profile,folder,image_zip,evidence_zip,row):
    """Authenticate the fresh envelope and original image; no daemon calls."""
    spec=ctx.D.PROFILES[profile]
    data=ctx.D.evidence(ctx.C.read(evidence_zip,spec['evidence_bytes']),spec)
    wanted=row['descriptor'];archive=wanted['archive']
    ctx.capacity(archive['size_bytes']+archive['uncompressed_bytes']+MAX_JSON)
    folder.mkdir(mode=0o700)
    with ctx.C.pinned(image_zip,row['image']['bytes'])as(stream,size):
        need(size==row['image']['bytes']and ctx.C.stream_digest(stream,size)==row['image']['sha256'],'app32_transport_pin')
        stream.seek(0)
        with zipfile.ZipFile(stream)as incoming:
            items=incoming.infolist()
            need(len(items)==2 and {i.filename for i in items}=={'transport.json','candidate-image.tar.gz'}
                and all(not i.is_dir()and not i.flag_bits&1 and stat.S_IFMT(i.external_attr>>16)in(0,stat.S_IFREG)for i in items),'app32_transport_inventory')
            item=incoming.getinfo('transport.json');need(0<item.file_size<=MAX_JSON,'app32_transport_inventory')
            need(document(incoming.read(item))==wanted,'app32_transport_descriptor')
            item=incoming.getinfo('candidate-image.tar.gz');need(item.file_size==archive['size_bytes'],'app32_transport_pin')
            compressed=folder/'candidate-image.tar.gz'
            fd=os.open(compressed,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
            with os.fdopen(fd,'wb')as output,incoming.open(item)as source:
                total=0;digest=sha256()
                while block:=source.read(min(65536,item.file_size+1-total)):
                    total+=len(block);need(total<=item.file_size,'app32_transport_pin');ctx.check();output.write(block);digest.update(block)
                need(total==item.file_size and digest.hexdigest()==archive['sha256'],'app32_transport_pin')
                output.flush();os.fsync(output.fileno())
    tar=folder/'candidate-image.tar'
    with ctx.C.pinned(compressed,archive['size_bytes'])as(stream,size):ctx.P.decode_gzip(stream,size,tar,archive['uncompressed_bytes'])
    # Only transport length changes. All original image/Config/layer operands stay.
    transported={**data,'spec':{**spec,'archive':archive}}
    with ctx.C.pinned(tar,archive['uncompressed_bytes'])as(stream,size):
        proof=ctx.D.validate_archive(stream,size,ctx.end,transported,ctx.T,ctx.L)
    return tar,data,proof


def prepare(root,old,ctx,lock,*,command,environment,write,digest):
    """Resolve all four exact retained assets before frontend/image work."""
    folder=root/'app32-dependencies';folder.mkdir(mode=0o700)
    release=lock['release']
    endpoint='repos/'+REPO+'/releases/'+str(release['id'])
    metadata=document(command(['/usr/bin/gh','api','--method','GET','-H','Accept: application/vnd.github+json',endpoint],
        environment=environment,maximum=MAX_JSON,seconds=120))
    need(type(metadata)is dict and type(metadata.get('id'))is int and metadata.get('id')==release['id']and metadata.get('tag_name')==release['tag']
        and metadata.get('target_commitish')==release['target']and metadata.get('draft')is False and metadata.get('prerelease')is True,
        'app32_release_metadata')
    assets=metadata.get('assets');expected=[row[kind]for row in lock['fixtures'].values()for kind in('evidence','image')]
    need(type(assets)is list and len(assets)==4 and all(type(a)is dict for a in assets)
        and {a.get('id')for a in assets}=={a['asset_id']for a in expected},'app32_release_metadata')
    for wanted in expected:
        found=next(a for a in assets if a['id']==wanted['asset_id'])
        need(type(found.get('id'))is int and type(found.get('size'))is int
            and found.get('name')==wanted['name']and found.get('size')==wanted['bytes']and found.get('digest')=='sha256:'+wanted['sha256']
            and found.get('state')=='uploaded'and found.get('url')=='https://api.github.com/repos/'+REPO+'/releases/assets/'+str(wanted['asset_id']),
            'app32_release_metadata')
        path=folder/wanted['name'];fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        try:
            command(['/usr/bin/gh','api','--method','GET','-H','Accept: application/octet-stream',
                'repos/'+REPO+'/releases/assets/'+str(wanted['asset_id'])],environment=environment,
                target=fd,maximum=wanted['bytes'],seconds=300)
            os.fsync(fd)
        finally:os.close(fd)
        need(digest(path,MAX_ZIP)=={k:wanted[k]for k in('bytes','sha256')},'app32_transport_pin')
    proofs={}
    for profile,row in lock['fixtures'].items():
        temporary=folder/(profile+'-admission')
        tar,data,proof=admit(ctx,old,profile,temporary,folder/row['image']['name'],folder/row['evidence']['name'],row)
        proofs[profile]={'archive':proof,'config_id':data['spec']['config_id'],'same_original_image':True}
        for path in (tar,temporary/'candidate-image.tar.gz'):path.unlink()
        temporary.rmdir()
    # Retain the actual fetched metadata and explicit new transport contract.
    write(folder/'release.json',metadata);write(folder/'inventory.json',lock)
    write(root/'reports/app32-dependencies.json',{'schema':'app32_dependency_prepare/1','status':'admitted',
        'inventory':pin(encode(lock)),'release_id':release['id'],'profiles':proofs,
        'network_complete_before_build':True,'production_data_used':False,'security_accepted':False})


def bind(ctx,old,root,lock,stack):
    """One loaded driver instance gets local fixture transport; assertions stay."""
    original_admit=old.admit_dependency;original_command=ctx.command;original_download=ctx.download
    def offline_command(argv,**kwargs):
        need(not kwargs.get('auth')and list(argv[:len(old.DOCKER)])==old.DOCKER,'app32_offline_transport')
        return original_command(argv,**kwargs)
    def no_download(*args,**kwargs):need(False,'app32_offline_transport')
    def local_admit(actual,name):
        need(actual is ctx and name in('postgres','redis'),'app32_offline_transport')
        row=lock['fixtures'][name];folder=root/'app32-dependencies'
        tar,data,proof=admit(ctx,old,name,ctx.root/name,folder/row['image']['name'],folder/row['evidence']['name'],row)
        spec=ctx.D.PROFILES[name]
        with ctx.C.pinned(tar,row['descriptor']['archive']['uncompressed_bytes'])as(source,size):
            image,binding=old.load_exact(ctx,source,spec['config_id'],data['image']['Config'],spec['layers'],proof['source_identities'],profile=name)
        ctx.write(ctx.root/'reports'/(name+'-admission.json'),{'archive':proof,'daemon':binding,
            'scope':'disposable_public_ci_dependency','transport':'app32_exact_fixture_new_transport/1','security_accepted':False})
        return image
    old.admit_dependency=local_admit;ctx.command=offline_command;ctx.download=no_download
    stack.callback(setattr,old,'admit_dependency',original_admit)
    stack.callback(setattr,ctx,'command',original_command);stack.callback(setattr,ctx,'download',original_download)
