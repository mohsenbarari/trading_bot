"""Read public E46 source/cache metadata only; never load cached code.

The root caller binds the existing container's image/User/mounts before and
after this bounded stdlib-only worker. The original 198-file control is never
modified. A valid or unchecked cache remains unknown and blocks admission.
"""
import base64
from hashlib import sha256
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import resource
import signal
import stat
import struct
import sys
import time
import _imp

CONTROL_SHA256 = '2e7dda021162d9800eef7a3705fb479a9fc9d8433c4f3ed4a65429fc7364d4a3'
IMAGE = 'sha256:e46aedbe135117301fb19183ff39587174162ca4a454ac60c7fccb2b302033cd'
SOURCE_UID = SOURCE_GID = 10001
CACHE_COUNT = 105
FIXED = ('core/__init__.py', 'config/market_data_sources.v1.json')
TREES = ('core/market_intelligence', 'contracts/market_data', 'deploy/market-data/migrations')
SECRET_NAMES = {'.env','credentials','credentials.json','secrets','secrets.json','session',
                'id_rsa','id_ed25519','authorized_keys','known_hosts'}
SECRET_SUFFIXES = ('.env','.key','.pem','.p12','.pfx','.session','.session-journal')
KNOWN_UNUSED = {'nonselected_cache_tag','source_absent','rejected_header','timestamp_stale','checked_hash_stale'}


class CacheError(RuntimeError):
    pass


def require(ok, code):
    if not ok: raise CacheError(code)


