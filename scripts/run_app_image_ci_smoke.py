"""Fixed ed59 hosted-CI smoke only. Never a release or security acceptance gate.

No product overlay, production credentials, real provider or host execution is
accepted. Only three owned disposable containers and one internal network run.
"""
from contextlib import ExitStack
from hashlib import sha256
from pathlib import Path
from types import ModuleType
import argparse
import ast
import copy
import io
import json
import os
import re
import stat
import sys
import tarfile
import time
import zipfile

DOCKER = ['/usr/bin/docker', '--host=unix:///var/run/docker.sock']
LABEL = 'io.gold-trade.app-image-ci-smoke-owner'
MAX_JSON = 2*1024**2
HELPERS = {
    'compose_security_os_candidate.py': ('scripts/compose_security_os_candidate.py','10ccbe216fef04e5f7a7ce2ad31ac30eca7eeae090dfaaf8074fa3cc5c789d44'),
    'compose_app_mountpoint_image.py': ('scripts/compose_app_mountpoint_image.py','14849bd8c616b27ade1c8488b8e6f276b369480456d0acc1a8c8eb4284f40226'),
    'compose_market_security_os_candidate.py': ('scripts/compose_market_security_os_candidate.py','c53cfd7ae4570314ef7f13d43ecd73de26436cf56407676ac2fe24d19dc1caa9'),
    'admit_security_component_image_reuse.py': ('scripts/admit_security_component_image_reuse.py','9adddae3d6966443f33040f54a57ce597a1816af918e8c606557801d27bf4025'),
    'datastore_candidate_contract.py': ('deploy/maintenance/datastore_candidate_contract.py','d210020e24fedd4986803a3fe2618a5016ecd99f9e3ca9588af02d77a967e17d'),
    'runtime_image_archive_transport.py': ('deploy/maintenance/runtime_image_archive_transport.py','20c4b90f1c719d4cdee69df8ad1c31c2bcd663b99619b1d58dcbafa1eafecad2'),
}
SELECTORS = {
    'test_security_form_parser.py': ('FormParserSecurityTests',6,'de0090351837742ba6b0be747f0b586d4943590831215a4b4ea2619ebd5149fb'),
    'test_stage6_otp_delivery_redis.py': ('Stage6OTPDeliveryRedisTests',15,'b1e59ad7285a3c1f034aa269e024e091fe331cbdb446ac5e82f85de64530bd50'),
    'test_telegram_otp_ephemeral_queue_redis.py': ('TelegramOTPEphemeralQueueRedisTests',3,'cbe02df0618239e4affa1f8e51b2e69cdfe13f332a44d46e8b4626b21d80e22e'),
}
PHASE = 'hosted_source_admission'
SUBPHASE = 'none'
FAILURE_CODES = frozenset(['app_archive_identity', 'app_export_scope', 'candidate_preexisting', 'ci_capacity', 'ci_identity', 'ci_root', 'container_identity', 'container_name', 'deadline', 'dependency_evidence_identity', 'dependency_readiness', 'dependency_state_shape', 'dependency_startup_bound', 'diagnostic_subphase', 'duplicate_json_key', 'export_bound', 'export_pin', 'gzip_bound', 'gzip_pin', 'hosted_ci_only', 'image_import_delta', 'image_inventory', 'image_zip_inventory', 'image_zip_pin', 'installed_app_must_not_be_masked', 'json_bound', 'json_constant', 'loaded_descriptor', 'loaded_identity_unknown', 'loaded_image_identity', 'manifest_metadata', 'output_bound', 'owned_cleanup_identity', 'owned_cleanup_inventory', 'owned_network_cleanup_identity', 'owned_network_cleanup_inventory', 'private_output', 'redis_readiness', 'scratch_database_name', 'smoke_container_exit', 'smoke_counter_types', 'smoke_receipt', 'source_changed', 'source_metadata', 'source_path', 'source_pin', 'tar_size'])
EXCEPTION_CLASSES = frozenset(('RuntimeError','Refusal','AssertionError','OSError','FileNotFoundError',
    'PermissionError','TimeoutError','KeyError','TypeError','ValueError','JSONDecodeError'))
