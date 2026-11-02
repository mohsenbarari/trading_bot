#!/usr/bin/env python3
"""Manual, single-attempt owned restore of the exact failed07 pre-install task.

Not an upgrade, data rollback, automatic retry or foreign Writer adoption.
Default check writes no files; explicit restore retains all evidence on failure.
"""
from contextlib import contextmanager, ExitStack
from hashlib import sha256
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import signal
import stat
from types import ModuleType

ROOT = Path('/root/.local/security-web-engine-20261024-task07')
SOURCE = ROOT / 'source/deploy/maintenance/web_engine_controller.py'
SOURCE_SHA = '81b8983cb0f790c95d0e51c3f3225bd60aa5eb8ef5037dd9c20f8dbc549e64b9'
DIGEST = 'f8b4759ea648ec0d8b8528f43c74ecc281e68b2d2fd94431398923c62e0f16c5'
OP = 'security-web-engine-20261023-07'
NONCE = '304b2ec68d304738a43794f5a666b6c8'
UNIT = 'security-web-engine-07-' + NONCE + '.service'
CONFIRM = 'RESTORE-OWNED-WEB-ENGINE-07'


def require(ok, code):
    if not ok:
        raise RuntimeError(code)


def controller():
    require(SOURCE.resolve(strict=True) == SOURCE, 'source_redirected')
    for parent in SOURCE.parents:
        info = parent.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022, 'source_parent_unsafe')
    fd = os.open(SOURCE, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_uid == 0 and before.st_nlink == 1
                and not before.st_mode & 0o022 and before.st_size <= 65536, 'source_metadata')
        raw = os.read(fd, 65537)
        identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        require(len(raw) == before.st_size and identity(before) == identity(os.fstat(fd)) == identity(SOURCE.lstat())
                and sha256(raw).hexdigest() == SOURCE_SHA, 'source_changed')
    finally:
        os.close(fd)
    module = ModuleType('_exact_failed_web_engine_controller')
    module.__file__ = str(SOURCE)
    exec(compile(raw, str(SOURCE), 'exec'), module.__dict__)
    return module


@contextmanager
def pinned(C):
    import dbus
    bus = dbus.SystemBus(private=True)
    acquired = []
    try:
        manager = dbus.Interface(bus.get_object('org.freedesktop.systemd1', '/org/freedesktop/systemd1'),
                                 'org.freedesktop.systemd1.Manager')
        for unit in sorted(C.UNITS | set(C.ENGINES)):
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


def jobs_idle(C, bus):
    import dbus
    manager = dbus.Interface(bus.get_object('org.freedesktop.systemd1', '/org/freedesktop/systemd1'),
                             'org.freedesktop.systemd1.Manager')
    for unit in sorted(C.UNITS | set(C.ENGINES)):
        obj = manager.GetUnit(unit, timeout=5)
        props = dbus.Interface(bus.get_object('org.freedesktop.systemd1', obj), 'org.freedesktop.DBus.Properties')
        require(str(props.Get('org.freedesktop.systemd1.Unit', 'LoadState', timeout=5)) == 'loaded', 'unit_unloaded')
        job = props.Get('org.freedesktop.systemd1.Unit', 'Job', timeout=5)
        require(len(job) == 2 and int(job[0]) == 0 and str(job[1]) == '/', 'unit_job_pending')
        state = str(props.Get('org.freedesktop.systemd1.Unit', 'ActiveState', timeout=5))
        require(state == 'active' if unit in C.ENGINES else state in {'inactive', 'failed'}, 'unit_activity_changed')


def descendants_gone(C, plan):
    state = dict(line.split('=', 1) for line in C.B.command(['/usr/bin/systemctl', 'show', UNIT,
        '--property=ActiveState,MainPID,ExecMainStatus']).splitlines())
    require(state == {'ActiveState': 'failed', 'MainPID': '0', 'ExecMainStatus': '1'}, 'original_runner_not_failed_exited')
    name = 'security-web-engine-' + NONCE + '-pre_install.scope'
    group = '/system.slice/' + name
    load = C.B.command(['/usr/bin/systemctl', 'show', name, '-p', 'LoadState', '--value'])
    if load != 'not-found':
        data = dict(line.split('=', 1) for line in C.B.command(['/usr/bin/systemctl', 'show', name,
            '--property=Description,ControlGroup,Transient,ActiveState,KillMode,Slice']).splitlines())
        require(data.get('Description') == ':'.join(('web-engine', OP, NONCE, DIGEST, 'pre_install'))
                and data.get('Transient') == 'yes' and data.get('KillMode') == 'control-group'
                and data.get('Slice') == 'system.slice' and data.get('ControlGroup') in {'', group}
                and data.get('ActiveState') in {'inactive', 'failed'}, 'old_scope_not_terminal')
    require(C.scope_empty(Path('/sys/fs/cgroup' + group)), 'old_descendants_present')