def encoded(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode()


def identity(info):
    return (info.st_dev,info.st_ino,info.st_mode,info.st_uid,info.st_gid,info.st_nlink,
            info.st_size,info.st_mtime_ns,info.st_ctime_ns)


def public(name):
    require(name not in ('','.','..') and '/' not in name and not any(ord(c)<32 for c in name)
            and name not in SECRET_NAMES and not name.startswith('.env.')
            and not name.endswith(SECRET_SUFFIXES),'public_path')


def cache_source(path):
    value=PurePosixPath(path)
    require(not value.is_absolute() and str(value)==path and '..' not in value.parts
            and path.startswith('core/market_intelligence/') and value.parent.name=='__pycache__','cache_path')
    match=re.fullmatch(r'([A-Za-z_][A-Za-z0-9_]*)\.(cpython-(?:311|312))(?:\.opt-[12])?\.pyc',value.name)
    require(match is not None,'cache_path')
    return str(value.parent.parent/(match[1]+'.py')),match[2]


def classify_header(path, header, source, *, cache_tag, magic, hash_policy='default'):
    """Apply only CPython source-cache selection/header rejection rules."""
    source_path,tag=cache_source(path)
    if tag!=cache_tag: return 'nonselected_cache_tag'
    if source is None: return 'source_absent'
    if len(header)<16 or header[:4]!=magic: return 'rejected_header'
    flags=struct.unpack('<I',header[4:8])[0]
    if flags&~3:return 'rejected_header'
    if not flags&1:
        mtime,size=struct.unpack('<II',header[8:16])
        if mtime!=(source['mtime_seconds']&0xffffffff) or size!=(source['bytes']&0xffffffff):return 'timestamp_stale'
    elif flags&2 and hash_policy in ('default','always') and header[8:16].hex()!=source['source_hash']:
        return 'checked_hash_stale'
    return 'unknown_cache_blocks_candidate'


def scan(app_root, end):
    rows,aux,counter=[],{},[0,0]
    def file(parent,name,path):
        public(name);require(time.monotonic()<end,'deadline')
        fd=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=parent)
        try:
            before=os.fstat(fd)
            require(stat.S_ISREG(before.st_mode) and before.st_nlink==1
                    and before.st_uid==SOURCE_UID and before.st_gid==SOURCE_GID
                    and 0<=before.st_size<=4*1024**2 and stat.S_IMODE(before.st_mode) in (0o644,0o755),'public_file')
            digest=sha256();size=0;header=b'';source=bytearray() if name.endswith('.py') else None
            while True:
                require(time.monotonic()<end,'deadline')
                data=os.read(fd,min(65536,before.st_size+1-size))
                if not data:break
                size+=len(data);require(size<=before.st_size,'public_file_changed');digest.update(data)
                if name.endswith('.pyc') and len(header)<16:header=(header+data)[:16]
                if source is not None:source.extend(data)
            require(size==before.st_size and identity(before)==identity(os.fstat(fd))
                    ==identity(os.stat(name,dir_fd=parent,follow_symlinks=False)),'public_file_changed')
            counter[1]+=size;require(counter[1]<=64*1024**2 and len(rows)<4096,'public_bound')
            rows.append({'path':path,'bytes':size,'sha256':digest.hexdigest(),
                         'mode':format(stat.S_IMODE(before.st_mode),'04o'),'uid':before.st_uid,'gid':before.st_gid})
            aux[path]={'identity':identity(before),'header':header.hex(),'mtime_ns':before.st_mtime_ns,
                       'mtime_seconds':int(before.st_mtime),'bytes':size,
                       'source_hash':importlib.util.source_hash(bytes(source)).hex() if source is not None else None}
        finally:os.close(fd)
    def directory(parent,name,callback):
        public(name);require(time.monotonic()<end,'deadline')
        before=os.stat(name,dir_fd=parent,follow_symlinks=False)
        fd=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=parent)
        try:
            require(stat.S_ISDIR(before.st_mode) and identity(before)==identity(os.fstat(fd)),'public_directory')
            callback(fd)
            require(identity(before)==identity(os.fstat(fd))
                    ==identity(os.stat(name,dir_fd=parent,follow_symlinks=False)),'public_directory_changed')
        finally:os.close(fd)
    def descend(parent,parts,callback):
        if not parts:return callback(parent)
        directory(parent,parts[0],lambda child:descend(child,parts[1:],callback))
    def walk(parent,prefix,depth=0):
        require(depth<64,'public_bound')
        with os.scandir(parent) as entries:
            for entry in entries:
                counter[0]+=1;require(counter[0]<=10000 and time.monotonic()<end,'public_bound');public(entry.name)
                info=os.stat(entry.name,dir_fd=parent,follow_symlinks=False);path=prefix+'/'+entry.name
                if stat.S_ISDIR(info.st_mode):directory(parent,entry.name,lambda child:walk(child,path,depth+1))
                else:
                    require(stat.S_ISREG(info.st_mode),'public_file');file(parent,entry.name,path)
    def app(fd):
        for name in FIXED:
            parts=name.split('/');descend(fd,parts[:-1],lambda parent:file(parent,parts[-1],name))
        for name in TREES:descend(fd,name.split('/'),lambda parent:walk(parent,name))
    require(app_root.is_absolute() and app_root.resolve(strict=True)==app_root,'public_root')
    root=os.open('/',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:descend(root,list(app_root.parts[1:]),app)
    finally:os.close(root)
    return sorted(rows,key=lambda row:row['path']),aux


def collect(raw, *, app_root=Path('/app'), expected_sha256=CONTROL_SHA256):
    require(type(raw) is bytes and 0<len(raw)<=2*1024**2 and sha256(raw).hexdigest()==expected_sha256,'control_pin')
    document=json.loads(raw)
    require(document['schema']=='market_security_public_source_fingerprint/1' and document['image_id']==IMAGE
            and document['stable_before_after'] is True and document['mount_overlays_absent'] is True
            and document['application_imported'] is False and document['secrets_read'] is False,'control_scope')
    require(sys.implementation.name=='cpython' and sys.version_info[:2]==(3,11)
            and sys.implementation.cache_tag=='cpython-311' and sys.pycache_prefix is None,'python_profile')
    end=time.monotonic()+20
    first,aux=scan(app_root,end);second,other=scan(app_root,end)
    require(first==second==document['files'] and aux==other,'whole_public_inventory_changed')
    expected={row['path']:row for row in first};caches=[row for row in first if row['path'].endswith('.pyc')]
    require(len(caches)==CACHE_COUNT,'cache_inventory')
    records=[]
    for row in caches:
        require(time.monotonic()<end,'deadline');path=row['path'];source_path,tag=cache_source(path)
        source=aux.get(source_path)
        header=bytes.fromhex(aux[path]['header'])
        records.append({'path':path,'bytes':row['bytes'],'sha256':row['sha256'],'cache_tag':tag,
                        'header_hex':header.hex(),'flags':struct.unpack('<I',header[4:8])[0] if len(header)>=8 else None,
                        'source_path':source_path,'source_exists':source is not None,
                        'source_bytes':source['bytes'] if source is not None else None,
                        'source_mtime_ns':source['mtime_ns'] if source is not None else None,
                        'source_sha256':expected[source_path]['sha256'] if source is not None else None,
                        'source_hash':source['source_hash'] if source is not None else None,
                        'classification':classify_header(path,header,source,cache_tag=sys.implementation.cache_tag,
                            magic=importlib.util.MAGIC_NUMBER,hash_policy=_imp.check_hash_based_pycs)})
    complete=all(row['classification'] in KNOWN_UNUSED for row in records)
    return {'schema':'market_security_public_cache_classification/1','source_manifest_sha256':expected_sha256,
            'container_id':document['container_id'],'image_id':IMAGE,'python_version':list(sys.version_info[:3]),
            'cache_tag':sys.implementation.cache_tag,'magic_hex':importlib.util.MAGIC_NUMBER.hex(),
            'hash_check_policy':_imp.check_hash_based_pycs,'standard_pycache_location':True,
            'scope':'normal_CPython_3_11_SourceFileLoader_cache_selection_and_header_rejection_only',
            'cache_count':len(records),'caches':records,'stable_before_after':True,
            'all_standard_cache_paths_nonselected_or_stale':complete,
            'status':'classified_not_accepted' if complete else 'unknown_cache_blocks_candidate',
            'explicit_bytecode_loader_reachability_assessed':False,'cached_code_deserialized':False,
            'cached_code_executed':False,'application_imported':False,'secrets_read':False,
            'runtime_changed':False,'security_accepted':False}


def main():
    resource.setrlimit(resource.RLIMIT_CPU,(20,20));resource.setrlimit(resource.RLIMIT_AS,(512*1024**2,)*2)
    resource.setrlimit(resource.RLIMIT_CORE,(0,0));signal.signal(signal.SIGALRM,signal.SIG_DFL);signal.alarm(20)
    require(os.getuid()==SOURCE_UID and os.getgid()==SOURCE_GID and len(sys.argv)==2
            and len(sys.argv[1])<=3*1024**2,'worker_profile')
    raw=base64.b64decode(sys.argv[1],validate=True)
    print(json.dumps(collect(raw),sort_keys=True))


if __name__=='__main__':
    try:main()
    except Exception:
        print('{"status":"refused","code":"public_cache_metadata_refused","secrets_read":false}')
        raise SystemExit(43) from None
