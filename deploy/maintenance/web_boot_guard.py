#!/usr/bin/env python3
"""Four fixed Web boot drop-ins after the completed current08 transaction only.

No reboot, restart, enablement, remount, application import or private env/key
read. The current marker flock is held through writes, reload and verification.
"""
from contextlib import contextmanager
from hashlib import sha256
import argparse
import configparser
import ctypes
import fcntl
import json
import os
from pathlib import Path
import re
import resource
import shlex
import stat
import subprocess
import tempfile
from types import ModuleType

OWNER = 0
PRIMARY = '/srv/trading-bot-three-site-staging-data'
UUID = 'e7f47458-ffc1-4181-81e1-58c6fc4519a2'
ROOT_UUID = 'dce03124-bc1e-47f1-8767-b5bf64a7664a'
MOUNT = 'srv-trading\\x2dbot\\x2dthree\\x2dsite\\x2dstaging\\x2ddata.mount'
SYSTEMD = Path('/etc/systemd/system')
RUNTIME = Path('/run/systemd/system')
TOKEN = Path('/run/trading-bot/market-capture-legacy-authority-enabled')
WORK = Path('/root/.local/security-web-engine-20261024-task08')
CONTROL = Path('/root/secure-envs/trading-bot/release-control')
MARKER = CONTROL.parent / 'queue-cutover-artifacts/production-release.lock'
PENDING = Path('/var/lib/trading-bot/production-release/two-host-release-state.json')
OP = 'security-web-engine-20261024-08'
NONCE = 'a872a41985d8f931cd856f50068c75f2'
C_SHA = '77ca1a9dc477d9a2057e6b33c61871bba529a986ceeb1bdd16a0da5f56a56f9e'
PROFILE_SHA = 'e28f258a86da83b435392abc6b1d9d997137a54f8dd600ead61b36455da0ec6a'
CONFIRMATION = 'WEB-BOOT-GUARDS-CURRENT08-20261024'
COLLECTORS = ('coin-capture.service', 'market-channel-capture.service')
CONSUMERS = (*COLLECTORS, 'coin-intelligence-capture-shadow-external.service',
             'coin-intelligence-capture-shadow-external.timer',
             'coin-intelligence-capture-shadow-input.service',
             'coin-intelligence-capture-shadow-input.timer',
             'market-pipeline-watchdog.service', 'market-pipeline-watchdog.timer',
             'trading-bot-sync-health-sampler.service', 'trading-bot-sync-health-sampler.timer')
BODY = '[Unit]\nConditionPathExists=/run/trading-bot/market-capture-legacy-authority-enabled\nRefuseManualStart=yes\n'
HEADER = '# Owned Web boot guards 2026-10-24; exact-content cleanup only.\n'
NAME = '90-security-20261024-web-boot.conf'
VOLUMES = {'three-site-727c1f337b75-webapp-fi_webapp_fi_postgres': 'three-site-727c1f337b75/webapp_fi/postgres',
           **{'three-site-dc32d90392b0-webapp-fi_webapp_fi_' + kind: 'three-site-dc32d90392b0/webapp_fi/' + kind
              for kind in ('audit', 'postgres', 'redis', 'uploads')}}
DOCKER = ['/usr/bin/docker', '--host=unix:///var/run/docker.sock']
INSPECT = ('{"id":{{json .Id}},"name":{{json .Name}},"image":{{json .Image}},'
           '"running":{{json .State.Running}},"restarting":{{json .State.Restarting}},'
           '"project":{{json (index .Config.Labels "com.docker.compose.project")}},"restart_policy":{{json .HostConfig.RestartPolicy}},'
           '"mounts":{{json .Mounts}},"network_names":{{json .NetworkSettings.Networks}},'
           '"network_mode":{{json .HostConfig.NetworkMode}},"port_bindings":{{json .HostConfig.PortBindings}},'
           '"health":{{with index .State "Health"}}{{json .Status}}{{else}}null{{end}}}')


