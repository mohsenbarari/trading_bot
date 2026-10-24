#!/usr/bin/env python3
"""One reviewed same-OS Web CE transaction; no reboot/release/authority change.

run defaults to read-only. --apply holds current marker + owned source and
controller flocks for its entire fixed local Ansible child. An ambiguous error
retains reservation/journal; there is no automatic retry, adoption or downgrade.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
import fcntl
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import resource
import shlex
import signal
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import types

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[1]


def reviewed_module(path, digest, name):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_mode & 0o022 or info.st_size > 65536:
            raise RuntimeError('reviewed_source_refused')
        data = os.read(fd, 65537)
        if sha256(data).hexdigest() != digest:
            raise RuntimeError('reviewed_source_changed')
    finally:
        os.close(fd)
    module = types.ModuleType(name)
    module.__file__ = str(path)
    exec(compile(data, str(path), 'exec'), module.__dict__)
    return module


P = reviewed_module(HERE / 'web_engine_preflight.py', 'cbadbc1085dd5e351510ed719a9e6c779523c4d644f53198d4f9c0c1d7750679', '_web_preparation')
B = reviewed_module(PROJECT / 'scripts/validate_docker_daemon_maintenance.py', 'fa2bf85cd50378bb999a7d8aabbd965a174722605c943c59ce6da56f68a74901', '_reviewed_daemon_primitives')
require = P.require
ROOT = Path('/root/secure-envs/trading-bot/release-control')
MAX_ARCHIVE = 10 * 1024**3 - 4096
FREE_FLOOR = 8 * 1024**3
UNITS = {
    'coin-intelligence-capture-shadow-external.timer', 'coin-intelligence-capture-shadow-external.service',
    'coin-intelligence-capture-shadow-input.timer', 'coin-intelligence-capture-shadow-input.service',
    'market-pipeline-watchdog.timer', 'market-pipeline-watchdog.service',
    'trading-bot-sync-health-sampler.timer', 'trading-bot-sync-health-sampler.service',
    'coin-capture.service', 'market-channel-capture.service',
}
ENGINES = ['docker.socket', 'docker.service', 'containerd.service']
INSPECT = (P.INSPECT[:-1] + ',"network_mode":{{json .HostConfig.NetworkMode}},'
           '"port_bindings":{{json .HostConfig.PortBindings}},'
           '"network_names":{{json .NetworkSettings.Networks}}}')
BACKUP_INPUTS = ['/var/lib/docker', '/var/lib/containerd',
                '/srv/trading-bot/market-data-staging-shadow/sessions',
                '/srv/trading-bot/secure/market-data']
OPTIONAL_INPUTS = ['/etc/docker', '/etc/containerd', '/etc/default/docker', '/etc/default/containerd']
PHASES = ['reserved', 'quiescing', 'writers_quiesced', 'market_backed_up', 'quiesced', 'backing_up', 'backup_ready', 'installing',
          'installed', 'engines_verified', 'datastores', 'healthy', 'complete']
RESTORE_RESERVE = 30 * 60
PREINSTALL_MARGIN = 35 * 60
MINIMUM_START = 65 * 60


class StageLivenessError(P.Refusal):
    """Retain all fences: no automatic restore without descendant quiescence."""


def paths(plan):
    prefix = plan['operation_id']
    return ROOT / (prefix + '.journal.json'), ROOT / (prefix + '.source.lock')


def binding(plan, path):
    matches = [v for v in plan['files'] if v['path'] == str(path)]
    require(len(matches) == 1, 'binding_missing_or_duplicate')
    return matches[0]


def bound_document(plan, path):
    return P.document(path, binding(plan, path)['sha256'])


def write_json(path, value, *, exclusive):
    B.private_parent(path)
    fd, temporary = tempfile.mkstemp(prefix='.web-engine-', dir=path.parent)
    try:
        B.write_all(fd, (json.dumps(value, sort_keys=True) + '\n').encode())
        os.fsync(fd)
        if exclusive:
            B.publish_noreplace(temporary, path)
        else:
            B.read(path)
            os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try: os.fsync(directory)
        finally: os.close(directory)
    finally:
        os.close(fd)
        Path(temporary).unlink(missing_ok=True)


def journal(plan, digest):
    value = B.document(paths(plan)[0])
    require(value['schema'] == 'web_engine_transaction/1' and value['operation_id'] == plan['operation_id']
            and value['nonce'] == plan['nonce'] and value['manifest_sha256'] == digest, 'journal_identity_changed')
    return value


def advance(plan, digest, phase, **evidence):
    value = journal(plan, digest)
    require(phase in PHASES and PHASES.index(phase) == PHASES.index(value['phase']) + 1, 'invalid_phase_transition')
    value.update(phase=phase, **evidence)
    write_json(paths(plan)[0], value, exclusive=False)
    return value


def check_owners(plan, digest):
    value = B.document(P.RESERVATION)
    require(value['schema'] == 'web_engine_transaction/1' and value['operation_id'] == plan['operation_id']
            and value['nonce'] == plan['nonce'] and value['manifest_sha256'] == digest, 'reservation_changed')
    with open('/proc/locks', 'rb') as stream:
        records = stream.read(2 * 1024 * 1024 + 1)
    require(len(records) <= 2 * 1024 * 1024, 'locks_bound')
    for path in (P.MARKER, paths(plan)[1], P.RESERVATION):
        info = path.lstat()
        key = f'{os.major(info.st_dev):02x}:{os.minor(info.st_dev):02x}:{info.st_ino}'
        require(any(len(fields) >= 8 and fields[1:4] == ['FLOCK', 'ADVISORY', 'WRITE']
                    and fields[4] == str(value['pid']) and fields[5] == key
                    for fields in [line.split() for line in records.decode('ascii').splitlines()]), 'operation_fence_lost')


def native_contract(plan):
    require(os.geteuid() == 0 and Path('/proc/1/exe').readlink().name == 'systemd', 'native_root_required')
    require(not any(Path(v).exists() for v in ('/.dockerenv', '/run/.containerenv', '/run/systemd/container')), 'container_refused')
    for ns in ('pid', 'mnt', 'user'):
        require(os.readlink('/proc/self/ns/' + ns) == os.readlink('/proc/1/ns/' + ns), 'namespace_mismatch')
    distro = dict(line.split('=', 1) for line in Path('/etc/os-release').read_text().splitlines() if '=' in line)
    require(distro.get('ID', '').strip('"') == 'ubuntu' and distro.get('VERSION_ID', '').strip('"') == '26.04', 'distro_mismatch')
    require(Path('/etc/machine-id').read_text().strip() == plan['machine_id'], 'host_changed')
    require(plan['deadline_utc'].endswith('Z') and datetime.now(timezone.utc) < datetime.fromisoformat(plan['deadline_utc'].replace('Z', '+00:00')), 'window_expired')


def backup_contract(plan):
    app, offhost = (bound_document(plan, plan[k]) for k in ('app_backup_receipt', 'app_offhost_receipt'))
    require(app['status'] == 'ok' and set(app['roles']) == {'foreign', 'iran'}, 'application_backup_incomplete')
    require({v['role'] for v in app['results']} == {'foreign', 'iran'} and len(app['results']) == 2, 'application_backup_roles_changed')
    for value in app['results']:
        require(value['status'] == 'ok' and len(value['files']) == 4 and value['restore_smoke']['status'] == 'passed'
                and value['restore_smoke']['cleanup']['status'] == 'passed', 'application_restore_missing')
    foreign = next(v for v in app['results'] if v['role'] == 'foreign')
    require(foreign['project_label'] == 'current', 'application_backup_scope_changed')
    require(offhost['schema'] == 'security_maintenance_offhost_sources/1.0' and offhost['status'] == 'passed'
            and offhost['host_verification'] == 'strict' and offhost['fsync_completed'] is True, 'application_offhost_missing')
    expected = {entry['sha256'] for entry in foreign['files']}
    require(expected <= {e['sha256'] for e in offhost['files']}, 'application_offhost_inventory_missing')
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(app['created_at'].replace('Z', '+00:00'))).total_seconds()
    require(0 <= age <= 24 * 3600, 'database_backup_stale')


def market_contract(plan, receipt, artifact_identity):
    market = receipt
    require(market['schema'] == 'market_pipeline_backup_restore/1.2' and market['status'] == 'PASS'
            and market['restore_smoke']['status'] == 'PASS' and market['restore_smoke']['cleanup_status'] == 'PASS'
            and market['database_mutated'] is False and market['services_started'] is False, 'market_restore_missing')
    ids = {r['id'] for r in plan['containers'] if r['project'] == 'market-private-pipeline-primary' and r['name'].endswith('-market-database-1')}
    recorded_id = market['source']['container_id']
    require(re.fullmatch('[0-9a-f]{12,64}', recorded_id) and len(ids) == 1
            and next(iter(ids)).startswith(recorded_id)
            and sum(row['id'].startswith(recorded_id) for row in plan['containers']) == 1, 'market_backup_container_changed')
    for key in ('schema_versions', 'table_count', 'fact_count', 'table_row_counts', 'sequence_values'):
        require(market['source'][key] == market['restore_smoke'][key], 'market_restore_parity_missing')
    artifact = market['backup']
    value = Path(artifact['path']).lstat()
    require(stat.S_ISREG(value.st_mode) and value.st_uid == os.geteuid() and value.st_nlink == 1
            and not value.st_mode & 0o077 and value.st_size == artifact['size_bytes']
            and list(B.identity(value)) == artifact_identity, 'market_backup_changed')
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(market['created_at_utc'].replace('Z', '+00:00'))).total_seconds()
    require(0 <= age <= 3600, 'market_backup_stale')


def market_document(plan, digest):
    proof = journal(plan, digest)['market_backup']
    receipt = P.document(Path(plan['backup_root']) / 'market' / 'market-pipeline-backup-receipt.json', proof['receipt_sha256'])
    market_contract(plan, receipt, proof['artifact_identity'])
    return receipt


def market_anchor_contract(plan, *, verified=None):
    """Real active Market image correlation, not the processor's missing label."""
    options = plan['market_backup']
    require(options['image_id'] in {row['image'] for row in plan['containers']
            if row['project'] == 'market-private-pipeline-primary' and row['running']}, 'market_anchor_not_active_baseline_image')
    expected = {'image_id': options['image_id'], 'revision': options['release_sha'],
                'tree': options['release_tree'], 'input_signature': options['image_input_signature']}
    if verified is not None:
        require(verified == expected, 'market_anchor_recorded_provenance_changed')
        return expected
    template = ('{"revision":{{json (index .Config.Labels "org.opencontainers.image.revision")}},'
                '"tree":{{json (index .Config.Labels "io.gold-trade.release.tree")}},'
                '"input_signature":{{json (index .Config.Labels "io.gold-trade.release.input-signature")}}}')
    actual = json.loads(B.command(['/usr/bin/docker', 'image', 'inspect', '--format', template, options['image_id']]))
    require(actual == {'revision': options['release_sha'], 'tree': options['release_tree'],
                       'input_signature': options['image_input_signature']}, 'market_anchor_provenance_changed')
    return expected


