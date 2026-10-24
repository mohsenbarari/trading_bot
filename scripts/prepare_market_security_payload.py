"""Prepare one exact historical Market product plus a closed security overlay.

The initial profile is E46 only. Unknown observed-public-source fingerprints
refuse CI; no label, current main tree, or configuration release substitutes for
the actual runtime file inventory. No deployment or live environment access.
"""
from hashlib import sha1, sha256
import _imp
import argparse
import ast
import json
import os
from pathlib import Path, PurePosixPath
import re
import resource
import stat
import struct
import subprocess
import sys
import tempfile
from types import ModuleType

BASELINE = 'a6dcd636a1525a3a3d47e2a6083ca887b16080db'
BASELINE_TREE = '259c9c4964864b7acfd92f94c9856e664130d082'
BASELINE_IMAGE = 'sha256:e46aedbe135117301fb19183ff39587174162ca4a454ac60c7fccb2b302033cd'
BASELINE_INPUT_SIGNATURE = '669ee2267889d54bb36ef4cd8184d7a02c10fadeb4f39609e60ddce9f6f62a69'
E46_PUBLIC_FINGERPRINT_SHA256 = '2e7dda021162d9800eef7a3705fb479a9fc9d8433c4f3ed4a65429fc7364d4a3'
E46_CACHE_CLASSIFICATION_SHA256 = 'a8b4c4d0358b17a0024306d3cdcc60466c126f4aad36ea0270e996b1cd9a2070'
E46_CACHE_COUNT = 105
PUBLIC_PROOF_PATH = 'docs/security/evidence/2026-10-24-market-e46-public-source.json'
CACHE_PROOF_PATH = 'docs/security/evidence/2026-10-24-market-e46-public-cache-classification.json'
COPY_FILES = frozenset(('core/__init__.py', 'config/market_data_sources.v1.json'))
COPY_ROOTS = frozenset(('core/market_intelligence', 'contracts/market_data', 'deploy/market-data/migrations'))
MAX_FILES, MAX_FILE_BYTES, MAX_TOTAL_BYTES = 4096, 4 * 1024**2, 64 * 1024**2
SECRET_NAMES = frozenset(('.env', 'credentials', 'credentials.json', 'secrets', 'secrets.json',
                          'session', 'id_rsa', 'id_ed25519', 'authorized_keys', 'known_hosts'))
SECRET_SUFFIXES = ('.env', '.key', '.pem', '.p12', '.pfx', '.session', '.session-journal')
OVERLAY = ('deploy/market-data/Dockerfile', 'deploy/market-data/requirements.lock',
           'deploy/production/pip-bootstrap-requirements.txt', 'scripts/verify_image_trust.py',
           'scripts/verify_psycopg2_runtime.py', 'scripts/verify_sqlite_runtime.py', 'scripts/remove_runtime_pip.py')
TEST_MODULES = ('test_market_pipeline_stage3_foundation', 'test_market_pipeline_stage4_capture',
                'test_market_pipeline_stage5_coin_processor', 'test_market_pipeline_stage6_channel_processor',
                'test_market_private_pipeline_contracts')
ROOT_FIXTURE_TEST_ID = ('tests.test_market_pipeline_stage3_foundation.MarketPipelineStage3FoundationTests.'
                        'test_secret_contract_requires_root_only_parent_and_shared_runtime_group')
TEST_PATHS = tuple('tests/'+name+'.py' for name in TEST_MODULES) + (
    'tests/__init__.py', 'scripts/manage_market_pipeline_stage3.py',
    'scripts/audit_production_market_catchup.py', 'scripts/export_market_private_pipeline_schemas.py',
    'tests/fixtures/market_private_pipeline/capture_record.json',
    'tests/fixtures/market_private_pipeline/market_fact.json',
    'tests/fixtures/market_private_pipeline/market_fact_batch.json',
    'tests/fixtures/market_private_pipeline/estimator_snapshot.json',
    'deploy/market-data/compose.yml', 'deploy/market-data/compose.web.yml', 'deploy/market-data/compose.bot.yml')
TEST_OLD = b'        self.assertIn("python:3.11-slim-bookworm@sha256:", dockerfile)\n'
TEST_NEW = b'''        self.assertIn("python:3.11.17-slim-trixie@sha256:b95327ce1439e687237841b0210677aeadd62d55a060a5f6bcb9c15bf6251dd3", dockerfile)
        for security_helper in ("verify_image_trust.py", "verify_psycopg2_runtime.py", "verify_sqlite_runtime.py", "remove_runtime_pip.py"):
            self.assertIn(security_helper, dockerfile)
        self.assertIn("--require-hashes", dockerfile)
'''
COMMAND_HELPERS_SHA256 = '10ccbe216fef04e5f7a7ce2ad31ac30eca7eeae090dfaaf8074fa3cc5c789d44'
TEST_GUARD_SOURCE = r'''
def apply_test_fence(stack):
    from unittest.mock import patch
    denied=lambda *a,**k:(_ for _ in ()).throw(AssertionError('ambient_host_network_denied'))
    for target in ('socket.socket.connect','socket.socket.connect_ex','socket.socket.bind',
                   'socket.socket.listen','socket.socket.accept','socket.socket.sendto','socket.socket.sendmsg',
                   'socket.create_connection','socket.getaddrinfo','subprocess.run','subprocess.Popen',
                   'os.system','os.kill','os.killpg','urllib.request.urlopen','urllib.request.OpenerDirector.open',
                   'ctypes.CDLL','ctypes.PyDLL','resource.setrlimit','signal.signal','signal.alarm'):
        stack.enter_context(patch(target,side_effect=denied))
'''
# Define this ordinary trusted fixture function from the identical public bytes
# embedded below; neither definition calls a host/network operation.
exec(compile(TEST_GUARD_SOURCE,'<market-test-fence>','exec'))
PRODUCT_CHECK_SOURCE = r'''
def verify_product_files(app, expected_product, read_public, *, expected_uid=10001):
    import os,stat
    from pathlib import Path,PurePosixPath
    from hashlib import sha256
    app=Path(app);expected={row['path']:row for row in expected_product}
    assert expected and len(expected)==len(expected_product),'candidate_public_inventory'
    trees=('core/market_intelligence','contracts/market_data','deploy/market-data/migrations')
    expected_dirs={str(parent) for name in expected for parent in PurePosixPath(name).parents if str(parent)!='.'}
    def directory(path):
        info=path.lstat()
        assert stat.S_ISDIR(info.st_mode) and info.st_uid==info.st_gid==expected_uid and stat.S_IMODE(info.st_mode)==0o755,'candidate_public_directory_metadata'
    directory(app)
    for name in expected_dirs:directory(app/name)
    actual={'core/__init__.py','config/market_data_sources.v1.json'}
    actual_dirs={name for name in expected_dirs if not any(name==tree or name.startswith(tree+'/') for tree in trees)}
    count=0
    def walk(path):
        nonlocal count
        directory(path);actual_dirs.add(path.relative_to(app).as_posix())
        # scandir propagates permission errors; pathlib.rglob can suppress them.
        with os.scandir(path) as entries:
            for entry in entries:
                count+=1;assert count<=10000,'candidate_public_inventory_bound'
                assert not entry.is_symlink(),'candidate_public_symlink'
                if entry.is_dir(follow_symlinks=False):walk(Path(entry.path))
                else:
                    assert entry.is_file(follow_symlinks=False),'candidate_public_file_type'
                    actual.add(Path(entry.path).relative_to(app).as_posix())
    for tree in trees:walk(app/tree)
    assert actual==set(expected) and actual_dirs==expected_dirs,'candidate_public_inventory'
    for name,row in expected.items():
        path=app/name;raw=read_public(path,4194304);info=path.lstat()
        assert len(raw)==row['bytes'] and sha256(raw).hexdigest()==row['sha256'] and info.st_uid==info.st_gid==expected_uid,'candidate_public_file_identity'
        assert stat.S_ISREG(info.st_mode) and stat.S_IMODE(info.st_mode)==(0o755 if row['mode']=='100755' else 0o644),'candidate_public_file_metadata'
    return True
'''
exec(compile(PRODUCT_CHECK_SOURCE,'<market-product-files>','exec'))