class BootGuardRefusal(RuntimeError):
    """Only this helper's static reason codes may enter its public result."""


def require(ok, code):
    if not ok:
        raise BootGuardRefusal(code)


def safe_reason(error):
    value = str(error)
    return value if type(error) is BootGuardRefusal and re.fullmatch('[a-z][a-z0-9_]{0,79}', value) else 'web_boot_guards_refused'


def identity(value):
    return (value.st_dev, value.st_ino, value.st_mode, value.st_uid, value.st_nlink,
            value.st_size, value.st_mtime_ns, value.st_ctime_ns)


def metadata(path, directory=False):
    path = Path(path)
    require(path.resolve(strict=True) == path, 'redirected_path')
    for parent in path.parents:
        info = parent.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid in {0, OWNER} and not info.st_mode & 0o022, 'unsafe_parent')
    info = path.lstat()
    require(info.st_uid == OWNER and not info.st_mode & 0o022 and
            (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode) and info.st_nlink == 1), 'unsafe_metadata')
    return info


def read(path, maximum=65536, private=False):
    path = Path(path)
    before = metadata(path)
    require(before.st_size <= maximum and (not private or not before.st_mode & 0o077), 'unsafe_control')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        require(identity(before) == identity(os.fstat(fd)), 'control_changed')
        data = os.read(fd, maximum + 1)
        require(len(data) == before.st_size and identity(before) == identity(os.fstat(fd)) == identity(path.lstat()), 'control_changed')
        return data
    finally:
        os.close(fd)


def run(argv):
    maximum = 2097152
    def limits():
        resource.setrlimit(resource.RLIMIT_FSIZE, (maximum, maximum))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    with tempfile.TemporaryFile() as output:
        with subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.DEVNULL,
                              preexec_fn=limits, env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL': 'C'}) as child:
            try:
                code = child.wait(timeout=15)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)
                raise RuntimeError('metadata_timeout') from None
        require(code == 0, 'metadata_failed')
        output.seek(0)
        data = output.read(maximum + 1)
        require(len(data) < maximum, 'metadata_bound')
        return data.decode().strip()


def props(unit):
    keys = 'LoadState,UnitFileState,ActiveState,FragmentPath,DropInPaths,RequiresMountsFor,NeedDaemonReload,WorkingDirectory,ReadOnlyPaths,ReadWritePaths,What,Where,Type,Options'
    return dict(line.split('=', 1) for line in run(['/usr/bin/systemctl', 'show', '--property=' + keys, '--', unit]).splitlines())


@contextmanager
def pinned():
    import dbus
    bus = dbus.SystemBus(private=True)
    acquired = []
    try:
        manager = dbus.Interface(bus.get_object('org.freedesktop.systemd1', '/org/freedesktop/systemd1'), 'org.freedesktop.systemd1.Manager')
        for unit in sorted(set(CONSUMERS) | {'docker.service', MOUNT}):
            manager.RefUnit(unit, timeout=5)
            acquired.append(unit)
        yield bus
    finally:
        for unit in reversed(acquired):
            try:
                manager.UnrefUnit(unit, timeout=5)
            except Exception:
                pass
        bus.close()


def property_value(bus, unit, name):
    import dbus
    manager = dbus.Interface(bus.get_object('org.freedesktop.systemd1', '/org/freedesktop/systemd1'), 'org.freedesktop.systemd1.Manager')
    obj = manager.GetUnit(unit, timeout=5)
    return dbus.Interface(bus.get_object('org.freedesktop.systemd1', obj), 'org.freedesktop.DBus.Properties').Get('org.freedesktop.systemd1.Unit', name, timeout=5)


def source_mount(path):
    found = json.loads(run(['/usr/bin/findmnt', '--json', '--target', str(Path(path).resolve(strict=True)),
                            '--output', 'TARGET,SOURCE,FSTYPE,UUID,OPTIONS']))['filesystems']
    require(len(found) == 1, 'ambiguous_mount')
    return found[0]


