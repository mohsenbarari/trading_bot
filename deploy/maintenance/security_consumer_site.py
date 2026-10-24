#!/usr/bin/env python3
"""Finite per-site worker; called only by the native fenced Ansible controller.

Resolved Compose/Docker Env is compared inside anonymous memory only. This
worker never prints, hashes, persists, transfers or reports configuration data.
"""
import argparse
from datetime import datetime, timezone
import fcntl
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import resource
import stat
import subprocess
import tempfile
import time
from types import ModuleType
from uuid import UUID

HERE = Path(__file__).resolve().parent
OPERATION = 'security-consumers-20261023-01'
CUTOFF = datetime(2026, 10, 24, 0, 30, tzinfo=timezone.utc)
DEADLINE = datetime(2026, 10, 24, 1, 30, tzinfo=timezone.utc)
CONFIRMATION = 'SECURITY-CONSUMERS-ONLY-20261023'
HELPER_SHA = 'fa2bf85cd50378bb999a7d8aabbd965a174722605c943c59ce6da56f68a74901'
WORKSPACES = {'bot': Path('/root/trading-bot/trading_bot/.local/security-consumers-20261023-task01'),
              'web': Path('/root/.local/security-consumers-20261023-task01')}


def require(ok, code):
    if not ok: raise RuntimeError(code)


def secure_parents(path):
    for parent in path.parents:
        info = parent.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid in {0, os.geteuid()} and not info.st_mode & 0o022, 'unsafe_control_parent')


def read(path, maximum=2 * 1024 * 1024, public=False):
    path = Path(path)
    require(path.is_absolute() and path.resolve(strict=True) == path, 'redirected_control_path')
    secure_parents(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_uid == os.geteuid() and before.st_nlink == 1 and
                not before.st_mode & (0o022 if public else 0o077) and before.st_size <= maximum, 'unsafe_control_file')
        data = bytearray()
        while block := os.read(fd, min(65536, maximum + 1 - len(data))):
            data.extend(block)
            require(len(data) <= maximum, 'control_file_bound')
        identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mode, s.st_uid, s.st_nlink, s.st_mtime_ns, s.st_ctime_ns)
        require(identity(before) == identity(os.fstat(fd)) == identity(path.lstat()) and len(data) == before.st_size, 'control_file_changed')
        return bytes(data)
    finally: os.close(fd)


def load_module(path, name, expected):
    raw = read(path, maximum=65536, public=True)
    require(sha256(raw).hexdigest() == expected, 'public_control_source_changed')
    module = ModuleType(name)
    module.__file__ = str(path)
    exec(compile(raw, str(path), 'exec'), module.__dict__)
    return module


def private_json(argv, timeout=30):
    """Bound BEFORE accumulation; sensitive command output never touches disk."""
    maximum = 8 * 1024 * 1024
    fd = os.memfd_create('security-consumer-private-json', os.MFD_CLOEXEC)
    def limits():
        resource.setrlimit(resource.RLIMIT_FSIZE, (maximum, maximum))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    try:
        try:
            subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=fd, stderr=subprocess.DEVNULL,
                           timeout=timeout, check=True, preexec_fn=limits,
                           env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C.UTF-8',
                                'DOCKER_HOST': 'unix:///var/run/docker.sock'})
        except (OSError, subprocess.SubprocessError):
            raise RuntimeError('private_metadata_command_failed') from None
        require(os.lseek(fd, 0, os.SEEK_END) < maximum, 'private_metadata_output_bound')
        os.lseek(fd, 0, os.SEEK_SET)
        raw = bytearray()
        while block := os.read(fd, 65536): raw.extend(block)
        return json.loads(raw)
    finally: os.close(fd)


def native(site, contract):
    require(os.geteuid() == 0 and Path('/proc/1/exe').readlink().name == 'systemd', 'native_root_required')
    require(not any(Path(path).exists() for path in ('/.dockerenv', '/run/.containerenv', '/run/systemd/container')), 'container_refused')
    for name in ('pid', 'mnt', 'user'):
        require(os.readlink('/proc/self/ns/' + name) == os.readlink('/proc/1/ns/' + name), 'namespace_mismatch')
    links = private_json(['/usr/sbin/ip', '-j', 'address'])
    require(any(value.get('local') == contract.SITES[site]['host'] for link in links
                for value in link.get('addr_info', [])), 'wrong_security_consumer_host')
    require(datetime.now(timezone.utc) < DEADLINE, 'maintenance_window_expired')
    if site == 'bot':
        require(os.uname().release == '6.8.0-147-generic', 'native_kernel_not_remediated')