class PayloadError(RuntimeError):
    """Only static public reason codes may leave this preparer."""


def require(value, code):
    if not value:
        raise PayloadError(code)


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()


def public_path(path):
    if type(path) is not str or not path or path.startswith('/'):
        return False
    parts = path.split('/')
    return (str(PurePosixPath(path)) == path and all(part not in ('', '.', '..')
            and not any(ord(char) < 32 for char in part)
            and part not in SECRET_NAMES and not part.startswith('.env.')
            and not part.endswith(SECRET_SUFFIXES) for part in parts))


def copied_path(path):
    return public_path(path) and (path in COPY_FILES or any(path.startswith(root + '/') for root in COPY_ROOTS))


def parse_document(raw):
    require(type(raw) is bytes and 0 < len(raw) <= 2 * 1024**2, 'public_fingerprint_bound')
    def unique(pairs):
        value = {}
        for key, item in pairs:
            require(key not in value, 'public_fingerprint_json')
            value[key] = item
        return value
    try:
        value = json.loads(raw, object_pairs_hook=unique,
                           parse_constant=lambda _: require(False, 'public_fingerprint_json'))
    except (ValueError, UnicodeError, RecursionError):
        raise PayloadError('public_fingerprint_json') from None
    require(type(value) is dict, 'public_fingerprint_json')
    return value