APP = {'activation_allowed': False,
 'added_provenance_labels': {'io.gold-trade.security.mountpoints.input-signature': '5351903a71b5bf6a281cf5e95a77ec8b57b2d69e804d519510c142473e544ce5',
                             'io.gold-trade.security.mountpoints.parent-config': 'sha256:8da3e404d01cc239606d1c79a30c4701c5a2ef2d103787b68b9ec221ae723672',
                             'io.gold-trade.security.mountpoints.source-commit': '9a4bec1e34f831a279388f62461a4d2f54770b49',
                             'io.gold-trade.security.mountpoints.status': 'packaging_candidate_not_accepted'},
 'app_root': [493, 0, 0],
 'export_bytes': 4386,
 'export_sha256': '9fe39cd695b822e8ccb8697b024d9279bb3608502b2cdb29320e679b77bdef28',
 'gzip': {'format': 'docker-archive+gzip',
          'name': 'candidate-image.tar.gz',
          'sha256': '6548855c65b6bc6fd04224b27e3c7ee037cdba3da98e72e216ea014f679dc4ac',
          'size_bytes': 184329652,
          'uncompressed_bytes': 451755520},
 'image_artifact_id': 12587747836,
 'image_config_id': 'sha256:ed59f14519b7329306f334e05d8666f808ac2c37da78afa922ddd21c60f3cedd',
 'image_zip_bytes': 183818338,
 'image_zip_sha256': 'e945f2c28e0bb6f8e3755eac0bcbbe21fed890843ef25486c58caa1e1b1e59d0',
 'manifest_id': 'sha256:e477ada2bb698f1dfaf7a5db9d40777ac135fdda768d6df9789e67aaf3c7e494',
 'raw_counts': {'CRITICAL': 0, 'HIGH': 44, 'LOW': 82, 'MEDIUM': 68, 'UNKNOWN': 1},
 'recipe_commit': '9a4bec1e34f831a279388f62461a4d2f54770b49',
 'rootfs_compact_sha256': 'f9b10901fa6b94f5b82d4a9514c15ac09b16088e87f0a8d5fbfab9f4e4e87991',
 'rootfs_layers': ['sha256:a70f6272827f64e1427d048ced77491aab5d64ab95725e56920802ff391197d7',
                   'sha256:535a5d38abecfac4fa49da33fa942b53c53c463bd0d39a860967e46eb03faafe',
                   'sha256:6972161a9a77e7c1afb997daac15abc54f5b70c7b40b814601f53e038d8c398c',
                   'sha256:d86e9bd004831a6069b9a02e5caac80b73526315f52719079b4017b099956ed2',
                   'sha256:fee22469b1324a206e81cfb2dfbc9228f07d41966d60142d98cd097ef101ac43',
                   'sha256:dda7d7be30aa48dc4e700d79bda245b5e6daa1f176c7229a8da4a790157af628',
                   'sha256:dddaae010f1e4eb050073c033f12155c0b16ab42d11d2c4613ccb3ef7c724af5',
                   'sha256:2a8547e3faf599ee73d5bb138871a95f3855582cb8751b9682db06929223e95e',
                   'sha256:2c22b9c72b0b2b1618098f37e4dc438bdcae17b9713555edf54aee7cc1a4dea4',
                   'sha256:f5e8ace0de4519a91cb0c2e20a026d88ff65385ec51dc8e04aacf8d17c6eaf55',
                   'sha256:f54a50fe1d380fbf3e0754d8abd0ef3c7594eeb53e8df7868649b2ddc72bf21e',
                   'sha256:96005344633fb01f5546a8c95188813003296f5c8c68fc90229850410deb6a06',
                   'sha256:914ff920d2f0fae36102c585f26569de069ffb4860e84fbf401114640feee414',
                   'sha256:07e61529e95d6c7e28835071b8c4c019293715b00a2610e45572532e8962df09',
                   'sha256:a030392f9adc6b84b9f7b683bbc2032405852362aa903ca16e905b02f8fb8825',
                   'sha256:bbbdcf9c40dde2b408738d65728d3ef37d3f395f400e2ac5e3f0b4d5708d9f21',
                   'sha256:74dc6ee1ccaf0c5c737c44c9f24c6b094c4927936bd3368d5cee99a5690dbc51',
                   'sha256:c7394abbb7e4fe75c75937976fd0eada8312603574e4e8df82ef569c7e5e286f',
                   'sha256:a5193bcbfd1aa624edaa308672e1c0e492d22c5e6d8d9cf014e651cbfa79353b',
                   'sha256:cb1ac21a50abe046e7e9e2415010fff99cf389d1a22aff9481855a87492c0cce',
                   'sha256:8f903aaac52bd32afbe17a9eb42919ee8d85365cb3aa0d629e54cc745f00c7ae',
                   'sha256:71498a1b8d410e5fb360cf857d20dad1fca39f2b7e4dcd63d344331fc2206836',
                   'sha256:889e24ce7bc9c7f1e663fefe3cad8b5b3ec4c90851050ec7b7d4133ae6d4ae56',
                   'sha256:28c50f1565f854f3b95699f71cc64483113e0eabe20fc21e269d980429090fc5',
                   'sha256:cb3afa755dd2f9d7d7c83f3e6bdf1eac8b84a756b1b96d7999d511aabedb11c2',
                   'sha256:1511d69876802e93d76598f2f6debf6a1e4a8193fbd207d48492f632abc81d2b',
                   'sha256:5f70bf18a086007016e948b04aed3b82103a36bea41755b6cddfaf10ace3c6ef',
                   'sha256:5f70bf18a086007016e948b04aed3b82103a36bea41755b6cddfaf10ace3c6ef',
                   'sha256:5f70bf18a086007016e948b04aed3b82103a36bea41755b6cddfaf10ace3c6ef',
                   'sha256:b50d5bcbe816a504406e77ccea3d5ab12fdac305726d5a41c00cac8b8f859dd3',
                   'sha256:c077ffe84ae5dbbd4b0ff5285d6a31c3756f3b7463bb111905895ca48620de0d',
                   'sha256:3f250faafaea277bec42a34f656b9b9b759d6b49660a23d644d719b3b04b3202',
                   'sha256:abc8554713c07989de04c078c41b34836bdb00f80887fdc3a19f959fbcb52aa5'],
 'run_id': 40165530395,
 'runtime_config_semantic_sha256': '55388e3944c0b00b3e671049229eac3722bca4d409d595488520f73c79ba34c2',
 'runtime_config_semantics': 'PRIOR.oci_runtime_config typed API-zero normalization then compact sorted '
                             'ASCII JSON; all remaining keys/types/values retained',
 'schema': 'app_ed59_transport_image_facts/1',
 'security_accepted': False,
 'tar_sha256': '6b7dc2de34b8feed051f6e4c24761afecee85dab81b80b4f557b485e7d7156fa'}


def need(ok, code):
    if not ok:raise RuntimeError(code)


def subphase(profile,stage):
    global SUBPHASE
    need(profile in ('app','postgres','redis') and stage in ('metadata','evidence_download','evidence_admission',
        'image_download','archive_unpack','archive_admission','image_load','daemon_config','readiness'),'diagnostic_subphase')
    SUBPHASE=profile+'_'+stage


def failure_diagnostic(error,ctx):
    allowed=set(FAILURE_CODES)
    for module in (() if ctx is None else getattr(ctx,'modules',{}).values()):
        allowed.update(getattr(module,'PUBLIC_FAILURE_CODES',()))
        allowed.update(getattr(module,'PUBLIC_CODES',()))
    label=type(error).__name__
    code=error.args[0] if isinstance(error,RuntimeError) and len(error.args)==1 and type(error.args[0]) is str and error.args[0] in allowed else 'unexpected_failure'
    return {'closed_code':code,'exception_class':label if label in EXCEPTION_CLASSES else 'unclassified_exception',
        'subphase':SUBPHASE}


def encode(value):
    return (json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode()


def document(raw):
    need(type(raw) is bytes and 0<len(raw)<=MAX_JSON,'json_bound')
    def unique(rows):
        out={}
        for key,value in rows:need(key not in out,'duplicate_json_key');out[key]=value
        return out
    return json.loads(raw,object_pairs_hook=unique,parse_constant=lambda _:need(False,'json_constant'))


def public(value):
    if type(value) is dict:
        need(not {'Config','Env','environment','runtime_config','resolved_environment','access_token','refresh_token','otp_code'}&set(value),'private_output')
        for child in value.values():public(child)
    elif type(value) is list:
        for child in value:public(child)


def identity(s):
    return (s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)


def sealed(stack,path,pin,maximum):
    need(path.is_absolute() and path.resolve(strict=True)==path,'source_path')
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK);stack.callback(os.close,fd)
    before=os.fstat(fd)
    need(stat.S_ISREG(before.st_mode) and before.st_uid==os.geteuid() and before.st_nlink==1
         and stat.S_IMODE(before.st_mode)==0o600 and 0<before.st_size<=maximum,'source_metadata')
    raw=os.pread(fd,before.st_size+1,0)
    need(len(raw)==before.st_size and sha256(raw).hexdigest()==pin,'source_pin')
    def check():need(identity(before)==identity(os.fstat(fd))==identity(path.lstat()),'source_changed')
    check();return raw,check


def ci(root):
    need(os.geteuid()==1001 and os.environ.get('GITHUB_ACTIONS')=='true'
         and os.environ.get('RUNNER_ENVIRONMENT')=='github-hosted'
         and os.environ.get('GITHUB_REPOSITORY')=='mohsenbarari/trading_bot','hosted_ci_only')
    parent=Path(os.environ['RUNNER_TEMP']).resolve(strict=True)
    need(root.is_absolute() and root.resolve(strict=True)==root and root.parent==parent
         and root.name.startswith('security-app-ci-smoke-') and root.stat().st_uid==1001
         and stat.S_IMODE(root.stat().st_mode)==0o700 and not parent.stat().st_mode&0o022,'ci_root')
    need(re.fullmatch('[a-f0-9]{40}',os.environ.get('GITHUB_SHA','')) is not None
         and re.fullmatch('[0-9]+-[0-9]+',owner()) is not None,'ci_identity')
    os.umask(0o077)


def owner():return os.environ.get('GITHUB_RUN_ID','')+'-'+os.environ.get('GITHUB_RUN_ATTEMPT','')


