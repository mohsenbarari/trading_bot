"""Exact closed role source probe; no business imports or live data."""
import hashlib

import json

import os

from pathlib import Path

import stat

import sys

import time


# Closed published source expectations, never runtime-identity substitutes.
from hashlib import sha256 as _role_sha256
ROLE_CATALOG_PATH = 'deploy/market-data/role-business-components.json'
ROLE_CATALOG_SHA = '33c092cafd2cd301bd1f7b92a73aeca7c685ea189ddc44e8f98bd8d7c09c2831'
ROLE_MAP = {'native/coin-estimator': {'business_commit': 'f4871edbe507546a0a6544c56bc7d6b34bf770a6', 'business_tree': '9b049e5031a33f416b6c6a2a45d89c3ba334fc3e', 'bytes': 1928470, 'component': 'market-role-sources-20261027-fc622107fadf1de9-v1', 'count': 94, 'manifest_sha256': '6ddbb7459a9b69c20accb2e277c8e25c701b95958b8219cfd38a08e998f1b1ca', 'rows_sha256': 'fc622107fadf1de9adec789425c1d3816271829e1310b75347a524c5c104bbcd'}, 'native/estimator-snapshot-sender': {'business_commit': '6c7bfb3fe3714f1b9ecb375e68bbb0b1263ef328', 'business_tree': 'bc90e1224cda5bd2c58be81ea2aa12d1c6fbcadd', 'bytes': 1848102, 'component': 'market-role-sources-20261027-971dc9e57b1a4220-v1', 'count': 93, 'manifest_sha256': '3ce2f0f0b6ed975c2665f68ca0dc53198b9c00c9673fde3979c094c4ae05ec5c', 'rows_sha256': '971dc9e57b1a4220b3604af0ed101377a62ae7b9f6510340b0cc9c6b9a3cb33f'}, 'native/market-fact-receiver': {'business_commit': 'c9f8ada468c94af99a30ede8cdcbd9a220e9b917', 'business_tree': '08d9d82693f8a42a49da89067c4def1862ce1e83', 'bytes': 1934716, 'component': 'market-role-sources-20261027-83c61098e5581bcb-v1', 'count': 95, 'manifest_sha256': '384995da7738128ca902737e7324cd8fc1abff5a581047e41533b06c14683f16', 'rows_sha256': '83c61098e5581bcb9795859444df9730888c46cac911046f293a8056ef3bc3c1'}, 'native/market-store-adapter': {'business_commit': '6cad2c7e4a4b4622ec3610ddb91cce32cd04f2c0', 'business_tree': 'c3a25a925927802f413dc1eda59ca87b3af3e6b7', 'bytes': 1937533, 'component': 'market-role-sources-20261027-0c440e791bde7779-v1', 'count': 95, 'manifest_sha256': '88a982cf6c6da9f736fa11708be694a444ee7d6b3ffcfaea81b43e1c6c52f133', 'rows_sha256': '0c440e791bde77790179ef65b30b9f5abda5434287c09bf87648b61f19011157'}, 'web/estimator-snapshot-receiver': {'business_commit': '89b64c47c952c6f6a6ba75df2b2f40d0208de196', 'business_tree': '9ba0c52b9a9d3a1cbdb01d0417eaa9891b2585e2', 'bytes': 1934784, 'component': 'market-role-sources-20261027-45ccb288a62edc6a-v1', 'count': 95, 'manifest_sha256': 'bacda97ab7e51af550207a9930977be8e12d37d28ed5354d60cc3c078decedc9', 'rows_sha256': '45ccb288a62edc6a5f08042ca33060ce81ca45e34a2a2bf4e18b55f2de73a884'}, 'web/market-capture-account1': {'business_commit': '4ee3fd21003ebb3038a2e4b572ca2eb01cb45d2f', 'business_tree': 'c63f575f2a67287076a2188a72494ee236b2109c', 'bytes': 1896295, 'component': 'market-role-sources-20261027-12cac77ad8481fa6-v1', 'count': 93, 'manifest_sha256': '1693511310372cb9e8906141bfcb0818d19cd32ed4ca5066acf61d8719013394', 'rows_sha256': '12cac77ad8481fa6d11f7885c97ab4e8593290ed70cf388a7b15e86c4139dbad'}, 'web/market-capture-external': {'business_commit': '6c7bfb3fe3714f1b9ecb375e68bbb0b1263ef328', 'business_tree': 'bc90e1224cda5bd2c58be81ea2aa12d1c6fbcadd', 'bytes': 1848102, 'component': 'market-role-sources-20261027-971dc9e57b1a4220-v1', 'count': 93, 'manifest_sha256': '3ce2f0f0b6ed975c2665f68ca0dc53198b9c00c9673fde3979c094c4ae05ec5c', 'rows_sha256': '971dc9e57b1a4220b3604af0ed101377a62ae7b9f6510340b0cc9c6b9a3cb33f'}, 'web/market-fact-sync-worker': {'business_commit': '064451c17c9f2f1b72f3f33fbe68f15fc1384f60', 'business_tree': '7f7f209b17b06984b2ac2fa989177277ed876b24', 'bytes': 1935251, 'component': 'market-role-sources-20261027-4a848dcf6a46d6f5-v1', 'count': 95, 'manifest_sha256': '83a42c0507c28c40574538c61382034a6f376001cf5cb76a1aed97148416de36', 'rows_sha256': '4a848dcf6a46d6f5a0f0e2784f537f6deb2cd6722fcb6ae580a12dfdc7cbf53d'}, 'web/market-processor': {'business_commit': 'db0f8eba339b830ab7ae5fb1c810f149992458b3', 'business_tree': 'e956bcc0ae619fd7495bb4ba41d8a1aeaa7885c1', 'bytes': 1946368, 'component': 'market-role-sources-20261027-5cc7196abb5cfafe-v1', 'count': 95, 'manifest_sha256': 'a5b587cc382563735bb66df783e8def505d12509a9133a08a2537e562cbaf2c9', 'rows_sha256': '5cc7196abb5cfafeb5ca00aab2d34579c94d493c933768253bfba7a34d995c30'}}