def verify_cache_classification(raw, expected_sha256, control, extras, baseline_files, control_sha256):
    require(type(expected_sha256) is str and re.fullmatch('[a-f0-9]{64}',expected_sha256)
            and type(raw) is bytes and 0<len(raw)<=2*1024**2 and sha256(raw).hexdigest()==expected_sha256,'cache_classification_pin')
    value=parse_document(raw)
    fields={'schema','source_manifest_sha256','container_id','image_id','python_version','cache_tag','magic_hex',
            'hash_check_policy','standard_pycache_location','scope','cache_count','caches','stable_before_after',
            'all_standard_cache_paths_nonselected_or_stale','status','explicit_bytecode_loader_reachability_assessed',
            'cached_code_deserialized','cached_code_executed','application_imported','secrets_read','runtime_changed','security_accepted'}
    require(set(value)==fields and value['schema']=='market_security_public_cache_classification/1'
            and value['source_manifest_sha256']==control_sha256
            and value['container_id']==control['container_id'] and value['image_id']==BASELINE_IMAGE,'cache_classification_identity')
    require(value['python_version']==[3,11,16] and all(type(n) is int for n in value['python_version'])
            and value['cache_tag']=='cpython-311' and value['magic_hex']=='a70d0d0a' and value['hash_check_policy']=='default'
            and value['standard_pycache_location'] is True and value['stable_before_after'] is True
            and value['all_standard_cache_paths_nonselected_or_stale'] is True and value['status']=='classified_not_accepted'
            and value['scope']=='normal_CPython_3_11_SourceFileLoader_cache_selection_and_header_rejection_only'
            and all(value[field] is False for field in ('explicit_bytecode_loader_reachability_assessed',
                'cached_code_deserialized','cached_code_executed','application_imported','secrets_read','runtime_changed','security_accepted')),
            'cache_classification_scope')
    rows=value['caches'];observed={row['path']:row for row in control['files']}
    require(type(rows) is list and len(rows)==len(extras)==E46_CACHE_COUNT
            and type(value['cache_count']) is int and value['cache_count']==len(rows)
            and rows==sorted(rows,key=lambda row:row['path'])
            and {row['path'] for row in rows}==set(extras),'cache_classification_inventory')
    kinds={'nonselected_cache_tag':0,'timestamp_stale':0}
    for row in rows:
        require(type(row) is dict and set(row)=={'path','bytes','sha256','cache_tag','header_hex','flags','source_path',
            'source_exists','source_bytes','source_mtime_ns','source_sha256','source_hash','classification'},'cache_classification_shape')
        path=PurePosixPath(row['path'])
        match=re.fullmatch(r'([A-Za-z_][A-Za-z0-9_]*)\.(cpython-(?:311|312))\.pyc',path.name)
        require(match is not None and path.parent.name=='__pycache__' and row['path'].startswith('core/market_intelligence/'),
                'cache_classification_path')
        source_path=str(path.parent.parent/(match[1]+'.py'));source=baseline_files.get(source_path)
        require(row['bytes']==observed[row['path']]['bytes'] and type(row['bytes']) is int
                and row['sha256']==observed[row['path']]['sha256'] and row['cache_tag']==match[2]
                and row['source_path']==source_path and row['source_exists'] is (source is not None)
                and type(row['header_hex']) is str and re.fullmatch('[a-f0-9]{32}',row['header_hex']),'cache_classification_metadata')
        header=bytes.fromhex(row['header_hex']);flags=struct.unpack('<I',header[4:8])[0]
        require(type(row['flags']) is int and row['flags']==flags,'cache_classification_header')
        if source is None:
            require(all(row[key] is None for key in ('source_bytes','source_mtime_ns','source_sha256','source_hash')),
                    'cache_classification_source')
        else:
            data=source[1]
            require(type(row['source_bytes']) is int and row['source_bytes']==len(data)
                    and type(row['source_mtime_ns']) is int and 0<=row['source_mtime_ns']<2**63
                    and row['source_sha256']==sha256(data).hexdigest()
                    and row['source_hash']==_imp.source_hash(int.from_bytes(bytes.fromhex('a70d0d0a'),'little'),data).hex(),
                    'cache_classification_source')
        if match[2]=='cpython-312':kind='nonselected_cache_tag'
        else:
            require(source is not None and header[:4]==bytes.fromhex('a70d0d0a') and flags==0,'cache_classification_unknown')
            mtime,size=struct.unpack('<II',header[8:16])
            require(mtime!=(row['source_mtime_ns']//10**9&0xffffffff) or size!=(len(source[1])&0xffffffff),
                    'cache_classification_unknown')
            kind='timestamp_stale'
        require(row['classification']==kind,'cache_classification_unknown');kinds[kind]+=1
    return {'control_sha256':expected_sha256,'cache_files':len(rows),'classifications':kinds,
            'scope':'standard_CPython_3_11_source_loader_selection_and_header_rejection',
            'custom_or_third_party_bytecode_loader_assessed':False,'security_accepted':False}


def verify_public_fingerprint(raw, baseline_files, expected_sha256, *, cache_raw=None, cache_sha256=None):
    """Verify pinned public runtime files against complete historical COPY inputs.

    baseline_files maps each Git-relative path to (Git mode, exact public bytes).
    No private Env/config/session bytes are inputs, outputs or hashed values.
    """
    require(type(expected_sha256) is str and re.fullmatch('[a-f0-9]{64}', expected_sha256),
            'baseline_public_fingerprint_unreviewed')
    require(type(raw) is bytes and 0 < len(raw) <= 2 * 1024**2, 'public_fingerprint_bound')
    require(sha256(raw).hexdigest() == expected_sha256,
            'baseline_public_fingerprint_pin')
    value = parse_document(raw)
    fields = {'schema', 'container_id', 'image_id', 'release_sha', 'release_tree', 'image_input_signature',
              'platform', 'runtime_user', 'files', 'file_count', 'total_bytes', 'fingerprint_sha256',
              'stable_before_after', 'mount_overlays_absent', 'application_imported', 'secrets_read', 'runtime_changed'}
    require(set(value) == fields and value['schema'] == 'market_security_public_source_fingerprint/1',
            'public_fingerprint_shape')
    require(type(value['container_id']) is str and re.fullmatch('[a-f0-9]{64}', value['container_id'])
            and value['image_id'] == BASELINE_IMAGE and value['release_sha'] == BASELINE
            and value['release_tree'] == BASELINE_TREE and value['image_input_signature'] == BASELINE_INPUT_SIGNATURE
            and value['platform'] == 'linux/amd64' and value['runtime_user'] == '10001:10001',
            'public_fingerprint_identity')
    require(value['stable_before_after'] is True and value['mount_overlays_absent'] is True
            and all(value[key] is False for key in ('application_imported', 'secrets_read', 'runtime_changed')),
            'public_fingerprint_scope')
    rows = value['files']
    require(type(rows) is list and 0 < len(rows) <= MAX_FILES, 'public_fingerprint_bound')
    seen, total = set(), 0
    for row in rows:
        require(type(row) is dict and set(row) == {'path', 'bytes', 'sha256', 'mode', 'uid', 'gid'},
                'public_fingerprint_shape')
        name = row['path']
        require(copied_path(name) and name not in seen, 'public_fingerprint_path')
        seen.add(name)
        require(type(row['bytes']) is int and 0 <= row['bytes'] <= MAX_FILE_BYTES
                and type(row['sha256']) is str and re.fullmatch('[a-f0-9]{64}', row['sha256'])
                and row['mode'] in ('0644', '0755') and type(row['uid']) is int and row['uid'] == 10001
                and type(row['gid']) is int and row['gid'] == 10001, 'public_fingerprint_metadata')
        total += row['bytes']
        require(total <= MAX_TOTAL_BYTES, 'public_fingerprint_bound')
    require(rows == sorted(rows, key=lambda row: row['path'])
            and type(value['file_count']) is int and value['file_count'] == len(rows)
            and type(value['total_bytes']) is int and value['total_bytes'] == total
            and value['fingerprint_sha256'] == sha256(encoded(rows)).hexdigest(), 'public_fingerprint_digest')
    expected = []
    for name, (mode, data) in sorted(baseline_files.items()):
        require(copied_path(name) and mode in ('100644', '100755') and type(data) is bytes
                and len(data) <= MAX_FILE_BYTES,
                'baseline_copy_input')
        expected.append({'path': name, 'bytes': len(data), 'sha256': sha256(data).hexdigest(),
                         'mode': '0755' if mode == '100755' else '0644', 'uid': 10001, 'gid': 10001})
    actual={row['path']:row for row in rows};expected_map={row['path']:row for row in expected}
    require(all(actual.get(name)==row for name,row in expected_map.items()) and COPY_FILES <= seen and all(any(name.startswith(root + '/') for name in seen)
            for root in COPY_ROOTS), 'baseline_copy_scope_drift')
    extras=set(actual)-set(expected_map);cache_proof=None
    if extras:cache_proof=verify_cache_classification(cache_raw,cache_sha256,value,extras,baseline_files,expected_sha256)
    else:require(rows==expected,'baseline_copy_scope_drift')
    return {'control_sha256': expected_sha256, 'fingerprint_sha256': value['fingerprint_sha256'],
            'image_id': BASELINE_IMAGE, 'container_id': value['container_id'], 'file_count': len(rows),
            'total_bytes': total, 'runtime_files_equal_public_git':not extras,
            'product_files_equal_public_git':True,'product_file_count':len(expected),'cache_classification':cache_proof,
            'full_observed_inventory_preserved_in_control':True,
            'writable_layer_absence_asserted': False, 'security_accepted': False}


def git(repo, *args, maximum=8*1024**2):
    def limits():
        resource.setrlimit(resource.RLIMIT_FSIZE,(maximum+1,maximum+1))
        resource.setrlimit(resource.RLIMIT_CORE,(0,0))
    with tempfile.TemporaryFile() as output:
        try:
            subprocess.run(['/usr/bin/git','--no-optional-locks','-C',str(repo),*args],
                stdin=subprocess.DEVNULL,stdout=output,stderr=subprocess.DEVNULL,timeout=30,
                check=True,preexec_fn=limits)
        except (OSError,subprocess.SubprocessError):
            raise PayloadError('public_git_read_failed') from None
        require(output.tell()<=maximum,'public_git_output_bound');output.seek(0)
        return output.read(maximum+1)


def tree_entries(raw):
    result={}
    for row in raw.split(b'\0'):
        if not row:continue
        try:
            header,name=row.split(b'\t',1);mode,kind,oid=header.decode('ascii').split();name=name.decode('utf-8')
        except (ValueError,UnicodeError):raise PayloadError('git_tree_shape') from None
        # Whole historical trees can contain unrelated symlinks. Selected build
        # and test inputs below require ordinary tracked files individually.
        require(type(name) is str and not name.startswith('/') and str(PurePosixPath(name))==name
                and all(part not in ('','.','..') for part in name.split('/'))
                and not any(ord(char)<32 for char in name) and name not in result
                and mode in ('100644','100755','120000') and kind=='blob'
                and re.fullmatch('[a-f0-9]{40}',oid),'git_tree_shape')
        result[name]=(mode,oid);require(len(result)<=30000,'git_tree_bound')
    return result


def tree_digest(files):
    root={}
    for name,value in files.items():
        node=root;parts=name.split('/')
        for part in parts[:-1]:
            node=node.setdefault(part,{});require(type(node) is dict,'git_tree_shape')
        require(parts[-1] not in node,'git_tree_shape');node[parts[-1]]=value
    def digest(node):
        data=bytearray()
        for name,value in sorted(node.items(),key=lambda row:row[0].encode()+(b'/' if type(row[1]) is dict else b'')):
            mode,oid=('40000',digest(value)) if type(value) is dict else value
            data.extend(mode.encode()+b' '+name.encode()+b'\0'+bytes.fromhex(oid))
        return sha1(b'tree '+str(len(data)).encode()+b'\0'+data).hexdigest()
    return digest(root)


def blob(repo, entry):
    mode,oid=entry;require(mode in ('100644','100755'),'source_link_refused')
    size=git(repo,'cat-file','-s',oid,maximum=64)
    require(re.fullmatch(rb'[0-9]{1,8}\n',size) and int(size)<=MAX_FILE_BYTES,'source_file_bound')
    raw=git(repo,'cat-file','blob',oid,maximum=MAX_FILE_BYTES)
    require(len(raw)==int(size) and sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()==oid,'source_blob_identity')
    return raw


def published(repo, commit):
    require(type(commit) is str and re.fullmatch('[a-f0-9]{40}',commit),'source_commit')
    require(git(repo,'rev-parse','--show-object-format').strip()==b'sha1'
            and git(repo,'rev-parse','HEAD').strip()==commit.encode()
            and git(repo,'symbolic-ref','--short','HEAD').strip()==b'main'
            and not git(repo,'status','--porcelain','--untracked-files=normal'),'published_main_required')
    remote=git(repo,'remote','get-url','origin').decode().strip()
    require(remote in ('https://github.com/mohsenbarari/trading_bot.git','https://github.com/mohsenbarari/trading_bot',
                       'git@github.com:mohsenbarari/trading_bot.git'),'source_repository')
    require(git(repo,'ls-remote','--exit-code','origin','refs/heads/main').split()==[commit.encode(),b'refs/heads/main'],
            'source_publication_drift')


def write(path, raw, mode=0o600):
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,mode)
    with os.fdopen(fd,'wb') as output:
        output.write(raw);output.flush();os.fchmod(output.fileno(),mode);os.fsync(output.fileno())