class Controller:
    def __init__(self,root,stack):
        ci(root);self.root,self.stack,self.checks=root,stack,[];self.created=[];self.network=None
        self.end=time.monotonic()+1800;self.cleanup_end=None
        self.modules={}
        for name,(_,pin) in HELPERS.items():
            raw,check=sealed(stack,root/'source'/name,pin,131072);self.checks.append(check)
            module=ModuleType('_hosted_smoke_'+name[:-3]);module.__file__=str(root/'source'/name)
            exec(compile(raw,module.__file__,'exec'),module.__dict__);self.modules[name]=module
        self.C=self.modules['compose_security_os_candidate.py'];self.P=self.modules['compose_app_mountpoint_image.py']
        self.M=self.modules['compose_market_security_os_candidate.py'];self.L=self.modules['admit_security_component_image_reuse.py']
        self.D=self.modules['datastore_candidate_contract.py'];self.T=self.modules['runtime_image_archive_transport.py']
        # Only these NEW pure module instances receive their immutable operands.
        self.P.PRIOR,self.P.LEGACY=self.M,self.L

    def check(self):
        need(time.monotonic()<(self.cleanup_end or self.end),'deadline')
        for check in self.checks:check()

    def command(self,argv,*,seconds=30,maximum=MAX_JSON,input_stream=None,auth=False,public_log=None,combine_stderr=False):
        self.check()
        return self.C.command(argv,seconds=min(seconds,max(.01,(self.cleanup_end or self.end)-time.monotonic())),maximum=maximum,input_stream=input_stream,
            environment={'GH_TOKEN':os.environ['GH_TOKEN']} if auth else {'DOCKER_CONFIG':str(self.root/'docker-config')},log_path=public_log,
            combine_stderr=combine_stderr)

    def docker(self,args,**kwargs):return self.command([*DOCKER,*args],**kwargs)

    def write(self,path,value):
        public(value);raw=encode(value);need(len(raw)<=MAX_JSON,'output_bound');self.C.write(path,raw)

    def download(self,identifier,path,size,pin):
        self.check();self.C.fetch_artifact(identifier,path,size,pin);self.check()

    def capacity(self,amount):
        info=os.statvfs(self.root)
        need(info.f_bavail*info.f_frsize>=8*1024**3+amount,'ci_capacity')


def unpack(ctx,spec,zip_path,folder):
    """One pinned fixed artifact: no extractall and bounded gzip expansion."""
    expected=spec['gzip'] if 'gzip' in spec else spec['archive']
    ctx.capacity(expected['size_bytes']+expected['uncompressed_bytes']+MAX_JSON)
    with ctx.C.pinned(zip_path,spec['image_zip_bytes']) as (stream,size):
        need(size==spec['image_zip_bytes'] and ctx.C.stream_digest(stream,size)==spec['image_zip_sha256'],'image_zip_pin')
        stream.seek(0)
        with zipfile.ZipFile(stream) as z:
            rows=z.infolist()
            need(len(rows)==2 and {i.filename for i in rows}=={'image-export.json','candidate-image.tar.gz'}
                 and all(not i.is_dir() and not i.flag_bits&1 and stat.S_IFMT(i.external_attr>>16) in (0,stat.S_IFREG) for i in rows),'image_zip_inventory')
            item=z.getinfo('image-export.json');need(item.file_size==spec['export_bytes']<=65536,'export_bound')
            raw=z.read(item);need(sha256(raw).hexdigest()==spec['export_sha256'],'export_pin')
            exported=document(raw)
            if 'gzip' in spec:
                need(exported['schema']=='app_mountpoint_image_export/1' and exported['image_id']==spec['image_config_id']
                     and exported['archive']==expected and exported['security_accepted'] is False
                     and exported['deployment_eligible'] is False and exported['runtime_guards_changed'] is False,'app_export_scope')
            else:ctx.D.export_record(exported,spec)
            item=z.getinfo('candidate-image.tar.gz');need(item.file_size==expected['size_bytes'],'gzip_bound')
            target=folder/'candidate-image.tar.gz';fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
            with os.fdopen(fd,'wb') as out,z.open(item) as source:
                total=0;digest=sha256()
                while block:=source.read(min(65536,item.file_size+1-total)):
                    total+=len(block);need(total<=item.file_size,'gzip_bound');ctx.check();out.write(block);digest.update(block)
                need(total==item.file_size and digest.hexdigest()==expected['sha256'],'gzip_pin');out.flush();os.fsync(out.fileno())
    tar=folder/'candidate-image.tar'
    with ctx.C.pinned(target,expected['size_bytes']) as (source,size):ctx.P.decode_gzip(source,size,tar,expected['uncompressed_bytes'])
    return tar


def inspect_image(ctx,image,expected_config,layers,*,config_id=None,identifiers=None):
    config_id=config_id or image
    identifiers=identifiers or {config_id:{'kind':'Docker_config_identity'}}
    need(image in identifiers,'loaded_identity_unknown')
    raw=ctx.docker(['image','inspect','--format','{"id":{{json .Id}},"os":{{json .Os}},"arch":{{json .Architecture}},"rootfs":{{json .RootFS}},"config":{{json .Config}}}',image])
    value=document(raw)
    need(value['id']==image and value['os']=='linux' and value['arch']=='amd64'
         and value['rootfs']=={'Type':'layers','Layers':layers}
         and ctx.M.same_json_value(ctx.M.oci_runtime_config(value['config']),ctx.M.oci_runtime_config(expected_config)),'loaded_image_identity')
    if image!=config_id:
        descriptor=document(ctx.docker(['image','inspect','--format','{{with index . "Descriptor"}}{{json .}}{{else}}null{{end}}',image]))
        expected=identifiers[image]
        need(type(descriptor) is dict and descriptor.get('digest')==image and descriptor.get('mediaType')==expected['media_type']
             and type(descriptor.get('size')) is int and descriptor['size']==expected['size'],'loaded_descriptor')
    return {'image_config_id':config_id,'daemon_image_id':image,'rootfs_layers':len(layers),'rootfs_sha256':sha256(json.dumps(layers,separators=(',',':')).encode()).hexdigest(),
        'whole_config_compared_in_ram':True,'configuration_values_disclosed':False,'security_accepted':False}


def load_exact(ctx,source,config_id,expected_config,layers,identifiers,*,profile='app'):
    def inventory():
        rows=ctx.docker(['image','ls','--all','--no-trunc','--quiet'],maximum=65536).decode().splitlines()
        need(len(rows)<=512 and all(re.fullmatch('sha256:[a-f0-9]{64}',row) for row in rows),'image_inventory')
        return set(rows)
    before=inventory();need(not before&set(identifiers),'candidate_preexisting')
    subphase(profile,'image_load')
    source.seek(0);ctx.docker(['image','load','--quiet'],input_stream=source,seconds=300)
    after=inventory();added=after-before
    need(before<=after and len(added)==1 and added<=set(identifiers),'image_import_delta')
    image=next(iter(added))
    subphase(profile,'daemon_config')
    return image,inspect_image(ctx,image,expected_config,layers,config_id=config_id,identifiers=identifiers)


