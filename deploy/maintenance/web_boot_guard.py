#!/usr/bin/env python3
"""Four fixed Web boot drop-ins after the completed CE transaction only.

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

OWNER = 0
PRIMARY = '/srv/trading-bot-three-site-staging-data'
UUID = 'e7f47458-ffc1-4181-81e1-58c6fc4519a2'
ROOT_UUID = 'dce03124-bc1e-47f1-8767-b5bf64a7664a'
MOUNT = 'srv-trading\\x2dbot\\x2dthree\\x2dsite\\x2dstaging\\x2ddata.mount'
SYSTEMD = Path('/etc/systemd/system')
RUNTIME = Path('/run/systemd/system')
TOKEN = Path('/run/trading-bot/market-capture-legacy-authority-enabled')
WORK = Path('/root/.local/security-web-engine-20261024-task07')
CONTROL = Path('/root/secure-envs/trading-bot/release-control')
MARKER = CONTROL.parent / 'queue-cutover-artifacts/production-release.lock'
PENDING = Path('/var/lib/trading-bot/production-release/two-host-release-state.json')
OP = 'security-web-engine-20261023-07'
NONCE = '304b2ec68d304738a43794f5a666b6c8'
C_SHA = '81b8983cb0f790c95d0e51c3f3225bd60aa5eb8ef5037dd9c20f8dbc549e64b9'
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
           '"running":{{json .State.Running}},"restart_policy":{{json .HostConfig.RestartPolicy}},'
           '"mounts":{{json .Mounts}},"network_names":{{json .NetworkSettings.Networks}},'
           '"network_mode":{{json .HostConfig.NetworkMode}},"port_bindings":{{json .HostConfig.PortBindings}},'
           '"health":{{with index .State "Health"}}{{json .Status}}{{else}}null{{end}}}')


def require(ok, code):
    if not ok:
        raise RuntimeError(code)


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


def completed(digest):
    require(re.fullmatch('[a-f0-9]{64}', digest), 'invalid_completed_manifest')
    absent = (PENDING, CONTROL / 'private-primary-controller.lock', CONTROL / (OP + '.source.lock'),
              CONTROL.parent / '.production-runtime-source.lock',
              CONTROL.parent / 'market-pipeline-cutover/bot-legacy-handoff-4ef8e6dc.json')
    require(not any(os.path.lexists(path) for path in absent), 'maintenance_active')
    raw = read(WORK / 'plan.json', 2097152, True)
    require(sha256(raw).hexdigest() == digest, 'completed_plan_changed')
    plan = json.loads(raw)
    require(plan['schema'] == 'web_engine_transaction_plan/1' and plan['operation_id'] == OP and plan['nonce'] == NONCE, 'wrong_completed_plan')
    receipt = json.loads(read(CONTROL / (OP + '.journal.json'), 2097152, True))
    require(receipt['schema'] == 'web_engine_transaction/1' and receipt['operation_id'] == OP and receipt['nonce'] == NONCE
            and receipt['manifest_sha256'] == digest and receipt['phase'] == 'complete', 'ce_not_complete')
    controller = WORK / 'source/deploy/maintenance/web_engine_controller.py'
    require(sha256(read(controller)).hexdigest() == C_SHA and
            sum(row['path'] == str(controller) and row['sha256'] == C_SHA for row in plan['files']) == 1, 'completed_controller_changed')
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


def runtime(plan, bus):
    require(run(DOCKER + ['version', '--format', '{{.Server.Version}}']) == '29.9.2', 'ce_not_active')
    require(re.search(r'^Server:\s*\n\s*Version:\s*v?2\.4\.2\s*$',
                      run(['/usr/bin/ctr', '--address', '/run/containerd/containerd.sock', 'version']), re.MULTILINE), 'containerd_not_patched')
    require(re.search(r'^runc version 1\.5\.2\b', run(['/usr/bin/runc', '--version'])), 'runc_not_patched')
    rows = plan['containers']
    require(len(rows) == 27 and len({row['id'] for row in rows}) == 27
            and sum(row['running'] for row in rows) == 26, 'plan_container_scope')
    identifiers = run(DOCKER + ['ps', '--all', '--quiet', '--no-trunc']).splitlines()
    require(len(identifiers) == 27 and set(identifiers) == {row['id'] for row in rows}, 'container_inventory_changed')
    sources = set()
    for row in rows:
        actual = json.loads(run(DOCKER + ['inspect', '--format', INSPECT, row['id']]))
        require(all(actual[key] == row[key] for key in ('id', 'name', 'image', 'running', 'restart_policy', 'network_mode', 'port_bindings'))
                and canonical_mounts(actual['mounts']) == row['mounts']
                and sorted(actual['network_names']) == row['network_names'], 'container_baseline_changed')
        require(not actual['running'] or actual['health'] in {None, 'healthy'}, 'container_unhealthy')
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
    require({row['name'] for row in plan['units']} == set(CONSUMERS), 'native_scope_changed')
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
    return {'containers': 27, 'prior_running': 26, 'unique_sources': len(sources), 'nonroot_mounts': sorted(roots)}


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
                    require(info['What'] == '/dev/disk/by-uuid/' + UUID, 'uuid_override_not_loaded')
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


def execute(action, digest):
    require(action in {'check', 'setup', 'cleanup'}, 'invalid_action')
    native()
    with locked_marker(), pinned() as bus:
        plan = completed(digest)
        mounted = primary(bus)
        closure = runtime(plan, bus)
        targets = entries(action, False, bus)
        if action == 'check':
            return {'status': 'checked', 'runtime_changed': False, 'configuration_changed': False, 'mount_union': closure}
        changed = False
        for item in targets:
            completed(digest)
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
        completed(digest)
        require(primary(bus) == mounted, 'mounted_identity_changed')
        entries(action, True, bus)
        require(runtime(plan, bus) == closure, 'runtime_closure_changed')
        return {'status': 'persistent_guards_installed' if action == 'setup' else 'owned_guards_removed',
                'runtime_changed': False, 'configuration_changed': changed, 'mount_union': closure}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--action', choices=('check', 'setup', 'cleanup'), default='check')
    parser.add_argument('--completed-manifest-sha256', required=True)
    parser.add_argument('--confirmation', default='')
    args = parser.parse_args()
    require(args.action == 'check' or args.confirmation == 'WEB-BOOT-GUARDS-20261024', 'explicit_action_required')
    print(json.dumps(execute(args.action, args.completed_manifest_sha256), sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('{"status":"refused","code":"web_boot_guards_refused"}')
        raise SystemExit(1)
