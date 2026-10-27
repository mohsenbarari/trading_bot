"""Whole, locked Git business component for a normal Main Market release.

No network, image, serving-control or deployment entry point is provided.
The supplied lock is intentionally unarmed until its real Git publication.
"""
from contextlib import ExitStack
from hashlib import sha1, sha256
from pathlib import Path, PurePosixPath
import argparse
import io
import json
import os
import re
import resource
import stat
import subprocess
import sys
import tarfile
import tempfile
import time

SCHEMA = 'normal_market_business_source_lock/1'
COMPONENT = 'capture2-f79-accepted-20261026'
ROLE = 'market-capture-account2'
BASELINE = 'sha256:f79cdda21c192c52536f4ccfecadc7f724453a9ea4abfae96b1efe540ebbc61b'
MANIFEST_SHA = 'fef6305b1d7e6db851d1d0b77f3aa9910aad535e83f64f012790165a3d62c81b'
ROWS_SHA = '75d7d2cfb2c20fd036501e7aac198cb5567fb82c53301fb3972f8c81b935e57f'
LOCK_PATH = 'deploy/market-data/business-source.lock.json'
MANIFEST_PATH = 'deploy/market-data/accepted-f79-business.manifest.json'
SELF_PATH = 'scripts/prepare_market_product_source.py'
COPY_FILES = frozenset(('core/__init__.py', 'config/market_data_sources.v1.json'))
COPY_ROOTS = ('core/market_intelligence', 'contracts/market_data', 'deploy/market-data/migrations')
RELEASE_FILES = (
    'deploy/market-data/Dockerfile.capture2', 'deploy/market-data/requirements.lock',
    'deploy/production/pip-bootstrap-requirements.txt',
    'scripts/verify_psycopg2_runtime.py', 'scripts/verify_sqlite_runtime.py',
    'scripts/prepare_app_security_os_cache.py', 'scripts/app_security_os_packages.lock.json',
    'scripts/compose_security_os_candidate.py', 'scripts/prepare_sqlite_wal_os_cache.py',
    'scripts/sqlite_wal_package.lock.json', 'scripts/prepare_tar_acl_os_cache.py',
    'scripts/tar_acl_package.lock.json', 'scripts/remove_runtime_pip.py',
    'scripts/verify_image_trust.py', SELF_PATH, LOCK_PATH, MANIFEST_PATH)
PRODUCT_COPY_LINES = frozenset((
    'COPY --from=business-source core/__init__.py /rootfs/app/core/__init__.py',
    'COPY --from=business-source core/market_intelligence /rootfs/app/core/market_intelligence',
    'COPY --from=business-source config/market_data_sources.v1.json /rootfs/app/config/market_data_sources.v1.json',
    'COPY --from=business-source contracts/market_data /rootfs/app/contracts/market_data',
    'COPY --from=business-source deploy/market-data/migrations /rootfs/app/deploy/market-data/migrations'))
RELEASE_COPY_LINES = frozenset((
    'COPY scripts/prepare_app_security_os_cache.py scripts/app_security_os_packages.lock.json scripts/compose_security_os_candidate.py /usr/local/libexec/',
    'COPY scripts/prepare_sqlite_wal_os_cache.py scripts/sqlite_wal_package.lock.json /usr/local/libexec/',
    'COPY scripts/prepare_tar_acl_os_cache.py scripts/tar_acl_package.lock.json /usr/local/libexec/',
    'COPY scripts/verify_image_trust.py /usr/local/libexec/verify_image_trust.py',
    'COPY scripts/verify_psycopg2_runtime.py /usr/local/libexec/verify_psycopg2_runtime.py',
    'COPY scripts/verify_sqlite_runtime.py /usr/local/libexec/verify_sqlite_runtime.py',
    'COPY scripts/remove_runtime_pip.py /usr/local/libexec/remove_runtime_pip.py',
    'COPY deploy/production/pip-bootstrap-requirements.txt /tmp/pip-bootstrap-requirements.txt',
    'COPY deploy/market-data/requirements.lock ./requirements.lock',
    'COPY --from=assembly /rootfs/ /'))
FROM_LINES = frozenset(('FROM ${PYTHON_BASE_IMAGE} AS patched-base',
                        'FROM patched-base AS assembly', 'FROM patched-base AS runtime'))
MAX_FILE = 4 * 1024**2
MAX_TOTAL = 64 * 1024**2
MAX_TREE = 8 * 1024**2


class Refusal(RuntimeError):
    pass