def public_destination(root,name):
    """Public COPY/test directories stay readable under a private outer root."""
    require(public_path(name) and root.is_absolute() and root.resolve(strict=True)==root,'public_output_path')
    descriptors=[os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)]
    try:
        for part in name.split('/')[:-1]:
            parent=descriptors[-1];created=False
            try:os.mkdir(part,0o755,dir_fd=parent);created=True
            except FileExistsError:pass
            fd=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=parent);descriptors.append(fd)
            info=os.fstat(fd)
            require(stat.S_ISDIR(info.st_mode) and info.st_uid==os.geteuid() and not info.st_mode&0o022,'public_output_directory')
            if created:os.fchmod(fd,0o755)
            require(stat.S_IMODE(os.fstat(fd).st_mode)==0o755,'public_output_directory')
        return root/name
    finally:
        for fd in reversed(descriptors):os.close(fd)


def read(path, maximum=2*1024**2):
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        before=os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_uid==os.geteuid() and before.st_nlink==1
                and not before.st_mode&0o022 and 0<=before.st_size<=maximum,'file_metadata')
        data=bytearray()
        while len(data)<=before.st_size:
            chunk=os.read(fd,min(65536,before.st_size+1-len(data)))
            if not chunk:break
            data.extend(chunk)
        identity=lambda s:(s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
        require(len(data)==before.st_size and identity(before)==identity(os.fstat(fd))==identity(path.lstat()),'file_changed')
        return bytes(data)
    finally:os.close(fd)


def dependency_delta(old, new):
    def records(raw):
        result={}
        for line in raw.decode('ascii').replace('\\\n',' ').splitlines():
            if not line.strip():continue
            match=re.fullmatch(r'([a-z0-9-]+)(==[^ ]+| @ https://[^ ]+)\s+--hash=sha256:([a-f0-9]{64})\s*',line)
            require(match is not None and match[1] not in result,'dependency_lock_shape')
            result[match[1]]=(match[2],match[3])
        return result
    before,after=records(old),records(new)
    expected={'annotated-types','pydantic','pydantic-core','pyaes','pyasn1','psycopg2-binary','rsa','telethon','typing-extensions'}
    require(set(before)==set(after)==expected and before['pydantic']==after['pydantic']
            and before['pydantic'][0]=='==2.8.2' and before['pydantic-core']==after['pydantic-core']
            and all(before[name]==after[name] for name in expected-{'psycopg2-binary'}),'dependency_product_drift')
    require(before['psycopg2-binary']==('==2.9.9','977646e05232579d2e7b9c59e21dbe5261f403a88417f6a6512e70d3f8a046be')
            and after['psycopg2-binary']==(' @ https://files.pythonhosted.org/packages/ed/76/7b4383014be0fcc6c1c0e24292845a14e1672cf17fca62ca0a2bd5f4563d/psycopg2_binary-2.9.13.tar.gz',
                                        'e324ecf60f952d21dd11413b8bbed0951bbd99579a06fd06f28bfc37737cd373'),'dependency_security_delta')
    return {'psycopg2-binary':{'before':'2.9.9','after':'2.9.13','source_build_system_libpq':True},
            'pydantic_unchanged':'2.8.2','all_other_locked_entries_unchanged':True}


def test_transform(path, raw):
    if path!='tests/test_market_pipeline_stage3_foundation.py':return raw
    require(raw.count(TEST_OLD)==1 and TEST_NEW not in raw,'baseline_test_assertion')
    result=raw.replace(TEST_OLD,TEST_NEW,1)
    require(result.replace(TEST_NEW,TEST_OLD,1)==raw,'baseline_test_assertion')
    return result


def historical_test_ids(selected):
    result=[]
    for name in TEST_MODULES:
        tree=ast.parse(selected['tests/'+name+'.py'][2])
        for node in tree.body:
            if isinstance(node,ast.ClassDef):
                for child in node.body:
                    if isinstance(child,(ast.FunctionDef,ast.AsyncFunctionDef)) and child.name.startswith('test_'):
                        result.append('tests.'+name+'.'+node.name+'.'+child.name)
    require(len(result)==len(set(result))==85 and ROOT_FIXTURE_TEST_ID in result,'historical_test_inventory')
    return sorted(result)


def prepare(repo, commit, output, *, profile='e46'):
    require(profile=='e46','unreviewed_market_profile')
    require(type(E46_PUBLIC_FINGERPRINT_SHA256) is str and re.fullmatch('[a-f0-9]{64}',E46_PUBLIC_FINGERPRINT_SHA256),
            'baseline_public_fingerprint_unreviewed')
    published(repo,commit)
    require(git(repo,'rev-parse',BASELINE+'^{tree}').strip().decode()==BASELINE_TREE,'baseline_tree_identity')
    base=tree_entries(git(repo,'ls-tree','-r','-z',BASELINE));require(tree_digest(base)==BASELINE_TREE,'baseline_tree_identity')
    source_tree=git(repo,'rev-parse',commit+'^{tree}').strip().decode();require(re.fullmatch('[a-f0-9]{40}',source_tree),'source_tree_identity')
    overlay=tree_entries(git(repo,'ls-tree','-r','-z',commit,'--',*OVERLAY))
    require(set(overlay)==set(OVERLAY) and all(mode in ('100644','100755') for mode,_ in overlay.values()),'security_overlay_inventory')
    mixed={**base,**overlay}
    require(all(mixed[name]==entry for name,entry in base.items() if name not in OVERLAY),'baseline_product_drift')
    product_entries={name:entry for name,entry in base.items() if name in COPY_FILES or any(name.startswith(root+'/') for root in COPY_ROOTS)}
    require(0<len(product_entries)<=MAX_FILES and all(copied_path(name) for name in product_entries),'baseline_copy_input')
    product={};product_bytes=0
    for name,entry in product_entries.items():
        raw=blob(repo,entry);product_bytes+=len(raw);require(product_bytes<=MAX_TOTAL_BYTES,'context_byte_bound')
        product[name]=(entry[0],raw)
    proof_entry=tree_entries(git(repo,'ls-tree','-r','-z',commit,'--',PUBLIC_PROOF_PATH,CACHE_PROOF_PATH))
    require(set(proof_entry)=={PUBLIC_PROOF_PATH,CACHE_PROOF_PATH},'public_fingerprint_missing')
    public_proof=blob(repo,proof_entry[PUBLIC_PROOF_PATH])
    cache_proof=blob(repo,proof_entry[CACHE_PROOF_PATH])
    binding=verify_public_fingerprint(public_proof,product,E46_PUBLIC_FINGERPRINT_SHA256,
        cache_raw=cache_proof,cache_sha256=E46_CACHE_CLASSIFICATION_SHA256)
    inputs=dict(product)
    for name in OVERLAY:inputs[name]=(mixed[name][0],blob(repo,mixed[name]))
    inputs=dict(sorted(inputs.items()))
    require(sum(len(raw) for _,raw in inputs.values())<=MAX_TOTAL_BYTES,'context_byte_bound')
    delta=dependency_delta(blob(repo,base['deploy/market-data/requirements.lock']),inputs['deploy/market-data/requirements.lock'][1])
    selected={}
    for name in TEST_PATHS:
        require(name in base,'baseline_test_missing');raw=blob(repo,base[name]);selected[name]=(base[name][0],raw,test_transform(name,raw),BASELINE,base[name][1])
    name='deploy/market-data/Dockerfile';mode,raw=inputs[name];selected[name]=(mode,raw,raw,commit,overlay[name][1])
    test_ids=historical_test_ids(selected)
    require(output.is_absolute() and output.parent.resolve(strict=True)==output.parent and not os.path.lexists(output),'output_scope')
    for parent in output.parents:
        info=parent.lstat();require(stat.S_ISDIR(info.st_mode) and info.st_uid in (0,os.geteuid()) and not info.st_mode&0o022,'output_parent')
    os.mkdir(output,0o700)
    for name in ('context','tests','reports','docker-config'):(output/name).mkdir(mode=0o755 if name=='tests' else 0o700)
    (output/'tests').chmod(0o755)
    files=[]
    for name,(mode,raw) in inputs.items():
        target=public_destination(output/'context',name)
        write(target,raw,0o755 if mode=='100755' else 0o644)
        files.append({'path':name,'bytes':len(raw),'sha256':sha256(raw).hexdigest(),'git_blob':mixed[name][1],'mode':mode})
    tests=[]
    for name,(mode,original,effective,origin,oid) in sorted(selected.items()):
        target=public_destination(output/'tests',name)
        write(target,effective,0o755 if mode=='100755' else 0o644)
        tests.append({'path':name,'bytes':len(effective),'sha256':sha256(effective).hexdigest(),'original_sha256':sha256(original).hexdigest(),
                      'git_blob':oid,'source_commit':origin,'transformation':'security_base_assertion_only' if original!=effective else 'none'})
    write(output/'tests/public-baseline.json',public_proof,0o644)
    test_proof={'schema':'market_security_candidate_test_sources/1','baseline_commit':BASELINE,'source_commit':commit,
                'modules':list(TEST_MODULES),'expected_tests':85,'test_ids':test_ids,'files':tests,'public_baseline_sha256':E46_PUBLIC_FINGERPRINT_SHA256,
                'runtime_data_used':False,'historical_test_change':'only_Bookworm_assertion_replaced_with_explicit_reviewed_security_base_checks'}
    write(output/'tests/test-sources.json',encoded(test_proof),0o644)
    signature=sha256(encoded({'scope':'market-pipeline-security-runtime-v1','platform':'linux/amd64','files':files})).hexdigest()
    record={'schema':'market_security_payload/1','profile':'e46','baseline_commit':BASELINE,'baseline_tree':BASELINE_TREE,
            'baseline_image_id':BASELINE_IMAGE,'source_commit':commit,'source_tree':source_tree,'payload_tree':tree_digest(mixed),
            'image_input_signature':signature,'public_source_binding':binding,'dependency_delta':delta,'files':files,
            'test_sources_sha256':sha256(encoded(test_proof)).hexdigest(),'test_ids':test_ids,'security_accepted':False,'deployment_eligible':False,
            'production_services_changed':False,'runtime_environment_read':False}
    published(repo,commit);write(output/'payload.json',encoded(record))
    return record


def verify_context(output, record):
    require(record['schema']=='market_security_payload/1' and record['profile']=='e46'
            and record['baseline_commit']==BASELINE and record['baseline_tree']==BASELINE_TREE
            and record['baseline_image_id']==BASELINE_IMAGE,'payload_identity')
    expected={row['path']:row for row in record['files']}
    require(len(expected)==len(record['files']) and set(OVERLAY)<=set(expected)
            and all(copied_path(name) or name in OVERLAY for name in expected),'context_inventory')
    seen=set();count=0;total=0
    for parent,dirs,files in os.walk(output/'context',followlinks=False):
        for name in (*dirs,*files):
            path=Path(parent)/name;info=path.lstat();count+=1
            require(count<=10000 and not stat.S_ISLNK(info.st_mode) and info.st_uid==os.geteuid()
                    and not info.st_mode&0o022 and (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)),
                    'context_metadata')
            if stat.S_ISDIR(info.st_mode):require(stat.S_IMODE(info.st_mode)==0o755,'context_directory_mode')
        for name in files:
            path=Path(parent)/name;relative=path.relative_to(output/'context').as_posix()
            require(relative in expected,'context_inventory');row=expected[relative]
            raw=read(path,MAX_FILE_BYTES);total+=len(raw);require(total<=MAX_TOTAL_BYTES,'context_byte_bound')
            require(len(raw)==row['bytes'] and sha256(raw).hexdigest()==row['sha256']
                    and sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()==row['git_blob']
                    and stat.S_IMODE(path.lstat().st_mode)==(0o755 if row['mode']=='100755' else 0o644),
                    'context_source_drift');seen.add(relative)
    require(seen==set(expected),'context_inventory')