def primary(bus):
    info = metadata(PRIMARY, True)
    found = source_mount(PRIMARY)
    require(found['target'] == PRIMARY and found['uuid'] == UUID and found['fstype'] == 'ext4'
            and 'rw' in found['options'].split(',') and found['source'].startswith('/dev/'), 'wrong_primary')
    unit = props(MOUNT)
    require(unit['LoadState'] == 'loaded' and unit['UnitFileState'] == 'enabled' and unit['ActiveState'] == 'active'
            and unit['Where'] == PRIMARY and unit['Type'] == 'ext4', 'wrong_mount_unit')
    require(unit['FragmentPath'] == str(SYSTEMD / MOUNT), 'wrong_primary_fragment')
    require(set(map(str, property_value(bus, MOUNT, 'DropInPaths'))) <= {str(SYSTEMD / (MOUNT + '.d') / NAME)}, 'foreign_mount_dropin')
    base = configparser.ConfigParser(interpolation=None)
    base.read_string(read(unit['FragmentPath']).decode())
    require(base['Mount']['Where'] == PRIMARY and base['Mount']['Type'] == 'ext4'
            and base['Mount']['What'] in {found['source'], '/dev/disk/by-uuid/' + UUID}, 'mount_source_changed')
    require(unit['What'] in {found['source'], '/dev/disk/by-uuid/' + UUID}, 'mount_what_changed')
    require(Path('/dev/disk/by-uuid/' + UUID).resolve(strict=True) == Path(found['source']).resolve(strict=True), 'uuid_device_changed')
    return {'device': info.st_dev, 'inode': info.st_ino, 'mounted': found}


def document(path, digest=None, maximum=2097152):
    raw = read(path, maximum, True)
    require(digest is None or sha256(raw).hexdigest() == digest, 'completed_control_changed')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, 'duplicate_control_key')
            result[key] = value
        return result
    value = json.loads(raw, object_pairs_hook=unique,
                       parse_constant=lambda _: require(False, 'invalid_control_constant'))
    require(type(value) is dict, 'invalid_control_shape')
    return value


def equal(left, right):
    if type(left) is not type(right):
        return False
    if type(right) is dict:
        return set(left) == set(right) and all(equal(left[k], v) for k, v in right.items())
    if type(right) is list:
        return len(left) == len(right) and all(equal(a, b) for a, b in zip(left, right))
    return left == right


def current_profile(plan):
    path = WORK / 'source/deploy/maintenance/web_engine_current_profile.py'
    source = read(path, 65536)
    require(sha256(source).hexdigest() == PROFILE_SHA, 'completed_profile_source_changed')
    recipe = ModuleType('_sealed_web_boot_current_profile')
    exec(compile(source, str(path), 'exec'), recipe.__dict__)
    buffers = {name: read(WORK / 'evidence' / (name + '.json'), 2097152, True)
               for name in recipe.CONTROL_SHA256}
    profile = recipe.derive_current_profile(buffers)
    require(equal(plan['current_profile'], profile) and equal(plan['containers'], profile['containers'])
            and len(profile['containers']) == 27 and profile['expected_running'] == 16
            and len(profile['parked_ids']) == 10 and len(profile['retained_ids']) == 17,
            'completed_current_profile_changed')
    old = json.loads(buffers['c07_plan'])
    require(all(equal(plan[key], old[key]) for key in ('marker', 'units', 'engine_intent', 'store_roots')),
            'completed_web_metadata_changed')
    return profile