REFUSAL_CODES = frozenset(('accepted_inventory', 'accepted_manifest_identity', 'accepted_manifest_schema',
    'arguments', 'build_contract', 'clean_main_required', 'control_archive_bound', 'control_product_identity',
    'control_product_inventory', 'dockerfile_bound', 'dockerfile_context_shadow', 'dockerfile_copy_contract',
    'dockerfile_syntax', 'duplicate_key', 'git_blob', 'git_blob_identity', 'git_command', 'git_commit',
    'git_file_type', 'git_output_bound', 'git_readonly_command', 'git_timeout', 'git_tree', 'git_tree_bound',
    'input_receipt', 'json_bound', 'json_constant', 'json_invalid', 'lock_schema', 'lock_scope',
    'output_directory', 'output_directory_changed', 'output_exists', 'output_file_changed', 'output_git_path',
    'output_inventory', 'output_operand', 'output_parent', 'output_parent_changed', 'output_readback',
    'output_write', 'phase', 'product_file_mismatch', 'product_git_identity', 'product_inventory',
    'product_lock_unarmed', 'public_source_bound', 'public_source_changed', 'public_source_metadata',
    'release_changed', 'release_commit', 'release_input_empty', 'release_inputs_missing',
    'repository_identity', 'repository_path', 'sealer_source_identity', 'signature_binding',
    'source_deadline', 'source_parent', 'source_total_bound'))
PHASES = frozenset(('bootstrap', 'source', 'release', 'product', 'output', 'control_component', 'final'))
PHASE = 'bootstrap'


def need(ok, code):
    if not ok:
        raise Refusal(code)


def phase(name):
    global PHASE
    need(name in PHASES, 'phase')
    PHASE = name


def closed_refusal(error):
    reason = (error.args[0] if type(error) is Refusal and len(error.args) == 1
              and type(error.args[0]) is str and error.args[0] in REFUSAL_CODES else 'unclassified_source_refusal')
    classes = {Refusal: 'Refusal', OSError: 'OSError', FileNotFoundError: 'FileNotFoundError',
               PermissionError: 'PermissionError', ValueError: 'ValueError', RuntimeError: 'RuntimeError',
               TypeError: 'TypeError', UnicodeDecodeError: 'UnicodeDecodeError'}
    return {'schema': 'normal_market_source_refusal/1', 'status': 'refused',
            'phase': PHASE if PHASE in PHASES else 'bootstrap', 'reason_code': reason,
            'exception_type': classes.get(type(error), 'other'),
            'build_performed': False, 'activation_authority': False}


def encoded(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode()


def document(raw, maximum=MAX_FILE):
    need(type(raw) is bytes and 0 < len(raw) <= maximum, 'json_bound')
    def pairs(items):
        result = {}
        for name, value in items:
            need(name not in result, 'duplicate_key')
            result[name] = value
        return result
    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_constant=lambda _: need(False, 'json_constant'))
    except (ValueError, UnicodeError) as error:
        raise Refusal('json_invalid') from error


def hex40(value):
    return type(value) is str and re.fullmatch('[a-f0-9]{40}', value) is not None


def hex64(value):
    return type(value) is str and re.fullmatch('[a-f0-9]{64}', value) is not None


def path_ok(name):
    return type(name) is str and 0 < len(name) <= 1024 and not name.startswith('/') and '\\' not in name and '\x00' not in name and all(part not in ('', '.', '..') for part in name.split('/'))


def copied(name):
    return path_ok(name) and (name in COPY_FILES or any(name.startswith(root + '/') for root in COPY_ROOTS))


def git_blob(raw):
    return sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()


def origin_identity(raw):
    need(type(raw) is str and len(raw) <= 512, 'repository_identity')
    match = re.fullmatch(r'(?:https://github\.com/|git@github\.com:)([A-Za-z0-9_-]+/[A-Za-z0-9_.-]+?)(?:\.git)?', raw)
    need(match is not None, 'repository_identity')
    repository = match.group(1)
    need(not repository.split('/')[1].startswith('.') and '..' not in repository.split('/'), 'repository_identity')
    return {'host': 'github.com', 'repository': repository.lower()}


def template():
    return {'schema': SCHEMA, 'component': COMPONENT, 'role': ROLE,
            'baseline_image_id': BASELINE, 'accepted_manifest_sha256': MANIFEST_SHA,
            'business_commit': None, 'business_tree': None, 'repository_identity': None,
            'source_policy': 'whole_git_component_no_main_fallback'}