def ci_admit(root):
    require(os.geteuid()==1001 and os.environ.get('GITHUB_ACTIONS')=='true'
            and os.environ.get('RUNNER_ENVIRONMENT')=='github-hosted'
            and os.environ.get('GITHUB_REPOSITORY')=='mohsenbarari/trading_bot','github_ci_only')
    parent=Path(os.environ['RUNNER_TEMP']).resolve(strict=True)
    require(root.is_absolute() and root.resolve(strict=True)==root and root.parent==parent
            and root.name.startswith('security-market-e46-') and root.stat().st_uid==1001
            and stat.S_IMODE(root.stat().st_mode)==0o700,'ci_root')
    os.umask(0o077)


def primitives():
    path=Path(__file__).resolve(strict=True).with_name('compose_security_os_candidate.py')
    raw=read(path,131072);require(sha256(raw).hexdigest()==COMMAND_HELPERS_SHA256,'command_helper_pin')
    module=ModuleType('_sealed_market_command_primitives');module.__file__=str(path)
    exec(compile(raw,str(path),'exec'),module.__dict__)
    return module


def image_metadata(helpers, root, image, record):
    require(type(image) is str and re.fullmatch('sha256:[a-f0-9]{64}',image),'candidate_image_identity')
    values=json.loads(helpers.docker(root,['image','inspect',image],maximum=2*1024**2))
    require(type(values) is list and len(values)==1,'candidate_image_identity');value=values[0]
    config=value.get('Config',{});labels=config.get('Labels',{})
    expected={'org.opencontainers.image.revision':BASELINE,'io.gold-trade.release.tree':record['payload_tree'],
              'io.gold-trade.release.input-signature':record['image_input_signature'],
              'io.gold-trade.security.baseline.tree':BASELINE_TREE,
              'io.gold-trade.security.baseline.image':BASELINE_IMAGE,
              'io.gold-trade.security.overlay.revision':record['source_commit'],
              'io.gold-trade.security.overlay.tree':record['source_tree']}
    require(value.get('Id')==image and value.get('Os')=='linux' and value.get('Architecture')=='amd64'
            and config.get('User')=='10001:10001' and config.get('WorkingDir')=='/app'
            and config.get('Entrypoint')==['python','-m','core.market_intelligence.private_pipeline_foundation']
            and config.get('Cmd')==['service','--role','market-processor']
            and all(labels.get(key)==item for key,item in expected.items())
            and value.get('RootFS',{}).get('Type')=='layers' and type(value['RootFS'].get('Layers')) is list
            and all(re.fullmatch('sha256:[a-f0-9]{64}',item) for item in value['RootFS']['Layers']),
            'candidate_image_contract')
    return {'image_id':image,'platform':'linux/amd64','runtime_user':'10001:10001',
            'labels':expected,'rootfs_layers':list(value['RootFS']['Layers'])}


NATIVE_PROGRAM = r'''
import json,os,runpy,sys
assert os.getuid()==os.getgid()==10001
sys.path[:0]=['/app','/opt/market-python']
image=sys.argv[1]
checks=(('/usr/local/libexec/verify_image_trust.py',[]),
        ('/usr/local/libexec/verify_psycopg2_runtime.py',['--package-root','/opt/market-python','--final-runtime']),
        ('/usr/local/libexec/verify_sqlite_runtime.py',[]))
for path,args in checks:
    sys.argv=[path,*args]
    try:runpy.run_path(path,run_name='__main__')
    except SystemExit as error:
        if error.code not in (None,0):raise
import pydantic,psycopg2
assert pydantic.__version__=='2.8.2' and psycopg2.__version__.split()[0]=='2.9.13'
print(json.dumps({'schema':'market_security_native_checks/1','image_id':image,'status':'passed',
                 'pydantic':'2.8.2','psycopg2':'2.9.13','execution_uid':os.getuid(),'execution_gid':os.getgid(),
                 'runtime_data_used':False,'security_accepted':False},sort_keys=True))
'''


