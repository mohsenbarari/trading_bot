#!/usr/bin/env python3
"""One-host, one-window daemon maintenance guard; never a release/reboot tool.

`run` owns all three fences for the ENTIRE fixed Ansible subprocess. `check`
is its read-only phase probe. A crashed/unsafe operation leaves its reservation.
No environment, container arguments, secrets or exception strings are emitted.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import ctypes
from datetime import datetime, timezone
import fcntl
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import resource
import signal
import stat
import subprocess
import sys
import tempfile
import time

ROOT = Path('/root/secure-envs/trading-bot')
MARKER = ROOT / 'queue-cutover-artifacts/production-release.lock'
BOTJ = ROOT / 'market-pipeline-cutover/bot-legacy-handoff-4ef8e6dc.json'
SOURCE = ROOT / '.production-runtime-source.lock'
RESERVATION = ROOT / 'release-control/private-primary-controller.lock'
PENDING = Path('/var/lib/trading-bot/production-release/two-host-release-state.json')
PACKAGES = {
    'docker-ce': '5:29.9.2-1~ubuntu.24.04~noble',
    'docker-ce-cli': '5:29.9.2-1~ubuntu.24.04~noble',
    'docker-ce-rootless-extras': '5:29.9.2-1~ubuntu.24.04~noble',
    'containerd.io': '2.4.2-3~ubuntu.24.04~noble',
    'docker-compose-plugin': '5.6.1-1~ubuntu.24.04~noble',
}
INSPECT = ('{"id":{{json .Id}},"name":{{json .Name}},"image":{{json .Image}},'
           '"running":{{json .State.Running}},"restart_policy":{{json .HostConfig.RestartPolicy}},'
           '"project":{{json (index .Config.Labels "com.docker.compose.project")}},'
           '"mounts":{{json .Mounts}},"health":{{with index .State "Health"}}{{json .Status}}{{else}}null{{end}}}')
CONTAINER_HTTP_PROBE = '''import sys, urllib.request
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, url):
        return None
try:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(sys.argv[1], timeout=5) as response:
        status = response.status
    if status != 200:
        raise SystemExit(1)
except Exception:
    raise SystemExit(1) from None
print('200')
'''


class Refusal(RuntimeError):
    """Only static, public diagnostic codes are safe to emit."""


def require(ok, code):
    if not ok:
        raise Refusal(code)


def private_parent(path):
    require(path.is_absolute() and path.resolve(strict=False) == path, 'redirected_path')
    for parent in (path.parent, *path.parents):
        info = parent.stat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid in {0, os.geteuid()} and not info.st_mode & 0o022, 'unsafe_parent')


def identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid, info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def read(path, maximum=2 * 1024 * 1024, private=True):
    path = Path(path)
    private_parent(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_uid == os.geteuid() and before.st_nlink == 1, 'unsafe_file')
        require(not before.st_mode & (0o077 if private else 0o022) and before.st_size <= maximum, 'file_mode_or_bound')
        data = bytearray()
        while chunk := os.read(fd, min(65536, maximum + 1 - len(data))):
            data.extend(chunk)
            require(len(data) <= maximum, 'file_bound')
        require(identity(os.fstat(fd)) == identity(before) == identity(path.lstat()), 'file_changed')
        return bytes(data)
    finally:
        os.close(fd)


def command(argv):
    binary = Path(argv[0]).resolve(strict=True)
    info = binary.stat()
    require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022, 'untrusted_binary')
    with tempfile.TemporaryFile() as output:
        try:
            subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.DEVNULL,
                           timeout=25, check=True, env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C.UTF-8', 'DOCKER_HOST': 'unix:///var/run/docker.sock'},
                           preexec_fn=lambda: resource.setrlimit(resource.RLIMIT_FSIZE, (2 * 1024 * 1024, 2 * 1024 * 1024)))
        except subprocess.CalledProcessError:
            raise Refusal('native_command_failed_' + binary.name) from None
        except subprocess.TimeoutExpired:
            raise Refusal('native_command_timeout_' + binary.name) from None
        require(output.tell() <= 2 * 1024 * 1024, 'command_output_bound')
        output.seek(0)
        return output.read(2 * 1024 * 1024).decode('utf-8').strip()


def decode(raw):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            require(key not in value, 'duplicate_key')
            value[key] = item
        return value
    return json.loads(raw, object_pairs_hook=unique)


def document(path):
    return decode(read(path))


def bound_document(plan, path):
    binding = next(f for f in plan['files'] if f['path'] == str(path))
    raw = read(path)
    require(len(raw) == binding['size'] and sha256(raw).hexdigest() == binding['sha256'], 'bound_document_changed')
    return decode(raw)


def load(path, digest):
    require(re.fullmatch('[a-f0-9]{64}', digest) is not None, 'manifest_hash_invalid')
    raw = read(path)
    require(sha256(raw).hexdigest() == digest, 'manifest_changed')
    plan = decode(raw)
    require(plan['schema'] == 'docker_daemon_security/1', 'schema_invalid')
    require(re.fullmatch('[a-zA-Z0-9._-]{1,80}', plan['operation_id']) is not None, 'operation_id_invalid')
    return plan


def mount_fence(plan):
    paths = [m['path'] for m in plan['mounts']]
    required = {plan['source_root'], str(ROOT), plan['docker_root'], '/srv/trading-bot/production-data', '/srv/trading-bot/backups', '/root/golden_bot'}
    require(len(set(paths)) == len(paths) and required <= set(paths), 'required_mount_binding_missing')
    for mount in plan['mounts']:
        info = os.stat(mount['path'])
        require((info.st_dev, info.st_ino) == (mount['device'], mount['inode']), 'mount_inode_changed')
        require(command(['/usr/bin/findmnt', '-n', '-o', 'ID', '-T', mount['path']]) == mount['mount_id'], 'mount_changed')


def health_contract(plan, containers):
    urls, probes = plan.get('health_urls', []), plan.get('health_probes', [])
    require(isinstance(urls, list) and isinstance(probes, list) and 1 <= len(urls) + len(probes) <= 16, 'health_targets_required')
    require(len(set(urls)) == len(urls) and len({(p['id'], p['url']) for p in probes}) == len(probes), 'duplicate_health_target')
    prior_running = {c['id'] for c in containers if c['running']}
    require(all(p['id'] in prior_running for p in probes), 'health_container_not_prior_running')
    for url in urls + [p['url'] for p in probes]:
        match = re.fullmatch(r'http://127\.0\.0\.1:([0-9]{1,5})/[a-zA-Z0-9/_-]+', url)
        require(match is not None and 1 <= int(match[1]) <= 65535, 'health_target_invalid')


def invariants(plan):
    require(os.geteuid() == 0 and Path('/proc/1/exe').readlink().name == 'systemd', 'native_root_required')
    require(not any(Path(p).exists() for p in ('/.dockerenv', '/run/.containerenv', '/run/systemd/container')), 'container_refused')
    for namespace in ('pid', 'user', 'mnt'):
        own, init = os.stat(f'/proc/self/ns/{namespace}'), os.stat(f'/proc/1/ns/{namespace}')
        require((own.st_dev, own.st_ino) == (init.st_dev, init.st_ino), 'namespace_mismatch')
    distro = dict(line.split('=', 1) for line in Path('/etc/os-release').read_text().splitlines() if '=' in line)
    require(distro.get('ID', '').strip('"') == 'ubuntu' and distro.get('VERSION_ID', '').strip('"') == '24.04', 'distro_mismatch')
    require(Path('/etc/machine-id').read_text().strip() == plan['machine_id'], 'host_mismatch')
    require(plan['deadline_utc'].endswith('Z') and datetime.now(timezone.utc) < datetime.fromisoformat(plan['deadline_utc'].replace('Z', '+00:00')), 'window_expired')
    require(not os.path.lexists(PENDING), 'pending_two_host_operation')
    require(command(['/usr/bin/git', '-C', plan['source_root'], 'rev-parse', 'HEAD']) == plan['source_revision'], 'source_revision_changed')
    required = {str(MARKER), str(BOTJ), str(Path(__file__).resolve()), *(str(Path(plan['source_root']) / 'deploy/maintenance' / name) for name in ('docker_daemon_security.yml', 'inventory.example.yml', 'ansible.cfg')), plan['runtime_snapshot'], plan['backup_receipt'], plan['offhost_receipt'], '/root/trading-bot/trading_bot/.local/security-maintenance-ansible-venv/bin/ansible-playbook'}
    require(required <= {f['path'] for f in plan['files']}, 'required_file_binding_missing')
    for binding in plan['files'] + plan['rollback_packages']:
        data = read(binding['path'], maximum=200 * 1024 * 1024, private=binding.get('private', True))
        require(len(data) == binding['size'] and sha256(data).hexdigest() == binding['sha256'], 'bound_file_changed')
    require({p['name'] for p in plan['rollback_packages']} == set(PACKAGES) and len(plan['rollback_packages']) == 5, 'rollback_inventory_invalid')
    for package in plan['rollback_packages']:
        require(command(['/usr/bin/dpkg-deb', '-f', package['path'], 'Package', 'Version']).splitlines() == [f"Package: {package['name']}", f"Version: {package['version']}"], 'rollback_package_mismatch')
    backup, offhost = bound_document(plan, plan['backup_receipt']), bound_document(plan, plan['offhost_receipt'])
    require(backup['status'] == 'ok' and set(backup['roles']) == {'foreign', 'iran'} and len(backup['results']) == 2, 'backup_incomplete')
    require({r['role'] for r in backup['results']} == {'foreign', 'iran'} and all(r['restore_smoke']['status'] == 'passed' and r['restore_smoke']['cleanup']['status'] == 'passed' for r in backup['results']), 'restore_proof_missing')
    for result in backup['results']:
        require(result['status'] == 'ok' and len(result['files']) == 4, 'backup_files_incomplete')
        for entry in result['files']:
            info = Path(plan.get('backup_file_paths', {}).get(entry['path'], entry['path'])).lstat()
            require(stat.S_ISREG(info.st_mode) and info.st_size == entry['bytes'] and info.st_uid == 0 and info.st_nlink == 1, 'backup_file_missing_or_changed')
    require(offhost['schema'] == 'security_maintenance_offhost_sources/1.0' and offhost['status'] == 'passed' and offhost['host_verification'] == 'strict' and offhost['fsync_completed'] is True and offhost['iran_backup_pull']['status'] == 'passed', 'offhost_proof_missing')
    require(len(offhost['files']) == 10 and {p['sha256'] for p in plan['rollback_packages']} <= {p['sha256'] for p in offhost['files']}, 'offhost_inventory_incomplete')
    mount_fence(plan)
    require(plan['units'] and len({u['name'] for u in plan['units']}) == len(plan['units']), 'unit_inventory_invalid')
    for unit in plan['units']:
        require(re.fullmatch(r'[a-zA-Z0-9_.@-]+\.(service|timer)', unit['name']) and unit['kind'] in {'timer', 'daemon', 'oneshot'}, 'unit_invalid')
        require(command(['/usr/bin/systemctl', 'show', unit['name'], '-p', 'FragmentPath', '--value']) == unit['fragment_path'], 'unit_identity_changed')
        require(sha256(read(unit['fragment_path'], private=False)).hexdigest() == unit['fragment_sha256'], 'unit_configuration_changed')
        require(command(['/usr/bin/systemctl', 'show', unit['name'], '-p', 'DropInPaths', '--value']).split() == [d['path'] for d in unit['dropins']], 'unit_dropins_changed')
        require(all(sha256(read(d['path'], private=False)).hexdigest() == d['sha256'] for d in unit['dropins']), 'unit_dropin_content_changed')
        if unit['kind'] == 'oneshot':
            require(command(['/usr/bin/systemctl', 'show', unit['name'], '-p', 'Type', '--value']) == 'oneshot', 'unit_type_mismatch')
        if unit['kind'] == 'timer':
            require(set(command(['/usr/bin/systemctl', 'show', unit['name'], '-p', 'Triggers', '--value']).split()) <= {u['name'] for u in plan['units']}, 'unrecorded_timer_target')
    for unit in plan['absent_units']:
        require(command(['/usr/bin/systemctl', 'show', unit, '-p', 'ActiveState', '--value']) in {'inactive', 'failed'}, 'private_primary_present')
    require(set(plan['datastore_ids']) and all(re.fullmatch('[a-f0-9]{64}', value) for value in plan['datastore_ids']), 'datastore_inventory_invalid')
    require(len(set(plan['datastore_ids'])) == len(plan['datastore_ids']) == len(plan['datastore_probes']) and {p['id'] for p in plan['datastore_probes']} == set(plan['datastore_ids']), 'datastore_probes_incomplete')
    require(all(p['kind'] in {'postgres', 'redis'} and (p['kind'] == 'redis' or all(re.fullmatch('[a-zA-Z0-9_]{1,63}', p[k]) for k in ('user', 'database'))) for p in plan['datastore_probes']), 'datastore_probe_invalid')
    health_contract(plan, bound_document(plan, plan['runtime_snapshot'])['containers'])
    command(['/usr/bin/python3', '-I', '-B', '-c', 'import apt, apt_pkg'])
    simulation = command(['/usr/bin/apt-get', '--simulate', '--no-remove', 'install', *(f'{name}={version}' for name, version in PACKAGES.items())])
    require(not re.search(r'^Remv ', simulation, re.MULTILINE) and set(re.findall(r'^Inst ([a-z0-9.+-]+)', simulation, re.MULTILINE)) <= set(PACKAGES), 'apt_transaction_scope_changed')


def owners(plan, digest):
    reservation = document(RESERVATION)
    require(reservation['schema'] == 'docker_daemon_maintenance_guard/1' and reservation['operation_id'] == plan['operation_id'] and reservation['manifest_sha256'] == digest, 'reservation_mismatch')
    with open('/proc/locks', 'rb') as stream:
        raw = stream.read(2 * 1024 * 1024 + 1)
    require(len(raw) <= 2 * 1024 * 1024, 'lock_inventory_bound')
    locks = raw.decode('ascii')
    for path in (MARKER, SOURCE, RESERVATION):
        info = path.lstat()
        key = f'{os.major(info.st_dev):02x}:{os.minor(info.st_dev):02x}:{info.st_ino}'
        require(any('FLOCK' in line and 'WRITE' in line and str(reservation['pid']) == line.split()[4] and key == line.split()[5] for line in locks.splitlines() if len(line.split()) >= 8), 'operation_fence_lost')


def canonical_mounts(mounts):
    rows = [{key: mount[key] for key in ('Destination', 'Mode', 'Propagation', 'RW', 'Source', 'Type')} for mount in mounts]
    require(len({row['Destination'] for row in rows}) == len(rows), 'duplicate_mount_destination')
    return sorted(rows, key=lambda row: row['Destination'])


def runtime(plan, phase, recovery=False):
    baseline = bound_document(plan, plan['runtime_snapshot'])
    require(baseline['schema'] == 'security_runtime_snapshot/1.0' and baseline['containers'], 'snapshot_invalid')
    health_contract(plan, baseline['containers'])
    expected = {c['id']: c for c in baseline['containers']}
    require(len(expected) == len(baseline['containers']), 'duplicate_snapshot_container')
    require(set(command(['/usr/bin/docker', 'container', 'ls', '-aq', '--no-trunc']).splitlines()) == set(expected), 'container_set_changed')
    require(set(plan['datastore_ids']) <= {c['id'] for c in expected.values() if c['running']}, 'datastore_not_prior_running')
    require({c['project'] for c in expected.values()} == set(plan['managed_project_labels']), 'container_scope_changed')
    require(command(['/usr/bin/docker', 'info', '--format', '{{.DockerRootDir}}']) == plan['docker_root'], 'docker_data_root_changed')
    for ident, before in expected.items():
        current = json.loads(command(['/usr/bin/docker', 'inspect', '--format', INSPECT, ident]))
        require(all(current[key] == before[key] for key in ('id', 'name', 'image', 'project')), 'container_identity_changed')
        require(canonical_mounts(current['mounts']) == canonical_mounts(before['mounts']), 'container_identity_changed')
        allowed = [before['restart_policy']] if phase in {'preflight', 'complete'} else [{'Name': 'no', 'MaximumRetryCount': 0}]
        if phase == 'recoverable':
            allowed.append(before['restart_policy'])
        require(current['restart_policy'] in allowed, 'restart_policy_changed')
        if phase != 'recoverable':
            running = before['running'] if phase in {'preflight', 'healthy', 'complete'} else ident in plan['datastore_ids'] if phase == 'datastores' else False
            require(current['running'] == running, 'container_running_state_changed')
        if (phase in {'healthy', 'complete'} and before['running']) or (phase == 'datastores' and ident in plan['datastore_ids']):
            require(current['health'] in {None, 'healthy'}, 'container_unhealthy')
    if phase in {'datastores', 'healthy', 'complete'}:
        for probe in plan['datastore_probes']:
            args = ['redis-cli', 'ping'] if probe['kind'] == 'redis' else ['pg_isready', '-U', probe['user'], '-d', probe['database']]
            output = command(['/usr/bin/docker', 'exec', probe['id'], *args])
            require(probe['kind'] != 'redis' or output == 'PONG', 'redis_not_ready')
    if phase in {'preflight', 'complete'}:
        for unit in plan['units']:
            require(command(['/usr/bin/systemctl', 'show', unit['name'], '-p', 'UnitFileState', '--value']) == unit['enabled'], 'unit_enablement_changed')
            if unit['kind'] != 'oneshot':
                require((command(['/usr/bin/systemctl', 'show', unit['name'], '-p', 'ActiveState', '--value']) == 'active') == unit['active'], 'unit_intent_changed')
    if phase == 'preflight' or (phase in {'healthy', 'complete'} and not recovery):
        versions = dict(line.split('=', 1) for line in command(['/usr/bin/dpkg-query', '-W', '-f=${Package}=${Version}\\n', *PACKAGES]).splitlines())
        required = {p['name']: p['version'] for p in plan['rollback_packages']} if phase == 'preflight' else PACKAGES
        require(versions == required, 'installed_package_versions_mismatch')
    if phase in {'healthy', 'complete'} and not recovery:
        require(command(['/usr/bin/docker', 'version', '--format', '{{.Server.Version}}']) == '29.9.2', 'docker_daemon_version_mismatch')
        require(re.search(r'^Server:\s*\n\s*Version:\s*v?2\.4\.2\s*$', command(['/usr/bin/ctr', '--address', '/run/containerd/containerd.sock', 'version']), re.MULTILINE) is not None, 'containerd_server_version_mismatch')
    if phase in {'healthy', 'complete'}:
        for probe in plan.get('health_probes', []):
            try:
                status = command(['/usr/bin/docker', 'exec', probe['id'], '/usr/local/bin/python', '-I', '-B', '-c', CONTAINER_HTTP_PROBE, probe['url']])
            except (OSError, subprocess.SubprocessError):
                raise Refusal('container_health_probe_failed') from None
            require(status == '200', 'container_health_probe_failed')
    return baseline


def validate(plan, digest, phase, recovery=False):
    invariants(plan)
    owners(plan, digest)
    if phase in {'invariants', 'quiesced', 'datastores', 'healthy'}:
        for unit in plan['units']:
            require(command(['/usr/bin/systemctl', 'show', unit['name'], '-p', 'ActiveState', '--value']) in {'inactive', 'failed'}, 'quiescence_lost')
    if phase != 'invariants':
        return runtime(plan, phase, recovery)


def write_all(fd, raw):
    while raw:
        count = os.write(fd, raw)
        require(count > 0, 'short_write_no_progress')
        raw = raw[count:]


def publish_noreplace(temporary, path):
    # Same reviewed Linux renameat2 pattern as identity recovery; never call
    # that tool's recovery/re-attestation or change its marker/journal.
    function = getattr(ctypes.CDLL(None, use_errno=True), 'renameat2', None)
    require(function is not None, 'atomic_noreplace_unavailable')
    function.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
    function.restype = ctypes.c_int
    require(function(-100, os.fsencode(temporary), -100, os.fsencode(path), 1) == 0, 'reservation_already_exists')


def receipt(plan, digest, status):
    path = RESERVATION.with_name(plan['operation_id'] + '.daemon-maintenance-result.json')
    fd, temporary = tempfile.mkstemp(prefix='.daemon-result.', dir=path.parent)
    try:
        write_all(fd, (json.dumps({'schema': 'docker_daemon_maintenance_result/1', 'operation_id': plan['operation_id'], 'manifest_sha256': digest, 'status': status, 'reboot_performed': False, 'authority_changed': False, 'recorded_at_utc': datetime.now(timezone.utc).isoformat()}) + '\n').encode())
        os.fsync(fd)
        publish_noreplace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        os.close(fd)
        Path(temporary).unlink(missing_ok=True)


def execute_ansible(argv, env, deadline, *, lock_fds=()):
    """Never release fences while a timed-out Ansible subprocess can keep writing."""
    seconds = (datetime.fromisoformat(deadline.replace('Z', '+00:00')) - datetime.now(timezone.utc)).total_seconds()
    require(seconds > 0, 'window_expired')
    child = subprocess.Popen(argv, env=env, start_new_session=True, pass_fds=tuple(lock_fds))
    try:
        return child.wait(timeout=min(seconds, 3600))
    finally:
        # This is our dedicated session, never a broad process-name kill.
        try:
            os.killpg(child.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        else:
            stop = time.monotonic() + 10
            while time.monotonic() < stop:
                child.poll()  # Reap our own exited leader; do not mistake it for a live writer.
                try:
                    os.killpg(child.pid, 0)
                except ProcessLookupError:
                    break
                time.sleep(0.05)
            else:
                os.killpg(child.pid, signal.SIGKILL)
        child.wait(timeout=10)


def run(plan, path, digest, apply):
    invariants(plan)
    with ExitStack() as stack:
        lock_fds = []
        for name in (MARKER, SOURCE):
            fd = os.open(name, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK)
            stack.callback(os.close, fd)
            lock_fds.append(fd)
            read(name, maximum=8192)
            require(identity(os.fstat(fd)) == identity(name.lstat()), 'lock_inode_changed')
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        require(not os.path.lexists(RESERVATION), 'private_primary_operation_present')
        invariants(plan)
        runtime(plan, 'preflight')
        if not apply:
            print(json.dumps({'status': 'preflight_passed', 'runtime_changed': False}))
            return
        require(not os.path.lexists(RESERVATION.with_name(plan['operation_id'] + '.daemon-maintenance-result.json')), 'operation_already_recorded')
        private_parent(RESERVATION)
        fd, temporary = tempfile.mkstemp(prefix='.daemon-maintenance.', dir=RESERVATION.parent)
        lock_fds.append(fd)
        stack.callback(os.close, fd)
        stack.callback(lambda: Path(temporary).unlink(missing_ok=True))
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        payload = {'schema': 'docker_daemon_maintenance_guard/1', 'pid': os.getpid(), 'operation_id': plan['operation_id'], 'manifest_sha256': digest}
        write_all(fd, (json.dumps(payload, sort_keys=True) + '\n').encode())
        os.fsync(fd)
        publish_noreplace(temporary, RESERVATION)
        directory = os.open(RESERVATION.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        stack.callback(os.close, directory)
        os.fsync(directory)
        owned = identity(RESERVATION.stat())
        invoked = False
        try:
            validate(plan, digest, 'preflight')
            source = Path(plan['source_root'])
            binary = Path('/root/trading-bot/trading_bot/.local/security-maintenance-ansible-venv/bin/ansible-playbook')
            require(str(binary) in {f['path'] for f in plan['files']}, 'ansible_binary_unbound')
            variables = {'maintenance_manifest': str(path), 'maintenance_sha256': digest, 'maintenance_apply': apply}
            env = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C.UTF-8', 'DOCKER_HOST': 'unix:///var/run/docker.sock', 'ANSIBLE_LOCAL_TEMP': '/root/trading-bot/trading_bot/.local/security-maintenance-ansible-tmp', 'ANSIBLE_HOME': '/root/trading-bot/trading_bot/.local/security-maintenance-ansible-home', 'ANSIBLE_CONFIG': str(source / 'deploy/maintenance/ansible.cfg')}
            invoked = True
            returncode = execute_ansible([str(binary), '-i', str(source / 'deploy/maintenance/inventory.example.yml'), str(source / 'deploy/maintenance/docker_daemon_security.yml'), '-e', json.dumps(variables)], env, plan['deadline_utc'], lock_fds=lock_fds)
            validate(plan, digest, 'complete', recovery=returncode != 0)
            require(identity(RESERVATION.stat()) == owned, 'reservation_changed')
            receipt(plan, digest, 'completed' if returncode == 0 else 'failed_recovered')
            RESERVATION.unlink()
            os.fsync(directory)
            require(returncode == 0, 'ansible_failed_runtime_restored')
        except BaseException:
            # Intentionally retain the exclusive reservation after an unsafe or
            # interrupted operation. No automated adoption, downgrade or retry.
            if not invoked and identity(RESERVATION.stat()) == owned:
                RESERVATION.unlink()
                os.fsync(directory)
            elif os.path.lexists(RESERVATION) and identity(RESERVATION.stat()) == owned:
                receipt(plan, digest, 'failed_closed')
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('run', 'check'))
    parser.add_argument('--manifest', required=True, type=Path)
    parser.add_argument('--manifest-sha256', required=True)
    parser.add_argument('--phase', choices=('preflight', 'invariants', 'quiesced', 'datastores', 'recoverable', 'healthy', 'complete'), default='preflight')
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--recovery', action='store_true', help='Check recovered old runtime; never claim patched')
    args = parser.parse_args()
    try:
        plan = load(args.manifest, args.manifest_sha256)
        if args.mode == 'run':
            run(plan, args.manifest, args.manifest_sha256, args.apply)
        else:
            baseline = validate(plan, args.manifest_sha256, args.phase, recovery=args.recovery)
            print(json.dumps({'status': 'passed', 'plan': plan, 'containers': baseline['containers'] if baseline else []}))
        return 0
    except BaseException as exc:
        print(json.dumps({'status': 'refused', 'error_class': type(exc).__name__, 'code': str(exc) if isinstance(exc, Refusal) else 'unexpected_failure'}), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