def lock_document(raw):
    value = document(raw, 16384)
    fixed = template()
    need(type(value) is dict and set(value) == set(fixed), 'lock_schema')
    for name in ('schema', 'component', 'role', 'baseline_image_id', 'accepted_manifest_sha256', 'source_policy'):
        need(value[name] == fixed[name] and type(value[name]) is str, 'lock_scope')
    need(hex40(value['business_commit']) and hex40(value['business_tree']), 'product_lock_unarmed')
    origin = value['repository_identity']
    need(type(origin) is dict and set(origin) == {'host', 'repository'} and origin['host'] == 'github.com'
         and type(origin['repository']) is str and origin_identity('https://github.com/' + origin['repository']) == origin, 'repository_identity')
    return value


def manifest_document(raw):
    need(type(raw) is bytes and sha256(raw).hexdigest() == MANIFEST_SHA, 'accepted_manifest_identity')
    value = document(raw)
    need(value['schema'] == 'normal_market_accepted_business_baseline/1' and value['baseline_image_id'] == BASELINE
         and value['product_count'] == 93 and value['product_bytes'] == 1851697, 'accepted_manifest_schema')
    rows = value['product_files']
    need(type(rows) is list and len(rows) == 93 and rows == sorted(rows, key=lambda row: row['path']), 'accepted_inventory')
    need(sha256(encoded(rows)).hexdigest() == ROWS_SHA, 'accepted_inventory')
    seen = set()
    for row in rows:
        need(type(row) is dict and set(row) == {'path', 'mode', 'git_blob', 'bytes', 'sha256'} and copied(row['path'])
             and row['path'] not in seen and row['mode'] in {'100644', '100755'} and hex40(row['git_blob'])
             and type(row['bytes']) is int and 0 <= row['bytes'] <= MAX_FILE and hex64(row['sha256']), 'accepted_inventory')
        seen.add(row['path'])
    return rows


def tree_entries(raw):
    need(type(raw) is bytes and 0 < len(raw) <= MAX_TREE and raw.endswith(b'\0'), 'git_tree_bound')
    result = {}
    for item in raw[:-1].split(b'\0'):
        try:
            header, name = item.split(b'\t', 1)
            mode, kind, oid = header.decode('ascii').split(' ')
            name = name.decode('utf-8')
        except (ValueError, UnicodeError) as error:
            raise Refusal('git_tree') from error
        need(path_ok(name) and name not in result and hex40(oid), 'git_tree')
        result[name] = {'mode': mode, 'kind': kind, 'oid': oid}
    return result


def identity(info):
    return [info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
            info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns]


class PublicFile:
    def __init__(self, path, stack, maximum=MAX_FILE, *, allow_empty=False):
        self.path = Path(path)
        self.parents = []
        for parent in self.path.parents:
            info = parent.lstat()
            need(parent.resolve(strict=True) == parent and stat.S_ISDIR(info.st_mode), 'source_parent')
            self.parents.append((parent, identity(info)[:5]))
        self.before = identity(self.path.lstat())
        need(self.path.resolve(strict=True) == self.path and self.before[2] in (0o100600, 0o100644, 0o100755)
             and self.before[3] == os.geteuid() and self.before[4] == os.getegid() and self.before[5] == 1
             and (0 if allow_empty else 1) <= self.before[6] <= maximum, 'public_source_metadata')
        self.fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        stack.callback(os.close, self.fd)
        self.check()
        self.raw = os.pread(self.fd, self.before[6] + 1, 0)
        need(len(self.raw) == self.before[6], 'public_source_bound')
        self.check()
        stack.callback(self.check)

    def check(self, closed=False):
        need(self.path.resolve(strict=True) == self.path and identity(self.path.lstat()) == self.before
             and all(path.resolve(strict=True) == path and identity(path.lstat())[:5] == before for path, before in self.parents), 'public_source_changed')
        if not closed:
            need(identity(os.fstat(self.fd)) == self.before, 'public_source_changed')