def candidate_test_program(record, *, root_fixture=False):
    """Bind the full historical suite and product bytes before test imports."""
    product=[row for row in record['files'] if copied_path(row['path'])]
    require(type(root_fixture) is bool,'test_execution_profile')
    setup=("expected_proof="+repr(record['test_sources_sha256'])+"\nexpected_commit="+repr(record['source_commit'])+
           "\nexpected_product="+repr(product)+"\nroot_fixture="+repr(root_fixture)+
           "\nexpected_ids="+repr(record['test_ids'])+"\nroot_test="+repr(ROOT_FIXTURE_TEST_ID)+"\n")
    return setup+r'''
import ctypes,json,os,pathlib,resource,signal,socket,subprocess,sys,threading,types,unittest,urllib.request
from contextlib import ExitStack
from hashlib import sha1,sha256
from unittest.mock import patch
image=sys.argv[1]
assert os.getuid()==os.getgid()==(0 if root_fixture else 10001)
def read(path,maximum):
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        before=os.fstat(fd)
        assert __import__('stat').S_ISREG(before.st_mode) and before.st_nlink==1 and not before.st_mode&0o022 and 0<=before.st_size<=maximum
        chunks=bytearray()
        while len(chunks)<=before.st_size:
            chunk=os.read(fd,min(65536,before.st_size+1-len(chunks)))
            if not chunk:break
            chunks.extend(chunk)
        identity=lambda s:(s.st_dev,s.st_ino,s.st_mode,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
        assert len(chunks)==before.st_size and identity(before)==identity(os.fstat(fd))==identity(path.lstat())
        return bytes(chunks)
    finally:os.close(fd)
evidence=pathlib.Path('/evidence');proof_raw=read(evidence/'test-sources.json',2097152)
assert sha256(proof_raw).hexdigest()==expected_proof
proof=json.loads(proof_raw)
assert proof['schema']=='market_security_candidate_test_sources/1' and proof['source_commit']==expected_commit
assert proof['baseline_commit']=='a6dcd636a1525a3a3d47e2a6083ca887b16080db' and proof['expected_tests']==85 and proof['runtime_data_used'] is False
assert proof['test_ids']==expected_ids and len(expected_ids)==len(set(expected_ids))==85 and root_test in expected_ids
assert proof['modules']==['test_market_pipeline_stage3_foundation','test_market_pipeline_stage4_capture','test_market_pipeline_stage5_coin_processor','test_market_pipeline_stage6_channel_processor','test_market_private_pipeline_contracts']
rows={row['path']:row for row in proof['files']};assert len(rows)==len(proof['files'])
assert {p.relative_to(evidence).as_posix() for p in evidence.rglob('*') if p.is_file()}==set(rows)|{'test-sources.json','public-baseline.json'}
assert not any(p.is_symlink() for p in evidence.rglob('*'))
sources={}
for name,row in rows.items():
    raw=read(evidence/name,4194304)
    assert len(raw)==row['bytes'] and sha256(raw).hexdigest()==row['sha256']
    if row['transformation']=='security_base_assertion_only':
        assert name=='tests/test_market_pipeline_stage3_foundation.py'
        original=raw.replace(TEST_NEW,TEST_OLD,1)
        assert raw.count(TEST_NEW)==1 and original.count(TEST_OLD)==1
    else:
        assert row['transformation']=='none';original=raw
    assert sha256(original).hexdigest()==row['original_sha256']
    assert sha1(b'blob '+str(len(original)).encode()+b'\0'+original).hexdigest()==row['git_blob']
    assert row['source_commit']==(expected_commit if name=='deploy/market-data/Dockerfile' else proof['baseline_commit'])
    if name.endswith('.py'):sources[name]=raw
assert sha256(read(evidence/'public-baseline.json',2097152)).hexdigest()==proof['public_baseline_sha256']
__PRODUCT_CHECK__
assert verify_product_files(pathlib.Path('/app'),expected_product,read)
sys.path[:0]=['/app','/opt/market-python','/evidence']
__TEST_GUARD__
with ExitStack() as fence:
    apply_test_fence(fence)
    package=types.ModuleType('scripts');package.__path__=[str(evidence/'scripts')];sys.modules['scripts']=package
    for name in ('scripts/manage_market_pipeline_stage3.py','scripts/audit_production_market_catchup.py','scripts/export_market_private_pipeline_schemas.py'):
        key=name[:-3].replace('/','.');module=types.ModuleType(key);module.__file__=str(evidence/name);sys.modules[key]=module
        exec(compile(sources[name],module.__file__,'exec'),module.__dict__)
        setattr(package,key.split('.')[-1],module)
    suite=unittest.TestSuite()
    for name in proof['modules']:
        path='tests/'+name+'.py';module=types.ModuleType('tests.'+name);module.__file__=str(evidence/path);sys.modules[module.__name__]=module
        exec(compile(sources[path],module.__file__,'exec'),module.__dict__)
        suite.addTests(unittest.defaultTestLoader.loadTestsFromModule(module))
    assert suite.countTestCases()==85
    def cases(value):
        for test in value:
            if isinstance(test,unittest.TestSuite):yield from cases(test)
            else:yield test
    tests=list(cases(suite));assert sorted(test.id() for test in tests)==expected_ids
    selected=[test for test in tests if (test.id()==root_test)==root_fixture]
    assert len(selected)==(1 if root_fixture else 84)
    selected_ids=sorted(test.id() for test in selected)
    results=[]
    def run_tests():
        results.append(unittest.TextTestRunner(verbosity=2,stream=sys.stderr).run(unittest.TestSuite(selected)))
    # asyncio's main-thread Runner installs SIGINT handlers. Keep the absolute
    # signal fence and use one owned ordinary test thread instead.
    worker=threading.Thread(target=run_tests,name='market-historical-fixtures',daemon=True)
    worker.start();worker.join(150)
    assert not worker.is_alive() and len(results)==1
    result=results[0]
ok=result.wasSuccessful() and not result.skipped and result.testsRun==(1 if root_fixture else 84)
print(json.dumps({'schema':'market_security_candidate_tests/1','image_id':image,'test_sources_sha256':expected_proof,'status':'passed' if ok else 'failed','tests':result.testsRun,'skips':len(result.skipped),'selected_test_ids':selected_ids,'scope':'root_owned_metadata_fixture' if root_fixture else 'nonroot_product_tests','execution_uid':os.getuid(),'execution_gid':os.getgid(),'suite_execution_thread':'owned_nonmain_test_thread','public_source_directory_mode':'0755','product_files_equal_baseline':True,'runtime_data_used':False,'security_accepted':False},sort_keys=True))
raise SystemExit(0 if ok else 1)
'''.replace('TEST_NEW',repr(TEST_NEW)).replace('TEST_OLD',repr(TEST_OLD)).replace('__TEST_GUARD__',TEST_GUARD_SOURCE).replace('__PRODUCT_CHECK__',PRODUCT_CHECK_SOURCE)