def completed(digest, journal_digest, cleanup_digest):
    require(all(type(v) is str and re.fullmatch('[a-f0-9]{64}', v)
                for v in (digest, journal_digest, cleanup_digest)), 'invalid_completed_manifest')
    absent = (PENDING, CONTROL / 'private-primary-controller.lock', CONTROL / (OP + '.source.lock'),
              CONTROL / 'security-web-engine-20261023-07.source.lock',
              CONTROL.parent / '.production-runtime-source.lock',
              CONTROL.parent / 'market-pipeline-cutover/bot-legacy-handoff-4ef8e6dc.json')
    require(not any(os.path.lexists(path) for path in absent), 'maintenance_active')
    plan = document(WORK / 'plan.json', digest)
    require(plan['schema'] == 'web_engine_current_plan/1' and plan['operation_id'] == OP
            and plan['nonce'] == NONCE and plan['source_root'] == str(WORK / 'source'), 'wrong_completed_plan')
    receipt = document(CONTROL / (OP + '.journal.json'), journal_digest)
    require(receipt['schema'] == 'web_engine_current_transaction/1' and receipt['operation_id'] == OP
            and receipt['nonce'] == NONCE and receipt['manifest_sha256'] == digest and receipt['phase'] == 'complete'
            and receipt['backup_requests_started'] is True and receipt['backup_requests_complete'] is True
            and receipt['cold_archive_compared'] is True and receipt['fresh_dump_in_cold_archive'] is True
            and receipt['security_accepted'] is False and receipt['deployment_eligible'] is False
            and receipt['reboot_performed'] is False, 'ce_not_complete')
    fresh = receipt['fresh_quiesced_backup']
    require(fresh['schema'] == 'web_engine_fresh_quiesced_operand/1' and fresh['operation_id'] == OP
            and fresh['nonce'] == NONCE and fresh['backup_mode'] == 'quiesced'
            and fresh['restore_status'] == fresh['cleanup_status'] == 'PASS', 'completed_backup_missing')
    offhost = receipt['offhost']
    require(offhost['status'] == 'verified' and offhost['operation_id'] == OP and offhost['nonce'] == NONCE
            and offhost['manifest_sha256'] == digest and offhost['fsync_completed'] is True
            and offhost['plaintext_materialized_offhost'] is False, 'completed_offhost_missing')
    cleanup = document(Path('/srv/trading-bot') / (OP + '-' + NONCE) / 'watch-cleanup.json', cleanup_digest)
    require(cleanup['schema'] == 'web_engine_current_owned_control/1' and cleanup['control_name'] == 'watch-cleanup'
            and cleanup['operation_id'] == OP and cleanup['nonce'] == NONCE and cleanup['manifest_sha256'] == digest
            and cleanup['all_owned_resources_absent'] is True and cleanup['worker_succeeded'] is True
            and cleanup['marker_retained'] is True, 'completed_cleanup_missing')
    controller = WORK / 'source/deploy/maintenance/web_engine_current_controller.py'
    require(sha256(read(controller, 131072)).hexdigest() == C_SHA, 'completed_controller_changed')
    # The expired engine window authenticates published source bytes only. It
    # supplies no authority to install guards, restart services or reboot.
    window = document(WORK / 'window.json', plan['window_sha256'])
    require(window['schema'] == 'web_engine_current_window/1' and window['operation_id'] == OP
            and window['nonce'] == NONCE and window['reboot_allowed'] is False
            and window['approved_sources_sha256']['deploy/maintenance/web_engine_current_controller.py'] == C_SHA
            and window['approved_sources_sha256']['deploy/maintenance/web_engine_current_profile.py'] == PROFILE_SHA,
            'completed_source_window_changed')
    profile = current_profile(plan)
    require(all(equal(fresh[key], value) for key, value in {
        'release_sha': profile['configuration_source']['release_sha'],
        'release_tree': profile['configuration_source']['release_tree'],
        'image_id': profile['source_image']['image_id'],
        'image_input_signature': profile['source_image']['image_input_signature']}.items()), 'completed_backup_identity')
    marker = plan['marker']
    info = metadata(MARKER)
    require(marker['path'] == str(MARKER) and (info.st_dev, info.st_ino) == (marker['device'], marker['inode'])
            and (len(read(MARKER, 8192, True)), sha256(read(MARKER, 8192, True)).hexdigest()) == (marker['size'], marker['sha256']), 'marker_changed')
    return plan