class GitRepository:
    """Fixed local Git reads only. Existing live-origin Main gate stays external."""
    def __init__(self, path):
        self.path = Path(path)
        need(self.path.is_absolute() and self.path.resolve(strict=True) == self.path and self.path.is_dir(), 'repository_path')
        self.end = time.monotonic() + 180

    def git(self, *args, maximum=MAX_TREE):
        need(type(maximum) is int and 0 < maximum <= MAX_TREE, 'git_output_bound')
        need(time.monotonic() < self.end, 'source_deadline')
        fixed = {('rev-parse', '--verify', 'HEAD^{commit}'), ('rev-parse', '--verify', 'HEAD^{tree}'),
                 ('rev-parse', '--verify', 'refs/remotes/origin/main^{commit}'),
                 ('symbolic-ref', '--short', 'HEAD'), ('config', '--local', '--get', 'remote.origin.url'),
                 ('rev-parse', '--show-toplevel'), ('status', '--porcelain', '--untracked-files=all')}
        allowed = args in fixed
        allowed = allowed or (len(args) == 3 and args[:2] == ('rev-parse', '--verify')
                              and re.fullmatch(r'[a-f0-9]{40}\^\{(?:commit|tree)\}', args[2]) is not None)
        allowed = allowed or (len(args) == 5 and args[:4] == ('ls-tree', '-r', '-z', '--full-tree') and hex40(args[4]))
        allowed = allowed or (len(args) == 3 and args[:2] == ('cat-file', 'blob') and hex40(args[2]))
        need(allowed, 'git_readonly_command')
        def limits():
            resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
            resource.setrlimit(resource.RLIMIT_FSIZE, (maximum + 1, maximum + 1))
        env = {'PATH': '/usr/bin:/bin', 'LC_ALL': 'C.UTF-8', 'HOME': '/nonexistent',
               'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_TERMINAL_PROMPT': '0',
               'GIT_NO_REPLACE_OBJECTS': '1', 'GIT_ALLOW_PROTOCOL': '', 'GIT_NO_LAZY_FETCH': '1'}
        argv = ['/usr/bin/git', '--no-optional-locks', '-c', 'core.fsmonitor=false', '-c', 'core.hooksPath=/dev/null', '-C', str(self.path), *args]
        with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
            try:
                result = subprocess.run(argv, env=env, stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                                        timeout=min(30, max(.001, self.end - time.monotonic())), check=False, preexec_fn=limits)
            except subprocess.TimeoutExpired as error:
                raise Refusal('git_timeout') from error
            need(result.returncode == 0 and os.fstat(out.fileno()).st_size <= maximum and os.fstat(err.fileno()).st_size <= maximum, 'git_command')
            need(time.monotonic() < self.end, 'source_deadline')
            out.seek(0)
            return out.read(maximum + 1)

    def snapshot(self):
        def text(*args):
            return self.git(*args, maximum=4096).decode('utf-8').strip()
        return {'head': text('rev-parse', '--verify', 'HEAD^{commit}'),
                'tree': text('rev-parse', '--verify', 'HEAD^{tree}'),
                'branch': text('symbolic-ref', '--short', 'HEAD'),
                'origin_main': text('rev-parse', '--verify', 'refs/remotes/origin/main^{commit}'),
                'origin': text('config', '--local', '--get', 'remote.origin.url'),
                'top': text('rev-parse', '--show-toplevel'),
                'dirty': bool(self.git('status', '--porcelain', '--untracked-files=all', maximum=MAX_TREE))}

    def commit_tree(self, commit):
        need(hex40(commit), 'git_commit')
        actual = self.git('rev-parse', '--verify', commit + '^{commit}', maximum=128).decode().strip()
        tree = self.git('rev-parse', '--verify', commit + '^{tree}', maximum=128).decode().strip()
        need(actual == commit and hex40(tree), 'git_commit')
        return tree

    def tree(self, commit):
        need(hex40(commit), 'git_commit')
        return tree_entries(self.git('ls-tree', '-r', '-z', '--full-tree', commit))

    def blob(self, oid):
        need(hex40(oid), 'git_blob')
        return self.git('cat-file', 'blob', oid, maximum=MAX_FILE)


def release_snapshot(repository, release):
    need(hex40(release), 'release_commit')
    observed = repository.snapshot()
    need(type(observed) is dict and set(observed) == {'head', 'tree', 'branch', 'origin_main', 'origin', 'top', 'dirty'}
         and observed['head'] == observed['origin_main'] == release and hex40(observed['tree'])
         and observed['branch'] == 'main' and observed['dirty'] is False
         and observed['top'] == str(repository.path), 'clean_main_required')
    origin_identity(observed['origin'])
    return observed


def read_blob(repository, path, entry):
    need(entry['kind'] == 'blob' and entry['mode'] in {'100644', '100755'} and hex40(entry['oid']), 'git_file_type')
    raw = repository.blob(entry['oid'])
    need(type(raw) is bytes and 0 <= len(raw) <= MAX_FILE and git_blob(raw) == entry['oid'], 'git_blob_identity')
    return {'path': path, 'mode': entry['mode'], 'git_blob': entry['oid'], 'bytes': len(raw), 'sha256': sha256(raw).hexdigest()}, raw