def native_coordinator_owner(record, b):
    """The native worker must observe real held fences, not a claimed PID.

    Web executes through the fixed, authenticated Ansible hop. It cannot inspect
    a remote host's /proc; this is deliberately not a cross-host lease scheme.
    """
    owner = b.document(b.RESERVATION)
    require(owner.get('schema') == 'security_consumer_release_guard/1' and
            owner.get('operation_id') == OPERATION and owner.get('nonce') == record['nonce'] and
            owner.get('source_commit') == record['source_commit'] and
            type(owner.get('pid')) is int and owner['pid'] > 1 and
            owner.get('inode') == b.RESERVATION.lstat().st_ino, 'native_coordinator_reservation_missing')
    # /proc is not a regular stable control file. This reviewed command reader
    # bounds public numeric lock metadata before accumulation; no Env is read.
    locks = b.command(['/usr/bin/cat', '/proc/locks'])
    held = set()
    for line in locks.splitlines():
        fields = line.split()
        if len(fields) < 8 or fields[1:4] != ['FLOCK', 'ADVISORY', 'WRITE']:
            continue
        major, minor, inode = fields[5].split(':')
        held.add((int(fields[4]), os.makedev(int(major, 16), int(minor, 16)), int(inode)))
    for path in (b.MARKER, b.SOURCE, b.RESERVATION):
        info = path.lstat()
        require((owner['pid'], info.st_dev, info.st_ino) in held, 'native_coordinator_fences_not_held')


def artifact(path, expected, site=None, raw=None):
    raw = read(path) if raw is None else raw
    require(isinstance(raw, bytes) and len(raw) <= 2 * 1024 * 1024, 'acceptance_buffer_invalid')
    require(re.fullmatch('[a-f0-9]{64}', expected) and sha256(raw).hexdigest() == expected, 'acceptance_bytes_changed')
    data = json.loads(raw)
    require(data['schema'] == 'security_consumer_acceptance/1' and data['status'] == 'accepted' and
            data['operation_id'] == OPERATION and data['deadline_utc'] == '2026-10-24T01:30:00Z' and
            data['migrations_allowed'] is False and data['capture_changes_allowed'] is False and
            data['writer_dns_changes_allowed'] is False, 'security_acceptance_scope_invalid')
    require(str(UUID(data['nonce'])) == data['nonce'] and
            all(re.fullmatch('[a-f0-9]{40}', data[name]) for name in ('source_commit', 'source_tree')) and
            set(data['images']) == {'bot', 'web'} and
            all(re.fullmatch('sha256:[a-f0-9]{64}', image) for image in data['images'].values()), 'acceptance_identity_invalid')
    # Final Codex acceptance binds actual reports. This code never mints PASS.
    checks = {'image_security', 'dependency_compatibility', 'producer_import_boundaries',
              'frontend', 'migration_tree_unchanged', 'backup_restore', 'bot_host_maintenance', 'web_host_maintenance'}
    require(set(data['evidence']) == checks, 'acceptance_evidence_incomplete')
    for name, binding in data['evidence'].items():
        evidence = read(HERE / 'evidence' / (name + '.json') if site == 'web' else binding['path'])
        require(sha256(evidence).hexdigest() == binding['sha256'], 'accepted_evidence_drift')
    require(set(data['control_sources']) == {'security_consumer_contract.py', 'security_consumer_site.py',
            'security_consumer_release.py', 'security_consumers.yml',
            'security_consumers_inventory.yml', 'security_consumers_ansible.cfg'} and
            all(re.fullmatch('[a-f0-9]{64}', value) for value in data['control_sources'].values()), 'control_source_scope')
    return data


def inspect_targets(site, contract, managed_override=None):
    names = [name.lstrip('/') for name in contract.SITES[site]['services'].values()]
    values = private_json(['/usr/bin/docker', 'inspect', *names])
    require(len(values) == len(names), 'target_inventory_incomplete')
    return {contract.assert_target(site, item, managed_override=managed_override): item for item in values}


def all_public(b):
    # Selected metadata only; no Env or full inspect response is written.
    fmt = '{"id":{{json .Id}},"name":{{json .Name}},"image":{{json .Image}}}'
    ids = b.command(['/usr/bin/docker', 'container', 'ls', '-aq', '--no-trunc']).split()
    require(0 < len(ids) <= 64, 'container_inventory_bound')
    rows = [json.loads(line) for line in b.command(['/usr/bin/docker', 'inspect', '--format', fmt, *ids]).splitlines()]
    require(len(rows) == len(ids) and len({row['id'] for row in rows}) == len(rows), 'container_inventory_ambiguous')
    return sorted(rows, key=lambda row: row['name'])


