#!/usr/bin/env python3
"""Finite Web CE preparation only: public pinned debs + actual metadata plan.

No package installation, service control, data backup, env/private-key reads or
application-source activation. Default check never writes/downloads anything.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
import argparse
import ctypes
import json
import os
from pathlib import Path
import re
import signal
import stat
import sys
import tempfile
from types import ModuleType
import urllib.parse
import urllib.request

ROOT = Path('/root/.local/security-web-engine-20261023')
SOURCE = ROOT / 'source'
INPUTS = ROOT / 'inputs.json'
PLAN = ROOT / 'plan.json'
PACKAGES = ROOT / 'packages'
NATIVE_INPUTS = Path('/root/trading-bot/trading_bot/.local/security-web-engine-20261023-inputs.json')
RECEIPTS = {
    'app_backup_receipt': ('/root/secure-envs/trading-bot/production-backups/evidence/security-host-maintenance-20261023.json', ROOT / 'app-backup-receipt.json'),
    'app_offhost_receipt': ('/root/trading-bot/trading_bot/.local/security-maintenance-20261023-offhost.json', ROOT / 'app-offhost-receipt.json'),
}
ANSIBLE = '/root/.local/security-kernel-reboot-20261023-task02/venv/bin/ansible-playbook'
PUBLIC_SOURCES = (
    'deploy/maintenance/web_engine_preflight.py', 'deploy/maintenance/web_engine_controller.py',
    'deploy/maintenance/web_engine_offhost.py', 'deploy/maintenance/web_engine_transaction.yml',
    'deploy/maintenance/web_engine_local_inventory.yml', 'deploy/maintenance/ansible.cfg',
    'scripts/validate_docker_daemon_maintenance.py', 'scripts/crypt_market_pipeline_backup.py',
    'scripts/backup_market_pipeline_archive.py', 'scripts/prepare_market_pipeline_release.py',
    'deploy/maintenance/web_engine_prepare.py', 'deploy/maintenance/web_engine_prepare.yml',
    'deploy/maintenance/web_engine_inventory.yml',
)
EMPTY_INIT = 'scripts/__init__.py'  # Generated EMPTY only in this private bundle.
CONTROLLER_SHA = '7137b99be2cdb84d4efd715adca14c41f7e8185978463e7c91b8624806980fb5'
OLD_URLS = {
    'docker.io': 'https://archive.ubuntu.com/ubuntu/pool/universe/d/docker.io-app/docker.io_29.1.3-0ubuntu4.1_amd64.deb',
    'containerd': 'https://security.ubuntu.com/ubuntu/pool/main/c/containerd-app/containerd_2.2.2-0ubuntu1.1_amd64.deb',
    'runc': 'https://archive.ubuntu.com/ubuntu/pool/main/r/runc-app/runc_1.4.0-0ubuntu1_amd64.deb',
    'docker-compose-v2': 'https://archive.ubuntu.com/ubuntu/pool/universe/d/docker-compose-v2/docker-compose-v2_2.40.3+ds1-0ubuntu1_amd64.deb',
}


def require(ok, code):
    if not ok: raise RuntimeError(code)


def parents(path):
    require(path.is_absolute() and path.resolve(strict=False) == path, 'noncanonical_path')
    for parent in path.parents:
        value = parent.lstat()
        require(stat.S_ISDIR(value.st_mode) and value.st_uid in {0, os.geteuid()} and not value.st_mode & 0o022, 'unsafe_parent')


def identity(value):
    return (value.st_dev, value.st_ino, value.st_mode, value.st_uid, value.st_gid,
            value.st_nlink, value.st_size, value.st_mtime_ns, value.st_ctime_ns)


def read(path, *, maximum=2 * 1024 * 1024, private=True):
    parents(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_uid == os.geteuid() and before.st_nlink == 1
                and not before.st_mode & (0o077 if private else 0o022) and before.st_size <= maximum, 'unsafe_file')
        data = bytearray()
        while chunk := os.read(fd, min(65536, maximum + 1 - len(data))):
            data.extend(chunk); require(len(data) <= maximum, 'file_bound')
        require(identity(before) == identity(os.fstat(fd)) == identity(path.lstat()), 'file_changed')
        return bytes(data)
    finally: os.close(fd)


def document(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, 'duplicate_json_key'); result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=unique)


def validate_inputs(spec):
    require(set(spec) == {'schema', 'operation_id', 'nonce', 'deadline_utc', 'source_sha256',
        'app_backup_receipt', 'app_offhost_receipt', 'market_backup', 'backup_key_file', 'offhost', 'datastore_probes'}, 'input_scope')
    require(spec['schema'] == 'web_engine_prepare_inputs/1' and
        re.fullmatch('security-web-engine-20261023-[A-Za-z0-9._-]{1,32}', spec['operation_id']) and
        re.fullmatch('[0-9a-f]{32}', spec['nonce']), 'operation_scope')
    require(spec['deadline_utc'].endswith('Z'), 'deadline_scope')
    deadline = datetime.fromisoformat(spec['deadline_utc'].replace('Z', '+00:00'))
    require(datetime.now(timezone.utc) < deadline <= datetime(2026, 10, 24, 4, 41, tzinfo=timezone.utc), 'window_expired_or_expanded')
    require(set(spec['source_sha256']) == set(PUBLIC_SOURCES) and all(
        re.fullmatch('[0-9a-f]{64}', value) for value in spec['source_sha256'].values()), 'source_inventory')
    require(spec['source_sha256']['deploy/maintenance/web_engine_controller.py'] == CONTROLLER_SHA, 'unreviewed_controller')
    for key in ('app_backup_receipt', 'app_offhost_receipt'):
        entry = spec[key]
        require(set(entry) == {'source_path', 'path', 'sha256'} and re.fullmatch('[0-9a-f]{64}', entry['sha256'])
                and entry['source_path'] == RECEIPTS[key][0] and Path(entry['path']) == RECEIPTS[key][1], 'receipt_input')
    market = spec['market_backup']
    require(set(market) == {'env_file', 'release_sha', 'release_tree', 'image_id', 'image_input_signature'}
        and Path(market['env_file']).is_absolute() and all(re.fullmatch('[0-9a-f]{40}', market[k]) for k in ('release_sha', 'release_tree'))
        and re.fullmatch('sha256:[0-9a-f]{64}', market['image_id']) and re.fullmatch('[0-9a-f]{64}', market['image_input_signature']), 'market_input_scope')
    remote = spec['offhost']
    require(set(remote) == {'identity_file', 'known_hosts_file', 'known_hosts_sha256'} and
        remote['identity_file'] == '/root/.ssh/security-controller-20261023-host-patch-03/id_ed25519' and
        remote['known_hosts_file'] == '/root/.ssh/security-controller-20261023-host-patch-03/known_hosts' and
        re.fullmatch('[0-9a-f]{64}', remote['known_hosts_sha256']), 'offhost_lane_scope')
    require(Path(spec['backup_key_file']).is_absolute() and isinstance(spec['datastore_probes'], list)
            and len(spec['datastore_probes']) == 8, 'private_path_or_probe_scope')
    require(len({p['name'] for p in spec['datastore_probes']}) == 8, 'duplicate_probe')
    for probe in spec['datastore_probes']:
        keys = {'name', 'kind'} if probe['kind'] == 'redis' else {'name', 'kind', 'user', 'database'}
        require(set(probe) == keys and re.fullmatch('/[A-Za-z0-9_.-]{1,100}', probe['name'])
            and probe['kind'] in {'postgres', 'redis'} and all(re.fullmatch('[A-Za-z0-9_]{1,63}', probe[k])
            for k in ('user', 'database') if k in probe), 'probe_scope')


def controller(spec, source):
    for name in PUBLIC_SOURCES:
        require(sha256(read(source / name, maximum=128 * 1024, private=False)).hexdigest()
                == spec['source_sha256'][name], 'public_source_changed')
    if source == SOURCE: require(read(source / EMPTY_INIT, private=False) == b'', 'bundle_namespace_marker_not_empty')
    path = source / 'deploy/maintenance/web_engine_controller.py'
    code = read(path, maximum=65536, private=False)
    require(sha256(code).hexdigest() == CONTROLLER_SHA, 'controller_changed')
    result = ModuleType('_reviewed_web_engine_controller'); result.__file__ = str(path)
    exec(compile(code, str(path), 'exec'), result.__dict__)
    return result


def private_metadata(path, maximum):
    path = Path(path); parents(path); value = path.lstat()
    require(stat.S_ISREG(value.st_mode) and value.st_uid == os.geteuid() and value.st_nlink == 1
            and not value.st_mode & 0o077 and 0 < value.st_size <= maximum, 'private_metadata_unsafe')
    return list(identity(value))  # NEVER open/read env or private-key contents.


def binding(path, *, private):
    path = Path(path); raw = read(path, private=private)
    return {'path': str(path), 'size': len(raw), 'sha256': sha256(raw).hexdigest(), 'private': private}


def package_entries(C):
    result = []
    for pins, old in ((C.P.OLD_PACKAGES, True), (C.P.NEW_PACKAGES, False)):
        for name, (version, size, digest) in pins.items():
            url = OLD_URLS[name] if old else ('https://download.docker.com/linux/ubuntu/dists/resolute/pool/stable/amd64/' +
                    name + '_' + version.split(':')[-1] + '_amd64.deb')
            filename = urllib.parse.urlsplit(url).path.rsplit('/', 1)[-1]
            result.append({'name': name, 'version': version, 'size': size, 'sha256': digest,
                           'path': str(PACKAGES / filename), 'url': url, 'old': old})
    return result


def publish(source, destination):
    function = ctypes.CDLL(None, use_errno=True).renameat2
    function.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    function.restype = ctypes.c_int
    if function(-100, os.fsencode(source), -100, os.fsencode(destination), 1):
        raise OSError(ctypes.get_errno(), 'exclusive_publish_failed')
    fd = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try: os.fsync(fd)
    finally: os.close(fd)


@contextmanager
def download_deadline():
    previous = signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(RuntimeError('download_timeout')))
    signal.alarm(120)
    try: yield
    finally: signal.alarm(0); signal.signal(signal.SIGALRM, previous)


class SameOrigin(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        old, new = urllib.parse.urlsplit(request.full_url), urllib.parse.urlsplit(newurl)
        require(new.scheme == 'https' and new.netloc == old.netloc and not new.username
                and not new.password and not new.fragment and not new.query, 'download_redirect_refused')
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def download(C, entry):
    destination = Path(entry['path']); parents(destination)
    require(destination.parent == PACKAGES and entry in package_entries(C), 'download_pin_scope')
    if os.path.lexists(destination):
        require(C.P.file_digest(destination, maximum=64 * 1024**2, private=True)
                == (entry['size'], entry['sha256']), 'existing_package_changed'); return
    fd, temporary = tempfile.mkstemp(prefix='.download-', dir=destination.parent)
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), SameOrigin())
        with download_deadline(), opener.open(entry['url'], timeout=15) as response:
            require(response.status == 200 and (response.headers.get('Content-Length') is None or
                response.headers['Content-Length'] == str(entry['size'])), 'download_status_or_length')
            digest, size = sha256(), 0
            while chunk := response.read(min(65536, entry['size'] + 1 - size)):
                size += len(chunk); require(size <= entry['size'], 'download_early_bound')
                digest.update(chunk); C.B.write_all(fd, chunk)
            require((size, digest.hexdigest()) == (entry['size'], entry['sha256']), 'download_digest_or_length')
        os.fsync(fd)
        try: publish(Path(temporary), destination)
        except FileExistsError:
            require(C.P.file_digest(destination, maximum=64 * 1024**2, private=True)
                    == (entry['size'], entry['sha256']), 'package_publish_race')
    finally: os.close(fd); Path(temporary).unlink(missing_ok=True)


def snapshot(C, spec):
    require(Path(__file__).resolve().parents[2] == SOURCE, 'fixed_staged_project_required')
    plan = {'schema': 'web_engine_transaction_plan/1', 'operation_id': spec['operation_id'],
        'nonce': spec['nonce'], 'deadline_utc': spec['deadline_utc'],
        'machine_id': Path('/etc/machine-id').read_text().strip(), 'source_root': str(SOURCE),
        'ansible_binary': ANSIBLE, 'app_backup_receipt': spec['app_backup_receipt']['path'],
        'app_offhost_receipt': spec['app_offhost_receipt']['path'], 'backup_key_file': spec['backup_key_file'],
        'offhost': dict(spec['offhost']), 'market_backup': dict(spec['market_backup']),
        'backup_root': '/srv/trading-bot/backups/security-web-engine-' + spec['operation_id'] + '-' + spec['nonce'],
        'absent_control_paths': ['/root/secure-envs/trading-bot/.production-runtime-source.lock',
            '/root/secure-envs/trading-bot/market-pipeline-cutover/bot-legacy-handoff-4ef8e6dc.json'],
        'health_urls': []}
    C.native_contract(plan); C.control_parent_contract(plan)
    addresses = document(C.B.command(['/usr/sbin/ip', '-j', 'address']))
    require(any(value.get('local') == '65.109.220.59' for row in addresses for value in row.get('addr_info', [])), 'web_ip_required')
    require(not any(os.path.lexists(path) for path in [C.P.RESERVATION, C.P.PENDING, *C.paths(plan), *plan['absent_control_paths']]), 'existing_operation')
    for path in (ROOT, SOURCE, PACKAGES):
        parents(path); info = path.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) == 0o700, 'workspace_directory_unsafe')
    marker = binding(C.P.MARKER, private=True); info = C.P.MARKER.lstat()
    plan['marker'] = {**marker, 'device': info.st_dev, 'inode': info.st_ino}
    plan['store_roots'] = []
    for path in ('/var/lib/docker', '/var/lib/containerd'):
        value = Path(path).lstat()
        entry = {'path': path, 'device': value.st_dev, 'inode': value.st_ino,
            'uuid': C.B.command(['/usr/bin/findmnt', '-n', '-o', 'UUID', '-T', path])}
        C.store_root_contract(entry); plan['store_roots'].append(entry)
    plan['containers'] = []
    for ident in C.B.command(['/usr/bin/docker', 'ps', '-aq', '--no-trunc']).splitlines():
        require(re.fullmatch('[0-9a-f]{64}', ident), 'container_id')
        row = document(C.B.command(['/usr/bin/docker', 'inspect', '--format', C.INSPECT, ident]))
        row['mounts'] = C.P.canonical_mounts(row['mounts']); row['network_names'] = sorted(row['network_names'])
        plan['containers'].append(row)
    C.P.baseline_contract(plan['containers'])
    by_name = {row['name']: row for row in plan['containers']}
    plan['datastore_probes'] = [{**{k: v for k, v in probe.items() if k != 'name'}, 'id': by_name[probe['name']]['id']} for probe in spec['datastore_probes']]
    plan['datastore_ids'] = [probe['id'] for probe in plan['datastore_probes']]
    actual_stores = {row['id'] for row in plan['containers'] if row['name'] in {'/trading_bot_db', '/trading_bot_redis'}
        or row['name'].endswith(('_db-1', '-db-1', '-market-database-1', '_redis-1', '-redis-1'))}
    require(len(actual_stores) == 8 and set(plan['datastore_ids']) == actual_stores, 'datastore_probe_identity_scope')
    plan['health_probes'] = [{'id': by_name['/trading_bot_app']['id'], 'url': 'http://127.0.0.1:8000/api/config'}]
    plan['units'] = []
    for name in sorted(C.UNITS):
        values = dict(line.split('=', 1) for line in C.B.command(['/usr/bin/systemctl', 'show', name,
            '--property=FragmentPath,DropInPaths,UnitFileState,ActiveState,Type']).splitlines())
        fragment = values['FragmentPath']; kind = 'timer' if name.endswith('.timer') else 'oneshot' if values['Type'] == 'oneshot' else 'daemon'
        plan['units'].append({'name': name, 'enabled': values['UnitFileState'], 'active': values['ActiveState'] == 'active',
            'kind': kind, 'fragment_path': fragment, 'fragment_sha256': binding(fragment, private=False)['sha256'],
            'dropins': [{'path': path, 'sha256': binding(path, private=False)['sha256']} for path in values['DropInPaths'].split()]})
    plan['engine_intent'] = []
    for name in [*C.ENGINES, 'nginx.service']:
        values = dict(line.split('=', 1) for line in C.B.command(['/usr/bin/systemctl', 'show', name, '--property=UnitFileState,ActiveState']).splitlines())
        plan['engine_intent'].append({'name': name, 'enabled': values['UnitFileState'], 'active': values['ActiveState'] == 'active'})
    plan['files'] = [binding(SOURCE / name, private=False) for name in (*PUBLIC_SOURCES, EMPTY_INIT)]
    plan['files'] += [binding(ANSIBLE, private=False), binding(INPUTS, private=True)]
    for key in ('app_backup_receipt', 'app_offhost_receipt'):
        item = binding(spec[key]['path'], private=True)
        require(item['sha256'] == spec[key]['sha256'], 'receipt_changed'); plan['files'].append(item)
    plan['market_backup']['env_file_identity'] = private_metadata(spec['market_backup']['env_file'], 1024 * 1024)
    private_metadata(spec['backup_key_file'], 1024 * 1024)
    private_metadata(spec['offhost']['identity_file'], 16384)
    require(binding(spec['offhost']['known_hosts_file'], private=True)['sha256'] == spec['offhost']['known_hosts_sha256'], 'offhost_pin_changed')
    C.backup_contract(plan)
    python = str(Path(ANSIBLE).parent / 'python3')
    require(C.B.command([python, '-I', '-B', '-c', 'import importlib.metadata as m;print(m.version("ansible-core"))']) == '2.21.5', 'ansible_version')
    C.B.command([python, '-I', '-B', '-m', 'pip', '--disable-pip-version-check', 'check'])
    packages = package_entries(C)
    for old, key in ((True, 'old_packages'), (False, 'new_packages')):
        plan[key] = [{k: v for k, v in entry.items() if k not in {'url', 'old'}} for entry in packages if entry['old'] == old]
    C.runtime_contract(plan, 'baseline')
    require(C.package_versions(C.P.OLD_PACKAGES) == {name: pin[0] for name, pin in C.P.OLD_PACKAGES.items()}, 'old_package_baseline_changed')
    return plan


def prepare(C, spec, *, apply):
    if os.path.lexists(PLAN):
        plan = document(read(PLAN))
        require(next(v for v in plan['files'] if v['path'] == str(INPUTS))['sha256'] == sha256(read(INPUTS)).hexdigest(), 'existing_plan_input_changed')
        C.invariant_contract(plan, sha256(read(PLAN)).hexdigest(), owned=False); C.runtime_contract(plan, 'baseline')
        return {'status': 'existing_plan_verified', 'plan_sha256': sha256(read(PLAN)).hexdigest(), 'runtime_changed': False}
    snapshot(C, spec)  # Wrong host/baseline/receipts refuse BEFORE downloads.
    if not apply: return {'status': 'metadata_checked', 'plan_created': False, 'runtime_changed': False}
    available = os.statvfs(ROOT)
    packages = package_entries(C)
    require(available.f_bavail * available.f_frsize >= 44 * 1024**3 + sum(v['size'] for v in packages), 'preparation_capacity_insufficient')
    for entry in packages: download(C, entry)
    plan = snapshot(C, spec)  # Freeze fresh facts after network transfers.
    C.invariant_contract(plan, '0' * 64, owned=False)
    fd, temporary = tempfile.mkstemp(prefix='.plan-', dir=ROOT)
    try:
        C.B.write_all(fd, (json.dumps(plan, sort_keys=True) + '\n').encode()); os.fsync(fd)
        publish(Path(temporary), PLAN)
    finally: os.close(fd); Path(temporary).unlink(missing_ok=True)
    return {'status': 'prepared_not_applied', 'plan_sha256': sha256(read(PLAN)).hexdigest(),
            'container_count': 27, 'prior_running': 26, 'packages_verified': 9, 'runtime_changed': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['validate-inputs', 'check', 'prepare'], default='check', nargs='?')
    args = parser.parse_args()
    try:
        path = NATIVE_INPUTS if args.action == 'validate-inputs' else INPUTS
        input_bytes = read(path, maximum=65536); spec = document(input_bytes); validate_inputs(spec)
        source = Path(__file__).resolve().parents[2]
        C = controller(spec, source)
        if args.action == 'validate-inputs':
            plan = {'files': []}
            for key in ('app_backup_receipt', 'app_offhost_receipt'):
                path = spec[key]['source_path']; value = binding(path, private=True)
                require(value['sha256'] == spec[key]['sha256'], 'native_receipt_changed')
                plan[key] = path; plan['files'].append(value)
            C.backup_contract(plan)
            result = {'status': 'public_inputs_validated', 'runtime_changed': False,
                      'source_sha256': spec['source_sha256'], 'inputs_sha256': sha256(input_bytes).hexdigest(),
                      'receipt_sha256': {Path(spec[key]['path']).name: spec[key]['sha256']
                                         for key in ('app_backup_receipt', 'app_offhost_receipt')}}
        else: result = prepare(C, spec, apply=args.action == 'prepare')
        print(json.dumps(result, sort_keys=True)); return 0
    except Exception as exc:
        message = str(exc)
        code = message if isinstance(exc, RuntimeError) and re.fullmatch('[a-z_]{1,96}', message) else 'preparation_refused'
        print(json.dumps({'status': 'refused', 'code': code}), file=sys.stderr); return 1


if __name__ == '__main__': raise SystemExit(main())