def copy_contract(raw):
    need(type(raw) is bytes and 0 < len(raw) <= 131072, 'dockerfile_bound')
    try:
        lines = raw.decode('utf-8').splitlines()
    except UnicodeError as error:
        raise Refusal('dockerfile_syntax') from error
    # This is a closed current recipe contract, not a permissive Docker parser.
    # New COPY/ADD forms need review rather than a Main-source fallback.
    need(not any(re.match(r'(?i)^\s*#\s*escape\s*=', line) for line in lines), 'dockerfile_syntax')
    instructions = [line.strip() for line in lines if re.match(r'(?i)^\s*(FROM|COPY|ADD)\s', line)]
    need(all('\\' not in line and '`' not in line for line in instructions), 'dockerfile_syntax')
    starts = [line for line in instructions if re.match(r'(?i)^FROM\s', line)]
    need(len(starts) == len(FROM_LINES) and set(starts) == FROM_LINES, 'dockerfile_context_shadow')
    selected = [line for line in instructions if re.match(r'(?i)^(COPY|ADD)\s', line)]
    need(not any(re.search(r'(?i)^\s*FROM\s+.*\s+AS\s+business-source\s*(?:#.*)?$', line) for line in lines), 'dockerfile_context_shadow')
    need(len(selected) == len(PRODUCT_COPY_LINES | RELEASE_COPY_LINES)
         and set(selected) == PRODUCT_COPY_LINES | RELEASE_COPY_LINES, 'dockerfile_copy_contract')
    return True


def collect(repository, release, self_raw):
    """Public source only; no local dirty file or Main product fallback."""
    phase('release')
    end = time.monotonic() + 180
    def checkpoint():
        need(time.monotonic() < end, 'source_deadline')
    before = release_snapshot(repository, release)
    checkpoint()
    tree = repository.tree(release)
    need(all(name in tree for name in RELEASE_FILES), 'release_inputs_missing')
    releases = []
    release_raw = {}
    total = 0
    for name in sorted(RELEASE_FILES):
        checkpoint()
        row, raw = read_blob(repository, name, tree[name])
        need(len(raw) > 0, 'release_input_empty')
        checkpoint()
        total += len(raw)
        need(total <= MAX_TOTAL, 'source_total_bound')
        releases.append(row)
        release_raw[name] = raw
    need(release_raw[SELF_PATH] == self_raw, 'sealer_source_identity')
    copy_contract(release_raw['deploy/market-data/Dockerfile.capture2'])
    lock = lock_document(release_raw[LOCK_PATH])
    expected = manifest_document(release_raw[MANIFEST_PATH])
    need(origin_identity(before['origin']) == lock['repository_identity'], 'repository_identity')
    phase('product')
    commit = lock['business_commit']
    need(commit != release and repository.commit_tree(commit) == lock['business_tree'], 'product_git_identity')
    product_tree = repository.tree(commit)
    actual_names = {name for name in product_tree if copied(name)}
    need(actual_names == {row['path'] for row in expected}, 'product_inventory')
    product_raw = {}
    for row in expected:
        checkpoint()
        actual, raw = read_blob(repository, row['path'], product_tree[row['path']])
        checkpoint()
        need(actual == row, 'product_file_mismatch')
        total += len(raw)
        need(total <= MAX_TOTAL, 'source_total_bound')
        product_raw[row['path']] = raw
    need(release_snapshot(repository, release) == before, 'release_changed')
    checkpoint()
    receipt = {'schema': 'normal_market_git_component_inputs/1', 'component': COMPONENT, 'role': ROLE,
               'release_commit': release, 'release_tree': before['tree'],
               'business_commit': commit, 'business_tree': lock['business_tree'],
               'repository_identity': lock['repository_identity'],
               'product_lock_sha256': sha256(release_raw[LOCK_PATH]).hexdigest(),
               'accepted_manifest_sha256': MANIFEST_SHA, 'product_files': expected, 'release_files': releases,
               'signature_format': 'normal-market-two-git-sources-v1',
               'live_remote_main_gate_performed': False, 'activation_authority': False, 'security_accepted': False}
    signature = sha256(encoded(receipt)).hexdigest()
    return receipt, signature, product_raw, release_raw, before