def compose_prefix(site, contract, override=None):
    scope = contract.SITES[site]
    result = ['/usr/bin/docker', 'compose', '--project-name', scope['project'], '--project-directory', scope['workdir']]
    for path in scope['files'] + ([str(override)] if override else []): result += ['-f', path]
    return result


def check_images(site, record, targets, contract):
    ids = {record['images'][site], *(item['Image'] for item in targets.values())}
    images = {row['Id']: row for row in private_json(['/usr/bin/docker', 'image', 'inspect', *sorted(ids)])}
    require(set(images) == ids, 'image_not_preloaded')
    new = images[record['images'][site]]
    labels = new['Config'].get('Labels') or {}
    contract.assert_payload_provenance(record, labels)
    return images


def config_metadata(site, contract, b):
    """Public Compose hashes, but ONLY metadata for the existing secret .env."""
    scope = contract.SITES[site]
    result = {'compose': []}
    for alias in scope['files']:
        path = Path(alias).resolve(strict=True)
        raw = b.read(path, private=False)
        info = path.lstat()
        result['compose'].append({'alias': alias, 'resolved_path': str(path), 'inode': info.st_ino,
                                  'size': len(raw), 'sha256': sha256(raw).hexdigest()})
    path = (Path(scope['workdir']) / '.env').resolve(strict=True)
    secure_parents(path)
    fd = os.open(path, os.O_PATH | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)  # O_PATH cannot read even a single configuration byte.
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid() and info.st_nlink == 1 and
                stat.S_IMODE(info.st_mode) == 0o600 and 0 < info.st_size <= 262144 and
                b.identity(info) == b.identity(path.lstat()), 'unsafe_runtime_env_metadata')
    finally: os.close(fd)
    result['env_metadata_only'] = [str(path), info.st_dev, info.st_ino, info.st_size, info.st_mode,
                                   info.st_uid, info.st_gid, info.st_mtime_ns, info.st_ctime_ns]
    return result


def baseline(site, record, contract, b):
    bound_config = config_metadata(site, contract, b)
    targets = inspect_targets(site, contract)
    rendered = private_json([*compose_prefix(site, contract), 'config', '--format', 'json'])
    images = check_images(site, record, targets, contract)
    for service, actual in targets.items():
        # An earlier full release leaves the writer-disabled file in labels
        # then restores policy via docker update. Normalize ONLY exact current
        # captured intent, never an inferred/desired restart policy.
        policy = actual['HostConfig']['RestartPolicy']
        require(policy['Name'] in {'no', 'always', 'unless-stopped'} and policy['MaximumRetryCount'] == 0, 'unsupported_restart_intent')
        rendered['services'][service]['restart'] = policy['Name']
        contract.assert_config_parity(site, service, actual, rendered,
                                      images[actual['Image']], images[record['images'][site]])
    require(config_metadata(site, contract, b) == bound_config, 'runtime_config_changed_during_preflight')
    public = {service: {**contract.public_identity(item), 'mounts': b.canonical_mounts(item['Mounts']),
                        'restart_policy': item['HostConfig']['RestartPolicy'],
                        'ports': contract.ports_from_inspect(item['HostConfig'].get('PortBindings')),
                        'networks': sorted(item['NetworkSettings']['Networks'])}
              for service, item in targets.items()}
    return {'schema': 'security_consumer_site_baseline/1', 'operation_id': OPERATION, 'site': site,
            'nonce': record['nonce'], 'source_commit': record['source_commit'],
            'candidate_image': record['images'][site], 'targets': public,
            'all_containers': all_public(b), 'config_binding': bound_config, 'env_persisted_or_hashed': False}