def root_fixture_container(helpers,root,image,program):
    """One unchanged host-metadata unit fixture; no product service runs as root."""
    token=os.environ['GITHUB_RUN_ID']+'-'+os.environ['GITHUB_RUN_ATTEMPT']
    require(re.fullmatch('[0-9]+-[0-9]+',token),'ci_identity')
    name='security-market-root-fixture-'+token;cid=None
    argv=['create','--name',name,'--label','io.gold-trade.os-compose-owner='+token,'--user=0:0',
          '--network=none','--read-only','--cap-drop=ALL','--security-opt=no-new-privileges',
          '--memory=1g','--memory-swap=1g','--cpus=2','--pids-limit=128','--ulimit','core=0',
          '--tmpfs','/tmp:rw,nosuid,nodev,noexec,size=128m',
          '--mount','type=bind,source='+str(root/'payload/tests')+',target=/evidence,readonly',
          '--entrypoint','/usr/local/bin/python3',image,'-I','-B','-c',program,image]
    try:
        observed=helpers.docker(root,argv,maximum=256).decode().strip()
        require(re.fullmatch('[a-f0-9]{64}',observed),'fixture_container_identity');cid=observed
        output=helpers.docker(root,['start','--attach',cid],seconds=180,maximum=4*1024**2,
                              combine_stderr=True,log_path=root/'reports/market-root-fixture-run.log')
        require(helpers.docker(root,['inspect','--format','{{.State.ExitCode}}',cid],maximum=64).strip()==b'0','fixture_exit')
        return output
    finally:
        if cid is None:
            cleanup='not_confirmed'
            try:helpers.cleanup_isolated(root,name,name,token,image);cleanup='removed_owned_container'
            except Exception:pass
            try:write(root/'reports/root-fixture-daemon-outcome.json',encoded({'daemon_outcome':'UNKNOWN',
                'best_effort_cleanup':cleanup,'name':name,'image_id':image,'cancellation_proven':False,'security_accepted':False}))
            except OSError:pass
        else:helpers.cleanup_isolated(root,cid,name,token,image)


def validate_test_receipts(record,image,normal,fixture):
    expected=record['test_ids']
    require(type(expected) is list and len(expected)==len(set(expected))==85
            and expected==sorted(expected) and ROOT_FIXTURE_TEST_ID in expected,'historical_test_inventory')
    selections=[]
    for value,uid,count,scope in ((normal,10001,84,'nonroot_product_tests'),(fixture,0,1,'root_owned_metadata_fixture')):
        require(type(value) is dict and value.get('schema')=='market_security_candidate_tests/1'
                and value.get('image_id')==image and value.get('test_sources_sha256')==record['test_sources_sha256']
                and value.get('status')=='passed' and type(value.get('tests')) is int and value['tests']==count
                and value.get('suite_execution_thread')=='owned_nonmain_test_thread'
                and value.get('public_source_directory_mode')=='0755'
                and type(value.get('skips')) is int and value['skips']==0 and value.get('scope')==scope
                and type(value.get('execution_uid')) is int and value['execution_uid']==uid
                and type(value.get('execution_gid')) is int and value['execution_gid']==uid
                and value.get('product_files_equal_baseline') is True
                and all(value.get(field) is False for field in ('runtime_data_used','security_accepted')),'candidate_tests')
        ids=value.get('selected_test_ids')
        require(type(ids) is list and all(type(name) is str for name in ids) and ids==sorted(ids)
                and len(ids)==len(set(ids))==count,'candidate_test_selection')
        selections.append(set(ids))
    require(selections[1]=={ROOT_FIXTURE_TEST_ID} and not selections[0]&selections[1]
            and selections[0]|selections[1]==set(expected),'candidate_test_selection')
    return {'schema':'market_security_candidate_tests/1','image_id':image,'test_sources_sha256':record['test_sources_sha256'],
            'status':'passed','tests':85,'skips':0,'selected_test_ids':expected,'product_files_equal_baseline':True,
            'execution_profiles':{'nonroot':{'uid':10001,'gid':10001,'tests':84},
                                  'root_owned_metadata_fixture':{'uid':0,'gid':0,'tests':1,'test_id':ROOT_FIXTURE_TEST_ID}},
            'test_selections_disjoint':True,'historical_test_bodies_unchanged_except_security_base_assertion':True,
            'public_source_directory_mode':'0755',
            'runtime_data_used':False,'security_accepted':False}


def build_candidate(root, record):
    ci_admit(root);helpers=primitives();output=root/'payload';verify_context(output,record)
    require(os.statvfs(root).f_bavail*os.statvfs(root).f_frsize>=12*1024**3,'runner_space')
    iid=root/'candidate-image.id'
    args=['build','--file',str(output/'context/deploy/market-data/Dockerfile'),'--iidfile',str(iid),
          '--build-arg','SOURCE_SHA='+BASELINE,'--build-arg','SOURCE_TREE='+record['payload_tree'],
          '--build-arg','IMAGE_INPUT_SIGNATURE='+record['image_input_signature'],
          '--build-arg','SOURCE_DATE_EPOCH=0']
    for key,value in {'io.gold-trade.security.baseline.tree':BASELINE_TREE,
                      'io.gold-trade.security.baseline.image':BASELINE_IMAGE,
                      'io.gold-trade.security.overlay.revision':record['source_commit'],
                      'io.gold-trade.security.overlay.tree':record['source_tree']}.items():args+=['--label',key+'='+value]
    phase(root,'build_image')
    helpers.docker(root,[*args,str(output/'context')],seconds=2400,maximum=32*1024**2,
                   combine_stderr=True,log_path=root/'reports/build.log')
    phase(root,'inspect_candidate')
    image=read(iid,256).decode('ascii').strip();facts=image_metadata(helpers,root,image,record)
    verify_context(output,record)
    phase(root,'native_checks')
    raw=helpers.isolated(root,image,NATIVE_PROGRAM,'market-native',maximum=2*1024**2)
    native=json.loads(raw.splitlines()[-1])
    require(native.get('schema')=='market_security_native_checks/1' and native.get('image_id')==image
            and native.get('status')=='passed' and native.get('pydantic')=='2.8.2'
            and native.get('psycopg2')=='2.9.13' and native.get('runtime_data_used') is False
            and type(native.get('execution_uid')) is int and native['execution_uid']==10001
            and type(native.get('execution_gid')) is int and native['execution_gid']==10001
            and native.get('security_accepted') is False,'native_checks')
    write(root/'reports/native.json',encoded(native))
    program=candidate_test_program(record)
    phase(root,'historical_tests')
    raw=helpers.isolated(root,image,program,'market-focused',mounts=((output/'tests','/evidence'),),maximum=4*1024**2)
    normal=json.loads(raw.splitlines()[-1]);write(root/'reports/focused-nonroot.json',encoded(normal))
    phase(root,'root_metadata_fixture')
    raw=root_fixture_container(helpers,root,image,candidate_test_program(record,root_fixture=True))
    fixture=json.loads(raw.splitlines()[-1]);write(root/'reports/focused-root-fixture.json',encoded(fixture))
    focused=validate_test_receipts(record,image,normal,fixture)
    write(root/'reports/focused.json',encoded(focused))
    require(image_metadata(helpers,root,image,record)==facts,'candidate_image_drift')
    proof={'schema':'market_security_candidate_build/1',**facts,'payload_sha256':sha256(encoded(record)).hexdigest(),
           'test_sources_sha256':record['test_sources_sha256'],'native_sha256':sha256(encoded(native)).hexdigest(),
           'focused_sha256':sha256(encoded(focused)).hexdigest(),'product_files_equal_baseline':True,
           'public_source_directory_mode':'0755',
           'focused_nonroot_sha256':sha256(encoded(normal)).hexdigest(),'focused_root_fixture_sha256':sha256(encoded(fixture)).hexdigest(),
           'buildkit_resource_cgroup_asserted':False,'vendor_debs_reused':False,
           'full_runtime_matrix_completed':False,'security_accepted':False,'deployment_eligible':False}
    write(root/'reports/build-receipt.json',encoded(proof))
    write(root/'empty-trivy.yaml',b'{}\n')
    return image


PHASES=frozenset(('prepare_payload','build_image','inspect_candidate','native_checks','historical_tests',
                  'root_metadata_fixture','bind_build_evidence','bind_raw_reports','export_image','complete'))