class NewTree:
    """Exclusive public output, descriptor-relative writes and physical finals."""
    def __init__(self, root, stack):
        self.root = Path(root)
        need('.git' not in self.root.parts, 'output_git_path')
        need(self.root.is_absolute() and not os.path.lexists(self.root), 'output_exists')
        parent = self.root.parent
        need(parent.resolve(strict=True) == parent and parent.is_dir(), 'output_parent')
        self.parent = parent
        self.parent_meta = identity(parent.lstat())[:5]
        need(self.parent_meta[3:5] == [os.geteuid(), os.getegid()] and not self.parent_meta[2] & 0o022, 'output_parent')
        self.parent_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        stack.callback(os.close, self.parent_fd)
        need(identity(os.fstat(self.parent_fd))[:5] == self.parent_meta, 'output_parent')
        os.mkdir(self.root.name, 0o700, dir_fd=self.parent_fd)
        self.directories = {}
        self.files = []
        self.frozen = None
        self._open_dir('', 0o700, stack)

    def _open_dir(self, name, mode, stack):
        path = self.root / name
        if name:
            parent_name = str(PurePosixPath(name).parent)
            parent_fd = self.directories['' if parent_name == '.' else parent_name][1]
            leaf = PurePosixPath(name).name
        else:
            parent_fd = self.parent_fd
            leaf = self.root.name
        admitted = identity(os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False))
        fd = os.open(leaf, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent_fd)
        stack.callback(os.close, fd)
        opened = identity(os.fstat(fd))
        need(opened == admitted == identity(os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False))
             and path.resolve(strict=True) == path and opened == identity(path.lstat())
             and stat.S_ISDIR(opened[2]) and opened[3:5] == [os.geteuid(), os.getegid()], 'output_directory')
        os.fchmod(fd, mode)
        before = identity(os.fstat(fd))[:5]
        need(path.resolve(strict=True) == path and before == identity(path.lstat())[:5]
             and before[2:] == [stat.S_IFDIR | mode, os.geteuid(), os.getegid()], 'output_directory')
        self.directories[name] = (path, fd, before)

    def check(self, closed=False):
        need(self.parent.resolve(strict=True) == self.parent and identity(self.parent.lstat())[:5] == self.parent_meta, 'output_parent_changed')
        if not closed:
            need(identity(os.fstat(self.parent_fd))[:5] == self.parent_meta, 'output_parent_changed')
        for path, fd, before in self.directories.values():
            need(path.resolve(strict=True) == path and identity(path.lstat())[:5] == before, 'output_directory_changed')
            if self.frozen is not None:
                need(identity(path.lstat()) == self.frozen[path], 'output_directory_changed')
            if not closed:
                need(identity(os.fstat(fd))[:5] == before, 'output_directory_changed')
                if self.frozen is not None:
                    need(identity(os.fstat(fd)) == self.frozen[path], 'output_directory_changed')
                allowed = {child.name for child, _, _ in self.directories.values() if child.parent == path}
                allowed.update(child.name for child, _, _ in self.files if child.parent == path)
                with os.scandir(fd) as entries:
                    found = []
                    for item in entries:
                        need(len(found) < 256, 'output_inventory')
                        found.append(item.name)
                need(set(found) == allowed, 'output_inventory')
        for path, fd, before in self.files:
            need(path.resolve(strict=True) == path and identity(path.lstat()) == before, 'output_file_changed')
            if not closed:
                need(identity(os.fstat(fd)) == before, 'output_file_changed')

    def freeze(self):
        self.check()
        self.frozen = {path: identity(os.fstat(fd)) for path, fd, _ in self.directories.values()}
        self.check()

    def write(self, name, raw, mode, stack):
        need(path_ok(name) and type(raw) is bytes and 0 <= len(raw) <= MAX_TOTAL + 1024**2 and mode in {0o600, 0o644, 0o755}, 'output_operand')
        self.check()
        parent = ''
        for part in PurePosixPath(name).parts[:-1]:
            child = (parent + '/' if parent else '') + part
            if child not in self.directories:
                os.mkdir(part, 0o755, dir_fd=self.directories[parent][1])
                self._open_dir(child, 0o755, stack)
            parent = child
        fd = os.open(PurePosixPath(name).name, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                     mode, dir_fd=self.directories[parent][1])
        stack.callback(os.close, fd)
        os.fchmod(fd, mode)
        offset = 0
        while offset < len(raw):
            wrote = os.write(fd, raw[offset:offset + 131072])
            need(wrote > 0, 'output_write')
            offset += wrote
        os.fsync(fd)
        before = identity(os.fstat(fd))
        need(before[2:7] == [stat.S_IFREG | mode, os.geteuid(), os.getegid(), 1, len(raw)]
             and os.pread(fd, len(raw) + 1, 0) == raw, 'output_readback')
        self.files.append((self.root / name, fd, before))
        self.check()