def market_anchor_invariant(plan, digest, *, owned):
    anchor = None
    if owned and journal(plan, digest)['phase'] in {'quiesced', 'backing_up', 'backup_ready', 'installing', 'installed'}:
        anchor = journal(plan, digest).get('market_anchor_provenance')
        require(isinstance(anchor, dict), 'market_anchor_quiescent_proof_missing')
    return market_anchor_contract(plan, verified=anchor)


def store_root_contract(entry):
    path = Path(entry['path'])
    B.private_parent(path)  # canonical path and safe parents, including no redirect
    value = path.lstat()
    require(stat.S_ISDIR(value.st_mode) and value.st_uid == os.geteuid()
            and not value.st_mode & 0o022, 'store_root_metadata_unsafe')
    require((value.st_dev, value.st_ino) == (entry['device'], entry['inode']), 'store_root_changed')
    require(B.command(['/usr/bin/findmnt', '-n', '-o', 'UUID', '-T', entry['path']]) == entry['uuid'], 'store_filesystem_changed')


def control_parent_contract(plan):
    B.private_parent(paths(plan)[0])
    value = ROOT.lstat()
    require(stat.S_ISDIR(value.st_mode) and value.st_uid == os.geteuid()
            and not value.st_mode & 0o022, 'control_parent_unsafe')


