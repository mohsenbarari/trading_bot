#!/usr/bin/env python3
"""Prepare public build inputs, never images, deployment or runtime configuration.

The recipe is published main; the product is the deployed baseline plus this
closed security overlay. Git objects/refs/index and the checkout are read-only.
"""
import argparse
from hashlib import sha1, sha256
import json
import os
from pathlib import Path, PurePosixPath
import re
import resource
import stat
import subprocess
import sys
import tempfile

BASELINE = 'ba1f7616ade57558f51bb3a7fbb903243a33aab4'
BASELINE_TREE = '5d2ff35831696a3886372e4388f2a769a3594a8a'
SOURCE_REPOSITORY = 'mohsenbarari/trading_bot'
OVERLAY = (
    'Dockerfile', 'Dockerfile.iran', 'requirements.txt',
    'deploy/production/pip-bootstrap-requirements.txt',
    'api/deps.py', 'api/routers/auth.py', 'api/routers/chat.py',
    'api/routers/commodities.py', 'api/routers/realtime.py', 'api/routers/sessions.py',
    'core/frontend_static.py', 'core/jwt_codec.py', 'core/security.py',
    'core/services/chat_upload_session_service.py', 'main.py',
    'frontend/package.json', 'frontend/package-lock.json',
    'frontend/scripts/check-node-security-baseline.mjs',
    'frontend/src/composables/useWebSocket.ts', 'frontend/src/utils/currentUser.ts',
    'scripts/prepare_psycopg2_source.py', 'scripts/verify_image_trust.py',
    'scripts/verify_psycopg2_runtime.py', 'scripts/verify_sqlite_runtime.py', 'scripts/remove_runtime_pip.py',
)
DIRECT = {'.dockerignore', 'Dockerfile', 'Dockerfile.iran', 'requirements.txt',
          'alembic.ini', 'main.py', 'manage.py', 'run_bot.py', 'schemas.py',
          'seed_fake_data.py', 'trading_settings.json',
          'deploy/production/pip-bootstrap-requirements.txt'}
ROOTS = {'api', 'bot', 'core', 'src', 'migrations', 'models', 'templates', 'fonts', 'scripts', 'frontend'}
MAXIMUM = 128 * 1024 * 1024
PUBLIC_FAILURE_CODES = frozenset({
    'baseline_identity_changed', 'baseline_product_drift', 'baseline_tree_mismatch',
    'blob_inventory_incomplete', 'blob_metadata_invalid', 'blob_stream_bound',
    'blob_stream_changed', 'blob_stream_file_bound', 'blob_stream_header',
    'build_context_size_bound', 'clean_published_main_required', 'closed_security_overlay_required',
    'context_entry_count_bound', 'context_entry_metadata', 'context_file_bound',
    'context_file_changed', 'context_file_metadata', 'context_link_refused',
    'duplicate_context_binding', 'existing_output_refused', 'frontend_dist_bound',
    'git_entry_count_bound', 'git_object_format', 'git_output_bound', 'git_path_prefix_collision',
    'incomplete_build_context', 'invalid_git_entry', 'invalid_recipe_tree', 'invalid_source_commit',
    'market_overlay_forbidden', 'non_source_context_entry', 'overlay_link_refused',
    'payload_label_identity', 'public_git_read_failed', 'published_main_changed',
    'real_github_origin_required', 'sealed_context_source_changed', 'short_artifact_write',
    'source_publication_changed', 'unexpected_blob_stream_tail', 'unlisted_context_file',
    'unsafe_git_path', 'unsafe_output_parent', 'unsafe_output_path', 'wrong_source_repository',
})


def require(ok, code):
    if not ok:
        raise RuntimeError(code)


def git(repo, *args, data=None, maximum=8 * 1024 * 1024):
    """Bound a trusted Git command before output reaches Python memory."""
    def limits():
        resource.setrlimit(resource.RLIMIT_FSIZE, (maximum, maximum))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    with tempfile.TemporaryFile() as output:
        try:
            subprocess.run(['/usr/bin/git', '--no-optional-locks', '-C', str(repo), *args], input=data,
                           stdout=output, stderr=subprocess.DEVNULL, check=True,
                           timeout=30, preexec_fn=limits)
        except (OSError, subprocess.SubprocessError):
            raise RuntimeError('public_git_read_failed') from None
        require(output.tell() <= maximum, 'git_output_bound')
        output.seek(0)
        return output.read(maximum + 1)


