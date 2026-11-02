#!/usr/bin/env python3
"""One fixed Bot kernel reboot, owned by a durable external Web controller.

This tool never repairs/adopts the historic AUTHORITY_TRANSFERRED marker. It
holds its CURRENT inode and source fence around each native mutating phase;
a new private-primary reservation survives the single authorized reboot.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
from datetime import datetime, timezone
import fcntl
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shlex
import signal
import stat
import sys
import tempfile
from types import ModuleType
import uuid

PROJECT = Path(__file__).resolve().parents[2]
HERE = PROJECT / 'deploy/maintenance'
HELPER = PROJECT / 'scripts/validate_docker_daemon_maintenance.py'
HELPER_SHA = 'fa2bf85cd50378bb999a7d8aabbd965a174722605c943c59ce6da56f68a74901'
NATIVE_ANSIBLE = PROJECT / '.local/security-maintenance-ansible-venv/bin/ansible-playbook'
PREVIOUS = PROJECT / '.local/security-maintenance-20261023-daemon-plan.json'
OPERATION = 'security-kernel-reboot-20261023-02'
CUTOFF = '2026-10-24T00:30:00Z'
DEADLINE = '2026-10-24T01:30:00Z'
OLD_KERNEL = '6.8.0-138-generic'
NEW_KERNEL = '6.8.0-147-generic'
BOOT_ROOT = Path('/boot')
SOURCE_NAMES = ('reboot_guard.py', 'reboot_controller.py', 'reboot_fetch_wheel.py', 'reboot_bootstrap.yml',
                'reboot_native.yml', 'reboot_external.yml', 'reboot_inventory.yml', 'reboot_ansible.cfg',
                'reboot_requirements.txt', 'reboot_wheels.yml')
UNITS = {
    'coin-group-event-telegram.service', 'coin-group-event-telegram.timer',
    'coin-intelligence-production-snapshot-relay.service', 'coin-intelligence-production-snapshot-relay.timer',
    'coin-intelligence-staging-market-input-bridge.service', 'coin-intelligence-staging-market-input-bridge.timer',
    'coin-intelligence-staging-snapshot-publish.service', 'coin-intelligence-staging-snapshot-publish.timer',
    'coin-intelligence-staging-snapshot-relay.service', 'coin-intelligence-staging-snapshot-relay.timer',
    'coin-private-shadow-legacy-estimator-bridge.service', 'coin-private-shadow-legacy-estimator-bridge.timer',
    'coin-public-market-telegram.service', 'coin-rate-estimator-dashboard.service', 'golden_bot.service',
    'market-pipeline-watchdog.service', 'market-pipeline-watchdog.timer',
    'trading-bot-private-gold-collector.service', 'trading-bot-private-gold-collector.timer',
    'trading-bot-sync-health-sampler.service', 'trading-bot-sync-health-sampler.timer',
}


def base_module():
    # Read public source only; no application imports or production env loading.
    fd = os.open(HELPER, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1 or info.st_mode & 0o022 or info.st_size > 65536:
            raise RuntimeError('untrusted_reviewed_helper')
        raw = os.read(fd, 65537)
        identity = lambda s: (s.st_dev, s.st_ino, s.st_mode, s.st_uid, s.st_gid, s.st_nlink, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        if len(raw) != info.st_size or identity(info) != identity(os.fstat(fd)) or identity(info) != identity(HELPER.lstat()):
            raise RuntimeError('reviewed_helper_changed_during_read')
    finally:
        os.close(fd)
    if sha256(raw).hexdigest() != HELPER_SHA:
        raise RuntimeError('reviewed_helper_changed')
    module = ModuleType('_reviewed_daemon_primitives')
    module.__file__ = str(HELPER)
    # Never reopen after hashing: execute precisely the verified stable bytes.
    exec(compile(raw, str(HELPER), 'exec'), module.__dict__)
    return module


B = base_module()
ROOT = B.ROOT
STATE = ROOT / 'release-control' / (OPERATION + '.state.json')
RESULT = ROOT / 'release-control' / (OPERATION + '.result.json')
MANIFEST = ROOT / 'release-control' / (OPERATION + '.manifest.json')
TOKEN = ROOT / 'release-control' / (OPERATION + '.quiescence.json')
GOLDEN_INHIBITOR = Path('/etc/systemd/system/golden_bot.service.d/91-security-kernel-reboot-task02.conf')
GOLDEN_INHIBITOR_BYTES = ('# Owned temporary task02 reboot guard; exact-file cleanup only.\n'
                          '[Unit]\nRequiresMountsFor=' + str(ROOT) + '\n'
                          'AssertPathIsMountPoint=' + str(ROOT) + '\n'
                          'ConditionPathExists=!' + str(TOKEN) + '\n').encode()
CONTROLLER_READY = PROJECT / '.local/security-maintenance-20261023-reboot-controller-ready.json'
INTEGRATION = PROJECT / '.local/two-site-refactor/integration'
MONITOR = INTEGRATION / 'scripts/two_site_autopilot_monitor.sh'
MONITOR_SHA = '85c2ef7598339a4753d1678e15f496b868d267c3a59a449893dd6887b47bde26'
STOP = INTEGRATION / '.local/two-site-refactor/autopilot/STOP'
AUTOPILOT_STATUS = STOP.parent / 'status.json'
require = B.require


def now():
    return datetime.now(timezone.utc)


def deadline(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def boot_id():
    with open('/proc/sys/kernel/random/boot_id', 'r', encoding='ascii') as stream:
        value = stream.read(65).strip()
    require(re.fullmatch('[0-9a-f-]{36}', value) is not None, 'invalid_boot_id')
    return value


def atomic(path, payload, exclusive=False):
    B.private_parent(path)
    fd, temporary = tempfile.mkstemp(prefix='.kernel-reboot.', dir=path.parent)
    try:
        B.write_all(fd, (json.dumps(payload, sort_keys=True) + '\n').encode())
        os.fsync(fd)
        if exclusive:
            B.publish_noreplace(temporary, path)
        else:
            B.read(path)  # Refuse a foreign/symlink/unsafe existing journal.
            os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try: os.fsync(directory)
        finally: os.close(directory)
    finally:
        os.close(fd)
        Path(temporary).unlink(missing_ok=True)


def file_binding(path, private=True):
    raw = B.read(path, maximum=2 * 1024 * 1024, private=private)
    return {'path': str(path), 'size': len(raw), 'sha256': sha256(raw).hexdigest(),
            'inode': Path(path).lstat().st_ino, 'private': private}


def mount_binding(path):
    directory = Path(path)
    info = directory.lstat()
    require(directory.resolve(strict=True) == directory and stat.S_ISDIR(info.st_mode) and
            info.st_uid == os.geteuid() and not info.st_mode & 0o022, 'unsafe_mount_destination')
    value = B.decode(B.command(['/usr/bin/findmnt', '--json', '--target', path,
                                  '--output', 'TARGET,SOURCE,FSTYPE,UUID']))['filesystems']
    require(len(value) == 1, 'ambiguous_mount')
    value = value[0]
    # Device aliases and mount IDs can legitimately change across boot.
    return {'path': path, 'target': value['target'], 'uuid': value['uuid'],
            'root': value['source'].partition('[')[2], 'fstype': value['fstype'],
            'inode': info.st_ino}


def properties(unit):
    raw = B.command(['/usr/bin/systemctl', 'show', unit,
                     '--property=FragmentPath,DropInPaths,UnitFileState,ActiveState,Type,Triggers'])
    return dict(line.split('=', 1) for line in raw.splitlines() if '=' in line)


def native():
    require(os.geteuid() == 0 and Path('/proc/1/exe').readlink().name == 'systemd', 'native_root_required')
    require(not any(Path(p).exists() for p in ('/.dockerenv', '/run/.containerenv', '/run/systemd/container')), 'container_refused')
    for namespace in ('pid', 'user', 'mnt'):
        require(os.readlink('/proc/self/ns/' + namespace) == os.readlink('/proc/1/ns/' + namespace), 'namespace_mismatch')
    links = B.decode(B.command(['/usr/sbin/ip', '-j', 'address']))
    require(any(v.get('local') == '65.109.216.187' for link in links for v in link.get('addr_info', [])), 'wrong_native_host')


def launcher_inventory(raw):
    """Classify only project launcher tokens; never retain/display private cron."""
    result = []
    for line in raw.splitlines():
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        if (not re.match(r'^\s*PYTHONPATH\s*=', line) and
                not any(word in line for word in ('trading-bot', 'trading_bot', 'golden_bot', 'two-site-refactor'))):
            continue
        lexer = shlex.shlex(line, posix=True, punctuation_chars=';&|<>')
        lexer.whitespace_split = True
        tokens = list(lexer)
        if tokens[:1] == ['PYTHONPATH=' + str(INTEGRATION)]:
            require(len(tokens) == 1, 'unknown_project_environment')
            result.append({'kind': 'monitor_pythonpath', 'value': str(INTEGRATION)})
            continue
        if tokens[:1] == ['@reboot']:
            require(len(tokens) == 4 and Path(tokens[1]).name == 'systemctl' and
                    tokens[2:] == ['restart', 'golden_bot'], 'unknown_reboot_launcher')
            result.append({'kind': 'golden', 'schedule': '@reboot', 'verb': 'restart'})
        else:
            command = tokens[5:]
            require(tokens[:5] == ['*/15', '*', '*', '*', '*'] and len(command) == 19 and
                    command[0] in ('tmux', '/usr/bin/tmux') and
                    command[1:5] == ['set-environment', '-g', 'PYTHONPATH', str(INTEGRATION)] and
                    command[5:11] == ['>', '/dev/null', '2', '>&', '1', ';'] and
                    command[11:19] == ['/usr/bin/bash', str(MONITOR), '--quota-wait-enabled',
                                       '>', '/dev/null', '2', '>&', '1'], 'unknown_project_cron_launcher')
            result.append({'kind': 'monitor', 'schedule': '*/15 * * * *', 'script': str(MONITOR)})
    require(sorted(value['kind'] for value in result) == ['golden', 'monitor', 'monitor_pythonpath'], 'project_launcher_scope_changed')
    return sorted(result, key=lambda value: value['kind'])


def admission(plan=None):
    require(B.read(STOP, maximum=32) == b'STOP\n', 'automation_stop_not_durable')
    status = B.decode(B.read(AUTOPILOT_STATUS, private=False))
    require(status['state'] == 'BLOCKED' and status['cycle'] == 8, 'automation_pause_changed')
    require(sha256(B.read(MONITOR, private=False)).hexdigest() == MONITOR_SHA, 'monitor_stop_contract_changed')
    launchers = launcher_inventory(B.command(['/usr/bin/crontab', '-l']))
    if plan is not None:
        require(launchers == plan['launchers'], 'launcher_admission_changed')
        backup = B.bound_document(plan, plan['backup_receipt'])
        require(backup['status'] == 'ok' and set(backup['roles']) == {'foreign', 'iran'} and
                {v['role'] for v in backup['results']} == {'foreign', 'iran'}, 'backup_not_complete')
        for item in backup['results']:
            smoke = item['restore_smoke']
            cleanup = smoke['cleanup']
            require(item['status'] == 'ok' and smoke['status'] == 'passed' and
                    cleanup['status'] == 'passed' and cleanup['container_absent'] is True and
                    cleanup['named_volume_absent'] is True and cleanup['owned_volumes_absent'] is True,
                    'restore_smoke_not_proven')
        offhost = B.bound_document(plan, plan['offhost_receipt'])
        require(offhost['schema'] == 'security_maintenance_offhost_sources/1.0' and
                offhost['status'] == 'passed' and offhost['fsync_completed'] is True and
                offhost['host_verification'] == 'strict' and offhost['peer'] == '65.109.220.59:37067' and
                len(offhost['files']) == 10,
                'offhost_backup_not_proven')
    return launchers


def boot_gates():
    # Reuse the already-reviewed READ-ONLY verify_setup phase, not setup/reload.
    script = ('import json,pathlib,sys,yaml; p=pathlib.Path(sys.argv[1]); '
              'play=yaml.safe_load((p/"boot_mount_gates.yml").read_bytes())[0]; '
              'inventory=yaml.safe_load((p/"boot_mount_inventory.yml").read_bytes()); '
              'mapping=inventory["all"]["vars"]["boot_mount_service_paths"]; '
              'sys.argv=["boot_gate_verify","verify_setup",json.dumps(mapping)]; '
              'exec(compile(play["vars"]["boot_mount_validator"],"reviewed_boot_mount_validator","exec"))')
    B.command([str(NATIVE_ANSIBLE.parent / 'python'), '-I', '-B', '-c', script, str(HERE)])


def kernel_artifacts():
    parent = BOOT_ROOT.lstat()
    require(BOOT_ROOT.resolve(strict=True) == BOOT_ROOT and stat.S_ISDIR(parent.st_mode) and
            parent.st_uid == os.geteuid() and not parent.st_mode & 0o022, 'unsafe_boot_directory')
    result = {}
    for kernel in (OLD_KERNEL, NEW_KERNEL):
        for prefix in ('vmlinuz-', 'initrd.img-'):
            path = BOOT_ROOT / (prefix + kernel)
            info = path.lstat()
            require(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid() and info.st_nlink == 1 and
                    info.st_size > 0 and not info.st_mode & 0o022, 'boot_artifact_missing_or_unsafe')
            result[str(path)] = [info.st_ino, info.st_size, info.st_mode, info.st_uid, info.st_gid,
                                 info.st_mtime_ns, info.st_ctime_ns]
    return result


def grub_gate():
    artifacts = kernel_artifacts()
    values = {}
    for line in B.read('/etc/default/grub', maximum=65536, private=False).decode().splitlines():
        stripped = line.strip()
        if stripped.startswith(('GRUB_DEFAULT=', 'GRUB_SAVEDEFAULT=')):
            key, value = stripped.split('=', 1)
            require(key not in values, 'ambiguous_grub_default')
            values[key] = shlex.split(value)
    require(values.get('GRUB_DEFAULT') == ['0'] and 'GRUB_SAVEDEFAULT' not in values, 'unexpected_grub_default')
    entries = re.findall(r'^\s*(linux|linuxefi|initrd|initrdefi)\s+([^\n]+)',
                         B.read('/boot/grub/grub.cfg', private=False).decode(), flags=re.MULTILINE)
    require(len(entries) >= 2 and entries[0][0] in {'linux', 'linuxefi'} and
            shlex.split(entries[0][1])[0] == '/boot/vmlinuz-' + NEW_KERNEL and
            entries[1][0] in {'initrd', 'initrdefi'} and
            shlex.split(entries[1][1]) == ['/boot/initrd.img-' + NEW_KERNEL], 'grub_first_kernel_or_initrd_not_target')
    environment = B.command(['/usr/bin/grub-editenv', '/boot/grub/grubenv', 'list'])
    require(not any(line.startswith('next_entry=') for line in environment.splitlines()), 'grub_next_entry_override')
    return artifacts


def expected_files():
    private = [B.MARKER, B.BOTJ, B.SOURCE, PREVIOUS, CONTROLLER_READY, STOP,
               PROJECT / '.local/security-maintenance-20261023-runtime-before.json',
               ROOT / 'production-backups/evidence/security-host-maintenance-20261023.json',
               PROJECT / '.local/security-maintenance-20261023-offhost.json']
    public = [HELPER, NATIVE_ANSIBLE, AUTOPILOT_STATUS, MONITOR, Path('/etc/default/grub'),
              Path('/boot/grub/grub.cfg'), *(HERE / name for name in SOURCE_NAMES),
              HERE / 'boot_mount_gates.yml', HERE / 'boot_mount_inventory.yml',
              HERE / 'controller_access.yml', HERE / 'controller_inventory.example.yml', HERE / 'ansible.cfg']
    return {str(path): True for path in private} | {str(path): False for path in public}


def validate_scope(plan):
    paths = [value['path'] for value in plan['mounts']]
    required_mounts = {str(PROJECT), str(ROOT), '/srv/trading-bot/production-data',
                       '/srv/trading-bot/production-data/docker-data', '/srv/trading-bot/backups',
                       '/root/golden_bot', '/srv/trading-bot/staging-data'}
    require(len(paths) == len(set(paths)) == 7 and set(paths) == required_mounts, 'required_mount_binding_missing')
    files = plan['files']
    require(len(files) == len({value['path'] for value in files}) and
            {value['path']: value['private'] for value in files} == expected_files(), 'required_file_binding_missing')
    original = B.bound_document(plan, str(PREVIOUS))
    for key in ('source_root', 'docker_root', 'runtime_snapshot', 'backup_receipt', 'offhost_receipt',
                'datastore_ids', 'datastore_probes', 'health_urls', 'health_probes', 'managed_project_labels', 'absent_units'):
        require(plan.get(key) == original.get(key), 'baseline_runtime_contract_changed')
    require(plan['source_root'] == str(PROJECT) and
            plan['docker_root'] == '/srv/trading-bot/production-data/docker-data', 'canonical_source_or_docker_root_changed')
    require(len(plan['datastore_ids']) == len(set(plan['datastore_ids'])) == len(plan['datastore_probes']) == 4 and
            {item['id'] for item in plan['datastore_probes']} == set(plan['datastore_ids']) and
            sorted(item['kind'] for item in plan['datastore_probes']) == ['postgres', 'postgres', 'redis', 'redis'],
            'datastore_probe_scope_changed')
    require(len(plan['health_probes']) >= 1, 'actual_api_health_probe_required')
    require({u['name']: u['kind'] for u in plan['units']} ==
            {u['name']: u['kind'] for u in original['units']}, 'baseline_unit_role_changed')


def make_manifest():
    native()
    require(now() < deadline(CUTOFF), 'mutation_cutoff')
    require(B.command(['/usr/bin/uname', '-r']) == OLD_KERNEL, 'unexpected_initial_kernel')
    require(not any(os.path.lexists(p) for p in (B.RESERVATION, B.PENDING, STATE, RESULT, MANIFEST, TOKEN, GOLDEN_INHIBITOR)), 'existing_operation_or_result')
    artifacts = grub_gate()
    plan = B.document(PREVIOUS)
    require(plan['schema'] == 'docker_daemon_security/1' and {u['name'] for u in plan['units']} == UNITS, 'baseline_scope_mismatch')
    plan.update(schema='security_kernel_reboot/1', operation_id=OPERATION, nonce=str(uuid.uuid4()),
                deadline_utc=DEADLINE, cutoff_utc=CUTOFF, before_boot_id=boot_id(),
                old_kernel=OLD_KERNEL, target_kernel=NEW_KERNEL,
                boot_artifacts=artifacts,
                source_revision=B.command(['/usr/bin/git', '-C', str(PROJECT), 'rev-parse', 'HEAD']))
    plan['launchers'] = admission()
    ready = B.document(CONTROLLER_READY)
    require(ready['schema'] == 'security_reboot_controller_ready/1' and ready['status'] == 'ready' and
            ready['task_id'] == 'host-patch-02' and ready['ansible_version'] == '2.21.5' and
            ready['source_revision'] == plan['source_revision'] and ready['strict_reverse_ssh'] is True,
            'external_controller_not_ready')
    require(B.command(['/usr/bin/dpkg-query', '-W', '-f=${db:Status-Status}', 'linux-image-' + NEW_KERNEL]) == 'installed', 'target_kernel_not_installed')
    for unit in plan['units']:
        state = properties(unit['name'])
        require(state['UnitFileState'] in {'enabled', 'disabled', 'static'}, 'unhandled_unit_enablement')
        require(unit['kind'] != 'oneshot' or state['UnitFileState'] == 'static', 'oneshot_enablement_mismatch')
        unit.update(enabled=state['UnitFileState'], active=state['ActiveState'] == 'active',
                    fragment_path=state['FragmentPath'],
                    fragment_sha256=sha256(B.read(state['FragmentPath'], private=False)).hexdigest(),
                    dropins=[{'path': path, 'sha256': sha256(B.read(path, private=False)).hexdigest()}
                             for path in state['DropInPaths'].split()])
        if unit['kind'] == 'timer':
            require(set(state['Triggers'].split()) <= UNITS, 'unrecorded_timer_target')
    plan['files'] = [file_binding(Path(path), private) for path, private in expected_files().items()]
    plan['mounts'] = [mount_binding(item['path']) for item in plan['mounts']]
    plan['mounts'].append(mount_binding('/srv/trading-bot/staging-data'))
    require(len(B.bound_document(plan, plan['runtime_snapshot'])['containers']) == 15, 'container_inventory_not_fifteen')
    invariants(plan, before=True)
    B.runtime(plan, 'complete')
    atomic(MANIFEST, plan, exclusive=True)
    print(json.dumps({'status': 'manifest_created', 'path': str(MANIFEST), 'sha256': sha256(B.read(MANIFEST)).hexdigest(), 'nonce': plan['nonce']}))


def load(path, digest):
    require(path == MANIFEST and re.fullmatch('[0-9a-f]{64}', digest), 'manifest_scope')
    raw = B.read(path)
    require(sha256(raw).hexdigest() == digest, 'manifest_changed')
    plan = B.decode(raw)
    require(plan['schema'] == 'security_kernel_reboot/1' and plan['operation_id'] == OPERATION,
            'wrong_reboot_schema')
    require(plan['cutoff_utc'] == CUTOFF and plan['deadline_utc'] == DEADLINE and
            plan['old_kernel'] == OLD_KERNEL and plan['target_kernel'] == NEW_KERNEL,
            'fixed_window_or_kernel_changed')
    require(set(u['name'] for u in plan['units']) == UNITS and len(plan['units']) == len(UNITS), 'unit_scope')
    require(re.fullmatch('[0-9a-f-]{36}', plan['nonce']), 'invalid_nonce')
    return plan


def invariants(plan, before):
    native()
    validate_scope(plan)
    require(now() < deadline(CUTOFF if before else DEADLINE), 'window_expired')
    require(Path('/etc/machine-id').read_text().strip() == plan['machine_id'], 'machine_changed')
    require(B.command(['/usr/bin/git', '-C', str(PROJECT), 'rev-parse', 'HEAD']) == plan['source_revision'], 'source_head_changed')
    require(not os.path.lexists(B.PENDING), 'pending_two_host_operation')
    admission(plan)
    require(grub_gate() == plan['boot_artifacts'], 'boot_artifact_metadata_changed')
    for binding in plan['files']:
        raw = B.read(binding['path'], private=binding['private'])
        require(len(raw) == binding['size'] and sha256(raw).hexdigest() == binding['sha256'], 'bound_file_changed')
        require(Path(binding['path']).lstat().st_ino == binding['inode'], 'bound_file_inode_changed')
    for binding in plan['mounts']:
        require(mount_binding(binding['path']) == binding, 'mount_uuid_source_or_inode_changed')
    boot_gates()
    for unit in plan['units']:
        current = properties(unit['name'])
        actual_dropins = current['DropInPaths'].split()
        expected_dropins = [d['path'] for d in unit['dropins']]
        if unit['name'] == 'golden_bot.service' and os.path.lexists(GOLDEN_INHIBITOR):
            own_reservation(plan, sha256(B.read(MANIFEST)).hexdigest())
            require(B.read(GOLDEN_INHIBITOR, private=False) == GOLDEN_INHIBITOR_BYTES, 'golden_inhibitor_changed')
            expected_dropins = sorted(expected_dropins + [str(GOLDEN_INHIBITOR)])
        require(current['FragmentPath'] == unit['fragment_path'] and actual_dropins == expected_dropins, 'unit_definition_changed')
        require(sha256(B.read(unit['fragment_path'], private=False)).hexdigest() == unit['fragment_sha256'], 'unit_definition_changed')
        require(all(sha256(B.read(d['path'], private=False)).hexdigest() == d['sha256'] for d in unit['dropins']), 'unit_definition_changed')
    expected_boot = boot_id() == plan['before_boot_id']
    require(expected_boot if before else not expected_boot, 'boot_identity_mismatch')
    require(B.command(['/usr/bin/uname', '-r']) == (OLD_KERNEL if before else NEW_KERNEL), 'active_kernel_not_expected')


def state(plan, digest):
    value = B.document(STATE)
    require(value['schema'] == 'security_kernel_reboot_state/1' and value['operation_id'] == OPERATION and
            value['nonce'] == plan['nonce'] and value['manifest_sha256'] == digest, 'owned_state_mismatch')
    return value


def advance(plan, digest, phase):
    value = state(plan, digest)
    value.update(phase=phase, owner_pid=os.getpid(), current_boot_id=boot_id(), recorded_at_utc=now().isoformat())
    atomic(STATE, value)


def own_reservation(plan, digest):
    value = B.document(B.RESERVATION)
    require(value['schema'] == 'security_kernel_reboot_reservation/1' and value['nonce'] == plan['nonce'] and
            value['operation_id'] == OPERATION and value['manifest_sha256'] == digest and
            value['before_boot_id'] == plan['before_boot_id'] and
            value['inode'] == B.RESERVATION.lstat().st_ino, 'foreign_or_changed_reservation')
    return value


def token_payload(plan, digest):
    return {'schema': 'security_kernel_reboot_quiescence/1', 'operation_id': OPERATION,
            'nonce': plan['nonce'], 'manifest_sha256': digest}


def golden_parent():
    for directory in (Path('/etc'), Path('/etc/systemd'), Path('/etc/systemd/system'), GOLDEN_INHIBITOR.parent):
        if directory == GOLDEN_INHIBITOR.parent and not os.path.lexists(directory):
            return True
        info = directory.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022,
                'unsafe_golden_dropin_parent')
    return False


def golden_health():
    # Explicitly approved read-only SQLite integrity check; no rows or credentials.
    probe = ('import sqlite3; c=sqlite3.connect("file:/root/golden_bot/trades.db?mode=ro",uri=True,timeout=5); '
             'r=c.execute("PRAGMA quick_check").fetchall(); c.close(); '
             'raise SystemExit(0 if r==[("ok",)] else 1)')
    B.command(['/usr/bin/python3', '-I', '-B', '-c', probe])


def lock_current(stack):
    descriptors = []
    for path in (B.MARKER, B.SOURCE):
        B.read(path, maximum=8192)
        fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK)
        stack.callback(os.close, fd)
        require(B.identity(os.fstat(fd)) == B.identity(path.lstat()), 'current_lock_identity_changed')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        descriptors.append(fd)
    return descriptors


def reserve(plan, digest, stack):
    require(not os.path.lexists(B.RESERVATION) and not os.path.lexists(STATE) and not os.path.lexists(RESULT), 'operation_already_exists')
    fd, temporary = tempfile.mkstemp(prefix='.kernel-reboot-reservation.', dir=B.RESERVATION.parent)
    stack.callback(os.close, fd)
    stack.callback(lambda: Path(temporary).unlink(missing_ok=True))
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    reservation = {'schema': 'security_kernel_reboot_reservation/1', 'operation_id': OPERATION,
                   'nonce': plan['nonce'], 'manifest_sha256': digest,
                   'before_boot_id': plan['before_boot_id'], 'inode': os.fstat(fd).st_ino}
    B.write_all(fd, (json.dumps(reservation, sort_keys=True) + '\n').encode())
    os.fsync(fd)
    atomic(STATE, {'schema': 'security_kernel_reboot_state/1', 'operation_id': OPERATION,
                  'nonce': plan['nonce'], 'manifest_sha256': digest, 'phase': 'intent_durable',
                  'owner_pid': os.getpid(), 'current_boot_id': boot_id()}, exclusive=True)
    B.publish_noreplace(temporary, B.RESERVATION)
    directory = os.open(B.RESERVATION.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    stack.callback(os.close, directory)
    os.fsync(directory)
    atomic(TOKEN, token_payload(plan, digest), exclusive=True)
    advance(plan, digest, 'reserved')
    return fd


def check_owners(plan, digest):
    own_reservation(plan, digest)
    status = state(plan, digest)
    require(status['current_boot_id'] == boot_id(), 'stale_native_owner_boot')
    with open('/proc/locks', 'rb') as stream: locks = stream.read(2 * 1024 * 1024 + 1)
    require(len(locks) <= 2 * 1024 * 1024, 'lock_inventory_bound')
    for path in (B.MARKER, B.SOURCE, B.RESERVATION):
        info = path.lstat()
        key = f'{os.major(info.st_dev):02x}:{os.minor(info.st_dev):02x}:{info.st_ino}'
        require(any(len(fields := line.split()) >= 8 and fields[1:4] == ['FLOCK', 'ADVISORY', 'WRITE'] and
                    fields[4] == str(status['owner_pid']) and fields[5] == key
                    for line in locks.decode('ascii').splitlines()), 'native_fence_lost')


def phase_check(plan, digest, phase):
    status = state(plan, digest)
    before = status['current_boot_id'] == plan['before_boot_id']
    invariants(plan, before)
    check_owners(plan, digest)
    for unit in plan['units']:
        current = properties(unit['name'])
        expected = 'disabled' if unit['enabled'] == 'enabled' else unit['enabled']
        require(current['UnitFileState'] == expected, 'quiesced_enablement_changed')
        require(current['ActiveState'] in {'inactive', 'failed'}, 'native_writer_not_quiescent')
    B.runtime(plan, phase)
    print(json.dumps({'status': 'passed', 'plan': plan,
                      'containers': B.bound_document(plan, plan['runtime_snapshot'])['containers']}))


def invoke_local(plan, digest, path, phase, descriptors):
    env = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C.UTF-8',
           'DOCKER_HOST': 'unix:///var/run/docker.sock',
           'ANSIBLE_CONFIG': str(HERE / 'reboot_ansible.cfg'),
           'ANSIBLE_HOME': str(PROJECT / '.local/security-kernel-reboot-native-ansible'),
           'ANSIBLE_LOCAL_TEMP': str(PROJECT / '.local/security-kernel-reboot-native-tmp')}
    variables = {'reboot_manifest': str(MANIFEST), 'reboot_manifest_sha256': digest,
                 'reboot_native_phase': phase}
    return B.execute_ansible([str(NATIVE_ANSIBLE), '-i', 'localhost,', '-c', 'local', str(path),
                              '-e', json.dumps(variables)], env, DEADLINE, lock_fds=descriptors)


def execute(plan, digest, before):
    invariants(plan, before)
    with ExitStack() as stack:
        descriptors = lock_current(stack)
        invariants(plan, before)
        if before:
            B.runtime(plan, 'complete')
            golden_health()
            descriptors.append(reserve(plan, digest, stack))
            code = invoke_local(plan, digest, HERE / 'reboot_native.yml', 'quiesce', descriptors)
            require(code == 0, 'quiescence_failed_reservation_retained')
            phase_check(plan, digest, 'quiesced')
            advance(plan, digest, 'reboot_requested')
            # No retries, reboot loops or stale AUTHORITY_TRANSFERRED recovery.
            B.command(['/usr/bin/systemctl', 'reboot'])
            # Keep current-inode locks until the requested shutdown terminates
            # this process; a single bounded wait is not a persistent daemon.
            signal.alarm(60)
            signal.pause()
            raise B.Refusal('reboot_did_not_terminate_native_guard')
        own_reservation(plan, digest)
        require(state(plan, digest)['phase'] == 'reboot_requested', 'postboot_phase_not_reboot_requested')
        require(B.document(TOKEN) == token_payload(plan, digest), 'quiescence_token_changed')
        fd = os.open(B.RESERVATION, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK)
        stack.callback(os.close, fd)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        require(os.fstat(fd).st_ino == own_reservation(plan, digest)['inode'], 'reservation_inode_changed')
        descriptors.append(fd)
        advance(plan, digest, 'postboot_verified')
        phase_check(plan, digest, 'quiesced')
        code = invoke_local(plan, digest, HERE / 'reboot_native.yml', 'restore', descriptors)
        require(code == 0, 'restore_failed_reservation_retained')
        invariants(plan, before=False)
        check_owners(plan, digest)
        B.runtime(plan, 'complete')
        golden_health()
        require(not os.path.lexists(TOKEN) and not os.path.lexists(GOLDEN_INHIBITOR), 'temporary_golden_gate_not_removed')
        atomic(RESULT, {'schema': 'security_kernel_reboot_result/1', 'operation_id': OPERATION,
                        'nonce': plan['nonce'], 'manifest_sha256': digest, 'status': 'completed',
                        'before_boot_id': plan['before_boot_id'], 'after_boot_id': boot_id(),
                        'active_kernel': NEW_KERNEL, 'authority_changed': False,
                        'application_image_changed': False, 'containers_verified': 15,
                        'golden_sqlite_quick_check': 'ok', 'mount_proof': 'uuid_source_inode',
                        'marker_journal_bytes_unchanged': True, 'recorded_at_utc': now().isoformat()}, exclusive=True)
        advance(plan, digest, 'completed')
        own_reservation(plan, digest)
        require(os.fstat(fd).st_ino == B.RESERVATION.lstat().st_ino, 'reservation_changed_at_release')
        B.RESERVATION.unlink()
        directory = os.open(B.RESERVATION.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try: os.fsync(directory)
        finally: os.close(directory)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('snapshot', 'check', 'owned-info', 'phase-check', 'release-golden', 'cleanup-golden', 'cleanup-controller', 'prepare', 'restore'))
    parser.add_argument('--manifest', type=Path, default=MANIFEST)
    parser.add_argument('--manifest-sha256')
    parser.add_argument('--phase', choices=('quiesced', 'datastores', 'healthy'))
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    try:
        if args.mode == 'snapshot':
            make_manifest()
        else:
            plan = load(args.manifest, args.manifest_sha256 or '')
            if args.mode == 'check':
                invariants(plan, before=True)
                require(not any(os.path.lexists(p) for p in (B.RESERVATION, STATE, RESULT)), 'operation_present')
                B.runtime(plan, 'complete')
                print(json.dumps({'status': 'preflight_passed', 'runtime_changed': False}))
            elif args.mode == 'phase-check':
                require(args.phase is not None, 'phase_required')
                phase_check(plan, args.manifest_sha256, args.phase)
            elif args.mode == 'owned-info':
                status = state(plan, args.manifest_sha256)
                invariants(plan, status['current_boot_id'] == plan['before_boot_id'])
                check_owners(plan, args.manifest_sha256)
                print(json.dumps({'plan': plan, 'containers': B.bound_document(plan, plan['runtime_snapshot'])['containers'],
                                  'golden_inhibitor': str(GOLDEN_INHIBITOR),
                                  'golden_directory_missing': golden_parent(),
                                  'golden_inhibitor_content': GOLDEN_INHIBITOR_BYTES.decode(), 'token': str(TOKEN)}))
            elif args.mode == 'release-golden':
                require(args.apply, 'explicit_apply_required')
                phase_check(plan, args.manifest_sha256, 'healthy')
                require(B.document(TOKEN) == token_payload(plan, args.manifest_sha256), 'quiescence_token_changed')
                TOKEN.unlink()
                directory = os.open(TOKEN.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                try: os.fsync(directory)
                finally: os.close(directory)
            elif args.mode == 'cleanup-golden':
                require(args.apply, 'explicit_apply_required')
                invariants(plan, before=False)
                check_owners(plan, args.manifest_sha256)
                B.runtime(plan, 'complete')
                golden_health()
                require(not os.path.lexists(TOKEN) and B.read(GOLDEN_INHIBITOR, private=False) == GOLDEN_INHIBITOR_BYTES,
                        'golden_gate_cleanup_not_owned_or_ready')
                GOLDEN_INHIBITOR.unlink()
                directory = os.open(GOLDEN_INHIBITOR.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                try: os.fsync(directory)
                finally: os.close(directory)
                B.command(['/usr/bin/systemctl', 'daemon-reload'])
            elif args.mode == 'cleanup-controller':
                require(args.apply, 'explicit_apply_required')
                invariants(plan, before=False)
                result = B.document(RESULT)
                require(result['status'] == 'completed' and result['nonce'] == plan['nonce'] and
                        result['manifest_sha256'] == args.manifest_sha256 and result['after_boot_id'] == boot_id(),
                        'completed_reboot_receipt_missing')
                require(not os.path.lexists(B.RESERVATION), 'operation_not_released')
                env = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C.UTF-8',
                       'ANSIBLE_CONFIG': str(HERE / 'ansible.cfg'),
                       'ANSIBLE_HOME': str(PROJECT / '.local/security-kernel-reboot-cleanup-home'),
                       'ANSIBLE_LOCAL_TEMP': str(PROJECT / '.local/security-kernel-reboot-cleanup-tmp')}
                variables = {'controller_access_id': 'host-patch-02', 'controller_access_action': 'cleanup',
                             'controller_access_confirmation': 'TEMPORARY-ROOT-SSH-20261024T013000Z'}
                status = B.execute_ansible([str(NATIVE_ANSIBLE), '-i', str(HERE / 'controller_inventory.example.yml'),
                                            str(HERE / 'controller_access.yml'), '-e', json.dumps(variables)], env, DEADLINE)
                require(status == 0, 'temporary_controller_cleanup_failed')
                print(json.dumps({'status': 'controller_cleanup_completed', 'private_key_contents_transferred': False}))
            else:
                require(args.apply, 'explicit_apply_required')
                execute(plan, args.manifest_sha256, before=args.mode == 'prepare')
        return 0
    except BaseException as exc:
        print(json.dumps({'status': 'refused', 'code': str(exc) if isinstance(exc, B.Refusal) else 'unexpected_failure',
                          'error_class': type(exc).__name__}), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