def admit_app(ctx):
    folder=ctx.root/'app-image';folder.mkdir(mode=0o700);path=folder/'image.zip'
    subphase('app','image_download')
    ctx.download(APP['image_artifact_id'],path,APP['image_zip_bytes'],APP['image_zip_sha256'])
    subphase('app','archive_unpack')
    tar=unpack(ctx,APP,path,folder)
    subphase('app','archive_admission')
    with ctx.C.pinned(tar,APP['gzip']['uncompressed_bytes']) as (source,size):
        need(size==APP['gzip']['uncompressed_bytes'],'tar_size')
        proof=ctx.P.validate_archive(source,size,APP['image_config_id'],last_layer=APP['app_root'])
        config=proof.pop('config')
        need(proof['archive_sha256']==APP['tar_sha256'] and proof['layers']==33 and proof['physical_graph_closed'] is True
             and proof['oci_manifest_ids']==[APP['manifest_id']] and config['rootfs']=={'type':'layers','diff_ids':APP['rootfs_layers']}
             and sha256(encode(ctx.M.oci_runtime_config(config['config']))[:-1]).hexdigest()==APP['runtime_config_semantic_sha256']
             and all(config['config']['Labels'].get(k)==v for k,v in APP['added_provenance_labels'].items()),'app_archive_identity')
        source.seek(0)
        with tarfile.open(fileobj=source,mode='r:') as archive:
            member=archive.getmember('blobs/sha256/'+APP['manifest_id'][7:])
            need(member.isfile() and 0<member.size<=MAX_JSON,'manifest_metadata')
            identifiers={APP['image_config_id']:{'kind':'Docker_config_identity'},APP['manifest_id']:{
                'kind':'OCI_manifest_to_config','media_type':'application/vnd.oci.image.manifest.v1+json','size':member.size}}
        image,binding=load_exact(ctx,source,APP['image_config_id'],config['config'],APP['rootfs_layers'],identifiers)
    ctx.write(ctx.root/'reports/app-admission.json',{'archive':proof,'daemon':binding,'smoke_only':True,'security_accepted':False})
    return image


def admit_dependency(ctx,name):
    spec=ctx.D.PROFILES[name];folder=ctx.root/name;folder.mkdir(mode=0o700)
    subphase(name,'metadata')
    listing=document(ctx.command(['/usr/bin/gh','api',f"repos/mohsenbarari/trading_bot/actions/runs/{spec['run_id']}/artifacts"],auth=True,maximum=131072))
    rows=[r for r in listing['artifacts'] if r['size_in_bytes']==spec['evidence_bytes']
          and r.get('digest')=='sha256:'+spec['evidence_sha256'] and not r['expired']]
    need(len(rows)==1 and type(rows[0]['id']) is int,'dependency_evidence_identity')
    subphase(name,'evidence_download')
    evidence=folder/'evidence.zip';ctx.download(rows[0]['id'],evidence,spec['evidence_bytes'],spec['evidence_sha256'])
    subphase(name,'evidence_admission')
    data=ctx.D.evidence(ctx.C.read(evidence,spec['evidence_bytes']),spec)
    subphase(name,'image_download')
    image_zip=folder/'image.zip';ctx.download(spec['image_artifact_id'],image_zip,spec['image_zip_bytes'],spec['image_zip_sha256'])
    subphase(name,'archive_unpack')
    tar=unpack(ctx,spec,image_zip,folder)
    subphase(name,'archive_admission')
    with ctx.C.pinned(tar,spec['archive']['uncompressed_bytes']) as (source,size):
        proof=ctx.D.validate_archive(source,size,ctx.end,data,ctx.T,ctx.L)
        image,binding=load_exact(ctx,source,spec['config_id'],data['image']['Config'],spec['layers'],proof['source_identities'],profile=name)
    ctx.write(ctx.root/'reports'/(name+'-admission.json'),{'archive':proof,'daemon':binding,'scope':'disposable_public_ci_dependency','security_accepted':False})
    return image


def fixture_env(database):
    need(re.fullmatch('coin_intelligence_ci_[a-f0-9]{12}',database) is not None,'scratch_database_name')
    return {'DATABASE_URL':f'postgresql+asyncpg://ci_public:ci_public_fixture@pg:5432/{database}',
        'SYNC_DATABASE_URL':f'postgresql+psycopg2://ci_public:ci_public_fixture@pg:5432/{database}',
        'POSTGRES_DB':database,'POSTGRES_USER':'ci_public','POSTGRES_PASSWORD':'ci_public_fixture',
        'REDIS_URL':'redis://redis:6379/0','FRONTEND_URL':'http://127.0.0.1',
        'JWT_SECRET_KEY':'ci-public-synthetic-key-not-a-deployment-key','ENVIRONMENT':'production',
        'SERVER_MODE':'iran','TRADING_BOT_SERVICE':'api','BACKGROUND_JOBS_ENABLED':'false',
        'TELEGRAM_PROVIDER_TEST_AUTHORITY':'false','TELEGRAM_LOGIN_OTP_ENABLED':'false',
        'TELEGRAM_DELIVERY_EXECUTION_OWNER':'producer-only','TELEGRAM_DELIVERY_PRODUCER_MODE':'legacy',
        'TELEGRAM_DELIVERY_EXPECTED_EXECUTION_OWNER':'legacy','STAGING_LOG_OTP_CODES':'false',
        'SMSIR_API_KEY':'ci-public-fake-provider','SMSIR_LINE_NUMBER':'300000000000',
        'SMSIR_BASE_URL':'http://127.0.0.1:9080','SMSIR_OTP_TEMPLATE_ID':'123456',
        'SMSIR_OTP_TEMPLATE_PARAMETER':'CODE','TRADING_BOT_MIGRATION_MODE':'scratch',
        'TRADING_BOT_EXPECTED_CHECKOUT':'/app','STAGE6_TEST_REDIS_URL':'redis://redis:6379/1',
        'TELEGRAM_OTP_EQ_TEST_REDIS_URL':'redis://redis:6379/1','PYTHONDONTWRITEBYTECODE':'1'}


def prepare_proof(ctx,database):
    proof=ctx.root/'proof';proof.mkdir(mode=0o755)
    identity={}
    for name,(_,count,pin) in SELECTORS.items():
        raw,check=sealed(ctx.stack,ctx.root/'source'/name,pin,131072);ctx.checks.append(check)
        ctx.C.write(proof/name,raw,0o444);identity[name]={'sha256':pin,'tests':count}
    ctx.C.write(proof/'selectors.json',encode(identity),0o444)
    env=fixture_env(database)
    ctx.C.write(proof/'runtime.env',''.join(k+'='+v+'\n' for k,v in env.items()).encode(),0o444)
    # Settings comes from this public file; no inherited production env is used.
    return proof


def container_args(ctx,name,image,command,*,alias=None,env=None,proof=None,user=None,tmpfs=(),output_limit=False):
    need(re.fullmatch('app-ci-[a-z]+-[0-9]+-[0-9]+',name) is not None,'container_name')
    args=['create','--name',name,'--label',LABEL+'='+owner(),'--network',ctx.network,
          '--cap-drop=ALL','--security-opt=no-new-privileges','--memory=1g','--memory-swap=1g',
          '--cpus=2','--pids-limit=128','--ulimit','core=0']
    if output_limit:args+=['--ulimit','fsize=2097153:2097153']
    if alias:args+=['--network-alias',alias]
    if user:args+=['--user',user]
    for row in tmpfs:args+=['--tmpfs',row]
    if proof:args+=['--mount',f'type=bind,source={proof},target=/proof,readonly']
    for key,value in sorted((env or {}).items()):args+=['--env',key+'='+value]
    return [*args,image,*command]


def create(ctx,name,image,command,**kwargs):
    # Intent precedes RPC. Cleanup checks exact public name/label/image first.
    ctx.created.append((name,image))
    cid=ctx.docker(container_args(ctx,name,image,command,**kwargs),maximum=256).decode().strip()
    need(re.fullmatch('[a-f0-9]{64}',cid) is not None,'container_identity')
    return cid