def published_source(repo, commit):
    require(re.fullmatch('[a-f0-9]{40}', commit), 'invalid_source_commit')
    require(git(repo, 'rev-parse', '--show-object-format').strip() == b'sha1', 'git_object_format')
    require(git(repo, 'symbolic-ref', '--short', 'HEAD').strip() == b'main' and
            git(repo, 'rev-parse', 'HEAD').strip() == commit.encode() and
            git(repo, 'rev-parse', '@{u}').strip() == commit.encode() and
            git(repo, 'rev-parse', '--abbrev-ref', '@{u}').strip() == b'origin/main' and
            not git(repo, 'status', '--porcelain', '--untracked-files=normal'), 'clean_published_main_required')
    remote = git(repo, 'remote', 'get-url', 'origin').decode().strip()
    match = re.fullmatch(r'(?:https://github\.com/|git@github\.com:)([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?', remote)
    require(match is not None, 'real_github_origin_required')
    require(match[1].removesuffix('.git') == SOURCE_REPOSITORY, 'wrong_source_repository')
    require(git(repo, 'ls-remote', '--exit-code', 'origin', 'refs/heads/main').split() ==
            [commit.encode(), b'refs/heads/main'], 'published_main_changed')
    return 'https://github.com/' + SOURCE_REPOSITORY


def entries(raw):
    result = {}
    for record in raw.split(b'\0'):
        if not record:
            continue
        header, name = record.split(b'\t', 1)
        mode, kind, digest = header.decode('ascii').split()
        name = name.decode('utf-8')
        require(not name.startswith('/') and str(PurePosixPath(name)) == name and
                all(part not in {'', '.', '..'} for part in name.split('/')) and
                not any(ord(c) < 32 for c in name), 'unsafe_git_path')
        require(mode in {'100644', '100755', '120000'} and kind == 'blob' and
                re.fullmatch('[a-f0-9]{40}', digest) and name not in result, 'invalid_git_entry')
        result[name] = (mode, digest)
        require(len(result) <= 30000, 'git_entry_count_bound')
    return result


def tree_digest(files):
    root = {}
    for name, entry in files.items():
        node = root
        parts = name.split('/')
        for part in parts[:-1]:
            node = node.setdefault(part, {})
            require(isinstance(node, dict), 'git_path_prefix_collision')
        require(parts[-1] not in node, 'git_path_prefix_collision')
        node[parts[-1]] = entry
    def digest(node):
        items = sorted(node.items(), key=lambda item: item[0].encode() + (b'/' if isinstance(item[1], dict) else b''))
        body = bytearray()
        for name, entry in items:
            mode, oid = ('40000', digest(entry)) if isinstance(entry, dict) else entry
            body.extend(mode.encode() + b' ' + name.encode() + b'\0' + bytes.fromhex(oid))
        return sha1(b'tree ' + str(len(body)).encode() + b'\0' + body).hexdigest()
    return digest(root)


def compose(base, overlay):
    require(tree_digest(base) == BASELINE_TREE, 'baseline_tree_mismatch')
    require(set(overlay) == set(OVERLAY), 'closed_security_overlay_required')
    require(all(not name.startswith('core/market_intelligence/') for name in overlay), 'market_overlay_forbidden')
    require(all(mode in {'100644', '100755'} for mode, _ in overlay.values()), 'overlay_link_refused')
    result = {**base, **overlay}
    require(all(result[name] == value for name, value in base.items() if name not in OVERLAY), 'baseline_product_drift')
    return result


def context_entry(name):
    # Dependency caches are generated inside the reviewed builder, never taken
    # from local disk. A regular empty cache directory satisfies Docker COPY.
    return name in DIRECT or name.split('/')[0] in ROOTS


def image_labels(record, manifest_digest):
    require(record['schema'] == 'security_consumer_payload/1' and
            record['baseline_commit'] == BASELINE and record['baseline_tree'] == BASELINE_TREE and
            re.fullmatch('[a-f0-9]{64}', manifest_digest), 'payload_label_identity')
    return {'org.opencontainers.image.revision': BASELINE,
            'org.opencontainers.image.source': 'https://github.com/' + SOURCE_REPOSITORY,
            'io.gold-trade.release.tree': record['payload_tree'],
            'io.gold-trade.security.baseline.tree': BASELINE_TREE,
            'io.gold-trade.security.overlay.revision': record['source_commit'],
            'io.gold-trade.security.overlay.tree': record['source_tree'],
            'io.gold-trade.security.payload.sha256': manifest_digest}