def phase(root,name):
    require(name in PHASES,'phase_invalid')
    temporary=root/'reports/current-phase.pending'
    write(temporary,encoded({'phase':name,'security_accepted':False}))
    os.replace(temporary,root/'reports/current-phase.json')


def load_payload(root):
    record=parse_document(read(root/'payload/payload.json'))
    require(record['source_commit']==os.environ['GITHUB_SHA']
            and record['public_source_binding']['control_sha256']==E46_PUBLIC_FINGERPRINT_SHA256
            and all(record[field] is False for field in ('security_accepted','deployment_eligible',
                'production_services_changed','runtime_environment_read')),'payload_identity')
    verify_context(root/'payload',record)
    return record


def bind_reports(root,record,build,vulnerabilities,sbom):
    image=build['image_id']
    require(build['schema']=='market_security_candidate_build/1'
            and build['payload_sha256']==sha256(encoded(record)).hexdigest()
            and build['test_sources_sha256']==record['test_sources_sha256']
            and build['product_files_equal_baseline'] is True
            and build['public_source_directory_mode']=='0755'
            and all(build[field] is False for field in ('buildkit_resource_cgroup_asserted','vendor_debs_reused',
                'full_runtime_matrix_completed','security_accepted','deployment_eligible')),'build_receipt')
    for name,field in (('native.json','native_sha256'),('focused.json','focused_sha256'),
                       ('focused-nonroot.json','focused_nonroot_sha256'),('focused-root-fixture.json','focused_root_fixture_sha256')):
        require(sha256(read(root/'reports'/name,4*1024**2)).hexdigest()==build[field],'build_evidence_changed')
    require(vulnerabilities.get('SchemaVersion')==2 and vulnerabilities.get('ArtifactType')=='container_image'
            and vulnerabilities.get('Metadata',{}).get('ImageID')==image
            and vulnerabilities['Metadata'].get('ImageConfig',{}).get('rootfs',{}).get('diff_ids')==build['rootfs_layers']
            and type(vulnerabilities.get('Results')) is list and vulnerabilities['Results'],'raw_report_identity')
    component=sbom.get('metadata',{}).get('component',{})
    require(sbom.get('bomFormat')=='CycloneDX' and component.get('type')=='container' and component.get('name')==image
            and [row.get('value') for row in component.get('properties',[]) if row.get('name')=='aquasecurity:trivy:ImageID']==[image],
            'sbom_image_identity')
    counts={key:0 for key in ('UNKNOWN','LOW','MEDIUM','HIGH','CRITICAL')}
    for result in vulnerabilities['Results']:
        require(type(result) is dict and type(result.get('Class')) is str and type(result.get('Type')) is str,'raw_report_shape')
        rows=result.get('Vulnerabilities')
        require(rows is None or type(rows) is list,'raw_report_shape')
        for row in rows or []:
            require(type(row) is dict and row.get('Severity') in counts and type(row.get('VulnerabilityID')) is str
                    and type(row.get('PkgName')) is str,'raw_report_shape')
            counts[row['Severity']]+=1
    return counts


def finalize(root,record):
    ci_admit(root);helpers=primitives();phase(root,'bind_build_evidence')
    build_raw=read(root/'reports/build-receipt.json');build=parse_document(build_raw)
    require(image_metadata(helpers,root,build['image_id'],record)=={key:build[key] for key in
        ('image_id','platform','runtime_user','labels','rootfs_layers')},'candidate_image_drift')
    phase(root,'bind_raw_reports')
    reports={name:read(root/'reports'/name,32*1024**2) for name in ('vulnerabilities.json','sbom.cdx.json')}
    counts=bind_reports(root,record,build,*(json.loads(reports[name]) for name in ('vulnerabilities.json','sbom.cdx.json')))
    phase(root,'export_image')
    # The sealed helper's export primitive has no App constants or admission
    # calls: only this supplied immutable ID and a bounded normal save stream.
    archive=helpers.export_image(root,build['image_id'])
    verify_context(root/'payload',record)
    proof={'schema':'market_security_candidate_image_export/1','status':'exported_not_accepted',
           'image_id':build['image_id'],'baseline_image_id':BASELINE_IMAGE,'baseline_commit':BASELINE,
           'baseline_tree':BASELINE_TREE,'source_commit':record['source_commit'],'source_tree':record['source_tree'],
           'payload_tree':record['payload_tree'],'image_input_signature':record['image_input_signature'],
           'payload_sha256':sha256(encoded(record)).hexdigest(),'build_receipt_sha256':sha256(build_raw).hexdigest(),
           'public_source_binding':record['public_source_binding'],'test_sources_sha256':record['test_sources_sha256'],
           'native_sha256':build['native_sha256'],'focused_sha256':build['focused_sha256'],
           'focused_nonroot_sha256':build['focused_nonroot_sha256'],'focused_root_fixture_sha256':build['focused_root_fixture_sha256'],
           'raw_counts':counts,'reports_sha256':{name:sha256(raw).hexdigest() for name,raw in reports.items()},
           'archive':archive,'product_files_equal_baseline':True,'vendor_debs_reused':False,
           'public_source_directory_mode':'0755',
           'full_runtime_matrix_completed':False,'security_accepted':False,'findings_waived':False,
           'runtime_data_used':False,'deployment_eligible':False,'deployment_performed':False}
    write(root/'image-export.json',encoded(proof));phase(root,'complete')


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=('prepare','build','finalize','gate'))
    parser.add_argument('--root',type=Path,required=True);parser.add_argument('--repo',type=Path)
    args=parser.parse_args(argv)
    try:
        ci_admit(args.root)
        if args.command=='prepare':
            require(args.repo is not None,'source_repository');phase(args.root,'prepare_payload')
            prepare(args.repo,os.environ['GITHUB_SHA'],args.root/'payload')
        elif args.command=='build':
            image=build_candidate(args.root,load_payload(args.root))
            with Path(os.environ['GITHUB_OUTPUT']).open('a') as output:output.write('image_id='+image+'\n')
        elif args.command=='finalize':finalize(args.root,load_payload(args.root))
        else:
            proof=parse_document(read(args.root/'image-export.json'))
            require(proof['schema']=='market_security_candidate_image_export/1'
                    and proof['status']=='exported_not_accepted' and proof['security_accepted'] is False
                    and proof['findings_waived'] is False and proof['deployment_eligible'] is False,'export_receipt')
            record=load_payload(args.root);build_raw=read(args.root/'reports/build-receipt.json')
            build=parse_document(build_raw)
            reports={name:read(args.root/'reports'/name,32*1024**2) for name in ('vulnerabilities.json','sbom.cdx.json')}
            counts=bind_reports(args.root,record,build,*(json.loads(reports[name]) for name in ('vulnerabilities.json','sbom.cdx.json')))
            require(proof['build_receipt_sha256']==sha256(build_raw).hexdigest()
                    and proof['image_id']==build['image_id'] and proof['payload_sha256']==sha256(encoded(record)).hexdigest()
                    and proof['reports_sha256']=={name:sha256(raw).hexdigest() for name,raw in reports.items()}
                    and type(proof['raw_counts']) is dict and set(proof['raw_counts'])==set(counts)
                    and all(type(n) is int and n>=0 for n in proof['raw_counts'].values())
                    and proof['raw_counts']==counts,'export_receipt')
            require(counts['CRITICAL']==counts['HIGH']==0,'raw_high_critical_findings')
        return 0
    except Exception as error:
        # Static phase and reason only; command output/Config and arbitrary
        # exception messages never enter host-console diagnostics.
        reason=str(error) if type(error) is PayloadError else 'market_candidate_execution_failed'
        print(json.dumps({'status':'failed','code':reason,'security_accepted':False,'deployment_performed':False}),file=sys.stderr)
        return 1


if __name__=='__main__':raise SystemExit(main())