def input_receipt(receipt, signature):
    keys = {'schema', 'component', 'role', 'release_commit', 'release_tree', 'business_commit', 'business_tree',
            'repository_identity', 'product_lock_sha256', 'accepted_manifest_sha256', 'product_files', 'release_files',
            'signature_format', 'live_remote_main_gate_performed', 'activation_authority', 'security_accepted'}
    need(type(receipt) is dict and set(receipt) == keys and receipt['schema'] == 'normal_market_git_component_inputs/1'
         and receipt['component'] == COMPONENT and receipt['role'] == ROLE
         and receipt['accepted_manifest_sha256'] == MANIFEST_SHA and receipt['signature_format'] == 'normal-market-two-git-sources-v1'
         and all(receipt[name] is False for name in ('live_remote_main_gate_performed', 'activation_authority', 'security_accepted'))
         and all(hex40(receipt[name]) for name in ('release_commit', 'release_tree', 'business_commit', 'business_tree'))
         and receipt['release_commit'] != receipt['business_commit'] and hex64(receipt['product_lock_sha256']), 'input_receipt')
    origin = receipt['repository_identity']
    need(type(origin) is dict and set(origin) == {'host', 'repository'} and origin['host'] == 'github.com'
         and type(origin['repository']) is str and origin_identity('https://github.com/' + origin['repository']) == origin, 'input_receipt')
    product_rows = receipt['product_files']
    need(type(product_rows) is list and len(product_rows) == 93, 'input_receipt')
    for row in product_rows:
        need(type(row) is dict and set(row) == {'path', 'mode', 'git_blob', 'bytes', 'sha256'}
             and copied(row['path']) and row['mode'] in {'100644', '100755'}
             and hex40(row['git_blob']) and hex64(row['sha256'])
             and type(row['bytes']) is int and 0 <= row['bytes'] <= MAX_FILE, 'input_receipt')
    need(sha256(encoded(product_rows)).hexdigest() == ROWS_SHA, 'input_receipt')
    rows = receipt['release_files']
    need(type(rows) is list and len(rows) == len(RELEASE_FILES), 'input_receipt')
    for row in rows:
        need(type(row) is dict and set(row) == {'path', 'mode', 'git_blob', 'bytes', 'sha256'}
             and row['mode'] in {'100644', '100755'} and hex40(row['git_blob']) and hex64(row['sha256'])
             and row['path'] in RELEASE_FILES and type(row['bytes']) is int and 0 < row['bytes'] <= MAX_FILE, 'input_receipt')
    need(rows == sorted(rows, key=lambda row: row['path']) and {row['path'] for row in rows} == set(RELEASE_FILES), 'input_receipt')
    need(hex64(signature) and sha256(encoded(receipt)).hexdigest() == signature, 'signature_binding')
    return receipt


def control_component(product_raw, receipt, signature):
    """Only the product parcel; never pretend this is a complete deploy payload."""
    input_receipt(receipt, signature)
    rows = receipt['product_files']
    need(type(product_raw) is dict and sha256(encoded(rows)).hexdigest() == ROWS_SHA
         and set(product_raw) == {row['path'] for row in rows} and hex64(signature), 'control_product_inventory')
    for row in rows:
        raw = product_raw[row['path']]
        need(type(raw) is bytes and len(raw) == row['bytes'] and sha256(raw).hexdigest() == row['sha256'], 'control_product_identity')
    class BoundedBuffer(io.BytesIO):
        def write(self, raw):
            need(self.tell() + len(raw) <= MAX_TOTAL + 1024**2, 'control_archive_bound')
            return super().write(raw)
    output = BoundedBuffer()
    with tarfile.open(fileobj=output, mode='w', format=tarfile.USTAR_FORMAT) as archive:
        for row in receipt['product_files']:
            raw = product_raw[row['path']]
            need(len(raw) == row['bytes'] and sha256(raw).hexdigest() == row['sha256'], 'control_product_identity')
            info = tarfile.TarInfo(row['path'])
            info.size = len(raw)
            info.mode = 0o444 if row['mode'] == '100644' else 0o555
            info.uid = info.gid = info.mtime = 0
            archive.addfile(info, io.BytesIO(raw))
        metadata = encoded({'schema': 'normal_market_control_product_component/1',
                            'release_commit': receipt['release_commit'], 'business_commit': receipt['business_commit'],
                            'business_tree': receipt['business_tree'], 'input_signature': signature,
                            'accepted_manifest_sha256': MANIFEST_SHA, 'component': COMPONENT, 'role': ROLE,
                            'complete_deployment_payload': False, 'activation_authority': False})
        info = tarfile.TarInfo('market-business-component.json')
        info.size = len(metadata)
        info.mode = 0o444
        archive.addfile(info, io.BytesIO(metadata))
    raw = output.getvalue()
    need(len(raw) <= MAX_TOTAL + 1024**2, 'control_archive_bound')
    return raw