def safe_parent(path):
    require(path.is_absolute() and path.parent.resolve(strict=True) == path.parent, 'unsafe_output_path')
    for parent in path.parents:
        info = parent.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid in {0, os.geteuid()} and
                not info.st_mode & 0o022, 'unsafe_output_parent')
    require(not os.path.lexists(path), 'existing_output_refused')


def write_file(path, raw, mode=0o600):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    try:
        view = memoryview(raw)
        while view:
            count = os.write(fd, view)
            require(count > 0, 'short_artifact_write')
            view = view[count:]
        os.fsync(fd)
    finally:
        os.close(fd)


def read_regular(path, maximum):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_uid == os.geteuid() and
                before.st_nlink == 1 and not before.st_mode & 0o022 and before.st_size <= maximum, 'context_file_metadata')
        raw = bytearray()
        while block := os.read(fd, min(65536, maximum + 1 - len(raw))):
            raw.extend(block); require(len(raw) <= maximum, 'context_file_bound')
        identity = lambda s: (s.st_dev, s.st_ino, s.st_uid, s.st_mode, s.st_nlink, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        require(len(raw) == before.st_size and identity(before) == identity(os.fstat(fd)) == identity(path.lstat()), 'context_file_changed')
        return bytes(raw)
    finally: os.close(fd)


def verify_context(context, record):
    """After npm, forbid extra backend files as well as changed tracked bytes."""
    expected = {item['path']: item for item in record['files']}
    require(len(expected) == len(record['files']), 'duplicate_context_binding')
    seen, dist, count, dist_size = set(), [], 0, 0
    for parent, dirs, files in os.walk(context, followlinks=False):
        relative = Path(parent).relative_to(context)
        if relative == Path('frontend') and 'node_modules' in dirs:
            dirs.remove('node_modules')  # Excluded by the sealed .dockerignore.
        for name in [*dirs, *files]:
            path = Path(parent) / name; info = path.lstat()
            require(not stat.S_ISLNK(info.st_mode) and info.st_uid == os.geteuid() and
                    not info.st_mode & 0o022 and (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)), 'context_entry_metadata')
            count += 1; require(count <= 30000, 'context_entry_count_bound')
        for name in files:
            path = Path(parent) / name; key = path.relative_to(context).as_posix()
            raw = read_regular(path, 8 * 1024 * 1024)
            if key in expected:
                item = expected[key]
                require(len(raw) == item['bytes'] and sha256(raw).hexdigest() == item['sha256'], 'sealed_context_source_changed')
                seen.add(key)
            else:
                require(key.startswith('mini_app_dist/') and not any(part.startswith('.env') for part in key.split('/')), 'unlisted_context_file')
                dist_size += len(raw); require(dist_size <= 64 * 1024 * 1024, 'frontend_dist_bound')
                dist.append({'path': key, 'bytes': len(raw), 'sha256': sha256(raw).hexdigest()})
    require(seen == set(expected) and any(item['path'] == 'mini_app_dist/index.html' for item in dist), 'incomplete_build_context')
    return sha256(json.dumps(sorted(dist, key=lambda item: item['path']), sort_keys=True).encode()).hexdigest()