def _role_encoded(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode()

def role_data(raw, role):
    need(type(role) is str and role in ROLE_MAP, 'role_component')
    need(type(raw) is bytes and len(raw) == 179771 and
         _role_sha256(raw).hexdigest() == ROLE_CATALOG_SHA, 'role_catalog')
    value = json.loads(raw)
    spec = ROLE_MAP[role]
    manifest = value['components'][spec['rows_sha256']]
    need(value['schema'] == 'normal_market_published_role_components/1' and
         _role_sha256(_role_encoded(manifest)).hexdigest() == spec['manifest_sha256'] and
         role in manifest['roles'] and manifest['business_commit'] == spec['business_commit'] and
         manifest['business_tree'] == spec['business_tree'] and
         manifest['source_files'] == spec['count'] and manifest['source_bytes'] == spec['bytes'] and
         _role_sha256(_role_encoded(manifest['product_files'])).hexdigest() == spec['rows_sha256'] and
         all(manifest[key] is False for key in ('already_running_code_proven',
             'complete_effective_business_inventory_proven', 'full_outer_business_proven',
             'activation_authority', 'security_accepted')), 'role_manifest')
    return manifest

def receipt_role(receipt):
    need(type(receipt) is dict and receipt.get('schema') == 'normal_market_git_component_inputs/2'
         and type(receipt.get('role')) is str and receipt['role'] in ROLE_MAP, 'role_receipt')
    spec = ROLE_MAP[receipt['role']]
    locked = {'business_commit': spec['business_commit'], 'business_tree': spec['business_tree'],
              'repository_identity': {'host': 'github.com', 'repository': 'mohsenbarari/trading_bot'},
              'role': receipt['role'], 'catalog_sha256': ROLE_CATALOG_SHA,
              'source_policy': 'whole_git_component_no_main_fallback'}
    need(receipt.get('role_source') == spec and receipt.get('role_catalog_sha256') == ROLE_CATALOG_SHA and
         receipt.get('repository_identity') == locked['repository_identity'] and
         receipt.get('component') == spec['component'] and
         receipt.get('business_commit') == spec['business_commit'] and
         receipt.get('business_tree') == spec['business_tree'] and
         receipt.get('business_manifest_sha256') == spec['manifest_sha256'] and
         receipt.get('product_lock_sha256') == _role_sha256(_role_encoded(locked)).hexdigest() and
         type(receipt.get('product_files')) is list and len(receipt['product_files']) == spec['count'] and
         _role_sha256(_role_encoded(receipt['product_files'])).hexdigest() == spec['rows_sha256'],
         'role_receipt')
    return dict(spec)

def candidate_spec(value):
    need(type(value) is dict and type(value.get('role')) is str and value['role'] in ROLE_MAP,
         'role_candidate')
    role = value['role']
    need(value == {'role': role, 'role_source': ROLE_MAP[role],
                   'catalog_sha256': ROLE_CATALOG_SHA}, 'role_candidate')
    return dict(ROLE_MAP[role])


SCOPES = ('core/market_intelligence', 'contracts/market_data', 'deploy/market-data/migrations')

SINGLES = frozenset(('core/__init__.py', 'config/market_data_sources.v1.json'))

CODES = frozenset(('flags', 'manifest', 'path', 'parent', 'metadata', 'identity', 'inventory',
                   'bound', 'content', 'deadline', 'final', 'unclassified'))

class Refusal(RuntimeError):
    pass

def need(value, code):
    if not value:
        raise Refusal(code)

def ident(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
            info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)

def encoded(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode()

def _inspect(root, rows, owner):
    """The fixed main calls /app/ROWS/10001; owned fixtures use actual owners."""
    root = Path(root)
    expected = {row['path']: row for row in rows}
    need(len(expected) == len(rows) and root.is_absolute() and root.resolve(strict=True) == root, 'path')
    end = time.monotonic() + 30
    directories = {}
    files = []
    cache = []
    found = set()
    scanned = set()
    ancestor = []
    entries_seen = 0
    primary = None
    for parent in root.parents:
        value = ident(parent.lstat())
        need(parent.resolve(strict=True) == parent and stat.S_ISDIR(value[2]), 'parent')
        ancestor.append((parent, value))

    def check(closed=False):
        need(time.monotonic() < end, 'deadline')
        need(all(path.resolve(strict=True) == path and ident(path.lstat()) == value for path, value in ancestor), 'parent')
        for path, fd, value in directories.values():
            need(path.resolve(strict=True) == path and ident(path.lstat()) == value, 'identity')
            if not closed:
                need(ident(os.fstat(fd)) == value, 'identity')
        for path, fd, value in files:
            need(path.resolve(strict=True) == path and ident(path.lstat()) == value, 'identity')
            if not closed:
                need(ident(os.fstat(fd)) == value, 'identity')
        for path, value in cache:
            need(path.resolve(strict=True) == path and ident(path.lstat()) == value, 'identity')

    def directory(name):
        need(time.monotonic() < end and len(directories) < 128, 'bound')
        if name in directories:
            return directories[name][1]
        if name:
            parent, _, leaf = name.rpartition('/')
            parent_fd = directory(parent)
            before = ident(os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False))
            need(stat.S_ISDIR(before[2]) and before[3:5] == owner and not before[2] & 0o022, 'metadata')
            fd = os.open(leaf, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent_fd)
        else:
            before = ident(root.lstat())
            need(stat.S_ISDIR(before[2]) and before[3:5] == owner and not before[2] & 0o022, 'metadata')
            fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        path = root / name
        try:
            need(ident(os.fstat(fd)) == before == ident(path.lstat()) and path.resolve(strict=True) == path, 'identity')
        except BaseException:
            os.close(fd)
            raise
        directories[name] = (path, fd, before)
        return fd

    def source(name):
        row = expected[name]
        parent, _, leaf = name.rpartition('/')
        parent_fd = directory(parent)
        before = ident(os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False))
        need(before[2:7] == (int(row['mode'], 8), *owner, 1, row['bytes']), 'metadata')
        fd = os.open(leaf, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=parent_fd)
        files.append((root / name, fd, before))
        check()
        digest = hashlib.sha256()
        count = 0
        while True:
            need(time.monotonic() < end, 'deadline')
            part = os.read(fd, min(65536, row['bytes'] - count + 1))
            if not part:
                break
            count += len(part)
            need(count <= row['bytes'], 'bound')
            digest.update(part)
        need(count == row['bytes'] and digest.hexdigest() == row['sha256'], 'content')
        check()
        found.add(name)

    def walk(name, in_cache=False):
        nonlocal entries_seen
        fd = directory(name)
        with os.scandir(fd) as iterator:
            children = []
            for item in iterator:
                entries_seen += 1
                need(entries_seen <= 1024 and len(item.name) <= 255 and item.name not in ('.', '..'), 'bound')
                children.append(item.name)
        scanned.add(name)
        for leaf in sorted(children):
            child = name + '/' + leaf
            info = ident(os.stat(leaf, dir_fd=fd, follow_symlinks=False))
            if child in expected and not in_cache:
                source(child)
            elif stat.S_ISDIR(info[2]):
                need(not in_cache and (leaf == '__pycache__' or any(path.startswith(child + '/') for path in expected)), 'inventory')
                walk(child, leaf == '__pycache__')
            else:
                need(leaf.endswith('.pyc') and stat.S_ISREG(info[2]) and info[2:6] == (0o100644, *owner, 1)
                     and 0 < info[6] <= 4 * 1024**2 and len(cache) < 512, 'inventory')
                # Cache metadata only: O_PATH, fstat, close. Never read/pread it.
                handle = os.open(leaf, os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
                try:
                    need(ident(os.fstat(handle)) == info == ident((root / child).lstat()), 'identity')
                finally:
                    os.close(handle)
                cache.append((root / child, info))
        check()

    try:
        directory('')
        for name in sorted(SINGLES):
            need(name in expected, 'manifest')
            source(name)
        for scope in SCOPES:
            walk(scope)
        need(found == set(expected), 'inventory')
        # Re-list each admitted directory only; no unknown child is traversed.
        for name in scanned:
            fd = directories[name][1]
            known = {path[len(name) + 1:].split('/', 1)[0] for path in expected if path.startswith(name + '/')}
            known |= {str(path.relative_to(root))[len(name) + 1:].split('/', 1)[0] for path, _ in cache if str(path.relative_to(root)).startswith(name + '/')}
            known |= {child[len(name) + 1:].split('/', 1)[0] for child in directories if child.startswith(name + '/')}
            with os.scandir(fd) as iterator:
                observed = []
                for item in iterator:
                    need(len(observed) < 1024, 'bound')
                    observed.append(item.name)
            need(set(observed) == known, 'inventory')
        check()
    except BaseException as error:
        primary = error
    finally:
        errors = []
        try:
            check()
        except BaseException as error:
            errors.append(error)
        for _, fd, _ in reversed(files):
            try:
                os.close(fd)
            except BaseException as error:
                errors.append(error)
        for _, fd, _ in reversed(list(directories.values())):
            try:
                os.close(fd)
            except BaseException as error:
                errors.append(error)
        try:
            check(closed=True)
        except BaseException as error:
            errors.append(error)
        if primary is not None:
            raise primary
        if errors:
            raise Refusal('final') from None
    return {'source_files': len(found), 'excluded_cache_metadata_files': len(cache)}


def observe_role(role):
    need(os.getuid() == os.geteuid() == os.getgid() == os.getegid() == 10001 and
         sys.flags.isolated == sys.flags.no_site == sys.flags.dont_write_bytecode == 1,
         'flags')
    path = Path('/proof/role-catalog.json')
    parents = []
    fd = None
    before = None
    primary = None
    result = None
    def names():
        need(path.resolve(strict=True) == path and ident(path.lstat()) == before,
             'role_catalog')
        for parent, _, prior in parents:
            need(parent.resolve(strict=True) == parent and ident(parent.lstat())[:5] == prior,
                 'parent')
    def check():
        names()
        need(ident(os.fstat(fd)) == before, 'role_catalog')
        for _, handle, prior in parents:
            need(ident(os.fstat(handle))[:5] == prior, 'parent')
    try:
        for parent in path.parents:
            prior = ident(parent.lstat())[:5]
            need(parent.resolve(strict=True) == parent and stat.S_ISDIR(prior[2])
                 and prior[3] == 0 and not prior[2] & 0o022, 'parent')
            handle = os.open(parent, os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
            parents.append((parent, handle, prior))
            need(ident(os.fstat(handle))[:5] == prior, 'parent')
        before = ident(path.lstat())
        need(stat.S_ISREG(before[2]) and stat.S_IMODE(before[2]) in (0o444, 0o644)
             and before[3] in (0, 1001) and before[5] == 1 and 0 < before[6] <= 1048576,
             'role_catalog')
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        check()
        raw = os.pread(fd, before[6] + 1, 0)
        check()
        manifest = role_data(raw, role)
        result = _inspect(Path('/app'), manifest['product_files'], (10001, 10001))
        check()
        result = {'schema': 'market_role_public_source_readonly_observation/1',
                  'status': 'match', 'role': role,
                  'business_commit': manifest['business_commit'],
                  'business_tree': manifest['business_tree'],
                  'expected_manifest_sha256': ROLE_MAP[role]['manifest_sha256'],
                  'expected_rows_sha256': ROLE_MAP[role]['rows_sha256'],
                  'role_catalog_sha256': ROLE_CATALOG_SHA,
                  **result, 'all_source_rows_match': True,
                  'project_cache_bodies_read': False,
                  'unknown_noncache_files_admitted': False,
                  'project_imported': False, 'source_only_nonatomic_observation': True,
                  'already_running_code_proven': False,
                  'business_data_freshness_proven': False, 'security_accepted': False}
    except BaseException as error:
        primary = error
    finally:
        errors = []
        if fd is not None:
            try:
                check()
            except BaseException as error:
                errors.append(error)
            finally:
                os.close(fd)
            try:
                names()
            except BaseException as error:
                errors.append(error)
        for _, handle, _ in reversed(parents):
            try:
                os.close(handle)
            except BaseException as error:
                errors.append(error)
        if before is not None:
            try:
                names()
            except BaseException as error:
                errors.append(error)
        if primary is not None:
            raise primary
        if errors:
            raise Refusal('final') from None
    return result


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--role', choices=tuple(ROLE_MAP))
    args = parser.parse_args(argv)
    if args.role is None:
        return {'status': 'UNARMED', 'security_accepted': False}
    return observe_role(args.role)


if __name__ == '__main__':
    try:
        value = main()
        code = 0
    except BaseException as error:
        allowed = CODES | {'role_component', 'role_catalog', 'role_manifest', 'role_receipt'}
        reason = error.args[0] if type(error) is Refusal and len(error.args) == 1 and error.args[0] in allowed else 'unclassified'
        value = {'schema': 'market_role_public_source_readonly_observation/1',
                 'status': 'refused', 'code': reason,
                 'all_source_rows_match': False, 'security_accepted': False}
        code = 2
    print(json.dumps(value, sort_keys=True))
    raise SystemExit(code)