def canonical_mounts(rows):
    keys = ('Destination', 'Mode', 'Propagation', 'RW', 'Source', 'Type')
    result = [{key: row[key] for key in keys} for row in rows]
    require(len({row['Destination'] for row in result}) == len(result), 'duplicate_destination')
    return sorted(result, key=lambda row: row['Destination'])


def parked_storage(plan):
    resources = plan['current_profile']['parked_resources']
    for before in [resources['parent_mount'], *[v['backing_identity'] for v in resources['volumes'].values()]]:
        path = Path(before['path'])
        require(path.is_absolute() and path.resolve(strict=True) == path, 'parked_backing_redirected')
        info = path.lstat()
        actual = {'path': str(path), 'device': info.st_dev, 'inode': info.st_ino,
                  'uid': info.st_uid, 'gid': info.st_gid, 'mode': stat.S_IMODE(info.st_mode)}
        require(stat.S_ISDIR(info.st_mode) and equal(actual, {k: v for k, v in before.items() if k != 'uuid'}),
                'parked_backing_changed')
        found = source_mount(path)
        require(found['target'] == PRIMARY and found['uuid'] == UUID and found['fstype'] == 'ext4',
                'parked_backing_mount_changed')
    template = ('{"name":{{json .Name}},"driver":{{json .Driver}},"mountpoint":{{json .Mountpoint}},'
                '"options":{{json .Options}},"labels":{{json .Labels}}}')
    for name, before in resources['volumes'].items():
        actual = json.loads(run(DOCKER + ['volume', 'inspect', '--format', template, name]))
        require(equal(actual, before['record']), 'parked_volume_changed')
    # Metadata only, matching the completed controller's nine-field binding.
    # Never open, copy, parse or hash a credential/Env/session file here.
    for name, before in resources['secret_metadata'].items():
        path = Path(name)
        require(path.is_absolute() and not path.is_symlink(), 'parked_file_metadata_changed')
        info = path.lstat()
        actual = [info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
                  info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns]
        require(equal(actual, before), 'parked_file_metadata_changed')