def prepare(repo, commit, output=None):
    repository = published_source(repo, commit)
    require(git(repo, 'rev-parse', BASELINE + '^{tree}').strip().decode() == BASELINE_TREE, 'baseline_identity_changed')
    source_tree = git(repo, 'rev-parse', commit + '^{tree}').strip().decode()
    require(re.fullmatch('[a-f0-9]{40}', source_tree), 'invalid_recipe_tree')
    base = entries(git(repo, 'ls-tree', '-r', '-z', BASELINE))
    overlay = entries(git(repo, 'ls-tree', '-r', '-z', commit, '--', *OVERLAY))
    payload = compose(base, overlay)
    record = {'schema': 'security_consumer_payload/1', 'repository': repository,
              'baseline_commit': BASELINE, 'baseline_tree': BASELINE_TREE,
              'source_commit': commit, 'source_tree': source_tree, 'payload_tree': tree_digest(payload),
              'overlay': [{'path': name, 'mode': mode, 'git_blob': oid} for name, (mode, oid) in sorted(overlay.items())],
              'protected_market_tree': tree_digest({name.removeprefix('core/market_intelligence/'): value
                    for name, value in base.items() if name.startswith('core/market_intelligence/')}),
              'application_activation': False, 'migrations_executed': False}
    if output is None:
        return record
    safe_parent(output)
    selected = {name: item for name, item in payload.items() if context_entry(name)}
    require(all(mode in {'100644', '100755'} for mode, _ in selected.values()), 'context_link_refused')
    require(not any(part.startswith('.env') or part in {'node_modules', '__pycache__', 'mini_app_dist'}
                    for name in selected for part in name.split('/')), 'non_source_context_entry')
    identifiers = [oid for _, oid in selected.values()]
    sizes = git(repo, 'cat-file', '--batch-check', data=('\n'.join(identifiers) + '\n').encode()).splitlines()
    require(len(sizes) == len(identifiers), 'blob_inventory_incomplete')
    total = 0
    for expected, line in zip(identifiers, sizes):
        oid, kind, size = line.decode().split()
        require(oid == expected and kind == 'blob' and size.isdigit() and int(size) <= 8 * 1024 * 1024, 'blob_metadata_invalid')
        total += int(size)
    require(total <= MAXIMUM, 'build_context_size_bound')
    os.mkdir(output, 0o700)
    context = output / 'context'; context.mkdir(mode=0o700)
    # The total blob data is bounded on disk before streaming file-by-file;
    # never load a full source archive or working tree into memory.
    def limits():
        resource.setrlimit(resource.RLIMIT_FSIZE, (MAXIMUM + 8 * 1024 * 1024,) * 2)
    with tempfile.TemporaryFile() as stream:
        subprocess.run(['/usr/bin/git', '--no-optional-locks', '-C', str(repo), 'cat-file', '--batch'],
                       input=('\n'.join(identifiers) + '\n').encode(), stdout=stream,
                       stderr=subprocess.DEVNULL, timeout=60, check=True, preexec_fn=limits)
        require(stream.tell() <= MAXIMUM + 8 * 1024 * 1024, 'blob_stream_bound')
        stream.seek(0)
        files = []
        for name, (mode, expected) in selected.items():
            header = stream.readline(256).decode().strip().split()
            require(len(header) == 3 and header[:2] == [expected, 'blob'] and header[2].isdigit(), 'blob_stream_header')
            size = int(header[2]); require(size <= 8 * 1024 * 1024, 'blob_stream_file_bound')
            raw = stream.read(size)
            require(len(raw) == size and stream.read(1) == b'\n' and
                    sha1(b'blob ' + str(size).encode() + b'\0' + raw).hexdigest() == expected, 'blob_stream_changed')
            target = context / name; target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            write_file(target, raw, 0o700 if mode == '100755' else 0o600)
            files.append({'path': name, 'git_blob': expected, 'mode': mode, 'bytes': size, 'sha256': sha256(raw).hexdigest()})
        require(not stream.read(1), 'unexpected_blob_stream_tail')
    (context / 'pip_packages').mkdir(mode=0o700)
    record['files'] = files
    require(published_source(repo, commit) == repository, 'source_publication_changed')
    write_file(output / 'payload.json', (json.dumps(record, sort_keys=True) + '\n').encode())
    fd = os.open(output, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try: os.fsync(fd)
    finally: os.close(fd)
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--source-commit', required=True)
    parser.add_argument('--output', type=Path, help='Explicitly create a new private public-source build context; absent is read-only.')
    args = parser.parse_args()
    value = prepare(args.repo, args.source_commit, args.output)
    print(json.dumps({key: value[key] for key in ('schema', 'baseline_commit', 'source_commit', 'source_tree', 'payload_tree')}))


def public_failure_code(error):
    if type(error) is RuntimeError and len(error.args) == 1:
        value = error.args[0]
        if type(value) is str and len(value) <= 64 and value in PUBLIC_FAILURE_CODES:
            return value
    return 'unexpected_failure'


def run_cli():
    try:
        main()
        return 0
    except Exception as error:
        print(json.dumps({'status': 'refused', 'code': public_failure_code(error)}), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(run_cli())