def cleanup(ctx):
    ctx.cleanup_end=time.monotonic()+120
    failures=[]
    for name,image in reversed(ctx.created):
        try:
            ids=ctx.docker(['container','ls','--all','--no-trunc','--quiet','--filter','name=^/'+name+'$'],maximum=256).decode().splitlines()
            need(len(ids)<=1,'owned_cleanup_inventory')
            if not ids:continue
            row=document(ctx.docker(['container','inspect','--format','{"name":{{json .Name}},"image":{{json .Image}},"owner":{{json (index .Config.Labels "'+LABEL+'")}}}',ids[0]],maximum=1024))
            need(row=={'name':'/'+name,'image':image,'owner':owner()},'owned_cleanup_identity')
            ctx.docker(['container','rm','--force',ids[0]],maximum=256)
        except BaseException:failures.append('owned_container_cleanup_unproven')
    if ctx.network:
        try:
            names=ctx.docker(['network','ls','--format','{{.Name}}','--filter','name=^'+ctx.network+'$'],maximum=256).decode().splitlines()
            need(names in ([],[ctx.network]),'owned_network_cleanup_inventory')
            if not names:return sorted(set(failures))
            rows=document(ctx.docker(['network','inspect','--format','{"name":{{json .Name}},"owner":{{json (index .Labels "'+LABEL+'")}},"internal":{{json .Internal}},"containers":{{json .Containers}}}',ctx.network],maximum=131072))
            need(rows['name']==ctx.network and rows['owner']==owner() and rows['internal'] is True and not rows['containers'],'owned_network_cleanup_identity')
            ctx.docker(['network','rm',ctx.network],maximum=256)
        except BaseException:failures.append('owned_network_cleanup_unproven')
    return sorted(set(failures))


def startup_classification(profile,raw):
    """Classify a bounded private RAM buffer; never retain text or exception args."""
    need(profile in ('postgres','redis'),'diagnostic_subphase')
    need(type(raw) is bytes and len(raw)<=8192,'dependency_startup_bound')
    common=((b'No space left on device','filesystem_capacity_marker'),
            (b'Permission denied','filesystem_permission_marker'),
            (b'Read-only file system','filesystem_readonly_marker'))
    specific=((b'Database is uninitialized and superuser password is not specified','postgres_password_required_marker'),
              (b'database files are incompatible with server','postgres_data_version_marker'),
              (b'initdb: error:','postgres_initdb_error_marker'),
              (b'FATAL:','postgres_fatal_marker')) if profile=='postgres' else (
              (b'Fatal error, can\'t open config file','redis_config_marker'),
              (b'Failed listening on port','redis_listen_marker'),
              (b'FATAL CONFIG FILE ERROR','redis_config_marker'))
    return next((code for marker,code in common+specific if marker in raw),'unclassified_startup')


def readiness_failure(ctx,containers,profile,error):
    # Only fields from these newly owned public CI dependencies are inspected.
    # Their log text stays in bounded RAM and cannot reach an artifact or stdout.
    receipt={'schema':'app_ci_dependency_readiness/1','failed_service':profile,
        'probe_failure':failure_diagnostic(error,ctx),'services':{},
        'scope':'owned_disposable_hosted_dependencies_only','security_accepted':False}
    fmt='{"running":{{json .State.Running}},"status":{{json .State.Status}},"exit_code":{{json .State.ExitCode}},"oom_killed":{{json .State.OOMKilled}},"restarting":{{json .State.Restarting}},"dead":{{json .State.Dead}}}'
    for name,cid in containers.items():
        row={'state_observation':'unavailable','startup_classification':'log_observation_unavailable'}
        try:
            state=document(ctx.docker(['container','inspect','--format',fmt,cid],seconds=10,maximum=1024))
            need(type(state) is dict and set(state)=={'running','status','exit_code','oom_killed','restarting','dead'}
                and all(type(state[k]) is bool for k in ('running','oom_killed','restarting','dead'))
                and state['status'] in ('created','running','paused','restarting','removing','exited','dead')
                and type(state['exit_code']) is int and 0<=state['exit_code']<=255,'dependency_state_shape')
            row.update(state_observation='observed',state=state)
        except BaseException:pass
        try:
            raw=ctx.docker(['logs','--tail','40',cid],seconds=10,maximum=8192,combine_stderr=True)
            row['startup_classification']=startup_classification(name,raw)
        except BaseException:pass
        receipt['services'][name]=row
    # Observational failure must never replace the primary failed readiness probe.
    try:ctx.write(ctx.root/'reports/dependency-readiness.json',receipt)
    except BaseException:pass


def execute(ctx):
    global PHASE,SUBPHASE
    PHASE='exact_public_image_admission';app=admit_app(ctx)
    postgres=admit_dependency(ctx,'postgres');redis=admit_dependency(ctx,'redis')
    SUBPHASE='none'
    database='coin_intelligence_ci_'+sha256(owner().encode()).hexdigest()[:12]
    proof=prepare_proof(ctx,database);ctx.network='app-ci-net-'+owner()
    PHASE='disposable_internal_network'
    ctx.docker(['network','create','--internal','--label',LABEL+'='+owner(),ctx.network],maximum=256)
    pg=create(ctx,'app-ci-pg-'+owner(),postgres,['postgres','-c','unix_socket_directories=/tmp','-c','shared_buffers=64MB','-c','max_connections=20','-c','max_wal_size=128MB'],alias='pg',user='postgres',
        env={'POSTGRES_USER':'ci_public','POSTGRES_PASSWORD':'ci_public_fixture','POSTGRES_DB':database,'PGDATA':'/var/lib/postgresql/data/pgdata'},
        tmpfs=('/var/lib/postgresql/data:rw,nosuid,nodev,size=1g,mode=1777','/tmp:rw,nosuid,nodev,size=64m,mode=1777'))
    rd=create(ctx,'app-ci-redis-'+owner(),redis,['redis-server','--save','','--appendonly','no'],alias='redis',user='1000:1000',
        tmpfs=('/data:rw,nosuid,nodev,size=128m,uid=1000,gid=1000','/tmp:rw,nosuid,nodev,size=16m,mode=1777'))
    ctx.docker(['start',pg,rd],maximum=512)
    PHASE='disposable_dependency_readiness'
    deadline=time.monotonic()+90
    while True:
        try:
            subphase('postgres','readiness');profile='postgres'
            ctx.docker(['exec',pg,'pg_isready','-h','127.0.0.1','-U','ci_public','-d',database],maximum=1024)
            subphase('redis','readiness');profile='redis'
            need(ctx.docker(['exec',rd,'redis-cli','ping'],maximum=64).strip()==b'PONG','redis_readiness');break
        except RuntimeError as error:
            if time.monotonic()>=deadline:
                readiness_failure(ctx,{'postgres':pg,'redis':rd},profile,error)
                need(False,'dependency_readiness')
            time.sleep(1)
    SUBPHASE='none'
    PHASE='installed_application_smoke'
    app_cid=create(ctx,'app-ci-api-'+owner(),app,['/usr/local/bin/python3','-I','-B','-c',CAPSULE],
        env={'APP_ENV_FILE':'/proof/runtime.env',**fixture_env(database)},proof=proof,output_limit=True,
        tmpfs=('/tmp:rw,nosuid,nodev,noexec,size=128m,mode=1777',))
    mounts=document(ctx.docker(['container','inspect','--format','{{json .Mounts}}',app_cid],maximum=8192))
    need(len(mounts)==2 and all(row['Destination'] in {'/proof','/tmp'} for row in mounts)
         and not any(row['Destination']=='/app' or row['Destination'].startswith('/app/') for row in mounts),'installed_app_must_not_be_masked')
    client_ok=True
    try:ctx.docker(['start','--attach',app_cid],seconds=600,maximum=65536,public_log=ctx.root/'smoke-output.json')
    except RuntimeError:client_ok=False
    raw=ctx.C.read(ctx.root/'smoke-output.json',65536)
    result=document(raw)
    ctx.write(ctx.root/'reports/actual-smoke.json',result)
    need(client_ok and ctx.docker(['inspect','--format','{{.State.ExitCode}}',app_cid],maximum=64).strip()==b'0','smoke_container_exit')
    validate_result(result)
    return result