def runtime(plan, bus):
    require(run(DOCKER + ['version', '--format', '{{.Server.Version}}']) == '29.9.2', 'ce_not_active')
    require(re.search(r'^Server:\s*\n\s*Version:\s*v?2\.4\.2\s*$',
                      run(['/usr/bin/ctr', '--address', '/run/containerd/containerd.sock', 'version']), re.MULTILINE), 'containerd_not_patched')
    require(re.search(r'^runc version 1\.5\.2\b', run(['/usr/bin/runc', '--version'])), 'runc_not_patched')
    rows = plan['containers']
    require(len(rows) == 27 and len({row['id'] for row in rows}) == 27
            and all(type(row['running']) is bool for row in rows)
            and sum(row['running'] for row in rows) == 16
            and equal(rows, plan['current_profile']['containers']), 'plan_container_scope')
    parked = plan['current_profile']['parked_ids']
    require(len(parked) == len(set(parked)) == 10
            and all(row['running'] is False and equal(row['restart_policy'], {'Name': 'no', 'MaximumRetryCount': 0})
                    for row in rows if row['id'] in parked)
            and {row['id'] for row in rows if row['id'] in parked} == set(parked), 'qa_parking_changed')
    parked_storage(plan)
    known = plan['current_profile']['known_health_incidents']
    require(known['status'] == 'open' and known['acceptance_waived'] is False
            and type(known['ids']) is list and len(known['ids']) == len(set(known['ids'])) == 2,
            'known_health_scope_changed')
    incidents = []
    identifiers = run(DOCKER + ['ps', '--all', '--quiet', '--no-trunc']).splitlines()
    require(len(identifiers) == 27 and set(identifiers) == {row['id'] for row in rows}, 'container_inventory_changed')
    sources = set()
    for row in rows:
        actual = json.loads(run(DOCKER + ['inspect', '--format', INSPECT, row['id']]))
        require(actual['restarting'] is False
                and all(equal(actual[key], row[key]) for key in ('id', 'name', 'image', 'project', 'running', 'restart_policy', 'network_mode', 'port_bindings'))
                and equal(canonical_mounts(actual['mounts']), row['mounts'])
                and equal(sorted(actual['network_names']), row['network_names']), 'container_baseline_changed')
        if actual['running'] and actual['health'] not in {None, 'healthy'}:
            require(actual['id'] in known['ids'], 'container_unhealthy')
            incidents.append(actual['id'])
        sources.update(entry['Source'] for entry in actual['mounts'])
    roots = set()
    for path in sources:
        require(Path(path).is_absolute() and not any(c.isspace() for c in path), 'source_path_unsafe')
        found = source_mount(path)
        require(found['fstype'] == 'ext4', 'source_fstype_changed')
        if found['target'] == '/':
            require(found['uuid'] == ROOT_UUID, 'root_uuid_changed')
        elif found['target'] == PRIMARY:
            require(found['uuid'] == UUID, 'primary_uuid_changed')
            roots.add(PRIMARY)
        else:
            match = re.fullmatch(r'/var/lib/docker/volumes/([A-Za-z0-9_.-]+)/_data', found['target'])
            require(match is not None and match[1] in VOLUMES and found['uuid'] == UUID, 'unknown_nested_mount')
            volume = json.loads(run(DOCKER + ['volume', 'inspect', '--format', '{{json .}}', match[1]]))
            require(volume['Driver'] == 'local' and volume['Mountpoint'] == found['target']
                    and volume['Options'] == {'device': PRIMARY + '/' + VOLUMES[match[1]], 'o': 'bind', 'type': 'none'}, 'volume_source_changed')
            require(source_mount(volume['Options']['device'])['target'] == PRIMARY, 'volume_backing_mount_changed')
            roots.add(PRIMARY)
    require(roots == {PRIMARY}, 'attached_source_missing')
    require(len(plan['units']) == len(CONSUMERS) and {row['name'] for row in plan['units']} == set(CONSUMERS), 'web_consumer_scope_changed')
    for row in plan['units']:
        info = props(row['name'])
        require(info['LoadState'] == 'loaded' and info['UnitFileState'] == row['enabled']
                and info['FragmentPath'] == row['fragment_path'], 'native_intent_changed')
        if row['kind'] != 'oneshot':
            require((info['ActiveState'] == 'active') == row['active'], 'native_activity_changed')
        else:
            require(info['ActiveState'] in {'inactive', 'active', 'activating'}, 'native_oneshot_unhealthy')
        require(sha256(read(row['fragment_path'])).hexdigest() == row['fragment_sha256'], 'native_fragment_changed')
        extra = str(SYSTEMD / (row['name'] + '.d') / NAME) if row['name'] in COLLECTORS else None
        require(set(map(str, property_value(bus, row['name'], 'DropInPaths'))) == {entry['path'] for entry in row['dropins']} | ({extra} if extra and os.path.lexists(extra) else set()), 'native_dropins_changed')
        for entry in row['dropins']:
            require(sha256(read(entry['path'])).hexdigest() == entry['sha256'], 'native_dropin_changed')
        for path in shlex.split(' '.join(info.get(key, '') for key in ('WorkingDirectory', 'ReadOnlyPaths', 'ReadWritePaths'))):
            require(source_mount(path.lstrip('-+'))['target'] == '/', 'new_native_attached_consumer')
    return {'containers': 27, 'prior_running': 16, 'parked_qa': 10,
            'unique_sources': len(sources), 'nonroot_mounts': sorted(roots),
            'known_health_incidents_status': 'open', 'observed_known_health_incidents': sorted(incidents),
            'health_incident_acceptance_waived': False}