def no_dump_or_restore(C, plan):
    for ident in plan['datastore_ids']:
        ids = json.loads(C.B.command(['/usr/bin/docker', 'inspect', '--format', '{{json .ExecIDs}}', ident]))
        require(ids in (None, []), 'datastore_exec_active')
    sql = ("SELECT count(*) FROM pg_stat_activity WHERE pid <> pg_backend_pid() "
           "AND application_name IN ('pg_dump','pg_restore')")
    for probe in plan['datastore_probes']:
        if probe['kind'] == 'postgres':
            result = C.B.command(['/usr/bin/docker', 'exec', probe['id'], 'psql', '-X', '-At',
                                  '-U', probe['user'], '-d', probe['database'], '-c', sql])
            require(result == '0', 'dump_or_restore_backend_active')


def proof(C, plan, bus, owners):
    C.native_contract(plan)
    C.control_parent_contract(plan)
    require(plan['operation_id'] == OP and plan['nonce'] == NONCE, 'wrong_operation')
    value = C.journal(plan, DIGEST)
    require(value['phase'] == 'writers_quiesced' and value['pid'] == owners['pid'], 'not_original_failed_phase')
    require(type(owners['pid']) is int and owners['pid'] > 1
            and not os.path.lexists(Path('/proc') / str(owners['pid'])), 'original_owner_not_dead')
    C.remaining_contract(plan, 300)
    descendants_gone(C, plan)
    jobs_idle(C, bus)
    require(not os.path.lexists(C.P.PENDING) and not any(os.path.lexists(path) for path in plan['absent_control_paths']), 'foreign_operation')
    require(C.package_versions(C.P.OLD_PACKAGES) == {name: pin[0] for name, pin in C.P.OLD_PACKAGES.items()}, 'package_boundary_crossed')
    for path in ('/etc/docker/daemon.json', '/etc/containerd/config.toml'):
        require(not os.path.lexists(path), 'vendor_install_config_present')
    marker = plan['marker']
    require(C.P.file_digest(C.P.MARKER, maximum=8192, private=True) == (marker['size'], marker['sha256'])
            and (C.P.MARKER.lstat().st_dev, C.P.MARKER.lstat().st_ino) == (marker['device'], marker['inode']), 'marker_changed')
    for entry in plan['files']:
        require(C.P.file_digest(entry['path'], maximum=2097152, private=entry.get('private', True))
                == (entry['size'], entry['sha256']), 'sealed_file_changed')
    for unit in plan['units']:
        require(sha256(C.B.read(unit['fragment_path'], private=False)).hexdigest() == unit['fragment_sha256'], 'unit_fragment_changed')
        observed = C.B.command(['/usr/bin/systemctl', 'show', unit['name'], '-p', 'DropInPaths', '--value']).split()
        require(observed == [entry['path'] for entry in unit['dropins']], 'unit_dropins_changed')
        for entry in unit['dropins']:
            require(sha256(C.B.read(entry['path'], private=False)).hexdigest() == entry['sha256'], 'unit_dropin_changed')
    for path in (plan['market_backup']['env_file'], plan['backup_key_file']):
        info = Path(path).lstat()
        C.B.private_parent(Path(path))
        require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_nlink == 1
                and not info.st_mode & 0o077 and info.st_size <= 1048576, 'private_input_metadata_changed')
    require(list(C.B.identity(Path(plan['market_backup']['env_file']).lstat()))
            == plan['market_backup']['env_file_identity'], 'env_metadata_changed')
    for entry in plan['store_roots']:
        C.store_root_contract(entry)
    C.backup_contract(plan)
    C.runtime_contract(plan, 'backup_datastores')
    no_dump_or_restore(C, plan)
    require(len(plan['datastore_ids']) == 8 and
            sum(row['running'] and row['id'] not in plan['datastore_ids'] for row in plan['containers']) == 18,
            'restore_scope_changed')
    return value


def rewrite_owner(C, fd, path, original):
    require(C.B.document(path) == original, 'fence_payload_changed')
    updated = {**original, 'pid': os.getpid()}
    raw = (json.dumps(updated, sort_keys=True) + '\n').encode()
    os.lseek(fd, 0, os.SEEK_SET)
    C.B.write_all(fd, raw)
    os.ftruncate(fd, len(raw))
    os.fsync(fd)