def invariant_contract(plan, digest, *, owned):
    native_contract(plan)
    require(plan['schema'] == 'web_engine_transaction_plan/1' and re.fullmatch('[A-Za-z0-9._-]{1,80}', plan['operation_id'])
            and re.fullmatch('[0-9a-f]{32}', plan['nonce']), 'plan_identity_invalid')
    control_parent_contract(plan)
    require(not os.path.lexists(P.PENDING), 'two_host_operation_present')
    marker = plan['marker']
    require(marker['path'] == str(P.MARKER) and P.file_digest(P.MARKER, maximum=8192, private=True)
            == (marker['size'], marker['sha256']), 'marker_changed')
    require((P.MARKER.stat().st_dev, P.MARKER.stat().st_ino) == (marker['device'], marker['inode']), 'marker_inode_changed')
    require(not any(os.path.lexists(p) for p in plan['absent_control_paths']), 'unexpected_control_artifact')
    require(set(plan['absent_control_paths']) == {
        '/root/secure-envs/trading-bot/.production-runtime-source.lock',
        '/root/secure-envs/trading-bot/market-pipeline-cutover/bot-legacy-handoff-4ef8e6dc.json'}, 'control_inventory_invalid')
    installed_phase = owned and PHASES.index(journal(plan, digest)['phase']) >= PHASES.index('installing')
    permitted = journal(plan, digest).get('vendor_configs', {}) if installed_phase else {}
    for config in ('/etc/docker/daemon.json', '/etc/containerd/config.toml'):
        if config in permitted:
            entry = permitted[config]
            require(P.file_digest(config, maximum=65536, private=False) == (entry['size'], entry['sha256']), 'vendor_store_config_changed')
        else:
            require(not os.path.lexists(config), 'store_config_changed')
    source = Path(plan['source_root'])
    expected = {str(HERE / name) for name in ('web_engine_preflight.py', 'web_engine_controller.py', 'web_engine_transaction.yml', 'web_engine_local_inventory.yml', 'web_engine_offhost.py')}
    expected |= {str(PROJECT / 'scripts' / name) for name in ('validate_docker_daemon_maintenance.py', 'crypt_market_pipeline_backup.py', 'backup_market_pipeline_archive.py', 'prepare_market_pipeline_release.py')}
    expected |= {plan[k] for k in ('app_backup_receipt', 'app_offhost_receipt', 'ansible_binary')}
    expected.add(str(PROJECT / 'deploy/maintenance/ansible.cfg'))
    require(source == PROJECT and expected <= {v['path'] for v in plan['files']}, 'source_binding_missing')
    for entry in plan['files']:
        require(P.file_digest(entry['path'], maximum=2 * 1024 * 1024, private=entry.get('private', True))
                == (entry['size'], entry['sha256']), 'bound_file_changed')
    P.baseline_contract(plan['containers'])
    for entries, pins in [(plan['old_packages'], P.OLD_PACKAGES), (plan['new_packages'], P.NEW_PACKAGES)]:
        P.package_contract(entries, pins)
        for entry in entries:
            require(P.file_digest(entry['path'], maximum=64 * 1024 * 1024, private=False)
                    == (entry['size'], entry['sha256']), 'package_payload_changed')
            require(B.command(['/usr/bin/dpkg-deb', '-f', entry['path'], 'Package', 'Version', 'Architecture']).splitlines()
                    == ['Package: ' + entry['name'], 'Version: ' + entry['version'], 'Architecture: amd64'], 'package_identity_changed')
    require({u['name'] for u in plan['units']} == UNITS and len(plan['units']) == len(UNITS), 'unit_inventory_changed')
    for unit in plan['units']:
        require(unit['enabled'] in {'enabled', 'disabled', 'static'}, 'unit_enablement_unsupported')
        kind = 'timer' if unit['name'].endswith('.timer') else 'oneshot' if B.command(['/usr/bin/systemctl', 'show', unit['name'], '-p', 'Type', '--value']) == 'oneshot' else 'daemon'
        require(unit['kind'] == kind, 'unit_kind_mismatch')
        require(B.command(['/usr/bin/systemctl', 'show', unit['name'], '-p', 'FragmentPath', '--value']) == unit['fragment_path'], 'unit_identity_changed')
        require(sha256(B.read(unit['fragment_path'], private=False)).hexdigest() == unit['fragment_sha256'], 'unit_source_changed')
        require(B.command(['/usr/bin/systemctl', 'show', unit['name'], '-p', 'DropInPaths', '--value']).split() == [d['path'] for d in unit['dropins']], 'unit_dropins_changed')
        require(all(sha256(B.read(d['path'], private=False)).hexdigest() == d['sha256'] for d in unit['dropins']), 'unit_dropin_source_changed')
    require({m['path'] for m in plan['store_roots']} == {'/var/lib/docker', '/var/lib/containerd'} and len(plan['store_roots']) == 2, 'store_roots_missing')
    for entry in plan['store_roots']:
        store_root_contract(entry)
    require(plan['backup_root'] == '/srv/trading-bot/backups/security-web-engine-' + plan['operation_id'] + '-' + plan['nonce'], 'backup_root_invalid')
    B.private_parent(Path(plan['backup_root']))
    backup_contract(plan)
    options = plan['market_backup']
    for key in ('release_sha', 'release_tree'):
        require(re.fullmatch('[0-9a-f]{40}', options[key]), 'market_release_binding_invalid')
    require(re.fullmatch('sha256:[0-9a-f]{64}', options['image_id'])
            and re.fullmatch('[0-9a-f]{64}', options['image_input_signature']), 'market_image_binding_invalid')
    market_anchor_invariant(plan, digest, owned=owned)
    for path in (options['env_file'], plan['backup_key_file']):
        value = Path(path).lstat()
        B.private_parent(Path(path))
        require(stat.S_ISREG(value.st_mode) and value.st_uid == 0 and value.st_nlink == 1
                and not value.st_mode & 0o077 and value.st_size <= 1024 * 1024, 'private_input_metadata_unsafe')
    require(list(B.identity(Path(options['env_file']).lstat())) == options['env_file_identity'], 'market_role_metadata_changed')
    B.health_contract(plan, plan['containers'])
    app_ids = {r['id'] for r in plan['containers'] if r['name'] == '/trading_bot_app'}
    require(len(app_ids) == 1 and not plan.get('health_urls', []) and any(
        p['id'] in app_ids and p['url'] == 'http://127.0.0.1:8000/api/config'
        for p in plan.get('health_probes', [])), 'application_container_probe_required')
    running = {r['id'] for r in plan['containers'] if r['running']}
    stores = {r['id'] for r in plan['containers'] if r['name'] in {'/trading_bot_db', '/trading_bot_redis'}
              or r['name'].endswith(('_db-1', '-db-1', '-market-database-1', '_redis-1', '-redis-1'))}
    require(len(stores) == 8 and set(plan['datastore_ids']) == stores and stores <= running, 'datastore_inventory_invalid')
    require(len(plan['datastore_ids']) == len(plan['datastore_probes'])
            and {p['id'] for p in plan['datastore_probes']} == set(plan['datastore_ids']), 'datastore_probes_missing')
    for p in plan['datastore_probes']:
        require(p['kind'] in {'postgres', 'redis'} and (p['kind'] == 'redis' or all(re.fullmatch('[A-Za-z0-9_]{1,63}', p[k]) for k in ('user', 'database'))), 'invalid_datastore_probe')
    if owned:
        check_owners(plan, digest)
        if PHASES.index(journal(plan, digest)['phase']) >= PHASES.index('market_backed_up'):
            market_document(plan, digest)
    else:
        require(not any(os.path.lexists(p) for p in (P.RESERVATION, *paths(plan))), 'operation_or_prior_journal_present')


def package_versions(pins):
    return dict(line.split('=', 1) for line in B.command(['/usr/bin/dpkg-query', '-W', '-f=${Package}=${Version}\\n', *pins]).splitlines())