def desired():
    return {**{unit: HEADER + BODY for unit in COLLECTORS},
            'docker.service': HEADER + '[Unit]\nRequiresMountsFor=' + PRIMARY + '\nAssertPathIsMountPoint=' + PRIMARY + '\n',
            MOUNT: HEADER + '[Mount]\nWhat=/dev/disk/by-uuid/' + UUID + '\n'}


def entries(action, verify, bus):
    require(not os.path.lexists(TOKEN), 'legacy_authority_token_present')
    result = []
    for unit, text in desired().items():
        directory = SYSTEMD / (unit + '.d')
        target = directory / NAME
        info = props(unit)
        loaded = set(map(str, property_value(bus, unit, 'DropInPaths')))
        require(info['LoadState'] == 'loaded' and info['NeedDaemonReload'] == 'no', 'unit_missing_or_reload_pending')
        if os.path.lexists(directory):
            metadata(directory, True)
        exists = os.path.lexists(target)
        if exists:
            require(stat.S_IMODE(metadata(target).st_mode) == 0o644 and read(target) == text.encode(), 'foreign_owned_filename')
        if unit in COLLECTORS:
            runtime_guard = RUNTIME / (unit + '.d') / 'market-container-authority.conf'
            if os.path.lexists(runtime_guard):
                require(read(runtime_guard) == BODY.encode(), 'runtime_authority_changed')
            else:
                require(exists and action != 'cleanup', 'runtime_authority_missing')
            require(info['UnitFileState'] == 'disabled' and info['ActiveState'] in {'inactive', 'failed'}, 'legacy_collector_not_disabled')
            require(loaded <= {str(runtime_guard), str(target)}, 'foreign_collector_dropin')
        elif unit == 'docker.service':
            require(info['UnitFileState'] == 'enabled' and info['ActiveState'] == 'active'
                    and loaded <= {str(target)}, 'docker_intent_changed')
        if verify:
            require(exists == (action == 'setup') and (str(target) in loaded) == (action == 'setup'), 'dropin_not_loaded')
            if action == 'setup':
                if unit in COLLECTORS:
                    require(bool(property_value(bus, unit, 'RefuseManualStart')), 'manual_start_unfenced')
                    require(any(list(row[:4]) == ['ConditionPathExists', False, False, str(TOKEN)]
                                for row in property_value(bus, unit, 'Conditions')), 'condition_not_loaded')
                elif unit == 'docker.service':
                    require(PRIMARY in shlex.split(info['RequiresMountsFor']), 'mount_requirement_missing')
                    require(any(list(row[:4]) == ['AssertPathIsMountPoint', False, False, PRIMARY]
                                for row in property_value(bus, unit, 'Asserts')), 'mount_assertion_missing')
                elif unit == MOUNT:
                    # systemd can canonicalize the loaded UUID alias to the
                    # actual block-device path. Reuse the full mounted UUID,
                    # source and device-resolution proof, not string spelling.
                    mounted = primary(bus)['mounted']
                    require(info['What'] in {'/dev/disk/by-uuid/' + UUID, mounted['source']}, 'uuid_override_not_loaded')
        result.append({'destination': str(target), 'content': text, 'exists': exists})
    return result