def verify_public(site, record, original, contract, b, rollback=False):
    require(original['nonce'] == record['nonce'] and original['site'] == site and
            original['source_commit'] == record['source_commit'], 'site_baseline_binding_changed')
    require(config_metadata(site, contract, b) == original['config_binding'], 'runtime_config_changed')
    override = HERE / ('rollback.override.json' if rollback else 'security.override.json')
    targets = inspect_targets(site, contract, managed_override=override)
    old_names = {item['name'] for item in original['targets'].values()}
    protected_before = [row for row in original['all_containers'] if row['name'] not in old_names]
    protected_after = [row for row in all_public(b) if row['name'] not in old_names]
    require(protected_before == protected_after, 'nonconsumer_identity_changed')
    for service, item in targets.items():
        old = original['targets'][service]
        expected = old['image'] if rollback else record['images'][site]
        require(item['Image'] == expected and b.canonical_mounts(item['Mounts']) == old['mounts'] and
                item['HostConfig']['RestartPolicy'] == old['restart_policy'] and
                contract.ports_from_inspect(item['HostConfig'].get('PortBindings')) == old['ports'] and
                sorted(item['NetworkSettings']['Networks']) == old['networks'], 'postactivation_runtime_drift')
        require(item['State'].get('Health', {}).get('Status') in {None, 'healthy'}, 'consumer_unhealthy')
    # Resolve config again and compare Env internally against the actual old
    # image defaults; public baseline intentionally contains no secret values.
    rendered = private_json([*compose_prefix(site, contract), 'config', '--format', 'json'])
    ids = {item['image'] for item in original['targets'].values()} | {record['images'][site]}
    images = {item['Id']: item for item in private_json(['/usr/bin/docker', 'image', 'inspect', *sorted(ids)])}
    for service, item in targets.items():
        rendered['services'][service]['restart'] = original['targets'][service]['restart_policy']['Name']
        # Image defaults can legitimately refresh Python build metadata. For a
        # running new image compare against its own defaults; explicit service
        # Env and all business flags remain exactly resolved from the same files.
        current_image = images[item['Image']]
        contract.assert_config_parity(site, service, item, rendered, current_image, current_image, managed_override=override)
    require(config_metadata(site, contract, b) == original['config_binding'], 'runtime_config_changed_during_postcheck')
    api = targets['app']
    try:
        status = b.command(['/usr/bin/docker', 'exec', api['Id'], '/usr/local/bin/python', '-I', '-B', '-c',
                            b.CONTAINER_HTTP_PROBE, 'http://127.0.0.1:8000/api/config'])
        b.command(['/usr/bin/docker', 'exec', 'trading_bot_db', '/bin/sh', '-c',
                   'exec pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"'])
        require(b.command(['/usr/bin/docker', 'exec', 'trading_bot_redis', 'redis-cli', 'ping']) == 'PONG', 'actual_api_not_healthy')
    except b.Refusal:
        raise RuntimeError('actual_api_not_healthy') from None
    require(status == '200', 'actual_api_not_healthy')


def rollback_preflight(site, record, original, contract, b):
    """Recognize only this operation's partial image cut, never another owner.

    A failed Compose cut can temporarily leave a selected consumer absent or
    stopped. It cannot authorize replacing a third-party container or changing
    any non-consumer. No command in this function mutates Docker or config.
    """
    require(config_metadata(site, contract, b) == original['config_binding'], 'rollback_runtime_config_changed')
    if not os.path.lexists(HERE / 'activation-intent.json'):
        require(baseline(site, record, contract, b) == original, 'precut_failure_not_unchanged')
        return False
    intent = json.loads(read(HERE / 'activation-intent.json'))
    require(intent == {'nonce': record['nonce'], 'site': site, 'phase': 'applying'}, 'rollback_not_owned_activation')
    current = all_public(b)
    names = {item['name'] for item in original['targets'].values()}
    require([row for row in current if row['name'] not in names] ==
            [row for row in original['all_containers'] if row['name'] not in names], 'rollback_nonconsumer_drift')
    existing = [row['id'] for row in current if row['name'] in names]
    require(len(existing) <= len(names), 'rollback_ambiguous_consumers')
    rows = private_json(['/usr/bin/docker', 'inspect', *existing]) if existing else []
    require(len(rows) == len(existing), 'rollback_inventory_incomplete')
    rendered = private_json([*compose_prefix(site, contract), 'config', '--format', 'json'])
    image_ids = {item['image'] for item in original['targets'].values()} | {record['images'][site]}
    images = {item['Id']: item for item in private_json(['/usr/bin/docker', 'image', 'inspect', *sorted(image_ids)])}
    require(set(images) == image_ids, 'rollback_image_missing')
    managed = HERE / 'security.override.json'
    seen = set()
    for item in rows:
        labels = item['Config']['Labels']
        service = labels.get('com.docker.compose.service')
        require(service in original['targets'] and service not in seen, 'rollback_ambiguous_service')
        seen.add(service)
        old = original['targets'][service]
        if item['Id'] == old['id']:
            require(item['Image'] == old['image'], 'rollback_original_image_drift')
            provenance = None
        else:
            require(item['Image'] == record['images'][site], 'rollback_foreign_image_or_identity')
            provenance = managed
        require(b.canonical_mounts(item['Mounts']) == old['mounts'] and
                item['HostConfig']['RestartPolicy'] == old['restart_policy'] and
                contract.ports_from_inspect(item['HostConfig'].get('PortBindings')) == old['ports'] and
                sorted(item['NetworkSettings']['Networks']) == old['networks'], 'rollback_consumer_runtime_drift')
        rendered['services'][service]['restart'] = old['restart_policy']['Name']
        actual_image = images[item['Image']]
        contract.assert_config_parity(site, service, item, rendered, actual_image, actual_image,
                                      managed_override=provenance, running=False)
    require(config_metadata(site, contract, b) == original['config_binding'], 'rollback_config_changed_during_preflight')
    return True