def restore(C, plan, status):
    # Datastores are already up. Their actual readiness precedes every app.
    C.old_datastores_and_api_ready({**plan, 'health_probes': []})
    for row in plan['containers']:
        if row['running'] and row['id'] not in plan['datastore_ids']:
            C.B.command(['/usr/bin/docker', 'start', row['id']])
    for attempt in range(30):
        try:
            C.old_datastores_and_api_ready(plan)
            break
        except (C.P.Refusal, C.B.Refusal):
            if attempt == 29:
                raise
            C.remaining_contract(plan, 30)
            C.time.sleep(2)
    for row in plan['containers']:
        policy = row['restart_policy']
        label = policy['Name'] + (':' + str(policy['MaximumRetryCount'])
            if policy['Name'] == 'on-failure' and policy['MaximumRetryCount'] > 0 else '')
        C.B.command(['/usr/bin/docker', 'update', '--restart=' + label, row['id']])
    for timer in (False, True):
        for unit in plan['units']:
            if unit['active'] and unit['kind'] != 'oneshot' and (unit['kind'] == 'timer') == timer:
                C.B.command(['/usr/bin/systemctl', 'start', unit['name']])
    C.invariant_contract(plan, DIGEST, owned=True)
    C.runtime_contract(plan, 'baseline')
    C.old_datastores_and_api_ready(plan)
    value = C.journal(plan, DIGEST)
    require(value == status, 'original_journal_changed')
    value.update(phase='aborted_restored', aborted_from='writers_quiesced', security_transition_completed=False,
                 owned_restore_pid=os.getpid())
    C.write_json(C.paths(plan)[0], value, exclusive=False)


def execute(C, action):
    require(action in {'check', 'restore'}, 'invalid_action')
    plan = C.P.document(ROOT / 'plan.json', DIGEST)
    require(plan['source_root'] == str(ROOT / 'source'), 'wrong_source_root')
    sidecar = C.ROOT / (OP + '.owned-restore.json')
    require(not os.path.lexists(sidecar), 'owned_restore_already_attempted')
    records = {}
    with ExitStack() as stack, pinned(C) as bus:
        owned = []
        fds = {}
        for path in (C.P.MARKER, C.paths(plan)[1], C.P.RESERVATION):
            before = path.lstat()
            C.B.read(path, maximum=8192)
            fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK)
            stack.callback(os.close, fd)
            require(C.B.identity(before) == C.B.identity(os.fstat(fd)) == C.B.identity(path.lstat()), 'fence_inode_changed')
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fds[path] = fd
            if path != C.P.MARKER:
                records[path] = C.B.document(path)
                owned.append((path, before.st_dev, before.st_ino))
        source = records[C.paths(plan)[1]]
        reservation = records[C.P.RESERVATION]
        require(source == reservation and source['schema'] == 'web_engine_transaction/1'
                and source['operation_id'] == OP and source['nonce'] == NONCE
                and source['manifest_sha256'] == DIGEST, 'foreign_fence')
        status = proof(C, plan, bus, reservation)
        if action == 'check':
            return {'status': 'owned_restore_checked', 'runtime_changed': False}
        C.write_json(sidecar, {'schema': 'web_engine_owned_restore/1', 'operation_id': OP, 'nonce': NONCE,
            'manifest_sha256': DIGEST, 'source_sha256': SOURCE_SHA, 'previous_owner_pid': source['pid'],
            'restore_owner_pid': os.getpid(), 'previous_phase': status['phase'],
            'original_fences': [{'path': str(path), 'device': dev, 'inode': ino, 'payload': records[path]}
                                for path, dev, ino in owned], 'status': 'restoring'}, exclusive=True)
        for path, _, _ in owned:
            rewrite_owner(C, fds[path], path, records[path])
        C.invariant_contract(plan, DIGEST, owned=True)  # Checks our real 3 FLOCKs.
        restore(C, plan, status)
        # Remove the source fence first and global reservation LAST, so a failed
        # intermediate unlink cannot expose an unreserved unfinished cleanup.
        C.release_owned_fences(list(reversed(owned)))
        result = C.B.document(sidecar)
        result.update(status='restored', security_transition_completed=False)
        C.write_json(sidecar, result, exclusive=False)
        return {'status': 'aborted_restored', 'security_transition_completed': False, 'authority_changed': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('check', 'restore'), nargs='?', default='check')
    parser.add_argument('--confirmation', default='')
    args = parser.parse_args()
    require(args.action == 'check' or args.confirmation == CONFIRM, 'explicit_owned_restore_required')
    C = controller()
    def expired(*_):
        raise RuntimeError('owned_restore_deadline')
    plan = C.P.document(ROOT / 'plan.json', DIGEST)
    deadline = C.remaining_contract(plan, 300)
    seconds = min(900, int((deadline - C.datetime.now(C.timezone.utc)).total_seconds()) - 5)
    require(seconds > 0, 'owned_restore_window_expired')
    signal.signal(signal.SIGALRM, expired)
    signal.alarm(seconds)
    print(json.dumps(execute(C, args.action), sort_keys=True))


def refusal(error):
    # Every recovery guard uses a constant metadata-only code. Never render a
    # traceback, arbitrary exception text, private path or command output.
    code = str(error)
    if not isinstance(error, RuntimeError) or not re.fullmatch(r'[a-z0-9_]{1,96}', code):
        code = 'owned_web_restore_refused'
    result = {'status': 'refused', 'code': code, 'completion_unverified': True}
    name = getattr(error, 'get_dbus_name', lambda: '')()
    if isinstance(name, str) and re.fullmatch(r'org\.freedesktop\.[A-Za-z0-9_.]{1,100}', name):
        result['dbus_error'] = name
    return result


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps(refusal(error), sort_keys=True))
        raise SystemExit(1)