def validate_result(value):
    public(value)
    need(type(value) is dict and type(value.get('runtime')) is dict and type(value.get('components')) is dict
         and all(type(value['runtime'].get(key)) is int for key in ('expected','run','passed'))
         and all(type(value['components'].get(key)) is int for key in ('expected','run','passed','errors','skipped')),'smoke_counter_types')
    need(value.get('schema')=='app_image_ci_smoke/1' and value.get('status')=='passed' and value.get('image_config_id')==APP['image_config_id']
         and value.get('runtime')=={'expected':8,'run':8,'passed':8,'failures':[]}
         and value.get('components')=={'expected':24,'run':24,'passed':24,'failures':[],'errors':0,'skipped':0}
         and value.get('production_config_guards_active') is True and value.get('app_source_masked') is False
         and value.get('real_provider_used') is False and value.get('security_accepted') is False
         and value.get('full_matrix_accepted') is False,'smoke_receipt')
    return value


def main(argv=None):
    global PHASE,SUBPHASE
    PHASE,SUBPHASE='hosted_source_admission','none'
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--root',type=Path,required=True)
    args=parser.parse_args(argv);ctx=None;primary=False;errors=[]
    try:
        with ExitStack() as stack:
            ctx=Controller(args.root,stack)
            try:result=execute(ctx)
            except BaseException:primary=True;raise
            finally:
                errors=cleanup(ctx)
                ctx.write(ctx.root/'reports/cleanup.json',{'owned_cleanup_complete':not errors,'closed_causes':errors,
                    'primary_failure_retained':primary,'security_accepted':False})
                if errors and not primary:raise RuntimeError('owned_cleanup_unproven')
            ctx.check();print(json.dumps({'status':'fixed_image_smoke_passed','tests':32,'security_accepted':False,'full_matrix_accepted':False},sort_keys=True))
        return 0
    except BaseException as error:
        diagnostic=failure_diagnostic(error,ctx)
        if ctx is not None:
            try:ctx.write(ctx.root/'reports/failure.json',{'status':'failed','phase':PHASE,'cleanup_causes':errors,**diagnostic,
                'security_accepted':False,'full_matrix_accepted':False})
            except BaseException:pass
        print(json.dumps({'status':'fixed_image_smoke_failed','phase':PHASE,**diagnostic,'security_accepted':False},sort_keys=True));return 1