def build_contract(receipt, signature, root):
    """Fixed argument data only; no Docker command or cache preparation."""
    input_receipt(receipt, signature)
    need(Path(root).is_absolute() and '.git' not in Path(root).parts, 'build_contract')
    return {'schema': 'normal_market_two_source_build_contract/1',
            'default_context': str(Path(root) / 'release'),
            'named_contexts': {'business-source': str(Path(root) / 'product')},
            'dockerfile': 'deploy/market-data/Dockerfile.capture2',
            'build_args': {'SOURCE_SHA': receipt['release_commit'], 'SOURCE_TREE': receipt['release_tree'],
                           'IMAGE_INPUT_SIGNATURE': signature, 'IMAGE_VERSION': 'production-private-primary',
                           'BUSINESS_SOURCE_COMMIT': receipt['business_commit'],
                           'BUSINESS_SOURCE_TREE': receipt['business_tree'],
                           'BUSINESS_SOURCE_MANIFEST_SHA256': MANIFEST_SHA},
            'source_date_epoch_from_same_release_commit_required': True,
            'normal_security_cache_preparation_required': True,
            'fresh_exact_context_revalidation_before_build_required': True,
            'live_remote_main_gate_required': True, 'build_performed': False,
            'activation_authority': False}


def seal(repository, release, root):
    output = source = None
    phase('output')
    need('.git' not in Path(root).parts, 'output_git_path')
    end = time.monotonic() + 180
    with ExitStack() as stack:
        phase('source')
        source = PublicFile(Path(__file__), stack, 131072)
        receipt, signature, product, releases, before = collect(repository, release, source.raw)
        phase('output')
        output = NewTree(root, stack)
        for row in receipt['product_files']:
            need(time.monotonic() < end, 'source_deadline')
            output.write('product/' + row['path'], product[row['path']], 0o755 if row['mode'] == '100755' else 0o644, stack)
        for row in receipt['release_files']:
            need(time.monotonic() < end, 'source_deadline')
            output.write('release/' + row['path'], releases[row['path']], 0o755 if row['mode'] == '100755' else 0o644, stack)
        phase('control_component')
        parcel = control_component(product, receipt, signature)
        output.write('product-control-component.tar', parcel, 0o600, stack)
        output.write('source-inputs.json', encoded({'input_signature': signature, 'inputs': receipt,
                     'control_component': {'bytes': len(parcel), 'sha256': sha256(parcel).hexdigest()},
                     'build_contract': build_contract(receipt, signature, root),
                     'sealed_source_only': True, 'build_performed': False, 'deployment_eligible': False}), 0o600, stack)
        phase('final')
        need(release_snapshot(repository, release) == before, 'release_changed')
        source.check()
        output.freeze()
        stack.callback(output.check)
    need(release_snapshot(repository, release) == before, 'release_changed')
    source.check(closed=True)
    output.check(closed=True)
    need(time.monotonic() < end, 'source_deadline')
    return {'schema': 'normal_market_sealed_git_product/1', 'component': COMPONENT, 'role': ROLE,
            'release_commit': release, 'business_commit': receipt['business_commit'],
            'input_signature': signature, 'product_files': 93, 'release_files': len(RELEASE_FILES),
            'build_performed': False, 'activation_authority': False, 'security_accepted': False}


def main(argv=None):
    phase('bootstrap')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path)
    parser.add_argument('--release-commit')
    parser.add_argument('--root', type=Path)
    parser.add_argument('--seal-public-source', action='store_true')
    args = parser.parse_args(argv)
    if not args.seal_public_source:
        print(json.dumps({'schema': 'normal_market_source_unarmed/1', 'lock': template(),
                          'git_invoked': False, 'build_performed': False, 'activation_authority': False}, sort_keys=True))
        return 0
    need(args.repo is not None and args.root is not None and hex40(args.release_commit), 'arguments')
    result = seal(GitRepository(args.repo), args.release_commit, args.root)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    try:
        exit_code = main()
    except Exception as error:
        print(json.dumps(closed_refusal(error), sort_keys=True))
        exit_code = 2
    raise SystemExit(exit_code)