def wait_ready(site, record, original, contract, b, rollback):
    stop = time.monotonic() + 180
    while True:
        try:
            verify_public(site, record, original, contract, b, rollback=rollback)
            return
        except RuntimeError as error:
            require(str(error) in {'actual_api_not_healthy', 'consumer_unhealthy'} and
                    time.monotonic() < stop and datetime.now(timezone.utc) < DEADLINE,
                    'consumer_smoke_failed_or_runtime_drift')
            time.sleep(2)


def write(b, path, data, exclusive=False):
    b.private_parent(path)
    fd, temporary = tempfile.mkstemp(prefix='.security-consumer-', dir=path.parent)
    try:
        b.write_all(fd, (json.dumps(data, sort_keys=True) + '\n').encode())
        os.fsync(fd)
        if exclusive: b.publish_noreplace(temporary, path)
        else:
            read(path)
            os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try: os.fsync(directory)
        finally: os.close(directory)
    finally:
        os.close(fd)
        Path(temporary).unlink(missing_ok=True)


def site_lock(b, nonce, rollback):
    path = HERE / 'site-operation.lock'
    if os.path.lexists(path):
        require(rollback, 'site_operation_requires_owned_recovery')
        value = json.loads(read(path))
        require(value['schema'] == 'security_consumer_site_lock/1' and value['operation_id'] == OPERATION and
                value['nonce'] == nonce, 'foreign_site_operation')
        fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            require(b.identity(os.fstat(fd)) == b.identity(path.lstat()) and
                    os.fstat(fd).st_ino == value['inode'] and
                    json.loads(read(path)) == value, 'site_lock_inode_changed')
            return fd
        except BaseException:
            os.close(fd)
            raise
    fd, temporary = tempfile.mkstemp(prefix='.site-lock-', dir=HERE)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        payload = {'schema': 'security_consumer_site_lock/1', 'operation_id': OPERATION,
                   'nonce': nonce, 'inode': os.fstat(fd).st_ino}
        b.write_all(fd, (json.dumps(payload, sort_keys=True) + '\n').encode())
        os.fsync(fd)
        b.publish_noreplace(temporary, path)
        directory = os.open(HERE, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try: os.fsync(directory)
        finally: os.close(directory)
        return fd
    except BaseException:
        os.close(fd)
        raise
    finally: Path(temporary).unlink(missing_ok=True)


def release_site_lock(fd, nonce):
    lock = HERE / 'site-operation.lock'
    owned = json.loads(read(lock))
    require(os.fstat(fd).st_ino == lock.lstat().st_ino == owned['inode'] and
            owned['schema'] == 'security_consumer_site_lock/1' and owned['nonce'] == nonce, 'site_lock_changed')
    lock.unlink()
    directory = os.open(HERE, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try: os.fsync(directory)
    finally: os.close(directory)


def main():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))  # This process will hold resolved Env only in RAM.
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('check', 'snapshot', 'apply', 'rollback', 'verify'))
    parser.add_argument('--site', choices=('bot', 'web'), required=True)
    parser.add_argument('--evidence', type=Path, required=True)
    parser.add_argument('--evidence-sha256', required=True)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--primitives', type=Path, required=True)
    parser.add_argument('--apply')
    args = parser.parse_args()
    record = artifact(args.evidence, args.evidence_sha256, args.site)
    b = load_module(args.primitives, '_security_consumer_primitives', HELPER_SHA)
    c = load_module(HERE / 'security_consumer_contract.py', '_security_consumer_contract',
                    record['control_sources']['security_consumer_contract.py'])
    require(sha256(read(Path(__file__).resolve(), public=True)).hexdigest() ==
            record['control_sources']['security_consumer_site.py'], 'site_worker_source_changed')
    native(args.site, c)
    require(args.directory == HERE == WORKSPACES[args.site], 'site_directory_not_scoped_workspace')
    if args.mode == 'check':
        baseline(args.site, record, c, b)
        print(json.dumps({'status': 'preflight_passed', 'site': args.site, 'production_mutated': False}))
        return 0
    require(args.apply == CONFIRMATION, 'explicit_scoped_apply_required')
    require(args.mode != 'apply' or datetime.now(timezone.utc) < CUTOFF, 'activation_cutoff_expired')
    if args.site == 'bot': native_coordinator_owner(record, b)
    fd = site_lock(b, record['nonce'], rollback=args.mode == 'rollback')
    try:
        path = HERE / 'site-baseline.json'
        if args.mode == 'snapshot':
            write(b, path, baseline(args.site, record, c, b), exclusive=True)
        else:
            original = json.loads(read(path))
            require(original['nonce'] == record['nonce'] and original['site'] == args.site, 'baseline_not_owned')
            rollback = args.mode == 'rollback'
            if args.mode == 'verify': verify_public(args.site, record, original, c, b)
            else:
                if not rollback:
                    current = baseline(args.site, record, c, b)
                    require(current == original, 'baseline_changed_before_activation')
                else:
                    if not rollback_preflight(args.site, record, original, c, b):
                        write(b, HERE / 'rollback-result.json', {'status': 'unchanged_pre_cut',
                              'nonce': record['nonce'], 'site': args.site,
                              'capture_writer_dns_changed': False, 'migrations_executed': False,
                              'env_persisted_or_hashed': False}, exclusive=True)
                        release_site_lock(fd, record['nonce'])
                        return 0
                override = {'services': {name: {'image': item['image'] if rollback else record['images'][args.site],
                                               'pull_policy': 'never', 'restart': item['restart_policy']['Name']}
                                         for name, item in original['targets'].items()}}
                override_path = HERE / ('rollback.override.json' if rollback else 'security.override.json')
                write(b, override_path, override, exclusive=True)
                write(b, HERE / ('rollback-intent.json' if rollback else 'activation-intent.json'),
                      {'nonce': record['nonce'], 'site': args.site, 'phase': 'rolling_back' if rollback else 'applying'}, exclusive=True)
                # Silence Compose output: it may include application config on
                # an error. Only static failure codes may escape this worker.
                command = c.compose_apply_argv(args.site, override_path)
                require(config_metadata(args.site, c, b) == original['config_binding'], 'runtime_config_changed_before_cut')
                require(rollback or datetime.now(timezone.utc) < CUTOFF, 'activation_cutoff_expired')
                private_json_command(b, command, fd)
                wait_ready(args.site, record, original, c, b, rollback=rollback)
                write(b, HERE / ('rollback-result.json' if rollback else 'site-result.json'),
                      {'status': 'rolled_back' if rollback else 'completed', 'nonce': record['nonce'],
                       'site': args.site, 'source_commit': record['source_commit'],
                       'consumers_verified': len(original['targets']), 'capture_writer_dns_changed': False,
                       'migrations_executed': False, 'env_persisted_or_hashed': False}, exclusive=True)
        release_site_lock(fd, record['nonce'])
    finally:
        os.close(fd)
    return 0


def private_json_command(b, argv, lock_fd):
    # Reuse reviewed child-session termination/reaping while inheriting the
    # actual site flock. A killed worker cannot leave an unfenced Compose child.
    saved = [os.dup(1), os.dup(2)]
    null = os.open('/dev/null', os.O_WRONLY)
    try:
        os.dup2(null, 1); os.dup2(null, 2)
        status = b.execute_ansible(argv, {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C.UTF-8',
                                         'DOCKER_HOST': 'unix:///var/run/docker.sock'},
                                   '2026-10-24T01:30:00Z', lock_fds=(lock_fd,))
    finally:
        os.dup2(saved[0], 1); os.dup2(saved[1], 2)
        for fd in (*saved, null): os.close(fd)
    require(status == 0, 'consumer_command_failed')


if __name__ == '__main__':
    try: raise SystemExit(main())
    except Exception as error:
        print(json.dumps({'status': 'failed_closed', 'error_class': type(error).__name__}))
        raise SystemExit(1)