def runtime_contract(plan, phase):
    expected = {r['id']: r for r in plan['containers']}
    require(set(B.command(['/usr/bin/docker', 'ps', '-aq', '--no-trunc']).splitlines()) == set(expected), 'container_set_changed')
    P.store_contract(json.loads(B.command(['/usr/bin/docker', 'info', '--format', '{"root":{{json .DockerRootDir}},"driver":{{json .Driver}},"driver_status":{{json .DriverStatus}},"live_restore":{{json .LiveRestoreEnabled}}}'])))
    require(json.loads(B.command(['/usr/bin/docker', 'info', '--format', '{"driver":{{json .CgroupDriver}},"version":{{json .CgroupVersion}}}']))
            == {'driver': 'systemd', 'version': '2'}, 'cgroup_runtime_changed')
    for ident, before in expected.items():
        current = json.loads(B.command(['/usr/bin/docker', 'inspect', '--format', INSPECT, ident]))
        require(all(current[k] == before[k] for k in ('id', 'name', 'image', 'project'))
                and P.canonical_mounts(current['mounts']) == P.canonical_mounts(before['mounts']), 'container_identity_changed')
        require(current['network_mode'] == before['network_mode'] and current['port_bindings'] == before['port_bindings']
                and sorted(current['network_names']) == sorted(before['network_names']), 'container_network_changed')
        running = before['running'] if phase in {'baseline', 'healthy', 'complete'} else ident in plan['datastore_ids'] if phase in {'datastores', 'backup_datastores'} else False
        require(current['running'] == running, 'container_running_intent_changed')
        policy = before['restart_policy'] if phase in {'baseline', 'complete'} else {'Name': 'no', 'MaximumRetryCount': 0}
        require(current['restart_policy'] == policy, 'container_policy_changed')
        require(not running or current['health'] in {None, 'healthy'}, 'container_unhealthy')
    if phase in {'datastores', 'backup_datastores', 'healthy', 'complete'}:
        for probe in plan['datastore_probes']:
            args = ['redis-cli', 'ping'] if probe['kind'] == 'redis' else ['pg_isready', '-U', probe['user'], '-d', probe['database']]
            result = B.command(['/usr/bin/docker', 'exec', probe['id'], *args])
            require(probe['kind'] != 'redis' or result == 'PONG', 'datastore_not_ready')
    if phase in {'healthy', 'complete'}:
        patched_runtime()
        for probe in plan.get('health_probes', []):
            require(B.command(['/usr/bin/docker', 'exec', probe['id'], '/usr/local/bin/python', '-I', '-B', '-c', B.CONTAINER_HTTP_PROBE, probe['url']]) == '200', 'api_smoke_failed')
    for unit in plan['units']:
        require(B.command(['/usr/bin/systemctl', 'show', unit['name'], '-p', 'UnitFileState', '--value']) == unit['enabled'], 'unit_enablement_changed')
        state = B.command(['/usr/bin/systemctl', 'show', unit['name'], '-p', 'ActiveState', '--value'])
        if phase in {'baseline', 'complete'}:
            if unit['kind'] != 'oneshot': require((state == 'active') == unit['active'], 'unit_intent_changed')
        else:
            require(state in {'inactive', 'failed'}, 'unit_not_quiescent')
    containerd_contract(plan, phase)
    if phase in {'baseline', 'complete'}:
        require({e['name'] for e in plan['engine_intent']} == {*ENGINES, 'nginx.service'}
                and len(plan['engine_intent']) == 4, 'engine_intent_inventory_invalid')
        for unit in plan['engine_intent']:
            require(B.command(['/usr/bin/systemctl', 'show', unit['name'], '-p', 'UnitFileState', '--value']) == unit['enabled'], 'engine_enablement_changed')
            require((B.command(['/usr/bin/systemctl', 'show', unit['name'], '-p', 'ActiveState', '--value']) == 'active') == unit['active'], 'engine_active_intent_changed')


def containerd_contract(plan, phase):
    base = ['/usr/bin/ctr', '--address', '/run/containerd/containerd.sock']
    namespaces = B.command(base + ['namespaces', 'list', '--quiet']).splitlines()
    require(sorted(namespaces) == ['moby', 'moby_history'], 'containerd_namespace_changed')
    running = {r['id'] for r in plan['containers'] if r['running']} if phase in {'baseline', 'healthy', 'complete'} else set(plan['datastore_ids']) if phase in {'datastores', 'backup_datastores'} else set()
    for namespace in namespaces:
        tasks = B.command(base + ['--namespace', namespace, 'tasks', 'list', '--quiet']).splitlines()
        require(len(set(tasks)) == len(tasks) and set(tasks) == (running if namespace == 'moby' else set()), 'non_docker_or_unexpected_containerd_tasks')
    plugins = [line.split() for line in B.command(base + ['plugins', 'list']).splitlines()[1:]]
    require(any(len(p) >= 4 and p[0:2] == ['io.containerd.snapshotter.v1', 'overlayfs'] and p[-1] == 'ok' for p in plugins), 'containerd_snapshotter_changed')


def market_backup(plan, digest):
    invariant_contract(plan, digest, owned=True)
    require(journal(plan, digest)['phase'] == 'writers_quiesced', 'market_backup_phase_invalid')
    runtime_contract(plan, 'backup_datastores')
    database = next(r for r in plan['containers'] if r['project'] == 'market-private-pipeline-primary' and r['name'].endswith('-market-database-1'))
    ports = json.loads(B.command(['/usr/bin/docker', 'inspect', '--format', '{{json .NetworkSettings.Ports}}', database['id']]))
    require(all(v is None or v == [] for v in ports.values()), 'market_database_externally_published')
    root = Path(plan['backup_root'])
    available = os.statvfs(root.parent)
    require(available.f_bavail * available.f_frsize >= 2 * (MAX_ARCHIVE + 4096) + FREE_FLOOR + 16 * 1024**3, 'market_backup_capacity_insufficient')
    os.mkdir(root, 0o700)
    options = plan['market_backup']
    env_path = Path(options['env_file'])
    info = env_path.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_nlink == 1 and not info.st_mode & 0o077
            and list(B.identity(info)) == options['env_file_identity'], 'market_role_metadata_changed')
    B.private_parent(env_path)  # no environment contents are read by this controller
    common = ['/usr/bin/python3', '-I', '-B', str(PROJECT / 'scripts/backup_market_pipeline_archive.py')]
    args = ['--env-file', str(env_path), '--receipt', str(root / 'market/market-pipeline-backup-receipt.json'),
            '--release-sha', options['release_sha'], '--release-tree', options['release_tree'],
            '--image-id', options['image_id'], '--image-input-signature', options['image_input_signature'], '--bluegreen-source-env']
    bounded(common + ['create', *args, '--backup-dir', str(root / 'market'),
                      '--confirm', 'create-production-market-pipeline-archive-backup'], timeout=1800, maximum=2 * 1024 * 1024)
    bounded(common + ['verify', *args, '--maximum-age-seconds', '3600'], timeout=900)
    receipt = root / 'market/market-pipeline-backup-receipt.json'
    observed = B.document(receipt)
    artifact = observed['backup']
    artifact_identity = list(B.identity(Path(artifact['path']).lstat()))
    market_contract(plan, observed, artifact_identity)
    require(P.file_digest(artifact['path'], maximum=16 * 1024**3, private=True)
            == (artifact['size_bytes'], artifact['sha256']), 'market_dump_digest_changed')
    runtime_contract(plan, 'backup_datastores')  # proves labelled restore resources cleaned up
    advance(plan, digest, 'market_backed_up', market_backup={
        'receipt_sha256': sha256(B.read(receipt)).hexdigest(), 'artifact_identity': artifact_identity})


def patched_runtime():
    require(package_versions(P.NEW_PACKAGES) == {n: p[0] for n, p in P.NEW_PACKAGES.items()}, 'active_package_versions_changed')
    require(B.command(['/usr/bin/docker', 'version', '--format', '{{.Server.Version}}']) == '29.9.2', 'docker_server_not_patched')
    require(re.search(r'^Server:\s*\n\s*Version:\s*v?2\.4\.2\s*$', B.command(['/usr/bin/ctr', '--address', '/run/containerd/containerd.sock', 'version']), re.MULTILINE), 'containerd_server_not_patched')
    require(re.search(r'^runc version 1\.5\.2\b', B.command(['/usr/bin/runc', '--version'])), 'runc_not_patched')


def engine_stopped():
    for unit in ENGINES:
        require(B.command(['/usr/bin/systemctl', 'show', unit, '-p', 'ActiveState', '--value']) in {'inactive', 'failed'}, 'engine_not_quiescent')