def sync_parent(path):
    fd = os.open(Path(path).parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def publish(path, text):
    path = Path(path)
    if not os.path.lexists(path.parent):
        metadata(path.parent.parent, True)
        os.mkdir(path.parent, 0o755)
        sync_parent(path.parent)
    metadata(path.parent, True)
    fd, temporary = tempfile.mkstemp(prefix='.web-boot-owned-', dir=path.parent)
    try:
        os.fchmod(fd, 0o644)
        raw = text.encode()
        while raw:
            written = os.write(fd, raw)
            require(written > 0, 'short_control_write')
            raw = raw[written:]
        os.fsync(fd)
        rename = ctypes.CDLL(None, use_errno=True).renameat2
        rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        rename.restype = ctypes.c_int
        if rename(-100, os.fsencode(temporary), -100, os.fsencode(path), 1):
            raise OSError(ctypes.get_errno(), 'exclusive_dropin_publish_refused')
        sync_parent(path)
    finally:
        os.close(fd)
        Path(temporary).unlink(missing_ok=True)


@contextmanager
def locked_marker():
    before = metadata(MARKER)
    fd = os.open(MARKER, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        require(identity(before) == identity(os.fstat(fd)), 'marker_open_changed')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield fd
        require(identity(before) == identity(os.fstat(fd)) == identity(MARKER.lstat()), 'marker_changed')
    finally:
        os.close(fd)


def native():
    require(os.geteuid() == OWNER and Path('/proc/1/exe').readlink().name == 'systemd'
            and not any(os.path.lexists(path) for path in ('/.dockerenv', '/run/.containerenv')), 'native_required')
    for name in ('pid', 'mnt', 'user'):
        require(os.readlink('/proc/self/ns/' + name) == os.readlink('/proc/1/ns/' + name), 'namespace_changed')
    require(any(item.get('local') == '65.109.220.59'
                for row in json.loads(run(['/usr/sbin/ip', '-j', 'address'])) for item in row.get('addr_info', [])), 'wrong_web')


def execute(action, digest, journal_digest, cleanup_digest, *, profile):
    require(action in {'check', 'setup', 'cleanup'}, 'invalid_action')
    require(profile == 'current08', 'explicit_current_profile_required')
    native()
    with locked_marker(), pinned() as bus:
        plan = completed(digest, journal_digest, cleanup_digest)
        mounted = primary(bus)
        closure = runtime(plan, bus)
        targets = entries(action, False, bus)
        if action == 'check':
            return {'status': 'checked', 'profile': profile, 'setup_preflight_passed': True,
                    'runtime_changed': False, 'configuration_changed': False, 'reboot_authorized': False,
                    'backup_artifact_bytes_reverified': False, 'mount_union': closure}
        changed = False
        for item in targets:
            completed(digest, journal_digest, cleanup_digest)
            require(primary(bus) == mounted, 'mounted_identity_changed')
            path = Path(item['destination'])
            if action == 'setup' and not item['exists']:
                publish(path, item['content'])
                changed = True
            elif action == 'cleanup' and item['exists']:
                require(read(path) == item['content'].encode(), 'owned_cleanup_changed')
                path.unlink()
                sync_parent(path)
                changed = True
        if changed:
            run(['/usr/bin/systemctl', 'daemon-reload'])
        completed(digest, journal_digest, cleanup_digest)
        require(primary(bus) == mounted, 'mounted_identity_changed')
        entries(action, True, bus)
        final_closure = runtime(plan, bus)
        # The same two already-open incidents can flap during daemon-reload;
        # each observation still refuses every other unhealthy identity.
        volatile = 'observed_known_health_incidents'
        require(equal({k: v for k, v in final_closure.items() if k != volatile},
                      {k: v for k, v in closure.items() if k != volatile}), 'runtime_closure_changed')
        return {'status': 'persistent_guards_installed' if action == 'setup' else 'owned_guards_removed',
                'profile': profile, 'runtime_changed': False, 'configuration_changed': changed,
                'reboot_authorized': False, 'backup_artifact_bytes_reverified': False, 'mount_union': final_closure}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--action', choices=('check', 'setup', 'cleanup'), default='check')
    parser.add_argument('--profile', choices=('current08',), required=True)
    parser.add_argument('--completed-manifest-sha256', required=True)
    parser.add_argument('--completed-journal-sha256', required=True)
    parser.add_argument('--completed-cleanup-sha256', required=True)
    parser.add_argument('--confirmation', default='')
    args = parser.parse_args()
    require(args.action == 'check' or args.confirmation == CONFIRMATION, 'explicit_action_required')
    print(json.dumps(execute(args.action, args.completed_manifest_sha256, args.completed_journal_sha256,
                             args.completed_cleanup_sha256, profile=args.profile), sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps({'status': 'refused', 'code': safe_reason(error)}))
        raise SystemExit(1)