CAPSULE = r'''
import asyncio,hashlib,http.server,io,json,os,pathlib,resource,subprocess,sys,threading,types,unittest
from contextlib import ExitStack
PUBLIC=os.dup(1);PRIVATE=os.memfd_create('synthetic-smoke-private-output')
os.dup2(PRIVATE,1);os.dup2(PRIVATE,2)
resource.setrlimit(resource.RLIMIT_FSIZE,(2097153,2097153))
resource.setrlimit(resource.RLIMIT_CORE,(0,0))
sys.path.insert(0,'/app')
RESULT={'schema':'app_image_ci_smoke/1','image_config_id':'sha256:ed59f14519b7329306f334e05d8666f808ac2c37da78afa922ddd21c60f3cedd',
    'runtime':{'expected':8,'run':0,'passed':0,'failures':[]},
    'components':{'expected':24,'run':0,'passed':0,'failures':[],'errors':0,'skipped':0},
    'production_config_guards_active':False,'app_source_masked':False,'real_provider_used':False,
    'security_accepted':False,'full_matrix_accepted':False}
RESULT['runtime_scope']='installed_API_lifespan_auth_OTP_upload_album_A_D'
RESULT['component_scope']='6_parser_and_18_Redis_regressions_with_existing_component_test_doubles'
RESULT['not_tested']=['browser','Bot_delivery','sync','backup_restore','production_network_TLS','background_scheduler_continuity']
CURRENT='bootstrap'
CLOSED_CODES = frozenset(['actual_migration_head', 'actual_production_fixture_config', 'actual_redis_expiry', 'actual_scratch_migration_failed', 'actual_sms_delivery', 'album_caption_policy', 'album_committed_twice', 'album_idempotent_message_ids', 'album_order', 'album_rows_persisted_without_duplicates', 'authenticated_identity', 'cancel_fixture_exists', 'cancel_removes_temporary_file', 'cancelled_session_state', 'decoded_image_pixels', 'fake_sms_exact_count', 'fake_sms_fixture_scope', 'fake_sms_parameter', 'fake_sms_reaped', 'fake_sms_request_bound', 'fake_sms_shape', 'http_delete_200', 'http_delete_201', 'http_delete_400', 'http_delete_401', 'http_delete_403', 'http_delete_404', 'http_delete_409', 'http_get_200', 'http_get_201', 'http_get_400', 'http_get_401', 'http_get_403', 'http_get_404', 'http_get_409', 'http_patch_200', 'http_patch_201', 'http_patch_400', 'http_patch_401', 'http_patch_403', 'http_patch_404', 'http_patch_409', 'http_post_200', 'http_post_201', 'http_post_400', 'http_post_401', 'http_post_403', 'http_post_404', 'http_post_409', 'installed_module_origin', 'invalid_otp_never_creates_session', 'multipart_real_image_processing', 'no_dependency_overrides', 'otp_expiry_fixture_exists', 'public_receipt_bound', 'real_session_tokens', 'selected_test_count', 'selected_test_inventory', 'selected_test_pin', 'selected_tests_failed', 'sid_required', 'sms_recipient', 'upload_ready'])
def need(ok,code):
    if not ok:raise RuntimeError(code)
def reply(value):
    raw=(json.dumps(value,sort_keys=True,separators=(',',':'))+'\n').encode()
    need(len(raw)<=65536,'public_receipt_bound');os.write(PUBLIC,raw)
def check_origins():
    for name,module in list(sys.modules.items()):
        if name=='main' or name.split('.')[0] in {'api','core','models','bot','src'}:
            path=getattr(module,'__file__',None)
            if path:need(pathlib.Path(path).resolve().is_relative_to('/app'),'installed_module_origin')

class FakeSMS(http.server.BaseHTTPRequestHandler):
    records=[]
    def log_message(self,*a):pass
    def do_POST(self):
        size=int(self.headers.get('Content-Length','0'))
        need(self.path=='/v1/send/verify' and 0<size<=4096,'fake_sms_request_bound')
        data=json.loads(self.rfile.read(size))
        need(self.headers.get('X-API-KEY')=='ci-public-fake-provider' and set(data)=={'mobile','templateId','parameters'},'fake_sms_shape')
        need(len(data['parameters'])==1 and data['parameters'][0]['name']=='CODE','fake_sms_parameter')
        code=str(data['parameters'][0]['value']);mobile=str(data['mobile'])
        need(mobile.startswith('09') and len(mobile)==11 and code.isdigit() and len(code)==5,'fake_sms_fixture_scope')
        self.records.append((mobile,code))
        raw=json.dumps({'status':1,'data':{'messageId':len(self.records)}}).encode()
        self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)

async def runtime():
    global CURRENT
    import httpx
    from sqlalchemy import select,func
    from PIL import Image
    from core.config import settings
    from core.db import AsyncSessionLocal,engine
    from core.redis import get_redis_client
    from core.enums import UserAccountStatus,ChatType,ChatMemberRole,ChatMembershipStatus
    from models.user import User,UserRole
    from models.chat import Chat
    from models.chat_member import ChatMember
    from models.session import UserSession
    from models.message import Message
    from models.upload_session import UploadSession
    from core.jwt_codec import jwt
    import main
    need(settings.environment=='production' and settings.trading_bot_service=='api' and settings.server_mode=='iran'
         and settings.background_jobs_enabled is False and settings.telegram_provider_test_authority is False
         and settings.staging_log_otp_codes is False and settings.telegram_login_otp_enabled is False
         and settings.smsir_base_url=='http://127.0.0.1:9080','actual_production_fixture_config')
    need(not main.app.dependency_overrides,'no_dependency_overrides')
    RESULT['production_config_guards_active']=True
    server=http.server.ThreadingHTTPServer(('127.0.0.1',9080),FakeSMS)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    users={};tokens={};group=None
    async def count_sessions(user):
        async with AsyncSessionLocal() as db:return int((await db.execute(select(func.count()).select_from(UserSession).where(UserSession.user_id==user.id))).scalar_one())
    async def request(client,method,path,expected=200,who=None,**kwargs):
        headers=dict(kwargs.pop('headers',{}))
        if who:headers['Authorization']='Bearer '+tokens[who]['access_token']
        response=await client.request(method,'/api'+path,headers=headers,**kwargs)
        need(response.status_code==expected,'http_'+method.lower()+'_'+str(expected))
        return response
    async def login(client,label):
        user=users[label];start=len(FakeSMS.records)
        response=await request(client,'POST','/auth/request-otp',json={'mobile_number':user.mobile_number})
        need(response.json().get('method')=='sms' and len(FakeSMS.records)==start+1,'actual_sms_delivery')
        mobile,code=FakeSMS.records[-1];need(mobile==user.mobile_number,'sms_recipient')
        response=await request(client,'POST','/auth/verify-otp',json={'mobile_number':mobile,'code':code})
        value=response.json();need(set(value)=={'access_token','refresh_token','token_type'} and value['token_type']=='bearer','real_session_tokens')
        tokens[label]=value;return value
    async def session(client,label='alice',batch=None,kind='direct',target=None,index=0):
        body={'batch_id':batch,'room_kind':kind,'target_id':target or users['bob'].id,'media_type':'image',
            'file_name':'ordinary.png','mime_type':'image/png','total_bytes':len(png),'chunk_size':len(png),
            'preview_metadata':{'album_index':index,'caption':'ordinary caption'}}
        return (await request(client,'POST','/chat/upload-sessions',expected=201,who=label,json=body)).json()
    async def append(client,value,offset=0,resume=None,expected=200):
        return await request(client,'PATCH','/chat/upload-sessions/'+value['session_id']+'/chunk',expected=expected,who='alice',
            data={'resume_token':resume or value['resume_token'],'offset':str(offset),'is_last_chunk':'true'},files={'chunk':('ordinary.png',png,'image/png')})
    async def finalize(client,value):
        await append(client,value)
        response=await request(client,'POST','/chat/upload-sessions/'+value['session_id']+'/finalize',who='alice')
        need(response.json()['status']=='ready','upload_ready');return response.json()['final_chat_file_id']
    async def read_file(client,file_id,label,expected=200):
        response=await request(client,'GET','/chat/files/'+file_id,expected=expected,params={'token':tokens[label]['access_token']})
        if expected==200:
            with Image.open(io.BytesIO(response.content)) as image:need(image.size==(2,2) and image.convert('RGB').getpixel((0,0))==(20,40,60),'decoded_image_pixels')
        return response
    image=Image.new('RGB',(2,2),(20,40,60));buffer=io.BytesIO();image.save(buffer,format='PNG');image.close();png=buffer.getvalue()
    try:
        async with main.app.router.lifespan_context(main.app):
            async with AsyncSessionLocal() as db:
                for index,label in enumerate(('alice','bob','outsider','login')):
                    user=User(account_name='ci_'+label,mobile_number='0912000000'+str(index),full_name='CI '+label,address='Public CI fixture',
                        role=UserRole.STANDARD,account_status=UserAccountStatus.ACTIVE,is_deleted=False,must_change_password=False,
                        home_server='iran',max_sessions=1,sync_version=1,counter_epoch=1,telegram_id=None,
                        bot_onboarding_required_step=0,bot_onboarding_completed_step=0)
                    db.add(user);users[label]=user
                await db.flush()
                room=Chat(type=ChatType.GROUP,title='Public CI group',created_by_id=users['alice'].id,is_deleted=False,is_system=False,is_mandatory=False)
                db.add(room);await db.flush();group=room.id
                for label,role in [('alice',ChatMemberRole.ADMIN),('bob',ChatMemberRole.MEMBER)]:
                    db.add(ChatMember(chat_id=group,user_id=users[label].id,role=role,membership_status=ChatMembershipStatus.ACTIVE))
                await db.commit()
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app),base_url='http://127.0.0.1',timeout=15) as client:
                async def startup():
                    await request(client,'GET','/auth/me',expected=401)
                    await request(client,'POST','/auth/dev-login',expected=404)
                    need(not main.app.dependency_overrides,'no_dependency_overrides')
                    async with AsyncSessionLocal() as db:
                        version=(await db.execute(__import__('sqlalchemy').text('SELECT version_num FROM alembic_version'))).scalar_one()
                        from alembic.config import Config
                        from alembic.script import ScriptDirectory
                        need(type(version) is str and ScriptDirectory.from_config(Config('/app/alembic.ini')).get_heads()==[version],'actual_migration_head')
                        RESULT['migration_head']=version
                async def otp_negative():
                    user=users['login'];before=await count_sessions(user)
                    await request(client,'POST','/auth/request-otp',json={'mobile_number':user.mobile_number})
                    code=FakeSMS.records[-1][1];wrong='00000' if code!='00000' else '00001'
                    await request(client,'POST','/auth/verify-otp',expected=400,json={'mobile_number':user.mobile_number,'code':wrong})
                    redis=get_redis_client();need(await redis.pexpire('otp:'+user.mobile_number,1),'otp_expiry_fixture_exists')
                    await asyncio.sleep(.02);need(await redis.get('otp:'+user.mobile_number) is None,'actual_redis_expiry')
                    await request(client,'POST','/auth/verify-otp',expected=400,json={'mobile_number':user.mobile_number,'code':code})
                    need(await count_sessions(user)==before,'invalid_otp_never_creates_session')
                    await redis.delete('otp_limit:'+user.mobile_number)
                async def auth_lifecycle():
                    value=await login(client,'login')
                    me=(await request(client,'GET','/auth/me',who='login')).json();need(me['id']==users['login'].id,'authenticated_identity')
                    value=(await request(client,'POST','/auth/refresh',json={'refresh_token':value['refresh_token']})).json()
                    tokens['login']=value
                    claims=jwt.decode(value['access_token'],settings.jwt_secret_key,algorithms=[settings.jwt_algorithm]);need(claims.get('sid'),'sid_required')
                    await request(client,'DELETE','/sessions/'+claims['sid'],who='login')
                    await request(client,'GET','/auth/me',expected=401,who='login')
                    await request(client,'POST','/auth/refresh',expected=401,json={'refresh_token':value['refresh_token']})
                    for label in ('alice','bob','outsider'):await login(client,label)
                async def multipart():
                    value=(await request(client,'POST','/chat/upload-media',who='alice',files={'file':('ordinary.png',png,'image/png')})).json()
                    need(value['mime_type']=='image/png' and (value['width'],value['height'])==(2,2),'multipart_real_image_processing')
                    await read_file(client,value['file_id'],'alice');await read_file(client,value['file_id'],'outsider',expected=403)
                    await request(client,'GET','/chat/files/'+value['file_id'],expected=401,who='alice')
                async def resumable():
                    value=await session(client)
                    await append(client,value,offset=1,expected=409);await append(client,value,resume='ordinary-wrong-token',expected=403)
                    file_id=await finalize(client,value);await read_file(client,file_id,'alice');await read_file(client,file_id,'outsider',expected=403)
                async def album(kind):
                    target=users['bob'].id if kind=='direct' else group
                    batch=(await request(client,'POST','/chat/upload-batches',expected=201,who='alice',json={
                        'room_kind':kind,'target_id':target,'message_kind':'album','expected_items':2,
                        'caption_policy':'first_item_only','idempotency_key':'ci-album-'+kind})).json()['batch_id']
                    files=[]
                    for index in (1,0):
                        value=await session(client,batch=batch,kind=kind,target=target,index=index)
                        files.append((index,await finalize(client,value)))
                    async with AsyncSessionLocal() as db:before_count=int((await db.execute(select(func.count()).select_from(Message))).scalar_one())
                    result=(await request(client,'POST','/chat/upload-batches/'+batch+'/commit',who='alice')).json()
                    need(result['status']=='committed' and result['committed_items']==2 and len(result['messages'])==2,'album_committed_twice')
                    ids=[row['id'] for row in result['messages']]
                    contents=[json.loads(row['content']) for row in result['messages']]
                    need([row['album_index'] for row in contents]==[0,1] and [row['file_id'] for row in contents]==[f for _,f in sorted(files)],'album_order')
                    need(contents[0].get('caption')=='ordinary caption' and not contents[1].get('caption'),'album_caption_policy')
                    repeated=(await request(client,'POST','/chat/upload-batches/'+batch+'/commit',who='alice')).json()
                    need([row['id'] for row in repeated['messages']]==ids,'album_idempotent_message_ids')
                    async with AsyncSessionLocal() as db:
                        count=int((await db.execute(select(func.count()).select_from(Message).where(Message.id.in_(ids)))).scalar_one())
                        total=int((await db.execute(select(func.count()).select_from(Message))).scalar_one())
                        need(count==2 and total==before_count+2,'album_rows_persisted_without_duplicates')
                    for _,file_id in files:
                        await read_file(client,file_id,'bob');await read_file(client,file_id,'outsider',expected=403)
                async def cancel():
                    batch=(await request(client,'POST','/chat/upload-batches',expected=201,who='alice',json={
                        'room_kind':'direct','target_id':users['bob'].id,'message_kind':'single','expected_items':1,
                        'caption_policy':'none','idempotency_key':'ci-cancel-single'})).json()['batch_id']
                    value=await session(client,batch=batch);await append(client,value)
                    async with AsyncSessionLocal() as db:
                        row=await db.get(UploadSession,value['session_id']);path=pathlib.Path(row.temp_storage_path);need(path.is_file(),'cancel_fixture_exists')
                    await request(client,'POST','/chat/upload-batches/'+batch+'/cancel',who='alice')
                    need(not path.exists(),'cancel_removes_temporary_file')
                    state=(await request(client,'GET','/chat/upload-sessions/'+value['session_id'],who='alice')).json()
                    need(state['status']=='cancelled','cancelled_session_state')
                cases=[('A_startup_auth_guards',startup),('B_invalid_and_expired_otp',otp_negative),('B_auth_session_lifecycle',auth_lifecycle),
                       ('C_multipart_image_access',multipart),('C_resumable_guards',resumable),('D_direct_album_idempotency',lambda:album('direct')),
                       ('D_group_album_idempotency',lambda:album('group')),('D_cancel_cleanup',cancel)]
                for name,case in cases:
                    CURRENT=name;RESULT['runtime']['run']+=1;await case();RESULT['runtime']['passed']+=1
                need(len(FakeSMS.records)==5,'fake_sms_exact_count')
                check_origins()
    finally:
        server.shutdown();server.server_close();thread.join(timeout=5);need(not thread.is_alive(),'fake_sms_reaped')
        await engine.dispose()

def components():
    global CURRENT
    CURRENT='components'
    manifest=json.loads(pathlib.Path('/proof/selectors.json').read_bytes());suite=unittest.TestSuite()
    names={'test_security_form_parser.py':('FormParserSecurityTests',6),
        'test_stage6_otp_delivery_redis.py':('Stage6OTPDeliveryRedisTests',15),
        'test_telegram_otp_ephemeral_queue_redis.py':('TelegramOTPEphemeralQueueRedisTests',3)}
    need(set(manifest)==set(names),'selected_test_inventory')
    for name,(class_name,count) in names.items():
        raw=pathlib.Path('/proof',name).read_bytes();need(len(raw)<=131072 and hashlib.sha256(raw).hexdigest()==manifest[name]['sha256'],'selected_test_pin')
        module=types.ModuleType('_ci_'+name[:-3]);module.__file__='/proof/'+name;sys.modules[module.__name__]=module
        exec(compile(raw,module.__file__,'exec'),module.__dict__)
        selected=unittest.defaultTestLoader.loadTestsFromTestCase(getattr(module,class_name));need(selected.countTestCases()==count,'selected_test_count');suite.addTests(selected)
    with os.fdopen(os.dup(PRIVATE),'w',encoding='utf-8') as private_log:
        result=unittest.TextTestRunner(verbosity=0,stream=private_log).run(suite)
    RESULT['components']={'expected':24,'run':result.testsRun,'passed':result.testsRun-len(result.failures)-len(result.errors)-len(result.skipped),
        'failures':[case.id() for case,_ in result.failures+result.errors],'errors':len(result.errors),'skipped':len(result.skipped)}
    need(result.wasSuccessful() and not result.skipped and result.testsRun==24,'selected_tests_failed')
    check_origins()

try:
    CURRENT='installed_guarded_scratch_migration'
    result=subprocess.run(['/usr/local/bin/python3','-I','-B','/app/scripts/run_guarded_scratch_alembic.py','upgrade','head'],
        cwd='/app',stdin=subprocess.DEVNULL,stdout=PRIVATE,stderr=PRIVATE,timeout=180,check=False)
    need(result.returncode==0,'actual_scratch_migration_failed')
    asyncio.run(runtime());components();RESULT['status']='passed';reply(RESULT);code=0
except BaseException as error:
    if CURRENT!='components':RESULT['runtime']['failures'].append(CURRENT)
    RESULT['status']='failed';RESULT['first_failure_scope']=CURRENT
    RESULT['closed_failure_code']=error.args[0] if type(error) is RuntimeError and len(error.args)==1 and type(error.args[0]) is str and error.args[0] in CLOSED_CODES else 'unexpected_smoke_failure'
    reply(RESULT);code=1
finally:
    os.close(PRIVATE);os.close(PUBLIC)
raise SystemExit(code)
'''

if __name__=='__main__':raise SystemExit(main())