def bounded(argv, *, timeout, stdout=None, stdin=None, maximum=2 * 1024 * 1024, extra_env=None):
    def limits():
        resource.setrlimit(resource.RLIMIT_FSIZE, (maximum, maximum))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    with tempfile.TemporaryFile() as output:
        process = subprocess.Popen(argv, stdin=stdin if stdin is not None else subprocess.DEVNULL,
                                   stdout=stdout if stdout is not None else output, stderr=subprocess.DEVNULL,
                                   start_new_session=True, preexec_fn=limits, umask=0o077,
                                   env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL': 'C',
                                        'DOCKER_HOST': 'unix:///var/run/docker.sock', **(extra_env or {})})
        try:
            try: code = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                raise P.Refusal('bounded_native_timeout') from None
            require(code == 0, 'bounded_native_failure')
            if stdout is None:
                require(output.tell() <= maximum, 'bounded_native_output')
                output.seek(0)
                return output.read(maximum).decode().strip()
            return ''
        finally:
            # This exact dedicated process group only, including compression/SSH children.
            try: os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError: pass
            process.wait(timeout=10)


def offhost(plan, digest, encrypted, *, verify):
    remote = plan['offhost']
    for key in ('identity_file', 'known_hosts_file'):
        info = Path(remote[key]).lstat()
        require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_nlink == 1
                and not info.st_mode & 0o077 and info.st_size <= 16384, 'ssh_metadata_unsafe')
        B.private_parent(Path(remote[key]))
    require(P.file_digest(remote['known_hosts_file'], maximum=16384, private=True)[1] == remote['known_hosts_sha256'], 'ssh_host_pin_changed')
    source = B.read(HERE / 'web_engine_offhost.py', private=False).decode()
    size, digest_cipher = encrypted['ciphertext_size_bytes'], encrypted['ciphertext_sha256']
    argv = ['/usr/bin/ssh', '-F', '/dev/null', '-i', remote['identity_file'], '-p', '22',
            '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes', '-o', 'UpdateHostKeys=no',
            '-o', 'ForwardAgent=no', '-o', 'ConnectTimeout=10', '-o', 'ControlMaster=no', '-S', 'none',
            '-o', 'UserKnownHostsFile=' + remote['known_hosts_file'], 'root@65.109.216.187',
            shlex.join(['/usr/bin/python3', '-I', '-B', '-c', source,
                        'capacity' if verify is None else 'verify' if verify else 'receive', '--operation', plan['operation_id'], '--nonce', plan['nonce'],
                        '--size', str(size), '--sha256', digest_cipher, '--manifest-sha256', digest])]
    with (open(Path(plan['backup_root']) / 'engine.enc', 'rb') if verify is False else open(os.devnull, 'rb')) as handle:
        result = json.loads(bounded(argv, stdin=handle, timeout=900))
    if verify is None:
        require(result['status'] == 'capacity_verified' and result['nonce'] == plan['nonce']
                and result['manifest_sha256'] == digest and result['minimum_free_bytes'] >= 12 * 1024**3, 'offhost_capacity_or_access_missing')
        return result
    require(result['status'] == 'verified' and result['nonce'] == plan['nonce'] and result['manifest_sha256'] == digest
            and result['ciphertext_size_bytes'] == size and result['ciphertext_sha256'] == digest_cipher
            and result['fsync_completed'] is True and result['plaintext_materialized_offhost'] is False, 'offhost_copy_not_verified')
    return result


def engine_backup(plan, digest):
    invariant_contract(plan, digest, owned=True)
    engine_stopped()
    require(journal(plan, digest)['phase'] == 'quiesced', 'backup_phase_invalid')
    advance(plan, digest, 'backing_up')
    root = Path(plan['backup_root'])
    available = os.statvfs(root.parent)
    require(available.f_bavail * available.f_frsize >= 2 * (MAX_ARCHIVE + 4096) + FREE_FLOOR, 'backup_capacity_insufficient')
    market_artifact = market_document(plan, digest)['backup']
    require(P.file_digest(market_artifact['path'], maximum=20 * 1024**3, private=True)
            == (market_artifact['size_bytes'], market_artifact['sha256']), 'market_backup_digest_changed')
    inputs = BACKUP_INPUTS + [market_artifact['path']] + [p for p in OPTIONAL_INPUTS if os.path.lexists(p)]
    sizes = B.command(['/usr/bin/du', '-sx', '--block-size=1', *inputs]).splitlines()
    require(len(sizes) == len(inputs) and sum(int(line.split()[0]) for line in sizes) <= 24 * 1024**3, 'engine_backup_scope_too_large')
    require(stat.S_ISDIR(root.lstat().st_mode) and root.lstat().st_uid == 0
            and stat.S_IMODE(root.lstat().st_mode) == 0o700, 'backup_root_changed')
    plain, cipher, crypt_receipt = root / 'engine.tar.gz', root / 'engine.enc', root / 'encryption.json'
    require(all(Path(p).resolve(strict=True) == Path(p) for p in inputs), 'backup_input_redirected')
    archive = ['/usr/bin/tar', '--acls', '--xattrs', '--numeric-owner', '--sparse', '--one-file-system', '--gzip', '--create', '--file=-', '--directory=/', *[p.lstrip('/') for p in inputs]]
    fd = os.open(plain, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        bounded(archive, timeout=900, stdout=fd, maximum=MAX_ARCHIVE)
        os.fsync(fd)
    finally: os.close(fd)
    engine_stopped(); invariant_contract(plan, digest, owned=True)
    bounded(['/usr/bin/tar', '--acls', '--xattrs', '--numeric-owner', '--gzip', '--compare', '--file=' + str(plain), '--directory=/'], timeout=900)
    bounded(['/usr/bin/python3', '-I', '-B', str(PROJECT / 'scripts/crypt_market_pipeline_backup.py'), 'encrypt',
             '--source', str(plain), '--destination', str(cipher), '--key-file', plan['backup_key_file'],
             '--receipt', str(crypt_receipt), '--confirm', 'encrypt-production-market-pipeline-offhost-backup'], timeout=900, maximum=MAX_ARCHIVE + 4096)
    encrypted = B.document(crypt_receipt)
    require(encrypted['schema'] == 'market_pipeline_backup_encryption/1.1'
            and encrypted['ciphertext_size_bytes'] <= MAX_ARCHIVE + 4096
            and encrypted['plaintext_materialized_offhost'] is False and encrypted['secrets_disclosed'] is False, 'encrypted_backup_contract_invalid')
    proof = offhost(plan, digest, encrypted, verify=False)
    value = plain.lstat()
    require(stat.S_ISREG(value.st_mode) and value.st_uid == 0 and value.st_nlink == 1
            and not value.st_mode & 0o077 and value.st_size == encrypted['plaintext_size_bytes'], 'plaintext_archive_changed')
    plain.unlink()  # exact owned redundant plaintext only; authenticated cipher retained on both hosts
    directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try: os.fsync(directory)
    finally: os.close(directory)
    # Decryption verification is inside the existing crypt tool; compare above
    # proves the quiescent archive against original sources without listing data.
    advance(plan, digest, 'backup_ready', encryption=encrypted, offhost=proof, redundant_plaintext_removed=True)


def apt_argv(plan, *, simulation):
    return ['/usr/bin/apt-get', *(['--simulate'] if simulation else ['--yes']), '--no-download',
            '--no-install-recommends', 'install', *[p['path'] for p in plan['new_packages']],
            *[name + '-' for name in P.OLD_PACKAGES]]


def apt_scope(output):
    installed = set(re.findall(r'^Inst ([a-z0-9.+-]+)', output, re.MULTILINE))
    removed = set(re.findall(r'^Remv ([a-z0-9.+-]+)', output, re.MULTILINE))
    require(installed == set(P.NEW_PACKAGES) and removed == set(P.OLD_PACKAGES), 'apt_scope_changed')


def vendor_configs(plan):
    """Permit only exact config bytes extracted from already digest-pinned debs."""
    result = {}
    for package in plan['new_packages']:
        if package['name'] not in {'docker-ce', 'containerd.io'}: continue
        with tempfile.TemporaryFile() as payload:
            bounded(['/usr/bin/dpkg-deb', '--fsys-tarfile', package['path']], timeout=45,
                    stdout=payload.fileno(), maximum=256 * 1024 * 1024)
            payload.seek(0)
            with tarfile.open(fileobj=payload, mode='r:') as archive:
                for index, item in enumerate(archive):
                    require(index < 20000, 'vendor_archive_item_bound')
                    name = '/' + item.name.removeprefix('./').lstrip('/')
                    if name not in {'/etc/containerd/config.toml', '/etc/docker/daemon.json'}: continue
                    require(name not in result and item.isfile() and item.uid == 0
                            and not item.mode & 0o022 and 0 < item.size <= 65536, 'vendor_config_metadata_invalid')
                    with archive.extractfile(item) as stream:
                        value = stream.read(65537)
                    require(len(value) == item.size, 'vendor_config_size_changed')
                    result[name] = {'size': len(value), 'sha256': sha256(value).hexdigest()}
    require('/etc/containerd/config.toml' in result, 'vendor_containerd_config_missing')
    return result


def install(plan, digest):
    invariant_contract(plan, digest, owned=True); engine_stopped()
    value = journal(plan, digest)
    require(value['phase'] == 'backup_ready', 'installation_phase_invalid')
    require(P.file_digest(Path(plan['backup_root']) / 'engine.enc', maximum=MAX_ARCHIVE + 4096, private=True)
            == (value['encryption']['ciphertext_size_bytes'], value['encryption']['ciphertext_sha256']), 'engine_backup_changed')
    offhost(plan, digest, value['encryption'], verify=True)
    apt_scope(B.command(apt_argv(plan, simulation=True)))
    require(not os.path.lexists('/usr/sbin/policy-rc.d'), 'existing_service_policy_requires_review')
    configs = vendor_configs(plan)
    remaining_contract(plan, RESTORE_RESERVE)  # Last check BEFORE any package write.
    advance(plan, digest, 'installing', vendor_configs=configs)
    policy = Path('/usr/sbin/policy-rc.d')
    fd = os.open(policy, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o755)
    owned = os.fstat(fd)
    try:
        os.fchmod(fd, 0o755)
        B.write_all(fd, b'#!/bin/sh\nexit 101\n'); os.fsync(fd)
    finally: os.close(fd)
    directory = os.open(policy.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try: os.fsync(directory)
    finally: os.close(directory)
    try:
        bounded(apt_argv(plan, simulation=False), timeout=240,
                extra_env={'NEEDRESTART_MODE': 'l', 'DEBIAN_FRONTEND': 'noninteractive'})
    finally:
        current = policy.lstat()
        require((current.st_dev, current.st_ino) == (owned.st_dev, owned.st_ino)
                and B.read(policy, private=False) == b'#!/bin/sh\nexit 101\n', 'service_policy_changed')
        policy.unlink()
        directory = os.open(policy.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try: os.fsync(directory)
        finally: os.close(directory)
    invariant_contract(plan, digest, owned=True); engine_stopped()
    require(package_versions(P.NEW_PACKAGES) == {n: p[0] for n, p in P.NEW_PACKAGES.items()}, 'installed_versions_not_exact')
    advance(plan, digest, 'installed')


def checkpoint(plan, digest, phase):
    invariant_contract(plan, digest, owned=True)
    if phase == 'quiescing':
        runtime_contract(plan, 'baseline')
    elif phase == 'writers_quiesced':
        runtime_contract(plan, 'backup_datastores')
    elif phase in {'quiesced', 'engines_verified'}:
        runtime_contract(plan, 'quiesced')
        if phase == 'engines_verified': patched_runtime()
    else:
        runtime_contract(plan, phase)
    if journal(plan, digest)['phase'] != phase:
        evidence = {'market_anchor_provenance': market_anchor_contract(plan)} if phase == 'quiesced' else {}
        advance(plan, digest, phase, **evidence)


def verify_private_inputs(plan):
    # Existing reviewed tools validate their own private inputs internally.
    # Only a fixed success marker is returned; no value/env/key is emitted.
    options = plan['market_backup']
    code = ('import runpy,sys;from pathlib import Path;'
            'm=runpy.run_path(sys.argv[1],run_name="_maintenance_validation");'
            'm["validate_release_env"](Path(sys.argv[2]),release_sha=sys.argv[3],'
            'image_id=sys.argv[4],allow_target_identity_mismatch=True);print("validated")')
    require(bounded(['/usr/bin/python3', '-I', '-B', '-c', code,
                     str(PROJECT / 'scripts/backup_market_pipeline_archive.py'), options['env_file'],
                     options['release_sha'], options['image_id']], timeout=30) == 'validated', 'market_private_input_invalid')
    code = ('import runpy,sys;from pathlib import Path;'
            'm=runpy.run_path(sys.argv[1],run_name="_maintenance_validation");'
            'm["_master_key"](Path(sys.argv[2]));print("validated")')
    require(bounded(['/usr/bin/python3', '-I', '-B', '-c', code,
                     str(PROJECT / 'scripts/crypt_market_pipeline_backup.py'), plan['backup_key_file']], timeout=30)
            == 'validated', 'backup_key_invalid')
    restore_image = 'postgres:15-alpine@sha256:fe0737ba566a2c5b2a28f34433c0a423261900ec17b9bf7ad115e1aae7e57f1b'
    require(re.fullmatch('sha256:[0-9a-f]{64}', B.command(['/usr/bin/docker', 'image', 'inspect', '--format', '{{.Id}}', restore_image])), 'market_restore_image_not_cached')


def remaining_contract(plan, minimum):
    deadline = datetime.fromisoformat(plan['deadline_utc'].replace('Z', '+00:00'))
    require((deadline - datetime.now(timezone.utc)).total_seconds() >= minimum, 'insufficient_restore_window')
    return deadline


def scope_empty(path):
    if not os.path.lexists(path): return True
    B.private_parent(path / 'cgroup.events')
    value = path.lstat()
    require(stat.S_ISDIR(value.st_mode) and value.st_uid == 0 and not value.st_mode & 0o022, 'stage_cgroup_unsafe')
    events = dict(line.split() for line in B.read(path / 'cgroup.events', maximum=4096, private=False).decode('ascii').splitlines())
    return events.get('populated') == '0'


def execute_stage(plan, digest, argv, env, deadline, descriptors, stage):
    """A scope preserves inherited FDs and contains even setsid descendants.

    Unlike a new service, systemd-run --scope execs the caller's child directly.
    Group/session death is never treated as proof that all stage work stopped.
    """
    require(stage in {'pre_install', 'install_restore'}, 'stage_invalid')
    unit = 'security-web-engine-' + plan['nonce'] + '-' + stage + '.scope'
    relative = '/system.slice/' + unit
    cgroup = Path('/sys/fs/cgroup' + relative)
    description = ':'.join(('web-engine', plan['operation_id'], plan['nonce'], digest, stage))
    def load_state():
        return B.command(['/usr/bin/systemctl', 'show', unit, '-p', 'LoadState', '--value'])
    if load_state() != 'not-found' or os.path.lexists(cgroup):
        raise StageLivenessError('prior_stage_scope_present')
    seconds = int((datetime.fromisoformat(deadline.replace('Z', '+00:00')) - datetime.now(timezone.utc)).total_seconds())
    require(seconds > 0, 'stage_window_expired')
    command = ['/usr/bin/systemd-run', '--scope', '--system', '--quiet', '--no-ask-password',
        '--expand-environment=no', '--slice=system.slice', '--unit=' + unit, '--description=' + description,
        '--property=KillMode=control-group', '--property=SendSIGKILL=yes', '--property=TimeoutStopSec=10s',
        '--property=RuntimeMaxSec=' + str(seconds), '--', *argv, '--tags', stage]
    try:
        return B.execute_ansible(command, env, deadline, lock_fds=descriptors)
    finally:
        try:
            state = load_state()
            if state != 'not-found':
                fields = dict(line.split('=', 1) for line in B.command(['/usr/bin/systemctl', 'show', unit,
                    '--property=Description,ControlGroup,Transient,ActiveState,KillMode,Slice']).splitlines())
                require(fields.get('Description') == description and fields.get('Transient') == 'yes'
                        and fields.get('KillMode') == 'control-group' and fields.get('Slice') == 'system.slice'
                        and fields.get('ControlGroup') in {'', relative}, 'foreign_stage_scope')
                if fields.get('ControlGroup') or fields.get('ActiveState') not in {'inactive', 'failed'}:
                    try: B.command(['/usr/bin/systemctl', 'stop', unit])
                    except B.Refusal:
                        require(load_state() == 'not-found' and scope_empty(cgroup), 'stage_stop_failed')
            require(scope_empty(cgroup), 'stage_descendants_still_populated')
        except BaseException:
            raise StageLivenessError('stage_descendant_quiescence_unproven') from None


def release_owned_fences(owned):
    for target, device, inode in reversed(owned):
        require((target.lstat().st_dev, target.lstat().st_ino) == (device, inode), 'owned_fence_changed')
        target.unlink()
        directory = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try: os.fsync(directory)
        finally: os.close(directory)


def old_datastores_and_api_ready(plan):
    for probe in plan['datastore_probes']:
        args = ['redis-cli', 'ping'] if probe['kind'] == 'redis' else ['pg_isready', '-U', probe['user'], '-d', probe['database']]
        result = B.command(['/usr/bin/docker', 'exec', probe['id'], *args])
        require(probe['kind'] != 'redis' or result == 'PONG', 'old_datastore_not_ready')
    for probe in plan.get('health_probes', []):
        require(B.command(['/usr/bin/docker', 'exec', probe['id'], '/usr/local/bin/python', '-I', '-B', '-c', B.CONTAINER_HTTP_PROBE, probe['url']]) == '200', 'old_api_smoke_failed')
    if plan.get('health_probes'):
        for row in plan['containers']:
            current = json.loads(B.command(['/usr/bin/docker', 'inspect', '--format', INSPECT, row['id']]))
            require(current['running'] == row['running'] and (not row['running'] or current['health'] in {None, 'healthy'}), 'old_container_not_healthy')


def abort_restore(plan, digest):
    """Only this still-fenced pre-install owner may restore the unchanged runtime.

    Not a package rollback or crash adoption. A changed identity/old package set,
    unknown resources, expired window or failed smoke retains all durable fences.
    """
    deadline = remaining_contract(plan, 60)
    seconds = min(RESTORE_RESERVE, int((deadline - datetime.now(timezone.utc)).total_seconds()) - 5)
    def expired(*_): raise P.Refusal('abort_restore_deadline')
    previous = signal.signal(signal.SIGALRM, expired)
    signal.alarm(seconds)
    try: _abort_restore(plan, digest)
    finally: signal.alarm(0); signal.signal(signal.SIGALRM, previous)


def _abort_restore(plan, digest):
    invariant_contract(plan, digest, owned=True)
    value = journal(plan, digest)
    require(value['phase'] in PHASES[:PHASES.index('installing')], 'package_boundary_requires_review')
    require(package_versions(P.OLD_PACKAGES) == {n: p[0] for n, p in P.OLD_PACKAGES.items()}, 'old_package_versions_changed')
    remaining_contract(plan, 60)
    for unit in plan['units']:
        if unit['kind'] != 'oneshot' and not unit['active']:
            require(B.command(['/usr/bin/systemctl', 'show', unit['name'], '-p', 'ActiveState', '--value'])
                    in {'inactive', 'failed'}, 'abort_unexpected_native_writer')
    for unit in ('containerd.service', 'docker.socket', 'docker.service'):
        before = next(e for e in plan['engine_intent'] if e['name'] == unit)
        require(B.command(['/usr/bin/systemctl', 'show', unit, '-p', 'UnitFileState', '--value']) == before['enabled'], 'engine_enablement_changed')
        if before['active']: B.command(['/usr/bin/systemctl', 'start', unit])
    market_anchor_contract(plan)  # Re-read actual OCI metadata after old engines return.
    expected = {row['id']: row for row in plan['containers']}
    require(set(B.command(['/usr/bin/docker', 'ps', '-aq', '--no-trunc']).splitlines()) == set(expected), 'abort_container_set_changed')
    observed = {}
    for ident, before in expected.items():
        current = json.loads(B.command(['/usr/bin/docker', 'inspect', '--format', INSPECT, ident]))
        require(all(current[k] == before[k] for k in ('id', 'name', 'image', 'project', 'network_mode', 'port_bindings'))
                and P.canonical_mounts(current['mounts']) == P.canonical_mounts(before['mounts'])
                and sorted(current['network_names']) == sorted(before['network_names']), 'abort_container_identity_changed')
        require(not current['running'] or before['running'], 'abort_unexpected_writer')
        require(current['restart_policy'] in [before['restart_policy'], {'Name': 'no', 'MaximumRetryCount': 0}], 'abort_container_policy_changed')
        observed[ident] = current
    P.store_contract(json.loads(B.command(['/usr/bin/docker', 'info', '--format', '{"root":{{json .DockerRootDir}},"driver":{{json .Driver}},"driver_status":{{json .DriverStatus}},"live_restore":{{json .LiveRestoreEnabled}}}'])))
    require(json.loads(B.command(['/usr/bin/docker', 'info', '--format', '{"driver":{{json .CgroupDriver}},"version":{{json .CgroupVersion}}}']))
            == {'driver': 'systemd', 'version': '2'}, 'cgroup_runtime_changed')
    containerd_contract({**plan, 'containers': list(observed.values())}, 'baseline')
    for ident in plan['datastore_ids']:
        if not observed[ident]['running']: B.command(['/usr/bin/docker', 'start', ident])
    # PostgreSQL/Redis readiness must succeed BEFORE applications are restarted.
    probes = {**plan, 'health_probes': []}
    for attempt in range(30):
        try: old_datastores_and_api_ready(probes); break
        except (P.Refusal, B.Refusal):
            if attempt == 29: raise
            remaining_contract(plan, 30); time.sleep(2)
    for ident, before in expected.items():
        if before['running'] and ident not in plan['datastore_ids'] and not observed[ident]['running']:
            B.command(['/usr/bin/docker', 'start', ident])
    for attempt in range(30):
        try: old_datastores_and_api_ready(plan); break
        except (P.Refusal, B.Refusal):
            if attempt == 29: raise
            remaining_contract(plan, 30); time.sleep(2)
    for row in plan['containers']:
        policy = row['restart_policy']
        label = policy['Name'] + (':' + str(policy['MaximumRetryCount']) if policy['Name'] == 'on-failure' and policy['MaximumRetryCount'] > 0 else '')
        B.command(['/usr/bin/docker', 'update', '--restart=' + label, row['id']])
    for timer in (False, True):
        for unit in plan['units']:
            if unit['active'] and unit['kind'] != 'oneshot' and (unit['kind'] == 'timer') == timer:
                B.command(['/usr/bin/systemctl', 'start', unit['name']])
    invariant_contract(plan, digest, owned=True); runtime_contract(plan, 'baseline')
    old_datastores_and_api_ready(plan)
    value.update(phase='aborted_restored', aborted_from=value['phase'], security_transition_completed=False)
    write_json(paths(plan)[0], value, exclusive=False)


def run(plan, path, digest, apply):
    invariant_contract(plan, digest, owned=False)
    runtime_contract(plan, 'baseline')
    require(package_versions(P.OLD_PACKAGES) == {n: p[0] for n, p in P.OLD_PACKAGES.items()}, 'old_package_versions_changed')
    verify_private_inputs(plan)
    apt_scope(B.command(apt_argv(plan, simulation=True)))
    require(not os.path.lexists('/usr/sbin/policy-rc.d'), 'existing_service_policy_requires_review')
    available = os.statvfs(Path(plan['backup_root']).parent)
    require(available.f_bavail * available.f_frsize >= 2 * (MAX_ARCHIVE + 4096) + FREE_FLOOR + 16 * 1024**3,
            'initial_backup_capacity_insufficient')
    offhost(plan, digest, {'ciphertext_size_bytes': MAX_ARCHIVE + 4096, 'ciphertext_sha256': '0' * 64}, verify=None)
    if not apply:
        print('{"status":"preflight_passed","runtime_changed":false}')
        return True
    deadline = remaining_contract(plan, MINIMUM_START)
    with ExitStack() as stack:
        descriptors, owned = [], []
        fd = os.open(P.MARKER, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK)
        stack.callback(os.close, fd); descriptors.append(fd)
        require(B.identity(os.fstat(fd)) == B.identity(P.MARKER.lstat()), 'marker_fd_changed')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        invariant_contract(plan, digest, owned=False); runtime_contract(plan, 'baseline')
        value = {'schema': 'web_engine_transaction/1', 'operation_id': plan['operation_id'],
                 'nonce': plan['nonce'], 'manifest_sha256': digest, 'pid': os.getpid(), 'phase': 'reserved'}
        for target in (paths(plan)[1], P.RESERVATION):
            fd, temporary = tempfile.mkstemp(prefix='.web-engine-reservation-', dir=target.parent)
            stack.callback(os.close, fd); descriptors.append(fd)
            stack.callback(lambda t=temporary: Path(t).unlink(missing_ok=True))
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            B.write_all(fd, (json.dumps(value, sort_keys=True) + '\n').encode()); os.fsync(fd)
            B.publish_noreplace(temporary, target)
            owned.append((target, os.fstat(fd).st_dev, os.fstat(fd).st_ino))
            directory = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try: os.fsync(directory)
            finally: os.close(directory)
        write_json(paths(plan)[0], value, exclusive=True)
        try:
            invariant_contract(plan, digest, owned=True)
            argv = [plan['ansible_binary'], '-i', str(HERE / 'web_engine_local_inventory.yml'),
                    str(HERE / 'web_engine_transaction.yml'), '-e', json.dumps({'web_engine_manifest': str(path), 'web_engine_manifest_sha256': digest})]
            env = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL': 'C', 'DOCKER_HOST': 'unix:///var/run/docker.sock',
                   'ANSIBLE_HOME': str(Path(plan['backup_root']).parent / '.web-engine-ansible-home'),
                   'ANSIBLE_LOCAL_TEMP': str(Path(plan['backup_root']).parent / '.web-engine-ansible-tmp'),
                   'ANSIBLE_CONFIG': str(PROJECT / 'deploy/maintenance/ansible.cfg')}
            # Aggregate pre-install work cannot consume the restore reserve.
            cutoff = (deadline - timedelta(seconds=PREINSTALL_MARGIN)).isoformat().replace('+00:00', 'Z')
            require(execute_stage(plan, digest, argv, env, cutoff, descriptors, 'pre_install') == 0, 'ansible_failed_fence_retained')
            require(journal(plan, digest)['phase'] == 'backup_ready', 'preinstall_evidence_incomplete')
            remaining_contract(plan, PREINSTALL_MARGIN)
            require(execute_stage(plan, digest, argv, env, plan['deadline_utc'], descriptors, 'install_restore') == 0, 'ansible_failed_fence_retained')
            invariant_contract(plan, digest, owned=True); runtime_contract(plan, 'complete')
            require(journal(plan, digest)['phase'] == 'complete', 'completion_evidence_missing')
            release_owned_fences(owned)
            print('{"status":"completed","authority_changed":false,"reboot_performed":false}')
            return True
        except BaseException as failure:
            if isinstance(failure, StageLivenessError): raise
            try:
                abort_restore(plan, digest)
                release_owned_fences(owned)
                print('{"status":"aborted_restored","security_transition_completed":false,"authority_changed":false}')
                return False
            except BaseException:
                pass  # Not an admissible pre-install restore; retain identity.
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['run', 'inspect', 'checkpoint', 'market-backup', 'backup', 'install'])
    parser.add_argument('--manifest', required=True, type=Path)
    parser.add_argument('--manifest-sha256', required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--phase', choices=['quiescing', 'writers_quiesced', 'quiesced', 'engines_verified', 'datastores', 'healthy', 'complete'])
    args = parser.parse_args()
    try:
        plan = P.document(args.manifest, args.manifest_sha256)
        if args.mode == 'run': return 0 if run(plan, args.manifest, args.manifest_sha256, args.apply) else 1
        elif args.mode == 'inspect':
            invariant_contract(plan, args.manifest_sha256, owned=True)
            print(json.dumps({k: plan[k] for k in ('units', 'containers', 'datastore_ids')}))
        elif args.mode == 'backup': engine_backup(plan, args.manifest_sha256)
        elif args.mode == 'market-backup': market_backup(plan, args.manifest_sha256)
        elif args.mode == 'install': install(plan, args.manifest_sha256)
        else: checkpoint(plan, args.manifest_sha256, args.phase)
        return 0
    except BaseException as error:
        print(json.dumps({'status': 'refused', 'code': str(error) if isinstance(error, (P.Refusal, B.Refusal)) else 'unexpected_failure'}), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
